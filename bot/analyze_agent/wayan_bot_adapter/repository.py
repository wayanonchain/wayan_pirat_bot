"""
Async repository for the accumulation_* tables.

Uses aiosqlite directly against the bot.db path read from
`config.settings.DB_PATH` (WAYNE_PIRATE). Intentionally NOT using the
existing SQLAlchemy session because our tables are isolated and we don't
want to force model imports on the bot's cold path.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite


@dataclass
class WatchlistEntry:
    token_address: str
    chain: str
    symbol: str
    added_source: str
    added_by_user_id: Optional[int]
    sm_wallets_at_add: int
    added_at: datetime
    last_checked_at: Optional[datetime]
    last_score: Optional[float]
    last_tier: Optional[str]
    status: str


def _db_path() -> str:
    """Resolve the bot.db path from the hosting bot's settings, or from
    the WAYAN_SM_DB_PATH env var when the adapter is running standalone.
    """
    try:
        from config.settings import DB_PATH   # noqa: F401 — bot is importable
        return str(DB_PATH)
    except Exception:
        import os
        p = os.getenv("WAYAN_SM_DB_PATH", "")
        if not p:
            raise RuntimeError(
                "Cannot resolve DB path: not inside WAYNE_PIRATE and "
                "WAYAN_SM_DB_PATH is not set."
            )
        return p


# ─────────────────────────────────────────────────────────────────────────
# Watchlist
# ─────────────────────────────────────────────────────────────────────────

async def add_to_watchlist(
    token_address: str,
    *,
    chain: str = "solana",
    symbol: str = "",
    added_source: str,
    added_by_user_id: Optional[int] = None,
    sm_wallets_at_add: int = 0,
) -> bool:
    """Insert (or no-op if already present) a token into the watchlist.

    Returns True if the row was inserted, False if it already existed.
    """
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO accumulation_watchlist
                (token_address, chain, symbol, added_source,
                 added_by_user_id, sm_wallets_at_add, status)
            VALUES (?, ?, ?, ?, ?, ?, 'monitoring')
            """,
            (token_address, chain, symbol, added_source,
             added_by_user_id, sm_wallets_at_add),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_from_watchlist(token_address: str, reason: str = "manual") -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE accumulation_watchlist
               SET status       = 'removed',
                   removed_at   = CURRENT_TIMESTAMP,
                   removed_reason = ?
             WHERE token_address = ?
            """,
            (reason, token_address),
        )
        await db.commit()


async def list_active_watchlist() -> list[WatchlistEntry]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM accumulation_watchlist
             WHERE status = 'monitoring'
             ORDER BY added_at DESC
            """
        )
        rows = await cur.fetchall()
    return [_row_to_entry(r) for r in rows]


async def update_check_result(
    token_address: str,
    score: float,
    tier: str,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE accumulation_watchlist
               SET last_checked_at = CURRENT_TIMESTAMP,
                   last_score      = ?,
                   last_tier       = ?
             WHERE token_address   = ?
            """,
            (score, tier, token_address),
        )
        await db.commit()


async def mark_stale_old_entries(max_age_days: int = 60) -> int:
    """Auto-expire watchlist entries that have sat without producing a
    SIGNAL/STRONG for N days. Returns count of rows marked stale.
    """
    async with aiosqlite.connect(_db_path()) as db:
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        cur = await db.execute(
            """
            UPDATE accumulation_watchlist
               SET status         = 'stale',
                   removed_at     = CURRENT_TIMESTAMP,
                   removed_reason = 'aged out'
             WHERE status = 'monitoring'
               AND added_at < ?
               AND (last_tier IS NULL
                    OR last_tier IN ('NOISE', 'WATCHLIST'))
            """,
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount


def _row_to_entry(r: aiosqlite.Row) -> WatchlistEntry:
    def _ts(x):
        if not x:
            return None
        if isinstance(x, datetime):
            return x
        try:
            return datetime.fromisoformat(str(x))
        except Exception:
            return None

    return WatchlistEntry(
        token_address=r["token_address"],
        chain=r["chain"],
        symbol=r["symbol"] or "",
        added_source=r["added_source"],
        added_by_user_id=r["added_by_user_id"],
        sm_wallets_at_add=r["sm_wallets_at_add"] or 0,
        added_at=_ts(r["added_at"]) or datetime.utcnow(),
        last_checked_at=_ts(r["last_checked_at"]),
        last_score=r["last_score"],
        last_tier=r["last_tier"],
        status=r["status"],
    )


# ─────────────────────────────────────────────────────────────────────────
# Signals
# ─────────────────────────────────────────────────────────────────────────

async def record_signal(
    token_address: str,
    symbol: str,
    tier: str,
    score: float,
    drawdown_from_ath: float,
    consolidation_days: float,
    spring_status: str,
    volume_spike_ratio: float,
    no_new_low_days: int,
    sm_wallets_24h: int,
    sm_total_buy_usd: float,
    mcap_at_signal: float,
    liquidity_at_signal: float,
    ath_mcap: float,
    sent_to_telegram: bool = False,
    telegram_message_id: Optional[int] = None,
) -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            """
            INSERT INTO accumulation_signals (
                token_address, symbol, tier, score,
                drawdown_from_ath, consolidation_days, spring_status,
                volume_spike_ratio, no_new_low_days,
                sm_wallets_24h, sm_total_buy_usd,
                mcap_at_signal, liquidity_at_signal, ath_mcap,
                sent_to_telegram, telegram_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_address, symbol, tier, score,
                drawdown_from_ath, consolidation_days, spring_status,
                volume_spike_ratio, no_new_low_days,
                sm_wallets_24h, sm_total_buy_usd,
                mcap_at_signal, liquidity_at_signal, ath_mcap,
                1 if sent_to_telegram else 0, telegram_message_id,
            ),
        )
        await db.commit()
        return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────

async def get_state(token_address: str) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM accumulation_state WHERE token_address = ?",
            (token_address,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


async def update_state(
    token_address: str,
    *,
    ath_mcap: Optional[float] = None,
    cg_ath_checked: bool = False,
    cooldown_hours: Optional[float] = None,
    cooldown_tier: Optional[str] = None,
) -> None:
    """Upsert a state row, modifying only the fields we were told to touch."""
    existing = await get_state(token_address)

    new_ath = ath_mcap if ath_mcap is not None else (existing.get("ath_mcap") if existing else 0.0)
    if existing and ath_mcap is not None:
        # ATH only ever ratchets upward
        new_ath = max(existing.get("ath_mcap") or 0.0, ath_mcap)

    cg_stamp = datetime.utcnow() if cg_ath_checked else (
        existing.get("cg_ath_checked_at") if existing else None
    )
    cd_until = None
    cd_tier  = None
    if cooldown_hours is not None:
        cd_until = datetime.utcnow() + timedelta(hours=cooldown_hours)
        cd_tier  = cooldown_tier or "SIGNAL"
    elif existing:
        cd_until = existing.get("cooldown_until")
        cd_tier  = existing.get("cooldown_tier")

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO accumulation_state
                (token_address, ath_mcap, cg_ath_checked_at,
                 cooldown_until, cooldown_tier, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(token_address) DO UPDATE SET
                ath_mcap          = excluded.ath_mcap,
                cg_ath_checked_at = excluded.cg_ath_checked_at,
                cooldown_until    = excluded.cooldown_until,
                cooldown_tier     = excluded.cooldown_tier,
                updated_at        = CURRENT_TIMESTAMP
            """,
            (token_address, new_ath, cg_stamp, cd_until, cd_tier),
        )
        await db.commit()


_TIER_RANK = {"WATCHLIST": 1, "SIGNAL": 2, "STRONG": 3}


async def is_on_cooldown(token_address: str, incoming_tier: str) -> bool:
    """Tier-aware cooldown check — identical semantics to the standalone
    state.py implementation: only same-or-weaker tiers are suppressed.
    """
    state = await get_state(token_address)
    if not state:
        return False
    cd = state.get("cooldown_until")
    if not cd:
        return False
    cd_dt = cd if isinstance(cd, datetime) else datetime.fromisoformat(str(cd))
    if cd_dt <= datetime.utcnow():
        return False
    stored_rank = _TIER_RANK.get(state.get("cooldown_tier") or "SIGNAL", 2)
    incoming_rank = _TIER_RANK.get(incoming_tier, 2)
    return incoming_rank <= stored_rank
