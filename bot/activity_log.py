"""
Activity logger — sends structured logs to the team group chat.
Each log has a human-readable description of what happened.

Anti-spam: signal delivery logs are batched (collected per signal,
sent as one message with all recipients).
"""

import logging
import asyncio
from datetime import datetime

from config.settings import LOG_CHAT_ID

logger = logging.getLogger(__name__)

# Will be set after bot is created (avoids circular import)
_bot = None


def _get_bot():
    global _bot
    if _bot is None:
        from bot.telegram_bot import bot
        _bot = bot
    return _bot


def _user_line(user_id: int, username: str, first_name: str) -> str:
    """Format user info consistently."""
    name = first_name or username or str(user_id)
    handle = f" (@{username})" if username else ""
    return f"{name}{handle}", f"<code>{user_id}</code>"


async def _send_log(text: str):
    """Send a log message to the team chat. Silently fails."""
    if not LOG_CHAT_ID:
        return
    try:
        await _get_bot().send_message(
            chat_id=LOG_CHAT_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    except Exception as e:
        logger.warning(f"Failed to send activity log: {e}")


# ────────────────────────────────────────
#  Новый юзер
# ────────────────────────────────────────

async def log_new_user(user_id: int, username: str, first_name: str, referred_by: int = None):
    name, uid = _user_line(user_id, username, first_name)
    ref = ""
    if referred_by:
        ref = f"\n   Пришёл по реферальной ссылке от ID <code>{referred_by}</code>"
    text = (
        f"👤 <b>Новый юзер в боте</b>\n"
        f"Юзер нажал /start и впервые попал в бота.\n\n"
        f"   Кто: {name}\n"
        f"   ID: {uid}{ref}"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Реферальная ссылка активирована
# ────────────────────────────────────────

async def log_referral_activated(user_id: int, username: str, first_name: str,
                                  referrer_id: int, referrer_name: str):
    name, uid = _user_line(user_id, username, first_name)
    text = (
        f"🔗 <b>Юзер перешёл по реферальной ссылке</b>\n"
        f"Кто-то кликнул на реферальную ссылку и зашёл в бота. "
        f"Ему активирована скидка 20% на Курс.\n\n"
        f"   Новый юзер: {name} (ID: {uid})\n"
        f"   Пригласил: {referrer_name} (ID: <code>{referrer_id}</code>)\n"
        f"   Скидка: 20% на Курс (400 → 320 USDT)"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Юзер нажал кнопку покупки
# ────────────────────────────────────────

async def log_product_view(user_id: int, username: str, first_name: str,
                            product: str, discount_info: str = ""):
    name, uid = _user_line(user_id, username, first_name)
    discount_line = f"\n   Скидка: {discount_info}" if discount_info else "\n   Скидка: нет"
    text = (
        f"👁 <b>Юзер открыл страницу покупки</b>\n"
        f"Юзер нажал кнопку «Купить» и видит реквизиты для оплаты.\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Продукт: {product}{discount_line}"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Юзер отправил TX hash (заявка на оплату)
#  — логируется прямо в course.py → _process_payment_request
# ────────────────────────────────────────

# (этот лог формируется в course.py, здесь не дублируем)


# ────────────────────────────────────────
#  Оплата подтверждена (payment)
# ────────────────────────────────────────

async def log_payment(user_id: int, username: str, first_name: str,
                      tier: str, amount_sol: float, tx_sig: str,
                      referral_discount: bool = False,
                      amount_usdt: float = 0):
    name, uid = _user_line(user_id, username, first_name)
    tier_names = {
        "premium": "Premium (бот)",
        "premium_plus": "Premium+ (бот)",
        "course": "Курс Onchain Trading",
        "community": "Wayan Premium комьюнити",
    }
    tier_name = tier_names.get(tier, tier)
    discount = "\n   Применена реферальная скидка 20%" if referral_discount else ""
    if amount_usdt > 0:
        amount_str = f"{amount_usdt:.0f} USDT"
    else:
        amount_str = f"{amount_sol:.4f} SOL"
    text = (
        f"💰 <b>Оплата подтверждена</b>\n"
        f"Админ подтвердил оплату и юзер получил доступ.\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Продукт: {tier_name}\n"
        f"   Сумма: {amount_str}{discount}\n"
        f"   TX: <code>{tx_sig[:20]}...</code>"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Курс выдан (/grant_course)
# ────────────────────────────────────────

async def log_course_granted(user_id: int, username: str, first_name: str):
    name, uid = _user_line(user_id, username, first_name)
    text = (
        f"✅ <b>Курс выдан юзеру</b>\n"
        f"Админ выполнил /grant_course — юзеру отправлена ссылка на полный курс. "
        f"Теперь у него скидка 30% на комьюнити.\n\n"
        f"   Кто: {name} (ID: {uid})"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Комьюнити выдано (/grant_community)
# ────────────────────────────────────────

async def log_community_granted(user_id: int, username: str, first_name: str):
    name, uid = _user_line(user_id, username, first_name)
    text = (
        f"✅ <b>Комьюнити выдано юзеру</b>\n"
        f"Админ выполнил /grant_community — юзеру отправлена ссылка в Wayan Premium.\n\n"
        f"   Кто: {name} (ID: {uid})"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Реферальный кредит начислен
# ────────────────────────────────────────

async def log_referral_credit_earned(referrer_id: int, referrer_name: str,
                                      buyer_id: int, buyer_name: str, product: str):
    text = (
        f"🎁 <b>Реферер получил скидку за друга</b>\n"
        f"Друг реферера купил продукт — рефереру начислен кредит на скидку 20% "
        f"на его следующую покупку.\n\n"
        f"   Реферер: {referrer_name} (ID: <code>{referrer_id}</code>)\n"
        f"   Друг купил: {buyer_name} (ID: <code>{buyer_id}</code>)\n"
        f"   Продукт друга: {product}\n"
        f"   Бонус реферера: скидка 20% на следующую покупку"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Бесплатный курс (модули 1-2)
# ────────────────────────────────────────

async def log_course_access(user_id: int, username: str, first_name: str, is_test: bool):
    name, uid = _user_line(user_id, username, first_name)
    if is_test:
        desc = "Юзер нажал «Бесплатные модули» и получил ссылку на модули 1-2."
        mode = "Бесплатный (модули 1-2)"
    else:
        desc = "Юзер получил доступ к полному курсу (7 модулей)."
        mode = "Полный (оплачен)"
    text = (
        f"📚 <b>Доступ к курсу</b>\n"
        f"{desc}\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Тип: {mode}"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Комьюнити доступ (старый формат, для совместимости)
# ────────────────────────────────────────

async def log_community_access(user_id: int, username: str, first_name: str,
                                amount_usdt: float, tx_sig: str):
    name, uid = _user_line(user_id, username, first_name)
    text = (
        f"👥 <b>Доступ к комьюнити</b>\n"
        f"Юзер получил доступ в Wayan Premium.\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Сумма: {amount_usdt:.0f} USDT\n"
        f"   TX: <code>{tx_sig[:20]}...</code>"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Сигналы (batched)
# ────────────────────────────────────────

_signal_buffer: dict[int, dict] = {}
_signal_flush_tasks: dict[int, asyncio.Task] = {}
SIGNAL_BATCH_DELAY = 5


async def log_signal_sent(signal_id: int, token_symbol: str,
                          user_id: int, username: str, first_name: str,
                          tier: str):
    name = first_name or username or str(user_id)
    handle = f"@{username}" if username else str(user_id)

    if signal_id not in _signal_buffer:
        _signal_buffer[signal_id] = {
            "token": token_symbol,
            "recipients": [],
            "admin_sent": False,
        }

    tier_emoji = {"premium_plus": "🔥", "premium": "👑", "free": "🆓"}.get(tier, "")
    _signal_buffer[signal_id]["recipients"].append(f"{tier_emoji} {name} ({handle})")

    if signal_id in _signal_flush_tasks:
        _signal_flush_tasks[signal_id].cancel()

    _signal_flush_tasks[signal_id] = asyncio.create_task(
        _flush_signal_log(signal_id)
    )


async def log_signal_admin(signal_id: int, token_symbol: str):
    if signal_id not in _signal_buffer:
        _signal_buffer[signal_id] = {
            "token": token_symbol,
            "recipients": [],
            "admin_sent": True,
        }
    else:
        _signal_buffer[signal_id]["admin_sent"] = True


async def _flush_signal_log(signal_id: int):
    await asyncio.sleep(SIGNAL_BATCH_DELAY)

    buf = _signal_buffer.pop(signal_id, None)
    _signal_flush_tasks.pop(signal_id, None)

    if not buf:
        return

    recipients = buf["recipients"]
    token = buf["token"]
    admin = "✅ Admin" if buf.get("admin_sent") else ""

    if not recipients and not admin:
        return

    lines = [
        f"📡 <b>Сигнал отправлен подписчикам</b>",
        f"Бот обнаружил сигнал по токену {token} и разослал его.",
        "",
    ]
    if admin:
        lines.append(f"   {admin}")
    for r in recipients:
        lines.append(f"   {r}")
    lines.append(f"   Всего получателей: {len(recipients)}")

    await _send_log("\n".join(lines))


# ────────────────────────────────────────
#  Реферальный бонус (старый, для совместимости)
# ────────────────────────────────────────

async def log_referral_bonus(referrer_id: int, referrer_name: str,
                             referred_id: int, referred_name: str):
    text = (
        f"🤝 <b>Реферальный бонус активирован</b>\n"
        f"Реферал оформил подписку — реферер получил бонус.\n\n"
        f"   Реферал: {referred_name} (ID: <code>{referred_id}</code>)\n"
        f"   Реферер: {referrer_name} (ID: <code>{referrer_id}</code>)\n"
        f"   Бонус: 7 дней Premium"
    )
    await _send_log(text)
