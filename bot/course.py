"""Course & Community module — manual payment with referral discounts."""

import logging
import re
import time
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command, CommandObject

from config.settings import (
    COURSE_FREE_CHANNEL_ID, COURSE_PAID_CHANNEL_ID,
    COURSE_PRICE_USDT, COURSE_INVITE_EXPIRE_SECONDS,
    COURSE_PAYMENT_WALLET, PAYMENT_WALLET,
    COMMUNITY_PAYMENT_WALLET, COMMUNITY_PRICE_USDT,
    COMMUNITY_CHANNEL_ID,
    TELEGRAM_CHAT_ID,
    REFERRAL_COURSE_DISCOUNT,
    COURSE_OWNER_COMMUNITY_DISCOUNT,
)
from db import repository as repo

logger = logging.getLogger(__name__)

course_router = Router()

# Track which product a user is paying for
_pending_payments: dict[int, dict] = {}

# Solana TX signature pattern: base58, typically 87-88 chars
TX_PATTERN = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{80,100}$')


# ──────────────────────────────────────
#  Discount calculation
# ──────────────────────────────────────

async def get_discount_info(user_id: int) -> dict:
    """Calculate discounts for a user."""
    sub = await repo.get_subscriber(user_id)
    has_course = bool(sub and sub.course_purchased)
    has_referrer = bool(sub and sub.referred_by)
    referral_credits = (sub.referral_credits or 0) if sub else 0

    # Course discount: 20% if referred OR has referral credit
    course_discount_pct = 0
    course_discount_reason = ""
    if has_referrer and not has_course:
        course_discount_pct = int(REFERRAL_COURSE_DISCOUNT * 100)
        course_discount_reason = "referral"
    elif referral_credits > 0 and not has_course:
        course_discount_pct = int(REFERRAL_COURSE_DISCOUNT * 100)
        course_discount_reason = "referral_credit"

    course_price = COURSE_PRICE_USDT
    if course_discount_pct:
        course_price = COURSE_PRICE_USDT * (1 - course_discount_pct / 100)

    # Community discount: 30% if owns course
    community_discount_pct = 0
    community_discount_reason = ""
    if has_course:
        community_discount_pct = int(COURSE_OWNER_COMMUNITY_DISCOUNT * 100)
        community_discount_reason = "course_owner"

    community_price = COMMUNITY_PRICE_USDT
    if community_discount_pct:
        community_price = COMMUNITY_PRICE_USDT * (1 - community_discount_pct / 100)

    return {
        "course_price": course_price,
        "course_discount_pct": course_discount_pct,
        "course_discount_reason": course_discount_reason,
        "community_price": community_price,
        "community_discount_pct": community_discount_pct,
        "community_discount_reason": community_discount_reason,
        "has_course": has_course,
        "referral_credits": referral_credits,
    }


# ──────────────────────────────────────
#  Helpers
# ──────────────────────────────────────

async def _generate_invite(bot: Bot, channel_id: int, user_id: int) -> str:
    expire_at = int(time.time()) + COURSE_INVITE_EXPIRE_SECONDS
    invite = await bot.create_chat_invite_link(
        chat_id=channel_id,
        member_limit=1,
        expire_date=expire_at,
        name=f"wayan_{user_id}_{int(time.time())}",
    )
    return invite.invite_link


async def _is_member(bot: Bot, channel_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def _record_access(user_id: int, invite_link: str,
                          payment_tx: str = None, is_test: bool = False):
    await repo.save_course_access(user_id, invite_link, payment_tx, is_test)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


def _price_text(original: float, discounted: float, discount_pct: int, reason: str) -> str:
    """Format price with optional strikethrough discount."""
    if discount_pct:
        reason_text = {
            "referral": "реферальная скидка",
            "referral_credit": "скидка за реферала",
            "course_owner": "скидка для владельцев курса",
        }.get(reason, "скидка")
        return (
            f"🎁 <b>{reason_text} {discount_pct}%!</b>\n"
            f"💰 Цена: <s>{original:.0f}</s> → <b>{discounted:.0f} USDT</b>"
        )
    return f"💰 Цена: <b>{original:.0f} USDT</b>"


# ──────────────────────────────────────
#  Course info
# ──────────────────────────────────────

def _course_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📖 Бесплатные модули (1-2)",
            callback_data="course_free",
        )],
        [InlineKeyboardButton(
            text="🎓 Купить полный курс",
            callback_data="course_buy",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


COURSE_TEXT = (
    "🎓 <b>Курс — Onchain Trading на Solana</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Полное обучение DEX-трейдингу на Solana.\n"
    "7 модулей — от основ до продвинутых стратегий.\n\n"
    "📌 <b>Модуль 1.</b> Среда и безопасность\n"
    "📌 <b>Модуль 2.</b> Инструменты трейдера\n"
    "📌 <b>Модуль 3.</b> Оценка токена\n"
    "📌 <b>Модуль 4.</b> Поиск монет\n"
    "📌 <b>Модуль 5.</b> Риск-менеджмент\n"
    "📌 <b>Модуль 6.</b> Система трейдера\n"
    "📌 <b>Модуль 7.</b> Продвинутые стратегии\n\n"
    "Модули 1-2 доступны бесплатно.\n"
    "Полный курс — все 7 модулей + приложения.\n"
    "Доступ навсегда после покупки.\n\n"
    f"💰 <b>Цена: {COURSE_PRICE_USDT:.0f} USDT</b>"
)


# ──────────────────────────────────────
#  Community info
# ──────────────────────────────────────

def _community_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔑 Вступить в комьюнити",
            callback_data="community_buy",
        )],
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


COMMUNITY_TEXT = (
    "👥 <b>Wayan Premium</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Мой закрытый ресерч-спейс по On-chain, крипте и AI.\n\n"
    "Я — Wayan, трейдер, аналитик и networker, "
    "который работал рядом с проектами, "
    "привлекавшими шестизначные суммы.\n\n"
    "<b>Внутри канала:</b>\n\n"
    "🍑 мой личный взгляд на рынок\n"
    "🍑 идеи, за которыми я сам слежу\n"
    "🍑 AI + crypto как новая зона роста\n"
    "🍑 закрытый чат и сильное комьюнити\n\n"
    "Быть раньше рынка и видеть новые возможности в 2026.\n\n"
    f"💰 <b>Доступ: {COMMUNITY_PRICE_USDT:.0f} USDT</b>"
)


# ──────────────────────────────────────
#  /course, /community commands
# ──────────────────────────────────────

@course_router.message(Command("course"))
async def cmd_course(message: Message):
    info = await get_discount_info(message.from_user.id)
    text = COURSE_TEXT
    if info["has_course"]:
        text += "\n\n✅ <b>Ты уже купил курс!</b>"
    elif info["course_discount_pct"]:
        # Replace static price with discount price
        text = text.replace(
            f"💰 <b>Цена: {COURSE_PRICE_USDT:.0f} USDT</b>",
            _price_text(COURSE_PRICE_USDT, info["course_price"],
                        info["course_discount_pct"], info["course_discount_reason"]),
        )
    await message.answer(text, parse_mode="HTML", reply_markup=_course_keyboard())


@course_router.message(Command("community"))
async def cmd_community(message: Message):
    info = await get_discount_info(message.from_user.id)
    text = COMMUNITY_TEXT
    if info["has_course"]:
        text += (
            f"\n\n🎁 <b>У тебя есть курс → скидка {info['community_discount_pct']}%!</b>\n"
            f"Цена для тебя: <b>{info['community_price']:.0f} USDT</b>"
        )
    await message.answer(text, parse_mode="HTML", reply_markup=_community_keyboard())


# ──────────────────────────────────────
#  Course callbacks
# ──────────────────────────────────────

@course_router.callback_query(F.data == "course_info")
async def cb_course_info(callback: CallbackQuery):
    user = callback.from_user
    info = await get_discount_info(user.id)

    text = (
        "🎓 <b>Курс — Onchain Trading на Solana</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Полное обучение DEX-трейдингу на Solana.\n"
        "7 модулей — от основ до продвинутых стратегий.\n\n"
        "📌 <b>Модуль 1.</b> Среда и безопасность\n"
        "📌 <b>Модуль 2.</b> Инструменты трейдера\n"
        "📌 <b>Модуль 3.</b> Оценка токена\n"
        "📌 <b>Модуль 4.</b> Поиск монет\n"
        "📌 <b>Модуль 5.</b> Риск-менеджмент\n"
        "📌 <b>Модуль 6.</b> Система трейдера\n"
        "📌 <b>Модуль 7.</b> Продвинутые стратегии\n\n"
        "Модули 1-2 доступны бесплатно.\n"
        "Полный курс — все 7 модулей + приложения.\n"
        "Доступ навсегда после покупки.\n\n"
    )

    if info["has_course"]:
        text += "✅ <b>Ты уже купил курс!</b>"
    elif info["course_discount_pct"]:
        text += _price_text(
            COURSE_PRICE_USDT, info["course_price"],
            info["course_discount_pct"], info["course_discount_reason"],
        )
    else:
        text += f"💰 <b>Цена: {COURSE_PRICE_USDT:.0f} USDT</b>"

    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=_course_keyboard(),
    )
    await callback.answer()


@course_router.callback_query(F.data == "course_free")
async def cb_course_free(callback: CallbackQuery):
    user = callback.from_user
    bot = callback.bot

    if await _is_member(bot, COURSE_FREE_CHANNEL_ID, user.id):
        await callback.message.edit_text(
            "✅ Ты уже в бесплатной части курса.\n\n"
            "Открой группу и начинай обучение.\n"
            "Для полного курса нажми кнопку ниже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🎓 Купить полный курс",
                    callback_data="course_buy",
                )],
                [InlineKeyboardButton(text="« Назад", callback_data="course_info")],
            ]),
        )
        await callback.answer()
        return

    try:
        invite_link = await _generate_invite(bot, COURSE_FREE_CHANNEL_ID, user.id)

        from bot.activity_log import log_course_access
        await log_course_access(user.id, user.username or "", user.first_name or "", is_test=True)

        expire_min = COURSE_INVITE_EXPIRE_SECONDS // 60
        await callback.message.edit_text(
            "📖 <b>Добро пожаловать в курс!</b>\n\n"
            "Доступны модули 1-2:\n"
            "— Среда и безопасность\n"
            "— Инструменты трейдера\n\n"
            f"🔗 Ссылка:\n{invite_link}\n\n"
            f"⏳ Ссылка одноразовая, действует <b>{expire_min} мин</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🎓 Купить полный курс",
                    callback_data="course_buy",
                )],
                [InlineKeyboardButton(text="« Назад", callback_data="course_info")],
            ]),
        )
    except Exception as e:
        logger.error(f"Failed to generate free course invite for {user.id}: {e}")
        await callback.message.edit_text(
            "❌ Не удалось сгенерировать ссылку.\n"
            f"Ошибка: <code>{e}</code>",
            parse_mode="HTML",
            reply_markup=_back_kb(),
        )
    await callback.answer()


@course_router.callback_query(F.data == "course_buy")
async def cb_course_buy(callback: CallbackQuery):
    """Show payment instructions with dynamic price (discounts applied)."""
    user = callback.from_user
    info = await get_discount_info(user.id)

    if info["has_course"]:
        await callback.message.edit_text(
            "✅ Ты уже купил курс!", parse_mode="HTML", reply_markup=_back_kb(),
        )
        await callback.answer()
        return

    price = info["course_price"]
    wallet = COURSE_PAYMENT_WALLET or PAYMENT_WALLET

    price_line = _price_text(
        COURSE_PRICE_USDT, price,
        info["course_discount_pct"], info["course_discount_reason"],
    )

    text = (
        f"🎓 <b>Купить полный курс</b>\n\n"
        f"{price_line}\n\n"
        f"Отправь <b>{price:.0f} USDT</b> (SPL USDT на Solana) на кошелёк:\n"
        f"<code>{wallet}</code>\n\n"
        f"После оплаты <b>скопируй TX hash и отправь его сюда в чат</b>.\n\n"
        f"Мы проверим транзакцию и откроем тебе доступ."
    )

    _pending_payments[user.id] = {
        "product": "course",
        "expected_price": price,
        "discount_pct": info["course_discount_pct"],
        "discount_reason": info["course_discount_reason"],
    }

    from bot.activity_log import log_product_view
    discount_str = f"Скидка {info['course_discount_pct']}% → {price:.0f} USDT" if info["course_discount_pct"] else ""
    await log_product_view(user.id, user.username or "", user.first_name or "", "Курс (покупка)", discount_str)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
    await callback.answer()


# ──────────────────────────────────────
#  Community callbacks
# ──────────────────────────────────────

@course_router.callback_query(F.data == "community_info")
async def cb_community_info(callback: CallbackQuery):
    user = callback.from_user
    info = await get_discount_info(user.id)

    text = COMMUNITY_TEXT
    if info["has_course"]:
        discount_price = info["community_price"]
        text += (
            f"\n\n🎁 <b>У тебя есть курс → скидка {info['community_discount_pct']}%!</b>\n"
            f"Цена для тебя: <b>{discount_price:.0f} USDT</b>"
        )

    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=_community_keyboard(),
    )
    await callback.answer()


@course_router.callback_query(F.data == "community_buy")
async def cb_community_buy(callback: CallbackQuery):
    """Show payment instructions with dynamic price."""
    user = callback.from_user
    info = await get_discount_info(user.id)

    price = info["community_price"]

    price_line = _price_text(
        COMMUNITY_PRICE_USDT, price,
        info["community_discount_pct"], info["community_discount_reason"],
    )

    text = (
        f"👥 <b>Вступить в Wayan Premium</b>\n\n"
        f"{price_line}\n\n"
        f"Отправь <b>{price:.0f} USDT</b> (SPL USDT на Solana) на кошелёк:\n"
        f"<code>{COMMUNITY_PAYMENT_WALLET}</code>\n\n"
        f"После оплаты <b>скопируй TX hash и отправь его сюда в чат</b>.\n\n"
        f"Мы проверим транзакцию и откроем тебе доступ."
    )

    _pending_payments[user.id] = {
        "product": "community",
        "expected_price": price,
        "discount_pct": info["community_discount_pct"],
        "discount_reason": info["community_discount_reason"],
    }

    from bot.activity_log import log_product_view
    discount_str = f"Скидка {info['community_discount_pct']}% → {price:.0f} USDT" if info["community_discount_pct"] else ""
    await log_product_view(user.id, user.username or "", user.first_name or "", "Комьюнити (покупка)", discount_str)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
    await callback.answer()


# ──────────────────────────────────────
#  TX hash detection
# ──────────────────────────────────────

@course_router.message()
async def handle_possible_tx(message: Message):
    """Catch messages that look like Solana TX signatures."""
    text = (message.text or "").strip()
    if not TX_PATTERN.match(text):
        return

    user = message.from_user
    tx_sig = text
    pending = _pending_payments.pop(user.id, None)

    if not pending:
        await message.answer(
            "Похоже на TX hash. Что ты оплачивал?\n\nВыбери продукт:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🎓 Курс",
                    callback_data=f"tx_product:course:{tx_sig}",
                )],
                [InlineKeyboardButton(
                    text="👥 Комьюнити",
                    callback_data=f"tx_product:community:{tx_sig}",
                )],
            ]),
        )
        return

    await _process_payment_request(
        message, user.id, user.username or "",
        user.first_name or "", pending, tx_sig,
    )


@course_router.callback_query(F.data.startswith("tx_product:"))
async def cb_tx_product(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка")
        return

    product = parts[1]
    tx_sig = parts[2]
    user = callback.from_user

    info = await get_discount_info(user.id)
    if product == "course":
        pending = {
            "product": "course",
            "expected_price": info["course_price"],
            "discount_pct": info["course_discount_pct"],
            "discount_reason": info["course_discount_reason"],
        }
    else:
        pending = {
            "product": "community",
            "expected_price": info["community_price"],
            "discount_pct": info["community_discount_pct"],
            "discount_reason": info["community_discount_reason"],
        }

    await callback.message.edit_text("⏳ Записываю...", parse_mode="HTML")
    await callback.answer()

    await _process_payment_request(
        callback.message, user.id, user.username or "",
        user.first_name or "", pending, tx_sig,
    )


async def _process_payment_request(message: Message, user_id: int,
                                     username: str, first_name: str,
                                     pending: dict, tx_sig: str):
    """Record payment request, notify admin with discount info."""
    from bot.activity_log import _send_log
    from bot.telegram_bot import bot

    name = first_name or username or str(user_id)
    handle = f" (@{username})" if username else ""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    product = pending["product"]
    expected_price = pending["expected_price"]
    discount_pct = pending.get("discount_pct", 0)
    discount_reason = pending.get("discount_reason", "")

    product_names = {"course": "Курс", "community": "Комьюнити"}
    product_name = product_names.get(product, product)

    discount_line = ""
    if discount_pct:
        reason_text = {
            "referral": "реферальная",
            "referral_credit": "за реферала",
            "course_owner": "владелец курса",
        }.get(discount_reason, "")
        original = COURSE_PRICE_USDT if product == "course" else COMMUNITY_PRICE_USDT
        discount_line = f"\n🎁 Скидка: {discount_pct}% ({reason_text}) — {original:.0f} → {expected_price:.0f} USDT"

    # Log to activity chat
    log_text = (
        f"💳 <b>Юзер отправил TX hash — заявка на оплату</b>\n"
        f"Юзер оплатил продукт и скинул TX hash в чат бота. "
        f"Нужно проверить транзакцию на Solscan и подтвердить.\n\n"
        f"   Кто: {name}{handle}\n"
        f"   ID: <code>{user_id}</code>\n"
        f"   Продукт: {product_name}{discount_line}\n"
        f"   Ожидаемая сумма: {expected_price:.0f} USDT\n"
        f"   TX: <code>{tx_sig}</code>\n"
        f"   Дата: {now}\n"
        f"   Solscan: https://solscan.io/tx/{tx_sig}"
    )
    await _send_log(log_text)

    # Notify admin
    grant_cmd = f"/grant_course {user_id}" if product == "course" else f"/grant_community {user_id}"
    admin_text = (
        f"💳 <b>Новая заявка на оплату!</b>\n\n"
        f"👤 {name}{handle} (ID: <code>{user_id}</code>)\n"
        f"📦 Продукт: <b>{product_name}</b>{discount_line}\n"
        f"💰 Ожидаемая сумма: <b>{expected_price:.0f} USDT</b>\n"
        f"🔗 TX: <code>{tx_sig}</code>\n"
        f"📅 {now}\n\n"
        f"<a href=\"https://solscan.io/tx/{tx_sig}\">Проверить на Solscan</a>\n\n"
        f"Для подтверждения:\n<code>{grant_cmd}</code>"
    )
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text=admin_text,
            parse_mode="HTML", disable_web_page_preview=False,
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about payment: {e}")

    # Confirm to user
    await message.answer(
        f"✅ <b>Заявка принята!</b>\n\n"
        f"Продукт: <b>{product_name}</b>\n"
        f"Сумма: <b>{expected_price:.0f} USDT</b>\n"
        f"TX: <code>{tx_sig[:20]}...</code>\n\n"
        f"Мы проверим транзакцию и откроем доступ.\n"
        f"Обычно это занимает до нескольких часов.",
        parse_mode="HTML",
        reply_markup=_back_kb(),
    )

    logger.info(f"Payment request: {name} ({user_id}) -> {product_name} {expected_price:.0f} USDT, TX: {tx_sig[:20]}...")


# ──────────────────────────────────────
#  Admin commands: /grant_course, /grant_community
# ──────────────────────────────────────

@course_router.message(Command("grant_course"))
async def cmd_grant_course(message: Message, command: CommandObject):
    """Admin: confirm course payment and grant access."""
    if str(message.from_user.id) != str(TELEGRAM_CHAT_ID):
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("Использование: <code>/grant_course USER_ID</code>", parse_mode="HTML")
        return

    try:
        target_user_id = int(args)
    except ValueError:
        await message.answer("❌ Неверный user ID", parse_mode="HTML")
        return

    # Mark course as purchased
    await repo.mark_course_purchased(target_user_id)

    # Generate invite
    bot = message.bot
    try:
        invite_link = await _generate_invite(bot, COURSE_PAID_CHANNEL_ID, target_user_id)
        await _record_access(target_user_id, invite_link, is_test=False)
    except Exception as e:
        await message.answer(
            f"✅ Курс отмечен как купленный, но ссылку создать не удалось:\n<code>{e}</code>",
            parse_mode="HTML",
        )
        return

    # Send invite to user
    expire_min = COURSE_INVITE_EXPIRE_SECONDS // 60
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=(
                "🎉 <b>Оплата подтверждена!</b>\n\n"
                "Добро пожаловать в полный курс Onchain Trading!\n\n"
                f"🔗 Ссылка:\n{invite_link}\n\n"
                f"⏳ Ссылка одноразовая, действует <b>{expire_min} мин</b>.\n\n"
                f"🎁 Теперь у тебя скидка <b>30%</b> на Wayan Premium комьюнити!\n"
                f"Используй /community чтобы узнать больше."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ссылку юзеру: <code>{e}</code>", parse_mode="HTML")
        return

    # Give referral credit to referrer
    sub = await repo.get_subscriber(target_user_id)
    referrer_info = ""
    if sub and sub.referred_by:
        await repo.add_referral_credit(sub.referred_by)
        referrer = await repo.get_subscriber(sub.referred_by)
        referrer_name = (referrer.first_name or referrer.username or str(sub.referred_by)) if referrer else str(sub.referred_by)
        buyer_name = sub.first_name or sub.username or str(target_user_id)
        referrer_info = f"\n🤝 Реферер {referrer_name} ({sub.referred_by}) получил кредит на скидку 20%"

        # Log referral credit
        from bot.activity_log import log_referral_credit_earned
        await log_referral_credit_earned(
            sub.referred_by, referrer_name,
            target_user_id, buyer_name, "Курс",
        )

        # Notify referrer
        try:
            await bot.send_message(
                chat_id=sub.referred_by,
                text=(
                    f"🎁 <b>Реферальный бонус!</b>\n\n"
                    f"Твой друг {buyer_name} купил курс.\n"
                    f"Тебе начислена <b>скидка 20%</b> на следующую покупку!"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Log
    from bot.activity_log import log_course_granted
    target_sub = await repo.get_subscriber(target_user_id)
    t_name = (target_sub.first_name or target_sub.username or str(target_user_id)) if target_sub else str(target_user_id)
    t_uname = (target_sub.username or "") if target_sub else ""
    await log_course_granted(target_user_id, t_uname, t_name)

    await message.answer(
        f"✅ Курс выдан юзеру <code>{target_user_id}</code>.\n"
        f"Ссылка отправлена.{referrer_info}",
        parse_mode="HTML",
    )


@course_router.message(Command("grant_community"))
async def cmd_grant_community(message: Message, command: CommandObject):
    """Admin: confirm community payment and grant access."""
    if str(message.from_user.id) != str(TELEGRAM_CHAT_ID):
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("Использование: <code>/grant_community USER_ID</code>", parse_mode="HTML")
        return

    try:
        target_user_id = int(args)
    except ValueError:
        await message.answer("❌ Неверный user ID", parse_mode="HTML")
        return

    if not COMMUNITY_CHANNEL_ID:
        await message.answer("❌ COMMUNITY_CHANNEL_ID не настроен в .env", parse_mode="HTML")
        return

    bot = message.bot
    try:
        invite_link = await _generate_invite(bot, COMMUNITY_CHANNEL_ID, target_user_id)
    except Exception as e:
        await message.answer(f"❌ Не удалось создать ссылку:\n<code>{e}</code>", parse_mode="HTML")
        return

    expire_min = COURSE_INVITE_EXPIRE_SECONDS // 60
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=(
                "🎉 <b>Оплата подтверждена!</b>\n\n"
                "Добро пожаловать в Wayan Premium!\n\n"
                f"🔗 Ссылка:\n{invite_link}\n\n"
                f"⏳ Ссылка одноразовая, действует <b>{expire_min} мин</b>."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ссылку юзеру: <code>{e}</code>", parse_mode="HTML")
        return

    from bot.activity_log import log_community_granted
    target_sub = await repo.get_subscriber(target_user_id)
    t_name = (target_sub.first_name or target_sub.username or str(target_user_id)) if target_sub else str(target_user_id)
    t_uname = (target_sub.username or "") if target_sub else ""
    await log_community_granted(target_user_id, t_uname, t_name)

    await message.answer(
        f"✅ Комьюнити выдано юзеру <code>{target_user_id}</code>. Ссылка отправлена.",
        parse_mode="HTML",
    )
