"""
Telegram alert formatter and sender.
Uses httpx directly (no python-telegram-bot dependency needed).
"""
import logging
import httpx
from models import AccumulationAnalysis, SignalTier, SpringStatus

log = logging.getLogger(__name__)


TIER_EMOJI = {
    SignalTier.STRONG:    "🔥",
    SignalTier.SIGNAL:    "🟢",
    SignalTier.WATCHLIST: "👀",
    SignalTier.NOISE:     "⚫",
}

TIER_LABEL = {
    SignalTier.STRONG:    "СИЛЬНЫЙ СИГНАЛ",
    SignalTier.SIGNAL:    "СИГНАЛ",
    SignalTier.WATCHLIST: "WATCHLIST",
    SignalTier.NOISE:     "ШУМ",
}


def format_alert(analysis: AccumulationAnalysis, mode: str = "BALANCED") -> str:
    t    = analysis.token
    tier = analysis.tier
    emoji = TIER_EMOJI[tier]
    label = TIER_LABEL[tier]

    drawdown_pct = analysis.drawdown_from_ath * 100
    ath_mcap_m   = t.ath_mcap / 1_000_000

    lines = [
        f"{emoji} {label} · {mode}",
        f"",
        f"Токен:   <b>${t.symbol}</b>",
        f"Адрес:   <code>{t.address}</code>",
        f"Сеть:    {t.chain.capitalize()}",
        f"",
        f"MC:      <b>${t.current_mcap:,.0f}</b>",
        f"ATH MC:  ${t.ath_mcap:,.0f}  (−{drawdown_pct:.0f}%)",
        f"Ликв:    ${t.liquidity_usd:,.0f}",
        f"Возраст: {t.age_days:.0f} дней",
    ]
    if t.holders:
        lines.append(f"Холдеры: {t.holders:,}")

    lines.append("")
    lines.append("📊 <b>Паттерн аккумуляции:</b>")

    # Consolidation
    c = analysis.consolidation
    if c:
        lines.append(f"• Боковик:  {c.duration_days:.0f}д, диапазон {c.range_pct*100:.0f}%")
        if c.volume_at_drop > 0:
            ratio = c.avg_volume_usd / c.volume_at_drop
            lines.append(f"• Объём высох: {ratio*100:.0f}% от периода падения")
    else:
        lines.append("• Боковик:  не обнаружен ⚠️")

    # No new low
    lines.append(f"• Нет нового лоя: {analysis.no_new_low_days}д")

    # Spring
    sp = analysis.spring
    if sp.status == SpringStatus.CONFIRMED:
        lines.append(f"• Spring:   ✅ подтверждён ({sp.breach_pct*100:.0f}% пробой, возврат {sp.recovery_hours:.0f}h, {sp.days_ago:.0f}д назад)")
    elif sp.status == SpringStatus.DETECTED:
        lines.append(f"• Spring:   ⏳ обнаружен (пробой {sp.breach_pct*100:.0f}%, ещё не вернулся)")
    else:
        lines.append("• Spring:   не обнаружен")

    # Volume spike
    lines.append(f"• Объём:    x{analysis.volume_spike_ratio:.1f} от среднего")

    # Smart-money DB activity (curated wallets)
    if analysis.sm_wallets_24h > 0:
        lines.append("")
        lines.append("🧠 <b>Smart Money (наши кошельки):</b>")
        lines.append(
            f"• {analysis.sm_wallets_24h} кошельков купили за 24h "
            f"на ${analysis.sm_total_buy_usd:,.0f}"
        )
        for l in analysis.sm_reason_lines[:3]:
            lines.append(f"• {l}")

    # Score
    lines.append("")
    lines.append(f"Скор:  <b>{analysis.score:.0f} / 100</b>  [{label}]")

    # Take profit table
    if analysis.take_profit_levels:
        lines.append("")
        lines.append("🎯 <b>Цели выхода (MarketCap):</b>")
        labels_tp = ["ТП1 (x2)", "ТП2 (x4)", "ТП3 (ATH)", "ТП4 (x2 ATH)"]
        percents  = [20, 25, 25, 20]
        for lbl, tp, pct in zip(labels_tp, analysis.take_profit_levels, percents):
            lines.append(f"• {lbl}: ${tp:,.0f}  → продать {pct}%")
        lines.append("• Остаток 10% — лотерейный билет")

    # What to watch for exit (sell signals)
    lines.append("")
    lines.append("⚠️ <b>Выходить если:</b>")
    lines.append("• Объём растёт, цена стоит (ММ выгружает)")
    lines.append("• Хайп в соц.сетях + KOL посты")
    lines.append("• On-chain: крупняк выводит на CEX")
    lines.append("• Пробой ниже зоны Spring → стоп 100%")

    # Links
    lines.append("")
    links = []
    if t.dexscreener_url:
        links.append(f'<a href="{t.dexscreener_url}">DexScreener</a>')
    if t.birdeye_url:
        links.append(f'<a href="{t.birdeye_url}">Birdeye</a>')
    if links:
        lines.append("🔗 " + " · ".join(links))

    return "\n".join(lines)


def format_watchlist_alert(analysis: AccumulationAnalysis, mode: str = "BALANCED") -> str:
    t = analysis.token
    lines = [
        f"👀 WATCHLIST · {mode}",
        f"",
        f"<b>${t.symbol}</b> | MC ${t.current_mcap:,.0f} | Скор {analysis.score:.0f}/100",
        f"Просадка от ATH: −{analysis.drawdown_from_ath*100:.0f}%",
    ]
    if analysis.consolidation:
        c = analysis.consolidation
        lines.append(f"Боковик {c.duration_days:.0f}д ({c.range_pct*100:.0f}% диапазон)")
    sp = analysis.spring
    if sp.status != SpringStatus.NONE:
        lines.append(f"Spring: {sp.status.value}")
    lines.append(f"Объём: x{analysis.volume_spike_ratio:.1f}")
    lines.append(f"<code>{t.address}</code>")
    if t.dexscreener_url:
        lines.append(f'<a href="{t.dexscreener_url}">DexScreener</a>')
    return "\n".join(lines)


class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id   = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(self, text: str, disable_web_page_preview: bool = True) -> bool:
        if not self.bot_token or not self.chat_id:
            log.warning("Telegram not configured — printing to stdout:\n%s", text)
            print("\n" + "=" * 60)
            print(text)
            print("=" * 60 + "\n")
            return True

        payload = {
            "chat_id":                  self.chat_id,
            "text":                     text,
            "parse_mode":               "HTML",
            "disable_web_page_preview": disable_web_page_preview,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(self._url, json=payload)
                r.raise_for_status()
                return True
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            return False

    async def send_analysis(
        self,
        analysis: AccumulationAnalysis,
        mode: str = "BALANCED",
    ) -> bool:
        tier = analysis.tier
        if tier in (SignalTier.SIGNAL, SignalTier.STRONG):
            text = format_alert(analysis, mode)
        elif tier == SignalTier.WATCHLIST:
            text = format_watchlist_alert(analysis, mode)
        else:
            return False  # don't send NOISE

        return await self.send(text)
