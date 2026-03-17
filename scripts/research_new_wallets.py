#!/usr/bin/env python3
"""
Research New Smart Money Wallets for Wayne Pirate Bot v2
========================================================
Sources:
1. SolanaTracker Top Traders (multiple periods, paginated)
2. Birdeye Top Traders per trending token
3. Nansen Smart Trader DEX trades
4. SolanaTracker PnL validation for each candidate

Criteria for ACTIVE:
- Realized PnL >= $5,000
- Win rate >= 15%
- Traded at least 5 tokens
- Not already in database
"""

import json
import time
import asyncio
import httpx
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path("/Users/mac/Documents/WAYNE_PIRATE/.env"))

SOLANATRACKER_API_KEY = os.getenv("SOLANATRACKER_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")

DATA_DIR = Path(__file__).parent.parent / "data"
WALLET_DB_PATH = DATA_DIR / "wallet_database.json"

# Validation thresholds
MIN_PNL_USD = 5000
MIN_WIN_RATE = 0.15
MIN_TOKENS_TRADED = 5

# Rate limit config
ST_DELAY = 4  # seconds between SolanaTracker requests
BE_DELAY = 1  # seconds between Birdeye requests


def load_existing_wallets() -> set:
    if not WALLET_DB_PATH.exists():
        return set()
    with open(WALLET_DB_PATH) as f:
        db = json.load(f)
    return set(db.get("wallets", {}).keys())


# ============================================================
# SOURCE 1: SolanaTracker Top Traders
# ============================================================

def collect_from_solanatracker_top(existing: set) -> set:
    """Collect top trader wallets from SolanaTracker leaderboard."""
    candidates = set()
    headers = {"x-api-key": SOLANATRACKER_API_KEY}
    base = "https://data.solanatracker.io"

    periods = ["1h", "6h", "24h"]
    max_pages = 8  # 25 per page × 8 = 200 per period

    for period in periods:
        print(f"\n  Period: {period}")
        for page in range(1, max_pages + 1):
            url = f"{base}/top-traders/all/{period}?page={page}"
            try:
                time.sleep(ST_DELAY)
                resp = httpx.get(url, headers=headers, timeout=15)

                if resp.status_code == 429:
                    print(f"    Rate limited on page {page}, waiting 30s...")
                    time.sleep(30)
                    resp = httpx.get(url, headers=headers, timeout=15)

                if resp.status_code != 200:
                    print(f"    Page {page}: HTTP {resp.status_code}")
                    break

                data = resp.json()
                wallets_data = data.get("wallets", [])
                has_next = data.get("hasNext", False)

                new_on_page = 0
                for w in wallets_data:
                    addr = w.get("wallet", "")
                    if addr and addr not in existing and addr not in candidates:
                        # Pre-filter: only add if realized PnL looks promising
                        summary = w.get("summary", {})
                        realized = summary.get("realized", 0)
                        if realized >= 1000:  # Low bar - full validation later
                            candidates.add(addr)
                            new_on_page += 1

                print(f"    Page {page}: {len(wallets_data)} wallets, {new_on_page} new candidates")

                if not has_next:
                    break

            except Exception as e:
                print(f"    Page {page} error: {e}")
                break

    return candidates


# ============================================================
# SOURCE 2: Birdeye Top Traders per Trending Token
# ============================================================

def collect_from_birdeye(existing: set, already_found: set) -> set:
    """Get top traders from trending tokens via Birdeye."""
    candidates = set()
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    combined_existing = existing | already_found

    # Step 1: Get trending tokens
    print("\n  Fetching trending tokens...")
    try:
        resp = httpx.get(
            "https://public-api.birdeye.so/defi/token_trending?sort_by=rank&sort_type=asc&limit=10",
            headers=headers, timeout=15
        )
        if resp.status_code != 200:
            print(f"  Trending tokens error: {resp.status_code}")
            return candidates

        tokens_data = resp.json().get("data", {}).get("tokens", [])
        token_addresses = [t.get("address", "") for t in tokens_data if t.get("address")]
        print(f"  Got {len(token_addresses)} trending tokens")

    except Exception as e:
        print(f"  Trending tokens error: {e}")
        return candidates

    # Step 2: Get top traders for each token
    for token_addr in token_addresses:
        symbol = next((t.get("symbol", "?") for t in tokens_data if t.get("address") == token_addr), "?")
        time.sleep(BE_DELAY)

        try:
            resp = httpx.get(
                f"https://public-api.birdeye.so/defi/v2/tokens/top_traders"
                f"?address={token_addr}&time_frame=24h&sort_type=volume&sort_order=desc&limit=10",
                headers=headers, timeout=15
            )

            if resp.status_code != 200:
                print(f"  {symbol}: HTTP {resp.status_code}")
                continue

            data = resp.json()
            traders = data.get("data", {}).get("traders", data.get("data", {}).get("items", []))
            if not isinstance(traders, list):
                traders = []

            new_count = 0
            for t in traders:
                addr = t.get("owner", t.get("address", ""))
                if addr and addr not in combined_existing and addr not in candidates:
                    candidates.add(addr)
                    new_count += 1

            print(f"  {symbol}: {len(traders)} traders, {new_count} new")

        except Exception as e:
            print(f"  {symbol}: error - {e}")

    return candidates


# ============================================================
# SOURCE 3: Nansen Smart Traders
# ============================================================

def collect_from_nansen(existing: set, already_found: set) -> set:
    """Collect new wallets from Nansen DEX trades."""
    candidates = set()
    combined = existing | already_found

    if not NANSEN_API_KEY:
        print("  No API key, skipping")
        return candidates

    url = "https://api.nansen.ai/api/v1/smart-money/dex-trades"
    headers = {"apikey": NANSEN_API_KEY, "Content-Type": "application/json"}

    labels = ["180D Smart Trader", "Smart Trader", "30D Smart Trader"]

    for label in labels:
        payload = {
            "chains": ["solana"],
            "filters": {
                "include_smart_money_labels": [label],
                "trade_value_usd": {"min": 500}
            },
            "pagination": {"page": 1, "per_page": 1000},
            "order_by": [{"field": "block_timestamp", "direction": "DESC"}]
        }

        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=60)
            credits = resp.headers.get("X-Nansen-Credits-Remaining", "?")
            print(f"  {label}: credits={credits}", end="")

            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                continue

            data = resp.json().get("data", [])
            new_count = 0
            for trade in data:
                addr = trade.get("trader_address", "")
                if addr and addr not in combined and addr not in candidates:
                    candidates.add(addr)
                    new_count += 1

            print(f" → {len(data)} trades, {new_count} new")

        except Exception as e:
            print(f" error: {e}")

        time.sleep(1)

    return candidates


# ============================================================
# VALIDATION via SolanaTracker PnL
# ============================================================

async def validate_wallet(address: str, session: httpx.AsyncClient) -> dict | None:
    """Validate a single wallet via SolanaTracker PnL."""
    url = f"https://data.solanatracker.io/pnl/{address}"
    headers = {"x-api-key": SOLANATRACKER_API_KEY}

    try:
        resp = await session.get(url, headers=headers)

        if resp.status_code == 429:
            return "RATE_LIMITED"
        if resp.status_code != 200:
            return None

        data = resp.json()

        # PnL API returns: { summary: {...}, tokens: {addr: {...}, ...}, pnl_since: ... }
        summary = data.get("summary", {})
        tokens_dict = data.get("tokens", {})

        realized_pnl = summary.get("realized", 0) or 0
        unrealized_pnl = summary.get("unrealized", 0) or 0
        total_invested = summary.get("totalInvested", 0) or 0
        win_pct = summary.get("winPercentage", 0) or 0
        win_rate = win_pct / 100.0  # Convert from percentage to ratio
        total_wins = summary.get("totalWins", 0) or 0
        total_losses = summary.get("totalLosses", 0) or 0

        # tokens is a dict {token_address: {holding, realized, ...}}
        traded_token_count = len(tokens_dict) if isinstance(tokens_dict, dict) else 0
        roi = (realized_pnl / total_invested) if total_invested > 0 else 0

        # Top tokens by realized PnL
        top_tokens = []
        if isinstance(tokens_dict, dict):
            token_list = [
                {"token_address": addr, **info}
                for addr, info in tokens_dict.items()
            ]
            sorted_t = sorted(token_list, key=lambda t: t.get("realized", 0), reverse=True)
            for t in sorted_t[:5]:
                invested = t.get("total_invested", 0) or 0
                realized = t.get("realized", 0) or 0
                top_tokens.append({
                    "realized_pnl": realized,
                    "realized_roi": (realized / invested) if invested > 0 else 0,
                    "token_address": t.get("token_address", ""),
                    "token_symbol": "",
                    "chain": "solana",
                })

        is_valid = (
            realized_pnl >= MIN_PNL_USD
            and win_rate >= MIN_WIN_RATE
            and traded_token_count >= MIN_TOKENS_TRADED
        )

        return {
            "address": address,
            "realized_pnl_usd": realized_pnl,
            "unrealized_pnl_usd": unrealized_pnl,
            "total_invested_usd": total_invested,
            "roi": roi,
            "win_rate": win_rate,
            "traded_token_count": traded_token_count,
            "top5_tokens": top_tokens,
            "source": "research_v2",
            "first_discovered": datetime.now().isoformat(),
            "validated": is_valid,
            "status": "ACTIVE" if is_valid else "REJECTED",
            "labels_seen": [],
        }

    except Exception as e:
        return None


async def validate_all(addresses: list[str]) -> list[dict]:
    """Validate all candidate wallets sequentially with rate limiting."""
    results = []
    total = len(addresses)
    active_count = 0
    rejected_count = 0

    async with httpx.AsyncClient(timeout=30) as session:
        for i, addr in enumerate(addresses, 1):
            if i % 25 == 1:
                print(f"\n  Progress: {i}/{total} (✅ {active_count} / ❌ {rejected_count})")

            result = await validate_wallet(addr, session)

            if result == "RATE_LIMITED":
                print(f"    Rate limited, waiting 30s...")
                await asyncio.sleep(30)
                result = await validate_wallet(addr, session)

            if result and result != "RATE_LIMITED":
                if result["status"] == "ACTIVE":
                    active_count += 1
                    pnl = result["realized_pnl_usd"]
                    wr = result["win_rate"]
                    print(f"    ✅ {addr[:16]}... PnL=${pnl:>10,.0f} WR={wr:.0%}")
                else:
                    rejected_count += 1

                results.append(result)

            await asyncio.sleep(ST_DELAY)

    return results


# ============================================================
# SAVE & REPORT
# ============================================================

def save_results(new_wallets: list[dict], existing_db: dict):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    active = [w for w in new_wallets if w["status"] == "ACTIVE"]
    rejected = [w for w in new_wallets if w["status"] == "REJECTED"]

    # Save research report
    research_file = DATA_DIR / f"research_v2_{timestamp}.json"
    with open(research_file, "w") as f:
        json.dump({
            "timestamp": timestamp,
            "criteria": {
                "min_pnl_usd": MIN_PNL_USD,
                "min_win_rate": MIN_WIN_RATE,
                "min_tokens_traded": MIN_TOKENS_TRADED,
            },
            "total_candidates_validated": len(new_wallets),
            "active": len(active),
            "rejected": len(rejected),
            "active_wallets": active,
            "rejected_wallets": rejected,
        }, f, indent=2, default=str)
    print(f"\nSaved research report to {research_file}")

    # Update main database with new wallets
    db = existing_db.copy()
    added_active = 0
    added_rejected = 0
    for w in new_wallets:
        addr = w["address"]
        if addr not in db["wallets"]:
            db["wallets"][addr] = w
            if w["status"] == "ACTIVE":
                added_active += 1
            else:
                added_rejected += 1

    if added_active + added_rejected > 0:
        backup_path = DATA_DIR / f"wallet_database_pre_research_{timestamp}.json"
        with open(backup_path, "w") as f:
            json.dump(existing_db, f, indent=2, default=str)
        print(f"Backed up database to {backup_path}")

        with open(WALLET_DB_PATH, "w") as f:
            json.dump(db, f, indent=2, default=str)
        print(f"Updated wallet_database.json")

    # Summary
    old_active = sum(1 for w in existing_db["wallets"].values() if w.get("status") == "ACTIVE")
    new_active_total = sum(1 for w in db["wallets"].values() if w.get("status") == "ACTIVE")

    print(f"\n{'='*60}")
    print(f"RESEARCH v2 SUMMARY")
    print(f"{'='*60}")
    print(f"Candidates validated:  {len(new_wallets)}")
    print(f"  ✅ Passed (ACTIVE):   {len(active)}")
    print(f"  ❌ Failed (REJECTED):  {len(rejected)}")
    print(f"")
    print(f"Database BEFORE:       {len(existing_db['wallets'])} total ({old_active} active)")
    print(f"Database AFTER:        {len(db['wallets'])} total ({new_active_total} active)")
    print(f"New ACTIVE added:      {added_active}")
    print(f"")

    if active:
        sorted_active = sorted(active, key=lambda w: w["realized_pnl_usd"], reverse=True)
        print(f"TOP NEW ACTIVE WALLETS:")
        for i, w in enumerate(sorted_active[:30], 1):
            print(f"  {i:3d}. {w['address'][:20]}... | PnL: ${w['realized_pnl_usd']:>12,.0f} | "
                  f"WR: {w['win_rate']:.0%} | Tokens: {w['traded_token_count']}")

    return added_active


# ============================================================
# MAIN
# ============================================================

async def main():
    print("=" * 60)
    print("WAYNE PIRATE — WALLET RESEARCH v2")
    print(f"Started: {datetime.now().isoformat()}")
    print(f"Criteria: PnL>=${MIN_PNL_USD:,} | WR>={MIN_WIN_RATE:.0%} | Tokens>={MIN_TOKENS_TRADED}")
    print("=" * 60)

    existing = load_existing_wallets()
    print(f"\nExisting wallets in DB: {len(existing)}")

    with open(WALLET_DB_PATH) as f:
        existing_db = json.load(f)

    # === Source 1: SolanaTracker Top Traders ===
    print(f"\n{'='*60}")
    print("SOURCE 1: SolanaTracker Top Traders")
    print(f"{'='*60}")
    st_candidates = collect_from_solanatracker_top(existing)
    print(f"\n  Total new from SolanaTracker: {len(st_candidates)}")

    # === Source 2: Birdeye Top Traders ===
    print(f"\n{'='*60}")
    print("SOURCE 2: Birdeye Top Traders (trending tokens)")
    print(f"{'='*60}")
    be_candidates = collect_from_birdeye(existing, st_candidates)
    print(f"\n  Total new from Birdeye: {len(be_candidates)}")

    # === Source 3: Nansen ===
    print(f"\n{'='*60}")
    print("SOURCE 3: Nansen Smart Traders")
    print(f"{'='*60}")
    ns_candidates = collect_from_nansen(existing, st_candidates | be_candidates)
    print(f"\n  Total new from Nansen: {len(ns_candidates)}")

    # Combine all
    all_candidates = st_candidates | be_candidates | ns_candidates
    print(f"\n{'='*60}")
    print(f"TOTAL UNIQUE CANDIDATES: {len(all_candidates)}")
    print(f"{'='*60}")

    if not all_candidates:
        print("\nNo new candidates found!")
        return

    # === Validate all ===
    print(f"\n{'='*60}")
    print("VALIDATION: SolanaTracker PnL Check")
    print(f"{'='*60}")
    print(f"Estimated time: ~{len(all_candidates) * ST_DELAY // 60} minutes")

    validated = await validate_all(list(all_candidates))

    # === Save ===
    added = save_results(validated, existing_db)

    print(f"\nCompleted: {datetime.now().isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
