"""
APScheduler-based task scheduler for:
- Daily admin stats (every day at 21:00 MSK)
- Weekly SM report (Sunday at 20:00 MSK)
"""

import logging
import asyncio
from datetime import timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

scheduler = AsyncIOScheduler(timezone=MSK)


async def _run_daily_stats():
    """Wrapper to run daily admin stats."""
    try:
        from core.admin_stats import send_admin_stats
        await send_admin_stats()
    except Exception as e:
        logger.error(f"Daily stats job error: {e}", exc_info=True)


async def _run_weekly_report():
    """Wrapper to run weekly report."""
    try:
        from core.weekly_report import send_weekly_report
        await send_weekly_report()
    except Exception as e:
        logger.error(f"Weekly report job error: {e}", exc_info=True)


def start_scheduler():
    """Configure and start the scheduler."""
    # Daily admin stats at 21:00 MSK (18:00 UTC)
    scheduler.add_job(
        _run_daily_stats,
        CronTrigger(hour=21, minute=0, timezone=MSK),
        id="daily_admin_stats",
        name="Daily Admin Stats Report",
        replace_existing=True,
    )

    # Weekly report on Sunday at 20:00 MSK (17:00 UTC)
    scheduler.add_job(
        _run_weekly_report,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=MSK),
        id="weekly_sm_report",
        name="Weekly Smart Money Report",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started: daily stats @ 21:00 MSK, weekly report @ Sun 20:00 MSK")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
