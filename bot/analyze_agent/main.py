"""
Accumulation Pattern Agent — Main polling loop.

Usage:
    python main.py                      # balanced mode
    python main.py --mode aggressive    # aggressive mode
    python main.py --mode conservative  # conservative mode
    python main.py --once               # single scan and exit
    python main.py --add <address> <chain>  # add token to watchlist
"""
import asyncio
import argparse
import json
import logging
import sys
import time
from pathlib import Path

from agent_config import get_config, AGGRESSIVE, BALANCED, CONSERVATIVE, AgentConfig
from data_fetcher import DataFetcher
from detector import AccumulationDetector
from state import StateManager
from agent_alerts import TelegramSender
from models import SignalTier
from rugcheck import fetch_rugcheck
from sm_db import count_sm_buys, score_bonus_from_sm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent")


MODES = {
    "aggressive":   AGGRESSIVE,
    "balanced":     BALANCED,
    "conservative": CONSERVATIVE,
}


def load_tokens(tokens_file: str) -> list[dict]:
    path = Path(tokens_file)
    if not path.exists():
        log.warning("tokens.json not found — creating empty file")
        path.write_text(json.dumps([], indent=2))
        return []
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.error("Failed to load tokens: %s", e)
        return []


def add_token(tokens_file: str, address: str, chain: str, symbol: str = ""):
    tokens = load_tokens(tokens_file)
    # avoid duplicates
    if any(t["address"].lower() == address.lower() for t in tokens):
        print(f"Token {address} already in list")
        return
    tokens.append({"address": address, "chain": chain, "symbol": symbol})
    Path(tokens_file).write_text(json.dumps(tokens, indent=2))
    print(f"Added {address} ({chain}) to {tokens_file}")


async def scan_token(
    entry: dict,
    fetcher: DataFetcher,
    detector: AccumulationDetector,
    state: StateManager,
    telegram: TelegramSender,
    mode_name: str,
    cfg: AgentConfig,
):
    address = entry["address"]
    chain   = entry.get("chain", "solana")

    # Note: we no longer early-return on cooldown here. The cooldown is
    # checked after analysis so a WATCHLIST cooldown cannot suppress a
    # subsequent SIGNAL/STRONG upgrade on the same token.

    log.info("Scanning %s (%s)...", address, chain)

    # ── Fetch metadata ────────────────────────────────────────────
    meta = await fetcher.get_token_metadata(address, chain)
    if meta is None:
        log.warning("No metadata for %s — skipping", address)
        return

    # Update symbol from config if not returned by API
    if entry.get("symbol") and meta.symbol == "???":
        meta.symbol = entry["symbol"]

    # Update stored ATH with current observation
    state.update_ath_mcap(address, meta.current_mcap)

    # Seed ATH from CoinGecko on first contact. Without this, a token
    # scanned for the first time post-drawdown reports drawdown=0 and the
    # whole pattern detector is dead on arrival.
    if not state.has_coingecko_ath(address):
        cg_ath = await fetcher.get_coingecko_ath_mcap(address, chain)
        if cg_ath > 0:
            state.update_ath_mcap(address, cg_ath)
        state.mark_coingecko_ath_checked(address)

    stored_ath = state.get_ath_mcap(address)
    state.register_token(address, meta.symbol, chain)

    # ── Fetch OHLCV ───────────────────────────────────────────────
    pool_addr, resolved_chain = await fetcher.get_pool_address(address, chain)
    # auto-detected chain overrides the entry hint — keeps state/keys coherent
    chain = resolved_chain or chain
    daily_candles  = []
    hourly_candles = []

    if pool_addr:
        daily_candles, hourly_candles = await asyncio.gather(
            fetcher.get_ohlcv(pool_addr, chain, timeframe="day",  aggregate=1, limit=90),
            fetcher.get_ohlcv(pool_addr, chain, timeframe="hour", aggregate=4, limit=60),
        )

    if not daily_candles:
        log.warning("No OHLCV for %s — skipping", address)
        return

    # ── Fetch holders ─────────────────────────────────────────────
    if meta.holders == 0:
        meta.holders = await fetcher.get_holders(address, chain)

    # ── Rugcheck (Solana only, short-circuits obvious scams) ─────
    rug = await fetch_rugcheck(fetcher._client, address, chain)
    if rug.available and (
        rug.rugged
        or (not rug.mint_authority_renounced)
        or (rug.top_holder_pct >= 0.25)
    ):
        log.info(
            "Skipping %s — Rugcheck blocker (rugged=%s mint_auth=%s top1=%.1f%%)",
            meta.symbol, rug.rugged, not rug.mint_authority_renounced, rug.top_holder_pct * 100,
        )
        return

    # ── Smart-money DB lookup (Solana curated wallets) ───────────
    sm_bonus = 0.0
    sm_lines: list[str] = []
    sm_wallets = 0
    sm_total   = 0.0
    if chain.lower() == "solana":
        sm_result = count_sm_buys(address, hours=24)
        if sm_result.available:
            sm_bonus, sm_lines = score_bonus_from_sm(sm_result)
            sm_wallets = sm_result.unique_wallets
            sm_total   = sm_result.total_buy_usd

    # ── Detect pattern ────────────────────────────────────────────
    analysis = detector.analyze(
        token=meta,
        daily_candles=daily_candles,
        hourly_candles=hourly_candles,
        stored_ath_mcap=stored_ath,
        sm_bonus=sm_bonus,
        sm_reason_lines=sm_lines,
        sm_wallets_24h=sm_wallets,
        sm_total_buy_usd=sm_total,
    )

    # ── Log result ────────────────────────────────────────────────
    tier_name = analysis.tier.value
    log.info(
        "%s $%s — score %.0f [%s] drawdown=%.0f%% spring=%s vol=x%.1f",
        address[:8],
        meta.symbol,
        analysis.score,
        tier_name,
        analysis.drawdown_from_ath * 100,
        analysis.spring.status.value,
        analysis.volume_spike_ratio,
    )

    if analysis.failed_filters:
        log.debug("Failed: %s", " | ".join(analysis.failed_filters))
    if analysis.passed_filters:
        log.debug("Passed: %s", " | ".join(analysis.passed_filters))

    # ── Send alert ────────────────────────────────────────────────
    if analysis.tier == SignalTier.NOISE:
        return

    if state.is_on_cooldown(address, incoming_tier=tier_name):
        remaining = state.cooldown_remaining_hours(address)
        log.debug("Skipping alert for %s — cooldown (%.1fh left, incoming %s)",
                  address, remaining, tier_name)
        return

    sent = await telegram.send_analysis(analysis, mode=mode_name.upper())
    if sent:
        state.record_signal(address, analysis.score, tier_name)
        # SIGNAL/STRONG → full cooldown; WATCHLIST → short anti-spam cooldown.
        # Tier-aware cooldown allows future WATCHLIST → SIGNAL upgrades.
        if analysis.tier in (SignalTier.SIGNAL, SignalTier.STRONG):
            state.set_cooldown(address, cfg.cooldown_hours, tier=tier_name)
        else:
            state.set_cooldown(address, cfg.watchlist_cooldown_hours, tier=tier_name)
        log.info("Alert sent for %s [%s]", meta.symbol, tier_name)


async def run_scan(
    cfg: AgentConfig,
    mode_name: str,
    tokens_file: str,
    state_file: str,
    once: bool = False,
):
    fetcher  = DataFetcher()
    detector = AccumulationDetector(cfg)
    state    = StateManager(state_file)
    telegram = TelegramSender(cfg.telegram_bot_token, cfg.telegram_chat_id)

    log.info("Accumulation Agent started | mode=%s | interval=%ds", mode_name.upper(), cfg.poll_interval_seconds)

    try:
        while True:
            tokens = load_tokens(tokens_file)
            if not tokens:
                log.warning("No tokens in %s — add some with --add", tokens_file)
            else:
                log.info("Scanning %d tokens...", len(tokens))
                for entry in tokens:
                    try:
                        await scan_token(entry, fetcher, detector, state, telegram, mode_name, cfg)
                    except Exception as e:
                        log.error("Error scanning %s: %s", entry.get("address", "?"), e)
                    await asyncio.sleep(0.5)  # rate limit

            state.cleanup_expired()

            if once:
                break

            log.info("Next scan in %ds...", cfg.poll_interval_seconds)
            await asyncio.sleep(cfg.poll_interval_seconds)

    finally:
        await fetcher.close()


def main():
    parser = argparse.ArgumentParser(description="Accumulation Pattern Agent")
    parser.add_argument("--mode", choices=["aggressive", "balanced", "conservative"],
                        default="balanced")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--add", nargs="+", metavar=("ADDRESS", "CHAIN"),
                        help="Add token: --add <address> <chain> [symbol]")
    parser.add_argument("--tokens", default="tokens.json")
    parser.add_argument("--state",  default="state.json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.add:
        address = args.add[0]
        chain   = args.add[1] if len(args.add) > 1 else "solana"
        symbol  = args.add[2] if len(args.add) > 2 else ""
        add_token(args.tokens, address, chain, symbol)
        return

    cfg = MODES[args.mode]
    cfg.telegram_bot_token = get_config().telegram_bot_token
    cfg.telegram_chat_id   = get_config().telegram_chat_id
    cfg.tokens_file = args.tokens
    cfg.state_file  = args.state

    asyncio.run(run_scan(cfg, args.mode, args.tokens, args.state, args.once))


if __name__ == "__main__":
    main()
