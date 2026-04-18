"""
Accumulation Pattern Agent — Configuration
Phase 2: Post-ATH re-accumulation detector
"""
from dataclasses import dataclass, field
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AgentConfig:
    # ── Telegram ──────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Market cap filters ────────────────────────────────────────
    min_mcap_usd: float = 500_000        # минимальный mcap для мониторинга
    max_mcap_usd: float = 15_000_000     # выше — поздно входить
    min_drawdown_from_ath: float = 0.50  # минимальная просадка от ATH (50%)
    max_drawdown_from_ath: float = 0.97  # максимальная просадка от ATH (97%)

    # ── Liquidity ─────────────────────────────────────────────────
    min_liquidity_usd: float = 80_000

    # ── Token age ─────────────────────────────────────────────────
    min_token_age_days: int = 14         # не новый скам
    max_token_age_days: int = 120        # не мёртвый

    # ── Holders ──────────────────────────────────────────────────
    min_holders: int = 300
    max_holders: int = 10_000

    # ── Consolidation (боковик) ────────────────────────────────────
    consolidation_min_days: int = 14     # минимальная длительность боковика
    consolidation_max_days: int = 45     # максимальная
    consolidation_max_range_pct: float = 0.30  # ширина боковика ≤ 30%

    # ── No-new-low window ─────────────────────────────────────────
    no_new_low_days: int = 10            # нет нового лоя N дней

    # ── Volume dryup ──────────────────────────────────────────────
    volume_dryup_ratio: float = 0.30     # объём боковика < 30% объёма падения

    # ── Spring detection ─────────────────────────────────────────
    spring_min_pct: float = 0.05         # пробой вниз минимум 5%
    spring_max_pct: float = 0.15         # пробой вниз максимум 15%
    spring_recovery_hours: int = 48      # возврат обратно в боковик за N часов

    # ── Volume spike (breakout trigger) ──────────────────────────
    volume_spike_multiplier: float = 3.0  # объём вырос в 3x от среднего
    volume_spike_lookback_hours: int = 6  # за последние N часов

    # ── Score thresholds ──────────────────────────────────────────
    watchlist_score: float = 45.0
    signal_score: float = 65.0
    strong_score: float = 80.0

    # ── Polling ───────────────────────────────────────────────────
    poll_interval_seconds: int = 300         # опрос каждые 5 минут
    cooldown_hours: float = 4.0              # пауза после SIGNAL/STRONG
    watchlist_cooldown_hours: float = 1.0    # мини-пауза после WATCHLIST (anti-spam)

    # ── Data ─────────────────────────────────────────────────────
    tokens_file: str = "tokens.json"
    state_file: str = "state.json"


def get_config() -> AgentConfig:
    cfg = AgentConfig()
    cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    cfg.telegram_chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    return cfg


# Presets
AGGRESSIVE = AgentConfig(
    min_mcap_usd=300_000,
    max_mcap_usd=20_000_000,
    min_drawdown_from_ath=0.45,
    min_liquidity_usd=50_000,
    consolidation_min_days=10,
    volume_spike_multiplier=2.0,
    watchlist_score=35.0,
    signal_score=55.0,
    strong_score=70.0,
)

BALANCED = get_config()  # default

CONSERVATIVE = AgentConfig(
    min_mcap_usd=1_000_000,
    max_mcap_usd=10_000_000,
    min_drawdown_from_ath=0.60,
    min_liquidity_usd=120_000,
    consolidation_min_days=21,
    volume_spike_multiplier=4.0,
    watchlist_score=55.0,
    signal_score=72.0,
    strong_score=88.0,
)
