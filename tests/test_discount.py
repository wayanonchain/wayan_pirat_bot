"""Discount tests — base price and promo-code discount."""

import pytest


pytestmark = pytest.mark.asyncio


async def test_no_discount_for_fresh_user(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=10, username="u", first_name="U")

    from bot.course import get_discount_info
    info = await get_discount_info(10)
    assert info["course_discount_pct"] == 0
    assert info["course_price"] == pytest.approx(400.0)


async def test_promo_only_discount(clean_db):
    repo = clean_db
    await repo.upsert_subscriber(user_id=200, username="u", first_name="U")
    await repo.set_promo_code(200, "KATE")

    from bot.course import get_discount_info
    info = await get_discount_info(200)
    assert info["course_discount_pct"] == 10
    assert info["course_price"] == pytest.approx(360.0)


async def test_discount_disabled_once_course_purchased(clean_db):
    """A user who already owns the course sees no course discount."""
    repo = clean_db
    await repo.upsert_subscriber(user_id=801, username="f", first_name="F")
    await repo.set_promo_code(801, "KATE")
    async with repo.async_session() as session:
        sub = await session.get(repo.Subscriber, 801)
        sub.course_purchased = True
        await session.commit()

    from bot.course import get_discount_info
    info = await get_discount_info(801)
    assert info["course_discount_pct"] == 0
    assert info["has_course"] is True
