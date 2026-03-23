"""Nansen API client — Smart Money netflow data."""

import logging
from typing import Optional

import aiohttp

from config.settings import NANSEN_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://api.nansen.ai/api/v1"


async def fetch_smart_money_netflow(
    chains: list[str] | None = None,
    per_page: int = 20,
    order_field: str = "net_flow_24h_usd",
    order_dir: str = "DESC",
) -> Optional[dict]:
    """
    Fetch Smart Money netflow from Nansen API.
    Returns dict with 'data', 'pagination', and 'credits' info.
    """
    if not NANSEN_API_KEY:
        logger.error("NANSEN_API_KEY not configured")
        return None

    url = f"{BASE_URL}/smart-money/netflow"
    headers = {"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"}

    payload = {
        "chains": chains or ["solana"],
        "filters": {
            "include_smart_money_labels": [
                "Fund", "Smart Trader", "30D Smart Trader",
                "90D Smart Trader", "180D Smart Trader",
            ],
            "include_stablecoins": False,
            "include_native_tokens": False,
        },
        "order_by": [{"field": order_field, "direction": order_dir}],
        "pagination": {"page": 1, "per_page": per_page},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                credits_used = resp.headers.get("X-Nansen-Credits-Used", "?")
                credits_remaining = resp.headers.get("X-Nansen-Credits-Remaining", "?")
                rate_remaining = resp.headers.get("X-RateLimit-Remaining-Minute", "?")

                logger.info(
                    f"Nansen API: status={resp.status}, "
                    f"credits_used={credits_used}, credits_remaining={credits_remaining}, "
                    f"rate_remaining={rate_remaining}/min"
                )

                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Nansen API error {resp.status}: {text}")
                    return None

                data = await resp.json()
                data["_credits"] = {
                    "used": credits_used,
                    "remaining": credits_remaining,
                }
                return data

    except Exception as e:
        logger.error(f"Nansen API request failed: {e}", exc_info=True)
        return None
