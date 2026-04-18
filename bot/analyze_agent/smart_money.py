"""
Smart money detector using GeckoTerminal recent trades.
No paid API — analyzes public trade data for smart money patterns.
"""
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import httpx

log = logging.getLogger(__name__)


def _parse_ts(value) -> int:
    """Parse timestamp: int (unix) or ISO string → unix int."""
    if not value:
        return int(time.time())
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"

CHAIN_SLUG = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "avalanche": "avax",
}


@dataclass
class TradeRecord:
    wallet: str
    side: str          # "buy" | "sell"
    usd_value: float
    timestamp: int
    price: float


@dataclass
class WalletProfile:
    address: str
    total_buys_usd: float
    total_sells_usd: float
    buy_count: int
    sell_count: int
    first_buy_ts: int
    last_activity_ts: int
    avg_buy_size: float
    is_smart_pattern: bool
    pattern_reason: str


@dataclass
class SmartMoneyReport:
    has_smart_money: bool
    confidence: str          # "high" | "medium" | "low" | "none"
    smart_wallets_count: int
    total_smart_buy_usd: float
    buy_sell_ratio: float    # buys / (buys + sells) last 24h
    large_buy_count: int     # buys > $5000
    avg_buy_size: float
    whale_activity: bool     # any single buy > $10k
    pattern_description: str
    wallets: list[WalletProfile]
    signal_lines: list[str]  # human-readable signals for report
    warning_lines: list[str] # red flags


async def fetch_recent_trades(
    pool_address: str,
    chain: str = "solana",
    limit: int = 100,
) -> list[TradeRecord]:
    """Fetch recent trades.

    For Solana: prefer Helius (real wallet addresses for smart-money work),
    falling back to the public GeckoTerminal endpoint. For other chains:
    GeckoTerminal only.
    """
    # Try Helius first on Solana — only path that gives real wallets.
    if (chain or "").lower() == "solana":
        try:
            # Local import to avoid circular dependency at module load time
            from helius import fetch_pool_trades_helius
            async with httpx.AsyncClient(timeout=15) as client:
                helius_trades = await fetch_pool_trades_helius(
                    pool_address, client, limit=limit,
                )
            if helius_trades:
                return helius_trades
        except Exception as e:
            log.debug("Helius path failed, falling back to GeckoTerminal: %s", e)

    # Fallback: public GeckoTerminal — no real wallet addresses, only tx_hash
    # prefix as a distinguisher. Useful for non-Solana chains or when the
    # Helius key is not configured.
    chain_slug = CHAIN_SLUG.get(chain.lower(), chain.lower())
    url = f"{GECKOTERMINAL_BASE}/networks/{chain_slug}/pools/{pool_address}/trades"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"trade_volume_in_usd_greater_than": 0})
            r.raise_for_status()
            raw_trades = r.json().get("data", [])

        trades = []
        for t in raw_trades[:limit]:
            attrs = t.get("attributes", {})
            side  = attrs.get("kind", "buy")
            usd   = float(attrs.get("volume_in_usd", 0) or 0)
            ts    = _parse_ts(attrs.get("block_timestamp", 0))
            price = float(attrs.get("price_to_in_usd", 0) or 0)
            tx    = attrs.get("tx_hash", "")
            # tx-hash prefix: intentionally NOT a real wallet. Downstream
            # wallet-level logic should skip non-canonical addresses.
            wallet = f"tx:{tx[:10]}" if tx else "unknown"

            trades.append(TradeRecord(
                wallet=wallet,
                side=side,
                usd_value=usd,
                timestamp=ts,
                price=price,
            ))
        return trades

    except Exception as e:
        log.warning("Failed to fetch trades for %s: %s", pool_address, e)
        return []


async def fetch_top_traders(
    pool_address: str,
    chain: str = "solana",
) -> list[dict]:
    """Fetch top traders from GeckoTerminal (if available)."""
    chain_slug = CHAIN_SLUG.get(chain.lower(), chain.lower())
    # GeckoTerminal doesn't have a direct top_traders endpoint in free tier
    # We use the trades endpoint and aggregate ourselves
    return []


def analyze_smart_money(trades: list[TradeRecord]) -> SmartMoneyReport:
    """
    Analyze trade data for smart money patterns.
    Smart money characteristics:
    - Consistent large buys (avg buy > $2k)
    - Buy without immediate sell (holding pattern)
    - Accumulation at lows (buy when price is low)
    - Multiple buys over time (not single FOMO)
    - Low sell pressure relative to buy
    """
    if not trades:
        return SmartMoneyReport(
            has_smart_money=False, confidence="none",
            smart_wallets_count=0, total_smart_buy_usd=0,
            buy_sell_ratio=0.5, large_buy_count=0, avg_buy_size=0,
            whale_activity=False,
            pattern_description="Нет данных о сделках",
            wallets=[], signal_lines=[], warning_lines=[],
        )

    now = int(time.time())
    hour_24 = now - 86400
    hour_6  = now - 21600
    hour_1  = now - 3600

    # ── Volume breakdown ──────────────────────────────────────────
    buys_24h  = [t for t in trades if t.side == "buy"  and t.timestamp >= hour_24]
    sells_24h = [t for t in trades if t.side == "sell" and t.timestamp >= hour_24]
    buys_6h   = [t for t in trades if t.side == "buy"  and t.timestamp >= hour_6]
    buys_1h   = [t for t in trades if t.side == "buy"  and t.timestamp >= hour_1]

    total_buy_vol  = sum(t.usd_value for t in buys_24h)
    total_sell_vol = sum(t.usd_value for t in sells_24h)
    total_vol      = total_buy_vol + total_sell_vol

    buy_sell_ratio = total_buy_vol / total_vol if total_vol > 0 else 0.5

    # Unique buyer wallets — only real Solana addresses count. Synthetic
    # "tx:XXXX" identifiers from the GeckoTerminal fallback path are
    # counted separately so we don't overstate wallet diversity.
    real_buyer_wallets = {t.wallet for t in buys_24h if not t.wallet.startswith("tx:")}
    real_seller_wallets = {t.wallet for t in sells_24h if not t.wallet.startswith("tx:")}
    unique_buyers  = len(real_buyer_wallets)
    unique_sellers = len(real_seller_wallets)

    # ── Large trades ──────────────────────────────────────────────
    large_buys  = [t for t in buys_24h  if t.usd_value >= 5_000]
    whale_buys  = [t for t in buys_24h  if t.usd_value >= 10_000]
    large_sells = [t for t in sells_24h if t.usd_value >= 5_000]

    avg_buy_size = total_buy_vol / len(buys_24h) if buys_24h else 0

    # ── Pattern analysis ──────────────────────────────────────────
    signal_lines  = []
    warning_lines = []

    # Buy pressure
    if buy_sell_ratio >= 0.70:
        signal_lines.append(f"🟢 Сильное давление покупок: {buy_sell_ratio*100:.0f}% объёма — байсайд")
    elif buy_sell_ratio >= 0.55:
        signal_lines.append(f"🟡 Умеренное давление покупок: {buy_sell_ratio*100:.0f}%")
    else:
        warning_lines.append(f"🔴 Доминируют продажи: только {buy_sell_ratio*100:.0f}% объёма — байсайд")

    # Wallet diversity (only meaningful when Helius path is active)
    if unique_buyers >= 30:
        signal_lines.append(f"🟢 {unique_buyers} уникальных покупателей за 24h — широкий интерес")
    elif unique_buyers >= 10:
        signal_lines.append(f"🟡 {unique_buyers} уникальных покупателей за 24h")
    elif unique_buyers > 0 and unique_buyers < 5:
        warning_lines.append(f"⚠️ Только {unique_buyers} покупателей — возможно узкий интерес")

    # Large buys
    if len(large_buys) >= 5:
        signal_lines.append(f"🟢 {len(large_buys)} крупных покупок (>$5k) за 24h — институциональный интерес")
    elif len(large_buys) >= 2:
        signal_lines.append(f"🟡 {len(large_buys)} крупных покупок (>$5k) за 24h")

    # Whale activity
    whale_activity = len(whale_buys) > 0
    if whale_buys:
        max_whale = max(whale_buys, key=lambda t: t.usd_value)
        signal_lines.append(f"🟢 Кит: одна покупка ${max_whale.usd_value:,.0f}")

    # Large sells
    if len(large_sells) >= 3:
        warning_lines.append(f"⚠️ {len(large_sells)} крупных продаж (>$5k) за 24h — умные деньги выходят?")

    # Volume acceleration
    if buys_1h and buys_24h:
        hourly_rate_now = sum(t.usd_value for t in buys_1h)
        hourly_rate_avg = total_buy_vol / 24
        if hourly_rate_avg > 0:
            accel = hourly_rate_now / hourly_rate_avg
            if accel >= 3:
                signal_lines.append(f"🟢 Ускорение покупок: x{accel:.1f} от среднечасового за 24h")
            elif accel >= 1.5:
                signal_lines.append(f"🟡 Рост активности: x{accel:.1f} от средней")

    # Sell/buy pattern in last 6h vs earlier
    buys_early  = [t for t in buys_24h if t.timestamp < hour_6]
    buys_recent = buys_6h
    if buys_early and buys_recent:
        early_avg  = sum(t.usd_value for t in buys_early)  / len(buys_early)
        recent_avg = sum(t.usd_value for t in buys_recent) / len(buys_recent)
        if recent_avg >= early_avg * 1.5:
            signal_lines.append(f"🟢 Размер покупок растёт: средняя за 6h ${recent_avg:,.0f} vs ${early_avg:,.0f} ранее")

    # ── Smart money score ─────────────────────────────────────────
    sm_score = 0
    sm_score += min(len(large_buys), 10) * 2
    sm_score += min(len(whale_buys), 5)  * 3
    sm_score += (buy_sell_ratio - 0.5) * 30
    sm_score += len(signal_lines) * 5
    sm_score -= len(warning_lines) * 8

    has_sm = sm_score >= 15
    if sm_score >= 30:   confidence = "high"
    elif sm_score >= 15: confidence = "medium"
    elif sm_score >= 5:  confidence = "low"
    else:                confidence = "none"

    if not signal_lines and not warning_lines:
        pattern = "Стандартная торговая активность без явных паттернов"
    elif has_sm:
        pattern = "Признаки координированных крупных покупок"
    else:
        pattern = "Преобладает розничная торговля или продажи"

    return SmartMoneyReport(
        has_smart_money=has_sm,
        confidence=confidence,
        smart_wallets_count=len(large_buys),
        total_smart_buy_usd=sum(t.usd_value for t in large_buys),
        buy_sell_ratio=buy_sell_ratio,
        large_buy_count=len(large_buys),
        avg_buy_size=avg_buy_size,
        whale_activity=whale_activity,
        pattern_description=pattern,
        wallets=[],
        signal_lines=signal_lines,
        warning_lines=warning_lines,
    )
