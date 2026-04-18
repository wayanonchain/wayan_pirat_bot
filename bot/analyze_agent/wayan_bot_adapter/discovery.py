"""
SM-Discovery job — scans `token_buys` for tokens that ≥N distinct curated
smart-money wallets have bought in the last `window_hours`, and enrols
them into `accumulation_watchlist`.

Designed to be called from APScheduler (see scheduler_jobs.py) or from a
one-shot `python -m ... discovery` run for backfill.

Thresholds are on the conservative side of what the plan discussed:
  - window = 72 hours
  - min_unique_wallets = 2

so we catch early movement without flooding the monitor with scammy
pump-and-dumps.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from . import repository as repo

log = logging.getLogger(__name__)


async def find_sm_candidates(
    db_path: str,
    window_hours: int = 72,
    min_unique_wallets: int = 2,
    min_total_usd: float = 2_000.0,
    max_mcap_at_buy: Optional[float] = None,
    limit: int = 50,
) -> list[dict]:
    """Return token candidates matching the SM-coincidence threshold.

    A token shows up with distinct active SM wallets, total volume, the
    most recent buy timestamp and the symbol seen on those buys. The
    optional max_mcap_at_buy guard filters out tokens that were already
    mega-cap when the wallets bought (we want low/mid-cap discovery).

    Hard-capped at `limit` candidates (default 50) to keep the monitor's
    per-tick cost bounded — each candidate triggers 3-4 external API
    calls, so an unbounded list can blow through DexScreener rate limits.
    """
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    query = """
        SELECT
            tb.token_address,
            MIN(tb.token_symbol)        AS symbol,
            COUNT(DISTINCT tb.wallet_address) AS n_wallets,
            SUM(tb.amount_usd)          AS total_usd,
            MAX(tb.timestamp)           AS last_buy,
            MIN(tb.mcap_at_buy)         AS min_mcap_at_buy
        FROM token_buys tb
        INNER JOIN wallets w ON w.address = tb.wallet_address
        WHERE tb.timestamp >= ?
          AND UPPER(w.status) = 'ACTIVE'
        GROUP BY tb.token_address
        HAVING n_wallets >= ?
           AND total_usd >= ?
        ORDER BY n_wallets DESC, total_usd DESC
        LIMIT ?
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            query,
            (cutoff_s, min_unique_wallets, min_total_usd, limit),
        )
        rows = await cur.fetchall()

    candidates = []
    for r in rows:
        if max_mcap_at_buy is not None and (r["min_mcap_at_buy"] or 0) > max_mcap_at_buy:
            continue
        candidates.append({
            "token_address": r["token_address"],
            "symbol":        r["symbol"] or "",
            "n_wallets":     int(r["n_wallets"]),
            "total_usd":     float(r["total_usd"] or 0),
            "last_buy":      r["last_buy"],
            "min_mcap_at_buy": float(r["min_mcap_at_buy"] or 0),
        })
    return candidates


async def enroll_candidates(candidates: list[dict]) -> tuple[int, int]:
    """Insert candidates into the watchlist. Returns (inserted, skipped)."""
    inserted = 0
    skipped  = 0
    for c in candidates:
        added = await repo.add_to_watchlist(
            c["token_address"],
            chain="solana",
            symbol=c["symbol"],
            added_source="sm_discovery",
            sm_wallets_at_add=c["n_wallets"],
        )
        if added:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


async def run_discovery(
    db_path: Optional[str] = None,
    window_hours: int = 72,
    min_unique_wallets: int = 2,
    min_total_usd: float = 2_000.0,
) -> dict:
    """One-shot discovery run. Safe to invoke from a scheduler wrapper."""
    path = db_path or repo._db_path()
    candidates = await find_sm_candidates(
        path,
        window_hours=window_hours,
        min_unique_wallets=min_unique_wallets,
        min_total_usd=min_total_usd,
    )
    inserted, skipped = await enroll_candidates(candidates)

    log.info(
        "[discovery] window=%dh min_wallets=%d → %d candidates "
        "(%d new, %d already watched)",
        window_hours, min_unique_wallets, len(candidates), inserted, skipped,
    )
    return {
        "candidates_found": len(candidates),
        "inserted":         inserted,
        "already_watched":  skipped,
        "top": candidates[:5],
    }


if __name__ == "__main__":
    import argparse, asyncio
    parser = argparse.ArgumentParser(description="SM-discovery run")
    parser.add_argument("--db", help="Path to bot.db (default: env WAYAN_SM_DB_PATH)")
    parser.add_argument("--window", type=int, default=72)
    parser.add_argument("--min-wallets", type=int, default=2)
    parser.add_argument("--min-usd", type=float, default=2_000.0)
    args = parser.parse_args()
    result = asyncio.run(run_discovery(
        db_path=args.db,
        window_hours=args.window,
        min_unique_wallets=args.min_wallets,
        min_total_usd=args.min_usd,
    ))
    print(result)
