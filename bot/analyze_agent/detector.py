"""
Accumulation Pattern Detector — Phase 2 (Post-ATH re-accumulation)

Detects 4 stages:
  1. Shakeout      — big drop from ATH (50–97%)
  2. Consolidation — tight range, volume drying up
  3. Spring        — false breakdown + recovery
  4. Breakout      — volume spike + range top breach
"""
import logging
import time
from typing import Optional
from models import (
    OHLCV, TokenMetadata, ConsolidationZone, SpringEvent,
    AccumulationAnalysis, SpringStatus, SignalTier,
)
from agent_config import AgentConfig

log = logging.getLogger(__name__)


class AccumulationDetector:
    def __init__(self, config: AgentConfig):
        self.cfg = config

    def analyze(
        self,
        token: TokenMetadata,
        daily_candles: list[OHLCV],
        hourly_candles: list[OHLCV],
        stored_ath_mcap: float = 0.0,
        sm_bonus: float = 0.0,
        sm_reason_lines: Optional[list[str]] = None,
        sm_wallets_24h: int = 0,
        sm_total_buy_usd: float = 0.0,
    ) -> AccumulationAnalysis:
        """
        Full accumulation pattern analysis.
        Returns AccumulationAnalysis with score and tier.
        """
        passed = []
        failed = []
        hard_failed = []   # hard filters that must block the signal
        cfg = self.cfg

        # ── Use stored ATH if better than current ────────────────
        ath_mcap = max(token.ath_mcap, stored_ath_mcap)
        if ath_mcap > token.ath_mcap:
            token.ath_mcap = ath_mcap

        drawdown = self._compute_drawdown(token.current_mcap, ath_mcap)

        # ── Hard filters ─────────────────────────────────────────

        if not (cfg.min_mcap_usd <= token.current_mcap <= cfg.max_mcap_usd):
            msg = f"mcap ${token.current_mcap:,.0f} out of range"
            failed.append(msg)
            hard_failed.append(msg)

        if not (cfg.min_drawdown_from_ath <= drawdown <= cfg.max_drawdown_from_ath):
            msg = f"drawdown {drawdown:.0%} out of range [{cfg.min_drawdown_from_ath:.0%}–{cfg.max_drawdown_from_ath:.0%}]"
            failed.append(msg)
            hard_failed.append(msg)
        else:
            passed.append(f"drawdown {drawdown:.0%} from ATH ✅")

        if token.liquidity_usd < cfg.min_liquidity_usd:
            msg = f"liquidity ${token.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}"
            failed.append(msg)
            hard_failed.append(msg)
        else:
            passed.append(f"liquidity ${token.liquidity_usd:,.0f} ✅")

        if not (cfg.min_token_age_days <= token.age_days <= cfg.max_token_age_days):
            msg = f"age {token.age_days:.0f}d out of range [{cfg.min_token_age_days}–{cfg.max_token_age_days}d]"
            failed.append(msg)
            hard_failed.append(msg)
        else:
            passed.append(f"age {token.age_days:.0f}d ✅")

        if token.holders > 0:
            if not (cfg.min_holders <= token.holders <= cfg.max_holders):
                msg = f"holders {token.holders:,} out of range"
                failed.append(msg)
                hard_failed.append(msg)
            else:
                passed.append(f"holders {token.holders:,} ✅")

        # ── Consolidation zone detection ──────────────────────────
        consolidation = self._detect_consolidation(daily_candles)
        spring        = self._detect_spring(daily_candles, hourly_candles, consolidation)
        no_new_low    = self._count_no_new_low_days(daily_candles)
        vol_spike     = self._compute_volume_spike(daily_candles, hourly_candles)

        if consolidation is None:
            failed.append("no consolidation zone detected")
        else:
            passed.append(f"consolidation {consolidation.duration_days:.0f}d range {consolidation.range_pct:.0%} ✅")
            if consolidation.volume_at_drop > 0:
                vol_dryup_ratio = consolidation.avg_volume_usd / consolidation.volume_at_drop
                if vol_dryup_ratio > cfg.volume_dryup_ratio:
                    failed.append(f"volume not dried up ({vol_dryup_ratio:.0%} of drop volume)")
                else:
                    passed.append(f"volume dried up ({vol_dryup_ratio:.0%} of drop volume) ✅")

        if no_new_low < cfg.no_new_low_days:
            failed.append(f"new low {cfg.no_new_low_days - no_new_low}d ago (need {cfg.no_new_low_days}d clear)")
        else:
            passed.append(f"no new low for {no_new_low}d ✅")

        if spring.status == SpringStatus.CONFIRMED:
            passed.append(f"spring confirmed ({spring.breach_pct:.0%} breach, recovery {spring.recovery_hours:.0f}h) ✅")
        elif spring.status == SpringStatus.DETECTED:
            passed.append(f"spring detected (breach {spring.breach_pct:.0%}, not yet recovered)")

        if vol_spike >= cfg.volume_spike_multiplier:
            passed.append(f"volume spike x{vol_spike:.1f} ✅")
        else:
            if vol_spike >= 1.5:
                passed.append(f"volume rising x{vol_spike:.1f} (weak)")
            else:
                failed.append(f"no volume spike (x{vol_spike:.1f})")

        # ── Score ─────────────────────────────────────────────────
        score = self._compute_score(
            drawdown=drawdown,
            consolidation=consolidation,
            spring=spring,
            no_new_low=no_new_low,
            vol_spike=vol_spike,
            token=token,
        )

        # Smart-money bonus from curated wallet DB (caps total at 100)
        if sm_bonus > 0:
            score = min(100.0, score + sm_bonus)
            passed.extend(sm_reason_lines or [])

        # ── Tier ─────────────────────────────────────────────────
        tier = self._tier(score)

        # Hard filters override tier: if any hard filter failed,
        # do not emit a signal regardless of score.
        if hard_failed:
            tier = SignalTier.NOISE

        # ── Take profit levels (mcap targets) ────────────────────
        tp = self._take_profit_levels(token.current_mcap, ath_mcap)

        return AccumulationAnalysis(
            token=token,
            drawdown_from_ath=drawdown,
            consolidation=consolidation,
            spring=spring,
            volume_spike_ratio=vol_spike,
            no_new_low_days=no_new_low,
            score=score,
            tier=tier,
            passed_filters=passed,
            failed_filters=failed,
            take_profit_levels=tp,
            sm_wallets_24h=sm_wallets_24h,
            sm_total_buy_usd=sm_total_buy_usd,
            sm_reason_lines=list(sm_reason_lines or []),
        )

    # ─────────────────────────────────────────────────────────────
    # Pattern detection helpers
    # ─────────────────────────────────────────────────────────────

    def _compute_drawdown(self, current_mcap: float, ath_mcap: float) -> float:
        if ath_mcap <= 0:
            return 0.0
        return max(0.0, 1.0 - current_mcap / ath_mcap)

    def _detect_consolidation(self, candles: list[OHLCV]) -> Optional[ConsolidationZone]:
        """
        Find longest recent range where (high - low) / low <= max_range_pct.
        Looks at last 90 candles max.
        """
        cfg = self.cfg
        if len(candles) < cfg.consolidation_min_days:
            return None

        # Scan from recent candles backwards to find the consolidation window
        best: Optional[ConsolidationZone] = None
        n = len(candles)
        window_size = min(n, 90)

        # Try different window end points (most recent end)
        for end in range(n - 1, n - 10, -1):
            for start in range(end - cfg.consolidation_min_days, max(0, end - cfg.consolidation_max_days) - 1, -1):
                window = candles[start:end + 1]
                if len(window) < cfg.consolidation_min_days:
                    continue

                highs  = [c.high for c in window]
                lows   = [c.low  for c in window]
                zone_h = max(highs)
                zone_l = min(lows)
                if zone_l <= 0:
                    continue

                range_pct = (zone_h - zone_l) / zone_l
                if range_pct > cfg.consolidation_max_range_pct:
                    continue

                duration = len(window)
                avg_vol  = sum(c.volume for c in window) / len(window)

                # volume during the "drop" period (candles before this window)
                drop_candles = candles[:start] if start > 0 else []
                vol_at_drop  = 0.0
                if drop_candles:
                    # Take the highest volume period before the consolidation
                    lookback = min(len(drop_candles), 14)
                    vol_at_drop = sum(c.volume for c in drop_candles[-lookback:]) / lookback

                zone = ConsolidationZone(
                    high=zone_h,
                    low=zone_l,
                    range_pct=range_pct,
                    duration_days=float(duration),
                    avg_volume_usd=avg_vol,
                    volume_at_drop=vol_at_drop,
                )

                if best is None or duration > best.duration_days:
                    best = zone

        return best

    def _detect_spring(
        self,
        daily_candles: list[OHLCV],
        hourly_candles: list[OHLCV],
        consolidation: Optional[ConsolidationZone],
    ) -> SpringEvent:
        if consolidation is None or not daily_candles:
            return SpringEvent(SpringStatus.NONE, 0, 0, 0, 0)

        zone_low = consolidation.low
        breach_threshold = zone_low * (1.0 - self.cfg.spring_min_pct)
        recovery_level   = zone_low * 0.97  # "returned to zone"

        # Scan last 30 daily candles for a spring pattern
        check_candles = daily_candles[-30:]
        for i, candle in enumerate(check_candles):
            low = candle.low
            close = candle.close

            if low > breach_threshold:
                continue

            breach_pct = (zone_low - low) / zone_low
            if breach_pct > self.cfg.spring_max_pct:
                continue

            breach_ts = candle.timestamp
            days_ago  = (len(check_candles) - 1 - i)

            # Same-candle recovery — use hourly candles to measure real hours
            if close >= recovery_level:
                recovery_h = self._measure_recovery_hours(
                    hourly_candles, breach_ts, recovery_level, fallback_hours=12.0,
                )
                return SpringEvent(
                    status=SpringStatus.CONFIRMED,
                    low_price=low,
                    breach_pct=breach_pct,
                    recovery_hours=recovery_h,
                    days_ago=float(days_ago),
                )

            # Check subsequent daily candles for recovery
            for j in range(1, 4):
                if i + j >= len(check_candles):
                    break
                next_c = check_candles[i + j]
                if next_c.close >= recovery_level:
                    recovery_h = self._measure_recovery_hours(
                        hourly_candles, breach_ts, recovery_level,
                        fallback_hours=float(j * 24),
                    )
                    return SpringEvent(
                        status=SpringStatus.CONFIRMED,
                        low_price=low,
                        breach_pct=breach_pct,
                        recovery_hours=recovery_h,
                        days_ago=float(days_ago),
                    )

            # Breach detected but not yet recovered
            if i == len(check_candles) - 1:
                return SpringEvent(
                    status=SpringStatus.DETECTED,
                    low_price=low,
                    breach_pct=breach_pct,
                    recovery_hours=0.0,
                    days_ago=0.0,
                )

        return SpringEvent(SpringStatus.NONE, 0, 0, 0, 0)

    def _measure_recovery_hours(
        self,
        hourly_candles: list[OHLCV],
        breach_ts: int,
        recovery_level: float,
        fallback_hours: float,
    ) -> float:
        """Find the first hourly candle after breach_ts where close >= recovery_level.

        Returns hours elapsed from breach candle to recovery. Falls back to
        the coarse daily-based estimate if hourly data is insufficient.
        """
        if not hourly_candles:
            return fallback_hours
        after = [c for c in hourly_candles if c.timestamp >= breach_ts]
        if not after:
            return fallback_hours
        for c in after:
            if c.close >= recovery_level:
                delta_sec = c.timestamp - breach_ts
                if delta_sec <= 0:
                    return fallback_hours
                return round(delta_sec / 3600.0, 1)
        return fallback_hours

    def _count_no_new_low_days(self, candles: list[OHLCV]) -> int:
        """Count days since the most recent all-time low in the window.

        Walks candles forward, resetting the counter every time a new
        running minimum appears. Returns the number of candles since
        that final minimum (i.e., how many days the low has held).
        """
        if not candles:
            return 0
        running_low = float("inf")
        last_low_idx = 0
        for i, c in enumerate(candles):
            if c.low < running_low:
                running_low = c.low
                last_low_idx = i
        return len(candles) - 1 - last_low_idx

    def _compute_volume_spike(
        self,
        daily_candles: list[OHLCV],
        hourly_candles: list[OHLCV],
    ) -> float:
        """
        Compare recent N hours volume vs average consolidation volume.
        Returns multiplier (1.0 = no spike, 3.0 = 3x spike).
        """
        cfg = self.cfg
        hours_back = cfg.volume_spike_lookback_hours

        if hourly_candles:
            recent_vol = sum(c.volume for c in hourly_candles[-hours_back:])
            if len(hourly_candles) > hours_back * 2:
                baseline  = sum(c.volume for c in hourly_candles[-(hours_back * 8):-hours_back])
                per_period_baseline = baseline / 7  # 7 prior periods
                if per_period_baseline > 0:
                    return recent_vol / per_period_baseline
        elif daily_candles and len(daily_candles) >= 7:
            recent_vol  = daily_candles[-1].volume
            baseline_n  = min(14, len(daily_candles) - 1)
            avg_baseline = sum(c.volume for c in daily_candles[-baseline_n - 1:-1]) / baseline_n
            if avg_baseline > 0:
                return recent_vol / avg_baseline

        return 1.0

    # ─────────────────────────────────────────────────────────────
    # Scoring
    # ─────────────────────────────────────────────────────────────

    def _compute_score(
        self,
        drawdown: float,
        consolidation: Optional[ConsolidationZone],
        spring: SpringEvent,
        no_new_low: int,
        vol_spike: float,
        token: TokenMetadata,
    ) -> float:
        score = 0.0
        cfg   = self.cfg

        # ── Drawdown depth (0–20) ─────────────────────────────────
        if drawdown >= 0.90:   score += 20
        elif drawdown >= 0.80: score += 17
        elif drawdown >= 0.70: score += 13
        elif drawdown >= 0.60: score += 9
        elif drawdown >= 0.50: score += 5

        # ── Consolidation quality (0–25) ──────────────────────────
        if consolidation:
            # Duration bonus
            if consolidation.duration_days >= 30:   score += 12
            elif consolidation.duration_days >= 21: score += 9
            elif consolidation.duration_days >= 14: score += 6

            # Tightness bonus
            if consolidation.range_pct <= 0.15:   score += 8
            elif consolidation.range_pct <= 0.20: score += 5
            elif consolidation.range_pct <= 0.30: score += 2

            # Volume dryup bonus
            if consolidation.volume_at_drop > 0:
                ratio = consolidation.avg_volume_usd / consolidation.volume_at_drop
                if ratio <= 0.10:   score += 5
                elif ratio <= 0.20: score += 3
                elif ratio <= 0.30: score += 1

        # ── Spring event (0–20) ───────────────────────────────────
        if spring.status == SpringStatus.CONFIRMED:
            score += 20
            if spring.days_ago <= 7:  score += 3   # fresh spring
        elif spring.status == SpringStatus.DETECTED:
            score += 8   # partial credit — not confirmed yet

        # ── No-new-low streak (0–10) ──────────────────────────────
        if no_new_low >= 21:   score += 10
        elif no_new_low >= 14: score += 7
        elif no_new_low >= 10: score += 4

        # ── Volume spike (0–20) ───────────────────────────────────
        if vol_spike >= 5.0:   score += 20
        elif vol_spike >= 4.0: score += 16
        elif vol_spike >= 3.0: score += 12
        elif vol_spike >= 2.0: score += 7
        elif vol_spike >= 1.5: score += 3

        # ── Liquidity quality (0–5) ───────────────────────────────
        liq_ratio = token.liquidity_usd / max(token.current_mcap, 1)
        if liq_ratio >= 0.20:   score += 5
        elif liq_ratio >= 0.12: score += 3
        elif liq_ratio >= 0.08: score += 1

        return round(min(score, 100.0), 1)

    def _tier(self, score: float) -> SignalTier:
        cfg = self.cfg
        if score >= cfg.strong_score:    return SignalTier.STRONG
        if score >= cfg.signal_score:    return SignalTier.SIGNAL
        if score >= cfg.watchlist_score: return SignalTier.WATCHLIST
        return SignalTier.NOISE

    # ─────────────────────────────────────────────────────────────
    # Take profit helpers
    # ─────────────────────────────────────────────────────────────

    def _take_profit_levels(self, current_mcap: float, ath_mcap: float) -> list[float]:
        """Return list of TP market caps: x2, x4, ATH, x2 ATH."""
        return [
            current_mcap * 2,
            current_mcap * 4,
            ath_mcap,                # return to ATH
            ath_mcap * 2,            # x2 new ATH
        ]
