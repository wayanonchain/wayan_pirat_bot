"""FastAPI webhook server to receive Helius transaction notifications."""

import hmac
import json
import logging
import os
import time
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.signal_detector import process_buy
from bot.telegram_bot import send_signal, send_message
from bot.bot_bridge import submit as submit_to_main_loop
from config.settings import (
    HELIUS_WEBHOOK_AUTH,
    WEBHOOK_SIGNALS_ENABLED,
    WEBHOOK_SELL_ALERTS_ENABLED,
)
from db import repository as repo
from api.birdeye_client import get_sol_price

logger = logging.getLogger(__name__)

app = FastAPI(title="Wayne Pirate Webhook Server")

# SOL and stablecoin addresses
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmKkdwZxEhJCGT2pPJixCjLNd3qLpJRDd"
STABLE_MINTS = {SOL_MINT, USDC_MINT, USDT_MINT}

# Cache of monitored addresses for fast lookup
_monitored_addresses: set[str] = set()

# Track tokens that had signals (for sell alerts)
_signaled_tokens: dict[str, str] = {}  # {token_address: token_symbol}

# Wall-clock of last successful webhook handling — read by /health so an
# external watchdog can tell "process up but not receiving events" apart
# from "process up and busy".
_last_event_at: float = 0.0


TRACKED_TOKENS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "tracked_tokens.json")


async def load_monitored_addresses():
    """Load active wallet addresses into memory."""
    global _monitored_addresses
    addresses = await repo.get_active_addresses()
    _monitored_addresses = set(addresses)
    logger.info(f"Loaded {len(_monitored_addresses)} monitored addresses")


def load_tracked_tokens():
    """Load manually tracked tokens from JSON file into _signaled_tokens."""
    try:
        with open(TRACKED_TOKENS_FILE, "r") as f:
            tokens = json.load(f)
        _signaled_tokens.update(tokens)
        logger.info(f"Loaded {len(tokens)} manually tracked tokens for sell alerts")
    except FileNotFoundError:
        logger.debug("No tracked_tokens.json found")
    except Exception as e:
        logger.warning(f"Error loading tracked tokens: {e}")


@app.on_event("startup")
async def startup():
    await load_monitored_addresses()
    load_tracked_tokens()
    if not HELIUS_WEBHOOK_AUTH:
        logger.warning(
            "HELIUS_WEBHOOK_AUTH is not set — /webhook/helius accepts "
            "unauthenticated requests. Set it in .env and update the Helius "
            "webhook's Authorization header to enable validation."
        )


@app.get("/health")
async def health():
    """Lightweight liveness probe. No external API calls — must answer fast
    so an external watchdog can detect a frozen event loop with a 5s timeout.
    """
    now = time.time()
    seconds_since_last_event = (
        int(now - _last_event_at) if _last_event_at else None
    )
    return {
        "status": "ok",
        "monitored_wallets": len(_monitored_addresses),
        "tracked_signal_tokens": len(_signaled_tokens),
        "last_event_at": _last_event_at or None,
        "seconds_since_last_event": seconds_since_last_event,
    }


@app.post("/webhook/helius")
async def helius_webhook(request: Request):
    """
    Receive enhanced transaction webhooks from Helius.
    Helius sends an array of enhanced transactions.
    """
    if HELIUS_WEBHOOK_AUTH:
        provided = request.headers.get("authorization", "")
        if not hmac.compare_digest(provided, HELIUS_WEBHOOK_AUTH):
            logger.warning("webhook auth mismatch from %s", request.client.host if request.client else "?")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # Helius sends array of transactions
    transactions = payload if isinstance(payload, list) else [payload]

    from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError

    processed = 0
    for tx in transactions:
        try:
            await process_transaction(tx)
            processed += 1
        except (OperationalError, SATimeoutError) as e:
            # Transient DB pressure — 'database is locked' past busy_timeout
            # or pool exhaustion under burst. Both self-heal once
            # concurrency drops; a lost buy is replayed by the next event
            # and costs us nothing (accumulation discovery runs on hours).
            logger.warning(f"DB under pressure, dropping tx: {e}")
        except Exception as e:
            logger.error(f"Error processing tx: {e}", exc_info=True)

    global _last_event_at
    _last_event_at = time.time()
    return {"processed": processed}


async def process_transaction(tx: dict):
    """Process a single enhanced transaction from Helius webhook."""
    tx_type = tx.get("type", "")
    if tx_type != "SWAP":
        return

    fee_payer = tx.get("feePayer", "")
    signature = tx.get("signature", "")

    # Check if this wallet is one we're monitoring
    if fee_payer not in _monitored_addresses:
        account_data = tx.get("accountData", [])
        matching_wallet = None
        for ad in account_data:
            if ad.get("account", "") in _monitored_addresses:
                matching_wallet = ad["account"]
                break
        if not matching_wallet:
            return
        fee_payer = matching_wallet

    # Parse the swap
    events = tx.get("events", {})
    swap = events.get("swap", {})
    if not swap:
        token_transfers = tx.get("tokenTransfers", [])
        native_transfers = tx.get("nativeTransfers", [])
        swap_info = await parse_from_transfers(fee_payer, token_transfers, native_transfers)
        if not swap_info:
            return
    else:
        swap_info = await parse_from_swap_event(fee_payer, swap)
        if not swap_info:
            return

    # Sell alerts are a legacy feature — gated OFF by default along with
    # the live signals. The accumulation module doesn't use this path.
    if swap_info.get("is_sell"):
        if WEBHOOK_SELL_ALERTS_ENABLED and swap_info["token_address"] in _signaled_tokens:
            await _handle_sell_alert(fee_payer, swap_info, signature)
        return

    token_address = swap_info["token_address"]
    token_symbol = swap_info.get("token_symbol", "")

    logger.info(f"Buy detected: {fee_payer[:8]}... bought {token_symbol} "
                f"({token_address[:8]}...) for ${swap_info['amount_usd']:.2f}")

    # record_buy still runs inside process_buy — the accumulation module
    # reads token_buys regardless of whether we alert. The alert path
    # (send_signal → admin DM + subscribers + log-chat mirror) is gated:
    # when WEBHOOK_SIGNALS_ENABLED is off we skip signal creation AND
    # alerting so no Telegram traffic is generated.
    if not WEBHOOK_SIGNALS_ENABLED:
        # Just persist the buy for the accumulation-module pipeline.
        from sqlalchemy.exc import OperationalError
        try:
            await repo.record_buy({
                "wallet_address": fee_payer,
                "token_address": token_address,
                "token_symbol": token_symbol,
                "amount_usd": swap_info["amount_usd"],
                "amount_token": swap_info.get("amount_token", 0),
                "amount_sol": swap_info.get("amount_sol", 0),
                "tx_signature": signature,
                "timestamp": datetime.utcnow(),
                "mcap_at_buy": swap_info.get("mcap"),
            })
        except OperationalError as e:
            logger.warning(f"DB busy recording buy: {e}")
        return

    # Process through signal detector
    signal = await process_buy(
        wallet_address=fee_payer,
        token_address=token_address,
        token_symbol=token_symbol,
        amount_usd=swap_info["amount_usd"],
        amount_sol=swap_info.get("amount_sol", 0),
        amount_token=swap_info.get("amount_token", 0),
        tx_signature=signature,
        mcap=swap_info.get("mcap"),
    )

    if signal:
        # Track for sell alerts
        _signaled_tokens[token_address] = signal.get("token_symbol", "")

        # Run Accumulation Score for premium+ subscribers
        try:
            from core.token_analyzer import analyze_token
            score_result = await analyze_token(token_address)
            if score_result:
                signal["accumulation_score"] = score_result
        except Exception as e:
            logger.warning(f"Accumulation Score error: {e}")

        # send_signal uses the aiogram Bot (aiohttp session) whose loop is the
        # main one. We're running in uvicorn's loop here, so hop over.
        submit_to_main_loop(send_signal(signal))


async def _handle_sell_alert(wallet: str, swap_info: dict, signature: str):
    """Send sell alert when SM wallet sells a previously signaled token.

    Kicks off the fanout on the main loop (the one the Bot session lives on).
    """
    token_address = swap_info["token_address"]
    token_symbol = _signaled_tokens.get(token_address, swap_info.get("token_symbol", "???"))
    amount_usd = swap_info.get("amount_usd", 0)
    amount_sol = swap_info.get("amount_sol", 0)

    submit_to_main_loop(_dispatch_sell_alert(
        wallet, token_address, token_symbol, amount_usd, amount_sol, signature
    ))


async def _dispatch_sell_alert(wallet: str, token_address: str, token_symbol: str,
                               amount_usd: float, amount_sol: float, signature: str):
    """Runs on the main loop — safe to use the Bot session here."""
    short_addr = f"{wallet[:6]}...{wallet[-4:]}"

    subscribers = await repo.get_active_subscriber_ids()
    premium_plus_users = [uid for uid, tier in subscribers.items() if tier in ("premium", "premium_plus")]

    if not premium_plus_users:
        return

    from bot.formatters import format_usd
    from bot.telegram_bot import bot
    from config.settings import TELEGRAM_CHAT_ID

    text = (
        f"\u26a0\ufe0f <b>Smart Money EXIT</b>\n\n"
        f"Token: <b>{token_symbol}</b>\n"
        f"<code>{token_address}</code>\n\n"
        f"Wallet: <code>{short_addr}</code>\n"
        f"Sold for: {format_usd(amount_usd)} ({amount_sol:.2f} SOL)\n\n"
        f'<a href="https://solscan.io/tx/{signature}">View TX</a>'
    )

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML",
                               disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"Sell alert admin send failed: {e}")

    sent_to = []
    for user_id in premium_plus_users:
        if str(user_id) == str(TELEGRAM_CHAT_ID):
            continue
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML",
                                   disable_web_page_preview=True)
            sub = await repo.get_subscriber(user_id)
            name = (sub.first_name or sub.username or str(user_id)) if sub else str(user_id)
            sent_to.append(name)
        except Exception as e:
            logger.warning(f"Failed to send sell alert to {user_id}: {e}")

    if sent_to:
        from bot.activity_log import _send_log
        recipients = ", ".join(sent_to)
        await _send_log(
            f"⚠️ <b>Sell alert: {token_symbol}</b>\n"
            f"   Wallet: <code>{short_addr}</code>\n"
            f"   Sent to: Admin + {len(sent_to)} subscriber(s)\n"
            f"   ({recipients})"
        )


async def parse_from_swap_event(wallet: str, swap: dict) -> dict | None:
    """Parse buy/sell info from Helius swap event."""
    token_inputs = swap.get("tokenInputs", [])
    token_outputs = swap.get("tokenOutputs", [])
    native_input = swap.get("nativeInput", {})
    native_output = swap.get("nativeOutput", {})

    # Determine direction
    # BUY: SOL/USDC in → Token out
    # SELL: Token in → SOL/USDC out

    # Check for BUY: non-stable token in outputs
    bought_token = None
    for out in token_outputs:
        mint = out.get("mint", "")
        if mint not in STABLE_MINTS:
            bought_token = out
            break

    # Check for SELL: non-stable token in inputs
    sold_token = None
    for inp in token_inputs:
        mint = inp.get("mint", "")
        if mint not in STABLE_MINTS:
            sold_token = inp
            break

    # If we have a bought token and no sold token (or sold token is stable) → BUY
    if bought_token and not sold_token:
        sol_spent = 0
        if native_input:
            sol_spent = int(native_input.get("amount", 0)) / 1e9

        sol_price = await get_sol_price()
        amount_usd = sol_spent * sol_price

        for inp in token_inputs:
            if inp.get("mint") == USDC_MINT:
                amount_usd = float(inp.get("rawTokenAmount", {}).get("tokenAmount", 0)) / 1e6
            elif inp.get("mint") == USDT_MINT:
                amount_usd = float(inp.get("rawTokenAmount", {}).get("tokenAmount", 0)) / 1e6

        raw_amount = float(bought_token.get("rawTokenAmount", {}).get("tokenAmount", "0"))
        decimals = int(bought_token.get("rawTokenAmount", {}).get("decimals", 9))
        token_amount = raw_amount / (10 ** decimals) if decimals > 0 else raw_amount

        return {
            "token_address": bought_token["mint"],
            "token_symbol": bought_token.get("tokenStandard", ""),
            "amount_usd": amount_usd,
            "amount_sol": sol_spent,
            "amount_token": token_amount,
            "is_sell": False,
        }

    # If sold token and no bought non-stable → SELL
    if sold_token and not bought_token:
        sol_received = 0
        if native_output:
            sol_received = int(native_output.get("amount", 0)) / 1e9

        sol_price = await get_sol_price()
        amount_usd = sol_received * sol_price

        return {
            "token_address": sold_token["mint"],
            "token_symbol": sold_token.get("tokenStandard", ""),
            "amount_usd": amount_usd,
            "amount_sol": sol_received,
            "amount_token": 0,
            "is_sell": True,
        }

    # Fallback: if both exist, treat as BUY (token swap)
    if bought_token:
        sol_spent = 0
        if native_input:
            sol_spent = int(native_input.get("amount", 0)) / 1e9

        sol_price = await get_sol_price()
        amount_usd = sol_spent * sol_price

        raw_amount = float(bought_token.get("rawTokenAmount", {}).get("tokenAmount", "0"))
        decimals = int(bought_token.get("rawTokenAmount", {}).get("decimals", 9))
        token_amount = raw_amount / (10 ** decimals) if decimals > 0 else raw_amount

        return {
            "token_address": bought_token["mint"],
            "token_symbol": "",
            "amount_usd": amount_usd,
            "amount_sol": sol_spent,
            "amount_token": token_amount,
            "is_sell": False,
        }

    return None


async def parse_from_transfers(wallet: str, token_transfers: list, native_transfers: list) -> dict | None:
    """Parse buy from raw transfer data when swap event is missing."""
    # Find outgoing SOL (we're spending SOL to buy)
    sol_spent = 0
    for nt in native_transfers:
        if nt.get("fromUserAccount") == wallet:
            sol_spent += int(nt.get("amount", 0)) / 1e9

    sol_received = 0
    for nt in native_transfers:
        if nt.get("toUserAccount") == wallet:
            sol_received += int(nt.get("amount", 0)) / 1e9

    sol_price = await get_sol_price()

    if sol_spent > sol_received:
        # BUY: spending SOL
        for tt in token_transfers:
            if tt.get("toUserAccount") == wallet:
                mint = tt.get("mint", "")
                if mint and mint not in STABLE_MINTS:
                    return {
                        "token_address": mint,
                        "token_symbol": "",
                        "amount_usd": sol_spent * sol_price,
                        "amount_sol": sol_spent,
                        "amount_token": tt.get("tokenAmount", 0),
                        "is_sell": False,
                    }
    elif sol_received > sol_spent:
        # SELL: receiving SOL
        for tt in token_transfers:
            if tt.get("fromUserAccount") == wallet:
                mint = tt.get("mint", "")
                if mint and mint not in STABLE_MINTS:
                    return {
                        "token_address": mint,
                        "token_symbol": "",
                        "amount_usd": sol_received * sol_price,
                        "amount_sol": sol_received,
                        "amount_token": tt.get("tokenAmount", 0),
                        "is_sell": True,
                    }

    return None
