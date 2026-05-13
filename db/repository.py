"""Database operations for the Smart Money bot."""

import asyncio
import json
from datetime import datetime, timedelta
from sqlalchemy import event, select, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from db.models import Base, Wallet, TokenBuy, Signal, TokenMetadata, Subscriber, Payment
from config.settings import DATABASE_URL


# Cap concurrent write-path transactions. Without this, a boot-storm of
# pent-up Helius retries (we saw 100+ tx/sec) all race for SQLite's single
# writer slot, each holding a connection for busy_timeout=5s, which both
# drains the SA pool and piles up lock errors. Serialising to ~4 slots
# keeps write latency low and the pool spacious. Reads stay unbounded
# thanks to WAL.
_WRITE_SEMAPHORE = asyncio.Semaphore(4)


# Pool sized for the current load:
#   - Webhook burst rate is ~20-50 events/sec; each event opens a short
#     session (record_buy). With SQLite busy_timeout=5000 a blocked writer
#     can hold a connection for up to 5s, so a tight pool is easily
#     exhausted under contention.
#   - The accumulation monitor + scan handlers + scheduler jobs all add
#     their own concurrent sessions.
# pool_size=30 + max_overflow=60 gives 90 concurrent sessions, plenty
# of headroom; aiosqlite connections are cheap.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=30,
    max_overflow=60,
    pool_timeout=10,
    pool_recycle=3600,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# SQLite tuning — without these, concurrent webhook handlers doing
# SELECT-then-INSERT race to acquire the write lock and raise
# ``OperationalError: database is locked``. WAL lets readers coexist with a
# single writer; busy_timeout tells SQLite to wait (up to N ms) for the lock
# instead of raising immediately. foreign_keys matches what our schema
# expects.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma_on_connect(dbapi_conn, _record):  # noqa: D401
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()


async def init_db():
    """Create all tables and run lightweight migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add meteora_purchased column if missing (SQLite doesn't auto-add)
        try:
            await conn.execute(
                __import__('sqlalchemy').text(
                    "ALTER TABLE subscribers ADD COLUMN meteora_purchased BOOLEAN DEFAULT 0"
                )
            )
        except Exception:
            pass  # Column already exists
        try:
            await conn.execute(
                __import__('sqlalchemy').text(
                    "ALTER TABLE subscribers ADD COLUMN promo_code VARCHAR(30)"
                )
            )
        except Exception:
            pass  # Column already exists


async def get_session() -> AsyncSession:
    return async_session()


# === Wallet operations ===

async def get_active_wallets() -> list[Wallet]:
    async with async_session() as session:
        result = await session.execute(
            select(Wallet).where(Wallet.status == "ACTIVE")
        )
        return list(result.scalars().all())


async def get_active_addresses() -> list[str]:
    async with async_session() as session:
        result = await session.execute(
            select(Wallet.address).where(Wallet.status == "ACTIVE")
        )
        return [r[0] for r in result.all()]


async def get_wallet(address: str) -> Wallet | None:
    async with async_session() as session:
        return await session.get(Wallet, address)


async def upsert_wallet(wallet_data: dict):
    async with async_session() as session:
        existing = await session.get(Wallet, wallet_data["address"])
        if existing:
            for key, value in wallet_data.items():
                if key != "address" and hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            session.add(Wallet(**wallet_data))
        await session.commit()


async def bulk_import_wallets(wallets: list[dict]):
    """Import wallets from JSON database to SQLite."""
    async with async_session() as session:
        count = 0
        for w in wallets:
            existing = await session.get(Wallet, w["address"])
            if existing:
                continue
            wallet = Wallet(
                address=w["address"],
                status=w.get("status", "ACTIVE"),
                realized_pnl_usd=w.get("realized_pnl_usd", 0),
                unrealized_pnl_usd=w.get("unrealized_pnl_usd", 0),
                total_invested_usd=w.get("total_invested_usd", 0),
                roi=w.get("roi", 0),
                win_rate=w.get("win_rate", 0),
                traded_token_count=w.get("traded_token_count", 0),
                source=w.get("source", ""),
                first_discovered=datetime.fromisoformat(w["first_discovered"]) if w.get("first_discovered") else datetime.utcnow(),
                wallet_type=w.get("wallet_type", "UNKNOWN"),
                nansen_label=w.get("nansen_label", ""),
            )
            session.add(wallet)
            count += 1
        await session.commit()
        return count


async def set_wallet_inactive(address: str):
    async with async_session() as session:
        await session.execute(
            update(Wallet).where(Wallet.address == address).values(status="INACTIVE")
        )
        await session.commit()


async def wallet_count() -> dict:
    async with async_session() as session:
        result = await session.execute(
            select(Wallet.status, func.count()).group_by(Wallet.status)
        )
        return dict(result.all())


# === Token Buy operations ===

async def record_buy(buy_data: dict) -> bool:
    """Record a token buy. Returns False if duplicate tx.

    Dedup is case-insensitive — explorers can return TX signatures in
    different cases, and a case-sensitive unique index would let the same
    transaction slip through twice.

    Two concurrent webhook handlers can both pass the SELECT check and both
    INSERT; the unique index then raises ``IntegrityError`` on the loser. We
    treat that as "duplicate, return False" rather than propagating — the
    other handler already persisted the row.

    The call is throttled via ``_WRITE_SEMAPHORE`` to keep burst load from
    draining the connection pool.
    """
    tx_sig = buy_data["tx_signature"]
    async with _WRITE_SEMAPHORE:
        async with async_session() as session:
            existing = await session.execute(
                select(TokenBuy).where(func.lower(TokenBuy.tx_signature) == tx_sig.lower())
            )
            if existing.scalar_one_or_none():
                return False

            session.add(TokenBuy(**buy_data))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False


async def get_recent_buys(token_address: str, minutes: int = 30) -> list[TokenBuy]:
    """Get recent buys for a token within the sliding window."""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    async with async_session() as session:
        result = await session.execute(
            select(TokenBuy)
            .where(TokenBuy.token_address == token_address)
            .where(TokenBuy.timestamp >= cutoff)
            .order_by(TokenBuy.timestamp.desc())
        )
        return list(result.scalars().all())


async def get_unique_buyers(token_address: str, minutes: int = 30) -> list[str]:
    """Get unique wallet addresses that bought a token recently."""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    async with async_session() as session:
        result = await session.execute(
            select(TokenBuy.wallet_address)
            .where(TokenBuy.token_address == token_address)
            .where(TokenBuy.timestamp >= cutoff)
            .distinct()
        )
        return [r[0] for r in result.all()]


# === Signal operations ===

async def create_signal(signal_data: dict) -> Signal:
    async with async_session() as session:
        signal = Signal(**signal_data)
        session.add(signal)
        await session.commit()
        await session.refresh(signal)
        return signal


async def mark_signal_sent(signal_id: int, message_id: int):
    async with async_session() as session:
        await session.execute(
            update(Signal)
            .where(Signal.id == signal_id)
            .values(sent_to_telegram=True, telegram_message_id=message_id)
        )
        await session.commit()


async def get_recent_signals(hours: int = 24) -> list[Signal]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        result = await session.execute(
            select(Signal)
            .where(Signal.created_at >= cutoff)
            .order_by(Signal.created_at.desc())
        )
        return list(result.scalars().all())


# === Token metadata ===

async def get_token_metadata(address: str) -> TokenMetadata | None:
    async with async_session() as session:
        return await session.get(TokenMetadata, address)


async def upsert_token_metadata(data: dict):
    async with async_session() as session:
        existing = await session.get(TokenMetadata, data["address"])
        if existing:
            for key, value in data.items():
                if key != "address":
                    setattr(existing, key, value)
            existing.last_updated = datetime.utcnow()
        else:
            data["last_updated"] = datetime.utcnow()
            session.add(TokenMetadata(**data))
        await session.commit()


# === Subscriber operations ===

async def get_subscriber(user_id: int) -> Subscriber | None:
    async with async_session() as session:
        return await session.get(Subscriber, user_id)


async def get_user_tier(user_id: int) -> str:
    """Get user's subscription tier. Returns 'free' if not subscribed or expired."""
    sub = await get_subscriber(user_id)
    if not sub:
        return "free"
    if sub.tier == "free":
        return "free"
    if sub.expires_at and sub.expires_at < datetime.utcnow():
        return "free"  # Expired
    return sub.tier


async def upsert_subscriber(user_id: int, username: str = "", first_name: str = "",
                             tier: str = "free", expires_at: datetime = None):
    async with async_session() as session:
        existing = await session.get(Subscriber, user_id)
        if existing:
            if username:
                existing.username = username
            if first_name:
                existing.first_name = first_name
            if tier != "free":
                existing.tier = tier
                existing.subscribed_at = datetime.utcnow()
            if expires_at:
                existing.expires_at = expires_at
        else:
            session.add(Subscriber(
                user_id=user_id,
                username=username,
                first_name=first_name,
                tier=tier,
                expires_at=expires_at,
            ))
        await session.commit()


async def activate_subscription(user_id: int, tier: str, days: int):
    """Activate or extend subscription."""
    async with async_session() as session:
        sub = await session.get(Subscriber, user_id)
        if not sub:
            sub = Subscriber(user_id=user_id)
            session.add(sub)

        now = datetime.utcnow()
        # If existing subscription hasn't expired, extend from expiry date
        if sub.expires_at and sub.expires_at > now:
            base = sub.expires_at
        else:
            base = now

        sub.tier = tier
        sub.subscribed_at = now
        sub.expires_at = base + timedelta(days=days)
        await session.commit()
        return sub.expires_at


async def get_all_subscribers_by_tier(tier: str) -> list[Subscriber]:
    """Get all active subscribers of a given tier (or higher)."""
    async with async_session() as session:
        now = datetime.utcnow()
        if tier == "premium":
            result = await session.execute(
                select(Subscriber).where(
                    Subscriber.tier.in_(["premium", "premium_plus"]),
                    Subscriber.expires_at > now,
                )
            )
        elif tier == "premium_plus":
            result = await session.execute(
                select(Subscriber).where(
                    Subscriber.tier == "premium_plus",
                    Subscriber.expires_at > now,
                )
            )
        else:
            result = await session.execute(select(Subscriber))
        return list(result.scalars().all())


async def get_active_subscriber_ids() -> dict[int, str]:
    """Returns {user_id: tier} for all active paid subscribers."""
    async with async_session() as session:
        now = datetime.utcnow()
        result = await session.execute(
            select(Subscriber).where(
                Subscriber.tier != "free",
                Subscriber.expires_at > now,
            )
        )
        return {s.user_id: s.tier for s in result.scalars().all()}


# === Payment operations ===

async def record_payment(user_id: int, amount_sol: float, tx_signature: str,
                         tier: str, period_days: int) -> bool:
    """Record a payment. Returns False if duplicate TX.

    Dedup is case-insensitive so a user can't double-redeem one on-chain
    payment by resubmitting the signature with different letter case.
    """
    async with async_session() as session:
        existing = await session.execute(
            select(Payment).where(func.lower(Payment.tx_signature) == tx_signature.lower())
        )
        if existing.scalar_one_or_none():
            return False

        session.add(Payment(
            user_id=user_id,
            amount_sol=amount_sol,
            tx_signature=tx_signature,
            tier=tier,
            period_days=period_days,
            verified=True,
        ))
        async with _WRITE_SEMAPHORE:
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False


async def set_promo_code(user_id: int, code: str) -> bool:
    """Apply a promo code to user. Returns False if already has one."""
    async with async_session() as session:
        sub = await session.get(Subscriber, user_id)
        if not sub:
            return False
        if sub.promo_code:
            return False  # Already has a promo code
        sub.promo_code = code.upper()
        await session.commit()
        return True


async def mark_course_purchased(user_id: int):
    """Mark user as having purchased the course."""
    async with async_session() as session:
        sub = await session.get(Subscriber, user_id)
        if sub:
            sub.course_purchased = True
            await session.commit()


async def has_course_purchased(user_id: int) -> bool:
    """Check if user has purchased the course."""
    sub = await get_subscriber(user_id)
    return bool(sub and sub.course_purchased)


async def mark_meteora_purchased(user_id: int):
    """Mark user as having purchased the Meteora course."""
    async with async_session() as session:
        sub = await session.get(Subscriber, user_id)
        if sub:
            sub.meteora_purchased = True
            await session.commit()


async def has_meteora_purchased(user_id: int) -> bool:
    """Check if user has purchased the Meteora course."""
    sub = await get_subscriber(user_id)
    return bool(sub and sub.meteora_purchased)


# === Course Access ===

async def get_course_access(user_id: int):
    """Get course access record for a user."""
    from db.models import CourseAccess
    async with async_session() as session:
        result = await session.execute(
            select(CourseAccess).where(CourseAccess.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def save_course_access(user_id: int, invite_link: str,
                              payment_tx: str = None, is_test: bool = False):
    """Save course access record."""
    from db.models import CourseAccess
    async with async_session() as session:
        access = CourseAccess(
            user_id=user_id,
            invite_link=invite_link,
            payment_tx=payment_tx,
            is_test=is_test,
        )
        session.add(access)
        await session.commit()
