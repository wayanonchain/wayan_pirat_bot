"""Birdeye API client — SOL price with caching and fallbacks."""

import asyncio
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

# Negative cache. When all 3 sources fail, suppress retries for this many
# seconds — otherwise every webhook event (≈2/sec) re-tries all 3 APIs and
# floods logs / risks event-loop starvation.
FAIL_BACKOFF_SECONDS = 60
_last_full_failure_at: float = 0.0

# Track "all sources down" state so we log it once per transition, not per call.
_in_failure_state: bool = False

# Serialize concurrent fetches: on cache miss with N concurrent webhook events,
# only the first one hits the APIs; the rest wait and reuse the cached result.
_fetch_lock = asyncio.Lock()


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
                logger.debug(f"Birdeye API: {data.get('message', 'error')}")
                return None
            price = data.get("data", {}).get("value")
            if price and price > 0:
                return float(price)
    except Exception as e:
        logger.debug(f"Birdeye SOL price error: {e}")
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
        logger.debug(f"SolanaTracker SOL price error: {e}")
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
        logger.debug(f"CoinGecko SOL price error: {e}")
    return None


async def get_sol_price() -> float:
    """Get SOL price in USD. Cached 5 min. Falls back through 3 sources."""
    global _last_full_failure_at, _in_failure_state

    now = time.time()
    if (_sol_price_cache["price"] is not None
            and now - _sol_price_cache["updated_at"] < CACHE_TTL_SECONDS):
        return _sol_price_cache["price"]

    # Negative cache short-circuit: skip API calls entirely during backoff.
    if now - _last_full_failure_at < FAIL_BACKOFF_SECONDS:
        return _sol_price_cache["price"] or 0.0

    async with _fetch_lock:
        # Re-check after lock — another coroutine may have refreshed the cache
        # or hit the failure backoff while we were waiting.
        now = time.time()
        if (_sol_price_cache["price"] is not None
                and now - _sol_price_cache["updated_at"] < CACHE_TTL_SECONDS):
            return _sol_price_cache["price"]
        if now - _last_full_failure_at < FAIL_BACKOFF_SECONDS:
            return _sol_price_cache["price"] or 0.0

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
                if _in_failure_state:
                    logger.info(f"SOL price recovered via {name}: ${price:.2f}")
                    _in_failure_state = False
                else:
                    logger.info(f"SOL price updated via {name}: ${price:.2f}")
                return price

        # All 3 sources failed: arm backoff and log once per transition.
        _last_full_failure_at = now
        if not _in_failure_state:
            _in_failure_state = True
            if _sol_price_cache["price"] is not None:
                logger.warning(
                    f"All SOL price sources failed — serving stale cache "
                    f"${_sol_price_cache['price']:.2f} for next {FAIL_BACKOFF_SECONDS}s"
                )
            else:
                logger.warning(
                    f"All SOL price sources failed and no cache — returning 0, "
                    f"backoff {FAIL_BACKOFF_SECONDS}s"
                )

        return _sol_price_cache["price"] or 0.0
