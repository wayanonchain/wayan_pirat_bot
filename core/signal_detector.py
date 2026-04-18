"""
Signal detector: identifies when 2+ Smart Money wallets buy the same token.
Uses a sliding window approach over recent token buys.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from config.settings import SIGNAL_WINDOW_MINUTES, MIN_WALLETS_MODE_1, MIN_WALLETS_MODE_2, MIN_BUY_USD
from db import repository as repo
from api import solanatracker_client as tracker

logger = logging.getLogger(__name__)

# In-memory cache of recently signaled tokens (to avoid duplicate signals).
# Access serialized by _signal_lock — at ~8k events/hour concurrent webhook
# handlers can otherwise race to create duplicate signals for the same token.
_recent_signals: dict[str, datetime] = {}
_signal_lock = asyncio.Lock()
SIGNAL_COOLDOWN_MINUTES = 60  # Don't re-signal same token within 60 min
_CACHE_MAX = 5000  # Hard cap before we prune entries older than 2×cooldown.


def _prune_recent_signals() -> None:
    """Drop entries whose cooldown has expired. Called under _signal_lock."""
    if len(_recent_signals) < _CACHE_MAX:
        return
    cutoff = datetime.utcnow() - timedelta(minutes=SIGNAL_COOLDOWN_MINUTES * 2)
    stale = [k for k, ts in _recent_signals.items() if ts < cutoff]
    for k in stale:
        del _recent_signals[k]
    if stale:
        logger.info(f"Pruned {len(stale)} stale entries from signal cooldown cache")


async def process_buy(wallet_address: str, token_address: str, token_symbol: str,
                      amount_usd: float, amount_sol: float, amount_token: float,
                      tx_signature: str, mcap: float | None = None) -> dict | None:
    """
    Process a detected token buy from a Smart Money wallet.
    Returns a signal dict if a coincidence is detected, otherwise None.
    """
    if amount_usd < MIN_BUY_USD:
        return None

    # Record the buy
    recorded = await repo.record_buy({
        "wallet_address": wallet_address,
        "token_address": token_address,
        "token_symbol": token_symbol,
        "amount_usd": amount_usd,
        "amount_token": amount_token,
        "amount_sol": amount_sol,
        "tx_signature": tx_signature,
        "timestamp": datetime.utcnow(),
        "mcap_at_buy": mcap,
    })

    if not recorded:
        logger.debug(f"Duplicate tx: {tx_signature[:20]}")
        return None

    # Cooldown check + reservation must be atomic — otherwise two concurrent
    # handlers both see "not recent" and both create a signal for the same
    # token within the window.
    reservation = datetime.utcnow()
    async with _signal_lock:
        last = _recent_signals.get(token_address)
        if last:
            elapsed = (reservation - last).total_seconds() / 60
            if elapsed < SIGNAL_COOLDOWN_MINUTES:
                logger.debug(f"Signal cooldown for {token_symbol} ({elapsed:.0f}m ago)")
                return None
        # Reserve this token now so parallel handlers back off while we build
        # the signal. Roll back below if no coincidence forms.
        _recent_signals[token_address] = reservation
        _prune_recent_signals()

    # Get all recent buys for this token
    recent_buys = await repo.get_recent_buys(token_address, SIGNAL_WINDOW_MINUTES)
    unique_wallets = list({b.wallet_address for b in recent_buys})

    if len(unique_wallets) < MIN_WALLETS_MODE_1:
        # Not enough wallets — free our reservation so the next buy can try.
        # Only delete if it's still our timestamp (don't clobber a newer one).
        async with _signal_lock:
            if _recent_signals.get(token_address) == reservation:
                del _recent_signals[token_address]
        return None

    # We have a signal!
    mode = 2 if len(unique_wallets) >= MIN_WALLETS_MODE_2 else 1

    # Build wallet details
    wallet_details = []
    for buy in recent_buys:
        if buy.wallet_address not in [wd["address"] for wd in wallet_details]:
            wallet_info = await repo.get_wallet(buy.wallet_address)
            wallet_details.append({
                "address": buy.wallet_address,
                "amount_usd": buy.amount_usd,
                "amount_sol": buy.amount_sol,
                "mcap_at_buy": buy.mcap_at_buy,
                "wallet_type": wallet_info.wallet_type if wallet_info else "UNKNOWN",
                "pnl": wallet_info.realized_pnl_usd if wallet_info else 0,
            })

    total_buy_usd = sum(b.amount_usd for b in recent_buys)

    # Fetch token metadata if not provided
    token_meta = await repo.get_token_metadata(token_address)
    if not token_meta:
        meta = await tracker.get_token_info(token_address)
        if meta:
            await repo.upsert_token_metadata(meta)
            mcap = meta.get("mcap")
            token_symbol = meta.get("symbol", token_symbol)

    # Calculate token age
    token_age_hours = None
    if token_meta and token_meta.created_at:
        delta = datetime.utcnow() - token_meta.created_at
        token_age_hours = delta.total_seconds() / 3600

    # Create signal
    signal_data = {
        "token_address": token_address,
        "token_symbol": token_symbol,
        "mode": mode,
        "wallet_count": len(unique_wallets),
        "wallets_json": json.dumps(wallet_details, default=str),
        "total_buy_usd": total_buy_usd,
        "mcap": mcap,
        "token_age_hours": token_age_hours,
    }

    signal = await repo.create_signal(signal_data)
    # Refresh the reservation timestamp (we set it earlier under the lock;
    # update it to "now" so cooldown is measured from signal creation time).
    async with _signal_lock:
        _recent_signals[token_address] = datetime.utcnow()

    logger.info(f"SIGNAL Mode {mode}: {len(unique_wallets)} wallets bought {token_symbol} "
                f"(${total_buy_usd:,.0f} total, MCAP: ${mcap:,.0f})" if mcap else
                f"SIGNAL Mode {mode}: {len(unique_wallets)} wallets bought {token_symbol} "
                f"(${total_buy_usd:,.0f} total)")

    return {
        "signal_id": signal.id,
        "token_address": token_address,
        "token_symbol": token_symbol,
        "mode": mode,
        "wallet_count": len(unique_wallets),
        "wallets": wallet_details,
        "total_buy_usd": total_buy_usd,
        "mcap": mcap,
        "token_age_hours": token_age_hours,
    }
