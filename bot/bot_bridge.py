"""Bridge for submitting coroutines from one event loop into the main loop.

Problem this solves:
    The FastAPI webhook server runs in a background thread via
    ``uvicorn.run(app)`` which creates its own event loop. aiogram's ``Bot``
    instance is created at import time on the main loop and lazily opens an
    aiohttp ``ClientSession`` bound to whichever loop first uses it. When the
    webhook thread directly ``await``s ``bot.send_message(...)``, aiohttp
    raises ``"Timeout context manager should be used inside a task"`` — its
    timeout context checks the running loop matches the session's loop.

Solution:
    ``register_main_loop()`` is called from ``main()`` once the main loop is
    running. Anything that wants to call a main-loop coroutine from a
    different loop/thread uses ``submit(coro)`` which forwards to
    ``asyncio.run_coroutine_threadsafe``. Fire-and-forget by default.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None


def register_main_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Record the main event loop for later cross-loop submissions.

    Call from the main coroutine (the one driving ``asyncio.run``) before
    starting the webhook thread.
    """
    global _main_loop
    _main_loop = loop or asyncio.get_running_loop()


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    return _main_loop


def submit(coro) -> Future | None:
    """Schedule ``coro`` on the main loop; return its concurrent Future.

    Safe to call from any thread. If the main loop hasn't been registered
    yet, the coroutine is closed (never scheduled) and ``None`` returned —
    the caller is expected to log/skip in that case.
    """
    if _main_loop is None or not _main_loop.is_running():
        logger.warning("Main loop not available — dropping coroutine")
        coro.close()
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, _main_loop)
    except RuntimeError as e:
        logger.warning(f"Could not submit to main loop: {e}")
        coro.close()
        return None


async def submit_and_wait(coro, timeout: float | None = None):
    """Await completion of ``coro`` on the main loop from another loop.

    Use sparingly — it serializes the caller on the main loop's progress.
    """
    fut = submit(coro)
    if fut is None:
        return None
    return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
