"""SQLAlchemy models for the Smart Money bot."""

from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, Index
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Wallet(Base):
    """Smart Money wallet being monitored."""
    __tablename__ = "wallets"

    address = Column(String(44), primary_key=True)
    status = Column(String(20), default="ACTIVE", index=True)  # ACTIVE, INACTIVE, REJECTED

    # PnL data
    realized_pnl_usd = Column(Float, default=0.0)
    unrealized_pnl_usd = Column(Float, default=0.0)
    total_invested_usd = Column(Float, default=0.0)
    roi = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    traded_token_count = Column(Integer, default=0)

    # Classification
    wallet_type = Column(String(20), default="UNKNOWN")  # BOT, LIKELY_BOT, TRADER, UNKNOWN
    nansen_label = Column(String(200), default="")

    # Source & tracking
    source = Column(String(50), default="")  # nansen, dune+solanatracker, etc.
    first_discovered = Column(DateTime, default=datetime.utcnow)
    last_pnl_update = Column(DateTime, nullable=True)
    last_active = Column(DateTime, nullable=True)
    last_seen_in_dex_trades = Column(DateTime, nullable=True)

    # Helius webhook
    webhook_id = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<Wallet {self.address[:12]}... status={self.status} pnl=${self.realized_pnl_usd:,.0f}>"


class TokenBuy(Base):
    """Record of a Smart Money wallet buying a token."""
    __tablename__ = "token_buys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(44), index=True)
    token_address = Column(String(44), index=True)
    token_symbol = Column(String(30), default="")
    amount_usd = Column(Float, default=0.0)
    amount_token = Column(Float, default=0.0)
    amount_sol = Column(Float, default=0.0)
    tx_signature = Column(String(100), unique=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    mcap_at_buy = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_token_timestamp", "token_address", "timestamp"),
    )


class Signal(Base):
    """Detected signal (2+ SM wallets buying same token)."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_address = Column(String(44), index=True)
    token_symbol = Column(String(30), default="")
    mode = Column(Integer, default=1)  # 1 = 2 wallets, 2 = 3+ wallets
    wallet_count = Column(Integer, default=0)
    wallets_json = Column(Text, default="[]")  # JSON array of wallet details
    total_buy_usd = Column(Float, default=0.0)
    mcap = Column(Float, nullable=True)
    token_age_hours = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_to_telegram = Column(Boolean, default=False)
    telegram_message_id = Column(Integer, nullable=True)


class TokenMetadata(Base):
    """Cached token metadata."""
    __tablename__ = "token_metadata"

    address = Column(String(44), primary_key=True)
    symbol = Column(String(30), default="")
    name = Column(String(100), default="")
    decimals = Column(Integer, default=9)
    mcap = Column(Float, nullable=True)
    price_usd = Column(Float, nullable=True)
    liquidity_usd = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=True)  # Token creation time
    last_updated = Column(DateTime, default=datetime.utcnow)


class Subscriber(Base):
    """Bot subscriber with tier info."""
    __tablename__ = "subscribers"

    user_id = Column(Integer, primary_key=True)  # Telegram user ID
    username = Column(String(100), default="")
    first_name = Column(String(100), default="")
    tier = Column(String(20), default="free")  # free / premium / premium_plus
    subscribed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    referral_code = Column(String(20), unique=True, nullable=True)
    referred_by = Column(Integer, nullable=True)
    course_purchased = Column(Boolean, default=False)
    meteora_purchased = Column(Boolean, default=False)
    referral_credits = Column(Integer, default=0)  # 20%-off credits earned from referrals
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Subscriber {self.user_id} tier={self.tier}>"

    @property
    def is_active_premium(self) -> bool:
        if self.tier == "free":
            return False
        if self.expires_at and self.expires_at < datetime.utcnow():
            return False
        return True

    @property
    def is_premium_plus(self) -> bool:
        return self.tier == "premium_plus" and self.is_active_premium


class Payment(Base):
    """SOL payment record."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, index=True)
    amount_sol = Column(Float, default=0.0)
    tx_signature = Column(String(100), unique=True)
    tier = Column(String(20), default="premium")
    period_days = Column(Integer, default=30)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserFilter(Base):
    """Per-user signal filter settings."""
    __tablename__ = "user_filters"

    user_id = Column(Integer, primary_key=True)
    filters_json = Column(Text, default="{}")


class CourseAccess(Base):
    """Course access records."""
    __tablename__ = "course_access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, index=True, nullable=False)
    invite_link = Column(String(200), nullable=False)
    payment_tx = Column(String(100), nullable=True)  # None for test access
    is_test = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
