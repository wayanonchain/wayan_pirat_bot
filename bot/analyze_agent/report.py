"""
Report formatter — renders FullAnalysis into a Telegram-ready HTML message.
"""
from models import SignalTier
from token_analyzer import FullAnalysis
from smart_money import SmartMoneyReport
from risk_analyzer import RiskReport
from narrative import NarrativeResult

VERDICT_EMOJI = {
    "BUY":   "🟢",
    "WATCH": "👀",
    "RISKY": "⚠️",
    "AVOID": "🔴",
}

VERDICT_RU = {
    "BUY":   "ИНТЕРЕСНО К ПОКУПКЕ",
    "WATCH": "СЛЕДИТЬ",
    "RISKY": "РИСКОВАННО",
    "AVOID": "НЕ БРАТЬ",
}

RISK_EMOJI = {
    "low":      "🟢",
    "medium":   "🟡",
    "high":     "🔴",
    "critical": "🚨",
}

SM_EMOJI = {
    "high":   "🔥",
    "medium": "👀",
    "low":    "🟡",
    "none":   "⚪",
}


def format_full_report(analysis: FullAnalysis) -> str:
    t  = analysis.token
    n  = analysis.narrative
    sm = analysis.smart_money
    r  = analysis.risk

    v_emoji = VERDICT_EMOJI[analysis.buy_verdict]
    v_label = VERDICT_RU[analysis.buy_verdict]

    lines = []

    # ── Header ────────────────────────────────────────────────────
    lines += [
        f"{v_emoji} <b>${t.symbol} — {v_label}</b>",
        f"Скор: <b>{analysis.buy_score}/100</b> | Риск: {RISK_EMOJI[r.overall_risk]} {r.overall_risk.upper()}",
        "",
    ]

    # ── О токене ─────────────────────────────────────────────────
    lines += ["📋 <b>О токене</b>"]
    lines.append(f"<b>{t.name}</b> ({t.symbol}) · {t.chain.capitalize()}")
    lines.append(f"Адрес: <code>{t.address}</code>")

    if t.description:
        desc = t.description.strip()[:280]
        if len(t.description) > 280:
            desc += "..."
        lines.append(f"📝 {desc}")

    lines.append("")

    # ── Рыночные данные ───────────────────────────────────────────
    lines += ["💰 <b>Рынок</b>"]
    lines.append(f"Цена:    ${t.current_price:.6g}")
    lines.append(f"MCap:    <b>${t.mcap:,.0f}</b>")
    if t.fdv > t.mcap * 1.05:
        lines.append(f"FDV:     ${t.fdv:,.0f}  ({r.fully_diluted_ratio:.1f}x mcap ⚠️)")
    lines.append(f"Ликв:    ${t.liquidity_usd:,.0f}  ({r.liq_mcap_ratio*100:.1f}% от mcap)")
    lines.append(f"Объём:   ${t.volume_24h:,.0f} (24h)")
    lines.append(f"Возраст: {t.age_days:.0f}д")
    if t.holders:
        lines.append(f"Холдеры: {t.holders:,}")

    # Price changes
    def fmt_chg(v: float) -> str:
        return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

    lines.append(
        f"Изменение: {fmt_chg(t.price_change_1h)} (1h) · "
        f"{fmt_chg(t.price_change_6h)} (6h) · "
        f"{fmt_chg(t.price_change_24h)} (24h)"
    )
    lines.append(f"Транзакции: {t.txns_24h_buy}↑ / {t.txns_24h_sell}↓ за 24h")
    lines.append("")

    # ── Нарратив ─────────────────────────────────────────────────
    lines += [f"🎯 <b>Нарратив: {n.name}</b>  [{n.confidence.upper()}]"]
    lines.append(n.description)
    lines.append(f"<i>Почему важно:</i> {n.why_matters}")
    if n.competitors:
        top_comp = ", ".join(n.competitors[:6])
        lines.append(f"Конкуренты: {top_comp}")
    lines.append("")

    # ── Curated Smart Money DB (наши кошельки из WAYNE_PIRATE) ──
    sm_db = getattr(analysis, "sm_db", None)
    if sm_db and sm_db.available and sm_db.unique_wallets > 0:
        lines += ["🏦 <b>Smart Money (наши кошельки)</b>"]
        lines.append(
            f"{sm_db.unique_wallets} кошельков купили за 24h на "
            f"<b>${sm_db.total_buy_usd:,.0f}</b>"
        )
        if sm_db.avg_buy_usd > 0:
            lines.append(f"Средняя покупка: ${sm_db.avg_buy_usd:,.0f}")
        for w in sm_db.top_wallets[:3]:
            label = f" · {w.nansen_label}" if w.nansen_label else ""
            lines.append(
                f"• <code>{w.wallet[:8]}…{w.wallet[-4:]}</code> "
                f"— ${w.amount_usd:,.0f}{label}"
            )
        lines.append("")

    # ── Умные деньги (публичные trades) ─────────────────────────
    sm_icon = SM_EMOJI[sm.confidence]
    lines += [f"🧠 <b>Умные деньги (публичные trades)</b>  {sm_icon} {sm.confidence.upper()}"]
    lines.append(f"Байсайд: {sm.buy_sell_ratio*100:.0f}% объёма")
    if sm.large_buy_count > 0:
        lines.append(f"Крупных покупок (>$5k): {sm.large_buy_count}")
    if sm.avg_buy_size > 0:
        lines.append(f"Средняя покупка: ${sm.avg_buy_size:,.0f}")
    if sm.total_smart_buy_usd > 0:
        lines.append(f"Объём крупных байеров: ${sm.total_smart_buy_usd:,.0f}")

    for s in sm.signal_lines:
        lines.append(s)
    for w in sm.warning_lines:
        lines.append(w)
    lines.append("")

    # ── Риски ────────────────────────────────────────────────────
    lines += [f"🛡 <b>Риски</b>  {RISK_EMOJI[r.overall_risk]} {r.overall_risk.upper()}"]
    for g in r.green_flags:
        lines.append(g)
    for y in r.yellow_flags:
        lines.append(y)
    for rd in r.red_flags:
        lines.append(rd)

    # Rugcheck summary (Solana only)
    if r.rugcheck and r.rugcheck.available:
        rc = r.rugcheck
        lines.append(
            f"RugCheck: score {rc.score} [{rc.risk_level.upper()}]"
            + (" · 🚨 RUGGED" if rc.rugged else "")
        )
        if rc.risks:
            lines.append("• " + " · ".join(rc.risks[:4]))

    # Socials
    social_icons = []
    if r.has_website:   social_icons.append("🌐 Сайт")
    if r.has_twitter:   social_icons.append("🐦 Twitter")
    if r.has_telegram:  social_icons.append("💬 Telegram")
    if social_icons:
        lines.append("Соцсети: " + " · ".join(social_icons))
    lines.append("")

    # ── Итоговый вердикт ─────────────────────────────────────────
    lines += [f"📊 <b>Итог: {v_emoji} {v_label}</b>"]
    for s in analysis.summary_lines:
        lines.append(s)

    # Add verdict guidance
    if analysis.buy_verdict == "BUY":
        lines.append("💡 Интересен для позиции. Проверь соц. активность и установи стоп.")
    elif analysis.buy_verdict == "WATCH":
        lines.append("💡 Пока в вотч-листе. Жди подтверждения объёмом или новостью.")
    elif analysis.buy_verdict == "RISKY":
        lines.append("⚠️ Если входить — только с маленькой позицией и чётким стопом.")
    else:
        lines.append("🚫 Слишком много красных флагов. Пропускаем.")

    lines.append("")

    # ── Ссылки ────────────────────────────────────────────────────
    link_parts = []
    if t.dexscreener_url:
        link_parts.append(f'<a href="{t.dexscreener_url}">DexScreener</a>')
    if t.birdeye_url:
        link_parts.append(f'<a href="{t.birdeye_url}">Birdeye</a>')
    # Rugcheck for Solana
    if t.chain == "solana":
        link_parts.append(f'<a href="https://rugcheck.xyz/tokens/{t.address}">RugCheck</a>')
    if t.socials.get("twitter"):
        link_parts.append(f'<a href="{t.socials["twitter"]}">Twitter</a>')
    if t.socials.get("website"):
        link_parts.append(f'<a href="{t.socials["website"]}">Website</a>')

    if link_parts:
        lines.append("🔗 " + " · ".join(link_parts))

    return "\n".join(lines)
