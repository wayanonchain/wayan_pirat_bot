"""Birdeye API client — SOL price with caching."""

import logging
import time

import httpx

from config.settings import BIRDEYE_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so"
HEADERS = {"X-API-KEY": BIRDEYE_API_KEY}

SOL_MINT = "So11111111111111111111111111111111111111112"

# Cache: price + timestamp
_sol_price_cache: dict = {"price": None, "updated_at": 0}
CACHE_TTL_SECONDS = 300  # 5 minutes


async def get_sol_price() -> float:
    """Get SOL price in USD. Cached for 5 minutes to save API credits."""
    now = time.time()
    if (_sol_price_cache["price"] is not None
            and now - _sol_price_cache["updated_at"] < CACHE_TTL_SECONDS):
        return _sol_price_cache["price"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BASE_URL}/defi/price",
                params={"address": SOL_MINT},
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            price = data.get("data", {}).get("value")
            if price and price > 0:
                _sol_price_cache["price"] = float(price)
                _sol_price_cache["updated_at"] = now
                logger.info(f"SOL price updated: ${price:.2f}")
                return float(price)
    except Exception as e:
        logger.warning(f"Birdeye SOL price error: {e}")

    # Fallback to cached or default
    if _sol_price_cache["price"] is not None:
        return _sol_price_cache["price"]
    return 130.0  # Last resort fallback
