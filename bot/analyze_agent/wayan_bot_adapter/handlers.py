"""
Aiogram 3 router exposing accumulation commands:

    /scan <contract>       — one-shot deep analysis (any user)
    /acc                   — show current watchlist state (admin)
    /acc_add <contract>    — manually enrol a token (admin)
    /acc_remove <contract> — remove a token (admin)
    /acc_discover          — force-run SM discovery now (admin)
    /acc_scan              — force-run the accumulation monitor now (admin)

Wiring:

    from bot.analyze_agent.wayan_bot_adapter.handlers import acc_router
    dp.include_router(acc_router)
"""
import logging
import re
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from token_analyzer import TokenAnalyzer
from report import format_full_report

from . import repository as repo
from . import discovery
from . import monitor

log = logging.getLogger(__name__)

acc_router = Router(name="accumulation")

_ADDR_RE = re.compile(r"^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def _extract_address(text: str) -> Optional[str]:
    for tok in (text or "").split():
        if _ADDR_RE.match(tok):
            return tok
    return None


def _is_admin(message: Message) -> bool:
    try:
        from config.settings import ADMIN_IDS
    except Exception:
        return False
    uid = str(message.from_user.id) if message.from_user else ""
    return uid in ADMIN_IDS


# ─────────────────────────────────────────────────────────────────────────
# /analyze — available to everyone (the public product surface)
# ─────────────────────────────────────────────────────────────────────────

@acc_router.message(Command("scan"))
async def cmd_analyze(message: Message, command: CommandObject):
    address = _extract_address(command.args or "")
    if not address:
        await message.reply(
            "Использование: <code>/scan &lt;адрес контракта&gt;</code>\n\n"
            "Поддержка: Solana, Ethereum, Base, BSC, Arbitrum.",
            parse_mode="HTML",
        )
        return

    status = await message.reply("🔎 Анализирую токен... 10–20 секунд")
    analyzer = TokenAnalyzer()
    try:
        analysis = await analyzer.analyze(address, chain="auto")
        text = format_full_report(analysis)
    except ValueError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        log.exception("analyze failed for %s", address)
        await status.edit_text(f"❌ Ошибка анализа: {e}")
        return
    finally:
        await analyzer.close()

    if len(text) <= 4000:
        await status.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
        return
    chunks = _split(text, 4000)
    await status.edit_text(chunks[0], parse_mode="HTML", disable_web_page_preview=True)
    for chunk in chunks[1:]:
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


def _split(text: str, max_len: int) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > max_len and cur:
            parts.append(cur.rstrip())
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


# ─────────────────────────────────────────────────────────────────────────
# /acc — admin-only watchlist inspection
# ─────────────────────────────────────────────────────────────────────────

@acc_router.message(Command("acc"))
async def cmd_acc_list(message: Message):
    if not _is_admin(message):
        return
    entries = await repo.list_active_watchlist()
    if not entries:
        await message.reply("📭 Watchlist пуст.")
        return

    lines = [f"📋 <b>Accumulation watchlist</b> — {len(entries)} токенов", ""]
    for e in entries[:30]:
        tier = e.last_tier or "—"
        score = f"{e.last_score:.0f}" if e.last_score is not None else "—"
        src = {"sm_discovery": "🔍", "manual": "✋", "narrative": "📰"}.get(e.added_source, "?")
        label = (e.symbol or e.token_address[:6] + "…")
        lines.append(
            f"{src} <b>${label}</b> — score {score} / {tier}"
            + (f"  ({e.sm_wallets_at_add} SM)" if e.sm_wallets_at_add else "")
        )
    if len(entries) > 30:
        lines.append(f"\n… и ещё {len(entries)-30}")
    await message.reply("\n".join(lines), parse_mode="HTML")


@acc_router.message(Command("acc_add"))
async def cmd_acc_add(message: Message, command: CommandObject):
    if not _is_admin(message):
        return
    address = _extract_address(command.args or "")
    if not address:
        await message.reply("Использование: <code>/acc_add &lt;адрес&gt;</code>", parse_mode="HTML")
        return
    added = await repo.add_to_watchlist(
        address,
        chain="solana",
        added_source="manual",
        added_by_user_id=message.from_user.id if message.from_user else None,
    )
    if added:
        await message.reply(f"✅ Добавлен в watchlist: <code>{address}</code>", parse_mode="HTML")
    else:
        await message.reply("⚠️ Уже есть в watchlist.")


@acc_router.message(Command("acc_remove"))
async def cmd_acc_remove(message: Message, command: CommandObject):
    if not _is_admin(message):
        return
    address = _extract_address(command.args or "")
    if not address:
        await message.reply("Использование: <code>/acc_remove &lt;адрес&gt;</code>", parse_mode="HTML")
        return
    await repo.remove_from_watchlist(address, reason=f"manual by {message.from_user.id}")
    await message.reply(f"🗑 Удалён: <code>{address}</code>", parse_mode="HTML")


@acc_router.message(Command("acc_discover"))
async def cmd_acc_discover(message: Message):
    if not _is_admin(message):
        return
    status = await message.reply("🔍 Запускаю SM-discovery...")
    try:
        result = await discovery.run_discovery(
            window_hours=72, min_unique_wallets=2, min_total_usd=2_000,
        )
    except Exception as e:
        log.exception("discovery failed")
        await status.edit_text(f"❌ Ошибка: {e}")
        return

    top_lines = "\n".join(
        f"• ${c.get('symbol') or c['token_address'][:6]+'…'} "
        f"— {c['n_wallets']} SM на ${c['total_usd']:,.0f}"
        for c in (result.get("top") or [])
    )
    await status.edit_text(
        f"✅ Discovery complete\n\n"
        f"Кандидатов: <b>{result['candidates_found']}</b>\n"
        f"Добавлено: <b>{result['inserted']}</b>\n"
        f"Уже в watchlist: {result['already_watched']}\n\n"
        + (f"<b>Топ-5:</b>\n{top_lines}" if top_lines else ""),
        parse_mode="HTML",
    )


@acc_router.message(Command("acc_scan"))
async def cmd_acc_scan(message: Message):
    if not _is_admin(message):
        return
    status = await message.reply("🔄 Запускаю monitor pass...")
    try:
        # Monitor uses its own alerter (the scheduler-installed one).
        # For an interactive trigger we don't attach one — alerts are still
        # persisted and will ship on the next scheduled run.
        result = await monitor.run_monitor_once(alerter=None)
    except Exception as e:
        log.exception("scan failed")
        await status.edit_text(f"❌ Ошибка: {e}")
        return
    await status.edit_text(
        f"✅ Monitor complete\n\n"
        f"Обработано: <b>{result['count']}</b>\n"
        f"Сигналов: <b>{result.get('fired', 0)}</b>",
        parse_mode="HTML",
    )
