"""
Backtest the accumulation detector on historical OHLCV data.

Given a list of (chain, token_address) pairs, the script pulls full daily
candles via GeckoTerminal, then slides the detector across the history:
for each day D, treat candles[:D] as "known" and ask the detector whether
it would have fired. For every fire, look at candles[D:D+forward_days] to
measure forward return (max multiple).

Usage:
    python backtest.py --tokens backtest_tokens.json --mode balanced
    python backtest.py --address 0x... --chain base --forward-days 30

Output: per-signal rows + aggregate precision/avg-ROI summary.

This is intentionally lightweight — no asyncio machinery, no charting. The
point is to know whether current thresholds produce anything useful before
we spend more effort tuning.
"""
import argparse
import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from agent_config import AGGRESSIVE, BALANCED, CONSERVATIVE, AgentConfig
from data_fetcher import DataFetcher
from detector import AccumulationDetector
from models import SignalTier, TokenMetadata, OHLCV

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("backtest")


MODES = {"aggressive": AGGRESSIVE, "balanced": BALANCED, "conservative": CONSERVATIVE}


@dataclass
class SignalRow:
    token: str
    chain: str
    day_idx: int                 # index in the daily candle series
    score: float
    tier: str
    drawdown: float
    consolidation_days: float
    vol_spike: float
    forward_days: int
    max_multiple: float          # best close/entry over the forward window
    final_multiple: float        # close at end of forward window / entry
    went_2x: bool
    failed: list[str] = field(default_factory=list)


@dataclass
class BacktestSummary:
    total_fires: int
    precision_2x: float
    avg_max_multiple: float
    avg_final_multiple: float
    tier_counts: dict


async def _fetch_full_history(
    fetcher: DataFetcher,
    address: str,
    chain: str,
) -> tuple[Optional[TokenMetadata], list[OHLCV]]:
    meta = await fetcher.get_token_metadata(address, chain)
    if not meta:
        return None, []
    pool_addr, resolved = await fetcher.get_pool_address(address, chain)
    if not pool_addr:
        return meta, []
    candles = await fetcher.get_ohlcv(pool_addr, resolved, timeframe="day", aggregate=1, limit=365)
    return meta, candles


def _simulate(
    meta: TokenMetadata,
    candles: list[OHLCV],
    detector: AccumulationDetector,
    forward_days: int,
    warmup_days: int = 45,
) -> list[SignalRow]:
    """Slide the detector across history. At each cursor, treat candles
    up to cursor as known history and pretend the token's current price
    equals the last close at that cursor.
    """
    rows: list[SignalRow] = []
    if len(candles) < warmup_days + 10:
        return rows

    # Running ATH from the series so the "stored_ath_mcap" argument
    # matches what a running agent would have accumulated.
    running_ath_price = 0.0

    # price → mcap conversion factor estimated from the end-of-series meta
    # (mcap / price). Imperfect but sufficient for threshold comparisons.
    if meta.current_price > 0 and meta.current_mcap > 0:
        mcap_per_price = meta.current_mcap / meta.current_price
    else:
        mcap_per_price = 1.0

    last_fired_idx = -999  # avoid firing twice in the same cooldown window

    for cursor in range(warmup_days, len(candles) - 1):
        hist = candles[: cursor + 1]
        entry_close = hist[-1].close
        running_ath_price = max(running_ath_price, hist[-1].high)

        # Synthesize a TokenMetadata snapshot for this cursor
        snap = TokenMetadata(
            address=meta.address,
            symbol=meta.symbol,
            name=meta.name,
            chain=meta.chain,
            current_price=entry_close,
            current_mcap=entry_close * mcap_per_price,
            ath_mcap=running_ath_price * mcap_per_price,
            liquidity_usd=meta.liquidity_usd,
            holders=meta.holders,
            age_days=meta.age_days,
            dexscreener_url=meta.dexscreener_url,
            birdeye_url=meta.birdeye_url,
        )

        analysis = detector.analyze(
            token=snap,
            daily_candles=hist,
            hourly_candles=[],    # no hourly sim — recovery_hours falls back
            stored_ath_mcap=snap.ath_mcap,
        )

        if analysis.tier == SignalTier.NOISE:
            continue

        # Cooldown emulation — skip fires within 4 cooldown-equivalent days
        if cursor - last_fired_idx < 4:
            continue
        last_fired_idx = cursor

        forward = candles[cursor : cursor + forward_days + 1]
        highs = [c.high for c in forward] or [entry_close]
        final_close = forward[-1].close if forward else entry_close
        max_mult = max(highs) / entry_close if entry_close > 0 else 0
        final_mult = final_close / entry_close if entry_close > 0 else 0

        rows.append(SignalRow(
            token=meta.symbol,
            chain=meta.chain,
            day_idx=cursor,
            score=analysis.score,
            tier=analysis.tier.value,
            drawdown=analysis.drawdown_from_ath,
            consolidation_days=(analysis.consolidation.duration_days if analysis.consolidation else 0.0),
            vol_spike=analysis.volume_spike_ratio,
            forward_days=forward_days,
            max_multiple=max_mult,
            final_multiple=final_mult,
            went_2x=max_mult >= 2.0,
            failed=analysis.failed_filters,
        ))

    return rows


def _summarize(rows: list[SignalRow]) -> BacktestSummary:
    if not rows:
        return BacktestSummary(0, 0.0, 0.0, 0.0, {})
    total = len(rows)
    wins  = sum(1 for r in rows if r.went_2x)
    avg_max   = sum(r.max_multiple   for r in rows) / total
    avg_final = sum(r.final_multiple for r in rows) / total
    tiers: dict[str, int] = {}
    for r in rows:
        tiers[r.tier] = tiers.get(r.tier, 0) + 1
    return BacktestSummary(
        total_fires=total,
        precision_2x=wins / total,
        avg_max_multiple=avg_max,
        avg_final_multiple=avg_final,
        tier_counts=tiers,
    )


async def run(
    tokens: list[dict],
    cfg: AgentConfig,
    forward_days: int,
) -> tuple[list[SignalRow], BacktestSummary]:
    detector = AccumulationDetector(cfg)
    fetcher  = DataFetcher()
    all_rows: list[SignalRow] = []
    try:
        for entry in tokens:
            addr  = entry["address"]
            chain = entry.get("chain", "solana")
            log.info("Backtesting %s (%s)...", addr, chain)
            meta, candles = await _fetch_full_history(fetcher, addr, chain)
            if not meta or not candles:
                print(f"⚠️  no data for {addr} ({chain})")
                continue
            rows = _simulate(meta, candles, detector, forward_days=forward_days)
            all_rows.extend(rows)
            for r in rows:
                print(
                    f"{r.token:<8} [{r.tier:<9}] score={r.score:5.1f} "
                    f"dd={r.drawdown*100:4.0f}% cons={r.consolidation_days:4.0f}d "
                    f"vol=x{r.vol_spike:4.1f}  →  max x{r.max_multiple:4.2f} "
                    f"final x{r.final_multiple:4.2f}  {'WIN' if r.went_2x else '—'}"
                )
    finally:
        await fetcher.close()
    return all_rows, _summarize(all_rows)


def main():
    parser = argparse.ArgumentParser(description="Accumulation detector backtest")
    parser.add_argument("--tokens", help="Path to JSON list of tokens")
    parser.add_argument("--address", help="Single token address (+ --chain)")
    parser.add_argument("--chain", default="solana")
    parser.add_argument("--mode", choices=list(MODES), default="balanced")
    parser.add_argument("--forward-days", type=int, default=30)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    if args.tokens:
        tokens = json.loads(Path(args.tokens).read_text())
    elif args.address:
        tokens = [{"address": args.address, "chain": args.chain}]
    else:
        print("Pass --tokens <file> or --address <addr> --chain <chain>")
        return

    cfg = MODES[args.mode]
    rows, summary = asyncio.run(run(tokens, cfg, args.forward_days))

    print("\n" + "=" * 70)
    print(f"Mode: {args.mode.upper()} · Forward window: {args.forward_days}d")
    print(f"Total fires:    {summary.total_fires}")
    if summary.total_fires > 0:
        print(f"Precision (2x): {summary.precision_2x*100:.1f}%")
        print(f"Avg max mult:   x{summary.avg_max_multiple:.2f}")
        print(f"Avg final mult: x{summary.avg_final_multiple:.2f}")
        print(f"Tiers:          {summary.tier_counts}")
    print("=" * 70)


if __name__ == "__main__":
    main()
