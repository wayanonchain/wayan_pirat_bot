"""Subscription management, SOL and USDT payment verification."""

import logging
import httpx

from config.settings import (
    PAYMENT_WALLET, PREMIUM_PRICE_SOL,
    SUBSCRIPTION_DURATION_DAYS, HELIUS_API_KEY,
    USDT_MINT,
)
from db import repository as repo

logger = logging.getLogger(__name__)

# Tolerance for amount verification (+/-5%)
AMOUNT_TOLERANCE = 0.05

# Referral system
REFERRAL_DISCOUNT = 0.20  # 20% discount for referred users
REFERRAL_BONUS_DAYS = 7   # 7 days free Premium for referrer


async def verify_sol_payment(tx_signature: str, expected_tier: str, user_id: int) -> dict:
    """
    Verify a SOL payment transaction via Helius RPC.

    Returns:
        {"ok": True, "expires_at": datetime} on success
        {"ok": False, "error": "reason"} on failure
    """
    if not PAYMENT_WALLET:
        return {"ok": False, "error": "Кошелёк для оплаты не настроен. Обратись к админу."}

    base_amount = PREMIUM_PRICE_SOL

    # Check if user was referred — apply 20% discount
    sub = await repo.get_subscriber(user_id)
    has_referral_discount = False
    if sub and sub.referred_by:
        from sqlalchemy import select
        from db.models import Payment
        from db.repository import async_session
        async with async_session() as session:
            result = await session.execute(
                select(Payment).where(Payment.user_id == user_id, Payment.verified == True)
            )
            prior_payments = result.scalars().all()
        if not prior_payments:
            has_referral_discount = True

    expected_amount = base_amount * (1 - REFERRAL_DISCOUNT) if has_referral_discount else base_amount

    # Fetch transaction via Helius
    try:
        tx_data = await _fetch_transaction(tx_signature)
    except Exception as e:
        logger.error(f"TX fetch error: {e}")
        return {"ok": False, "error": "Не удалось получить транзакцию. Проверь подпись (signature)."}

    if not tx_data:
        return {"ok": False, "error": "Транзакция не найдена. Убедись, что она подтверждена."}

    # Parse and verify
    verification = _verify_sol_transfer(tx_data, expected_amount)
    if not verification["ok"]:
        return verification

    # Record payment and activate subscription
    recorded = await repo.record_payment(
        user_id=user_id,
        amount_sol=verification["amount_sol"],
        tx_signature=tx_signature,
        tier=expected_tier,
        period_days=SUBSCRIPTION_DURATION_DAYS,
    )

    if not recorded:
        return {"ok": False, "error": "Эта транзакция уже была использована."}

    expires_at = await repo.activate_subscription(
        user_id=user_id,
        tier=expected_tier,
        days=SUBSCRIPTION_DURATION_DAYS,
    )

    logger.info(f"Subscription activated: user={user_id} tier={expected_tier} "
                f"expires={expires_at} tx={tx_signature[:20]}...")

    # Referral bonus: give referrer 7 days of Premium
    referral_bonus_given = False
    if sub and sub.referred_by and has_referral_discount:
        try:
            referrer_tier = await repo.get_user_tier(sub.referred_by)
            bonus_tier = "premium" if referrer_tier == "free" else referrer_tier
            await repo.activate_subscription(
                user_id=sub.referred_by,
                tier=bonus_tier,
                days=REFERRAL_BONUS_DAYS,
            )
            referral_bonus_given = True
            logger.info(f"Referral bonus: {REFERRAL_BONUS_DAYS} days {bonus_tier} "
                        f"given to referrer {sub.referred_by}")
        except Exception as e:
            logger.error(f"Failed to give referral bonus: {e}")

    return {
        "ok": True,
        "expires_at": expires_at,
        "tier": expected_tier,
        "amount_sol": verification["amount_sol"],
        "referral_discount": has_referral_discount,
        "referral_bonus_given": referral_bonus_given,
        "referrer_id": sub.referred_by if sub and sub.referred_by else None,
    }


async def _fetch_transaction(signature: str) -> dict | None:
    """Fetch parsed transaction from Helius."""
    url = f"https://api.helius.xyz/v0/transactions/?api-key={HELIUS_API_KEY}"
    payload = {"transactions": [signature]}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            url2 = f"https://api.helius.xyz/v0/transactions/{signature}?api-key={HELIUS_API_KEY}"
            resp = await client.get(url2)
            if resp.status_code != 200:
                return None
            return resp.json()

        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return data if isinstance(data, dict) else None


def _verify_sol_transfer(tx_data: dict, expected_amount: float, target_wallet: str = None) -> dict:
    """Verify SOL native transfer: recipient and amount."""
    wallet = target_wallet or PAYMENT_WALLET

    native_transfers = tx_data.get("nativeTransfers", [])
    total_received = 0.0
    found_recipient = False

    for transfer in native_transfers:
        to_account = transfer.get("toUserAccount", "")
        if to_account == wallet:
            found_recipient = True
            amount_lamports = transfer.get("amount", 0)
            total_received += amount_lamports / 1e9

    if not found_recipient:
        token_transfers = tx_data.get("tokenTransfers", [])
        for tt in token_transfers:
            if tt.get("toUserAccount") == wallet:
                found_recipient = True
                total_received += tt.get("tokenAmount", 0)

    if not found_recipient:
        return {"ok": False, "error": f"Оплата отправлена не на тот кошелёк.\nОтправь на:\n<code>{wallet}</code>"}

    min_amount = expected_amount * (1 - AMOUNT_TOLERANCE)
    if total_received < min_amount:
        return {
            "ok": False,
            "error": f"Недостаточная сумма. Получено: {total_received:.4f} SOL, "
                     f"нужно: {expected_amount} SOL"
        }

    return {"ok": True, "amount_sol": total_received}


async def verify_usdt_payment(tx_signature: str, expected_usdt: float,
                               target_wallet: str) -> dict:
    """
    Verify a USDT (SPL token) payment on Solana via Helius.

    Returns:
        {"ok": True, "amount_usdt": float} on success
        {"ok": False, "error": "reason"} on failure
    """
    if not target_wallet:
        return {"ok": False, "error": "Кошелёк для оплаты не настроен. Обратись к админу."}

    try:
        tx_data = await _fetch_transaction(tx_signature)
    except Exception as e:
        logger.error(f"TX fetch error: {e}")
        return {"ok": False, "error": "Не удалось получить транзакцию. Проверь подпись (signature)."}

    if not tx_data:
        return {"ok": False, "error": "Транзакция не найдена. Убедись, что она подтверждена."}

    # Check tokenTransfers for USDT
    token_transfers = tx_data.get("tokenTransfers", [])
    total_usdt = 0.0
    found = False

    for tt in token_transfers:
        mint = tt.get("mint", "")
        to_account = tt.get("toUserAccount", "")

        if to_account == target_wallet and mint == USDT_MINT:
            found = True
            total_usdt += tt.get("tokenAmount", 0)

    if not found:
        return {
            "ok": False,
            "error": (
                "USDT перевод не найден.\n\n"
                "Убедись, что ты отправил <b>USDT</b> (не SOL, не USDC) "
                f"на кошелёк:\n<code>{target_wallet}</code>"
            )
        }

    min_amount = expected_usdt * (1 - AMOUNT_TOLERANCE)
    if total_usdt < min_amount:
        return {
            "ok": False,
            "error": f"Недостаточная сумма. Получено: {total_usdt:.2f} USDT, "
                     f"нужно: {expected_usdt:.0f} USDT"
        }

    return {"ok": True, "amount_usdt": total_usdt}


async def verify_sol_payment_raw(tx_signature: str, expected_amount: float,
                                  user_id: int, target_wallet: str = None) -> dict:
    """Verify a SOL payment without subscription logic."""
    wallet = target_wallet or PAYMENT_WALLET
    if not wallet:
        return {"ok": False, "error": "Кошелёк для оплаты не настроен. Обратись к админу."}

    try:
        tx_data = await _fetch_transaction(tx_signature)
    except Exception as e:
        logger.error(f"TX fetch error: {e}")
        return {"ok": False, "error": "Не удалось получить транзакцию. Проверь подпись (signature)."}

    if not tx_data:
        return {"ok": False, "error": "Транзакция не найдена. Убедись, что она подтверждена."}

    return _verify_sol_transfer(tx_data, expected_amount, target_wallet=wallet)


def get_price_text() -> str:
    """Get formatted pricing text."""
    return f"<b>Premium</b> — {PREMIUM_PRICE_SOL} SOL/мес"
