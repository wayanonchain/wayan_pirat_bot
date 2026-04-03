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
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

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

# Webhook server
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# === Log Chat (team group for activity logs) ===
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID", "-1003833809842")

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
}
