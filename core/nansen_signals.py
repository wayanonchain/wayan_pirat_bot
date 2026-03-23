"""
Nansen Smart Money signals — fetch, format, and post to community.
"""

import logging
from datetime import datetime, timezone, timedelta

from api.nansen_client import fetch_smart_money_netflow
from config.settings import COMMUNITY_CHAT_ID, TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

# Chain emoji mapping
CHAIN_EMOJI = {
    "solana": "\U0001f7e3",   # purple circle
    "ethereum": "\U0001f535",  # blue circle
    "base": "\U0001f535",
    "bnb": "\U0001f7e1",      # yellow circle
    "arbitrum": "\U0001f535",
    "polygon": "\U0001f7e3",
    "avalanche": "\U0001f534", # red circle
    "optimism": "\U0001f534",
}


def _format_flow(value: float) -> str:
    """Format USD flow with sign and K/M suffix."""
    sign = "+" if value >= 0 else "-"
    abs_val = abs(value)
    if abs_val >= 1_000_000:
        return f"{sign}${abs_val / 1_000_000:.2f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val / 1_000:.2f}K"
    else:
        return f"{sign}${abs_val:.0f}"


def _format_mcap(value: float) -> str:
    """Format market cap."""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.2f}K"
    else:
        return f"${value:.0f}"


def _format_age(days: int) -> str:
    """Format token age."""
    if days >= 365:
        return f"{days}d"
    return f"{days}d"


def format_nansen_signal(tokens: list[dict]) -> str:
    """Format list of tokens into the signal message."""
    wita = timezone(timedelta(hours=8))
    now = datetime.now(wita)
    date_str = now.strftime("%a %d %b %Y \u00b7 %H:%M WITA")

    lines = [
        "\U0001f9e0 Nansen Smart Money \u2014 Top Tokens",
        f"\U0001f4c5 {date_str}",
        "Ranked by 24H Flow \u2193 (highest \u2192 lowest)",
        "",
    ]

    for i, token in enumerate(tokens, 1):
        symbol = token.get("token_symbol", "???")
        chain = token.get("chain", "unknown")
        emoji = CHAIN_EMOJI.get(chain, "\u26aa")
        age = token.get("token_age_days", 0)
        mcap = token.get("market_cap_usd", 0)
        flow_24h = token.get("net_flow_24h_usd", 0)
        flow_7d = token.get("net_flow_7d_usd", 0)
        flow_30d = token.get("net_flow_30d_usd", 0)
        traders = token.get("trader_count", 0)
        address = token.get("token_address", "")

        lines.append(
            f"{i}. {emoji} {symbol}  \u00b7  Age: {_format_age(age)}  \u00b7  MCap: {_format_mcap(mcap)}"
        )
        lines.append(
            f"   24H: {_format_flow(flow_24h)}  "
            f"7D: {_format_flow(flow_7d)}  "
            f"30D: {_format_flow(flow_30d)}"
        )
        lines.append(f"   \U0001f465 {traders} traders")
        lines.append(f"   \U0001f4cb {address}")
        lines.append("")

    return "\n".join(lines).strip()


async def send_nansen_signal_to_community(
    top_n: int = 20,
    chains: list[str] | None = None,
    chat_id: int | None = None,
) -> dict | None:
    """
    Fetch Nansen Smart Money data, format, and send to community.
    Returns credits info dict or None on failure.
    """
    from aiogram import Bot

    target_chat = chat_id or COMMUNITY_CHAT_ID
    result = await fetch_smart_money_netflow(chains=chains, per_page=top_n)

    if not result or not result.get("data"):
        logger.error("No data from Nansen API")
        return None

    tokens = result["data"]
    credits = result.get("_credits", {})

    logger.info(
        f"Nansen: got {len(tokens)} tokens, "
        f"credits used={credits.get('used')}, remaining={credits.get('remaining')}"
    )

    message = format_nansen_signal(tokens)

    # Split long messages (Telegram limit 4096 chars)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        if len(message) <= 4096:
            await bot.send_message(
                chat_id=target_chat,
                text=message,
                disable_web_page_preview=True,
            )
        else:
            parts = []
            current = ""
            for line in message.split("\n"):
                if len(current) + len(line) + 1 > 4000:
                    parts.append(current.strip())
                    current = ""
                current += line + "\n"
            if current.strip():
                parts.append(current.strip())

            for part in parts:
                await bot.send_message(
                    chat_id=target_chat,
                    text=part,
                    disable_web_page_preview=True,
                )

        logger.info(f"Nansen signal sent to chat {target_chat} ({len(tokens)} tokens)")
    except Exception as e:
        logger.error(f"Failed to send Nansen signal to community: {e}", exc_info=True)
    finally:
        await bot.session.close()

    return credits
