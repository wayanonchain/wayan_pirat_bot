"""One-time broadcast: psychology course announcement."""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot
from config.settings import TELEGRAM_BOT_TOKEN
from db.repository import get_all_subscribers_by_tier, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MESSAGE = (
    "Ребята, привет 👋\n\n"
    "Добавили в бота курс по психологии трейдинга.\n\n"
    "Страх входа после убытков, revenge trading, ранние выходы — "
    "если сталкивались, там разобрали основные боли и как с ними работать.\n\n"
    "Курс полностью бесплатный.\n\n"
    "Нажми /psychology чтобы получить доступ."
)


async def main():
    await init_db()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    subs = await get_all_subscribers_by_tier("free")  # all users
    total = len(subs)
    sent = 0
    failed = 0
    blocked = 0

    logger.info(f"Broadcasting to {total} users...")

    for i, sub in enumerate(subs):
        try:
            await bot.send_message(
                chat_id=sub.user_id,
                text=MESSAGE,
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            err = str(e)
            if "blocked" in err.lower() or "deactivated" in err.lower():
                blocked += 1
            else:
                failed += 1
                logger.warning(f"Failed {sub.user_id}: {e}")

        # Telegram rate limit: ~30 msg/sec
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1.0)
            logger.info(f"Progress: {i+1}/{total} (sent={sent}, blocked={blocked}, failed={failed})")

    await bot.session.close()
    logger.info(f"Done! sent={sent}, blocked={blocked}, failed={failed}, total={total}")


if __name__ == "__main__":
    asyncio.run(main())
