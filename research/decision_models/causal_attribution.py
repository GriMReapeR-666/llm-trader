"""Causal Attribution & Market Move Fingerprint Extractor.

Answers: 'Why did this move occur?' via deterministic candlestick & cross-asset
feature extraction, without relying on ungrounded LLM prose:
- Extracts candlestick geometry: body/range ratio, upper/lower wick ratios, volume Z, pace.
- Measures cross-asset fingerprint: DXY, US 10Y Yield, WTI Crude Oil, Gold (XAU), and VIX.
- Evaluates Q1-Q7 typed causal attribution decisions in a single pass.
- Disambiguates FUNDAMENTAL_REPRICING vs POSITIONING_SQUEEZE vs STOP_CASCADE.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from research.decision_models.extended_taxonomy import (
    Q1_PrimaryDriver,
    Q5_MoveQuality,
    Q7_CandlestickSignature,
)


@dataclass(frozen=True)
class CandlestickBar:
    """Standard OHLCV bar representation."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: str
    symbol: str = "EURUSD"
    timeframe: str = "15m"


@dataclass(frozen=True)
class CausalFingerprint:
    """Deterministic mathematical signature of a market move."""
    symbol: str
    timestamp: str
    move_pips: float
    duration_minutes: float
    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    volume_z: float
    move_speed_pips_min: float
    retest_held: bool
    session_id: str
    cross_asset_correlations: Dict[str, float]
    candlestick_signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "move_pips": round(self.move_pips, 2),
            "duration_minutes": round(self.duration_minutes, 1),
            "body_ratio": round(self.body_ratio, 3),
            "upper_wick_ratio": round(self.upper_wick_ratio, 3),
            "lower_wick_ratio": round(self.lower_wick_ratio, 3),
            "volume_z": round(self.volume_z, 2),
            "move_speed_pips_min": round(self.move_speed_pips_min, 3),
            "retest_held": self.retest_held,
            "session_id": self.session_id,
            "cross_asset_correlations": {k: round(v, 3) for k, v in self.cross_asset_correlations.items()},
            "candlestick_signature": self.candlestick_signature,
        }


@dataclass(frozen=True)
class CausalAttributionResult:
    """Structured causal attribution outcome (Q1-Q7)."""
    fingerprint: CausalFingerprint
    primary_driver: str                        # Q1
    secondary_driver: str                      # Q2
    macro_attribution_strength: float          # Q3 (0.0 - 1.0)
    technical_attribution_strength: float      # Q4 (0.0 - 1.0)
    move_quality: str                          # Q5 (FUNDAMENTAL_REPRICING vs SQUEEZE vs CASCADE)
    narrative_updated: bool                    # Q6
    candlestick_signature: str                 # Q7
    attribution_confidence: float              # 0.0 - 1.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "primary_driver": self.primary_driver,
            "secondary_driver": self.secondary_driver,
            "macro_attribution_strength": round(self.macro_attribution_strength, 3),
            "technical_attribution_strength": round(self.technical_attribution_strength, 3),
            "move_quality": self.move_quality,
            "narrative_updated": self.narrative_updated,
            "candlestick_signature": self.candlestick_signature,
            "attribution_confidence": round(self.attribution_confidence, 3),
            "created_at": self.created_at,
        }


class CausalFingerprintExtractor:
    """Extracts deterministic candlestick geometry and cross-asset correlations."""

    def __init__(self, pip_size: float = 0.0001) -> None:
        self.pip_size = pip_size

    def extract_from_bars(
        self,
        bars: List[CandlestickBar],
        cross_asset_changes: Optional[Dict[str, float]] = None,
        session_id: str = "LONDON_NY_OVERLAP",
        rolling_volume_mean: float = 1000.0,
        rolling_volume_std: float = 250.0,
    ) -> CausalFingerprint:
        """Calculate mathematical fingerprint from window of bars."""
        if not bars:
            raise ValueError("Must provide at least one bar")

        start_bar = bars[0]
        end_bar = bars[-1]
        symbol = end_bar.symbol

        open_p = start_bar.open
        close_p = end_bar.close
        high_p = max(b.high for b in bars)
        low_p = min(b.low for b in bars)

        tot_range = max(high_p - low_p, self.pip_size * 0.1)
        body = abs(close_p - open_p)

        if close_p >= open_p:
            upper_wick = high_p - close_p
            lower_wick = open_p - low_p
        else:
            upper_wick = high_p - open_p
            lower_wick = close_p - low_p

        body_ratio = body / tot_range
        upper_wick_ratio = upper_wick / tot_range
        lower_wick_ratio = lower_wick / tot_range

        tot_vol = sum(b.volume for b in bars)
        vol_z = (tot_vol - rolling_volume_mean) / max(rolling_volume_std, 1.0)

        move_pips = (close_p - open_p) / self.pip_size
        duration_min = max(len(bars) * 15.0, 1.0)
        speed = abs(move_pips) / duration_min

        # Retest heuristic: did price retest the breakout level and hold?
        retest_held = lower_wick_ratio > 0.3 if move_pips > 0 else upper_wick_ratio > 0.3

        # Candlestick signature rule
        if body_ratio >= 0.70 and vol_z >= 1.0:
            sig = Q7_CandlestickSignature.IMPULSIVE_BREAK.value
        elif upper_wick_ratio >= 0.50 or lower_wick_ratio >= 0.50:
            sig = Q7_CandlestickSignature.V_REVERSAL.value
        elif body_ratio >= 0.50 and upper_wick_ratio <= 0.25 and lower_wick_ratio <= 0.25:
            sig = Q7_CandlestickSignature.GRIND_TREND.value
        else:
            sig = Q7_CandlestickSignature.EXHAUSTION_FADE.value

        cross_corrs = cross_asset_changes or {"DXY": 0.0, "US10Y": 0.0, "OIL": 0.0, "VIX": 0.0}

        return CausalFingerprint(
            symbol=symbol,
            timestamp=end_bar.timestamp,
            move_pips=move_pips,
            duration_minutes=duration_min,
            body_ratio=body_ratio,
            upper_wick_ratio=upper_wick_ratio,
            lower_wick_ratio=lower_wick_ratio,
            volume_z=vol_z,
            move_speed_pips_min=speed,
            retest_held=retest_held,
            session_id=session_id,
            cross_asset_correlations=cross_corrs,
            candlestick_signature=sig,
        )

    def attribute_move(
        self,
        fingerprint: CausalFingerprint,
        news_event_intensity: float = 0.0,    # 0.0 - 1.0 based on recent Tier-1 shock
        cot_crowding_percentile: float = 50.0,
    ) -> CausalAttributionResult:
        """Disambiguate the move into structured causal drivers and move quality."""
        # 1. Primary Driver Disambiguation
        if news_event_intensity >= 0.7:
            primary = Q1_PrimaryDriver.MACRO_DATA.value
            secondary = Q1_PrimaryDriver.FLOW_POSITIONING.value
            macro_str = news_event_intensity
            tech_str = 0.3
        elif cot_crowding_percentile >= 85.0 or cot_crowding_percentile <= 15.0:
            primary = Q1_PrimaryDriver.FLOW_POSITIONING.value
            secondary = Q1_PrimaryDriver.TECHNICAL_ORDERFLOW.value
            macro_str = 0.2
            tech_str = 0.8
        elif fingerprint.move_speed_pips_min >= 2.0:
            primary = Q1_PrimaryDriver.TECHNICAL_ORDERFLOW.value
            secondary = Q1_PrimaryDriver.MACRO_DATA.value
            macro_str = 0.4
            tech_str = 0.8
        else:
            primary = Q1_PrimaryDriver.TECHNICAL_ORDERFLOW.value
            secondary = Q1_PrimaryDriver.UNKNOWN.value
            macro_str = 0.1
            tech_str = 0.6

        # 2. Move Quality Classification (Crucial for D8 gating)
        # Squeeze check: fast move against crowded positioning
        if (cot_crowding_percentile >= 85.0 and fingerprint.move_pips < 0) or \
           (cot_crowding_percentile <= 15.0 and fingerprint.move_pips > 0):
            quality = Q5_MoveQuality.POSITIONING_SQUEEZE.value
        # Stop sweep check: long wick rejection
        elif fingerprint.upper_wick_ratio >= 0.55 or fingerprint.lower_wick_ratio >= 0.55:
            quality = Q5_MoveQuality.STOP_CASCADE.value
        # Fundamental repricing: high volume Z + macro news + high body ratio
        elif news_event_intensity >= 0.6 and fingerprint.volume_z >= 1.0 and fingerprint.body_ratio >= 0.6:
            quality = Q5_MoveQuality.FUNDAMENTAL_REPRICING.value
        # Ghost book: fast move with low volume
        elif fingerprint.volume_z <= -1.2:
            quality = Q5_MoveQuality.LIQUIDITY_GHOST.value
        else:
            quality = Q5_MoveQuality.NOISE.value

        narrative_update = quality == Q5_MoveQuality.FUNDAMENTAL_REPRICING.value

        return CausalAttributionResult(
            fingerprint=fingerprint,
            primary_driver=primary,
            secondary_driver=secondary,
            macro_attribution_strength=macro_str,
            technical_attribution_strength=tech_str,
            move_quality=quality,
            narrative_updated=narrative_update,
            candlestick_signature=fingerprint.candlestick_signature,
            attribution_confidence=round(min(1.0, 0.5 + 0.3 * abs(fingerprint.volume_z) + 0.2 * news_event_intensity), 3),
        )

    def extract(
        self,
        prices: Sequence[float],
        volumes: Optional[Sequence[float]] = None,
        symbol: str = "EURUSD",
        cross_asset_changes: Optional[Dict[str, float]] = None,
    ) -> CausalFingerprint:
        """Extract fingerprint from raw prices and optional volumes."""
        if not prices:
            raise ValueError("Must provide at least one price")
        vols = list(volumes) if volumes and len(volumes) == len(prices) else [1000.0] * len(prices)
        now_iso = datetime.now(timezone.utc).isoformat()
        bars = []
        for i, p in enumerate(prices):
            prev_p = prices[i - 1] if i > 0 else p
            h = max(p, prev_p) + self.pip_size * 0.5
            l = min(p, prev_p) - self.pip_size * 0.5
            bars.append(CandlestickBar(
                open=prev_p,
                high=h,
                low=l,
                close=p,
                volume=vols[i],
                timestamp=now_iso,
                symbol=symbol,
            ))
        return self.extract_from_bars(bars, cross_asset_changes=cross_asset_changes)


CausalMoveAttribution = CausalAttributionResult


def attribute_move(
    fingerprint: CausalFingerprint,
    news_event_intensity: float = 0.0,
    cot_crowding_percentile: float = 50.0,
) -> CausalAttributionResult:
    """Convenience module function to attribute move from fingerprint."""
    extractor = CausalFingerprintExtractor()
    return extractor.attribute_move(fingerprint, news_event_intensity, cot_crowding_percentile)
