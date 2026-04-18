"""
Drop-in aiogram 3 router for WAYNE_PIRATE — exposes /analyze and /watch.

How to wire into the existing bot (bot/telegram_bot.py):

    from bot.analyze_agent.wayan_handler import analyze_router
    dp.include_router(analyze_router)

Expected layout inside WAYNE_PIRATE:

    solana-smart-money-bot/
        bot/
            analyze_agent/     <-- copy of this agent/ folder
                wayan_handler.py
                token_analyzer.py
                detector.py
                ...

The handler is deliberately self-contained: it uses its own TokenAnalyzer
instance and does not rely on the bot's repo layer, so wiring is one line.
"""
import logging
import re
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from token_analyzer import TokenAnalyzer
from report import format_full_report

log = logging.getLogger(__name__)

analyze_router = Router(name="analyze_agent")

# Solana mint = base58 32..44 chars; EVM = 0x + 40 hex. Broad regex that
# only exists to keep /analyze from treating any stray text as an address.
_ADDR_RE = re.compile(r"^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def _extract_address(text: str) -> Optional[str]:
    for tok in text.split():
        if _ADDR_RE.match(tok):
            return tok
    return None


@analyze_router.message(Command("analyze"))
async def cmd_analyze(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    address = _extract_address(arg) if arg else None
    if not address:
        await message.reply(
            "Использование: /analyze <адрес контракта>\n\n"
            "Поддерживается Solana и EVM сети (Base, Ethereum, BSC, Arbitrum)."
        )
        return

    status = await message.reply("🔎 Анализирую токен... это займёт 10–20 секунд")
    analyzer = TokenAnalyzer()
    try:
        analysis = await analyzer.analyze(address, chain="auto")
        report = format_full_report(analysis)
    except ValueError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        log.exception("analyze failed for %s", address)
        await status.edit_text(f"❌ Ошибка анализа: {e}")
        return
    finally:
        await analyzer.close()

    # Telegram 4096-char hard limit — split at blank lines.
    if len(report) <= 4000:
        await status.edit_text(report, parse_mode="HTML", disable_web_page_preview=True)
        return

    chunks = _split(report, 4000)
    await status.edit_text(chunks[0], parse_mode="HTML", disable_web_page_preview=True)
    for chunk in chunks[1:]:
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


def _split(text: str, max_len: int) -> list[str]:
    """Split a long HTML message at paragraph boundaries so we don't tear
    an opening tag apart from its closing tag. Tags we emit are all
    single-line (<b>, <code>, <a>), so a linebreak split is safe.
    """
    parts: list[str] = []
    cur = ""
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
# Optional: per-user watchlist. Stored in SQLite via the bot's own db/repo.
# The skeleton below assumes a table `user_watchlist(user_id, address, chain,
# created_at)` exists. Left commented so the router can be imported without
# forcing a schema migration — uncomment + wire once the table lands.
# ─────────────────────────────────────────────────────────────────────────

# @analyze_router.message(Command("watch"))
# async def cmd_watch(message: Message, command: CommandObject):
#     address = _extract_address((command.args or "").strip())
#     if not address:
#         await message.reply("Использование: /watch <адрес>")
#         return
#     # repo.add_user_watch(message.from_user.id, address, chain="auto")
#     await message.reply(f"✅ Добавлен в watchlist: <code>{address}</code>",
#                         parse_mode="HTML")
