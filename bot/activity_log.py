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

async def log_new_user(user_id: int, username: str, first_name: str):
    name, uid = _user_line(user_id, username, first_name)
    text = (
        f"👤 <b>Новый юзер в боте</b>\n"
        f"Юзер нажал /start и впервые попал в бота.\n\n"
        f"   Кто: {name}\n"
        f"   ID: {uid}"
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
                      amount_usdt: float = 0):
    name, uid = _user_line(user_id, username, first_name)
    tier_names = {
        "premium": "Premium (бот)",
        "premium_plus": "Premium+ (бот)",
        "course": "Курс Onchain Trading",
        "meteora": "Курс Meteora",
        "community": "Wayan Premium комьюнити",
    }
    tier_name = tier_names.get(tier, tier)
    if amount_usdt > 0:
        amount_str = f"{amount_usdt:.0f} USDT"
    else:
        amount_str = f"{amount_sol:.4f} SOL"
    text = (
        f"💰 <b>Оплата подтверждена</b>\n"
        f"Админ подтвердил оплату и юзер получил доступ.\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Продукт: {tier_name}\n"
        f"   Сумма: {amount_str}\n"
        f"   TX: <code>{tx_sig[:20]}...</code>"
    )
    await _send_log(text)


# ────────────────────────────────────────
#  Курс выдан (/grant_course)
# ────────────────────────────────────────

async def log_course_granted(user_id: int, username: str, first_name: str,
                             course_name: str = "Onchain"):
    name, uid = _user_line(user_id, username, first_name)
    if course_name == "Meteora":
        cmd = "/grant_meteora"
    else:
        cmd = "/grant_course"
    text = (
        f"✅ <b>Курс [{course_name}] выдан юзеру</b>\n"
        f"Админ выполнил {cmd} — юзеру отправлена ссылка на полный курс {course_name}.\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Курс: {course_name}"
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
#  Бесплатный курс (модули 1-2)
# ────────────────────────────────────────

async def log_course_access(user_id: int, username: str, first_name: str, is_test: bool,
                            course_name: str = "Onchain"):
    name, uid = _user_line(user_id, username, first_name)
    if is_test:
        if course_name == "Meteora":
            desc = "Юзер нажал «Бесплатная часть Meteora» и получил ссылку."
            mode = "Бесплатный"
        else:
            desc = "Юзер нажал «Бесплатные модули» и получил ссылку на модули 1-2."
            mode = "Бесплатный (модули 1-2)"
    else:
        if course_name == "Meteora":
            desc = "Юзер получил доступ к полному курсу Meteora."
        else:
            desc = "Юзер получил доступ к полному курсу Onchain (7 модулей)."
        mode = "Полный (оплачен)"
    text = (
        f"📚 <b>Доступ к курсу [{course_name}]</b>\n"
        f"{desc}\n\n"
        f"   Кто: {name} (ID: {uid})\n"
        f"   Курс: {course_name}\n"
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
