"""SolanaTracker API client for token metadata and PnL."""

import logging
import httpx

from config.settings import SOLANATRACKER_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://data.solanatracker.io"
HEADERS = {"x-api-key": SOLANATRACKER_API_KEY}


async def get_token_info(token_address: str) -> dict | None:
    """Get token metadata from SolanaTracker."""
    url = f"{BASE_URL}/tokens/{token_address}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code == 429:
                return None  # Rate limited
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token", data)
            pools = data.get("pools", [])

            mcap = None
            liquidity = None
            if pools:
                pool = pools[0]
                mcap = pool.get("marketCap", {}).get("usd")
                liquidity = pool.get("liquidity", {}).get("usd")

            return {
                "address": token_address,
                "symbol": token.get("symbol", ""),
                "name": token.get("name", ""),
                "decimals": token.get("decimals", 9),
                "mcap": mcap,
                "price_usd": token.get("price", None),
                "liquidity_usd": liquidity,
                "created_at": token.get("createdAt", None),
            }
        except Exception as e:
            logger.warning(f"Token info error for {token_address[:12]}: {e}")
            return None


async def get_wallet_pnl(wallet_address: str) -> dict | None:
    """Get PnL data for a wallet."""
    url = f"{BASE_URL}/pnl/{wallet_address}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code == 429:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Wallet PnL error for {wallet_address[:12]}: {e}")
            return None


async def get_sol_price() -> float | None:
    """Get current SOL price in USD."""
    sol_address = "So11111111111111111111111111111111111111112"
    info = await get_token_info(sol_address)
    if info and info.get("price_usd"):
        return info["price_usd"]
    return None
