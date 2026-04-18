"""
DexScreener + GeckoTerminal API client.
No API key required for basic usage.
"""
import asyncio
import logging
import os
import time
from typing import Optional
import httpx
from models import TokenMetadata, OHLCV
from coingecko import fetch_coingecko_info

log = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"

# chain slug maps for GeckoTerminal
CHAIN_SLUG = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
}


class DataFetcher:
    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "AccumulationAgent/1.0"},
        )
        # Short-lived cache (key: (address, chain) → (ts, pair dict)) so a
        # single monitor cycle only hits DexScreener once per token.
        self._pair_cache: dict[tuple[str, str], tuple[float, Optional[dict]]] = {}

    async def close(self):
        await self._client.aclose()

    # ─────────────────────────────────────────────────────────────
    # Token metadata via DexScreener
    # ─────────────────────────────────────────────────────────────

    async def _fetch_best_pair(self, address: str, chain: str = "solana") -> Optional[dict]:
        """Single DexScreener request; returns the best pair (by liquidity,
        optionally filtered to the requested chain). Cached for a short
        window so get_token_metadata and get_pool_address don't hit
        DexScreener twice for the same token on the same pass.
        """
        cache_key = (address, chain)
        cached = self._pair_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < 30:
            return cached[1]
        try:
            url = f"{DEXSCREENER_BASE}/tokens/{address}"
            r = await self._client.get(url)
            r.raise_for_status()
            pairs = (r.json() or {}).get("pairs") or []
            if not pairs:
                self._pair_cache[cache_key] = (time.time(), None)
                return None
            chain_lower = (chain or "").lower()
            if chain_lower and chain_lower != "auto":
                filtered = [p for p in pairs if (p.get("chainId") or "").lower() == chain_lower]
                if filtered:
                    pairs = filtered
            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
            self._pair_cache[cache_key] = (time.time(), best)
            return best
        except Exception as e:
            log.error("DexScreener fetch failed for %s: %s", address, e)
            return None

    async def get_token_metadata(self, address: str, chain: str = "solana") -> Optional[TokenMetadata]:
        """Fetch current token stats. Uses the per-request pair cache so a
        subsequent get_pool_address does NOT make a second DexScreener call.
        """
        pair = await self._fetch_best_pair(address, chain)
        if not pair:
            return None

        base = pair.get("baseToken", {})
        liq  = pair.get("liquidity", {})
        mcap = pair.get("marketCap") or pair.get("fdv") or 0
        price = float(pair.get("priceUsd") or 0)
        ath_mcap = float(pair.get("marketCap") or mcap)  # placeholder, updated via state
        pair_addr = pair.get("pairAddress", "")
        chain_id  = pair.get("chainId", chain)

        return TokenMetadata(
            address=address,
            symbol=base.get("symbol", "???"),
            name=base.get("name", ""),
            chain=chain_id,
            current_price=price,
            current_mcap=float(mcap),
            ath_mcap=ath_mcap,
            liquidity_usd=float(liq.get("usd", 0) or 0),
            holders=0,
            age_days=self._compute_age_days(pair.get("pairCreatedAt")),
            dexscreener_url=f"https://dexscreener.com/{chain_id}/{pair_addr}",
            birdeye_url=f"https://birdeye.so/token/{address}",
        )

    def _compute_age_days(self, created_at_ms: Optional[int]) -> float:
        if not created_at_ms:
            return 0.0
        now_ms = time.time() * 1000
        return (now_ms - created_at_ms) / (1000 * 86400)

    # ─────────────────────────────────────────────────────────────
    # OHLCV candles via GeckoTerminal
    # ─────────────────────────────────────────────────────────────

    async def get_ohlcv(
        self,
        pool_address: str,
        chain: str = "solana",
        timeframe: str = "day",    # "day" | "hour" | "minute"
        aggregate: int = 1,        # 1 day | 4 hours etc
        limit: int = 60,
    ) -> list[OHLCV]:
        """
        Fetch OHLCV from GeckoTerminal.
        timeframe: "day", "hour", "minute"
        """
        chain_slug = CHAIN_SLUG.get(chain.lower(), chain.lower())
        url = (
            f"{GECKOTERMINAL_BASE}/networks/{chain_slug}/pools/{pool_address}"
            f"/ohlcv/{timeframe}"
        )
        params = {"aggregate": aggregate, "limit": limit, "currency": "usd"}
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            raw = r.json()
            candles = raw.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            result = []
            for c in candles:
                # format: [timestamp, open, high, low, close, volume]
                if len(c) >= 6:
                    result.append(OHLCV(
                        timestamp=int(c[0]),
                        open=float(c[1]),
                        high=float(c[2]),
                        low=float(c[3]),
                        close=float(c[4]),
                        volume=float(c[5]),
                    ))
            # sort oldest first
            result.sort(key=lambda x: x.timestamp)
            return result
        except Exception as e:
            log.error("get_ohlcv failed for pool %s: %s", pool_address, e)
            return []

    async def get_pool_address(
        self,
        token_address: str,
        chain: str = "solana",
    ) -> tuple[Optional[str], str]:
        """Pool address + resolved chain. Reuses the cached pair from the
        most recent get_token_metadata call on the same token.
        """
        pair = await self._fetch_best_pair(token_address, chain)
        if not pair:
            return None, chain
        return pair.get("pairAddress"), (pair.get("chainId") or chain).lower()

    async def get_holders(self, token_address: str, chain: str = "solana") -> int:
        """Get holder count. Tries GeckoTerminal first (free, works for EVM
        chains more often), then Birdeye for Solana if BIRDEYE_API_KEY is
        set. Returns 0 if no source has data.
        """
        chain_slug = CHAIN_SLUG.get(chain.lower(), chain.lower())
        try:
            url = f"{GECKOTERMINAL_BASE}/networks/{chain_slug}/tokens/{token_address}"
            r = await self._client.get(url)
            r.raise_for_status()
            attrs = r.json().get("data", {}).get("attributes", {})
            gt_holders = int(attrs.get("holders", 0) or 0)
            if gt_holders > 0:
                return gt_holders
        except Exception:
            pass

        # Birdeye fallback (Solana). GeckoTerminal holders are often 0 for SOL.
        if chain.lower() == "solana":
            return await self._get_holders_birdeye(token_address)
        return 0

    async def _get_holders_birdeye(self, token_address: str) -> int:
        api_key = os.getenv("BIRDEYE_API_KEY", "")
        if not api_key:
            return 0
        try:
            r = await self._client.get(
                "https://public-api.birdeye.so/defi/v3/token/holder",
                params={"address": token_address, "offset": 0, "limit": 1},
                headers={"x-chain": "solana", "X-API-KEY": api_key},
            )
            r.raise_for_status()
            data = r.json().get("data") or {}
            # Birdeye returns {"total": N, "items": [...]}
            total = int(data.get("total") or 0)
            return total
        except Exception as e:
            log.debug("Birdeye holders fallback failed for %s: %s", token_address, e)
            return 0

    async def get_coingecko_ath_mcap(self, address: str, chain: str) -> float:
        """Fetch historical ATH market cap from CoinGecko.

        Critical for the accumulation detector: without a real ATH, the
        agent's "drawdown from ATH" collapses to zero whenever the bot is
        started after a drawdown has already happened.
        """
        info = await fetch_coingecko_info(self._client, address, chain)
        if info:
            return float(info.get("ath_market_cap", 0) or 0)
        return 0.0
