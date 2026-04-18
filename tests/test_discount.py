"""Discount stacking tests.

Covers the money-critical combinations: base, referral, promo, stacked,
and the clamp that protects against a future promo pushing the total
above 100% (which would yield a negative price).
"""

import pytest


pytestmark = pytest.mark.asyncio


async def test_no_discount_for_fresh_user(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=10, username="u", first_name="U")

    from bot.course import get_discount_info
    info = await get_discount_info(10)
    assert info["course_discount_pct"] == 0
    assert info["course_price"] == pytest.approx(400.0)


async def test_referral_only_discount(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=100, username="ref", first_name="Ref")
    await repo.upsert_subscriber(user_id=101, username="friend", first_name="Friend")
    await repo.set_referred_by(101, 100)

    from bot.course import get_discount_info
    info = await get_discount_info(101)
    assert info["course_discount_pct"] == 20
    assert info["course_price"] == pytest.approx(320.0)


async def test_promo_only_discount(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=200, username="u", first_name="U")
    await repo.set_promo_code(200, "KATE")

    from bot.course import get_discount_info
    info = await get_discount_info(200)
    assert info["course_discount_pct"] == 10
    assert info["course_price"] == pytest.approx(360.0)


async def test_referral_plus_promo_stacks(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=300, username="ref", first_name="R")
    await repo.upsert_subscriber(user_id=301, username="f", first_name="F")
    await repo.set_referred_by(301, 300)
    await repo.set_promo_code(301, "KATE")

    from bot.course import get_discount_info
    info = await get_discount_info(301)
    assert info["course_discount_pct"] == 30
    assert info["course_price"] == pytest.approx(280.0)


async def test_discount_clamped_at_100_percent(clean_db, monkeypatch):
    """Synthetic: a 95% promo + 20% referral must not produce a negative price."""
    repo = clean_db
    await repo.upsert_subscriber(user_id=400, username="ref", first_name="R")
    await repo.upsert_subscriber(user_id=401, username="f", first_name="F")
    await repo.set_referred_by(401, 400)

    from config import settings
    monkeypatch.setitem(
        settings.PROMO_CODES,
        "MEGA",
        {"discount": 0.95, "product": "course", "label": "mega"},
    )
    await repo.set_promo_code(401, "MEGA")

    from bot import course as course_module
    # The get_discount_info lookup is done via the `from … import PROMO_CODES`
    # reference captured in bot/course.py at import time. Patch that too.
    monkeypatch.setitem(
        course_module.PROMO_CODES,
        "MEGA",
        {"discount": 0.95, "product": "course", "label": "mega"},
    )

    info = await course_module.get_discount_info(401)
    assert info["course_discount_pct"] == 100
    assert info["course_price"] == 0


async def test_community_discount_tiers(clean_db):
    """1 paid referral → 20%, 2 → 50%, 3+ → 100%."""
    repo = clean_db

    async def setup_user_with_referrals(uid: int, paid_count: int) -> None:
        await repo.upsert_subscriber(user_id=uid, username=f"u{uid}", first_name="U")
        for i in range(paid_count):
            child_id = uid * 100 + i + 1
            await repo.upsert_subscriber(
                user_id=child_id, username=f"c{child_id}", first_name="C",
            )
            await repo.set_referred_by(child_id, uid)
            # Mark the child as a paying course buyer.
            async with repo.async_session() as session:
                sub = await session.get(repo.Subscriber, child_id)
                sub.course_purchased = True
                await session.commit()

    from bot.course import get_discount_info

    await setup_user_with_referrals(500, paid_count=1)
    info = await get_discount_info(500)
    assert info["community_discount_pct"] == 20

    await setup_user_with_referrals(600, paid_count=2)
    info = await get_discount_info(600)
    assert info["community_discount_pct"] == 50

    await setup_user_with_referrals(700, paid_count=3)
    info = await get_discount_info(700)
    assert info["community_discount_pct"] == 100
    assert info["community_price"] == 0


async def test_discount_disabled_once_course_purchased(clean_db):
    """Even with referral + promo, a user who already owns the course sees no course discount."""
    repo = clean_db
    await repo.upsert_subscriber(user_id=800, username="owner", first_name="O")
    await repo.upsert_subscriber(user_id=801, username="f", first_name="F")
    await repo.set_referred_by(801, 800)
    await repo.set_promo_code(801, "KATE")
    async with repo.async_session() as session:
        sub = await session.get(repo.Subscriber, 801)
        sub.course_purchased = True
        await session.commit()

    from bot.course import get_discount_info
    info = await get_discount_info(801)
    assert info["course_discount_pct"] == 0
    assert info["has_course"] is True
