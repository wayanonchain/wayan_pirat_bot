"""
Smart Money DB integration — queries WAYNE_PIRATE bot.db for recent buys
by curated smart-money wallets.

This is the killer feature: instead of inferring "smart money" from public
trade feeds (noisy), we check how many of our 3k+ curated SM wallets have
actually bought this token in a given window.

Path to the DB is read from env var WAYAN_SM_DB_PATH. When missing or the
DB is unreachable, all functions return neutral empty results so the rest
of the flow degrades gracefully.
"""
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class SmartMoneyWalletBuy:
    wallet: str
    amount_usd: float
    timestamp: int          # unix seconds
    mcap_at_buy: float
    nansen_label: str = ""
    roi: float = 0.0
    win_rate: float = 0.0


@dataclass
class SmartMoneyDBResult:
    available: bool                 # False = DB not configured or not reachable
    unique_wallets: int             # count of distinct SM wallets that bought in window
    total_buy_usd: float
    avg_buy_usd: float
    buys: list[SmartMoneyWalletBuy] = field(default_factory=list)
    top_wallets: list[SmartMoneyWalletBuy] = field(default_factory=list)  # top 5 by amount


def _db_path() -> Optional[str]:
    path = os.getenv("WAYAN_SM_DB_PATH", "").strip()
    if not path:
        return None
    if not os.path.exists(path):
        log.debug("WAYAN_SM_DB_PATH=%s does not exist", path)
        return None
    return path


def _open_ro() -> Optional[sqlite3.Connection]:
    """Open the SM DB in read-only mode so the agent can never mutate the
    production bot's state.
    """
    path = _db_path()
    if not path:
        return None
    try:
        # URI form lets us force read-only and avoid locking the writer
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=0", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.warning("Could not open SM DB at %s: %s", path, e)
        return None


def count_sm_buys(token_address: str, hours: int = 24) -> SmartMoneyDBResult:
    """Count distinct active SM wallets that bought `token_address` in the
    last `hours` hours, along with aggregate and top-N wallets.
    """
    conn = _open_ro()
    if conn is None:
        return SmartMoneyDBResult(
            available=False, unique_wallets=0,
            total_buy_usd=0.0, avg_buy_usd=0.0,
        )

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        rows = conn.execute(
            """
            SELECT
                tb.wallet_address AS wallet,
                SUM(tb.amount_usd) AS total_usd,
                MAX(tb.timestamp)  AS last_ts,
                MIN(tb.mcap_at_buy) AS mcap_at_buy,
                COALESCE(w.nansen_label, '') AS label,
                COALESCE(w.roi, 0)       AS roi,
                COALESCE(w.win_rate, 0)  AS win_rate
            FROM token_buys tb
            INNER JOIN wallets w ON w.address = tb.wallet_address
            WHERE tb.token_address = ?
              AND tb.timestamp >= ?
              AND UPPER(w.status) = 'ACTIVE'
            GROUP BY tb.wallet_address
            ORDER BY total_usd DESC
            """,
            (token_address, cutoff_str),
        ).fetchall()
    except sqlite3.Error as e:
        log.warning("SM DB query failed for %s: %s", token_address, e)
        try:
            conn.close()
        except Exception:
            pass
        return SmartMoneyDBResult(
            available=False, unique_wallets=0,
            total_buy_usd=0.0, avg_buy_usd=0.0,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    buys: list[SmartMoneyWalletBuy] = []
    for row in rows:
        ts_val = row["last_ts"]
        ts_unix = 0
        if ts_val:
            try:
                if isinstance(ts_val, (int, float)):
                    ts_unix = int(ts_val)
                else:
                    ts_unix = int(datetime.fromisoformat(str(ts_val)).timestamp())
            except Exception:
                ts_unix = int(time.time())
        buys.append(SmartMoneyWalletBuy(
            wallet=row["wallet"],
            amount_usd=float(row["total_usd"] or 0),
            timestamp=ts_unix,
            mcap_at_buy=float(row["mcap_at_buy"] or 0),
            nansen_label=row["label"] or "",
            roi=float(row["roi"] or 0),
            win_rate=float(row["win_rate"] or 0),
        ))

    total_usd = sum(b.amount_usd for b in buys)
    avg_usd = total_usd / len(buys) if buys else 0.0
    return SmartMoneyDBResult(
        available=True,
        unique_wallets=len(buys),
        total_buy_usd=total_usd,
        avg_buy_usd=avg_usd,
        buys=buys,
        top_wallets=buys[:5],
    )


def score_bonus_from_sm(result: SmartMoneyDBResult) -> tuple[float, list[str]]:
    """Return (bonus_points, reason_lines) to merge into the detector score.

    Tuned so that verified SM activity matters more than any technical
    pattern alone — 3+ curated wallets in the last 24h is a very strong
    signal and should be able to promote WATCHLIST → SIGNAL on its own.
    """
    if not result.available or result.unique_wallets == 0:
        return 0.0, []

    bonus = 0.0
    lines: list[str] = []

    if result.unique_wallets >= 10:
        bonus += 30
        lines.append(f"🔥 {result.unique_wallets} SM-кошельков закупились за 24h")
    elif result.unique_wallets >= 5:
        bonus += 20
        lines.append(f"🟢 {result.unique_wallets} SM-кошельков закупились за 24h")
    elif result.unique_wallets >= 3:
        bonus += 12
        lines.append(f"🟢 {result.unique_wallets} SM-кошельков закупились за 24h")
    elif result.unique_wallets >= 1:
        bonus += 5
        lines.append(f"🟡 {result.unique_wallets} SM-кошелёк закупился за 24h")

    if result.total_buy_usd >= 50_000:
        bonus += 5
        lines.append(f"💰 SM закупились на ${result.total_buy_usd:,.0f}")
    elif result.total_buy_usd >= 10_000:
        bonus += 2

    return bonus, lines
