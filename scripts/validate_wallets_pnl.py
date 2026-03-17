#!/usr/bin/env python3
"""
Validate Smart Money wallets by fetching PnL data from Nansen Profiler.
Filters wallets by realized PnL, win rate, and trade count.
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

import requests

NANSEN_API_KEY = "6yjpT19TBFEllBOLO4THBWf16n4GJaFp"
BASE_URL = "https://api.nansen.ai/api/v1"
HEADERS = {
    "apikey": NANSEN_API_KEY,
    "Content-Type": "application/json"
}
DATA_DIR = Path(__file__).parent.parent / "data"

# PnL period: last 4 months
PNL_FROM = "2025-11-09T00:00:00Z"
PNL_TO = "2026-03-09T00:00:00Z"

# Filter thresholds
MIN_REALIZED_PNL = 0           # Must be profitable
MIN_WIN_RATE = 0.25             # At least 25% win rate
MIN_TRADED_TIMES = 5            # At least 5 trades


def fetch_pnl_summary(address: str) -> dict:
    """Fetch PnL summary for a wallet from Nansen Profiler."""
    url = f"{BASE_URL}/profiler/address/pnl-summary"
    payload = {
        "address": address,
        "chain": "solana",
        "date": {
            "from": PNL_FROM,
            "to": PNL_TO
        }
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    credits_used = resp.headers.get("X-Nansen-Credits-Used", "?")
    credits_remaining = resp.headers.get("X-Nansen-Credits-Remaining", "?")
    print(f"    Credits: used={credits_used}, remaining={credits_remaining}")
    resp.raise_for_status()
    return resp.json()


def fetch_address_labels(address: str) -> dict:
    """Fetch labels for a wallet."""
    url = f"{BASE_URL}/profiler/address/labels"
    payload = {
        "address": address,
        "chain": "solana"
    }
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    Labels error: {e}")
        return {}


def main():
    # Load addresses
    addresses_file = DATA_DIR / "smart_money_addresses.txt"
    if not addresses_file.exists():
        print(f"Error: {addresses_file} not found. Run collect_nansen_wallets.py first.")
        sys.exit(1)

    addresses = [line.strip() for line in addresses_file.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(addresses)} addresses for validation")

    # Load existing wallet data for enrichment
    wallet_files = sorted(DATA_DIR.glob("smart_money_wallets_*.json"), reverse=True)
    existing_wallets = {}
    if wallet_files:
        with open(wallet_files[0]) as f:
            for w in json.load(f):
                existing_wallets[w["address"]] = w

    validated = []
    rejected = []

    for i, addr in enumerate(addresses, 1):
        print(f"\n[{i}/{len(addresses)}] Validating {addr[:16]}...")

        try:
            pnl_data = fetch_pnl_summary(addr)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print("    Rate limited! Waiting 60s...")
                time.sleep(60)
                try:
                    pnl_data = fetch_pnl_summary(addr)
                except:
                    print(f"    Still failing, skipping.")
                    continue
            else:
                print(f"    Error: {e}")
                continue
        except Exception as e:
            print(f"    Error: {e}")
            continue

        realized_pnl = pnl_data.get("realized_pnl_usd", 0)
        win_rate = pnl_data.get("win_rate", 0)
        traded_times = pnl_data.get("traded_times", 0)
        traded_tokens = pnl_data.get("traded_token_count", 0)
        top5 = pnl_data.get("top5_tokens", [])

        wallet_info = {
            "address": addr,
            "realized_pnl_usd": realized_pnl,
            "win_rate": win_rate,
            "traded_times": traded_times,
            "traded_token_count": traded_tokens,
            "top5_tokens": top5,
            "pnl_period": f"{PNL_FROM} to {PNL_TO}",
            "validated_at": datetime.now().isoformat(),
        }

        # Merge with existing data
        if addr in existing_wallets:
            wallet_info["nansen_label"] = existing_wallets[addr].get("label", "")
            wallet_info["nansen_labels_seen"] = existing_wallets[addr].get("labels_seen", [])
            wallet_info["total_volume_usd_24h"] = existing_wallets[addr].get("total_volume_usd", 0)

        # Apply filters
        passes = (
            realized_pnl >= MIN_REALIZED_PNL and
            win_rate >= MIN_WIN_RATE and
            traded_times >= MIN_TRADED_TIMES
        )

        status = "PASS" if passes else "REJECT"
        reason = ""
        if not passes:
            reasons = []
            if realized_pnl < MIN_REALIZED_PNL:
                reasons.append(f"PnL={realized_pnl:.2f} < {MIN_REALIZED_PNL}")
            if win_rate < MIN_WIN_RATE:
                reasons.append(f"WinRate={win_rate:.2%} < {MIN_WIN_RATE:.0%}")
            if traded_times < MIN_TRADED_TIMES:
                reasons.append(f"Trades={traded_times} < {MIN_TRADED_TIMES}")
            reason = ", ".join(reasons)
            wallet_info["reject_reason"] = reason
            rejected.append(wallet_info)
        else:
            validated.append(wallet_info)

        print(f"    [{status}] PnL: ${realized_pnl:>12,.2f} | WR: {win_rate:.1%} | "
              f"Trades: {traded_times} | Tokens: {traded_tokens}")
        if reason:
            print(f"    Reason: {reason}")

        time.sleep(0.5)  # Rate limit courtesy

    # Save validated wallets
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    validated_file = DATA_DIR / f"validated_wallets_{timestamp}.json"
    with open(validated_file, "w") as f:
        json.dump(validated, f, indent=2, default=str)

    # Also save as the "current" validated set
    current_file = DATA_DIR / "validated_wallets_current.json"
    with open(current_file, "w") as f:
        json.dump(validated, f, indent=2, default=str)

    rejected_file = DATA_DIR / f"rejected_wallets_{timestamp}.json"
    with open(rejected_file, "w") as f:
        json.dump(rejected, f, indent=2, default=str)

    # Summary
    print(f"\n{'='*60}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total wallets checked: {len(addresses)}")
    print(f"Validated (PASS): {len(validated)}")
    print(f"Rejected: {len(rejected)}")
    print(f"\nValidated wallets saved to: {validated_file}")
    print(f"Current set: {current_file}")

    if validated:
        print(f"\nTop validated wallets:")
        for i, w in enumerate(sorted(validated, key=lambda x: x["realized_pnl_usd"], reverse=True)[:10], 1):
            print(f"  {i}. {w['address'][:16]}... PnL: ${w['realized_pnl_usd']:>12,.2f} | "
                  f"WR: {w['win_rate']:.1%} | Trades: {w['traded_times']}")


if __name__ == "__main__":
    main()
