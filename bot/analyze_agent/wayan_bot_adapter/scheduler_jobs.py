"""
APScheduler jobs for the accumulation module.

Wiring (in WAYNE_PIRATE core/scheduler.py):

    from bot.analyze_agent.wayan_bot_adapter.scheduler_jobs import (
        register_accumulation_jobs,
    )
    register_accumulation_jobs(scheduler)

That single call registers the discovery and monitor jobs and makes sure
sys.path includes the package so the standalone-agent modules
(config.py, detector.py, data_fetcher.py, …) are importable.
"""
import logging
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


# sys.path bootstrap is handled in wayan_bot_adapter/__init__.py so any
# entry path into the package (handlers.py, scheduler_jobs.py, etc.) has
# the agent modules resolvable from the start.

from . import discovery, monitor
from .alerter import telegram_alerter


async def _job_discover():
    try:
        await discovery.run_discovery(
            window_hours=72,
            min_unique_wallets=2,
            min_total_usd=2_000,   # filter out dust buys
        )
    except Exception as e:
        log.exception("Accumulation discovery job failed: %s", e)


async def _job_monitor():
    try:
        await monitor.run_monitor_once(alerter=telegram_alerter)
    except Exception as e:
        log.exception("Accumulation monitor job failed: %s", e)


def register_accumulation_jobs(scheduler: AsyncIOScheduler) -> None:
    """Register the two recurring jobs on the bot's existing scheduler."""

    # Discovery: every 6 hours. 72h window / 4 daily runs ≈ good freshness.
    scheduler.add_job(
        _job_discover,
        CronTrigger(hour="*/6", minute=17),
        id="accumulation_discover",
        name="Accumulation: SM-discovery scan",
        replace_existing=True,
    )

    # Monitor: every 15 minutes. Free DexScreener + GeckoTerminal tiers
    # handle dozens of tokens comfortably on this cadence.
    scheduler.add_job(
        _job_monitor,
        CronTrigger(minute="*/15"),
        id="accumulation_monitor",
        name="Accumulation: pattern monitor",
        replace_existing=True,
    )

    log.info(
        "Accumulation jobs registered: discovery every 6h, monitor every 15m"
    )
