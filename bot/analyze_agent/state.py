"""
JSON-based state manager.
Tracks: cooldowns, ATH mcap history, last seen signal per token.
No Redis dependency — simple file-based persistence.
"""
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class StateManager:
    def __init__(self, state_file: str = "state.json"):
        self.path = Path(state_file)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception as e:
                log.warning("Could not load state file: %s", e)
        return {
            "cooldowns":        {},
            "ath_mcap":         {},
            "last_signal":      {},
            "seen_tokens":      {},
            "cg_ath_checked":   {},
        }

    def save(self):
        """Atomic write: tmpfile in same dir + os.replace so a crash can't
        leave a half-written state.json.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=self.path.name + ".",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self._data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.error("Could not save state: %s", e)

    # ── Cooldown ──────────────────────────────────────────────────
    # Cooldowns are stored per (address, tier). A cooldown for WATCHLIST
    # only suppresses new WATCHLIST alerts — a subsequent SIGNAL/STRONG
    # is still allowed. SIGNAL cooldown also suppresses WATCHLIST.

    _TIER_RANK = {"WATCHLIST": 1, "SIGNAL": 2, "STRONG": 3}

    def set_cooldown(self, token_address: str, hours: float, tier: str = "SIGNAL"):
        self._data.setdefault("cooldowns", {})[token_address] = {
            "expires": time.time() + hours * 3600,
            "tier": tier,
        }
        self.save()

    def _cooldown_entry(self, token_address: str) -> Optional[dict]:
        entry = self._data.get("cooldowns", {}).get(token_address)
        if not entry:
            return None
        # Back-compat: old format was a bare float timestamp
        if isinstance(entry, (int, float)):
            return {"expires": float(entry), "tier": "SIGNAL"}
        return entry

    def is_on_cooldown(self, token_address: str, incoming_tier: str = "SIGNAL") -> bool:
        entry = self._cooldown_entry(token_address)
        if not entry:
            return False
        if time.time() >= entry.get("expires", 0):
            return False
        stored_rank   = self._TIER_RANK.get(entry.get("tier", "SIGNAL"), 2)
        incoming_rank = self._TIER_RANK.get(incoming_tier, 2)
        # Block only if incoming tier is not strictly stronger than stored tier.
        return incoming_rank <= stored_rank

    def cooldown_remaining_hours(self, token_address: str) -> float:
        entry = self._cooldown_entry(token_address)
        if not entry:
            return 0.0
        remaining = entry.get("expires", 0) - time.time()
        return max(0.0, remaining / 3600)

    # ── ATH mcap tracking ─────────────────────────────────────────

    def update_ath_mcap(self, token_address: str, mcap: float):
        current = self._data.get("ath_mcap", {}).get(token_address, 0)
        if mcap > current:
            self._data.setdefault("ath_mcap", {})[token_address] = mcap
            self.save()

    def get_ath_mcap(self, token_address: str) -> float:
        return self._data.get("ath_mcap", {}).get(token_address, 0.0)

    def has_coingecko_ath(self, token_address: str) -> bool:
        """True if we've already asked CoinGecko for this token's ATH."""
        return token_address in self._data.get("cg_ath_checked", {})

    def mark_coingecko_ath_checked(self, token_address: str):
        self._data.setdefault("cg_ath_checked", {})[token_address] = time.time()
        self.save()

    # ── Signal history ─────────────────────────────────────────────

    def record_signal(self, token_address: str, score: float, tier: str):
        self._data.setdefault("last_signal", {})[token_address] = {
            "ts": time.time(),
            "score": score,
            "tier": tier,
        }
        self.save()

    def get_last_signal(self, token_address: str) -> Optional[dict]:
        return self._data.get("last_signal", {}).get(token_address)

    # ── Token registry ────────────────────────────────────────────

    def register_token(self, address: str, symbol: str, chain: str):
        self._data.setdefault("seen_tokens", {})[address] = {
            "symbol": symbol,
            "chain": chain,
            "first_seen": self._data.get("seen_tokens", {}).get(address, {}).get("first_seen", time.time()),
        }
        self.save()

    # ── Cleanup ───────────────────────────────────────────────────

    def cleanup_expired(self):
        """Remove expired cooldowns to keep file small."""
        now = time.time()
        cooldowns = self._data.get("cooldowns", {})
        expired = []
        for k, v in cooldowns.items():
            expires = v.get("expires", 0) if isinstance(v, dict) else float(v)
            if expires < now:
                expired.append(k)
        for k in expired:
            del cooldowns[k]
        if expired:
            self.save()
