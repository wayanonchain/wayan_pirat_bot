"""Birdeye API client — SOL price with caching and fallbacks."""

import logging
import time

import httpx

from config.settings import BIRDEYE_API_KEY, SOLANATRACKER_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so"
HEADERS = {"X-API-KEY": BIRDEYE_API_KEY}

SOL_MINT = "So11111111111111111111111111111111111111112"

# Cache: price + timestamp
_sol_price_cache: dict = {"price": None, "updated_at": 0}
CACHE_TTL_SECONDS = 300  # 5 minutes


async def _fetch_price_birdeye() -> float | None:
    """Try Birdeye API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BASE_URL}/defi/price",
                params={"address": SOL_MINT},
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success", True):
                logger.warning(f"Birdeye API: {data.get('message', 'error')}")
                return None
            price = data.get("data", {}).get("value")
            if price and price > 0:
                return float(price)
    except Exception as e:
        logger.warning(f"Birdeye SOL price error: {e}")
    return None


async def _fetch_price_solanatracker() -> float | None:
    """Try SolanaTracker API as fallback."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://data.solanatracker.io/tokens/{SOL_MINT}",
                headers={"x-api-key": SOLANATRACKER_API_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            price = data.get("pools", [{}])[0].get("price", {}).get("usd")
            if price and price > 0:
                return float(price)
    except Exception as e:
        logger.warning(f"SolanaTracker SOL price error: {e}")
    return None


async def _fetch_price_coingecko() -> float | None:
    """Try CoinGecko free API as last fallback (no key needed)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            price = resp.json().get("solana", {}).get("usd")
            if price and price > 0:
                return float(price)
    except Exception as e:
        logger.warning(f"CoinGecko SOL price error: {e}")
    return None


async def get_sol_price() -> float:
    """Get SOL price in USD. Cached 5 min. Falls back through 3 sources."""
    now = time.time()
    if (_sol_price_cache["price"] is not None
            and now - _sol_price_cache["updated_at"] < CACHE_TTL_SECONDS):
        return _sol_price_cache["price"]

    # Try sources in order: Birdeye → SolanaTracker → CoinGecko
    for name, fetcher in [
        ("Birdeye", _fetch_price_birdeye),
        ("SolanaTracker", _fetch_price_solanatracker),
        ("CoinGecko", _fetch_price_coingecko),
    ]:
        price = await fetcher()
        if price:
            _sol_price_cache["price"] = price
            _sol_price_cache["updated_at"] = now
            logger.info(f"SOL price updated via {name}: ${price:.2f}")
            return price

    # Fallback to last cached price (even if stale)
    if _sol_price_cache["price"] is not None:
        logger.warning(f"All price APIs failed, using stale cache: ${_sol_price_cache['price']:.2f}")
        return _sol_price_cache["price"]

    logger.error("All SOL price sources failed and no cache available")
    return 0.0
