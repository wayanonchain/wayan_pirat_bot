"""
Telegram alerter used by the monitor job.

Sends the formatted accumulation alert to the log chat defined in
WAYNE_PIRATE config.settings. Falls back to the main TELEGRAM_CHAT_ID if
LOG_CHAT_ID is unset. Returns the Telegram message_id on success so the
caller can persist it on accumulation_signals.
"""
import logging
from typing import Optional

from agent_alerts import format_alert, format_watchlist_alert
from models import SignalTier

log = logging.getLogger(__name__)


async def telegram_alerter(analysis) -> Optional[int]:
    """Post a Telegram alert for the given AccumulationAnalysis.

    Returns the Telegram message_id, or None when the send failed or no
    bot is configured (in which case the signal is still persisted in
    accumulation_signals — we just didn't send a push).
    """
    try:
        # The bot singleton is created inside bot/telegram_bot.py at import
        # time. We import lazily so this module stays testable.
        from bot.telegram_bot import bot
        from config.settings import TELEGRAM_CHAT_ID, LOG_CHAT_ID
    except Exception as e:
        log.warning("Cannot import bot/settings — alerter inactive: %s", e)
        return None

    chat_id = LOG_CHAT_ID or TELEGRAM_CHAT_ID
    if not chat_id:
        log.warning("No chat_id configured — alert dropped")
        return None

    if analysis.tier in (SignalTier.SIGNAL, SignalTier.STRONG):
        text = format_alert(analysis, mode="BALANCED")
    elif analysis.tier == SignalTier.WATCHLIST:
        text = format_watchlist_alert(analysis, mode="BALANCED")
    else:
        return None  # NOISE never ships

    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return msg.message_id
    except Exception as e:
        log.error("Failed to send accumulation alert: %s", e)
        return None
