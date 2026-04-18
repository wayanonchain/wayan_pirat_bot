"""
Risk analysis: concentration, dev wallet, red flags, social presence.
Uses DexScreener + GeckoTerminal + (for Solana) Rugcheck.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from rugcheck import RugCheckReport

log = logging.getLogger(__name__)


@dataclass
class RiskReport:
    overall_risk: str        # "low" | "medium" | "high" | "critical"
    risk_score: int          # 0 = safe, 100 = rug
    green_flags: list[str]
    red_flags: list[str]
    yellow_flags: list[str]

    # Liquidity
    liq_mcap_ratio: float
    liq_locked: bool
    liq_stability: str       # "stable" | "growing" | "declining" | "unknown"

    # Token metrics
    fully_diluted_ratio: float  # fdv / mcap — higher = more sell pressure incoming
    price_change_1h: float
    price_change_24h: float
    txns_buy_24h: int
    txns_sell_24h: int
    volume_24h: float

    # Social
    has_website: bool
    has_twitter: bool
    has_telegram: bool

    # Rugcheck (Solana only — empty on other chains)
    rugcheck: Optional[RugCheckReport] = None

    # Summary
    verdict: str = ""        # one-line verdict


def analyze_risk(
    mcap: float,
    fdv: float,
    liquidity_usd: float,
    price_change_1h: float,
    price_change_6h: float,
    price_change_24h: float,
    txns: dict,
    volume_24h: float,
    age_days: float,
    holders: int,
    socials: dict,
    pair_data: dict,
    rugcheck: Optional[RugCheckReport] = None,
) -> RiskReport:
    """
    Comprehensive risk analysis from market data.
    """
    green  = []
    red    = []
    yellow = []
    score  = 0  # higher = more risky

    # ── Liquidity / MCap ratio ────────────────────────────────────
    # For large caps (>$50M) liquidity is split across many pools/CEXes —
    # single-pool liq/mcap is naturally lower, so we scale thresholds.
    liq_ratio = liquidity_usd / max(mcap, 1)
    if mcap >= 50_000_000:
        # Large cap: just check absolute liquidity for tradability
        if liquidity_usd >= 500_000:
            green.append(f"✅ Ликвидность: ${liquidity_usd:,.0f} (large cap — пул только один из многих)")
        elif liquidity_usd >= 100_000:
            yellow.append(f"⚠️ Ликвидность пула: ${liquidity_usd:,.0f}")
            score += 5
        else:
            red.append(f"🔴 Низкая ликвидность пула: ${liquidity_usd:,.0f}")
            score += 15
    else:
        # Small cap: liq/mcap ratio matters a lot
        if liq_ratio >= 0.20:
            green.append(f"✅ Высокая ликвидность: {liq_ratio*100:.0f}% от mcap")
        elif liq_ratio >= 0.10:
            green.append(f"✅ Нормальная ликвидность: {liq_ratio*100:.0f}% от mcap")
        elif liq_ratio >= 0.05:
            yellow.append(f"⚠️ Низкая ликвидность: {liq_ratio*100:.0f}% от mcap")
            score += 10
        else:
            red.append(f"🔴 Критически низкая ликвидность: {liq_ratio*100:.1f}% от mcap")
            score += 25

    # ── FDV / MCap ratio ─────────────────────────────────────────
    fdv_ratio = fdv / max(mcap, 1) if fdv > 0 else 1.0
    if fdv_ratio <= 1.1:
        green.append("✅ FDV ≈ MCap — токен почти полностью в обращении")
    elif fdv_ratio <= 3.0:
        yellow.append(f"⚠️ FDV в {fdv_ratio:.1f}x выше MCap — ожидается доп. эмиссия")
        score += 10
    elif fdv_ratio <= 10:
        red.append(f"🔴 FDV в {fdv_ratio:.1f}x выше MCap — большой навес продаж")
        score += 20
    else:
        red.append(f"🔴 FDV в {fdv_ratio:.0f}x выше MCap — огромный будущий supply")
        score += 30

    # ── Age ───────────────────────────────────────────────────────
    if age_days >= 30:
        green.append(f"✅ Возраст {age_days:.0f}д — пережил первичный хайп")
    elif age_days >= 7:
        yellow.append(f"⚠️ Возраст {age_days:.0f}д — ещё молодой")
        score += 5
    else:
        red.append(f"🔴 Возраст {age_days:.0f}д — очень молодой токен")
        score += 15

    # ── Holder count ──────────────────────────────────────────────
    if holders >= 1000:
        green.append(f"✅ Холдеры: {holders:,} — широкое распределение")
    elif holders >= 300:
        yellow.append(f"⚠️ Холдеры: {holders:,} — небольшое комьюнити")
        score += 5
    elif holders > 0:
        red.append(f"🔴 Холдеры: {holders:,} — слишком мало")
        score += 15

    # ── Volume / MCap ─────────────────────────────────────────────
    vol_ratio = volume_24h / max(mcap, 1)
    if mcap >= 50_000_000:
        # Large caps: volume only in one DEX pool, so absolute volume matters
        if volume_24h >= 100_000:
            green.append(f"✅ Объём: ${volume_24h:,.0f}/24h (один из многих пулов)")
        elif volume_24h >= 10_000:
            yellow.append(f"⚠️ Объём: ${volume_24h:,.0f}/24h")
        else:
            yellow.append(f"⚠️ Низкая активность на этом пуле: ${volume_24h:,.0f}/24h")
    else:
        if vol_ratio >= 0.10:
            green.append(f"✅ Активная торговля: объём {vol_ratio*100:.0f}% от mcap за 24h")
        elif vol_ratio >= 0.02:
            yellow.append(f"⚠️ Умеренная торговля: объём {vol_ratio*100:.1f}% от mcap")
        else:
            red.append(f"🔴 Низкая ликвидность торговли: объём {vol_ratio*100:.1f}% от mcap")
            score += 10

    # ── Buy/sell ratio ────────────────────────────────────────────
    buys  = txns.get("buys",  0)
    sells = txns.get("sells", 0)
    total_txns = buys + sells
    if total_txns > 0:
        buy_ratio = buys / total_txns
        if buy_ratio >= 0.60:
            green.append(f"✅ Перевес байеров: {buys} покупок vs {sells} продаж")
        elif buy_ratio >= 0.45:
            yellow.append(f"⚠️ Баланс: {buys} покупок / {sells} продаж")
        else:
            red.append(f"🔴 Доминируют продажи: {sells} vs {buys} покупок")
            score += 15

    # ── Price action ──────────────────────────────────────────────
    if price_change_24h >= 5:
        green.append(f"✅ Цена растёт: +{price_change_24h:.1f}% за 24h")
    elif price_change_24h >= -5:
        yellow.append(f"⚠️ Цена стабильна: {price_change_24h:+.1f}% за 24h")
    elif price_change_24h >= -20:
        red.append(f"🔴 Цена падает: {price_change_24h:.1f}% за 24h")
        score += 10
    else:
        red.append(f"🔴 Сильное падение: {price_change_24h:.1f}% за 24h")
        score += 20

    # ── Socials ───────────────────────────────────────────────────
    has_website  = bool(socials.get("website"))
    has_twitter  = bool(socials.get("twitter"))
    has_telegram = bool(socials.get("telegram"))

    social_count = sum([has_website, has_twitter, has_telegram])
    if social_count >= 2:
        green.append(f"✅ Онлайн-присутствие: {social_count}/3 соцсетей")
    elif social_count == 1:
        yellow.append("⚠️ Минимальное онлайн-присутствие")
        score += 5
    else:
        red.append("🔴 Нет социальных сетей — анонимный проект")
        score += 20

    # ── Extreme pump flag ─────────────────────────────────────────
    if price_change_1h >= 50:
        red.append(f"🚨 Памп: +{price_change_1h:.0f}% за 1h — возможна манипуляция")
        score += 15

    # ── Liquidity stability ───────────────────────────────────────
    # Can't compute without historical data, mark as unknown
    liq_stability = "unknown"

    # ── Rugcheck signals (Solana) ────────────────────────────────
    liq_locked = False
    if rugcheck and rugcheck.available:
        if rugcheck.rugged:
            red.append("🚨 Rugcheck: токен помечен как RUGGED")
            score += 50

        if rugcheck.mint_authority_renounced:
            green.append("✅ Mint authority renounced (нельзя допечатать)")
        else:
            red.append("🔴 Mint authority НЕ renounced — можно допечатать supply")
            score += 15

        if rugcheck.freeze_authority_renounced:
            green.append("✅ Freeze authority renounced (нельзя заморозить)")
        else:
            red.append("🔴 Freeze authority активен — кошельки можно заморозить")
            score += 20

        if rugcheck.lp_locked_pct >= 0.90:
            green.append(f"✅ LP заблокирован: {rugcheck.lp_locked_pct*100:.0f}%")
            liq_locked = True
        elif rugcheck.lp_locked_pct >= 0.50:
            yellow.append(f"⚠️ LP частично заблокирован: {rugcheck.lp_locked_pct*100:.0f}%")
            score += 5
        elif rugcheck.lp_locked_pct > 0:
            red.append(f"🔴 LP почти не заблокирован: {rugcheck.lp_locked_pct*100:.0f}%")
            score += 15
        else:
            yellow.append("⚠️ LP lock не подтверждён")
            score += 5

        if rugcheck.top_holder_pct >= 0.20:
            red.append(f"🔴 Концентрация: топ-1 холдер держит {rugcheck.top_holder_pct*100:.1f}%")
            score += 20
        elif rugcheck.top_holder_pct >= 0.10:
            yellow.append(f"⚠️ Концентрация: топ-1 {rugcheck.top_holder_pct*100:.1f}%")
            score += 8

        if rugcheck.top10_holder_pct >= 0.50:
            red.append(f"🔴 Топ-10 держат {rugcheck.top10_holder_pct*100:.0f}% supply")
            score += 15
        elif rugcheck.top10_holder_pct >= 0.35:
            yellow.append(f"⚠️ Топ-10 держат {rugcheck.top10_holder_pct*100:.0f}%")
            score += 5

        if rugcheck.risk_level == "danger":
            score += 10
        elif rugcheck.risk_level == "warning":
            score += 5

    # ── Overall risk ──────────────────────────────────────────────
    if score <= 10:   overall = "low"
    elif score <= 25: overall = "medium"
    elif score <= 45: overall = "high"
    else:             overall = "critical"

    # ── Verdict ───────────────────────────────────────────────────
    if overall == "low":
        verdict = "Риски умеренные — ничего критичного не обнаружено"
    elif overall == "medium":
        verdict = "Есть некоторые предупреждения — торгуй с осторожностью"
    elif overall == "high":
        verdict = "Высокие риски — несколько красных флагов одновременно"
    else:
        verdict = "КРИТИЧЕСКИЕ РИСКИ — вероятна ловушка или скам"

    return RiskReport(
        overall_risk=overall,
        risk_score=min(score, 100),
        green_flags=green,
        red_flags=red,
        yellow_flags=yellow,
        liq_mcap_ratio=liq_ratio,
        liq_locked=liq_locked,
        liq_stability=liq_stability,
        fully_diluted_ratio=fdv_ratio,
        price_change_1h=price_change_1h,
        price_change_24h=price_change_24h,
        txns_buy_24h=buys,
        txns_sell_24h=sells,
        volume_24h=volume_24h,
        has_website=has_website,
        has_twitter=has_twitter,
        has_telegram=has_telegram,
        rugcheck=rugcheck,
        verdict=verdict,
    )
