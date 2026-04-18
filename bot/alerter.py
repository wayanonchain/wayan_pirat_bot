"""Telegram alerter for technical errors.

Installs a ``logging.Handler`` that forwards ``ERROR`` and ``CRITICAL``
records to the team log chat so issues are visible without tailing
``journalctl``. De-duplicates by (logger name, first 120 chars of message) so
a burst of identical errors posts once per cooldown window, not once per
event.

Never calls Telegram from inside the log pipeline directly — always hops
through ``bot_bridge.submit`` so cross-loop emits (e.g. from the webhook
thread) work without raising ``"Timeout context manager should be used
inside a task"``.
"""

from __future__ import annotations

import html
import logging
import time
from collections import defaultdict

from bot import bot_bridge

logger = logging.getLogger(__name__)

# Loggers whose output should never reach Telegram — either it's already
# covered elsewhere (activity_log posts to the same chat) or it's noisy
# transient failures that the system self-heals from.
_EXCLUDED_LOGGERS = (
    "bot.alerter",          # don't recurse on our own failures
    "bot.activity_log",     # this module already posts to the same chat
    "aiogram.event",        # aiogram wraps handler errors as ERROR but we get them twice
    "aiohttp.access",
    "uvicorn.access",
    "httpx",                # upstream 429/500 surface via callers instead
)

# Messages matching any of these substrings are suppressed — they're known
# transient conditions, not alert-worthy.
_NOISE_SUBSTRINGS = (
    "Too Many Requests",
    "Failed to send activity log",  # circular if log chat itself is down
    "Failed to send message",
    "Timeout context manager should be used inside a task",
)


class TelegramAlertHandler(logging.Handler):
    """Forward ERROR+ log records to the team log chat, de-duplicated."""

    def __init__(self, chat_id: str | int, cooldown_seconds: int = 60,
                 level: int = logging.ERROR):
        super().__init__(level)
        self.chat_id = str(chat_id)
        self.cooldown = cooldown_seconds
        self._last_sent: dict[str, float] = defaultdict(float)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            if self._should_skip(record):
                return

            key = f"{record.name}:{record.getMessage()[:120]}"
            now = time.time()
            if now - self._last_sent[key] < self.cooldown:
                return
            self._last_sent[key] = now

            text = self._format_message(record)
            # Fire-and-forget on the main loop. If the main loop isn't up yet
            # (early startup errors), submit() logs a warning and drops it.
            bot_bridge.submit(self._send(text))
        except Exception:
            # Never let the alerter crash the caller's logger chain.
            self.handleError(record)

    def _should_skip(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(_EXCLUDED_LOGGERS):
            return True
        msg = record.getMessage()
        return any(s in msg for s in _NOISE_SUBSTRINGS)

    def _format_message(self, record: logging.LogRecord) -> str:
        level_emoji = "🚨" if record.levelno >= logging.CRITICAL else "⚠️"
        msg = html.escape(record.getMessage())
        if len(msg) > 1200:
            msg = msg[:1200] + "…"

        lines = [
            f"{level_emoji} <b>{record.levelname}</b>",
            f"<b>{html.escape(record.name)}</b>",
            f"<pre>{msg}</pre>",
        ]

        if record.exc_info:
            import traceback
            tb = "".join(traceback.format_exception(*record.exc_info))
            if len(tb) > 1400:
                tb = tb[:1400] + "…"
            lines.append(f"<pre>{html.escape(tb)}</pre>")

        return "\n".join(lines)

    async def _send(self, text: str) -> None:
        try:
            from bot.telegram_bot import bot
            await bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=True,
            )
        except Exception as e:
            # Don't re-emit — would recurse through this handler.
            logging.getLogger("bot.alerter").debug(f"alerter send failed: {e}")


def install(chat_id: str | int, cooldown_seconds: int = 60) -> TelegramAlertHandler:
    """Attach the handler to the root logger. Idempotent per-call."""
    handler = TelegramAlertHandler(chat_id=chat_id, cooldown_seconds=cooldown_seconds)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    logger.info(f"Telegram error alerter installed → chat {chat_id} (cooldown {cooldown_seconds}s)")
    return handler
