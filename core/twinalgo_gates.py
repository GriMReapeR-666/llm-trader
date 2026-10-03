"""TwinAlgo expectancy gates + confidence calibration (P06 Sec. II-III).

Deterministic, LLM-independent slice of TwinAlgo V1.02-3 DA (Thophet 2026):

  - expectancy math: E(k) = r*(W*k - L) (paper Sec. I.B/III.B); breakeven
    k* = L/W (1.50 at W=0.40). EV computed by REUSING
    `core.metrics.calculate_expected_value` - never reimplemented.
  - RR floor: prompt-level hard constraint RR >= 1.5 already enforced by
    `core.trading_constraints.MIN_RISK_REWARD_RATIO`; `rr_gate()` exposes
    the same check for non-pipeline callers (orchestrators, audits).
  - confidence calibration (paper Sec. II.C): NEVER 7+ when CHoCH fired,
    price in premium, MTF misaligned, or RR < 1.5; min-confidence floor
    (default 6/10) maps below-floor outputs to WAIT.
  - TradeMemory loss clamp (paper Sec. II.C): >=3 losses in the window ->
    max confidence 5 + FORCE_LOWER flag.
  - breakout filter (paper Sec. III.D): body > 50% of range AND
    volume > 1.3x average kills false breakouts.
  - 6-state regime classifier (paper Sec. II.B) with deliberate UNKNOWN
    for mid-ADX ambiguity (improvement over default-to-RANGING).

References:
    Thophet, N. (2026). "TwinAlgo ..." (P06).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.metrics import calculate_expected_value


class TwinRegime(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    BREAKOUT = "BREAKOUT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TwinGates:
    min_rr: float = 1.5          # hard prompt-level floor (paper Sec. II.C)
    target_rr: float = 3.0       # operational target 1:3-1:4
    min_confidence: int = 6      # below floor -> WAIT (paper Sec. III.A)
    max_positions: int = 3       # position context cap (paper step 5)
    loss_clamp_n: int = 3        # >=3 similar losses -> clamp (TradeMemory)
    loss_clamp_cap: int = 5      # hard clamp level
    loss_window: int = 10
    breakout_body_frac: float = 0.50  # body > 50% of range
    breakout_vol_mult: float = 1.30   # volume > 1.3x average


def breakeven_rr(win_rate: float) -> float:
    """k* = L/W: minimum R:R for non-negative expectancy (paper Sec. III.B)."""
    w = max(1e-9, min(1.0 - 1e-9, float(win_rate)))
    return round((1.0 - w) / w, 4)


def expectancy_per_r(win_rate: float, rr: float) -> float:
    """E(k)/r via the shared metrics engine (paper Table 1: +0.60R @1:3, +1.00R @1:4)."""
    return round(calculate_expected_value(win_rate, avg_win_r=rr, avg_loss_r=1.0), 4)


def rr_gate(rr: float, gates: TwinGates = TwinGates()) -> bool:
    """Hard RR >= 1.5 constraint (mirrors trading_constraints for non-pipeline use)."""
    return rr >= gates.min_rr


def calibrate_confidence(raw: int, *, choch: bool = False, premium: bool = False,
                         mtf_aligned: bool = True, rr: float = 99.0,
                         gates: TwinGates = TwinGates()) -> int:
    """Apply the paper's NEVER-7+ prohibitions (paper Sec. II.C)."""
    cap = 10
    if choch or premium or not mtf_aligned or rr < gates.min_rr:
        cap = min(cap, 6)
    return max(1, min(int(raw), cap))


def action_gate(confidence: int, gates: TwinGates = TwinGates()) -> str:
    """Below min-confidence -> WAIT (paper Layer-3 response parser)."""
    return "WAIT" if confidence < gates.min_confidence else "PROCEED"


def loss_clamp_active(outcomes: List[float],
                      gates: TwinGates = TwinGates()) -> bool:
    """TradeMemory: >=3 losses in window -> clamp + FORCE_LOWER (paper Sec. II.C)."""
    recent = outcomes[-gates.loss_window:]
    return sum(1 for r in recent if r < 0) >= gates.loss_clamp_n


def clamped_confidence(raw: int, outcomes: List[float],
                       gates: TwinGates = TwinGates()) -> int:
    if loss_clamp_active(outcomes, gates):
        return min(int(raw), gates.loss_clamp_cap)
    return int(raw)


def breakout_ok(body: float, total_range: float, volume: float, avg_volume: float,
                gates: TwinGates = TwinGates()) -> bool:
    """False-breakout filter: body > 50% range AND volume > 1.3x avg (paper Sec. III.D)."""
    if total_range <= 0 or avg_volume <= 0:
        return False
    return (body / total_range) > gates.breakout_body_frac and \
           (volume / avg_volume) > gates.breakout_vol_mult


def classify_regime(*, adx: float, adx_trend: float = 25.0, adx_range: float = 20.0,
                    atr: float = 0.0, atr_p85: float = 0.0,
                    bb_expanding: bool = False, bb_contracting: bool = False,
                    ema_bull: bool = False, ema_bear: bool = False,
                    bos_bull: bool = False, bos_bear: bool = False) -> TwinRegime:
    """Hierarchical 6-state classifier (paper Sec. II.B, UNKNOWN-first for ambiguity)."""
    if atr_p85 > 0 and atr > atr_p85:
        return TwinRegime.VOLATILE
    if bb_expanding and adx > adx_trend and (bos_bull or bos_bear):
        return TwinRegime.BREAKOUT
    if adx > adx_trend and (ema_bull or ema_bear) and not (ema_bull and ema_bear):
        return TwinRegime.TRENDING_BULL if ema_bull else TwinRegime.TRENDING_BEAR
    if adx < adx_range and bb_contracting:
        return TwinRegime.RANGING
    return TwinRegime.UNKNOWN  # deliberate: never default to RANGING when ambiguous
