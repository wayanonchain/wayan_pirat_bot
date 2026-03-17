"""Format signal messages for Telegram — tier-based."""

from datetime import datetime


def format_time_ago(hours: float | None) -> str:
    if hours is None:
        return "Unknown"
    if hours < 1:
        return f"{int(hours * 60)}min ago"
    elif hours < 24:
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}h {m}min ago"
    else:
        d = int(hours / 24)
        h = int(hours % 24)
        return f"{d}d {h}h ago"


def format_usd(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount/1_000_000:,.2f}M"
    elif amount >= 1_000:
        return f"${amount:,.2f}"
    else:
        return f"${amount:.2f}"


def format_mcap(mcap: float | None) -> str:
    if mcap is None:
        return "N/A"
    if mcap >= 1_000_000_000:
        return f"${mcap/1_000_000_000:.2f}B"
    elif mcap >= 1_000_000:
        return f"${mcap/1_000_000:.2f}M"
    elif mcap >= 1_000:
        return f"${mcap/1_000:.2f}K"
    return f"${mcap:.0f}"


def wallet_type_label(wallet_type: str) -> str:
    return {
        "BOT": "BOT",
        "LIKELY_BOT": "BOT",
        "TRADER": "TRADER",
        "UNKNOWN": "—",
    }.get(wallet_type, "—")


def format_signal_message(signal: dict) -> str:
    """Format a FULL signal (Premium tier) — wallet addresses, PnL, types."""
    mode = signal["mode"]
    mode_label = f"M{mode}"

    token_symbol = signal.get("token_symbol", "???")
    token_address = signal["token_address"]
    mcap = signal.get("mcap")
    token_age = signal.get("token_age_hours")
    wallets = signal.get("wallets", [])

    lines = [
        f"[{mode_label}] <b>{token_symbol}</b>",
        "",
        f"<code>{token_address}</code>",
        "",
    ]

    if token_age is not None:
        lines.append(f"Created: {format_time_ago(token_age)}")

    if mcap is not None:
        lines.append(f"MCAP: {format_mcap(mcap)}")

    lines.append(f"SM wallets: {len(wallets)}")
    lines.append("")

    for w in wallets:
        addr = w["address"]
        short_addr = f"{addr[:6]}...{addr[-4:]}"
        wtype = wallet_type_label(w.get("wallet_type", "UNKNOWN"))
        amount = format_usd(w.get("amount_usd", 0))
        mcap_at = format_mcap(w.get("mcap_at_buy"))
        pnl = w.get("pnl", 0)

        line = f"[{wtype}] <code>{short_addr}</code> bought {amount}"
        if mcap_at != "N/A":
            line += f", MCAP: {mcap_at}"
        if pnl:
            line += f" (PnL: {format_usd(pnl)})"
        lines.append(line)

    lines.append("")

    dexs = f"https://dexscreener.com/solana/{token_address}"
    solscan = f"https://solscan.io/token/{token_address}"
    gmgn = f"https://gmgn.ai/sol/token/{token_address}"
    photon = f"https://photon-sol.tinyastro.io/en/lp/{token_address}"

    lines.append(
        f'<a href="{dexs}">DEXS</a> | '
        f'<a href="{solscan}">SolScan</a> | '
        f'<a href="{gmgn}">GmGn</a> | '
        f'<a href="{photon}">Photon</a>'
    )

    return "\n".join(lines)


def format_signal_message_free(signal: dict) -> str:
    """Format a FREE tier signal — no wallet addresses, no PnL. Delivered with delay."""
    mode = signal["mode"]
    mode_label = f"M{mode}"

    token_symbol = signal.get("token_symbol", "???")
    token_address = signal["token_address"]
    mcap = signal.get("mcap")
    token_age = signal.get("token_age_hours")
    wallets = signal.get("wallets", [])

    lines = [
        f"[{mode_label}] <b>{token_symbol}</b>  <i>(delayed)</i>",
        "",
        f"<code>{token_address}</code>",
        "",
    ]

    if token_age is not None:
        lines.append(f"Created: {format_time_ago(token_age)}")

    if mcap is not None:
        lines.append(f"MCAP: {format_mcap(mcap)}")

    lines.append(f"SM wallets: {len(wallets)}")

    total_usd = sum(w.get("amount_usd", 0) for w in wallets)
    lines.append(f"Total buy: {format_usd(total_usd)}")

    lines.append("")
    lines.append("<i>Wallet addresses, PnL, Accumulation Score — Premium</i>")
    lines.append("")

    dexs = f"https://dexscreener.com/solana/{token_address}"
    solscan = f"https://solscan.io/token/{token_address}"
    gmgn = f"https://gmgn.ai/sol/token/{token_address}"
    photon = f"https://photon-sol.tinyastro.io/en/lp/{token_address}"

    lines.append(
        f'<a href="{dexs}">DEXS</a> | '
        f'<a href="{solscan}">SolScan</a> | '
        f'<a href="{gmgn}">GmGn</a> | '
        f'<a href="{photon}">Photon</a>'
    )

    lines.append("\nUpgrade: /plan")

    return "\n".join(lines)


def format_stats_message(stats: dict) -> str:
    """Format bot statistics message."""
    lines = [
        "<b>Bot Statistics</b>",
        "",
        f"Wallets monitored: {stats.get('active_wallets', 0)}",
        f"Signals today: {stats.get('signals_today', 0)}",
        f"Signals 24h: {stats.get('signals_24h', 0)}",
        "",
        f"Active: {stats.get('active', 0)}",
        f"Inactive: {stats.get('inactive', 0)}",
        f"Rejected: {stats.get('rejected', 0)}",
    ]
    return "\n".join(lines)
