#!/usr/bin/env python3
"""
Test the full pipeline: simulate a Helius webhook → signal detection → Telegram alert.
Sends a fake SWAP webhook to the local server to verify everything works end-to-end.
"""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

WEBHOOK_URL = "http://localhost:8080/webhook/helius"

# Fake SWAP transaction mimicking Helius enhanced format
# Uses two different monitored wallets buying the same token
FAKE_TOKEN = "FakeTestToken111111111111111111111111111111"


async def main():
    from db.repository import init_db, get_active_addresses
    from api.birdeye_client import get_sol_price

    await init_db()
    addresses = await get_active_addresses()
    if len(addresses) < 2:
        print("Need at least 2 active wallets in DB")
        return

    wallet1 = addresses[0]
    wallet2 = addresses[1]

    # Test 1: SOL price from Birdeye
    print("[1] Testing Birdeye SOL price...")
    sol_price = await get_sol_price()
    print(f"    SOL price: ${sol_price:.2f}")

    # Test 2: Health check
    print("\n[2] Testing webhook server health...")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get("http://localhost:8080/health")
            print(f"    Health: {resp.json()}")
        except Exception as e:
            print(f"    ERROR: Server not running? {e}")
            print("    Start it: uvicorn webhook.server:app --host 0.0.0.0 --port 8080")
            return

    # Test 3: Send fake swap from wallet1
    print(f"\n[3] Sending fake SWAP from wallet1: {wallet1[:12]}...")
    tx1 = make_swap_tx(wallet1, FAKE_TOKEN, sol_amount=5_000_000_000, sig="testsig_wallet1_" + wallet1[:8])
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(WEBHOOK_URL, json=[tx1])
        print(f"    Response: {resp.json()}")

    # Test 4: Send fake swap from wallet2 (should trigger signal!)
    print(f"\n[4] Sending fake SWAP from wallet2: {wallet2[:12]}...")
    tx2 = make_swap_tx(wallet2, FAKE_TOKEN, sol_amount=3_000_000_000, sig="testsig_wallet2_" + wallet2[:8])
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(WEBHOOK_URL, json=[tx2])
        print(f"    Response: {resp.json()}")

    print("\n" + "=" * 50)
    print("If signal detection works, you should see a Telegram alert!")
    print("Check @wayanonchain_bot for the signal message.")
    print("=" * 50)


def make_swap_tx(wallet: str, token_mint: str, sol_amount: int, sig: str) -> dict:
    """Create a fake Helius enhanced SWAP transaction."""
    return {
        "type": "SWAP",
        "signature": sig,
        "feePayer": wallet,
        "timestamp": 1709999999,
        "events": {
            "swap": {
                "tokenInputs": [],
                "tokenOutputs": [
                    {
                        "mint": token_mint,
                        "rawTokenAmount": {
                            "tokenAmount": "1000000000",
                            "decimals": 6,
                        },
                        "tokenStandard": "TEST",
                    }
                ],
                "nativeInput": {"amount": sol_amount},
                "nativeOutput": {},
            }
        },
        "tokenTransfers": [],
        "nativeTransfers": [],
        "accountData": [],
    }


if __name__ == "__main__":
    asyncio.run(main())
