"""
Comprehensive admin analytics for the bot.
Commands: /analytics, /analytics_courses, /analytics_payments, /analytics_growth
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func, case, and_

from db.models import Subscriber, Payment, CourseAccess, Signal
from db.repository import async_session

logger = logging.getLogger(__name__)


async def get_overview_analytics() -> str:
    """Main overview: users, tiers, courses, revenue snapshot."""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        # --- Users ---
        total = (await session.execute(
            select(func.count()).select_from(Subscriber)
        )).scalar() or 0

        active_premium = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier == "premium", Subscriber.expires_at > now)
        )).scalar() or 0

        active_pp = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier == "premium_plus", Subscriber.expires_at > now)
        )).scalar() or 0

        expired_premium = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier != "free", Subscriber.expires_at <= now)
        )).scalar() or 0

        free_users = total - active_premium - active_pp - expired_premium

        # New users
        new_today = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= today)
        )).scalar() or 0

        new_week = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= week_ago)
        )).scalar() or 0

        new_month = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= month_ago)
        )).scalar() or 0

        # --- Courses ---
        onchain_buyers = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.course_purchased == True)
        )).scalar() or 0

        meteora_buyers = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.meteora_purchased == True)
        )).scalar() or 0

        both_courses = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.course_purchased == True, Subscriber.meteora_purchased == True)
        )).scalar() or 0

        # Free course access (test=True in course_access)
        free_course_access = (await session.execute(
            select(func.count(func.distinct(CourseAccess.user_id)))
            .where(CourseAccess.is_test == True)
        )).scalar() or 0

        paid_course_access = (await session.execute(
            select(func.count(func.distinct(CourseAccess.user_id)))
            .where(CourseAccess.is_test == False)
        )).scalar() or 0

        users_with_promo = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.promo_code != None)
        )).scalar() or 0

        # --- Payments total ---
        total_payments = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.verified == True)
        )).scalar() or 0

        total_revenue = (await session.execute(
            select(func.sum(Payment.amount_sol))
            .where(Payment.verified == True)
        )).scalar() or 0

    lines = [
        "📊 <b>АНАЛИТИКА — Обзор</b>",
        "━" * 28,
        "",
        "👥 <b>Пользователи:</b>",
        f"  Всего: <b>{total}</b>",
        f"  Free: {free_users}",
        f"  Premium: <b>{active_premium}</b>",
        f"  Premium+: <b>{active_pp}</b>",
        f"  Подписка истекла: {expired_premium}",
        "",
        f"  Новых сегодня: <b>{new_today}</b>",
        f"  За неделю: {new_week}",
        f"  За месяц: {new_month}",
        "",
        "📚 <b>Курсы:</b>",
        f"  Onchain купили: <b>{onchain_buyers}</b>",
        f"  Meteora купили: <b>{meteora_buyers}</b>",
        f"  Оба курса: {both_courses}",
        f"  Бесплатный доступ (free): {free_course_access}",
        f"  Платный доступ (paid): {paid_course_access}",
        "",
        "🔗 <b>Маркетинг:</b>",
        f"  Активировали промокод: {users_with_promo}",
        "",
        "💰 <b>Платежи:</b>",
        f"  Всего: {total_payments} на <b>{total_revenue:.3f} SOL</b>",
        "",
        "━" * 28,
        "<i>Детали: /ac /ap /ag</i>",
    ]
    return "\n".join(lines)


async def get_course_analytics() -> str:
    """Detailed course analytics."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        # Onchain course
        onchain_total = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.course_purchased == True)
        )).scalar() or 0

        onchain_week = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "course", Payment.verified == True,
                   Payment.created_at >= week_ago)
        )).scalar() or 0

        onchain_month = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "course", Payment.verified == True,
                   Payment.created_at >= month_ago)
        )).scalar() or 0

        # Meteora course
        meteora_total = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.meteora_purchased == True)
        )).scalar() or 0

        meteora_week = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "meteora", Payment.verified == True,
                   Payment.created_at >= week_ago)
        )).scalar() or 0

        meteora_month = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "meteora", Payment.verified == True,
                   Payment.created_at >= month_ago)
        )).scalar() or 0

        # Community
        community_total = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "community", Payment.verified == True)
        )).scalar() or 0

        community_week = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.tier == "community", Payment.verified == True,
                   Payment.created_at >= week_ago)
        )).scalar() or 0

        # Free course accesses
        free_onchain = (await session.execute(
            select(func.count(func.distinct(CourseAccess.user_id)))
            .where(CourseAccess.is_test == True)
        )).scalar() or 0

        # Conversion: free → paid
        # Users who got free access AND then bought
        free_then_paid = (await session.execute(
            select(func.count(func.distinct(CourseAccess.user_id)))
            .where(CourseAccess.is_test == True)
            .where(CourseAccess.user_id.in_(
                select(Subscriber.user_id).where(Subscriber.course_purchased == True)
            ))
        )).scalar() or 0

        conversion_pct = (free_then_paid / free_onchain * 100) if free_onchain > 0 else 0

        # Recent course buyers (last 10)
        recent_buyers = (await session.execute(
            select(Payment.user_id, Payment.tier, Payment.amount_sol, Payment.created_at)
            .where(Payment.verified == True, Payment.tier.in_(["course", "meteora", "community"]))
            .order_by(Payment.created_at.desc())
            .limit(10)
        )).all()

    lines = [
        "📚 <b>АНАЛИТИКА — Курсы</b>",
        "━" * 28,
        "",
        "🔶 <b>Onchain Trading (400 USDT):</b>",
        f"  Всего купили: <b>{onchain_total}</b>",
        f"  За неделю: {onchain_week}",
        f"  За месяц: {onchain_month}",
        "",
        "🔵 <b>Meteora (100 USDT):</b>",
        f"  Всего купили: <b>{meteora_total}</b>",
        f"  За неделю: {meteora_week}",
        f"  За месяц: {meteora_month}",
        "",
        "🟢 <b>Community (200 USDT/мес):</b>",
        f"  Всего оплат: <b>{community_total}</b>",
        f"  За неделю: {community_week}",
        "",
        "📈 <b>Конверсия (free → paid):</b>",
        f"  Бесплатный доступ: {free_onchain}",
        f"  Из них купили: {free_then_paid}",
        f"  Конверсия: <b>{conversion_pct:.1f}%</b>",
    ]

    if recent_buyers:
        lines.extend(["", "🕐 <b>Последние покупки:</b>"])
        tier_labels = {"course": "Onchain", "meteora": "Meteora", "community": "Community"}
        for uid, tier, amount, created in recent_buyers:
            label = tier_labels.get(tier, tier)
            date_str = created.strftime("%d.%m %H:%M")
            lines.append(f"  • {label} — user {uid} — {date_str}")

    return "\n".join(lines)


async def get_payment_analytics() -> str:
    """Detailed payment analytics by tier, period."""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        # By tier
        tier_stats = (await session.execute(
            select(
                Payment.tier,
                func.count(),
                func.sum(Payment.amount_sol),
            )
            .where(Payment.verified == True)
            .group_by(Payment.tier)
        )).all()

        # Today
        today_stats = (await session.execute(
            select(
                Payment.tier,
                func.count(),
                func.sum(Payment.amount_sol),
            )
            .where(Payment.verified == True, Payment.created_at >= today)
            .group_by(Payment.tier)
        )).all()

        # This week
        week_stats = (await session.execute(
            select(
                Payment.tier,
                func.count(),
                func.sum(Payment.amount_sol),
            )
            .where(Payment.verified == True, Payment.created_at >= week_ago)
            .group_by(Payment.tier)
        )).all()

        # This month
        month_stats = (await session.execute(
            select(
                Payment.tier,
                func.count(),
                func.sum(Payment.amount_sol),
            )
            .where(Payment.verified == True, Payment.created_at >= month_ago)
            .group_by(Payment.tier)
        )).all()

        # Unique paying users
        unique_payers = (await session.execute(
            select(func.count(func.distinct(Payment.user_id)))
            .where(Payment.verified == True)
        )).scalar() or 0

        # Repeat buyers (2+ payments)
        repeat_buyers = (await session.execute(
            select(func.count()).select_from(
                select(Payment.user_id)
                .where(Payment.verified == True)
                .group_by(Payment.user_id)
                .having(func.count() >= 2)
                .subquery()
            )
        )).scalar() or 0

    tier_labels = {
        "premium": "💎 Premium",
        "premium_plus": "💎 Premium+",
        "course": "📘 Onchain",
        "meteora": "🌊 Meteora",
        "community": "👥 Community",
    }

    def format_tier_line(stats_list):
        result = []
        for tier, cnt, total_sol in stats_list:
            label = tier_labels.get(tier, tier)
            sol = total_sol or 0
            result.append(f"  {label}: {cnt} платежей, {sol:.3f} SOL")
        return result

    lines = [
        "💰 <b>АНАЛИТИКА — Платежи</b>",
        "━" * 28,
        "",
        "📊 <b>Всего (all-time):</b>",
    ]
    lines.extend(format_tier_line(tier_stats))

    lines.extend(["", "📅 <b>Сегодня:</b>"])
    if today_stats:
        lines.extend(format_tier_line(today_stats))
    else:
        lines.append("  Нет платежей")

    lines.extend(["", "📅 <b>За неделю:</b>"])
    if week_stats:
        lines.extend(format_tier_line(week_stats))
    else:
        lines.append("  Нет платежей")

    lines.extend(["", "📅 <b>За месяц:</b>"])
    if month_stats:
        lines.extend(format_tier_line(month_stats))
    else:
        lines.append("  Нет платежей")

    lines.extend([
        "",
        "👤 <b>Покупатели:</b>",
        f"  Уникальных: <b>{unique_payers}</b>",
        f"  Повторных (2+ покупки): <b>{repeat_buyers}</b>",
    ])

    return "\n".join(lines)


async def get_growth_analytics() -> str:
    """User growth over time."""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as session:
        # Daily signups for last 14 days
        daily_data = []
        for i in range(13, -1, -1):
            day_start = today - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            cnt = (await session.execute(
                select(func.count()).select_from(Subscriber)
                .where(Subscriber.created_at >= day_start, Subscriber.created_at < day_end)
            )).scalar() or 0
            daily_data.append((day_start, cnt))

        # Weekly totals for last 4 weeks
        weekly_data = []
        for i in range(3, -1, -1):
            w_start = today - timedelta(weeks=i+1)
            w_end = today - timedelta(weeks=i)
            cnt = (await session.execute(
                select(func.count()).select_from(Subscriber)
                .where(Subscriber.created_at >= w_start, Subscriber.created_at < w_end)
            )).scalar() or 0
            weekly_data.append((w_start, w_end, cnt))

        # Cumulative milestones
        total_now = (await session.execute(
            select(func.count()).select_from(Subscriber)
        )).scalar() or 0

        # Expiring premium subs
        expiring_3d = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier != "free",
                   Subscriber.expires_at > now,
                   Subscriber.expires_at < now + timedelta(days=3))
        )).scalar() or 0

        expiring_7d = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier != "free",
                   Subscriber.expires_at > now,
                   Subscriber.expires_at < now + timedelta(days=7))
        )).scalar() or 0

    lines = [
        "📈 <b>АНАЛИТИКА — Рост</b>",
        "━" * 28,
        "",
        f"👥 Всего пользователей: <b>{total_now}</b>",
        "",
        "📅 <b>Регистрации по дням (14д):</b>",
    ]

    # Bar chart with simple text
    max_cnt = max((d[1] for d in daily_data), default=1) or 1
    for day, cnt in daily_data:
        bar_len = int(cnt / max_cnt * 10) if max_cnt > 0 else 0
        bar = "▓" * bar_len + "░" * (10 - bar_len)
        day_str = day.strftime("%d.%m")
        lines.append(f"  {day_str} {bar} <b>{cnt}</b>")

    lines.extend(["", "📊 <b>По неделям:</b>"])
    for w_start, w_end, cnt in weekly_data:
        lines.append(f"  {w_start.strftime('%d.%m')}–{w_end.strftime('%d.%m')}: <b>{cnt}</b>")

    lines.extend([
        "",
        "⏳ <b>Истекающие подписки:</b>",
        f"  За 3 дня: {expiring_3d}",
        f"  За 7 дней: {expiring_7d}",
    ])

    return "\n".join(lines)
