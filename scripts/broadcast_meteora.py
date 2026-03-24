#!/usr/bin/env python3
"""
One-time broadcast: notify all existing subscribers about the new Meteora course.
Run: python -m scripts.broadcast_meteora
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import TELEGRAM_BOT_TOKEN
from db.repository import get_all_subscribers_by_tier, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MESSAGE = (
    "Ребята, спасибо всем, кто уже пользуется нашим ботом "
    "и изучает материалы внутри ❤️\n\n"
    "Напоминаю, что у нас появился <b>новый курс по Meteora</b>.\n"
    "Там собрали полезные вещи по стратегиям и в целом постарались "
    "сделать материал таким, чтобы он реально расширял понимание рынка, "
    "а не был просто теорией ради теории.\n\n"
    "Плюс внутри есть <b>бесплатный модуль</b>, чтобы вы могли сначала "
    "ознакомиться, посмотреть подачу и понять, хотите ли идти в тему глубже.\n\n"
    "Нажмите /meteora чтобы узнать подробнее 👇"
)


async def main():
    from aiogram import Bot

    await init_db()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Get ALL subscribers (tier="free" returns everyone)
    subscribers = await get_all_subscribers_by_tier("free")
    total = len(subscribers)
    logger.info(f"Broadcasting to {total} subscribers...")

    sent = 0
    failed = 0
    blocked = 0

    for sub in subscribers:
        try:
            await bot.send_message(
                chat_id=sub.user_id,
                text=MESSAGE,
                parse_mode="HTML",
            )
            sent += 1
            if sent % 20 == 0:
                logger.info(f"Progress: {sent}/{total} sent")
            # Telegram rate limit: ~30 msg/sec, stay safe
            await asyncio.sleep(0.05)
        except Exception as e:
            err = str(e)
            if "blocked" in err.lower() or "deactivated" in err.lower():
                blocked += 1
            else:
                failed += 1
                logger.warning(f"Failed to send to {sub.user_id}: {e}")

    logger.info(
        f"Broadcast complete: {sent} sent, {blocked} blocked/deactivated, {failed} failed "
        f"(total {total})"
    )
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
