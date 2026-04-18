"""
Token Analyzer — main orchestrator.
Given a contract address: fetches all data, runs all analysis modules,
returns a structured FullAnalysis.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from narrative import classify_narrative, NarrativeResult
from smart_money import fetch_recent_trades, analyze_smart_money, SmartMoneyReport
from risk_analyzer import analyze_risk, RiskReport
from coingecko import fetch_coingecko_info
from rugcheck import fetch_rugcheck
from sm_db import count_sm_buys, SmartMoneyDBResult

log = logging.getLogger(__name__)

DEXSCREENER_BASE  = "https://api.dexscreener.com/latest/dex"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
COINGECKO_BASE    = "https://api.coingecko.com/api/v3"

CHAIN_SLUG = {
    "solana": "solana", "ethereum": "eth", "base": "base",
    "bsc": "bsc", "arbitrum": "arbitrum", "polygon": "polygon",
}


@dataclass
class TokenInfo:
    address: str
    chain: str
    symbol: str
    name: str
    description: str
    categories: list[str]
    current_price: float
    mcap: float
    fdv: float
    ath_mcap: float
    liquidity_usd: float
    volume_24h: float
    volume_6h: float
    volume_1h: float
    price_change_1h: float
    price_change_6h: float
    price_change_24h: float
    price_change_7d: float
    txns_24h_buy: int
    txns_24h_sell: int
    age_days: float
    holders: int
    pool_address: str
    dexscreener_url: str
    birdeye_url: str
    socials: dict = field(default_factory=dict)


@dataclass
class FullAnalysis:
    token: TokenInfo
    narrative: NarrativeResult
    smart_money: SmartMoneyReport
    risk: RiskReport
    buy_verdict: str       # "BUY" | "WATCH" | "RISKY" | "AVOID"
    buy_score: int         # 0–100
    summary_lines: list[str]
    sm_db: Optional[SmartMoneyDBResult] = None  # curated WAYNE_PIRATE SM wallets


class TokenAnalyzer:
    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "TokenAnalyzer/1.0"},
        )

    async def close(self):
        await self._client.aclose()

    # ─────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────

    async def analyze(self, address: str, chain: str = "auto") -> FullAnalysis:
        log.info("Analyzing %s on %s...", address, chain)

        # ── Fetch from DexScreener ────────────────────────────────
        ds_data = await self._fetch_dexscreener(address, chain_hint=chain)
        if not ds_data:
            raise ValueError(f"Token {address} not found on DexScreener")

        token = self._parse_dexscreener(address, ds_data, chain)

        # ── Fetch from GeckoTerminal (description, holders) ────────
        gt_info = await self._fetch_geckoterminal_token(address, token.chain)
        if gt_info:
            if gt_info.get("description") and not token.description:
                token.description = gt_info["description"]
            if gt_info.get("holders", 0) > 0:
                token.holders = gt_info["holders"]

        # ── Fetch from CoinGecko (description, categories, ATH) ───
        # Prefer contract lookup — symbol-search falls back only on miss.
        cg_info = await fetch_coingecko_info(
            self._client, address, token.chain, token.symbol, token.name,
        )
        if cg_info:
            if cg_info.get("description") and not token.description:
                token.description = cg_info["description"]
            if cg_info.get("categories"):
                token.categories = cg_info["categories"]
            if cg_info.get("ath_market_cap", 0) > token.ath_mcap:
                token.ath_mcap = cg_info["ath_market_cap"]

        # ── Fetch recent trades (smart money) + Rugcheck in parallel ──
        trades_task = asyncio.create_task(
            fetch_recent_trades(token.pool_address, token.chain, limit=100)
        ) if token.pool_address else None
        rugcheck_task = asyncio.create_task(
            fetch_rugcheck(self._client, token.address, token.chain)
        )
        trades   = await trades_task if trades_task else []
        rugcheck = await rugcheck_task

        # ── Run analysis modules ─────────────────────────────────
        narrative   = classify_narrative(token.symbol, token.name, token.description, token.categories)
        smart_money = analyze_smart_money(trades)
        risk        = analyze_risk(
            mcap=token.mcap,
            fdv=token.fdv,
            liquidity_usd=token.liquidity_usd,
            price_change_1h=token.price_change_1h,
            price_change_6h=token.price_change_6h,
            price_change_24h=token.price_change_24h,
            txns={"buys": token.txns_24h_buy, "sells": token.txns_24h_sell},
            volume_24h=token.volume_24h,
            age_days=token.age_days,
            holders=token.holders,
            socials=token.socials,
            pair_data={},
            rugcheck=rugcheck,
        )

        # ── Curated SM DB lookup (Solana only) ───────────────────
        sm_db_result: Optional[SmartMoneyDBResult] = None
        if token.chain.lower() == "solana":
            sm_db_result = count_sm_buys(token.address, hours=24)
            if not sm_db_result.available:
                sm_db_result = None

        # ── Compute final verdict ─────────────────────────────────
        verdict, score, summary = self._compute_verdict(
            token, narrative, smart_money, risk, sm_db_result,
        )

        return FullAnalysis(
            token=token,
            narrative=narrative,
            smart_money=smart_money,
            risk=risk,
            buy_verdict=verdict,
            buy_score=score,
            summary_lines=summary,
            sm_db=sm_db_result,
        )

    # ─────────────────────────────────────────────────────────────
    # Data fetchers
    # ─────────────────────────────────────────────────────────────

    async def _fetch_dexscreener(
        self,
        address: str,
        chain_hint: str = "auto",
    ) -> Optional[dict]:
        try:
            url = f"{DEXSCREENER_BASE}/tokens/{address}"
            r = await self._client.get(url)
            r.raise_for_status()
            pairs = r.json().get("pairs") or []
            if not pairs:
                return None
            chain_lower = (chain_hint or "").lower()
            if chain_lower and chain_lower != "auto":
                filtered = [p for p in pairs if (p.get("chainId") or "").lower() == chain_lower]
                if filtered:
                    pairs = filtered
            # Best pair by liquidity (on requested chain if possible, else global)
            return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
        except Exception as e:
            log.error("DexScreener error: %s", e)
            return None

    def _parse_dexscreener(self, address: str, pair: dict, chain_hint: str) -> TokenInfo:
        base   = pair.get("baseToken", {})
        liq    = pair.get("liquidity", {})
        vol    = pair.get("volume",    {})
        chg    = pair.get("priceChange", {})
        txns   = pair.get("txns", {})
        txns24 = txns.get("h24", {})
        chain  = pair.get("chainId", chain_hint)

        mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
        fdv  = float(pair.get("fdv") or mcap)

        # Social links from DexScreener info
        info    = pair.get("info", {})
        socials = {}
        for s in (info.get("socials") or []):
            socials[s.get("type", "")] = s.get("url", "")
        for w in (info.get("websites") or []):
            if w.get("url"):
                socials["website"] = w["url"]
                break

        # Age from pairCreatedAt
        created_ms = pair.get("pairCreatedAt")
        age_days = ((time.time() * 1000 - created_ms) / 86400000) if created_ms else 0

        pair_addr = pair.get("pairAddress", "")

        return TokenInfo(
            address=address,
            chain=chain,
            symbol=base.get("symbol", "???"),
            name=base.get("name", ""),
            description="",
            categories=[],
            current_price=float(pair.get("priceUsd") or 0),
            mcap=mcap,
            fdv=fdv,
            ath_mcap=mcap,  # will be updated if CoinGecko data available
            liquidity_usd=float(liq.get("usd", 0) or 0),
            volume_24h=float(vol.get("h24", 0) or 0),
            volume_6h=float(vol.get("h6", 0) or 0),
            volume_1h=float(vol.get("h1", 0) or 0),
            price_change_1h=float(chg.get("h1", 0) or 0),
            price_change_6h=float(chg.get("h6", 0) or 0),
            price_change_24h=float(chg.get("h24", 0) or 0),
            price_change_7d=0.0,
            txns_24h_buy=int(txns24.get("buys", 0) or 0),
            txns_24h_sell=int(txns24.get("sells", 0) or 0),
            age_days=age_days,
            holders=0,
            pool_address=pair_addr,
            dexscreener_url=f"https://dexscreener.com/{chain}/{pair_addr}",
            birdeye_url=f"https://birdeye.so/token/{address}",
            socials=socials,
        )

    async def _fetch_geckoterminal_token(self, address: str, chain: str) -> Optional[dict]:
        chain_slug = CHAIN_SLUG.get(chain.lower(), chain.lower())
        try:
            url = f"{GECKOTERMINAL_BASE}/networks/{chain_slug}/tokens/{address}"
            r = await self._client.get(url, params={"include": "top_pools"})
            r.raise_for_status()
            attrs = r.json().get("data", {}).get("attributes", {})
            return {
                "description": attrs.get("description", ""),
                "holders": int(attrs.get("holders", 0) or 0),
                "categories": [],
            }
        except Exception as e:
            log.debug("GeckoTerminal token info failed: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────
    # Verdict
    # ─────────────────────────────────────────────────────────────

    def _compute_verdict(
        self,
        token: TokenInfo,
        narrative: NarrativeResult,
        sm: SmartMoneyReport,
        risk: RiskReport,
        sm_db: Optional[SmartMoneyDBResult] = None,
    ) -> tuple[str, int, list[str]]:
        score = 50  # start neutral

        summary = []

        # ── Narrative strength ────────────────────────────────────
        if narrative.confidence == "high":
            score += 10
            summary.append(f"✅ Нарратив '{narrative.name}' — горячий тренд")
        elif narrative.confidence == "medium":
            score += 5
            summary.append(f"🟡 Нарратив '{narrative.name}' — умеренный интерес")
        else:
            summary.append(f"⚪ Нарратив '{narrative.name}' — неясный или новый")

        # ── Smart money ───────────────────────────────────────────
        if sm.confidence == "high":
            score += 20
            summary.append("✅ Сильные признаки умных денег")
        elif sm.confidence == "medium":
            score += 10
            summary.append("🟡 Умеренные признаки умных денег")
        elif sm.confidence == "low":
            score += 3
        else:
            score -= 5
            summary.append("⚪ Умные деньги не обнаружены")

        if sm.whale_activity:
            score += 5
            summary.append("✅ Замечена активность китов")

        # ── Risk adjustment ───────────────────────────────────────
        if risk.overall_risk == "low":
            score += 10
            summary.append("✅ Низкие риски")
        elif risk.overall_risk == "medium":
            pass
        elif risk.overall_risk == "high":
            score -= 15
            summary.append("🔴 Высокие риски — осторожно")
        elif risk.overall_risk == "critical":
            score -= 35
            summary.append("🚨 Критические риски — вероятный скам")

        # ── Market cap opportunity ────────────────────────────────
        if token.mcap < 1_000_000:
            score += 10
            summary.append(f"✅ Низкий mcap ${token.mcap:,.0f} — высокая потенциальная доходность")
        elif token.mcap < 10_000_000:
            score += 5

        # ── Buy/sell pressure ─────────────────────────────────────
        if sm.buy_sell_ratio >= 0.65:
            score += 5
        elif sm.buy_sell_ratio < 0.40:
            score -= 10

        # ── Volume/mcap health ────────────────────────────────────
        vol_ratio = token.volume_24h / max(token.mcap, 1)
        if vol_ratio >= 0.05:
            score += 5
        elif vol_ratio < 0.005:
            score -= 10
            summary.append("⚪ Очень низкий торговый объём")

        # ── Curated smart-money DB bonus (strongest single signal) ─
        if sm_db and sm_db.available and sm_db.unique_wallets > 0:
            if sm_db.unique_wallets >= 10:
                score += 25
                summary.append(f"🔥 {sm_db.unique_wallets} curated SM-кошельков купили за 24h")
            elif sm_db.unique_wallets >= 5:
                score += 18
                summary.append(f"✅ {sm_db.unique_wallets} SM-кошельков купили за 24h")
            elif sm_db.unique_wallets >= 3:
                score += 12
                summary.append(f"✅ {sm_db.unique_wallets} SM-кошельков купили за 24h")
            else:
                score += 5
                summary.append(f"🟡 {sm_db.unique_wallets} SM-кошелёк купил за 24h")
            if sm_db.total_buy_usd >= 50_000:
                score += 3

        # ── Clamp and verdict ─────────────────────────────────────
        score = max(0, min(100, score))

        if score >= 70:      verdict = "BUY"
        elif score >= 55:    verdict = "WATCH"
        elif score >= 35:    verdict = "RISKY"
        else:                verdict = "AVOID"

        return verdict, score, summary
