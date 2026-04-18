"""
CoinGecko client — lookup by contract address (primary) with symbol-search
fallback. Extracts description, categories and ATH market cap.

Looking up by contract avoids false matches between tokens that share a
ticker symbol (a common footgun with meme coins).
"""
import asyncio
import logging
from typing import Optional
import httpx

log = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Map our internal chain names → CoinGecko "asset_platform" slugs.
# See: https://api.coingecko.com/api/v3/asset_platforms
CG_PLATFORM = {
    "solana":     "solana",
    "ethereum":   "ethereum",
    "eth":        "ethereum",
    "base":       "base",
    "bsc":        "binance-smart-chain",
    "bnb":        "binance-smart-chain",
    "arbitrum":   "arbitrum-one",
    "polygon":    "polygon-pos",
    "matic":      "polygon-pos",
    "avalanche":  "avalanche",
    "avax":       "avalanche",
    "optimism":   "optimistic-ethereum",
    "fantom":     "fantom",
    "ftm":        "fantom",
}


def _extract_payload(data: dict) -> dict:
    desc = (data.get("description") or {}).get("en", "") or ""
    cats = data.get("categories") or []
    market = data.get("market_data") or {}
    ath_mc = float(((market.get("ath") or {}).get("usd") or 0) or 0)
    # Some low-cap tokens only expose "market_cap"/"fully_diluted_valuation"
    # without a historical ATH; in that case we fall back to current mcap.
    if ath_mc <= 0:
        current_mc = float(((market.get("market_cap") or {}).get("usd") or 0) or 0)
        ath_mc = current_mc
    return {
        "description":    desc[:500] if desc else "",
        "categories":     cats,
        "ath_market_cap": ath_mc,
        "coingecko_id":   data.get("id") or "",
    }


async def fetch_by_contract(
    client: httpx.AsyncClient,
    address: str,
    chain: str,
) -> Optional[dict]:
    """Preferred path: exact token lookup by (platform, contract)."""
    platform = CG_PLATFORM.get((chain or "").lower())
    if not platform:
        return None
    url = f"{COINGECKO_BASE}/coins/{platform}/contract/{address}"
    try:
        r = await client.get(
            url,
            params={"localization": "false", "tickers": "false", "community_data": "false"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _extract_payload(r.json())
    except Exception as e:
        log.debug("CoinGecko contract lookup failed for %s on %s: %s", address, chain, e)
        return None


async def fetch_by_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    name: str = "",
) -> Optional[dict]:
    """Fallback path: symbol-based search. Less accurate — only use when
    the contract is not on CoinGecko (e.g., very small / new tokens).
    """
    if not symbol:
        return None
    try:
        r = await client.get(f"{COINGECKO_BASE}/search", params={"query": symbol})
        r.raise_for_status()
        coins = r.json().get("coins", [])
        if not coins:
            return None
        best = None
        name_l = (name or "").lower()
        # Prefer exact symbol match AND close name match if available
        for c in coins[:10]:
            if c.get("symbol", "").upper() != symbol.upper():
                continue
            if name_l and name_l in (c.get("name") or "").lower():
                best = c
                break
            if best is None:
                best = c
        if not best:
            best = coins[0]
        coin_id = best.get("id")
        if not coin_id:
            return None
        await asyncio.sleep(0.3)   # gentle with the public endpoint
        r2 = await client.get(
            f"{COINGECKO_BASE}/coins/{coin_id}",
            params={"localization": "false", "tickers": "false", "community_data": "false"},
        )
        r2.raise_for_status()
        return _extract_payload(r2.json())
    except Exception as e:
        log.debug("CoinGecko symbol search failed for %s: %s", symbol, e)
        return None


async def fetch_coingecko_info(
    client: httpx.AsyncClient,
    address: str,
    chain: str,
    symbol: str = "",
    name: str = "",
) -> Optional[dict]:
    """Prefer contract lookup; fall back to symbol search only on miss."""
    info = await fetch_by_contract(client, address, chain)
    if info:
        return info
    if symbol:
        return await fetch_by_symbol(client, symbol, name)
    return None
