#!/usr/bin/env python3
"""
Import wallets to SQLite, test all components, and verify readiness.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    from config.settings import DATA_DIR, TELEGRAM_BOT_TOKEN, HELIUS_API_KEY, TELEGRAM_CHAT_ID
    from db.repository import init_db, bulk_import_wallets, wallet_count, get_active_wallets
    from db.models import Base

    print("=" * 60)
    print("IMPORT & TEST — Wayne Pirate Bot")
    print("=" * 60)

    # === 1. Test config ===
    print("\n[1] Config check:")
    print(f"  Telegram Token: {'✅' if TELEGRAM_BOT_TOKEN else '❌'} ({TELEGRAM_BOT_TOKEN[:20]}...)")
    print(f"  Telegram Chat:  {'✅' if TELEGRAM_CHAT_ID else '❌'} ({TELEGRAM_CHAT_ID})")
    print(f"  Helius Key:     {'✅' if HELIUS_API_KEY else '❌'} ({HELIUS_API_KEY[:15]}...)")
    print(f"  Data dir:       {DATA_DIR}")

    # === 2. Init DB ===
    print("\n[2] Database initialization:")
    await init_db()
    print("  ✅ Tables created")

    # === 3. Import wallets ===
    print("\n[3] Wallet import:")
    json_db = DATA_DIR / "wallet_database.json"
    if json_db.exists():
        with open(json_db) as f:
            db = json.load(f)
        all_wallets = list(db["wallets"].values())
        active_wallets = [w for w in all_wallets if w.get("status") == "ACTIVE"]
        print(f"  JSON DB: {len(all_wallets)} total, {len(active_wallets)} active")

        imported = await bulk_import_wallets(active_wallets)
        print(f"  ✅ Imported {imported} new wallets to SQLite")
    else:
        print(f"  ❌ {json_db} not found!")

    counts = await wallet_count()
    print(f"  DB counts: {counts}")

    # === 4. Test Telegram ===
    print("\n[4] Telegram bot test:")
    try:
        from aiogram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        me = await bot.get_me()
        print(f"  ✅ Bot: @{me.username} ({me.first_name})")

        # Send test message
        msg = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="🏴‍☠️ <b>Wayne Pirate Bot — Test Message</b>\n\n"
                 f"✅ DB: {counts.get('ACTIVE', 0)} wallets loaded\n"
                 "✅ All systems operational",
            parse_mode="HTML",
        )
        print(f"  ✅ Test message sent (msg_id={msg.message_id})")
        await bot.session.close()
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")

    # === 5. Test Helius ===
    print("\n[5] Helius API test:")
    try:
        from api.helius_client import get_parsed_transactions
        wallets = await get_active_wallets()
        if wallets:
            test_addr = wallets[0].address
            txs = await get_parsed_transactions(test_addr, limit=1, tx_type="SWAP")
            print(f"  ✅ Got {len(txs)} transactions for {test_addr[:12]}...")
            if txs:
                print(f"    Type: {txs[0].get('type')}, sig: {txs[0].get('signature', '')[:20]}...")
    except Exception as e:
        print(f"  ❌ Helius error: {e}")

    # === 6. Test Signal Formatter ===
    print("\n[6] Signal formatter test:")
    from bot.formatters import format_signal_message
    test_signal = {
        "signal_id": 1,
        "token_address": "TestTokenMint111111111111111111111111111111",
        "token_symbol": "TEST",
        "mode": 1,
        "wallet_count": 2,
        "wallets": [
            {"address": "Wallet1111111111111111111111111111111111111", "amount_usd": 5000, "amount_sol": 40, "mcap_at_buy": 500000, "wallet_type": "TRADER", "pnl": 100000},
            {"address": "Wallet2222222222222222222222222222222222222", "amount_usd": 3000, "amount_sol": 24, "mcap_at_buy": 520000, "wallet_type": "BOT", "pnl": 50000},
        ],
        "total_buy_usd": 8000,
        "mcap": 520000,
        "token_age_hours": 2.5,
    }
    formatted = format_signal_message(test_signal)
    print(f"  ✅ Formatted message ({len(formatted)} chars):")
    # Print without HTML tags for readability
    import re
    clean = re.sub(r'<[^>]+>', '', formatted)
    for line in clean.split('\n'):
        print(f"    {line}")

    # === 7. Test Helius webhooks listing ===
    print("\n[7] Helius webhooks:")
    try:
        from api.helius_client import get_webhooks
        hooks = await get_webhooks()
        print(f"  Existing webhooks: {len(hooks)}")
        for h in hooks:
            print(f"    ID: {h.get('webhookID', '?')[:20]}... "
                  f"addresses: {len(h.get('accountAddresses', []))}")
    except Exception as e:
        print(f"  ❌ Webhooks error: {e}")

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETE")
    print("=" * 60)
    print(f"\nActive wallets ready for monitoring: {counts.get('ACTIVE', 0)}")
    print(f"\nTo start the bot:")
    print(f"  cd {Path(__file__).parent.parent}")
    print(f"  source venv/bin/activate")
    print(f"  python main.py")


if __name__ == "__main__":
    asyncio.run(main())
