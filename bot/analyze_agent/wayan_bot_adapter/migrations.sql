-- Accumulation module — additive migration.
-- Idempotent: uses IF NOT EXISTS so applying twice is safe.
-- Intended to be applied against WAYNE_PIRATE solana-smart-money-bot/data/bot.db.

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Watchlist — tokens currently being monitored for the accumulation
--    pattern. Fed by the SM-discovery job, by manual /acc_add, or by an
--    optional narrative scan.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accumulation_watchlist (
    token_address          TEXT    NOT NULL PRIMARY KEY,
    chain                  TEXT    NOT NULL DEFAULT 'solana',
    symbol                 TEXT    DEFAULT '',

    added_source           TEXT    NOT NULL,      -- 'sm_discovery' | 'manual' | 'narrative'
    added_by_user_id       INTEGER,               -- telegram user_id for 'manual'; else NULL
    sm_wallets_at_add      INTEGER DEFAULT 0,     -- SM wallets that triggered discovery

    added_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_checked_at        TIMESTAMP,
    last_score             REAL,
    last_tier              TEXT,                  -- NOISE | WATCHLIST | SIGNAL | STRONG

    status                 TEXT    NOT NULL DEFAULT 'monitoring',
                                                  -- monitoring | signaled | stale | removed
    removed_at             TIMESTAMP,
    removed_reason         TEXT
);

CREATE INDEX IF NOT EXISTS idx_acc_watch_status
    ON accumulation_watchlist (status);

CREATE INDEX IF NOT EXISTS idx_acc_watch_added_at
    ON accumulation_watchlist (added_at);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Signals — every time the detector fires SIGNAL or STRONG (and
--    WATCHLIST when we track those). Keeps a historical record so we
--    can backtest / measure precision later.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accumulation_signals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address          TEXT    NOT NULL,
    symbol                 TEXT    DEFAULT '',

    tier                   TEXT    NOT NULL,
    score                  REAL    NOT NULL,

    drawdown_from_ath      REAL,
    consolidation_days     REAL,
    spring_status          TEXT,
    volume_spike_ratio     REAL,
    no_new_low_days        INTEGER,

    sm_wallets_24h         INTEGER DEFAULT 0,
    sm_total_buy_usd       REAL    DEFAULT 0,

    mcap_at_signal         REAL,
    liquidity_at_signal    REAL,
    ath_mcap               REAL,

    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_to_telegram       INTEGER DEFAULT 0,
    telegram_message_id    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_acc_signals_token_ts
    ON accumulation_signals (token_address, created_at);

CREATE INDEX IF NOT EXISTS idx_acc_signals_created
    ON accumulation_signals (created_at);

-- ─────────────────────────────────────────────────────────────────────────
-- 3. State — per-token persistent state used across monitor runs.
--    Replaces the standalone state.json the Desktop agent used to keep.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accumulation_state (
    token_address          TEXT    NOT NULL PRIMARY KEY,
    ath_mcap               REAL    DEFAULT 0,
    cg_ath_checked_at      TIMESTAMP,             -- NULL = never asked CoinGecko yet
    cooldown_until         TIMESTAMP,             -- NULL = no cooldown
    cooldown_tier          TEXT,                  -- WATCHLIST | SIGNAL | STRONG
    updated_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
