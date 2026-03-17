"""
Custom signal filters for Premium subscribers.
Stored per-user in SQLite, applied before sending signals.
"""

import json
import logging

from db.models import UserFilter
from db.repository import async_session

logger = logging.getLogger(__name__)

# Default filter values
DEFAULT_FILTERS = {
    "min_mcap": 0,           # Minimum MCAP in USD (0 = no filter)
    "max_mcap": 0,           # Maximum MCAP in USD (0 = no filter)
    "min_buy_usd": 0,        # Minimum total buy amount USD
    "min_wallets": 2,         # Minimum wallet count (2 or 3)
    "only_traders": False,    # Only show TRADER wallets (exclude BOT)
    "enabled": True,          # Filters enabled
}


async def get_user_filters(user_id: int) -> dict:
    """Get user's filter settings."""
    async with async_session() as session:
        uf = await session.get(UserFilter, user_id)
        if not uf:
            return DEFAULT_FILTERS.copy()
        try:
            saved = json.loads(uf.filters_json)
            # Merge with defaults (in case new fields added)
            filters = DEFAULT_FILTERS.copy()
            filters.update(saved)
            return filters
        except json.JSONDecodeError:
            return DEFAULT_FILTERS.copy()


async def save_user_filters(user_id: int, filters: dict):
    """Save user's filter settings."""
    async with async_session() as session:
        uf = await session.get(UserFilter, user_id)
        if uf:
            uf.filters_json = json.dumps(filters)
        else:
            session.add(UserFilter(
                user_id=user_id,
                filters_json=json.dumps(filters),
            ))
        await session.commit()


async def update_single_filter(user_id: int, key: str, value) -> dict:
    """Update a single filter field. Returns updated filters."""
    filters = await get_user_filters(user_id)
    if key in DEFAULT_FILTERS:
        filters[key] = value
        await save_user_filters(user_id, filters)
    return filters


def should_send_signal(signal: dict, filters: dict) -> bool:
    """Check if a signal passes the user's filters."""
    if not filters.get("enabled", True):
        return True  # Filters disabled = send everything

    mcap = signal.get("mcap") or 0

    # Min MCAP
    if filters.get("min_mcap") and mcap > 0 and mcap < filters["min_mcap"]:
        return False

    # Max MCAP
    if filters.get("max_mcap") and mcap > 0 and mcap > filters["max_mcap"]:
        return False

    # Min buy amount
    if filters.get("min_buy_usd") and signal.get("total_buy_usd", 0) < filters["min_buy_usd"]:
        return False

    # Min wallets
    if filters.get("min_wallets", 2) > signal.get("wallet_count", 0):
        return False

    # Only traders
    if filters.get("only_traders"):
        wallets = signal.get("wallets", [])
        trader_wallets = [w for w in wallets if w.get("wallet_type") == "TRADER"]
        if len(trader_wallets) < 2:
            return False

    return True


def format_filters(filters: dict) -> str:
    """Format current filters for display."""
    status = "\U0001f7e2 ON" if filters.get("enabled", True) else "\U0001f534 OFF"

    lines = [
        f"\U0001f527 <b>Your Signal Filters</b> [{status}]",
        "\u2500" * 30,
        "",
    ]

    min_mcap = filters.get("min_mcap", 0)
    max_mcap = filters.get("max_mcap", 0)
    min_buy = filters.get("min_buy_usd", 0)
    min_wallets = filters.get("min_wallets", 2)
    only_traders = filters.get("only_traders", False)

    lines.append(f"Min MCAP: <b>{'$' + _fmt_num(min_mcap) if min_mcap else 'Any'}</b>")
    lines.append(f"Max MCAP: <b>{'$' + _fmt_num(max_mcap) if max_mcap else 'Any'}</b>")
    lines.append(f"Min buy amount: <b>{'$' + _fmt_num(min_buy) if min_buy else 'Any'}</b>")
    lines.append(f"Min wallets: <b>{min_wallets}</b>")
    lines.append(f"Only traders: <b>{'Yes' if only_traders else 'No'}</b>")

    lines.extend([
        "",
        "<b>How to change:</b>",
        "<code>/filter min_mcap 50000</code> — min MCAP $50K",
        "<code>/filter max_mcap 5000000</code> — max MCAP $5M",
        "<code>/filter min_buy 500</code> — min buy $500",
        "<code>/filter min_wallets 3</code> — only 3+ wallets",
        "<code>/filter only_traders on</code> — exclude bots",
        "<code>/filter reset</code> — reset to defaults",
        "<code>/filter off</code> / <code>/filter on</code> — toggle",
    ])

    return "\n".join(lines)


def _fmt_num(val):
    if val >= 1_000_000:
        return f"{val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val/1_000:.0f}K"
    return str(int(val))
