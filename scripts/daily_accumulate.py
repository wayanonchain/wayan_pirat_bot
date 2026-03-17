#!/usr/bin/env python3
"""
Daily Smart Money wallet accumulator.
Runs collect + validate, merges results with existing database.
Run daily via cron or manually to grow the wallet base over time.
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

import requests

NANSEN_API_KEY = "6yjpT19TBFEllBOLO4THBWf16n4GJaFp"
BASE_URL = "https://api.nansen.ai/api/v1"
HEADERS = {"apikey": NANSEN_API_KEY, "Content-Type": "application/json"}
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "wallet_database.json"
PNL_FROM = "2025-11-09T00:00:00Z"
PNL_TO = "2026-03-09T00:00:00Z"


def load_db() -> dict:
    """Load or initialize wallet database."""
    if DB_FILE.exists():
        with open(DB_FILE) as f:
            return json.load(f)
    return {"wallets": {}, "metadata": {"created": datetime.now().isoformat(), "total_runs": 0}}


def save_db(db: dict):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)


def fetch_dex_trades(label: str) -> list:
    url = f"{BASE_URL}/smart-money/dex-trades"
    payload = {
        "chains": ["solana"],
        "filters": {"include_smart_money_labels": [label]},
        "pagination": {"page": 1, "per_page": 1000}
    }
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=60)
        credits = resp.headers.get("X-Nansen-Credits-Remaining", "?")
        print(f"  [{label}] Credits remaining: {credits}")
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"  [{label}] Error: {e}")
        return []


def fetch_pnl(address: str) -> dict | None:
    url = f"{BASE_URL}/profiler/address/pnl-summary"
    payload = {"address": address, "chain": "solana", "date": {"from": PNL_FROM, "to": PNL_TO}}
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        credits = resp.headers.get("X-Nansen-Credits-Remaining", "?")
        print(f"    PnL credits remaining: {credits}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    PnL error: {e}")
        return None


def main():
    print(f"{'='*60}")
    print(f"DAILY WALLET ACCUMULATOR — {datetime.now().isoformat()}")
    print(f"{'='*60}")

    db = load_db()
    existing_count = len(db["wallets"])
    print(f"Existing wallets in DB: {existing_count}")

    # Step 1: Collect from all labels
    labels = ["180D Smart Trader", "Smart Trader", "90D Smart Trader", "30D Smart Trader"]
    new_addresses = set()

    for label in labels:
        trades = fetch_dex_trades(label)
        for t in trades:
            addr = t["trader_address"]
            if addr not in db["wallets"]:
                new_addresses.add(addr)
            # Update last seen
            if addr in db["wallets"]:
                db["wallets"][addr]["last_seen_in_dex_trades"] = datetime.now().isoformat()
                if label not in db["wallets"][addr].get("labels_seen", []):
                    db["wallets"][addr].setdefault("labels_seen", []).append(label)
        time.sleep(0.5)

    print(f"\nNew addresses found: {len(new_addresses)}")

    # Step 2: Validate new wallets
    validated_new = 0
    for addr in new_addresses:
        print(f"  Validating {addr[:16]}...")
        pnl = fetch_pnl(addr)
        if not pnl:
            continue

        realized_pnl = pnl.get("realized_pnl_usd", 0)
        win_rate = pnl.get("win_rate", 0)
        traded_times = pnl.get("traded_times", 0)

        passes = realized_pnl > 0 and win_rate >= 0.25 and traded_times >= 5

        db["wallets"][addr] = {
            "address": addr,
            "realized_pnl_usd": realized_pnl,
            "win_rate": win_rate,
            "traded_times": traded_times,
            "traded_token_count": pnl.get("traded_token_count", 0),
            "top5_tokens": pnl.get("top5_tokens", []),
            "validated": passes,
            "first_discovered": datetime.now().isoformat(),
            "last_seen_in_dex_trades": datetime.now().isoformat(),
            "labels_seen": [],
            "status": "ACTIVE" if passes else "REJECTED",
        }

        status = "PASS" if passes else "REJECT"
        print(f"    [{status}] PnL: ${realized_pnl:,.2f} | WR: {win_rate:.1%} | Trades: {traded_times}")

        if passes:
            validated_new += 1
        time.sleep(0.5)

    # Update metadata
    db["metadata"]["last_run"] = datetime.now().isoformat()
    db["metadata"]["total_runs"] = db["metadata"].get("total_runs", 0) + 1

    save_db(db)

    # Stats
    active = sum(1 for w in db["wallets"].values() if w.get("status") == "ACTIVE")
    total = len(db["wallets"])

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"New wallets added: {len(new_addresses)}")
    print(f"New validated: {validated_new}")
    print(f"Total in DB: {total}")
    print(f"Active (validated): {active}")
    print(f"Database: {DB_FILE}")


if __name__ == "__main__":
    main()
