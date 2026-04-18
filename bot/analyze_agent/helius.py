"""
Helius client — replaces the tx_hash[:10] wallet proxy in smart_money.py
with real Solana wallet addresses via parsed transactions.

Usage:
    from helius import fetch_pool_trades_helius
    trades = await fetch_pool_trades_helius(pool_address, client, limit=100)

Requires HELIUS_API_KEY in env. Gracefully returns [] when missing so the
flow can fall back to the GeckoTerminal-based path.
"""
import logging
import os
from typing import Optional
import httpx

from smart_money import TradeRecord, _parse_ts

log = logging.getLogger(__name__)

HELIUS_RPC_TMPL    = "https://mainnet.helius-rpc.com/?api-key={key}"
HELIUS_PARSE_TMPL  = "https://api.helius.xyz/v0/transactions/?api-key={key}"
HELIUS_HISTORY_TMPL = "https://api.helius.xyz/v0/addresses/{addr}/transactions"

# Known DEX program IDs (source labels in Helius "SWAP" events)
SWAP_SOURCES = {"RAYDIUM", "ORCA", "METEORA", "PUMP_FUN", "JUPITER", "PHOENIX"}


async def fetch_pool_trades_helius(
    pool_address: str,
    client: httpx.AsyncClient,
    limit: int = 100,
) -> list[TradeRecord]:
    """Fetch recent trades on a Solana pool and return TradeRecord with
    real buyer/seller wallet addresses.

    Strategy:
      1. GET /v0/addresses/{pool}/transactions with type=SWAP
      2. Helius returns "tokenTransfers" with from/to wallets and native
         amounts. The SOL/stable leg tells us side (buy vs sell).

    Returns [] if HELIUS_API_KEY is missing or an error occurs — caller
    should fall back to the public GeckoTerminal path.
    """
    api_key = os.getenv("HELIUS_API_KEY", "")
    if not api_key:
        return []

    url = HELIUS_HISTORY_TMPL.format(addr=pool_address)
    try:
        r = await client.get(
            url,
            params={"api-key": api_key, "type": "SWAP", "limit": min(limit, 100)},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        log.debug("Helius swap history failed for %s: %s", pool_address, e)
        return []

    if not isinstance(raw, list):
        return []

    trades: list[TradeRecord] = []
    for tx in raw[:limit]:
        ts = _parse_ts(tx.get("timestamp"))
        events = tx.get("events") or {}
        swap   = events.get("swap") or {}
        native_out = swap.get("nativeOutput") or {}
        native_in  = swap.get("nativeInput")  or {}
        token_out_list = swap.get("tokenOutputs") or []
        token_in_list  = swap.get("tokenInputs")  or []

        # Wallet that signed the swap = fee payer = first account in keys.
        # Helius also surfaces it as feePayer.
        wallet = tx.get("feePayer") or ""
        if not wallet:
            continue

        # Determine side + USD value
        # SOL/stable going OUT (token coming in to wallet) = BUY
        # SOL/stable going IN  (token leaving wallet)       = SELL
        usd = 0.0
        side = None

        sol_amount_buy  = float(native_in.get("amount") or 0) / 1e9
        sol_amount_sell = float(native_out.get("amount") or 0) / 1e9

        if sol_amount_buy > 0 and token_out_list:
            side = "buy"
        elif sol_amount_sell > 0 and token_in_list:
            side = "sell"

        # USD — Helius doesn't always provide it; fall back to 0, smart_money
        # already handles this by downweighting unknowns.
        for tx_transfer in (tx.get("tokenTransfers") or []):
            if tx_transfer.get("mint") in {"So11111111111111111111111111111111111111112"}:
                usd = max(usd, float(tx_transfer.get("tokenAmount", 0) or 0))

        if not side:
            continue

        trades.append(TradeRecord(
            wallet=wallet,       # real Solana address, not tx-hash prefix
            side=side,
            usd_value=usd,       # may be 0; see caller for handling
            timestamp=ts,
            price=0.0,
        ))

    return trades


async def get_sol_price_usd(client: httpx.AsyncClient) -> float:
    """Very cheap SOL price fetch so Helius trades (which come in SOL) can
    be denominated in USD. Uses CoinGecko simple/price — public, no key.
    """
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=5,
        )
        r.raise_for_status()
        return float((r.json().get("solana") or {}).get("usd") or 0)
    except Exception:
        return 0.0
