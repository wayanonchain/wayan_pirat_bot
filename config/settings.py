"""Bot configuration — loads from .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root or parent
ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if not ENV_PATH.exists():
    ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_PATH)

# === API Keys ===
NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "aqPHceAQXybQ9KhIiaEBYqJWNP8yk7nZ")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
SOLANATRACKER_API_KEY = os.getenv("SOLANATRACKER_API_KEY", "")
DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ADMIN_IDS = {TELEGRAM_CHAT_ID, "422304752"}  # main admin + Viktor

# === Database ===
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
# Allow tests (and local overrides) to point the engine at a different DB
# without editing the source.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")

# === Bot Settings ===
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "60"))

# Signal detection
SIGNAL_WINDOW_MINUTES = 30       # Sliding window for detecting coincidences
MIN_WALLETS_MODE_1 = 2           # Mode 1: 2 SM wallets buy same token
MIN_WALLETS_MODE_2 = 3           # Mode 2: 3 SM wallets buy same token
MIN_BUY_USD = 100                # Minimum buy amount to count

# Wallet monitoring
MAX_MONITORED_WALLETS = 500      # Max wallets to monitor via webhooks
INACTIVE_DAYS_THRESHOLD = 7      # Days without activity -> INACTIVE

# Helius
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API_URL = f"https://api.helius.xyz/v0"
HELIUS_WEBHOOK_URL = os.getenv("HELIUS_WEBHOOK_URL", "")
# Shared secret Helius sends back in the Authorization header on each webhook
# POST. If empty, the endpoint accepts unauthenticated requests (legacy) and
# logs a warning at startup.
HELIUS_WEBHOOK_AUTH = os.getenv("HELIUS_WEBHOOK_AUTH", "")

# Webhook server
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# === Log Chat (team group for activity logs) ===
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID", "-1003833809842")
# Forum topic (thread) inside the log chat where technical ERRORs are posted.
# ``General`` is kept clear for user/sales events; set to empty string to
# post errors in General instead.
_thread_raw = os.getenv("LOG_CHAT_ERRORS_THREAD_ID", "60")
LOG_CHAT_ERRORS_THREAD_ID: int | None = int(_thread_raw) if _thread_raw.strip() else None

# Forum topic inside the log chat for Smart Money token signals. When set,
# every signal is mirrored there in addition to TELEGRAM_CHAT_ID + premium
# subscribers. Empty string disables the mirror.
_signals_thread_raw = os.getenv("LOG_CHAT_SIGNALS_THREAD_ID", "60")
LOG_CHAT_SIGNALS_THREAD_ID: int | None = (
    int(_signals_thread_raw) if _signals_thread_raw.strip() else None
)

# ── Legacy alert feature toggles ────────────────────────────────────────
# Everything below controls the pre-accumulation features. All default
# OFF so the current bot runs in accumulation-only mode. Flip to true in
# .env to re-enable any of them.
def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")

# Webhook-driven "2+ SM wallets buy same token" signals + admin DM +
# subscriber fanout + log-chat mirror.
WEBHOOK_SIGNALS_ENABLED = _bool_env("WEBHOOK_SIGNALS_ENABLED")
# "SM wallet exits a previously-signaled token" alerts.
WEBHOOK_SELL_ALERTS_ENABLED = _bool_env("WEBHOOK_SELL_ALERTS_ENABLED")
# APScheduler jobs posting to community / admin:
NANSEN_SIGNALS_ENABLED = _bool_env("NANSEN_SIGNALS_ENABLED")
WEEKLY_SM_REPORT_ENABLED = _bool_env("WEEKLY_SM_REPORT_ENABLED")
# Daily stats is diagnostic only — default ON.
DAILY_ADMIN_STATS_ENABLED = _bool_env("DAILY_ADMIN_STATS_ENABLED", default="true")

# === Subscription / Payment ===
PAYMENT_WALLET = os.getenv("PAYMENT_WALLET", "")
PREMIUM_PRICE_SOL = float(os.getenv("PREMIUM_PRICE_SOL", "0.15"))
SUBSCRIPTION_DURATION_DAYS = int(os.getenv("SUBSCRIPTION_DURATION_DAYS", "30"))

# Delay for free-tier signal delivery (minutes)
FREE_SIGNAL_DELAY_MINUTES = int(os.getenv("FREE_SIGNAL_DELAY_MINUTES", "15"))

# === Course (Wayan Onchain) ===
COURSE_PAYMENT_WALLET = os.getenv("COURSE_PAYMENT_WALLET", "")
COURSE_FREE_CHANNEL_ID = int(os.getenv("COURSE_FREE_CHANNEL_ID", "0"))
COURSE_PAID_CHANNEL_ID = int(os.getenv("COURSE_PAID_CHANNEL_ID", "0"))
COURSE_PRICE_USDT = float(os.getenv("COURSE_PRICE_USDT", "400"))
COURSE_INVITE_EXPIRE_SECONDS = int(os.getenv("COURSE_INVITE_EXPIRE_SECONDS", "3600"))
COURSE_TEST_MODE = os.getenv("COURSE_TEST_MODE", "false").lower() == "true"

# === Course (Psychology) ===
PSYCHOLOGY_CHANNEL_ID = int(os.getenv("PSYCHOLOGY_CHANNEL_ID", "-1003795418411"))

# === Course (Meteora) ===
METEORA_PAYMENT_WALLET = os.getenv("METEORA_PAYMENT_WALLET", "")
METEORA_FREE_CHANNEL_ID = int(os.getenv("METEORA_FREE_CHANNEL_ID", "0"))
METEORA_PAID_CHANNEL_ID = int(os.getenv("METEORA_PAID_CHANNEL_ID", "0"))
METEORA_PRICE_USDT = float(os.getenv("METEORA_PRICE_USDT", "100"))

# === Community (Wayan Premium) ===
COMMUNITY_PAYMENT_WALLET = os.getenv("COMMUNITY_PAYMENT_WALLET", "")
COMMUNITY_PRICE_USDT = float(os.getenv("COMMUNITY_PRICE_USDT", "200"))
COMMUNITY_CHANNEL_ID = int(os.getenv("COMMUNITY_CHANNEL_ID", "0"))
COMMUNITY_CHAT_ID = int(os.getenv("COMMUNITY_CHAT_ID", "-1003310009262"))

# USDT on Solana (SPL token mint)
USDT_MINT = "Es9vMFrzaCERmKfrNwnEUBLRKdPJg4kkjnBLTmM1JXWd"

# === Discounts ===
REFERRAL_COURSE_DISCOUNT = 0.20         # 20% off course for referred friend
# Referral community discounts (based on how many friends bought course):
# 1 friend → 20%, 2 friends → 50%, 3+ friends → 100% (free)
REFERRAL_COMMUNITY_TIERS = {1: 0.20, 2: 0.50, 3: 1.00}

# Promo codes: {code: {"discount": 0.10, "product": "course"}}
PROMO_CODES = {
    "KATE": {"discount": 0.10, "product": "course", "label": "промокод KATE"},
    "ANNA": {"discount": 0.10, "product": "course", "label": "промокод ANNA"},
    "CRYPTOENOT": {"discount": 0.10, "product": "course", "label": "промокод CRYPTOENOT"},
}
