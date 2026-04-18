"""
Rugcheck.xyz API client for Solana token safety checks.

Public endpoint (no API key required):
    https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary

Returns a normalized RugCheckReport with the signals most relevant to the
accumulation/analysis flow: LP lock status, mint/freeze authority, top holder
concentration, and the raw Rugcheck risk list for display.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional
import httpx

log = logging.getLogger(__name__)

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"


@dataclass
class RugCheckReport:
    available: bool               # False = Rugcheck not reachable or token not on Solana
    score: int                    # 0..1000, lower is safer on Rugcheck scale
    risk_level: str               # "safe" | "caution" | "warning" | "danger" | "unknown"
    mint_authority_renounced: bool
    freeze_authority_renounced: bool
    lp_locked_pct: float          # 0..1 — fraction of LP locked/burned
    top_holder_pct: float         # 0..1 — largest non-LP holder share
    top10_holder_pct: float       # 0..1 — top-10 combined (excluding LPs)
    rugged: bool
    risks: list[str] = field(default_factory=list)
    raw_url: str = ""


def _interpret_score(score: int) -> str:
    """Rugcheck publishes an internal 0..1000 score; map to our levels."""
    if score <= 0:      return "unknown"
    if score < 100:     return "safe"
    if score < 500:     return "caution"
    if score < 1000:    return "warning"
    return "danger"


async def fetch_rugcheck(
    client: httpx.AsyncClient,
    mint_address: str,
    chain: str = "solana",
) -> RugCheckReport:
    """Fetch a Rugcheck summary for a Solana mint. Safe to call for other
    chains — returns an unavailable report.
    """
    if (chain or "").lower() != "solana":
        return RugCheckReport(
            available=False, score=0, risk_level="unknown",
            mint_authority_renounced=False, freeze_authority_renounced=False,
            lp_locked_pct=0.0, top_holder_pct=0.0, top10_holder_pct=0.0,
            rugged=False,
        )

    url = f"{RUGCHECK_BASE}/tokens/{mint_address}/report/summary"
    try:
        r = await client.get(url, timeout=10)
        if r.status_code == 404:
            return RugCheckReport(
                available=False, score=0, risk_level="unknown",
                mint_authority_renounced=False, freeze_authority_renounced=False,
                lp_locked_pct=0.0, top_holder_pct=0.0, top10_holder_pct=0.0,
                rugged=False,
                raw_url=f"https://rugcheck.xyz/tokens/{mint_address}",
            )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.debug("Rugcheck fetch failed for %s: %s", mint_address, e)
        return RugCheckReport(
            available=False, score=0, risk_level="unknown",
            mint_authority_renounced=False, freeze_authority_renounced=False,
            lp_locked_pct=0.0, top_holder_pct=0.0, top10_holder_pct=0.0,
            rugged=False,
            raw_url=f"https://rugcheck.xyz/tokens/{mint_address}",
        )

    score = int(data.get("score_normalised") or data.get("score") or 0)
    risks_raw = data.get("risks") or []
    risk_names = [r.get("name", "") for r in risks_raw if r.get("name")]

    # Authorities
    token_meta = data.get("token") or data.get("tokenMeta") or {}
    mint_auth    = token_meta.get("mintAuthority")
    freeze_auth  = token_meta.get("freezeAuthority")
    mint_ren     = mint_auth   in (None, "", "null")
    freeze_ren   = freeze_auth in (None, "", "null")

    # LP lock — Rugcheck returns markets[].lp.lpLockedPct on full report,
    # but the summary exposes an aggregate. Be defensive about shape.
    lp_locked = 0.0
    markets   = data.get("markets") or []
    if markets:
        pct_values = []
        for m in markets:
            lp = m.get("lp") or {}
            v = lp.get("lpLockedPct")
            if v is not None:
                try:
                    pct_values.append(float(v) / 100.0)
                except (TypeError, ValueError):
                    pass
        if pct_values:
            # Use max — a single locked LP is usually the "real" one
            lp_locked = max(pct_values)
    else:
        aggregate = data.get("lpLockedPct")
        if aggregate is not None:
            try:
                lp_locked = float(aggregate) / 100.0
            except (TypeError, ValueError):
                lp_locked = 0.0

    # Holder concentration
    top_holders = data.get("topHolders") or []
    non_lp = [h for h in top_holders if not h.get("insider") and not h.get("isLP", False)]
    top_pct   = 0.0
    top10_pct = 0.0
    if non_lp:
        try:
            top_pct   = float(non_lp[0].get("pct", 0) or 0) / 100.0
            top10_pct = sum(float(h.get("pct", 0) or 0) for h in non_lp[:10]) / 100.0
        except (TypeError, ValueError):
            pass

    rugged = bool(data.get("rugged"))

    return RugCheckReport(
        available=True,
        score=score,
        risk_level=_interpret_score(score),
        mint_authority_renounced=mint_ren,
        freeze_authority_renounced=freeze_ren,
        lp_locked_pct=lp_locked,
        top_holder_pct=top_pct,
        top10_holder_pct=top10_pct,
        rugged=rugged,
        risks=risk_names,
        raw_url=f"https://rugcheck.xyz/tokens/{mint_address}",
    )
