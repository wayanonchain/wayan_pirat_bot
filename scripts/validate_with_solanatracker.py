#!/usr/bin/env python3
"""
Validate Dune candidate wallets using SolanaTracker PnL API.
Filters for wallets with positive realized PnL.
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

import requests

SOLANATRACKER_API_KEY = "40a30d3c-b951-4c53-8629-b6dd89c08e4e"
DATA_DIR = Path(__file__).parent.parent / "data"

# Filter thresholds
MIN_REALIZED_PNL = 5000        # Minimum $5K realized PnL
MIN_TOKENS_TRADED = 5          # At least 5 different tokens
MIN_TOTAL_INVESTED = 1000      # At least $1K invested (not a dust wallet)


def fetch_pnl(wallet: str) -> dict | None:
    """Fetch PnL from SolanaTracker."""
    url = f"https://data.solanatracker.io/pnl/{wallet}"
    headers = {"x-api-key": SOLANATRACKER_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 429:
            print("    Rate limited, waiting 5s...")
            time.sleep(5)
            resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    Error: {e}")
        return None


def analyze_pnl(data: dict) -> dict:
    """Analyze PnL data from SolanaTracker."""
    tokens = data.get("tokens", {})
    if not tokens:
        return {"valid": False, "reason": "no token data"}

    total_realized = 0
    total_unrealized = 0
    total_invested = 0
    total_sold = 0
    wins = 0
    losses = 0

    for addr, info in tokens.items():
        realized = info.get("realized", 0) or 0
        unrealized = info.get("unrealized", 0) or 0
        invested = info.get("total_invested", 0) or 0
        sold = info.get("total_sold", 0) or 0

        total_realized += realized
        total_unrealized += unrealized
        total_invested += invested
        total_sold += sold

        if realized > 0:
            wins += 1
        elif realized < 0:
            losses += 1

    token_count = len(tokens)
    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

    return {
        "total_realized_pnl": total_realized,
        "total_unrealized_pnl": total_unrealized,
        "total_pnl": total_realized + total_unrealized,
        "total_invested": total_invested,
        "total_sold": total_sold,
        "roi": total_realized / total_invested if total_invested > 0 else 0,
        "tokens_traded": token_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "valid": True,
    }


def main():
    # Load candidates from Dune
    candidates_file = DATA_DIR / "dune_smart_candidates_addresses.txt"
    if not candidates_file.exists():
        print("Error: Run Dune collection first")
        sys.exit(1)

    addresses = [l.strip() for l in candidates_file.read_text().splitlines() if l.strip()]
    print(f"Loaded {len(addresses)} candidate addresses from Dune")

    # Load existing DB to skip already validated
    db_file = DATA_DIR / "wallet_database.json"
    db = json.load(open(db_file)) if db_file.exists() else {"wallets": {}, "metadata": {}}
    existing = set(db["wallets"].keys())
    print(f"Existing wallets in DB: {len(existing)}")

    # Skip already validated
    to_validate = [a for a in addresses if a not in existing]
    print(f"New to validate: {len(to_validate)}")

    # Limit batch size to avoid excessive API calls
    BATCH_SIZE = 200
    to_validate = to_validate[:BATCH_SIZE]
    print(f"Processing batch of {len(to_validate)} wallets...")

    validated = 0
    rejected = 0
    errors = 0

    for i, addr in enumerate(to_validate, 1):
        if i % 10 == 0:
            print(f"\n--- Progress: {i}/{len(to_validate)} (validated: {validated}, rejected: {rejected}) ---\n")

        print(f"[{i}/{len(to_validate)}] {addr[:20]}...", end=" ")

        pnl_data = fetch_pnl(addr)
        if not pnl_data:
            errors += 1
            print("ERROR")
            continue

        analysis = analyze_pnl(pnl_data)
        if not analysis["valid"]:
            print(f"SKIP: {analysis.get('reason')}")
            continue

        passes = (
            analysis["total_realized_pnl"] >= MIN_REALIZED_PNL and
            analysis["tokens_traded"] >= MIN_TOKENS_TRADED and
            analysis["total_invested"] >= MIN_TOTAL_INVESTED
        )

        status = "ACTIVE" if passes else "REJECTED"
        db["wallets"][addr] = {
            "address": addr,
            "realized_pnl_usd": analysis["total_realized_pnl"],
            "unrealized_pnl_usd": analysis["total_unrealized_pnl"],
            "total_pnl_usd": analysis["total_pnl"],
            "total_invested_usd": analysis["total_invested"],
            "roi": analysis["roi"],
            "win_rate": analysis["win_rate"],
            "wins": analysis["wins"],
            "losses": analysis["losses"],
            "traded_token_count": analysis["tokens_traded"],
            "validated": passes,
            "source": "dune+solanatracker",
            "first_discovered": datetime.now().isoformat(),
            "status": status,
        }

        if passes:
            validated += 1
            print(f"PASS | PnL: ${analysis['total_realized_pnl']:>12,.0f} | "
                  f"ROI: {analysis['roi']:>6.1%} | WR: {analysis['win_rate']:.0%} | "
                  f"Tokens: {analysis['tokens_traded']}")
        else:
            rejected += 1
            reasons = []
            if analysis["total_realized_pnl"] < MIN_REALIZED_PNL:
                reasons.append(f"PnL=${analysis['total_realized_pnl']:,.0f}")
            if analysis["tokens_traded"] < MIN_TOKENS_TRADED:
                reasons.append(f"tokens={analysis['tokens_traded']}")
            print(f"REJECT | {', '.join(reasons)}")

        time.sleep(0.3)  # Rate limit

    # Save updated DB
    db["metadata"]["last_solanatracker_validation"] = datetime.now().isoformat()
    db["metadata"]["total_wallets"] = len(db["wallets"])
    db["metadata"]["active_wallets"] = sum(1 for w in db["wallets"].values() if w.get("status") == "ACTIVE")

    with open(db_file, "w") as f:
        json.dump(db, f, indent=2, default=str)

    active = db["metadata"]["active_wallets"]
    total = db["metadata"]["total_wallets"]

    print(f"\n{'='*60}")
    print(f"VALIDATION COMPLETE")
    print(f"{'='*60}")
    print(f"This batch: validated={validated}, rejected={rejected}, errors={errors}")
    print(f"Database total: {total} wallets, {active} ACTIVE")
    print(f"Saved to: {db_file}")

    # Show top validated
    active_wallets = sorted(
        [w for w in db["wallets"].values() if w.get("status") == "ACTIVE"],
        key=lambda w: w.get("realized_pnl_usd", 0),
        reverse=True
    )
    print(f"\nTop 15 ACTIVE wallets by realized PnL:")
    for i, w in enumerate(active_wallets[:15], 1):
        src = w.get("source", "nansen")
        print(f"  {i:3d}. {w['address'][:16]}... "
              f"PnL: ${w['realized_pnl_usd']:>12,.0f} | "
              f"WR: {w.get('win_rate',0):.0%} | "
              f"Tokens: {w.get('traded_token_count',0):>3} | "
              f"Src: {src}")


if __name__ == "__main__":
    main()
