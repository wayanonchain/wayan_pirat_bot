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

# max_instances=1 + coalesce=True default on every job: prevents overlap when
# a long-running job is still going when the next cron tick fires.
# misfire_grace_time=600 lets a job fire up to 10 min late (e.g. after a
# restart) instead of silently skipping.
_JOB_DEFAULTS = {
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 600,
}

scheduler = AsyncIOScheduler(timezone=MSK, job_defaults=_JOB_DEFAULTS)


async def _run_daily_stats():
    """Wrapper to run daily admin stats."""
    try:
        from core.admin_stats import send_admin_stats
        await send_admin_stats()
    except Exception as e:
        logger.error(f"Daily stats job error: {e}", exc_info=True)


async def _run_nansen_signal():
    """Wrapper to run Nansen Smart Money signal posting."""
    try:
        from core.nansen_signals import send_nansen_signal_to_community
        credits = await send_nansen_signal_to_community(top_n=20)
        if credits:
            logger.info(f"Nansen signal posted. Credits remaining: {credits.get('remaining')}")
    except Exception as e:
        logger.error(f"Nansen signal job error: {e}", exc_info=True)


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

    # Nansen Smart Money signal at 09:00 and 19:00 MSK
    scheduler.add_job(
        _run_nansen_signal,
        CronTrigger(hour="9,19", minute=0, timezone=MSK),
        id="nansen_smart_money_signal",
        name="Nansen Smart Money Signal to Community",
        replace_existing=True,
    )

    # Accumulation module — SM-discovery + Wyckoff-pattern monitor jobs.
    try:
        from bot.analyze_agent.wayan_bot_adapter.scheduler_jobs import (
            register_accumulation_jobs,
        )
        register_accumulation_jobs(scheduler)
    except Exception as e:
        logger.error(f"Could not register accumulation jobs: {e}", exc_info=True)

    scheduler.start()
    logger.info(
        "Scheduler started: daily stats @ 21:00 MSK, "
        "weekly report @ Sun 20:00 MSK, "
        "nansen signal @ 09:00 & 19:00 MSK, "
        "accumulation discovery @ every 6h, monitor @ every 15m"
    )


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
