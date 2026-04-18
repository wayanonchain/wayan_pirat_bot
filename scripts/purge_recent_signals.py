#!/usr/bin/env python3
"""One-shot cleanup: delete recent bot-sent signal messages from the
``TELEGRAM_CHAT_ID`` chat. Reads ``signals.telegram_message_id`` for every
row sent in the last N hours and issues ``bot.delete_message`` for each.

Usage:
    python scripts/purge_recent_signals.py [HOURS]

Notes:
- Defaults to the last 24 hours.
- Telegram allows bots to delete their own messages; for DMs there's no
  48h limit (that restriction is for groups/channels). 400 errors on
  individual messages are logged and skipped.
- The log-chat thread is NOT cleaned here — we never persisted those
  mirror message IDs. Use Telegram's "Clear Topic" in the UI instead.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta

# Make project modules importable when invoked from the repo root.
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from aiogram import Bot
from sqlalchemy import select

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db.models import Signal
from db.repository import async_session


async def purge(hours: int) -> None:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(Signal).where(
                    Signal.sent_to_telegram.is_(True),
                    Signal.telegram_message_id.is_not(None),
                    Signal.created_at >= cutoff,
                )
            )).scalars().all()

        print(f"Found {len(rows)} signals sent in last {hours}h — deleting from chat {TELEGRAM_CHAT_ID}")

        deleted = 0
        skipped = 0
        for sig in rows:
            try:
                await bot.delete_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    message_id=sig.telegram_message_id,
                )
                deleted += 1
            except Exception as e:
                skipped += 1
                print(f"  skip signal_id={sig.id} msg_id={sig.telegram_message_id}: {e}")

        print(f"Done: deleted={deleted}, skipped={skipped}")
    finally:
        await bot.session.close()


def main() -> None:
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    asyncio.run(purge(hours))


if __name__ == "__main__":
    main()
