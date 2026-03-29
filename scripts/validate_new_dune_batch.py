#!/usr/bin/env python3
"""
Validate new Dune candidates via SolanaTracker PnL API.
=======================================================
Reads the latest dune_3000_addresses_*.txt file,
validates each wallet, and updates wallet_database.json.

Usage:
    python scripts/validate_new_dune_batch.py [--batch-size 500] [--resume]
"""

import json
import time
import sys
import argparse
from datetime import datetime
from pathlib import Path

import requests

SOLANATRACKER_API_KEY = "40a30d3c-b951-4c53-8629-b6dd89c08e4e"
DATA_DIR = Path(__file__).parent.parent / "data"
WALLET_DB_PATH = DATA_DIR / "wallet_database.json"
PROGRESS_FILE = DATA_DIR / "validation_progress.json"

# Validation thresholds
MIN_REALIZED_PNL = 5000     # $5K min realized PnL
MIN_TOKENS_TRADED = 5       # At least 5 tokens
MIN_TOTAL_INVESTED = 1000   # At least $1K invested
MIN_WIN_RATE = 0.15         # 15% win rate

RATE_LIMIT_DELAY = 1.5      # seconds between requests (was 0.4 — too fast, causes rate limits)


def fetch_pnl(wallet: str) -> dict | None:
    url = f"https://data.solanatracker.io/pnl/{wallet}"
    headers = {"x-api-key": SOLANATRACKER_API_KEY}
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return {"tokens": {}}
            if resp.status_code in (403, 500, 502, 503):
                wait = 5 * (attempt + 1)
                print(f"    {resp.status_code} error, waiting {wait}s (retry {attempt+1}/3)...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.SSLError, requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 5 * (attempt + 1)
            print(f"    Connection issue, waiting {wait}s (retry {attempt+1}/3)...")
            time.sleep(wait)
        except Exception as e:
            print(f"    Error: {e}")
            time.sleep(3)
    return None


def analyze_pnl(data: dict) -> dict:
    tokens = data.get("tokens", {})
    if not tokens:
        return {"valid": False, "reason": "no token data"}

    total_realized = 0
    total_unrealized = 0
    total_invested = 0
    wins = 0
    losses = 0

    for addr, info in tokens.items():
        realized = info.get("realized", 0) or 0
        unrealized = info.get("unrealized", 0) or 0
        invested = info.get("total_invested", 0) or 0

        total_realized += realized
        total_unrealized += unrealized
        total_invested += invested

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
        "roi": total_realized / total_invested if total_invested > 0 else 0,
        "tokens_traded": token_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "valid": True,
    }


def find_latest_candidates() -> Path | None:
    """Find the most recent dune_3000_addresses file."""
    files = sorted(DATA_DIR.glob("dune_3000_addresses_*.txt"), reverse=True)
    return files[0] if files else None


def load_progress() -> set:
    """Load already-validated addresses from progress file."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            data = json.load(f)
        return set(data.get("validated_addresses", []))
    return set()


def save_progress(validated: set):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"validated_addresses": list(validated), "last_update": datetime.now().isoformat()}, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500, help="Max wallets to validate in one run")
    parser.add_argument("--resume", action="store_true", help="Resume from last progress")
    parser.add_argument("--file", type=str, help="Specific address file to use")
    args = parser.parse_args()

    # Find candidate file
    if args.file:
        addr_file = Path(args.file)
    else:
        addr_file = find_latest_candidates()

    if not addr_file or not addr_file.exists():
        print("❌ No candidate address file found. Run collect_dune_3000.py first.")
        sys.exit(1)

    print(f"📄 Using: {addr_file.name}")

    # Load addresses
    addresses = [l.strip() for l in addr_file.read_text().splitlines() if l.strip()]
    print(f"   Total candidates: {len(addresses)}")

    # Load existing DB
    if WALLET_DB_PATH.exists():
        with open(WALLET_DB_PATH) as f:
            db = json.load(f)
    else:
        db = {"wallets": {}, "metadata": {}}

    # Ensure correct structure
    if "wallets" not in db:
        db = {"wallets": db, "metadata": {}}

    existing = set(db["wallets"].keys())
    print(f"   Already in DB: {len(existing)}")

    # Filter out already-in-DB
    to_validate = [a for a in addresses if a not in existing]

    # Resume support
    if args.resume:
        already_done = load_progress()
        to_validate = [a for a in to_validate if a not in already_done]
        print(f"   Resuming, skipping {len(already_done)} already validated")
    else:
        already_done = set()

    # Apply batch size
    to_validate = to_validate[:args.batch_size]
    print(f"   Validating this batch: {len(to_validate)}")

    if not to_validate:
        print("✅ Nothing to validate!")
        return

    # Validate
    validated_count = 0
    rejected_count = 0
    errors = 0
    start_time = time.time()

    for i, addr in enumerate(to_validate, 1):
        if i % 25 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed * 60
            eta_min = (len(to_validate) - i) / rate if rate > 0 else 0
            print(f"\n--- [{i}/{len(to_validate)}] "
                  f"active={validated_count} rejected={rejected_count} errors={errors} "
                  f"({rate:.0f}/min, ETA ~{eta_min:.0f}min) ---\n")

        print(f"[{i}/{len(to_validate)}] {addr[:20]}...", end=" ")

        pnl_data = fetch_pnl(addr)
        if pnl_data is None:
            errors += 1
            already_done.add(addr)  # skip on resume
            print("ERROR")
            time.sleep(RATE_LIMIT_DELAY)
            continue

        analysis = analyze_pnl(pnl_data)
        if not analysis["valid"]:
            print(f"SKIP ({analysis.get('reason', '?')})")
            already_done.add(addr)
            time.sleep(RATE_LIMIT_DELAY)
            continue

        passes = (
            analysis["total_realized_pnl"] >= MIN_REALIZED_PNL
            and analysis["tokens_traded"] >= MIN_TOKENS_TRADED
            and analysis["total_invested"] >= MIN_TOTAL_INVESTED
            and analysis["win_rate"] >= MIN_WIN_RATE
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
            "source": "dune_3000_batch",
            "first_discovered": datetime.now().isoformat(),
            "status": status,
        }

        already_done.add(addr)

        if passes:
            validated_count += 1
            print(f"✅ PnL: ${analysis['total_realized_pnl']:>10,.0f} | "
                  f"WR: {analysis['win_rate']:.0%} | "
                  f"Tokens: {analysis['tokens_traded']}")
        else:
            rejected_count += 1
            reasons = []
            if analysis["total_realized_pnl"] < MIN_REALIZED_PNL:
                reasons.append(f"PnL=${analysis['total_realized_pnl']:,.0f}")
            if analysis["win_rate"] < MIN_WIN_RATE:
                reasons.append(f"WR={analysis['win_rate']:.0%}")
            if analysis["tokens_traded"] < MIN_TOKENS_TRADED:
                reasons.append(f"tokens={analysis['tokens_traded']}")
            print(f"❌ {', '.join(reasons)}")

        time.sleep(RATE_LIMIT_DELAY)

        # Auto-save every 50
        if i % 50 == 0:
            save_progress(already_done)
            with open(WALLET_DB_PATH, "w") as f:
                json.dump(db, f, indent=2, default=str)
            print("   [auto-saved]")

    # Final save
    active_total = sum(1 for w in db["wallets"].values() if w.get("status") == "ACTIVE")
    db["metadata"]["last_validation"] = datetime.now().isoformat()
    db["metadata"]["total_wallets"] = len(db["wallets"])
    db["metadata"]["active_wallets"] = active_total

    with open(WALLET_DB_PATH, "w") as f:
        json.dump(db, f, indent=2, default=str)
    save_progress(already_done)

    elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"  VALIDATION COMPLETE ({elapsed/60:.1f} min)")
    print(f"{'='*60}")
    print(f"  This batch:  ✅ {validated_count} active  |  ❌ {rejected_count} rejected  |  ⚠️ {errors} errors")
    print(f"  Database:    {len(db['wallets'])} total  |  {active_total} ACTIVE")
    print(f"")
    print(f"  Pass rate: {validated_count/(validated_count+rejected_count)*100:.1f}%" if (validated_count+rejected_count) > 0 else "")
    print(f"  Saved to: {WALLET_DB_PATH}")

    # Remaining to validate
    remaining = len(addresses) - len(existing) - len(already_done)
    if remaining > 0:
        print(f"\n  ⏳ {remaining} addresses remaining. Run with --resume to continue.")


if __name__ == "__main__":
    main()
