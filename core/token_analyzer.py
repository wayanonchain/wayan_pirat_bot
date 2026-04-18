"""
Token Analyzer — Accumulation Score calculator.
Ported from Taras_bot analyzer.py + adapted for async API clients.

Combines data from Birdeye, SolanaTracker, and Helius to produce
an Accumulation Score (0-100) for a given token.
"""

import logging
import time
import httpx

from config.settings import BIRDEYE_API_KEY, SOLANATRACKER_API_KEY, HELIUS_API_KEY

logger = logging.getLogger(__name__)

# In-memory cache for analysis results (token_address -> (result, timestamp)).
# Entries are pruned when the cache exceeds _CACHE_MAX; without this it grows
# unbounded over weeks of uptime.
_analysis_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300  # 5 minutes
_CACHE_MAX = 2000


def _prune_analysis_cache(now: float) -> None:
    if len(_analysis_cache) < _CACHE_MAX:
        return
    stale = [k for k, (_, ts) in _analysis_cache.items() if now - ts >= CACHE_TTL]
    for k in stale:
        del _analysis_cache[k]
    # If still over the cap (all entries fresh), drop the oldest.
    if len(_analysis_cache) >= _CACHE_MAX:
        oldest = sorted(_analysis_cache.items(), key=lambda kv: kv[1][1])
        for k, _ in oldest[: len(_analysis_cache) - _CACHE_MAX // 2]:
            del _analysis_cache[k]


def safe_get(data, *keys, default=0):
    """Safely extract nested keys."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


async def analyze_token(token_address: str) -> dict | None:
    """
    Run full Accumulation Score analysis for a token.
    Returns score dict or None on failure.
    Caches results for 5 minutes.
    """
    now = time.time()
    if token_address in _analysis_cache:
        cached, ts = _analysis_cache[token_address]
        if now - ts < CACHE_TTL:
            return cached

    try:
        birdeye_data = await _fetch_birdeye_data(token_address)
        st_data = await _fetch_solanatracker_data(token_address)

        # Only fetch Helius holder data if preliminary score looks promising
        preliminary_score = _quick_score(birdeye_data, st_data)
        helius_data = None
        if preliminary_score >= 40:
            helius_data = await _fetch_helius_holders(token_address)

        result = calculate_accumulation_score(birdeye_data, st_data, helius_data)
        _analysis_cache[token_address] = (result, now)
        _prune_analysis_cache(now)
        return result

    except Exception as e:
        logger.error(f"Token analysis error for {token_address[:12]}: {e}")
        return None


def _quick_score(birdeye_data: dict, st_data: dict) -> int:
    """Quick preliminary score from volumes + buyers only (no Helius call)."""
    overview = birdeye_data.get("overview") or {}
    st_stats = (st_data or {}).get("stats") or {}

    score = 0
    buy_vol = safe_get(overview, "vBuy24hUSD")
    sell_vol = safe_get(overview, "vSell24hUSD")
    if sell_vol > 0 and buy_vol / sell_vol >= 1.2:
        score += 10

    buyers = safe_get(st_stats, "24h", "buyers")
    sellers = safe_get(st_stats, "24h", "sellers")
    if sellers > 0 and buyers / sellers >= 1.2:
        score += 10

    change_24h = safe_get(overview, "priceChange24hPercent")
    if -10 < change_24h <= 20:
        score += 10

    wallet_change = safe_get(overview, "uniqueWallet24hChangePercent")
    if wallet_change > 0:
        score += 10

    return score


def calculate_accumulation_score(birdeye_data: dict, st_data: dict = None,
                                  helius_data: dict = None) -> dict:
    """
    Accumulation Score (0-100) based on 3 data sources.
    +20: Buy/Sell volume ratio 24h
    +20: Buyers > Sellers 24h
    +20: Top traders mostly buying
    +20: Unique wallets growing
    +20: Price in recovery phase
    """
    overview = birdeye_data.get("overview") or {}
    top_traders_data = birdeye_data.get("top_traders") or {}
    st_info = (st_data or {}).get("info") or {}
    st_stats = (st_data or {}).get("stats") or {}
    helius = helius_data or {}

    score = 0
    details = []
    api_status = {
        "birdeye": bool(overview),
        "solanatracker": bool(st_info or st_stats),
        "helius": bool(helius),
    }

    # --- 1. Buy/Sell Volume Ratio 24h (20 pts) ---
    buy_vol = safe_get(overview, "vBuy24hUSD")
    sell_vol = safe_get(overview, "vSell24hUSD")
    vol_source = "BE"
    if buy_vol == 0 and sell_vol == 0:
        buy_vol = safe_get(st_stats, "24h", "volume", "buys")
        sell_vol = safe_get(st_stats, "24h", "volume", "sells")
        vol_source = "ST"

    vol_ratio = buy_vol / sell_vol if sell_vol > 0 else (99.0 if buy_vol > 0 else 0)
    vol_score = 20 if vol_ratio >= 2.0 else 15 if vol_ratio >= 1.5 else 10 if vol_ratio >= 1.2 else 5 if vol_ratio >= 1.0 else 0
    score += vol_score
    details.append(f"Vol Ratio 24h: {vol_ratio:.2f} [{vol_source}] (+{vol_score})")

    # --- 2. Buyers vs Sellers 24h (20 pts) ---
    buyers_24h = safe_get(st_stats, "24h", "buyers")
    sellers_24h = safe_get(st_stats, "24h", "sellers")
    bs_source = "ST"
    if buyers_24h == 0 and sellers_24h == 0:
        buyers_24h = safe_get(overview, "buy24h")
        sellers_24h = safe_get(overview, "sell24h")
        bs_source = "BE"

    if sellers_24h > 0:
        trade_ratio = buyers_24h / sellers_24h
    elif buyers_24h > 0:
        trade_ratio = 99.0
    else:
        trade_ratio = 0

    trades_score = 20 if trade_ratio >= 1.5 else 15 if trade_ratio >= 1.2 else 10 if trade_ratio >= 1.0 else 5 if trade_ratio >= 0.8 else 0
    score += trades_score
    details.append(f"Buyers/Sellers 24h: {buyers_24h}/{sellers_24h} = {trade_ratio:.2f} [{bs_source}] (+{trades_score})")

    # --- 3. Top traders buy vs sell (20 pts) ---
    traders_list = []
    if isinstance(top_traders_data, dict):
        traders_list = top_traders_data.get("items", []) or []
    elif isinstance(top_traders_data, list):
        traders_list = top_traders_data

    top_buyers = sum(1 for t in traders_list[:10] if isinstance(t, dict) and (t.get("volumeBuy", 0) or 0) > (t.get("volumeSell", 0) or 0))
    top_sellers = sum(1 for t in traders_list[:10] if isinstance(t, dict) and (t.get("volumeSell", 0) or 0) > (t.get("volumeBuy", 0) or 0))

    total_top = top_buyers + top_sellers
    top_score = 0
    if total_top > 0:
        buy_pct = top_buyers / total_top
        top_score = 20 if buy_pct >= 0.7 else 15 if buy_pct >= 0.6 else 10 if buy_pct >= 0.5 else 0
    score += top_score
    details.append(f"Top10 traders: {top_buyers} buy / {top_sellers} sell [BE] (+{top_score})")

    # --- 4. Unique Wallets growth (20 pts) ---
    wallet_change_pct = safe_get(overview, "uniqueWallet24hChangePercent")
    unique_24h = safe_get(overview, "uniqueWallet24h")

    wallet_score = 20 if wallet_change_pct > 10 else 15 if wallet_change_pct > 5 else 10 if wallet_change_pct > 0 else 5 if wallet_change_pct > -10 else 0
    score += wallet_score
    details.append(f"Wallets 24h: {unique_24h} ({wallet_change_pct:+.1f}%) [BE] (+{wallet_score})")

    # --- 5. Price phase (20 pts) ---
    change_4h = safe_get(overview, "priceChange4hPercent")
    change_8h = safe_get(overview, "priceChange8hPercent")
    change_24h = safe_get(overview, "priceChange24hPercent")

    phase_score = 0
    if 0 < change_24h <= 20 and change_8h > 0:
        phase_score = 20
    elif 0 < change_24h <= 30:
        phase_score = 15
    elif -10 < change_24h <= 0 and change_8h > 0:
        phase_score = 15
    elif -20 < change_24h <= -10:
        phase_score = 10 if change_4h > 0 else 5
    score += phase_score
    details.append(f"Price: 4h={change_4h:+.1f}% 8h={change_8h:+.1f}% 24h={change_24h:+.1f}% [BE] (+{phase_score})")

    # --- Recommendation ---
    if score >= 80:
        level, recommendation = "STRONG", "STRONG SIGNAL"
    elif score >= 60:
        level, recommendation = "ACCUMULATION", "ACCUMULATION"
    elif score >= 40:
        level, recommendation = "WATCH", "WATCH"
    else:
        level, recommendation = "NOTHING", "NO SIGNAL"

    # Collect metrics
    price = safe_get(overview, "price")
    mc = safe_get(overview, "marketCap")
    liquidity = safe_get(overview, "liquidity")
    volume_24h = safe_get(overview, "v24hUSD")
    holder_count = safe_get(overview, "holder")
    name = safe_get(overview, "name", default="Unknown")
    symbol = safe_get(overview, "symbol", default="???")

    # SolanaTracker extras
    top10_pct = safe_get(st_info, "risk", "top10")
    dev_pct = safe_get(st_info, "risk", "dev", "percentage")
    lp_burn = 0
    pools = st_info.get("pools", []) if isinstance(st_info, dict) else []
    if pools and isinstance(pools, list) and len(pools) > 0 and isinstance(pools[0], dict):
        lp_burn = pools[0].get("lpBurn", 0)

    # Helius extras
    helius_top10_pct = helius.get("top10_pct", 0)
    helius_top20_pct = helius.get("top20_pct", 0)
    helius_whale_count = helius.get("whale_count", 0)
    helius_holders = helius.get("total_holders_fetched", 0)

    return {
        "score": score,
        "level": level,
        "recommendation": recommendation,
        "details": details,
        "api_status": api_status,
        "metrics": {
            "name": name, "symbol": symbol, "price": price,
            "market_cap": mc, "liquidity": liquidity, "volume_24h": volume_24h,
            "price_change_24h": change_24h, "holder_count": holder_count,
            "buy_volume_24h": buy_vol, "sell_volume_24h": sell_vol,
            "vol_ratio": vol_ratio, "buyers_24h": buyers_24h, "sellers_24h": sellers_24h,
            "unique_wallets_24h": unique_24h, "top_buyers": top_buyers, "top_sellers": top_sellers,
            "top10_holder_pct": top10_pct, "dev_pct": dev_pct,
            "lp_burn_pct": lp_burn,
            "helius_top10_pct": helius_top10_pct, "helius_top20_pct": helius_top20_pct,
            "helius_whale_count": helius_whale_count, "helius_holders": helius_holders,
        },
    }


# === Async API fetchers ===

async def _fetch_birdeye_data(token_address: str) -> dict:
    """Fetch Birdeye overview + top traders (2 API calls)."""
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
    }
    base = "https://public-api.birdeye.so"
    result = {"overview": None, "top_traders": None}

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        # Token overview
        try:
            resp = await client.get(f"{base}/defi/token_overview", params={"address": token_address})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    result["overview"] = data.get("data")
        except Exception as e:
            logger.warning(f"Birdeye overview error: {e}")

        # Top traders
        try:
            resp = await client.get(f"{base}/defi/v2/tokens/top_traders", params={"address": token_address})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    result["top_traders"] = data.get("data")
        except Exception as e:
            logger.warning(f"Birdeye top_traders error: {e}")

    return result


async def _fetch_solanatracker_data(token_address: str) -> dict:
    """Fetch SolanaTracker token info + stats (2 API calls)."""
    headers = {"x-api-key": SOLANATRACKER_API_KEY, "accept": "application/json"}
    base = "https://data.solanatracker.io"
    result = {"info": None, "stats": None}

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        try:
            resp = await client.get(f"{base}/tokens/{token_address}")
            if resp.status_code == 200:
                result["info"] = resp.json()
        except Exception as e:
            logger.warning(f"ST info error: {e}")

        try:
            resp = await client.get(f"{base}/stats/{token_address}")
            if resp.status_code == 200:
                result["stats"] = resp.json()
        except Exception as e:
            logger.warning(f"ST stats error: {e}")

    return result


async def _fetch_helius_holders(token_address: str) -> dict | None:
    """Fetch holder analysis from Helius DAS API."""
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    identity_base = f"https://api.helius.xyz/v1/wallet"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Get token accounts
            resp = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenAccounts",
                "params": {"mint": token_address, "page": 1, "limit": 1000},
            })

            if resp.status_code != 200:
                return None

            data = resp.json()
            result = data.get("result")
            if not result:
                return None

            accounts = result.get("token_accounts", [])
            if not accounts:
                return None

            accounts.sort(key=lambda x: x.get("amount", 0), reverse=True)
            total_amount = sum(a.get("amount", 0) for a in accounts)
            if total_amount == 0:
                return None

            top10 = accounts[:10]
            top10_pct = sum(a.get("amount", 0) for a in top10) / total_amount * 100

            top20 = accounts[:20]
            top20_pct = sum(a.get("amount", 0) for a in top20) / total_amount * 100

            whale_threshold = total_amount * 0.001
            whale_count = sum(1 for a in accounts if a.get("amount", 0) >= whale_threshold)

            return {
                "total_holders_fetched": len(accounts),
                "top10_pct": top10_pct,
                "top20_pct": top20_pct,
                "whale_count": whale_count,
            }

    except Exception as e:
        logger.warning(f"Helius holder analysis error: {e}")
        return None


# === Formatting ===

def fmt_usd(val):
    if val is None or val == 0:
        return "N/A"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.4f}" if val < 0.01 else f"${val:.2f}"


def fmt_num(val):
    if val is None or val == 0:
        return "N/A"
    if val >= 1_000_000:
        return f"{val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val/1_000:.1f}K"
    return str(int(val))


def format_score_message(token_address: str, result: dict) -> str:
    """Format Accumulation Score into Telegram HTML message."""
    m = result["metrics"]
    score = result["score"]
    level = result["level"]

    level_icons = {
        "STRONG": "\U0001f525",
        "ACCUMULATION": "\U0001f49a",
        "WATCH": "\U0001f49b",
        "NOTHING": "\u2764\ufe0f",
    }
    icon = level_icons.get(level, "\u2764\ufe0f")

    lines = [
        f"{icon} <b>{m['name']} ({m['symbol']}) — {score}/100</b>",
        f"\u27a1\ufe0f {result['recommendation']}",
        "",
        f"Price: {fmt_usd(m['price'])}  |  MC: {fmt_usd(m['market_cap'])}",
        f"Liq: {fmt_usd(m['liquidity'])}  |  Vol 24h: {fmt_usd(m['volume_24h'])}",
        f"Price 24h: {m['price_change_24h']:+.1f}%",
        "",
        f"Holders: {fmt_num(m['holder_count'])}",
        f"Buy Vol: {fmt_usd(m['buy_volume_24h'])}  |  Sell Vol: {fmt_usd(m['sell_volume_24h'])}",
        f"Vol Ratio: {m['vol_ratio']:.2f}",
        f"Buyers: {fmt_num(m['buyers_24h'])}  |  Sellers: {fmt_num(m['sellers_24h'])}",
        f"Wallets 24h: {fmt_num(m['unique_wallets_24h'])}",
        f"Top10 Traders: {m['top_buyers']} buy / {m['top_sellers']} sell",
    ]

    # SolanaTracker section
    st_lines = []
    if m.get("top10_holder_pct"):
        st_lines.append(f"Top10 Holders: {m['top10_holder_pct']:.1f}%")
    if m.get("dev_pct"):
        st_lines.append(f"Dev: {m['dev_pct']:.2f}%")
    if m.get("lp_burn_pct"):
        st_lines.append(f"LP Burn: {m['lp_burn_pct']}%")
    if st_lines:
        lines.append(f"\n\U0001f4ca SolanaTracker:")
        lines.extend(f"  {sl}" for sl in st_lines)

    # Helius section
    if m.get("helius_holders"):
        lines.append(f"\n\U0001f50d Helius (on-chain):")
        lines.append(f"  Holders (fetched): {m['helius_holders']}")
        if m.get("helius_top10_pct"):
            lines.append(f"  Top10 hold: {m['helius_top10_pct']:.2f}%")
        if m.get("helius_whale_count"):
            lines.append(f"  Whales (>0.1%): {m['helius_whale_count']}")

    lines.append("\nBreakdown:")
    for d in result["details"]:
        lines.append(f"  {d}")

    # API status
    api = result.get("api_status", {})
    sources = []
    for key, label in [("birdeye", "Birdeye"), ("solanatracker", "ST"), ("helius", "Helius")]:
        sources.append(f"{label} \u2705" if api.get(key) else f"{label} \u274c")
    lines.append(f"\nAPIs: {' | '.join(sources)}")
    lines.append(f"\n<code>{token_address}</code>")

    return "\n".join(lines)


def format_score_short(result: dict) -> str:
    """Short one-line score for embedding in signal messages."""
    score = result["score"]
    level = result["level"]
    icons = {"STRONG": "\U0001f525", "ACCUMULATION": "\U0001f49a", "WATCH": "\U0001f49b", "NOTHING": "\u2764\ufe0f"}
    icon = icons.get(level, "")
    m = result["metrics"]
    vol_r = f"{m['vol_ratio']:.1f}" if m.get("vol_ratio") else "?"
    return (
        f"\n{icon} <b>Accumulation Score: {score}/100 — {result['recommendation']}</b>\n"
        f"Vol Ratio: {vol_r} | Buyers/Sellers: {m.get('buyers_24h', '?')}/{m.get('sellers_24h', '?')} | "
        f"Wallets 24h: {fmt_num(m.get('unique_wallets_24h', 0))}"
    )
