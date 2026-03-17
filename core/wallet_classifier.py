"""Classify wallets as BOT or TRADER based on transaction patterns."""

import logging
from api import helius_client

logger = logging.getLogger(__name__)


async def classify_wallet(address: str) -> str:
    """
    Classify a wallet based on recent transaction patterns.
    Returns: BOT, LIKELY_BOT, or TRADER
    """
    try:
        txs = await helius_client.get_parsed_transactions(address, limit=50, tx_type="")
    except Exception as e:
        logger.warning(f"Classification failed for {address[:12]}: {e}")
        return "UNKNOWN"

    if not txs or len(txs) < 5:
        return "UNKNOWN"

    # Analyze patterns
    timestamps = sorted([tx.get("timestamp", 0) for tx in txs if tx.get("timestamp")])
    if len(timestamps) < 2:
        return "UNKNOWN"

    # Average interval between transactions (seconds)
    intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    avg_interval = sum(intervals) / len(intervals) if intervals else 9999

    # Time span covered
    time_span_hours = (timestamps[-1] - timestamps[0]) / 3600 if timestamps else 0

    # Estimated daily rate
    tx_per_day = len(txs) / max(time_span_hours / 24, 0.01)

    # Classification logic
    if tx_per_day > 500 and avg_interval < 60:
        return "BOT"
    elif tx_per_day > 100 and avg_interval < 300:
        return "LIKELY_BOT"
    else:
        return "TRADER"
