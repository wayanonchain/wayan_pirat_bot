"""Telegram bot — commands, menus, signal delivery."""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram import F

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_IDS,
    PAYMENT_WALLET, PREMIUM_PRICE_SOL,
    COURSE_PRICE_USDT, COMMUNITY_PRICE_USDT,
    FREE_SIGNAL_DELAY_MINUTES,
)
from bot.formatters import format_signal_message, format_signal_message_free, format_stats_message, format_usd, format_mcap
from db import repository as repo

logger = logging.getLogger(__name__)


def check_admin(user_id: int) -> bool:
    return str(user_id) in ADMIN_IDS

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

from bot.course import course_router
dp.include_router(course_router)


# ============================================================
#  Bot description (shown before /start)
# ============================================================

BOT_DESCRIPTION = (
    "On-chain, AI, Образование — до того как это стало трендом 💦\n\n"
    "Обучение Onchain Trading · "
    "Закрытое комьюнити, где знают всё про On-chain\n\n"
    "Каждому своё 👇\n\n"
    "📱 t.me/wayan_onchain\n"
    "𝕏  x.com/wayan_onchain\n"
    "🎵 tiktok.com/@wayan.onchain\n"
    "▶️ youtube.com/@Wayan_onchain"
)


async def setup_bot_profile():
    """Set bot description and commands on startup."""
    try:
        await bot.set_my_description(BOT_DESCRIPTION)
        await bot.set_my_short_description(
            "Обучение On-chain, закрытое комьюнити 🔥"
        )
        await bot.set_my_commands([
            {"command": "start", "description": "Главное меню"},
            {"command": "course", "description": "Курс Onchain Trading"},
            {"command": "community", "description": "Wayan Premium комьюнити"},
            {"command": "referral", "description": "Реферальная программа"},
            {"command": "help", "description": "Все команды"},
        ])
        logger.info("Bot profile updated (description, commands)")
    except Exception as e:
        logger.warning(f"Failed to set bot profile: {e}")


# ============================================================
#  Signal delivery (tier-based)
# ============================================================

async def _send_delayed_signal(user_id: int, text: str, delay_seconds: int,
                                signal_id: int, token_symbol: str, tier: str):
    """Send a signal to a free-tier user after a delay."""
    await asyncio.sleep(delay_seconds)
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        from bot.activity_log import log_signal_sent
        sub = await repo.get_subscriber(user_id)
        await log_signal_sent(
            signal_id, token_symbol,
            user_id,
            sub.username if sub else "",
            sub.first_name if sub else "",
            tier,
        )
    except Exception as e:
        logger.warning(f"Failed to send delayed signal to {user_id}: {e}")


async def send_signal(signal: dict):
    """Send a signal alert to admin + all subscribers."""
    from bot.activity_log import log_signal_admin, log_signal_sent

    signal_id = signal.get("signal_id", 0)
    token_symbol = signal.get("token_symbol", "???")

    text_full = format_signal_message(signal)
    if signal.get("accumulation_score"):
        from core.token_analyzer import format_score_short
        text_full += "\n" + format_score_short(signal["accumulation_score"])

    try:
        msg = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text_full,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await repo.mark_signal_sent(signal_id, msg.message_id)
        logger.info(f"Signal sent to admin: {token_symbol} (msg_id={msg.message_id})")
        await log_signal_admin(signal_id, token_symbol)
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

    subscribers = await repo.get_active_subscriber_ids()
    for user_id, tier in subscribers.items():
        if user_id == int(TELEGRAM_CHAT_ID):
            continue

        try:
            if tier in ("premium", "premium_plus"):
                from bot.filters import get_user_filters, should_send_signal
                filters = await get_user_filters(user_id)
                if not should_send_signal(signal, filters):
                    continue

                text = format_signal_message(signal)
                if signal.get("accumulation_score"):
                    from core.token_analyzer import format_score_short
                    text += "\n" + format_score_short(signal["accumulation_score"])

                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                sub = await repo.get_subscriber(user_id)
                await log_signal_sent(
                    signal_id, token_symbol,
                    user_id,
                    sub.username if sub else "",
                    sub.first_name if sub else "",
                    tier,
                )
            else:
                text = format_signal_message_free(signal)
                asyncio.create_task(
                    _send_delayed_signal(
                        user_id, text,
                        FREE_SIGNAL_DELAY_MINUTES * 60,
                        signal_id, token_symbol, "free",
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to send signal to {user_id}: {e}")


async def send_message(text: str, parse_mode: str = "HTML"):
    """Send a generic message to the admin chat."""
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


# ============================================================
#  Inline keyboards
# ============================================================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎓 Курс", callback_data="course_info"),
            InlineKeyboardButton(text="👥 Комьюнити", callback_data="community_info"),
        ],
        [
            InlineKeyboardButton(text="🤝 Рефералка", callback_data="referral"),
        ],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


def bot_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Сигналы 24ч", callback_data="signals"),
            InlineKeyboardButton(text="🏆 Топ кошельки", callback_data="wallets"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Как работает", callback_data="how_it_works"),
            InlineKeyboardButton(text="🟢 Статус", callback_data="status"),
        ],
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


def premium_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"💎 Premium — {PREMIUM_PRICE_SOL} SOL/мес", callback_data="buy_premium"),
        ],
        [InlineKeyboardButton(text="« Назад", callback_data="back_menu")],
    ])


# ============================================================
#  Welcome text
# ============================================================

WELCOME_TEXT = (
    "🏴‍☠️ <b>Wayan Pirate</b>\n\n"
    "Пока большинство догоняет тренды — ты можешь зайти в них раньше 💦\n\n"
    "Я собрал экосистему Wayan Onchain для тех, кто хочет не просто "
    "смотреть на рынок, а разбираться, адаптироваться и зарабатывать "
    "на новых возможностях быстрее других.\n\n"
    "Что у тебя есть прямо сейчас:\n\n"

    "🎓 <b>Onchain BASE — Бесплатно</b>\n"
    "Старт для тех, кто хочет зайти в on-chain "
    "без путаницы и с нормальной базой.\n\n"

    "🎓 <b>Onchain Premium — {course} USDT</b>\n"
    "7 модулей от нуля до продвинутого уровня.\n"
    "Система обучения, которая дает понимание рынка, "
    "логику on-chain анализа и доступ к закрытому чату "
    "с возможностью задавать вопросы напрямую.\n\n"
    "2 первых модуля — бесплатно.\n\n"

    "👥 <b>Закрытый чат Wayan Premium — {community} USDT / мес</b>\n\n"
    "Закрытое пространство с on-chain, AI, ресерчем, "
    "новыми нарративами и живым обсуждением рынка.\n\n"

    "Это уже не просто обучение.\n"
    "Это доступ к среде, информации и людям, "
    "которые смотрят глубже среднего рынка.\n\n"

    "Если тебе нужен рост, понимание и доступ к сильной среде — заходи 👇"
).format(
    course=f"{COURSE_PRICE_USDT:.0f}",
    community=f"{COMMUNITY_PRICE_USDT:.0f}",
)


# ============================================================
#  Command handlers
# ============================================================

@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    user = message.from_user
    existing = await repo.get_subscriber(user.id)
    is_new = existing is None

    await repo.upsert_subscriber(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    # Handle referral deep link: /start ref_XXXXXXXX
    referred_by = None
    args = (command.args or "").strip()
    logger.info(f"[START] user={user.id} args='{args}' is_new={is_new}")
    if args.startswith("ref_"):
        ref_code = args[4:]
        referrer = await repo.get_subscriber_by_referral_code(ref_code)
        logger.info(f"[REF] code={ref_code} referrer={referrer.user_id if referrer else None}")
        if referrer and referrer.user_id != user.id:
            success = await repo.set_referred_by(user.id, referrer.user_id)
            logger.info(f"[REF] set_referred_by success={success}")
            if success:
                referred_by = referrer.user_id
                from config.settings import REFERRAL_COURSE_DISCOUNT
                discount_pct = int(REFERRAL_COURSE_DISCOUNT * 100)
                discounted = COURSE_PRICE_USDT * (1 - REFERRAL_COURSE_DISCOUNT)
                await message.answer(
                    f"🎁 <b>Реферальный бонус активирован!</b>\n\n"
                    f"Скидка {discount_pct}% на покупку курса "
                    f"(<s>{COURSE_PRICE_USDT:.0f}</s> → <b>{discounted:.0f} USDT</b>).\n"
                    f"Приглашён от: {referrer.first_name or referrer.username or 'друга'}",
                    parse_mode="HTML",
                )
                from bot.activity_log import log_referral_activated
                await log_referral_activated(
                    user.id, user.username or "", user.first_name or "",
                    referrer.user_id,
                    referrer.first_name or referrer.username or str(referrer.user_id),
                )

                # Notify referrer that someone used their link
                new_name = user.first_name or user.username or "Кто-то"
                try:
                    await bot.send_message(
                        chat_id=referrer.user_id,
                        text=(
                            f"🔗 <b>По твоей ссылке пришёл новый юзер!</b>\n\n"
                            f"Кто: {new_name}\n"
                            f"Ему активирована скидка 20% на курс.\n\n"
                            f"Когда он купит курс — ты получишь скидку на комьюнити!"
                        ),
                        parse_mode="HTML",
                    )
                    logger.info(f"[REF] Notification sent to referrer {referrer.user_id}")
                except Exception as e:
                    logger.error(f"[REF] Failed to notify referrer {referrer.user_id}: {e}")
            else:
                logger.info(f"[REF] set_referred_by returned False (already referred or self-ref)")

    if is_new:
        from bot.activity_log import log_new_user
        await log_new_user(user.id, user.username or "", user.first_name or "", referred_by)

    await message.answer(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Команды</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Продукты:</b>\n"
        "/course — Курс Onchain Trading\n"
        "/buy_course — Купить полный курс\n"
        "/community — Wayan Premium комьюнити\n"
        "/buy_community — Вступить в комьюнити\n\n"
        "<b>Другое:</b>\n"
        "/referral — Реферальная ссылка\n"
        "/start — Главное меню",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    await _send_status(message)


@router.message(Command("signals"))
async def cmd_signals(message: Message):
    await _send_signals(message)


@router.message(Command("wallets"))
async def cmd_wallets(message: Message):
    await _send_wallets(message)


@router.message(Command("plan"))
async def cmd_plan(message: Message):
    await _send_plan(message)


@router.message(Command("my_plan"))
async def cmd_my_plan(message: Message):
    await _send_my_plan(message)


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, command: CommandObject):
    """Analyze a token's Accumulation Score. Premium only."""
    user_id = message.from_user.id
    tier = await repo.get_user_tier(user_id)
    is_admin = check_admin(user_id)

    if tier not in ("premium", "premium_plus") and not is_admin:
        await message.answer(
            "💎 <b>Функция Premium</b>\n\n"
            "Accumulation Score доступен для подписчиков Premium.\n"
            "Используй /plan для подробностей.",
            parse_mode="HTML",
        )
        return

    token_address = (command.args or "").strip()
    if not token_address or len(token_address) < 32:
        await message.answer(
            "Использование: /analyze <адрес_токена>\n\n"
            "Пример:\n<code>/analyze So11111111111111111111111111111111111111112</code>",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ Анализирую токен...")

    from core.token_analyzer import analyze_token, format_score_message
    result = await analyze_token(token_address)
    if not result:
        await message.answer("❌ Не удалось проанализировать токен. Проверь адрес и попробуй снова.")
        return

    text = format_score_message(token_address, result)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("filter"))
async def cmd_filter(message: Message, command: CommandObject):
    """Manage signal filters. Premium only."""
    user_id = message.from_user.id
    tier = await repo.get_user_tier(user_id)
    is_admin = check_admin(user_id)

    if tier not in ("premium", "premium_plus") and not is_admin:
        await message.answer(
            "💎 <b>Функция Premium</b>\n\n"
            "Фильтры доступны для подписчиков Premium.\n"
            "Используй /plan для подробностей.",
            parse_mode="HTML",
        )
        return

    from bot.filters import get_user_filters, update_single_filter, save_user_filters, format_filters, DEFAULT_FILTERS

    args = (command.args or "").strip().split()
    if not args:
        filters = await get_user_filters(user_id)
        await message.answer(format_filters(filters), parse_mode="HTML")
        return

    action = args[0].lower()

    if action == "reset":
        await save_user_filters(user_id, DEFAULT_FILTERS.copy())
        await message.answer("✅ Фильтры сброшены на значения по умолчанию.", parse_mode="HTML")
        return

    if action == "off":
        await update_single_filter(user_id, "enabled", False)
        await message.answer("⏸ Фильтры выключены. Ты будешь получать все сигналы.", parse_mode="HTML")
        return

    if action == "on":
        await update_single_filter(user_id, "enabled", True)
        await message.answer("▶️ Фильтры включены.", parse_mode="HTML")
        return

    if len(args) < 2:
        await message.answer("Использование: <code>/filter min_mcap 50000</code>", parse_mode="HTML")
        return

    key = action
    val_str = args[1].lower()

    if key in ("min_mcap", "max_mcap", "min_buy_usd", "min_buy"):
        if key == "min_buy":
            key = "min_buy_usd"
        try:
            val = int(float(val_str))
        except ValueError:
            await message.answer("❌ Неверное число.", parse_mode="HTML")
            return
        filters = await update_single_filter(user_id, key, val)
        await message.answer(f"✅ {key} = <b>{val}</b>\n\n" + format_filters(filters), parse_mode="HTML")

    elif key == "min_wallets":
        try:
            val = int(val_str)
            val = max(2, min(10, val))
        except ValueError:
            await message.answer("❌ Неверное число.", parse_mode="HTML")
            return
        filters = await update_single_filter(user_id, key, val)
        await message.answer(f"✅ min_wallets = <b>{val}</b>\n\n" + format_filters(filters), parse_mode="HTML")

    elif key == "only_traders":
        val = val_str in ("on", "true", "yes", "1")
        filters = await update_single_filter(user_id, key, val)
        await message.answer(f"✅ only_traders = <b>{val}</b>\n\n" + format_filters(filters), parse_mode="HTML")

    else:
        await message.answer(
            "Доступные фильтры: min_mcap, max_mcap, min_buy, min_wallets, only_traders",
            parse_mode="HTML",
        )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Admin stats."""
    user_id = message.from_user.id
    if not check_admin(user_id):
        return

    await message.answer("⏳ Генерирую статистику...")
    from core.admin_stats import get_admin_stats, format_admin_report
    stats = await get_admin_stats()
    text = format_admin_report(stats)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("weekly_report"))
async def cmd_weekly_report(message: Message):
    """Trigger weekly report. Admin only."""
    user_id = message.from_user.id
    if not check_admin(user_id):
        return

    await message.answer("⏳ Генерирую недельный отчёт...")
    from core.weekly_report import send_weekly_report
    await send_weekly_report()


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, command: CommandObject):
    """Activate subscription: /subscribe premium <tx_signature>"""
    args = (command.args or "").strip().split()
    if len(args) < 2:
        await message.answer(
            "💎 <b>Оформление подписки</b>\n\n"
            f"1️⃣ Отправь <b>{PREMIUM_PRICE_SOL} SOL</b> на:\n"
            f"<code>{PAYMENT_WALLET}</code>\n\n"
            f"2️⃣ Скопируй TX signature\n\n"
            f"3️⃣ Отправь команду:\n"
            f"<code>/subscribe premium TX_SIGNATURE</code>\n\n"
            f"💰 Цена: <b>{PREMIUM_PRICE_SOL} SOL/мес</b>",
            parse_mode="HTML",
        )
        return

    tier = args[0].lower()
    tx_sig = args[1]

    if tier not in ("premium",):
        await message.answer(
            "Используй: <code>/subscribe premium TX_SIGNATURE</code>",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ Проверяю оплату...")

    from bot.subscription import verify_sol_payment
    result = await verify_sol_payment(tx_sig, tier, message.from_user.id)

    if result["ok"]:
        expires = result["expires_at"]
        extra = ""
        if result.get("referral_discount"):
            extra = "\n<i>🎁 Применена реферальная скидка 20%</i>"
        if result.get("referral_bonus_given") and result.get("referrer_id"):
            extra += "\n<i>🤝 Твой реферер получил 7 дней Premium</i>"
            try:
                await bot.send_message(
                    chat_id=result["referrer_id"],
                    text=f"🎁 <b>Реферальный бонус!</b>\n\n"
                         f"Твой реферал оформил подписку. Тебе начислено <b>7 дней Premium</b>.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            from bot.activity_log import log_referral_bonus
            referrer_sub = await repo.get_subscriber(result["referrer_id"])
            await log_referral_bonus(
                result["referrer_id"],
                referrer_sub.first_name or referrer_sub.username if referrer_sub else str(result["referrer_id"]),
                message.from_user.id,
                message.from_user.first_name or message.from_user.username or str(message.from_user.id),
            )

        from bot.activity_log import log_payment
        await log_payment(
            user_id=message.from_user.id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
            tier=tier,
            amount_sol=result.get("amount_sol", PREMIUM_PRICE_SOL),
            tx_sig=tx_sig,
            referral_discount=result.get("referral_discount", False),
        )

        await message.answer(
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"Тариф: <b>Premium</b>\n"
            f"Действует до: <b>{expires.strftime('%Y-%m-%d %H:%M UTC')}</b>{extra}",
            parse_mode="HTML",
        )
        user = message.from_user
        await repo.upsert_subscriber(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            tier=tier,
            expires_at=expires,
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка проверки оплаты</b>\n\n{result['error']}",
            parse_mode="HTML",
        )


@router.message(Command("referral"))
async def cmd_referral(message: Message):
    """Show referral link and stats."""
    user_id = message.from_user.id
    ref_code = await repo.get_referral_code(user_id)

    if not ref_code:
        await repo.upsert_subscriber(
            user_id=user_id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        ref_code = await repo.get_referral_code(user_id)

    stats = await repo.get_referral_stats(user_id)
    sub = await repo.get_subscriber(user_id)
    paid = stats.get("paid_referrals", 0)
    link = f"https://t.me/wayan_pirat_bot?start=ref_{ref_code}"

    # Calculate current referral tier bonus
    if paid >= 3:
        your_bonus = "🎁 Комьюнити <b>бесплатно</b>"
    elif paid >= 2:
        your_bonus = "🎁 Скидка <b>50%</b> на комьюнити"
    elif paid >= 1:
        your_bonus = "🎁 Скидка <b>20%</b> на комьюнити"
    else:
        your_bonus = "Пока нет бонусов — приглашай друзей!"

    text = (
        "🤝 <b>Реферальная программа</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вход в курс — 400$.\n"
        "Приводи друзей и получай бонусы:\n\n"
        "— 1 друг → другу <b>-20%</b> на курс, тебе <b>-20%</b> на комьюнити\n"
        "— 2 друга → друзьям <b>-20%</b>, тебе <b>-50%</b> на комьюнити\n"
        "— 3 друга → друзьям <b>-20%</b>, тебе <b>бесплатный</b> вход в комьюнити\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"<b>Статистика:</b>\n"
        f"👥 Приглашено: <b>{stats['total_referrals']}</b>\n"
        f"💰 Купили курс: <b>{paid}</b>\n"
        f"{your_bonus}\n"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())


# ============================================================
#  Callback handlers (inline buttons)
# ============================================================

@router.callback_query(F.data == "back_menu")
async def cb_back_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "bot_info")
async def cb_bot_info(callback: CallbackQuery):
    counts = await repo.wallet_count()
    active = counts.get("ACTIVE", 0)

    text = (
        "📡 <b>Smart Money Monitor</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Видь, что покупают крупные игроки на Solana, "
        "и принимай решения на основе их действий — "
        "а не слухов и твиттера.\n\n"
        f"Бот отслеживает <b>{active} проверенных кошельков</b> с реальным PnL "
        "от тысяч до миллионов долларов. Когда несколько из них "
        "покупают один и тот же токен — бот формирует сигнал.\n\n"
        "🆓 <b>Free:</b>\n"
        "— Сигналы с задержкой\n"
        "— Базовая информация: символ, MCAP, ссылки\n\n"
        f"💎 <b>Premium — {PREMIUM_PRICE_SOL} SOL/мес:</b>\n"
        "— Мгновенные сигналы без задержки\n"
        "— Адреса кошельков, PnL, тип\n"
        "— Accumulation Score (0-100)\n"
        "— Sell Alerts — когда Smart Money выходят\n"
        "— Фильтры сигналов под свой стиль\n"
        "— /analyze — анализ любого токена\n"
        "— Недельные отчёты по рынку"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=bot_info_keyboard())
    await callback.answer()


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery):
    counts = await repo.wallet_count()
    signals = await repo.get_recent_signals(24)
    from api.birdeye_client import get_sol_price
    sol_price = await get_sol_price()

    active = counts.get("ACTIVE", 0)
    text = (
        "🟢 <b>Статус</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>Работает</b> ✅\n"
        f"Кошельков: <b>{active}</b>\n"
        f"SOL: <b>${sol_price:.2f}</b>\n"
        f"Сигналов за 24ч: <b>{len(signals)}</b>\n"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="bot_info")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "signals")
async def cb_signals(callback: CallbackQuery):
    signals = await repo.get_recent_signals(24)
    back_to_bot = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="bot_info")],
    ])

    if not signals:
        text = (
            "📊 <b>Сигналы (24ч)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Сигналов пока нет.\n\n"
            "<i>Сигнал появляется, когда 2+ Smart Money кошелька покупают один токен.</i>"
        )
    else:
        lines = [
            f"📊 <b>Сигналы (24ч): {len(signals)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        for s in signals[:10]:
            mode_label = "🔥 M2" if s.mode >= 2 else "📡 M1"
            total = format_usd(s.total_buy_usd)
            mcap = format_mcap(s.mcap) if s.mcap else "N/A"
            lines.append(
                f"\n{mode_label} <b>{s.token_symbol or '???'}</b>\n"
                f"   Кошельков: {s.wallet_count} | Всего: {total}\n"
                f"   MCAP: {mcap}"
            )
        text = "\n".join(lines)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_bot)
    await callback.answer()


@router.callback_query(F.data == "wallets")
async def cb_wallets(callback: CallbackQuery):
    user_id = callback.from_user.id
    tier = await repo.get_user_tier(user_id)
    is_admin = check_admin(user_id)

    wallets = await repo.get_active_wallets()
    wallets.sort(key=lambda w: w.realized_pnl_usd, reverse=True)

    if tier not in ("premium", "premium_plus") and not is_admin:
        # Free tier — show count only
        text = (
            f"🏆 <b>Топ Smart Money кошельки</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Под мониторингом: <b>{len(wallets)}</b> кошельков\n\n"
            f"🔒 Полный список с адресами, PnL и Win Rate "
            f"доступен для подписчиков <b>Premium</b>.\n\n"
            f"Используй /plan для подробностей."
        )
    else:
        lines = [
            f"🏆 <b>Топ Smart Money кошельки</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Под мониторингом: <b>{len(wallets)}</b>\n"
        ]
        for i, w in enumerate(wallets[:10]):
            addr = f"{w.address[:6]}...{w.address[-4:]}"
            pnl = w.realized_pnl_usd
            if pnl >= 1_000_000:
                pnl_str = f"${pnl/1_000_000:.1f}M"
            elif pnl >= 1_000:
                pnl_str = f"${pnl/1_000:.1f}K"
            else:
                pnl_str = f"${pnl:,.0f}"
            wr = f"{w.win_rate:.0%}" if w.win_rate else "N/A"
            lines.append(f"{i+1}. <code>{addr}</code>  PnL: <b>{pnl_str}</b> | WR: {wr}")
        text = "\n".join(lines)

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="bot_info")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "how_it_works")
async def cb_how_it_works(callback: CallbackQuery):
    text = (
        "⚙️ <b>Как работает бот</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ <b>База кошельков</b>\n"
        "425+ проверенных кошельков Smart Money на Solana "
        "с подтверждённым PnL от тысяч до миллионов долларов.\n\n"
        "2️⃣ <b>Мониторинг 24/7</b>\n"
        "Каждый swap отслеживается в реальном времени через Helius webhooks.\n\n"
        "3️⃣ <b>Детектор совпадений</b>\n"
        "Когда 2+ кошелька покупают один токен за 30 минут — это сигнал.\n\n"
        "4️⃣ <b>Доставка алертов</b>\n"
        "🆓 Free — сигнал с задержкой, базовая информация.\n"
        "💎 Premium — мгновенно, полная аналитика + Accumulation Score.\n\n"
        "<b>Режимы сигналов:</b>\n"
        "📡 M1 — 2 кошелька (стандартный)\n"
        "🔥 M2 — 3+ кошелька (сильный сигнал)\n\n"
        "<i>Не является финансовой рекомендацией. DYOR.</i>"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="bot_info")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "premium")
async def cb_premium(callback: CallbackQuery):
    text = (
        "💎 <b>Подписка на бот</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆓 <b>Free (бесплатно):</b>\n"
        "— Мониторинг 425+ кошельков Smart Money\n"
        "— Сигналы с задержкой\n"
        "— Базовая информация: символ, MCAP, ссылки\n"
        "— Первые 2 модуля курса\n\n"
        f"💎 <b>Premium — {PREMIUM_PRICE_SOL} SOL/мес:</b>\n"
        "— Мгновенные сигналы без задержки\n"
        "— Адреса кошельков, PnL, ROI, тип\n"
        "— Accumulation Score (0-100) в каждом сигнале\n"
        "— Sell Alerts — когда Smart Money продают\n"
        "— Фильтры под свой стиль торговли\n"
        "— /analyze — анализ любого токена\n"
        "— Недельные отчёты Smart Money\n\n"
        f"Отправь <b>{PREMIUM_PRICE_SOL} SOL</b> на:\n"
        f"<code>{PAYMENT_WALLET}</code>\n\n"
        f"Затем: <code>/subscribe premium TX_SIGNATURE</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=premium_keyboard())
    await callback.answer()


@router.callback_query(F.data == "my_plan")
async def cb_my_plan(callback: CallbackQuery):
    await _send_my_plan_cb(callback)


@router.callback_query(F.data == "referral")
async def cb_referral(callback: CallbackQuery):
    user_id = callback.from_user.id
    ref_code = await repo.get_referral_code(user_id)
    if not ref_code:
        await repo.upsert_subscriber(
            user_id=user_id,
            username=callback.from_user.username or "",
            first_name=callback.from_user.first_name or "",
        )
        ref_code = await repo.get_referral_code(user_id)

    stats = await repo.get_referral_stats(user_id)
    paid = stats.get("paid_referrals", 0)
    link = f"https://t.me/wayan_pirat_bot?start=ref_{ref_code}"

    if paid >= 3:
        your_bonus = "🎁 Комьюнити <b>бесплатно</b>"
    elif paid >= 2:
        your_bonus = "🎁 Скидка <b>50%</b> на комьюнити"
    elif paid >= 1:
        your_bonus = "🎁 Скидка <b>20%</b> на комьюнити"
    else:
        your_bonus = "Пока нет бонусов — приглашай друзей!"

    text = (
        "🤝 <b>Реферальная программа</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вход в курс — 400$.\n"
        "Приводи друзей и получай бонусы:\n\n"
        "— 1 друг → другу <b>-20%</b> на курс, тебе <b>-20%</b> на комьюнити\n"
        "— 2 друга → друзьям <b>-20%</b>, тебе <b>-50%</b> на комьюнити\n"
        "— 3 друга → друзьям <b>-20%</b>, тебе <b>бесплатный</b> вход в комьюнити\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"<b>Статистика:</b>\n"
        f"👥 Приглашено: <b>{stats['total_referrals']}</b>\n"
        f"💰 Купили курс: <b>{paid}</b>\n"
        f"{your_bonus}\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "buy_premium")
async def cb_buy_premium(callback: CallbackQuery):
    text = (
        f"💎 <b>Оформить Premium</b>\n\n"
        f"💰 Цена: <b>{PREMIUM_PRICE_SOL} SOL/мес</b>\n\n"
        f"1️⃣ Отправь <b>{PREMIUM_PRICE_SOL} SOL</b> на:\n"
        f"<code>{PAYMENT_WALLET}</code>\n\n"
        f"2️⃣ Скопируй TX signature\n\n"
        f"3️⃣ Отправь команду:\n"
        f"<code>/subscribe premium TX_SIGNATURE</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_keyboard())
    await callback.answer()


# ============================================================
#  Helper functions
# ============================================================

async def _send_status(message: Message):
    counts = await repo.wallet_count()
    signals = await repo.get_recent_signals(24)
    from api.birdeye_client import get_sol_price
    sol_price = await get_sol_price()

    active = counts.get("ACTIVE", 0)
    text = (
        "🟢 <b>Статус</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>Работает</b> ✅\n"
        f"Кошельков: <b>{active}</b>\n"
        f"SOL: <b>${sol_price:.2f}</b>\n"
        f"Сигналов за 24ч: <b>{len(signals)}</b>\n"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())


async def _send_signals(message: Message):
    signals = await repo.get_recent_signals(24)
    if not signals:
        text = (
            "📊 <b>Сигналы (24ч)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Сигналов пока нет.\n\n"
            "<i>Сигнал появляется, когда 2+ Smart Money кошелька покупают один токен.</i>"
        )
    else:
        lines = [
            f"📊 <b>Сигналы (24ч): {len(signals)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        for s in signals[:10]:
            mode_label = "🔥 M2" if s.mode >= 2 else "📡 M1"
            total = format_usd(s.total_buy_usd)
            mcap = format_mcap(s.mcap) if s.mcap else "N/A"
            lines.append(
                f"\n{mode_label} <b>{s.token_symbol or '???'}</b>\n"
                f"   Кошельков: {s.wallet_count} | Всего: {total}\n"
                f"   MCAP: {mcap}"
            )
        text = "\n".join(lines)

    await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())


async def _send_wallets(message: Message):
    user_id = message.from_user.id
    tier = await repo.get_user_tier(user_id)
    is_admin = check_admin(user_id)

    wallets = await repo.get_active_wallets()
    wallets.sort(key=lambda w: w.realized_pnl_usd, reverse=True)

    if tier not in ("premium", "premium_plus") and not is_admin:
        text = (
            f"🏆 <b>Топ Smart Money кошельки</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Под мониторингом: <b>{len(wallets)}</b> кошельков\n\n"
            f"🔒 Полный список с адресами, PnL и Win Rate "
            f"доступен для подписчиков <b>Premium</b>.\n\n"
            f"Используй /plan для подробностей."
        )
    else:
        lines = [
            f"🏆 <b>Топ Smart Money кошельки</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Под мониторингом: <b>{len(wallets)}</b>\n"
        ]
        for i, w in enumerate(wallets[:10]):
            addr = f"{w.address[:6]}...{w.address[-4:]}"
            pnl = w.realized_pnl_usd
            if pnl >= 1_000_000:
                pnl_str = f"${pnl/1_000_000:.1f}M"
            elif pnl >= 1_000:
                pnl_str = f"${pnl/1_000:.1f}K"
            else:
                pnl_str = f"${pnl:,.0f}"
            wr = f"{w.win_rate:.0%}" if w.win_rate else "N/A"
            lines.append(f"{i+1}. <code>{addr}</code>  PnL: <b>{pnl_str}</b> | WR: {wr}")
        text = "\n".join(lines)

    await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())


async def _send_plan(message: Message):
    text = (
        "💎 <b>Тарифы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆓 <b>Free (бесплатно):</b>\n"
        "— Сигналы с задержкой\n"
        "— Базовая информация (символ, MCAP, ссылки)\n"
        "— Первые 2 модуля курса\n\n"
        f"💎 <b>Premium — {PREMIUM_PRICE_SOL} SOL/мес:</b>\n"
        "— Мгновенные сигналы\n"
        "— Полная аналитика кошельков\n"
        "— Accumulation Score\n"
        "— Sell Alerts + фильтры + недельные отчёты\n\n"
        f"Отправь <b>{PREMIUM_PRICE_SOL} SOL</b> на:\n"
        f"<code>{PAYMENT_WALLET}</code>\n"
        f"Затем: <code>/subscribe premium TX</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=premium_keyboard())


async def _send_my_plan(message: Message):
    user_id = message.from_user.id
    sub = await repo.get_subscriber(user_id)

    if not sub or sub.tier == "free" or (sub.expires_at and sub.expires_at < datetime.utcnow()):
        text = (
            "📋 <b>Твой тариф: Free</b>\n\n"
            "Оформи Premium для полного доступа.\n"
            "Используй /plan для подробностей."
        )
    else:
        expires = sub.expires_at.strftime("%Y-%m-%d %H:%M UTC") if sub.expires_at else "N/A"
        text = (
            f"📋 <b>Твой тариф: Premium</b> 💎\n\n"
            f"Действует до: <b>{expires}</b>\n\n"
            f"Для продления: отправь платёж и используй /subscribe."
        )
    await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())


async def _send_my_plan_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    sub = await repo.get_subscriber(user_id)

    if not sub or sub.tier == "free" or (sub.expires_at and sub.expires_at < datetime.utcnow()):
        text = (
            "📋 <b>Твой тариф: Free</b>\n\n"
            "Оформи Premium для полного доступа.\n"
            "Используй /plan для подробностей."
        )
    else:
        expires = sub.expires_at.strftime("%Y-%m-%d %H:%M UTC") if sub.expires_at else "N/A"
        text = (
            f"📋 <b>Твой тариф: Premium</b> 💎\n\n"
            f"Действует до: <b>{expires}</b>\n\n"
            f"Для продления: отправь платёж и используй /subscribe."
        )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_keyboard())
    await callback.answer()


# ============================================================
#  Admin: reset user for testing
# ============================================================

@router.message(Command("reset_user"))
async def cmd_reset_user(message: Message, command: CommandObject):
    """Admin only: reset user's referral and course status for testing."""
    if not check_admin(message.from_user.id):
        return
    args = (command.args or "").strip()
    if not args:
        await message.answer("Usage: /reset_user USER_ID")
        return
    try:
        target_id = int(args)
    except ValueError:
        await message.answer("Invalid user ID")
        return

    async with repo.async_session() as session:
        sub = await session.get(repo.Subscriber, target_id)
        if not sub:
            await message.answer(f"User {target_id} not found")
            return
        sub.referred_by = None
        sub.course_purchased = False
        sub.referral_credits = 0
        await session.commit()
    await message.answer(
        f"✅ User {target_id} reset:\n"
        f"- referred_by = None\n"
        f"- course_purchased = False\n"
        f"- referral_credits = 0"
    )


# ============================================================
#  Bot lifecycle
# ============================================================

async def start_polling():
    """Start the Telegram bot polling."""
    logger.info("Starting Telegram bot polling...")
    await setup_bot_profile()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


async def stop_bot():
    """Stop the bot gracefully."""
    await bot.session.close()
