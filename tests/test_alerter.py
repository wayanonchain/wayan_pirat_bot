"""Unit tests for the Telegram error alerter.

We exercise the filtering and dedup logic without making real network
calls — ``bot_bridge.submit`` is stubbed in ``conftest.py``.
"""

import logging
import time

from bot.alerter import TelegramAlertHandler, _hint_for


def _record(level, name, msg, exc_info=None):
    return logging.LogRecord(
        name=name, level=level, pathname="", lineno=0,
        msg=msg, args=(), exc_info=exc_info,
    )


def test_excluded_logger_names_skipped():
    handler = TelegramAlertHandler(chat_id="-1000")
    r = _record(logging.ERROR, "httpx", "500 server error")
    assert handler._should_skip(r)


def test_noise_substring_skipped():
    handler = TelegramAlertHandler(chat_id="-1000")
    r = _record(logging.ERROR, "core.some_module", "HTTP 429 Too Many Requests")
    assert handler._should_skip(r)


def test_genuine_error_not_skipped():
    handler = TelegramAlertHandler(chat_id="-1000")
    r = _record(logging.ERROR, "core.signal_detector", "DB corrupted — something real")
    assert not handler._should_skip(r)


def test_dedup_cooldown(monkeypatch):
    """Identical errors within the cooldown should only emit once."""
    calls = []

    def fake_submit(coro):
        calls.append(1)
        coro.close()
        return None

    monkeypatch.setattr("bot.alerter.bot_bridge.submit", fake_submit)

    handler = TelegramAlertHandler(chat_id="-1000", cooldown_seconds=60)
    r = _record(logging.ERROR, "core.test", "same-message")
    handler.emit(r)
    handler.emit(r)
    handler.emit(r)
    assert len(calls) == 1


def test_dedup_different_messages_both_emit(monkeypatch):
    calls = []
    monkeypatch.setattr("bot.alerter.bot_bridge.submit",
                        lambda coro: calls.append(1) or coro.close())

    handler = TelegramAlertHandler(chat_id="-1000", cooldown_seconds=60)
    handler.emit(_record(logging.ERROR, "core.a", "error A"))
    handler.emit(_record(logging.ERROR, "core.b", "error B"))
    assert len(calls) == 2


def test_cooldown_expires(monkeypatch):
    calls = []
    monkeypatch.setattr("bot.alerter.bot_bridge.submit",
                        lambda coro: calls.append(1) or coro.close())

    handler = TelegramAlertHandler(chat_id="-1000", cooldown_seconds=60)
    r = _record(logging.ERROR, "core.test", "same-message")
    handler.emit(r)
    # Simulate time moving past the cooldown by rewinding the stored timestamp.
    key = "core.test:same-message"
    _, count = handler._buckets[key]
    handler._buckets[key] = (time.time() - 61, count)
    handler.emit(r)
    assert len(calls) == 2


def test_suppressed_count_reported(monkeypatch):
    """After a burst of duplicates, the next emit should mention how many
    were swallowed during the cooldown."""
    texts: list[str] = []

    def fake_submit(coro):
        # The coroutine will hold the text as a closed-over local; grab it
        # off its frame before closing.
        try:
            frame = coro.cr_frame
            if frame is not None and "text" in frame.f_locals:
                texts.append(frame.f_locals["text"])
        finally:
            coro.close()
        return None

    monkeypatch.setattr("bot.alerter.bot_bridge.submit", fake_submit)

    handler = TelegramAlertHandler(chat_id="-1000", cooldown_seconds=60)
    r = _record(logging.ERROR, "core.test", "loud")
    handler.emit(r)        # first emit — sends, suppressed=0
    handler.emit(r)        # in cooldown — bumps suppressed to 1
    handler.emit(r)        # in cooldown — bumps to 2
    handler.emit(r)        # in cooldown — bumps to 3

    # Force cooldown expiry for the next emit.
    key = "core.test:loud"
    _, count = handler._buckets[key]
    handler._buckets[key] = (time.time() - 61, count)
    handler.emit(r)        # should send with "+3 suppressed" in header

    assert len(texts) == 2
    assert "suppressed" not in texts[0]
    assert "+3 suppressed" in texts[1]


def test_hint_matches_known_error():
    r = _record(logging.ERROR, "webhook.server",
                "QueuePool limit of size 5 overflow 10 reached")
    assert "пул соединений исчерпан" in (_hint_for(r) or "")


def test_hint_telegram_network_error():
    r = _record(logging.ERROR, "aiogram.dispatcher",
                "Failed to fetch updates - TelegramNetworkError: HTTP Client says - Request timeout error")
    hint = _hint_for(r) or ""
    assert "Telegram" in hint


def test_hint_none_for_unknown_error():
    r = _record(logging.ERROR, "core.x", "some brand new error")
    assert _hint_for(r) is None


def test_format_message_includes_russian_hint():
    handler = TelegramAlertHandler(chat_id="-1000")
    r = _record(logging.ERROR, "webhook.server",
                "QueuePool limit of size 5 overflow 10 reached")
    text = handler._format_message(r)
    assert "Причина:" in text
    assert "пул соединений" in text


def test_format_message_escapes_html():
    handler = TelegramAlertHandler(chat_id="-1000")
    r = _record(logging.ERROR, "x", "<script>alert(1)</script>")
    text = handler._format_message(r)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_message_truncates_long_output():
    handler = TelegramAlertHandler(chat_id="-1000")
    long_msg = "x" * 5000
    r = _record(logging.ERROR, "x", long_msg)
    text = handler._format_message(r)
    # Large messages get a "…" suffix added by the truncation logic.
    assert "…" in text
    assert len(text) < 2000


def test_thread_id_forwarded_to_send_message(monkeypatch):
    """A configured message_thread_id must be propagated into bot.send_message."""
    import asyncio

    sent = {}

    class FakeBot:
        async def send_message(self, **kwargs):
            sent.update(kwargs)

    import sys
    import types
    fake_module = types.ModuleType("bot.telegram_bot")
    fake_module.bot = FakeBot()
    monkeypatch.setitem(sys.modules, "bot.telegram_bot", fake_module)

    handler = TelegramAlertHandler(chat_id="-1000", message_thread_id=60)
    asyncio.get_event_loop().run_until_complete(handler._send("hi"))

    assert sent["chat_id"] == "-1000"
    assert sent["message_thread_id"] == 60


def test_no_thread_id_omits_the_param(monkeypatch):
    """When no thread is configured, the send_message call must not carry
    message_thread_id — Telegram treats the missing key as ``general``."""
    import asyncio

    sent = {}

    class FakeBot:
        async def send_message(self, **kwargs):
            sent.update(kwargs)

    import sys
    import types
    fake_module = types.ModuleType("bot.telegram_bot")
    fake_module.bot = FakeBot()
    monkeypatch.setitem(sys.modules, "bot.telegram_bot", fake_module)

    handler = TelegramAlertHandler(chat_id="-1000")  # no thread
    asyncio.get_event_loop().run_until_complete(handler._send("hi"))

    assert "message_thread_id" not in sent
