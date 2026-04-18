"""Test that TX signature dedup is case-insensitive and race-safe.

These tests protect against the twin failure modes:
  1. User retypes the signature in different case → should NOT double-grant.
  2. Two webhook handlers race to record the same tx → losing INSERT is
     treated as "duplicate", not a propagated IntegrityError.
"""

import asyncio

import pytest


pytestmark = pytest.mark.asyncio


async def test_record_buy_case_insensitive_dedup(clean_db):
    repo = clean_db
    base = {
        "wallet_address": "Wallet1",
        "token_address": "Token1",
        "token_symbol": "TK",
        "amount_usd": 500.0,
        "amount_sol": 1.0,
        "amount_token": 1000.0,
        "tx_signature": "AbCdEf123",
    }
    assert await repo.record_buy(dict(base)) is True
    # Same TX, different case — must dedup.
    clash = dict(base, tx_signature="abcdef123")
    assert await repo.record_buy(clash) is False


async def test_record_payment_case_insensitive_dedup(clean_db):
    repo = clean_db
    assert await repo.record_payment(
        user_id=42, amount_sol=0.15, tx_signature="XyZ999",
        tier="premium", period_days=30,
    ) is True
    # Should catch dedup even though case differs.
    assert await repo.record_payment(
        user_id=99, amount_sol=0.15, tx_signature="xyz999",
        tier="premium", period_days=30,
    ) is False


async def test_concurrent_record_buy_only_one_wins(clean_db):
    repo = clean_db

    async def insert():
        return await repo.record_buy({
            "wallet_address": "W",
            "token_address": "T",
            "token_symbol": "SYM",
            "amount_usd": 100.0,
            "amount_sol": 0.5,
            "amount_token": 10.0,
            "tx_signature": "RACEsig123",
        })

    results = await asyncio.gather(*(insert() for _ in range(8)))
    # Exactly one caller should observe "inserted"; the rest should see "duplicate".
    assert sum(results) == 1


async def test_concurrent_record_payment_only_one_wins(clean_db):
    repo = clean_db

    async def insert():
        return await repo.record_payment(
            user_id=7, amount_sol=0.15,
            tx_signature="racePAY42",
            tier="premium", period_days=30,
        )

    results = await asyncio.gather(*(insert() for _ in range(8)))
    assert sum(results) == 1
