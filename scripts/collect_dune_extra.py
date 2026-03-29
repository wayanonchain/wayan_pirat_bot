#!/usr/bin/env python3
"""
Collect additional Smart Money wallets via Dune SQL (round 2).
==============================================================
New strategies not covered in collect_dune_3000.py:
1. Whale traders — avg trade > $5K (last 30 days)
2. Pump.fun specialists — wallets active on pump.fun tokens
3. High ROI traders — small volume but huge returns (last 45 days)
4. Fresh active traders — last 7 days, high frequency
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

from dune_client.client import DuneClient

DUNE_API_KEY = "twALuToHG2VFrULYkhWOSXsPiC14284o"
DATA_DIR = Path(__file__).parent.parent / "data"
WALLET_DB_PATH = DATA_DIR / "wallet_database.json"


def load_all_known() -> set:
    known = set()
    if WALLET_DB_PATH.exists():
        with open(WALLET_DB_PATH) as f:
            db = json.load(f)
        wallets = db.get("wallets", db)
        if isinstance(wallets, dict):
            known.update(wallets.keys())
    for txt in DATA_DIR.glob("dune_*_addresses*.txt"):
        for line in txt.read_text().splitlines():
            if line.strip():
                known.add(line.strip())
    return known


def run_sql(client, sql, desc):
    print(f"\n{'='*60}")
    print(f"  {desc}")
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


def query_whale_traders(client):
    sql = """
SELECT
    trader_id as wallet,
    COUNT(*) as trade_count,
    COUNT(DISTINCT CASE
        WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        THEN token_bought_mint_address
        ELSE token_sold_mint_address
    END) as unique_tokens,
    SUM(amount_usd) as total_volume,
    AVG(amount_usd) as avg_trade_usd,
    COUNT(DISTINCT DATE_TRUNC('day', block_time)) as active_days
FROM dex_solana.trades
WHERE block_time > NOW() - INTERVAL '30' DAY
    AND amount_usd > 500
    AND amount_usd < 1000000
    AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
         OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
    AND token_bought_mint_address != token_sold_mint_address
GROUP BY trader_id
HAVING AVG(amount_usd) > 5000
    AND COUNT(*) >= 10
    AND COUNT(DISTINCT CASE
        WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        THEN token_bought_mint_address
        ELSE token_sold_mint_address
    END) >= 3
    AND COUNT(DISTINCT DATE_TRUNC('day', block_time)) >= 3
ORDER BY avg_trade_usd DESC
LIMIT 500
"""
    return run_sql(client, sql, "Query 1: Whale traders (avg > $5K, 30 days)")


def query_pump_fun_specialists(client):
    sql = """
SELECT
    trader_id as wallet,
    COUNT(*) as trade_count,
    COUNT(DISTINCT token_bought_mint_address) as unique_pump_tokens,
    SUM(amount_usd) as total_volume,
    COUNT(DISTINCT DATE_TRUNC('day', block_time)) as active_days
FROM dex_solana.trades
WHERE block_time > NOW() - INTERVAL '30' DAY
    AND amount_usd > 50
    AND amount_usd < 500000
    AND token_sold_mint_address = 'So11111111111111111111111111111111111111112'
    AND token_bought_mint_address LIKE '%pump'
GROUP BY trader_id
HAVING COUNT(DISTINCT token_bought_mint_address) >= 5
    AND COUNT(*) >= 15
    AND SUM(amount_usd) > 5000
    AND COUNT(DISTINCT DATE_TRUNC('day', block_time)) >= 3
ORDER BY unique_pump_tokens DESC, total_volume DESC
LIMIT 500
"""
    return run_sql(client, sql, "Query 2: Pump.fun specialists (30 days)")


def query_high_roi(client):
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
        END) as net_pnl,
        SUM(CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN amount_usd
            ELSE 0
        END) as invested
    FROM dex_solana.trades
    WHERE block_time > NOW() - INTERVAL '45' DAY
        AND amount_usd > 20
        AND amount_usd < 500000
        AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
             OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
        AND token_bought_mint_address != token_sold_mint_address
    GROUP BY 1, 2
),
wallet_stats AS (
    SELECT
        wallet,
        COUNT(*) as tokens_traded,
        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(net_pnl) as total_net_pnl,
        SUM(invested) as total_invested
    FROM wallet_token_pnl
    WHERE invested > 0
    GROUP BY wallet
    HAVING COUNT(*) >= 5
        AND SUM(invested) > 500
        AND SUM(invested) < 500000
)
SELECT
    wallet,
    tokens_traded,
    wins,
    losses,
    ROUND(CAST(wins AS DOUBLE) / NULLIF(wins + losses, 0), 3) as win_rate,
    ROUND(CAST(total_net_pnl AS DOUBLE), 0) as net_pnl_usd,
    ROUND(CAST(total_invested AS DOUBLE), 0) as invested_usd,
    ROUND(CAST(total_net_pnl / NULLIF(total_invested, 0) AS DOUBLE), 2) as roi
FROM wallet_stats
WHERE total_net_pnl > 0
    AND CAST(total_net_pnl / NULLIF(total_invested, 0) AS DOUBLE) >= 2.0
    AND CAST(wins AS DOUBLE) / NULLIF(wins + losses, 0) >= 0.3
ORDER BY roi DESC
LIMIT 500
"""
    return run_sql(client, sql, "Query 3: High ROI traders (ROI >= 2x, 45 days)")


def query_fresh_active(client):
    sql = """
SELECT
    trader_id as wallet,
    COUNT(*) as trade_count,
    COUNT(DISTINCT CASE
        WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
        THEN token_bought_mint_address
        ELSE token_sold_mint_address
    END) as unique_tokens,
    SUM(amount_usd) as total_volume,
    COUNT(DISTINCT DATE_TRUNC('day', block_time)) as active_days
FROM dex_solana.trades
WHERE block_time > NOW() - INTERVAL '7' DAY
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
    AND COUNT(DISTINCT DATE_TRUNC('day', block_time)) >= 3
ORDER BY unique_tokens DESC, total_volume DESC
LIMIT 500
"""
    return run_sql(client, sql, "Query 4: Fresh active traders (last 7 days)")


def main():
    print("=" * 60)
    print("  DUNE EXTRA WALLET COLLECTOR (Round 2)")
    print("  Target: 200+ new ACTIVE candidates")
    print("=" * 60)

    client = DuneClient(api_key=DUNE_API_KEY)
    existing = load_all_known()
    print(f"\nAlready known wallets: {len(existing)}")

    all_candidates = {}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Query 1
    for row in query_whale_traders(client):
        addr = row.get("wallet", "")
        if addr and len(addr) > 30:
            all_candidates[addr] = {"address": addr, "source": "dune_whale_30d",
                                     "avg_trade_usd": row.get("avg_trade_usd", 0),
                                     "total_volume": row.get("total_volume", 0)}
    time.sleep(3)

    # Query 2
    for row in query_pump_fun_specialists(client):
        addr = row.get("wallet", "")
        if addr and len(addr) > 30:
            if addr in all_candidates:
                all_candidates[addr]["source"] += "+pump_fun"
            else:
                all_candidates[addr] = {"address": addr, "source": "dune_pump_fun_30d",
                                         "unique_pump_tokens": row.get("unique_pump_tokens", 0),
                                         "total_volume": row.get("total_volume", 0)}
    time.sleep(3)

    # Query 3
    for row in query_high_roi(client):
        addr = row.get("wallet", "")
        if addr and len(addr) > 30:
            if addr in all_candidates:
                all_candidates[addr]["source"] += "+high_roi"
            else:
                all_candidates[addr] = {"address": addr, "source": "dune_high_roi_45d",
                                         "roi": row.get("roi", 0),
                                         "net_pnl_usd": row.get("net_pnl_usd", 0)}
    time.sleep(3)

    # Query 4
    for row in query_fresh_active(client):
        addr = row.get("wallet", "")
        if addr and len(addr) > 30:
            if addr in all_candidates:
                all_candidates[addr]["source"] += "+fresh_7d"
            else:
                all_candidates[addr] = {"address": addr, "source": "dune_fresh_7d",
                                         "trade_count": row.get("trade_count", 0),
                                         "unique_tokens": row.get("unique_tokens", 0)}

    # Deduplicate
    total_raw = len(all_candidates)
    new_candidates = {k: v for k, v in all_candidates.items() if k not in existing}

    # Save
    report = {"timestamp": ts, "total_raw": total_raw,
              "already_known": total_raw - len(new_candidates),
              "new_candidates": len(new_candidates), "candidates": new_candidates}
    report_path = DATA_DIR / f"dune_extra_candidates_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    addr_path = DATA_DIR / f"dune_extra_addresses_{ts}.txt"
    with open(addr_path, "w") as f:
        for a in sorted(new_candidates.keys()):
            f.write(a + "\n")

    print(f"\n{'='*60}")
    print(f"  COLLECTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Total raw:       {total_raw}")
    print(f"  Already known:   {total_raw - len(new_candidates)}")
    print(f"  NEW candidates:  {len(new_candidates)}")
    print(f"  Saved: {addr_path.name}")

    sources = {}
    for c in new_candidates.values():
        s = c.get("source", "?")
        sources[s] = sources.get(s, 0) + 1
    print(f"\n  Sources:")
    for s, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"    {s}: {cnt}")

    multi = [a for a, c in new_candidates.items() if "+" in c.get("source", "")]
    print(f"\n  Multi-source: {len(multi)}")
    print(f"\n  Next: python scripts/validate_new_dune_batch.py --file {addr_path}")


if __name__ == "__main__":
    main()
