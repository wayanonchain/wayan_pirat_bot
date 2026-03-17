#!/usr/bin/env python3
"""
Nansen Smart Money Wallet Collector for Solana
Collects unique Smart Money wallet addresses from Nansen DEX Trades API.
Saves results to JSON and CSV for further processing.
"""

import json
import time
import sys
import os
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Installing requests...")
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

# Config
NANSEN_API_KEY = "6yjpT19TBFEllBOLO4THBWf16n4GJaFp"
BASE_URL = "https://api.nansen.ai/api/v1"
HEADERS = {
    "apikey": NANSEN_API_KEY,
    "Content-Type": "application/json"
}
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def fetch_dex_trades(page: int = 1, per_page: int = 1000, label: str = "180D Smart Trader", min_usd: float = 100) -> dict:
    """Fetch Smart Money DEX trades from Nansen API."""
    url = f"{BASE_URL}/smart-money/dex-trades"
    payload = {
        "chains": ["solana"],
        "filters": {
            "include_smart_money_labels": [label],
            "trade_value_usd": {"min": min_usd}
        },
        "pagination": {"page": page, "per_page": per_page},
        "order_by": [{"field": "trade_value_usd", "direction": "DESC"}]
    }

    resp = requests.post(url, headers=HEADERS, json=payload, timeout=60)

    # Log credits usage
    credits_used = resp.headers.get("X-Nansen-Credits-Used", "unknown")
    credits_remaining = resp.headers.get("X-Nansen-Credits-Remaining", "unknown")
    print(f"  Credits used: {credits_used}, remaining: {credits_remaining}")

    resp.raise_for_status()
    return resp.json()


def collect_all_wallets():
    """Collect all unique Smart Money wallets from multiple pages and labels."""
    wallets = {}  # address -> wallet info
    all_trades = []

    labels = ["180D Smart Trader", "Smart Trader", "90D Smart Trader", "30D Smart Trader"]

    for label in labels:
        print(f"\n{'='*60}")
        print(f"Fetching label: {label}")
        print(f"{'='*60}")

        page = 1
        label_trades = 0

        while True:
            print(f"  Page {page}...")
            try:
                result = fetch_dex_trades(page=page, per_page=1000, label=label, min_usd=100)
            except requests.exceptions.HTTPError as e:
                print(f"  Error on page {page}: {e}")
                if e.response.status_code == 429:
                    print("  Rate limited! Waiting 60s...")
                    time.sleep(60)
                    continue
                break
            except Exception as e:
                print(f"  Unexpected error: {e}")
                break

            data = result.get("data", [])
            pagination = result.get("pagination", {})

            if not data:
                print(f"  No data on page {page}, stopping.")
                break

            for trade in data:
                addr = trade["trader_address"]
                label_str = trade.get("trader_address_label", "")

                if addr not in wallets:
                    wallets[addr] = {
                        "address": addr,
                        "label": label_str,
                        "labels_seen": set(),
                        "total_trades_seen": 0,
                        "total_volume_usd": 0.0,
                        "first_trade_seen": trade["block_timestamp"],
                        "last_trade_seen": trade["block_timestamp"],
                        "tokens_traded": set(),
                    }

                w = wallets[addr]
                w["labels_seen"].add(label)
                w["total_trades_seen"] += 1
                w["total_volume_usd"] += trade.get("trade_value_usd", 0)

                # Track timestamps
                ts = trade["block_timestamp"]
                if ts < w["first_trade_seen"]:
                    w["first_trade_seen"] = ts
                if ts > w["last_trade_seen"]:
                    w["last_trade_seen"] = ts

                # Track tokens (bought side, excluding SOL/USDC/USDT)
                bought = trade.get("token_bought_symbol", "")
                sold = trade.get("token_sold_symbol", "")
                stable_tokens = {"SOL", "USDC", "USDT", "WSOL"}
                if bought and bought not in stable_tokens:
                    w["tokens_traded"].add(bought)
                if sold and sold not in stable_tokens:
                    w["tokens_traded"].add(sold)

                all_trades.append(trade)
                label_trades += 1

            print(f"  Got {len(data)} trades, total wallets so far: {len(wallets)}")

            is_last = pagination.get("is_last_page", True)
            if is_last:
                print(f"  Last page reached for {label}.")
                break

            page += 1
            time.sleep(1)  # Rate limit courtesy

        print(f"  Total trades for {label}: {label_trades}")

    # Convert sets to lists for JSON serialization
    for addr in wallets:
        wallets[addr]["labels_seen"] = list(wallets[addr]["labels_seen"])
        wallets[addr]["tokens_traded"] = list(wallets[addr]["tokens_traded"])
        wallets[addr]["unique_tokens_count"] = len(wallets[addr]["tokens_traded"])

    return wallets, all_trades


def save_results(wallets: dict, all_trades: list):
    """Save collected data to files."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save all trades (raw)
    trades_file = DATA_DIR / f"nansen_dex_trades_{timestamp}.json"
    with open(trades_file, "w") as f:
        json.dump(all_trades, f, indent=2, default=str)
    print(f"\nSaved {len(all_trades)} trades to {trades_file}")

    # Save wallet summary
    wallet_list = sorted(wallets.values(), key=lambda w: w["total_volume_usd"], reverse=True)
    wallets_file = DATA_DIR / f"smart_money_wallets_{timestamp}.json"
    with open(wallets_file, "w") as f:
        json.dump(wallet_list, f, indent=2, default=str)
    print(f"Saved {len(wallet_list)} unique wallets to {wallets_file}")

    # Also save a simple list of addresses
    addresses_file = DATA_DIR / "smart_money_addresses.txt"
    with open(addresses_file, "w") as f:
        for w in wallet_list:
            f.write(f"{w['address']}\n")
    print(f"Saved address list to {addresses_file}")

    # Save CSV summary
    csv_file = DATA_DIR / f"smart_money_wallets_{timestamp}.csv"
    with open(csv_file, "w") as f:
        f.write("address,label,labels_count,total_trades,total_volume_usd,unique_tokens,first_trade,last_trade\n")
        for w in wallet_list:
            f.write(f"{w['address']},{w['label']},{len(w['labels_seen'])},{w['total_trades_seen']},"
                    f"{w['total_volume_usd']:.2f},{w['unique_tokens_count']},"
                    f"{w['first_trade_seen']},{w['last_trade_seen']}\n")
    print(f"Saved CSV summary to {csv_file}")

    # Print summary stats
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique wallets: {len(wallet_list)}")
    print(f"Total trades collected: {len(all_trades)}")
    print(f"Top 20 wallets by volume:")
    for i, w in enumerate(wallet_list[:20], 1):
        print(f"  {i:3d}. {w['address'][:12]}... | Vol: ${w['total_volume_usd']:>12,.2f} | "
              f"Trades: {w['total_trades_seen']:>4} | Tokens: {w['unique_tokens_count']:>3} | "
              f"Labels: {', '.join(w['labels_seen'])}")


if __name__ == "__main__":
    print("=" * 60)
    print("NANSEN SMART MONEY WALLET COLLECTOR")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    wallets, trades = collect_all_wallets()
    save_results(wallets, trades)

    print(f"\nCompleted: {datetime.now().isoformat()}")
