"""Data models for accumulation pattern agent."""
from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum


class SignalTier(Enum):
    NOISE     = "NOISE"
    WATCHLIST = "WATCHLIST"
    SIGNAL    = "SIGNAL"
    STRONG    = "STRONG"


class SpringStatus(Enum):
    NONE        = "none"
    DETECTED    = "detected"     # пробой вниз зафиксирован, ещё не вернулся
    CONFIRMED   = "confirmed"    # вернулся в боковик — паттерн завершён


@dataclass
class OHLCV:
    timestamp: int    # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float     # USD


@dataclass
class TokenMetadata:
    address: str
    symbol: str
    name: str
    chain: str
    current_price: float
    current_mcap: float
    ath_mcap: float
    liquidity_usd: float
    holders: int
    age_days: float
    dexscreener_url: str = ""
    birdeye_url: str = ""


@dataclass
class ConsolidationZone:
    high: float
    low: float
    range_pct: float       # (high - low) / low
    duration_days: float
    avg_volume_usd: float  # средний дневной объём внутри боковика
    volume_at_drop: float  # средний объём в период падения


@dataclass
class SpringEvent:
    status: SpringStatus
    low_price: float       # цена в момент пробоя
    breach_pct: float      # насколько пробил вниз
    recovery_hours: float  # через сколько часов вернулся (0 если не вернулся)
    days_ago: float        # сколько дней назад был Spring


@dataclass
class AccumulationAnalysis:
    token: TokenMetadata
    drawdown_from_ath: float        # просадка от ATH
    consolidation: Optional[ConsolidationZone]
    spring: SpringEvent
    volume_spike_ratio: float       # текущий объём / средний объём боковика
    no_new_low_days: int            # дней без нового лоя
    score: float                    # 0–100
    tier: SignalTier
    passed_filters: list[str]
    failed_filters: list[str]
    take_profit_levels: list[float] # TP1..TP4 в USD mcap
    sm_wallets_24h: int = 0                    # count of curated SM wallets bought in 24h
    sm_total_buy_usd: float = 0.0
    sm_reason_lines: list[str] = field(default_factory=list)
