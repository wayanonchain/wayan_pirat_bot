"""
Token Analysis CLI — кидаешь контракт, получаешь полный анализ.

Usage:
    python analyze.py <contract_address>
    python analyze.py <contract_address> --chain solana
    python analyze.py <contract_address> --chain base --send   # + отправить в Telegram
    python analyze.py <contract_address> --json                # вывод в JSON
"""
import asyncio
import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from token_analyzer import TokenAnalyzer
from report import format_full_report
from agent_alerts import TelegramSender

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("analyze")


CHAIN_ALIASES = {
    "sol":      "solana",
    "eth":      "ethereum",
    "evm":      "ethereum",
    "base":     "base",
    "bsc":      "bsc",
    "bnb":      "bsc",
    "arb":      "arbitrum",
    "poly":     "polygon",
    "matic":    "polygon",
}


async def run(address: str, chain: str, send_telegram: bool, as_json: bool, verbose: bool):
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    chain = CHAIN_ALIASES.get(chain.lower(), chain.lower())

    analyzer = TokenAnalyzer()
    try:
        analysis = await analyzer.analyze(address, chain)
    finally:
        await analyzer.close()

    if as_json:
        # Simple JSON summary
        t  = analysis.token
        n  = analysis.narrative
        sm = analysis.smart_money
        r  = analysis.risk
        data = {
            "symbol": t.symbol,
            "name": t.name,
            "chain": t.chain,
            "mcap": t.mcap,
            "liquidity": t.liquidity_usd,
            "age_days": round(t.age_days, 1),
            "narrative": n.name,
            "narrative_confidence": n.confidence,
            "competitors": n.competitors[:5],
            "smart_money_confidence": sm.confidence,
            "buy_sell_ratio": round(sm.buy_sell_ratio, 2),
            "whale_activity": sm.whale_activity,
            "risk": r.overall_risk,
            "risk_score": r.risk_score,
            "verdict": analysis.buy_verdict,
            "score": analysis.buy_score,
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # Format and print report
    report = format_full_report(analysis)

    # Strip HTML tags for terminal output
    import re
    terminal_report = re.sub(r"<[^>]+>", "", report)
    print("\n" + "=" * 65)
    print(terminal_report)
    print("=" * 65 + "\n")

    # Send to Telegram if requested
    if send_telegram:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("⚠️  Telegram не настроен. Скопируй .env.example в .env")
            return
        sender = TelegramSender(bot_token, chat_id)
        # Split report if too long (Telegram limit 4096 chars)
        if len(report) <= 4096:
            await sender.send(report)
        else:
            # Send in chunks at natural break points
            chunks = _split_message(report, 4096)
            for chunk in chunks:
                await sender.send(chunk)
                await asyncio.sleep(0.3)
        print(f"✅ Отправлено в Telegram (@chat {chat_id})")


def _split_message(text: str, max_len: int) -> list[str]:
    """Split at paragraph boundaries."""
    parts  = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len and current:
            parts.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current.strip())
    return parts


def main():
    parser = argparse.ArgumentParser(
        description="Анализ токена по контракту",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python analyze.py EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  python analyze.py 0x1234... --chain base --send
  python analyze.py ADDR --chain eth --json
        """,
    )
    parser.add_argument("address", help="Адрес контракта токена")
    parser.add_argument("--chain", default="auto",
                        help="Блокчейн: solana, ethereum, base, bsc, arbitrum (default: auto)")
    parser.add_argument("--send", action="store_true",
                        help="Отправить отчёт в Telegram")
    parser.add_argument("--json", action="store_true",
                        help="Вывод в JSON формате")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Подробный лог")
    args = parser.parse_args()

    asyncio.run(run(
        address=args.address,
        chain=args.chain,
        send_telegram=args.send,
        as_json=args.json,
        verbose=args.verbose,
    ))


if __name__ == "__main__":
    main()
