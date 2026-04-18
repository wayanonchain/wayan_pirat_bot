"""Shared pytest fixtures.

Sets ``DATABASE_URL`` to a temporary SQLite file **before** importing any
project module that reads it — ``db.repository`` creates the engine at
import time. We use a file (not ``:memory:``) so multiple sessions in a
single test share the same state.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must come before any project imports.
_TMP_DB = Path(tempfile.mkdtemp(prefix="wayne-test-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("LOG_CHAT_ID", "-1000")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def clean_db():
    """Drop-and-recreate every table before each test that requests this."""
    from db import repository as repo
    from db.models import Base

    async with repo.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield repo


@pytest.fixture(autouse=True)
def _silence_alerter(monkeypatch):
    """Prevent the Telegram alerter from making real network calls in tests."""
    from bot import bot_bridge
    monkeypatch.setattr(bot_bridge, "submit", lambda coro: (coro.close() or None))
