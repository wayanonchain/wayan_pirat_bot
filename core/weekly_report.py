"""
Weekly Smart Money Report — Dune Analytics + Nansen.
Runs once a week (Sunday 20:00 MSK) via scheduler.
Sends report to admin + all Premium+ subscribers.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

from dune_client.client import DuneClient

from config.settings import DUNE_API_KEY, NANSEN_API_KEY

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


class DuneReporter:
    """Dune Analytics queries for weekly report."""

    def __init__(self):
        self.client = DuneClient(api_key=DUNE_API_KEY) if DUNE_API_KEY else None
        self._last_request_time = 0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < 2.0:
            time.sleep(2.0 - elapsed)
        self._last_request_time = time.time()

    def _run_sql(self, sql: str) -> list[dict] | None:
        if not self.client:
            return None
        self._throttle()
        try:
            result = self.client.run_sql(sql)
            if result.result and result.result.rows:
                return result.result.rows
            return []
        except Exception as e:
            logger.error(f"Dune SQL error: {e}")
            return None

    def get_weekly_top_tokens(self) -> list[dict] | None:
        """Top tokens by SM buy volume in last 7 days."""
        return self._run_sql("""
WITH token_trades AS (
    SELECT
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN token_bought_mint_address
            ELSE token_sold_mint_address
        END as token_address,
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN token_bought_symbol
            ELSE token_sold_symbol
        END as symbol,
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN 'buy'
            ELSE 'sell'
        END as side,
        amount_usd,
        trader_id
    FROM dex_solana.trades
    WHERE block_time > NOW() - INTERVAL '7' DAY
        AND amount_usd > 50
        AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
             OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
        AND token_bought_mint_address != token_sold_mint_address
)
SELECT
    token_address,
    symbol,
    SUM(CASE WHEN side = 'buy' THEN amount_usd ELSE 0 END) as buy_volume,
    SUM(CASE WHEN side = 'sell' THEN amount_usd ELSE 0 END) as sell_volume,
    COUNT(DISTINCT CASE WHEN side = 'buy' THEN trader_id END) as unique_buyers,
    COUNT(DISTINCT CASE WHEN side = 'sell' THEN trader_id END) as unique_sellers,
    SUM(CASE WHEN side = 'buy' AND amount_usd > 1000 THEN amount_usd ELSE 0 END) as whale_buy_vol,
    COUNT(DISTINCT CASE WHEN side = 'buy' AND amount_usd > 1000 THEN trader_id END) as whale_buyers
FROM token_trades
WHERE symbol IS NOT NULL
    AND symbol NOT IN ('SOL', 'WSOL', 'USDC', 'USDT', 'USDS', 'WBTC', 'WETH')
    AND token_address != 'So11111111111111111111111111111111111111112'
GROUP BY token_address, symbol
HAVING COUNT(DISTINCT trader_id) > 100
    AND SUM(amount_usd) > 100000
ORDER BY whale_buy_vol DESC
LIMIT 15
""")

    def get_weekly_smart_wallets(self) -> list[dict] | None:
        """Wallets trading 5+ fresh tokens this week."""
        return self._run_sql("""
WITH token_trades AS (
    SELECT
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN token_bought_mint_address
            ELSE token_sold_mint_address
        END as token_address,
        CASE
            WHEN token_sold_mint_address = 'So11111111111111111111111111111111111111112'
            THEN 'buy'
            ELSE 'sell'
        END as side,
        amount_usd,
        trader_id
    FROM dex_solana.trades
    WHERE block_time > NOW() - INTERVAL '7' DAY
        AND amount_usd > 100
        AND (token_sold_mint_address = 'So11111111111111111111111111111111111111112'
             OR token_bought_mint_address = 'So11111111111111111111111111111111111111112')
)
SELECT
    trader_id as wallet,
    COUNT(DISTINCT token_address) as tokens_traded,
    SUM(CASE WHEN side = 'buy' THEN amount_usd ELSE 0 END) as total_buy,
    SUM(CASE WHEN side = 'sell' THEN amount_usd ELSE 0 END) as total_sell,
    COUNT(*) as trade_count
FROM token_trades
WHERE token_address != 'So11111111111111111111111111111111111111112'
GROUP BY trader_id
HAVING COUNT(DISTINCT token_address) >= 5
    AND SUM(CASE WHEN side = 'buy' THEN amount_usd ELSE 0 END) > 1000
ORDER BY tokens_traded DESC, total_buy DESC
LIMIT 10
""")


def _fmt_usd(val):
    if not val:
        return "$0"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.0f}"


async def generate_weekly_report() -> list[str]:
    """Generate weekly report messages. Returns list of message strings."""
    import asyncio
    dune = DuneReporter()

    ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    messages = []

    # Run Dune queries in thread (they're sync/blocking)
    loop = asyncio.get_event_loop()
    top_tokens = await loop.run_in_executor(None, dune.get_weekly_top_tokens)
    smart_wallets = await loop.run_in_executor(None, dune.get_weekly_smart_wallets)

    # --- Message 1: Top Tokens ---
    lines = [
        "\U0001f4ca <b>Weekly Smart Money Report</b>",
        "\u2500" * 30,
        f"<b>Top Tokens by Whale Volume (7 days)</b>",
        f"[{ts}]",
        "",
    ]

    if top_tokens is None:
        lines.append("<i>Dune API unavailable</i>")
    elif not top_tokens:
        lines.append("No significant tokens found this week.")
    else:
        for i, t in enumerate(top_tokens[:10], 1):
            buy = t.get('buy_volume', 0) or 0
            sell = t.get('sell_volume', 0) or 0
            ratio = buy / sell if sell > 0 else 999
            ratio_str = f"{ratio:.1f}" if ratio < 100 else ">100"
            whale_buy = t.get('whale_buy_vol', 0) or 0
            lines.append(
                f"<b>{i}. {t.get('symbol', '?')}</b> | "
                f"Buy: {_fmt_usd(buy)} | Sell: {_fmt_usd(sell)} | R: {ratio_str}"
            )
            lines.append(
                f"   Buyers: {t.get('unique_buyers', 0)} | "
                f"Whale buy: {_fmt_usd(whale_buy)} ({t.get('whale_buyers', 0)})"
            )
            lines.append(f"   <code>{t.get('token_address', '?')}</code>")
            lines.append("")

    lines.append("\U0001f3f4\u200d\u2620\ufe0f Wayne Pirate Weekly")
    messages.append("\n".join(lines))

    # --- Message 2: Smart Wallets ---
    lines2 = [
        "\U0001f4ca <b>Weekly Smart Money Report</b>",
        "\u2500" * 30,
        f"<b>Active Smart Wallets (7 days)</b>",
        f"[{ts}]",
        "",
    ]

    if smart_wallets is None:
        lines2.append("<i>Dune API unavailable</i>")
    elif not smart_wallets:
        lines2.append("No multi-token wallets found this week.")
    else:
        for i, w in enumerate(smart_wallets[:10], 1):
            wallet = str(w.get('wallet', '?'))
            short = f"{wallet[:8]}...{wallet[-4:]}" if len(wallet) > 12 else wallet
            buy = w.get('total_buy', 0) or 0
            sell = w.get('total_sell', 0) or 0
            lines2.append(
                f"<b>{i}.</b> <code>{short}</code>"
            )
            lines2.append(
                f"   Tokens: {w.get('tokens_traded', 0)} | "
                f"Buy: {_fmt_usd(buy)} | Sell: {_fmt_usd(sell)} | "
                f"Trades: {w.get('trade_count', 0)}"
            )
            lines2.append(f"   <code>{wallet}</code>")
            lines2.append("")

    lines2.append("\U0001f3f4\u200d\u2620\ufe0f Wayne Pirate Weekly")
    messages.append("\n".join(lines2))

    return messages


async def send_weekly_report():
    """Generate and send weekly report to admin + Premium+ subscribers."""
    from bot.telegram_bot import bot
    from config.settings import TELEGRAM_CHAT_ID
    from db import repository as repo

    logger.info("Generating weekly report...")

    try:
        messages = await generate_weekly_report()
    except Exception as e:
        logger.error(f"Weekly report generation failed: {e}")
        return

    # Send to admin
    for msg in messages:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to send weekly report to admin: {e}")

    # Send to Premium subscribers
    premium_subs = await repo.get_all_subscribers_by_tier("premium")
    premium_plus_subs = await repo.get_all_subscribers_by_tier("premium_plus")
    all_premium = premium_subs + premium_plus_subs
    for sub in all_premium:
        if str(sub.user_id) == str(TELEGRAM_CHAT_ID):
            continue
        for msg in messages:
            try:
                await bot.send_message(
                    chat_id=sub.user_id,
                    text=msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send weekly report to {sub.user_id}: {e}")

    logger.info(f"Weekly report sent to admin + {len(all_premium)} premium subscribers")

    from bot.activity_log import _send_log
    names = [s.first_name or s.username or str(s.user_id) for s in all_premium
             if str(s.user_id) != str(TELEGRAM_CHAT_ID)]
    recipients = ", ".join(names) if names else "none"
    await _send_log(
        f"<b>Weekly Report sent</b>\n"
        f"   To: Admin + {len(names)} Premium subscriber(s)\n"
        f"   ({recipients})"
    )
