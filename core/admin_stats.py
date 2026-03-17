"""
Admin Statistics — daily report sent to admin chat.
Tracks: new users, subscribers, payments, signals, bot activity.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from db.models import Subscriber, Payment, Signal, TokenBuy, Wallet
from db.repository import async_session

logger = logging.getLogger(__name__)


async def get_admin_stats() -> dict:
    """Collect all stats for admin report."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    async with async_session() as session:
        # --- Subscribers ---
        total_users = (await session.execute(
            select(func.count()).select_from(Subscriber)
        )).scalar() or 0

        new_users_today = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= today_start)
        )).scalar() or 0

        new_users_yesterday = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= yesterday_start)
            .where(Subscriber.created_at < today_start)
        )).scalar() or 0

        new_users_week = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.created_at >= week_ago)
        )).scalar() or 0

        # Active premium subscribers
        active_premium = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier == "premium")
            .where(Subscriber.expires_at > now)
        )).scalar() or 0

        active_premium_plus = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier == "premium_plus")
            .where(Subscriber.expires_at > now)
        )).scalar() or 0

        free_users = total_users - active_premium - active_premium_plus

        # Expiring soon (within 3 days)
        expiring_soon = (await session.execute(
            select(func.count()).select_from(Subscriber)
            .where(Subscriber.tier != "free")
            .where(Subscriber.expires_at > now)
            .where(Subscriber.expires_at < now + timedelta(days=3))
        )).scalar() or 0

        # --- Payments ---
        total_payments = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.verified == True)
        )).scalar() or 0

        payments_today = (await session.execute(
            select(func.count()).select_from(Payment)
            .where(Payment.created_at >= today_start)
            .where(Payment.verified == True)
        )).scalar() or 0

        revenue_total = (await session.execute(
            select(func.sum(Payment.amount_sol)).where(Payment.verified == True)
        )).scalar() or 0

        revenue_today = (await session.execute(
            select(func.sum(Payment.amount_sol))
            .where(Payment.created_at >= today_start)
            .where(Payment.verified == True)
        )).scalar() or 0

        revenue_week = (await session.execute(
            select(func.sum(Payment.amount_sol))
            .where(Payment.created_at >= week_ago)
            .where(Payment.verified == True)
        )).scalar() or 0

        # Recent payments detail
        recent_payments_result = await session.execute(
            select(Payment)
            .where(Payment.created_at >= yesterday_start)
            .where(Payment.verified == True)
            .order_by(Payment.created_at.desc())
            .limit(10)
        )
        recent_payments = list(recent_payments_result.scalars().all())

        # --- Signals ---
        signals_today = (await session.execute(
            select(func.count()).select_from(Signal)
            .where(Signal.created_at >= today_start)
        )).scalar() or 0

        signals_yesterday = (await session.execute(
            select(func.count()).select_from(Signal)
            .where(Signal.created_at >= yesterday_start)
            .where(Signal.created_at < today_start)
        )).scalar() or 0

        signals_week = (await session.execute(
            select(func.count()).select_from(Signal)
            .where(Signal.created_at >= week_ago)
        )).scalar() or 0

        total_signals = (await session.execute(
            select(func.count()).select_from(Signal)
        )).scalar() or 0

        # --- Token Buys ---
        buys_today = (await session.execute(
            select(func.count()).select_from(TokenBuy)
            .where(TokenBuy.timestamp >= today_start)
        )).scalar() or 0

        buys_week = (await session.execute(
            select(func.count()).select_from(TokenBuy)
            .where(TokenBuy.timestamp >= week_ago)
        )).scalar() or 0

        total_buys = (await session.execute(
            select(func.count()).select_from(TokenBuy)
        )).scalar() or 0

        # --- Wallets ---
        active_wallets = (await session.execute(
            select(func.count()).select_from(Wallet)
            .where(Wallet.status == "ACTIVE")
        )).scalar() or 0

        # Recent new users detail
        recent_users_result = await session.execute(
            select(Subscriber)
            .where(Subscriber.created_at >= yesterday_start)
            .order_by(Subscriber.created_at.desc())
            .limit(20)
        )
        recent_users = list(recent_users_result.scalars().all())

    return {
        "timestamp": now,
        "users": {
            "total": total_users,
            "free": free_users,
            "premium": active_premium,
            "premium_plus": active_premium_plus,
            "new_today": new_users_today,
            "new_yesterday": new_users_yesterday,
            "new_week": new_users_week,
            "expiring_soon": expiring_soon,
        },
        "payments": {
            "total_count": total_payments,
            "today_count": payments_today,
            "revenue_total_sol": revenue_total,
            "revenue_today_sol": revenue_today,
            "revenue_week_sol": revenue_week,
            "recent": recent_payments,
        },
        "signals": {
            "today": signals_today,
            "yesterday": signals_yesterday,
            "week": signals_week,
            "total": total_signals,
        },
        "activity": {
            "buys_today": buys_today,
            "buys_week": buys_week,
            "total_buys": total_buys,
            "active_wallets": active_wallets,
        },
        "recent_users": recent_users,
    }


def format_admin_report(stats: dict) -> str:
    """Format admin stats into a Telegram message."""
    u = stats["users"]
    p = stats["payments"]
    s = stats["signals"]
    a = stats["activity"]
    ts = stats["timestamp"].strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "\U0001f4ca <b>Daily Admin Report</b>",
        "\u2500" * 30,
        f"<i>{ts}</i>",
        "",
        "\U0001f465 <b>Users:</b>",
        f"  Total: <b>{u['total']}</b> (Free: {u['free']} | Premium: {u['premium']} | Premium+: {u['premium_plus']})",
        f"  New today: <b>{u['new_today']}</b> | Yesterday: {u['new_yesterday']} | Week: {u['new_week']}",
    ]

    if u['expiring_soon']:
        lines.append(f"  \u26a0\ufe0f Expiring in 3 days: <b>{u['expiring_soon']}</b>")

    lines.extend([
        "",
        "\U0001f4b0 <b>Revenue:</b>",
        f"  Today: <b>{p['revenue_today_sol']:.3f} SOL</b> ({p['today_count']} payments)",
        f"  Week: <b>{p['revenue_week_sol']:.3f} SOL</b>",
        f"  All-time: <b>{p['revenue_total_sol']:.3f} SOL</b> ({p['total_count']} payments)",
    ])

    lines.extend([
        "",
        "\U0001f4e1 <b>Signals:</b>",
        f"  Today: <b>{s['today']}</b> | Yesterday: {s['yesterday']} | Week: {s['week']} | Total: {s['total']}",
    ])

    lines.extend([
        "",
        "\U0001f50d <b>Activity:</b>",
        f"  SM buys today: <b>{a['buys_today']}</b> | Week: {a['buys_week']} | Total: {a['total_buys']}",
        f"  Active wallets: <b>{a['active_wallets']}</b>",
    ])

    # Recent payments
    if p['recent']:
        lines.extend(["", "\U0001f4b3 <b>Recent payments:</b>"])
        for pay in p['recent'][:5]:
            tier_name = "Premium+" if pay.tier == "premium_plus" else "Premium"
            lines.append(
                f"  \u2022 User {pay.user_id}: {pay.amount_sol:.3f} SOL ({tier_name}) "
                f"- {pay.created_at.strftime('%m-%d %H:%M')}"
            )

    # Recent new users
    recent = stats.get("recent_users", [])
    if recent:
        lines.extend(["", "\U0001f195 <b>New users (24h):</b>"])
        for user in recent[:10]:
            name = user.first_name or user.username or str(user.user_id)
            username_str = f" @{user.username}" if user.username else ""
            lines.append(
                f"  \u2022 {name}{username_str} (ID: {user.user_id}) "
                f"- {user.created_at.strftime('%m-%d %H:%M')}"
            )

    lines.append(f"\n\U0001f3f4\u200d\u2620\ufe0f Wayne Pirate Admin")
    return "\n".join(lines)


async def send_admin_stats():
    """Collect stats and send to admin chat."""
    from bot.telegram_bot import bot
    from config.settings import TELEGRAM_CHAT_ID

    try:
        stats = await get_admin_stats()
        text = format_admin_report(stats)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("Admin stats report sent")
    except Exception as e:
        logger.error(f"Failed to send admin stats: {e}")
