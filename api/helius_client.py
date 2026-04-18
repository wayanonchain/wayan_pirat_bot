"""Helius API client for transaction monitoring and webhooks."""

import logging
from datetime import datetime

import httpx

from config.settings import HELIUS_API_KEY, HELIUS_API_URL, HELIUS_RPC_URL

logger = logging.getLogger(__name__)

HELIUS_HEADERS = {"Content-Type": "application/json"}


async def get_parsed_transactions(address: str, limit: int = 10, tx_type: str = "SWAP") -> list[dict]:
    """Get enhanced (parsed) transactions for an address."""
    url = f"{HELIUS_API_URL}/addresses/{address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit}
    if tx_type:
        params["type"] = tx_type

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def parse_swap_transaction(tx: dict) -> dict | None:
    """Parse a Helius enhanced transaction into a buy record."""
    if tx.get("type") != "SWAP":
        return None

    events = tx.get("events", {})
    swap = events.get("swap", {})
    if not swap:
        return None

    token_inputs = swap.get("tokenInputs", [])
    token_outputs = swap.get("tokenOutputs", [])
    native_input = swap.get("nativeInput", {})
    native_output = swap.get("nativeOutput", {})

    # Determine buy direction: SOL/USDC → Token = BUY
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    STABLE_MINTS = {SOL_MINT, USDC_MINT}

    bought_token = None
    spent_sol = 0

    # Check if native SOL was spent (input)
    if native_input and native_input.get("amount", 0) > 0:
        spent_sol = native_input["amount"] / 1e9  # lamports to SOL

    # Find the token that was bought (output, not SOL/USDC)
    for out in token_outputs:
        mint = out.get("mint", "")
        if mint and mint not in STABLE_MINTS:
            bought_token = {
                "address": mint,
                "amount": out.get("rawTokenAmount", {}).get("tokenAmount", "0"),
                "decimals": int(out.get("rawTokenAmount", {}).get("decimals", 9)),
            }
            break

    if not bought_token:
        return None

    # Calculate token amount
    raw_amount = float(bought_token["amount"])
    decimals = bought_token["decimals"]
    token_amount = raw_amount / (10 ** decimals) if decimals > 0 else raw_amount

    return {
        "token_address": bought_token["address"],
        "token_amount": token_amount,
        "sol_spent": spent_sol,
        "tx_signature": tx.get("signature", ""),
        "timestamp": datetime.fromtimestamp(tx.get("timestamp", 0)),
        "fee_payer": tx.get("feePayer", ""),
    }


async def create_webhook(addresses: list[str], webhook_url: str) -> dict | None:
    """Create a Helius webhook to monitor wallet addresses."""
    url = f"{HELIUS_API_URL}/webhooks?api-key={HELIUS_API_KEY}"
    payload = {
        "webhookURL": webhook_url,
        "transactionTypes": ["SWAP"],
        "accountAddresses": addresses,
        "webhookType": "enhanced",
        "encoding": "jsonParsed",
    }
    from config.settings import HELIUS_WEBHOOK_AUTH
    if HELIUS_WEBHOOK_AUTH:
        payload["authHeader"] = HELIUS_WEBHOOK_AUTH

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info(f"Created webhook: {data.get('webhookID')}")
            return data
        else:
            logger.error(f"Failed to create webhook: {resp.status_code} {resp.text}")
            return None


async def get_webhooks() -> list[dict]:
    """List all existing webhooks."""
    url = f"{HELIUS_API_URL}/webhooks?api-key={HELIUS_API_KEY}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def delete_webhook(webhook_id: str) -> bool:
    """Delete a webhook by ID."""
    url = f"{HELIUS_API_URL}/webhooks/{webhook_id}?api-key={HELIUS_API_KEY}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(url)
        return resp.status_code == 200


async def update_webhook_addresses(webhook_id: str, addresses: list[str]) -> bool:
    """Update addresses on an existing webhook."""
    url = f"{HELIUS_API_URL}/webhooks/{webhook_id}?api-key={HELIUS_API_KEY}"
    payload = {"accountAddresses": addresses}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json=payload)
        return resp.status_code == 200


async def update_webhook_url(webhook_id: str, new_url: str) -> bool:
    """Update the webhook URL for an existing webhook."""
    url = f"{HELIUS_API_URL}/webhooks/{webhook_id}?api-key={HELIUS_API_KEY}"
    payload = {"webhookURL": new_url}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json=payload)
        if resp.status_code == 200:
            logger.info(f"Webhook {webhook_id} URL updated to {new_url}")
            return True
        else:
            logger.error(f"Failed to update webhook URL: {resp.status_code} {resp.text}")
            return False
