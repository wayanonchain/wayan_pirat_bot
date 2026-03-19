"""
Publish free course lessons (Modules 1-2) to the free Telegram channel.
Posts in REVERSE order so lessons appear top-to-bottom when scrolling.
Pins the table of contents at the end.
"""

import asyncio
import re
from aiogram import Bot

BOT_TOKEN = "8788584502:AAG9J5Hr1T8ZGv1nT5swgOe8IrWHbszwL2M"
CHANNEL_ID = -1003883299541

# ── Markdown → Telegram HTML converter (simplified) ──

def md_to_html(text: str) -> str:
    """Convert course markdown to Telegram HTML."""
    lines = text.strip().split("\n")
    result = []
    in_code_block = False

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                result.append("</pre>")
                in_code_block = False
            else:
                result.append("<pre>")
                in_code_block = True
            continue

        if in_code_block:
            result.append(html_escape(line))
            continue

        # Skip horizontal rules
        if line.strip() == "---":
            continue

        # Headers
        if line.startswith("## "):
            result.append(f"<b>{html_escape(line[3:])}</b>")
            continue
        if line.startswith("# "):
            result.append(f"<b>{html_escape(line[2:])}</b>")
            continue

        # Process inline formatting
        line = process_inline(line)
        result.append(line)

    if in_code_block:
        result.append("</pre>")

    return "\n".join(result)


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def process_inline(line: str) -> str:
    """Process bold, italic, code, links in a line."""
    # First escape HTML
    # But we need to handle ** and ` before escaping
    # Strategy: replace markers with placeholders, escape, then restore

    # Extract code spans first (protect from other processing)
    codes = []
    def save_code(m):
        codes.append(m.group(1))
        return f"%%CODE{len(codes)-1}%%"
    line = re.sub(r'`([^`]+)`', save_code, line)

    # Escape HTML
    line = html_escape(line)

    # Bold **text**
    line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)

    # Italic *text* (but not inside bold)
    line = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', line)

    # Restore code spans
    for i, code in enumerate(codes):
        line = line.replace(f"%%CODE{i}%%", f"<code>{html_escape(code)}</code>")

    # Checkboxes
    line = line.replace("- [ ]", "☐")
    line = line.replace("- [x]", "☑")

    return line


# ── Lesson definitions ──

LESSONS_FILE = "/Users/mac/Desktop/КУРС Wayan Onchain .md"

# (start_line, end_line) — 1-indexed, inclusive
LESSON_RANGES = [
    # Module 1
    ("1.1", "Что такое DEX и чем отличается от CEX", 21, 62),
    ("1.2", "Пулы ликвидности: как формируется цена на DEX", 65, 111),
    ("1.3", "Безопасность кошелька: как не потерять всё за одну подпись", 114, 219),
    ("1.4", "Безопасность токена: mint, freeze, honeypot, tax", 222, 364),
    # Module 2
    ("2.1", "Рабочее место трейдера: что для чего", 374, 459),
    ("2.2", "Настройка Axiom и GMGN для торговли", 462, 512),
    ("2.3", "Telegram-боты: быстрая торговля с телефона", 515, 601),
    ("2.4", "Photon, BullX и другие web-терминалы", 604, 667),
]

MODULE_HEADERS = [
    (1, "МОДУЛЬ 1. СРЕДА И БЕЗОПАСНОСТЬ",
     "Прежде чем зарабатывать — научись не терять.\nЭтот модуль проходится ДО первой сделки. Без исключений."),
    (2, "МОДУЛЬ 2. ИНСТРУМЕНТЫ",
     "Не нужно осваивать всё сразу. Начни с минимума, добавляй по мере необходимости."),
]


def read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def extract_lesson(lines: list[str], start: int, end: int) -> str:
    """Extract lesson text from file lines (1-indexed)."""
    return "".join(lines[start - 1:end])


async def send_long_message(bot: Bot, chat_id: int, text: str):
    """Send message, splitting if > 4096 chars."""
    if len(text) <= 4096:
        return await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )

    # Split by double newlines to find natural break points
    parts = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > 4000:
            if current:
                parts.append(current.strip())
            current = paragraph
        else:
            current = current + "\n\n" + paragraph if current else paragraph
    if current:
        parts.append(current.strip())

    last_msg = None
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(1)  # Rate limit
        if len(parts) > 1:
            label = f"  <i>(часть {i+1}/{len(parts)})</i>"
            if i == 0:
                part = part + "\n\n" + label
            else:
                part = label + "\n\n" + part
        last_msg = await bot.send_message(
            chat_id=chat_id, text=part,
            parse_mode="HTML", disable_web_page_preview=True,
        )
    return last_msg


async def main():
    bot = Bot(token=BOT_TOKEN)
    lines = read_lines(LESSONS_FILE)

    print(f"Read {len(lines)} lines from course file")
    print(f"Publishing to channel {CHANNEL_ID}")
    print(f"Lessons: {len(LESSON_RANGES)}")
    print()

    # Publish in REVERSE order (last lesson first)
    # So when user opens channel, first lessons are at the top

    # First: publish lessons in reverse
    all_items = []  # (type, data) — for building TOC later

    for mod_num, mod_title, mod_desc in reversed(MODULE_HEADERS):
        mod_lessons = [(n, t, s, e) for n, t, s, e in LESSON_RANGES
                       if n.startswith(f"{mod_num}.")]

        # Publish lessons in reverse within module
        for num, title, start, end in reversed(mod_lessons):
            lesson_text = extract_lesson(lines, start, end)
            html = md_to_html(lesson_text)

            print(f"Publishing Урок {num} ({len(html)} chars)...", end=" ")
            try:
                msg = await send_long_message(bot, CHANNEL_ID, html)
                print(f"OK (msg_id: {msg.message_id})")
                all_items.append(("lesson", num, title, msg.message_id))
            except Exception as e:
                print(f"FAILED: {e}")
            await asyncio.sleep(2)  # Rate limit between lessons

        # Publish module header
        header_html = (
            f"{'━' * 30}\n"
            f"<b>{mod_title}</b>\n"
            f"{'━' * 30}\n\n"
            f"<i>{mod_desc}</i>"
        )
        print(f"Publishing {mod_title}...", end=" ")
        try:
            msg = await bot.send_message(
                chat_id=CHANNEL_ID, text=header_html,
                parse_mode="HTML",
            )
            print(f"OK (msg_id: {msg.message_id})")
            all_items.append(("module", mod_num, mod_title, msg.message_id))
        except Exception as e:
            print(f"FAILED: {e}")
        await asyncio.sleep(2)

    # Reverse items to get correct order (top to bottom)
    all_items.reverse()

    # Build and publish TOC (Table of Contents)
    toc_lines = [
        "<b>📚 КУРС WAYAN ONCHAIN — БЕСПЛАТНАЯ ЧАСТЬ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>Модули 1-2 (8 уроков)</b>",
        "",
    ]

    current_module = None
    for item_type, *data in all_items:
        if item_type == "module":
            mod_num, mod_title, _ = data
            toc_lines.append(f"\n<b>{mod_title}</b>")
            current_module = mod_num
        elif item_type == "lesson":
            num, title, _ = data
            toc_lines.append(f"  • Урок {num} — {title}")

    toc_lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "💎 <b>Хочешь полный курс?</b>",
        "Модули 3-7 (ещё 28 уроков) + приложения",
        "→ @Wayan_pirate_bot → Курс → Полный курс",
        "",
        "⬇️ <b>Листай вниз, чтобы начать обучение</b>",
    ])

    toc_text = "\n".join(toc_lines)
    print(f"\nPublishing TOC ({len(toc_text)} chars)...", end=" ")
    try:
        toc_msg = await bot.send_message(
            chat_id=CHANNEL_ID, text=toc_text, parse_mode="HTML",
        )
        print(f"OK (msg_id: {toc_msg.message_id})")

        # Pin the TOC
        await bot.pin_chat_message(
            chat_id=CHANNEL_ID,
            message_id=toc_msg.message_id,
            disable_notification=True,
        )
        print("TOC pinned!")
    except Exception as e:
        print(f"FAILED: {e}")

    print("\n✅ Done! Free course published.")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
