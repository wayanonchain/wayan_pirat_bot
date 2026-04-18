#!/usr/bin/env python3
"""
Wayne Pirate Smart Money Bot — Main entry point.

Runs:
1. SQLite DB initialization + wallet import
2. Telegram bot (polling)
3. Webhook server (FastAPI/uvicorn) for Helius webhooks
"""

import asyncio
import json
import logging
import resource
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

LOG_DIR = Path(__file__).parent.parent
LOG_FILE = LOG_DIR / "bot_log.txt"

rotating_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
rotating_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        rotating_handler,
    ]
)
logger = logging.getLogger("main")


def _raise_fd_limit():
    # Default RLIMIT_NOFILE on many VPS images is 1024. Under 8000 Helius
    # webhook events/hour plus aiohttp connection pools that's close to the
    # ceiling, and a leaked session on restart can push us over.
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(hard, 65536)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            logger.info(f"Raised RLIMIT_NOFILE: {soft} → {target}")
    except Exception as e:
        logger.warning(f"Could not raise RLIMIT_NOFILE: {e}")


async def init_database():
    """Initialize DB and import wallets from JSON."""
    from db.repository import init_db, bulk_import_wallets, wallet_count
    from config.settings import DATA_DIR

    logger.info("Initializing database...")
    await init_db()

    counts = await wallet_count()
    if not counts:
        json_db = DATA_DIR / "wallet_database.json"
        if json_db.exists():
            logger.info("Importing wallets from JSON database...")
            with open(json_db) as f:
                db = json.load(f)
            wallets = [w for w in db["wallets"].values() if w.get("status") == "ACTIVE"]
            imported = await bulk_import_wallets(wallets)
            logger.info(f"Imported {imported} wallets")
        else:
            logger.warning(f"No wallet database found at {json_db}")

    counts = await wallet_count()
    logger.info(f"Wallet counts: {counts}")


async def setup_helius_webhook():
    """Check for existing Helius webhooks, create one if needed."""
    from config.settings import HELIUS_WEBHOOK_URL, HELIUS_API_KEY
    from api.helius_client import get_webhooks, create_webhook
    from db.repository import get_active_addresses

    if not HELIUS_WEBHOOK_URL:
        logger.warning("HELIUS_WEBHOOK_URL not set — webhook will not be registered. "
                        "Set it in .env and restart to enable signal detection.")
        return

    if not HELIUS_API_KEY:
        logger.warning("HELIUS_API_KEY not set — cannot register webhook.")
        return

    try:
        existing = await get_webhooks()

        for wh in existing:
            wh_id = wh.get("webhookID")
            wh_url = wh.get("webhookURL", "")

            if wh_url == HELIUS_WEBHOOK_URL:
                logger.info(f"Helius webhook already configured: {wh_id}")
                return

            if wh_url != HELIUS_WEBHOOK_URL:
                from api.helius_client import update_webhook_url
                logger.info(f"Updating webhook {wh_id} URL: {wh_url} -> {HELIUS_WEBHOOK_URL}")
                ok = await update_webhook_url(wh_id, HELIUS_WEBHOOK_URL)
                if ok:
                    logger.info(f"Webhook URL updated successfully")
                else:
                    logger.error(f"Failed to update webhook URL")
                return

        addresses = await get_active_addresses()
        if not addresses:
            logger.warning("No active wallet addresses to monitor.")
            return

        logger.info(f"Creating Helius webhook for {len(addresses)} addresses -> {HELIUS_WEBHOOK_URL}")
        result = await create_webhook(addresses, HELIUS_WEBHOOK_URL)
        if result:
            logger.info(f"Helius webhook created: {result.get('webhookID')}")
        else:
            logger.error("Failed to create Helius webhook.")
    except Exception as e:
        logger.error(f"Error setting up Helius webhook: {e}")


async def run_telegram_bot():
    """Run Telegram bot in polling mode."""
    from bot.telegram_bot import start_polling
    try:
        await start_polling()
    except Exception as e:
        logger.error(f"Telegram bot error: {e}")


def run_webhook_server():
    """Run FastAPI webhook server in a separate thread."""
    from config.settings import WEBHOOK_HOST, WEBHOOK_PORT
    from webhook.server import app

    uvicorn.run(
        app,
        host=WEBHOOK_HOST,
        port=WEBHOOK_PORT,
        log_level="info",
    )


async def shutdown():
    """Release long-lived resources on exit.

    Without this, every systemctl restart leaks the aiohttp session opened by
    the aiogram Bot and the SQLAlchemy connection pool — which is what caused
    the ``OSError: [Errno 24] Too many open files`` observed 2026-04-18.
    """
    logger.info("Shutdown: releasing resources...")

    try:
        from core.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as e:
        logger.error(f"Scheduler stop error: {e}")

    try:
        from bot.telegram_bot import stop_bot
        await stop_bot()
    except Exception as e:
        logger.error(f"Bot session close error: {e}")

    try:
        from db.repository import engine
        await engine.dispose()
    except Exception as e:
        logger.error(f"DB engine dispose error: {e}")

    logger.info("Shutdown complete.")


async def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("WAYNE PIRATE SMART MONEY BOT — Starting...")
    logger.info("=" * 60)

    _raise_fd_limit()

    await init_database()

    from bot.telegram_bot import send_message
    from db.repository import wallet_count
    counts = await wallet_count()
    active = counts.get("ACTIVE", 0)

    await send_message(
        f"<b>Wayne Pirate Bot Started</b>\n\n"
        f"Smart Money wallets: {active}\n\n"
        f"Use /status for details",
    )

    logger.info(f"Startup notification sent. {active} wallets in DB.")

    # Helius webhook server — feeds `token_buys` with real SM trades which
    # in turn powers the accumulation-module discovery job.
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    logger.info("Webhook server started on background thread")
    await setup_helius_webhook()

    # Start scheduler
    from core.scheduler import start_scheduler
    start_scheduler()
    logger.info("Scheduler started")

    # Run Telegram bot (blocking until SIGTERM / SIGINT — aiogram installs its
    # own signal handlers on start_polling, so we rely on try/finally below
    # for cleanup rather than add_signal_handler here).
    logger.info("Starting Telegram bot polling...")
    try:
        await run_telegram_bot()
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrupt received — exiting.")
