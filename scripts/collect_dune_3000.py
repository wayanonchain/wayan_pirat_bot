#!/usr/bin/env python3
"""
Collect ~3000 Smart Money wallet candidates via Dune SQL.
=========================================================
Strategy: multiple Dune queries targeting different trader profiles:
1. Top traders by volume (last 60 days) — LIMIT 1500
2. Top traders by profit (high win rate) — LIMIT 1000
3. Early buyers (wallets that buy tokens < 1h after first trade) — LIMIT 500

Then: deduplicate, remove already-known wallets, save candidates.
Next step: validate via SolanaTracker PnL (separate script).
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

# pip install dune-client
from dune_client.client import DuneClient

DUNE_API_KEY = "twALuToHG2VFrULYkhWOSXsPiC14284o"
DATA_DIR = Path(__file__).parent.parent / "data"
WALLET_DB_PATH = DATA_DIR / "wallet_database.json"


def load_existing_wallets() -> set:
    """Load already-known wallet addresses from all local sources."""
    known = set()

    # From wallet_database.json
    if WALLET_DB_PATH.exists():
        with open(WALLET_DB_PATH) as f:
            db = json.load(f)
        wallets = db.get("wallets", db)
        if isinstance(wallets, dict):
            known.update(wallets.keys())

    # From previous Dune candidates
    for txt_file in DATA_DIR.glob("dune_*_addresses.txt"):
        for line in txt_file.read_text().splitlines():
            addr = line.strip()
            if addr:
                known.add(addr)

    # From backup databases
    for backup in DATA_DIR.glob("wallet_database_*.json"):
        try:
            with open(backup) as f:
                bk = json.load(f)
            bk_wallets = bk.get("wallets", bk)
            if isinstance(bk_wallets, dict):
                known.update(bk_wallets.keys())
        except Exception:
            pass

    return known


def run_dune_sql(client: DuneClient, sql: str, description: str) -> list[dict]:
    """Execute Dune SQL and return rows."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}")
    try:
        result = client.run_sql(sql)
        if result.result and result.result.rows:
            rows = result.result.rows
            print(f"  ✅ Got {len(rows)} rows")
            return rows
        print("  ⚠️  No rows returned")
        return []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return []


def collect_top_by_volume(client: DuneClient) -> list[dict]:
    """Query 1: Top traders by volume in last 60 days."""
    sql = """
SELECT
    trader_id as wallet,
    COUNT(*) as trade_count,
    COUNT(DISTINCT CASE
        WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        THEN token_bought_mint_address
        ELSE token_sold_mint_address
    END) as unique_tokens,
    SUM(amount_usd) as total_volume_usd,
    COUNT(DISTINCT DATE_TRUNC('day', block_time)) as active_days,
    MIN(block_time) as first_trade,
    MAX(block_time) as last_trade
FROM dex_solana.trades
WHERE block_time > NOW() - INTERVAL '60' DAY
    AND amount_usd > 50
    AND amount_usd < 500000
    AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
         OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
    AND token_bought_mint_address != token_sold_mint_address
GROUP BY trader_id
HAVING COUNT(*) >= 20
    AND COUNT(DISTINCT CASE
        WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        THEN token_bought_mint_address
        ELSE token_sold_mint_address
    END) >= 5
    AND SUM(amount_usd) > 10000
    AND COUNT(DISTINCT DATE_TRUNC('day', block_time)) >= 5
ORDER BY total_volume_usd DESC
LIMIT 1500
"""
    return run_dune_sql(client, sql, "Query 1: Top traders by volume (60 days)")


def collect_top_by_winrate(client: DuneClient) -> list[dict]:
    """Query 2: Traders with high win rates on memecoins."""
    sql = """
WITH wallet_token_pnl AS (
    SELECT
        trader_id as wallet,
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN token_bought_mint_address
            ELSE token_sold_mint_address
        END as token,
        SUM(CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN -amount_usd
            ELSE amount_usd
        END) as net_pnl
    FROM dex_solana.trades
    WHERE block_time > NOW() - INTERVAL '60' DAY
        AND amount_usd > 50
        AND amount_usd < 500000
        AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
             OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
        AND token_bought_mint_address != token_sold_mint_address
    GROUP BY 1, 2
    HAVING ABS(SUM(CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN -amount_usd ELSE amount_usd END)) > 10
),
wallet_stats AS (
    SELECT
        wallet,
        COUNT(*) as tokens_traded,
        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END) as total_profit,
        SUM(CASE WHEN net_pnl < 0 THEN net_pnl ELSE 0 END) as total_loss
    FROM wallet_token_pnl
    GROUP BY wallet
    HAVING COUNT(*) >= 5
)
SELECT
    wallet,
    tokens_traded,
    wins,
    losses,
    ROUND(CAST(wins AS DOUBLE) / NULLIF(wins + losses, 0), 3) as win_rate,
    ROUND(CAST(total_profit AS DOUBLE), 0) as total_profit_usd,
    ROUND(CAST(total_loss AS DOUBLE), 0) as total_loss_usd,
    ROUND(CAST(total_profit + total_loss AS DOUBLE), 0) as net_pnl_usd
FROM wallet_stats
WHERE wins >= 3
    AND CAST(wins AS DOUBLE) / NULLIF(wins + losses, 0) >= 0.4
    AND total_profit + total_loss > 1000
ORDER BY total_profit + total_loss DESC
LIMIT 1000
"""
    return run_dune_sql(client, sql, "Query 2: Top traders by win rate (60 days)")


def collect_early_buyers(client: DuneClient) -> list[dict]:
    """Query 3: Wallets that consistently buy tokens early."""
    sql = """
WITH token_first_trade AS (
    SELECT
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN token_bought_mint_address
            ELSE token_sold_mint_address
        END as token,
        MIN(block_time) as first_seen
    FROM dex_solana.trades
    WHERE block_time > NOW() - INTERVAL '30' DAY
        AND amount_usd > 10
        AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
             OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
    GROUP BY 1
    HAVING COUNT(DISTINCT trader_id) > 20
),
early_buys AS (
    SELECT
        t.trader_id as wallet,
        t.token_bought_mint_address as token,
        t.block_time,
        tf.first_seen,
        DATE_DIFF('minute', tf.first_seen, t.block_time) as minutes_after_launch
    FROM dex_solana.trades t
    JOIN token_first_trade tf
        ON t.token_bought_mint_address = tf.token
    WHERE t.block_time > NOW() - INTERVAL '30' DAY
        AND t.token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        AND t.amount_usd > 50
        AND DATE_DIFF('minute', tf.first_seen, t.block_time) BETWEEN 0 AND 60
)
SELECT
    wallet,
    COUNT(DISTINCT token) as early_tokens,
    COUNT(*) as early_buys,
    ROUND(AVG(minutes_after_launch), 1) as avg_minutes_after_launch
FROM early_buys
GROUP BY wallet
HAVING COUNT(DISTINCT token) >= 3
ORDER BY early_tokens DESC, early_buys DESC
LIMIT 500
"""
    return run_dune_sql(client, sql, "Query 3: Early buyers (< 1h after launch, 30 days)")


def main():
    print("=" * 60)
    print("  DUNE SMART MONEY WALLET COLLECTOR")
    print("  Target: ~3000 unique candidates")
    print("=" * 60)

    client = DuneClient(api_key=DUNE_API_KEY)

    # Test connection
    print("\nTesting Dune connection...")
    try:
        test = client.run_sql("SELECT 1 as ok")
        print("  ✅ Connected to Dune")
    except Exception as e:
        print(f"  ❌ Connection failed: {e}")
        sys.exit(1)

    # Load existing
    existing = load_existing_wallets()
    print(f"\nAlready known wallets: {len(existing)}")

    # Collect from all queries
    all_candidates = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Query 1: Volume
    print("\n" + "=" * 60)
    rows1 = collect_top_by_volume(client)
    for r in rows1:
        addr = r.get("wallet", "")
        if addr and len(addr) > 30:
            all_candidates[addr] = {
                "address": addr,
                "source": "dune_volume_60d",
                "trade_count": r.get("trade_count", 0),
                "unique_tokens": r.get("unique_tokens", 0),
                "total_volume_usd": r.get("total_volume_usd", 0),
                "active_days": r.get("active_days", 0),
            }

    time.sleep(3)  # Rate limit between queries

    # Query 2: Win rate
    rows2 = collect_top_by_winrate(client)
    for r in rows2:
        addr = r.get("wallet", "")
        if addr and len(addr) > 30:
            if addr in all_candidates:
                all_candidates[addr]["win_rate_dune"] = r.get("win_rate", 0)
                all_candidates[addr]["net_pnl_dune"] = r.get("net_pnl_usd", 0)
                all_candidates[addr]["sources"] = "dune_volume+winrate"
            else:
                all_candidates[addr] = {
                    "address": addr,
                    "source": "dune_winrate_60d",
                    "tokens_traded": r.get("tokens_traded", 0),
                    "wins": r.get("wins", 0),
                    "losses": r.get("losses", 0),
                    "win_rate_dune": r.get("win_rate", 0),
                    "net_pnl_dune": r.get("net_pnl_usd", 0),
                }

    time.sleep(3)

    # Query 3: Early buyers
    rows3 = collect_early_buyers(client)
    for r in rows3:
        addr = r.get("wallet", "")
        if addr and len(addr) > 30:
            if addr in all_candidates:
                all_candidates[addr]["early_tokens"] = r.get("early_tokens", 0)
                all_candidates[addr]["avg_minutes_after_launch"] = r.get("avg_minutes_after_launch", 0)
                src = all_candidates[addr].get("source", "")
                all_candidates[addr]["source"] = src + "+early_buyer"
            else:
                all_candidates[addr] = {
                    "address": addr,
                    "source": "dune_early_buyer_30d",
                    "early_tokens": r.get("early_tokens", 0),
                    "early_buys": r.get("early_buys", 0),
                    "avg_minutes_after_launch": r.get("avg_minutes_after_launch", 0),
                }

    # Deduplicate against existing
    total_raw = len(all_candidates)
    new_candidates = {k: v for k, v in all_candidates.items() if k not in existing}
    overlap = total_raw - len(new_candidates)

    # Save results
    output = {
        "timestamp": timestamp,
        "queries": {
            "volume_60d": len(rows1),
            "winrate_60d": len(rows2),
            "early_buyers_30d": len(rows3),
        },
        "total_raw_candidates": total_raw,
        "already_known": overlap,
        "new_candidates": len(new_candidates),
        "candidates": new_candidates,
    }

    # Save full report
    report_path = DATA_DIR / f"dune_3000_candidates_{timestamp}.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Save address list for validation
    addr_path = DATA_DIR / f"dune_3000_addresses_{timestamp}.txt"
    with open(addr_path, "w") as f:
        for addr in sorted(new_candidates.keys()):
            f.write(addr + "\n")

    # Summary
    print(f"\n{'='*60}")
    print(f"  COLLECTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Query 1 (Volume):     {len(rows1):>5} wallets")
    print(f"  Query 2 (Win rate):   {len(rows2):>5} wallets")
    print(f"  Query 3 (Early buy):  {len(rows3):>5} wallets")
    print(f"  {'─'*40}")
    print(f"  Total raw:            {total_raw:>5}")
    print(f"  Already known:        {overlap:>5}")
    print(f"  NEW candidates:       {len(new_candidates):>5}")
    print(f"")
    print(f"  Saved report: {report_path.name}")
    print(f"  Saved addresses: {addr_path.name}")

    # Source breakdown for new candidates
    sources = {}
    for c in new_candidates.values():
        s = c.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    print(f"\n  Source breakdown (new only):")
    for s, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"    {s}: {count}")

    # Wallets appearing in multiple queries (highest quality)
    multi = [a for a, c in new_candidates.items() if "+" in c.get("source", "")]
    print(f"\n  Multi-source candidates (appeared in 2+ queries): {len(multi)}")

    print(f"\n  Next step: run validate_new_dune_batch.py to check PnL")


if __name__ == "__main__":
    main()
