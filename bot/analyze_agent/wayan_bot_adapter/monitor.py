"""
Accumulation monitor job — runs the pattern detector over every token on
the active watchlist. For each token:

  1. Load metadata (DexScreener) + OHLCV (GeckoTerminal).
  2. Seed ATH from CoinGecko the first time we see the token.
  3. Run the standalone detector, plus the WAYNE_PIRATE SM-DB bonus.
  4. Respect tier-aware cooldowns stored in `accumulation_state`.
  5. Record any non-NOISE analysis into `accumulation_signals`.
  6. Fire a Telegram alert for SIGNAL/STRONG (and optionally WATCHLIST).

Intended to be called every 5–15 minutes from APScheduler.
"""
import asyncio
import logging
from typing import Optional

# These imports target the top-level agent/ modules — when this package is
# placed inside WAYNE_PIRATE (bot/analyze_agent/wayan_bot_adapter/), the
# parent directory (bot/analyze_agent/) must be on sys.path. The scheduler
# bootstrap in scheduler_jobs.py handles that.
from agent_config import BALANCED, AgentConfig
from data_fetcher import DataFetcher
from detector import AccumulationDetector
from models import SignalTier
from sm_db import count_sm_buys, score_bonus_from_sm
from rugcheck import fetch_rugcheck

from . import repository as repo

log = logging.getLogger(__name__)


async def monitor_one(
    entry: "repo.WatchlistEntry",
    fetcher: DataFetcher,
    detector: AccumulationDetector,
    alerter,
    cfg: AgentConfig,
) -> Optional[dict]:
    """Monitor a single token. Returns the analysis summary dict, or None
    when the token could not be fetched.
    """
    address = entry.token_address
    chain   = entry.chain or "solana"

    meta = await fetcher.get_token_metadata(address, chain)
    if not meta:
        log.debug("No metadata for %s — skipping", address)
        return None
    if entry.symbol and meta.symbol == "???":
        meta.symbol = entry.symbol

    # Seed ATH from CoinGecko exactly once per token
    state = await repo.get_state(address)
    stored_ath = float((state or {}).get("ath_mcap") or 0.0)
    if stored_ath < meta.current_mcap:
        stored_ath = meta.current_mcap

    if not state or not state.get("cg_ath_checked_at"):
        cg_ath = await fetcher.get_coingecko_ath_mcap(address, chain)
        if cg_ath > stored_ath:
            stored_ath = cg_ath
        await repo.update_state(
            address, ath_mcap=stored_ath, cg_ath_checked=True,
        )
    else:
        await repo.update_state(address, ath_mcap=stored_ath)

    # OHLCV
    pool_addr, resolved_chain = await fetcher.get_pool_address(address, chain)
    if resolved_chain:
        chain = resolved_chain
    daily, hourly = [], []
    if pool_addr:
        daily, hourly = await asyncio.gather(
            fetcher.get_ohlcv(pool_addr, chain, timeframe="day",  aggregate=1, limit=90),
            fetcher.get_ohlcv(pool_addr, chain, timeframe="hour", aggregate=4, limit=60),
        )
    if not daily:
        log.debug("No OHLCV for %s — skipping", address)
        return None

    if meta.holders == 0:
        meta.holders = await fetcher.get_holders(address, chain)

    # Rugcheck — short-circuit obvious rugs before spending more compute
    rug = await fetch_rugcheck(fetcher._client, address, chain)
    if rug.available and (
        rug.rugged
        or (not rug.mint_authority_renounced)
        or (rug.top_holder_pct >= 0.25)
    ):
        log.info("Skipping %s (%s) — Rugcheck blocker", meta.symbol, address[:8])
        await repo.remove_from_watchlist(address, reason="rugcheck_blocker")
        return None

    # SM-DB bonus
    sm_bonus = 0.0
    sm_lines: list[str] = []
    sm_wallets = 0
    sm_total   = 0.0
    sm_result = count_sm_buys(address, hours=24)
    if sm_result.available:
        sm_bonus, sm_lines = score_bonus_from_sm(sm_result)
        sm_wallets = sm_result.unique_wallets
        sm_total   = sm_result.total_buy_usd

    analysis = detector.analyze(
        token=meta,
        daily_candles=daily,
        hourly_candles=hourly,
        stored_ath_mcap=stored_ath,
        sm_bonus=sm_bonus,
        sm_reason_lines=sm_lines,
        sm_wallets_24h=sm_wallets,
        sm_total_buy_usd=sm_total,
    )

    tier_name = analysis.tier.value
    await repo.update_check_result(address, analysis.score, tier_name)

    if analysis.tier == SignalTier.NOISE:
        return {"address": address, "symbol": meta.symbol, "tier": tier_name,
                "score": analysis.score}

    # Tier-aware cooldown
    if await repo.is_on_cooldown(address, incoming_tier=tier_name):
        log.debug("Alert for %s suppressed by cooldown (tier %s)", meta.symbol, tier_name)
        return {"address": address, "symbol": meta.symbol, "tier": tier_name,
                "score": analysis.score, "suppressed": True}

    # Persist the signal
    message_id: Optional[int] = None
    sent = False
    if alerter is not None:
        try:
            message_id = await alerter(analysis)
            sent = message_id is not None
        except Exception as e:
            log.error("Alerter failed for %s: %s", meta.symbol, e)

    await repo.record_signal(
        token_address=address,
        symbol=meta.symbol,
        tier=tier_name,
        score=analysis.score,
        drawdown_from_ath=analysis.drawdown_from_ath,
        consolidation_days=(analysis.consolidation.duration_days if analysis.consolidation else 0.0),
        spring_status=analysis.spring.status.value,
        volume_spike_ratio=analysis.volume_spike_ratio,
        no_new_low_days=analysis.no_new_low_days,
        sm_wallets_24h=sm_wallets,
        sm_total_buy_usd=sm_total,
        mcap_at_signal=meta.current_mcap,
        liquidity_at_signal=meta.liquidity_usd,
        ath_mcap=stored_ath,
        sent_to_telegram=sent,
        telegram_message_id=message_id,
    )

    # Cooldown: full for SIGNAL/STRONG, short for WATCHLIST
    if analysis.tier in (SignalTier.SIGNAL, SignalTier.STRONG):
        await repo.update_state(address, cooldown_hours=cfg.cooldown_hours, cooldown_tier=tier_name)
    else:
        await repo.update_state(address, cooldown_hours=cfg.watchlist_cooldown_hours, cooldown_tier=tier_name)

    return {
        "address":  address,
        "symbol":   meta.symbol,
        "tier":     tier_name,
        "score":    analysis.score,
        "suppressed": False,
        "sent":     sent,
    }


async def run_monitor_once(
    alerter=None,
    cfg: AgentConfig = BALANCED,
    batch_size: int = 15,
    min_gap_hours: float = 12.0,
) -> dict:
    """Run one monitor pass over a staleness-ordered batch of the watchlist.

    Two budget guards keep us inside GeckoTerminal's ~30 RPM free-tier:

      * `batch_size` caps the number of tokens we'll touch in a single
        call (default 15).
      * `min_gap_hours` skips any token we already analysed in the last
        N hours — no point re-running the pattern check on a token that
        just reported NOISE. Default 12h.

    A hard circuit-breaker on 429 from GeckoTerminal aborts the rest of
    the batch; the next scheduled tick resumes from the stalest entries.
    """
    entries = await repo.list_active_watchlist(
        limit=batch_size, order_by_staleness=True,
    )
    if not entries:
        log.info("[monitor] watchlist empty")
        return {"count": 0, "results": []}

    # Drop entries that were checked recently (saves a ton of API budget)
    if min_gap_hours > 0:
        import datetime as _dt
        cutoff = _dt.datetime.utcnow() - _dt.timedelta(hours=min_gap_hours)
        fresh = [e for e in entries if (e.last_checked_at or _dt.datetime(1970, 1, 1)) < cutoff]
        if not fresh:
            log.info("[monitor] all %d entries checked within %dh — skipping tick",
                     len(entries), int(min_gap_hours))
            return {"count": 0, "results": []}
        entries = fresh

    fetcher  = DataFetcher()
    detector = AccumulationDetector(cfg)
    results  = []
    try:
        for entry in entries:
            try:
                r = await monitor_one(entry, fetcher, detector, alerter, cfg)
                if r:
                    results.append(r)
            except Exception as e:
                log.error("monitor_one failed for %s: %s", entry.token_address, e)
            if fetcher.rate_limited:
                log.warning("[monitor] aborting batch after 429 — resume next tick")
                break
            await asyncio.sleep(4.0)  # DexScreener/GeckoTerminal rate-limit guard

        expired = await repo.mark_stale_old_entries(max_age_days=60)
        if expired:
            log.info("[monitor] marked %d watchlist entries as stale", expired)
    finally:
        await fetcher.close()

    fired = [r for r in results if r.get("tier") in ("SIGNAL", "STRONG") and not r.get("suppressed")]
    log.info("[monitor] processed=%d fired=%d", len(results), len(fired))
    return {"count": len(results), "fired": len(fired), "results": results}
