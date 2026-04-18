"""Signal detector cooldown / race tests.

These tests monkey-patch the repository layer so we don't exercise DB
paths here — the focus is on the in-memory cooldown dict and its lock.
"""

import asyncio
from datetime import datetime, timedelta

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_repo(monkeypatch):
    """Stub repository so process_buy can run without a DB."""
    from core import signal_detector

    state = {
        "buys": [],            # rows returned by get_recent_buys
        "record_ok": True,     # record_buy return value
        "create_signal_id": 1,
    }

    async def record_buy(data):
        return state["record_ok"]

    async def get_recent_buys(token, minutes):
        return list(state["buys"])

    async def get_wallet(addr):
        class W:
            wallet_type = "TRADER"
            realized_pnl_usd = 10_000
        return W()

    async def get_token_metadata(addr):
        return None

    async def upsert_token_metadata(meta):
        return None

    async def create_signal(data):
        class S:
            id = state["create_signal_id"]
        return S()

    monkeypatch.setattr(signal_detector.repo, "record_buy", record_buy)
    monkeypatch.setattr(signal_detector.repo, "get_recent_buys", get_recent_buys)
    monkeypatch.setattr(signal_detector.repo, "get_wallet", get_wallet)
    monkeypatch.setattr(signal_detector.repo, "get_token_metadata", get_token_metadata)
    monkeypatch.setattr(signal_detector.repo, "upsert_token_metadata", upsert_token_metadata)
    monkeypatch.setattr(signal_detector.repo, "create_signal", create_signal)

    async def fake_token_info(addr):
        return None

    monkeypatch.setattr(signal_detector.tracker, "get_token_info", fake_token_info)

    # Reset in-memory state between tests.
    signal_detector._recent_signals.clear()

    return state


def _buy(wallet, token="T1", amount=500):
    class B:
        wallet_address = wallet
        token_address = token
        amount_usd = amount
        amount_sol = 1.0
        mcap_at_buy = 100_000
    return B()


async def test_single_wallet_buy_does_not_signal(fake_repo):
    """One SM wallet alone shouldn't form a signal."""
    from core.signal_detector import process_buy

    fake_repo["buys"] = [_buy("A")]
    result = await process_buy("A", "T1", "SYM", 500, 1.0, 10, "tx1")
    assert result is None


async def test_two_wallets_form_signal(fake_repo):
    from core.signal_detector import process_buy, _recent_signals

    fake_repo["buys"] = [_buy("A"), _buy("B")]
    result = await process_buy("B", "T1", "SYM", 500, 1.0, 10, "tx2")
    assert result is not None
    assert result["wallet_count"] == 2
    # Cooldown registered.
    assert "T1" in _recent_signals


async def test_cooldown_blocks_duplicate_signal(fake_repo):
    """Within the cooldown window, no second signal should fire."""
    from core.signal_detector import process_buy, _recent_signals

    fake_repo["buys"] = [_buy("A"), _buy("B")]
    first = await process_buy("B", "T1", "SYM", 500, 1.0, 10, "tx1")
    assert first is not None

    # Third wallet within cooldown — must be blocked.
    fake_repo["buys"] = [_buy("A"), _buy("B"), _buy("C")]
    second = await process_buy("C", "T1", "SYM", 500, 1.0, 10, "tx2")
    assert second is None


async def test_cooldown_expires_allows_new_signal(fake_repo):
    from core.signal_detector import process_buy, _recent_signals, SIGNAL_COOLDOWN_MINUTES

    _recent_signals["T1"] = datetime.utcnow() - timedelta(minutes=SIGNAL_COOLDOWN_MINUTES + 1)
    fake_repo["buys"] = [_buy("A"), _buy("B")]
    result = await process_buy("B", "T1", "SYM", 500, 1.0, 10, "tx1")
    assert result is not None


async def test_concurrent_buys_produce_one_signal(fake_repo):
    """Two handlers racing for the same token should yield exactly one signal."""
    from core.signal_detector import process_buy

    fake_repo["buys"] = [_buy("A"), _buy("B")]

    results = await asyncio.gather(
        process_buy("A", "T1", "SYM", 500, 1.0, 10, "tx_a"),
        process_buy("B", "T1", "SYM", 500, 1.0, 10, "tx_b"),
    )
    non_null = [r for r in results if r is not None]
    assert len(non_null) == 1


async def test_buy_below_threshold_ignored(fake_repo):
    from core.signal_detector import process_buy
    from config.settings import MIN_BUY_USD

    result = await process_buy("A", "T1", "SYM", MIN_BUY_USD - 1, 0.01, 1, "tx1")
    assert result is None


async def test_prune_removes_stale_entries():
    from core.signal_detector import (
        _recent_signals, _prune_recent_signals,
        SIGNAL_COOLDOWN_MINUTES, _CACHE_MAX,
    )

    _recent_signals.clear()
    stale = datetime.utcnow() - timedelta(minutes=SIGNAL_COOLDOWN_MINUTES * 3)
    for i in range(_CACHE_MAX + 10):
        _recent_signals[f"stale_{i}"] = stale
    _prune_recent_signals()
    # All stale entries should have been pruned.
    assert len(_recent_signals) == 0
