"""Logic-Q inspired Neuro-Symbolic Crash Detector.

Implements the program sketch paradigm from Logic-Q (AAAI 2025) for
detecting market trends and adjusting trading parameters post-hoc.

Key innovation: A lightweight program sketch (13-23 float32 params)
identifies 5 market trends using 3 indicators, then adjusts trading
parameters via Bayesian Optimization on a small validation window.

References:
    - Logic-Q: Improving DRL-based Quantitative Trading via Program
      Sketch-based Tuning (Li et al., AAAI 2025)
    - Program Synthesis by Sketching (Solar-Lezama, 2008)

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │           NEURO-SYMBOLIC CRASH DETECTOR              │
    │                                                      │
    │  Input: OHLCV close prices (window G=20 bars)       │
    │      ↓                                               │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 1: Compute 3 Indicators         │           │
    │  │   vol_g  = variance(close, G)        │           │
    │  │   dr_g   = downside_risk(close, G)   │           │
    │  │   gr_g   = growth_rate(close, G)     │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 2: Program Sketch Evaluation    │           │
    │  │   5 conditional branches:            │           │
    │  │   1. RAPID_DESCEND: vol>Θ0 & dr>Θ1  │           │
    │  │   2. STEADY_DESCEND: vol<Θ2 & dr>Θ3 │           │
    │  │   3. RAPID_ASCEND: vol>Θ4 & gr>Θ5   │           │
    │  │   4. STEADY_ASCEND: vol<Θ6 & gr>Θ7  │           │
    │  │   5. OSCILLATION: default            │           │
    │  │   Returns: trend τ + params ϕ_τ      │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 3: Policy Tuning                 │           │
    │  │   risk_mult = f(τ)                    │           │
    │  │   position_scale = g(τ)               │           │
    │  │   confidence_adj = h(τ)               │           │
    │  │   entry_gate = bool(τ)                │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  Output: TrendSignal with tuning parameters          │
    └─────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WINDOW = 20          # Lookback window for indicators
STATE_FILE = "crash_detector_state.json"


class MarketTrend(str, Enum):
    """Five market trends from Logic-Q's program sketch."""
    RAPID_DESCEND = "RAPID_DESCEND"
    STEADY_DESCEND = "STEADY_DESCEND"
    RAPID_ASCEND = "RAPID_ASCEND"
    STEADY_ASCEND = "STEADY_ASCEND"
    OSCILLATION = "OSCILLATION"


# ---------------------------------------------------------------------------
# Indicators (Logic-Q §3.1)
# ---------------------------------------------------------------------------

def calculate_volatility(closes: np.ndarray, window: int = DEFAULT_WINDOW) -> float:
    """Volatility: variance of close prices over window G.
    
    vol_g(t) = (1/G) * sum((x_i - x_bar)^2)
    
    Logic-Q uses this to measure degree of price fluctuation.
    """
    if len(closes) < 2:
        return 0.0
    recent = closes[-window:] if len(closes) >= window else closes
    mean = np.mean(recent)
    return float(np.mean((recent - mean) ** 2))


def calculate_downside_risk(closes: np.ndarray, window: int = DEFAULT_WINDOW) -> float:
    """Downside risk: variance of negative returns below mean.
    
    dr_g(t) = sqrt((1/n) * sum_{x_t < x_bar} (x_bar - x_t)^2)
    
    Logic-Q uses this to measure potential loss exposure.
    """
    if len(closes) < 2:
        return 0.0
    recent = closes[-window:] if len(closes) >= window else closes
    mean = np.mean(recent)
    below_mean = recent[recent < mean]
    if len(below_mean) == 0:
        return 0.0
    return float(np.sqrt(np.mean((mean - below_mean) ** 2)))


def calculate_growth_rate(closes: np.ndarray, window: int = DEFAULT_WINDOW) -> float:
    """Growth rate: percentage increase over time span.
    
    gr_g(t) = (X_t - X_start) / X_start * 100%  if X_t > X_start
            = 0%                                  if X_t <= X_start
    
    Logic-Q uses this to measure market ascending tendency.
    """
    if len(closes) < 2:
        return 0.0
    recent = closes[-window:] if len(closes) >= window else closes
    start = recent[0]
    end = recent[-1]
    if start <= 0:
        return 0.0
    gr = (end - start) / start * 100.0
    return max(0.0, gr)


# ---------------------------------------------------------------------------
# Program Sketch (Logic-Q §3.2, Figure 3)
# ---------------------------------------------------------------------------

@dataclass
class ProgramSketchParameters:
    """Tunable parameters for the program sketch.
    
    Logic-Q uses 13 float32 parameters for single-model RL:
    - 5 trend conditions × 2 thresholds each = 10 thresholds
    - 5 tuning parameters (ϕ) for each trend = 5
    Total: 15 parameters (we use 15 for 5 trends)
    """
    # Rapid Descend thresholds
    rapid_descend_vol_threshold: float = 0.001    # Θ0: volatility > this
    rapid_descend_dr_threshold: float = 0.002     # Θ1: downside risk > this
    
    # Steady Descend thresholds
    steady_descend_vol_threshold: float = 0.0005  # Θ2: volatility < this
    steady_descend_dr_threshold: float = 0.001    # Θ3: downside risk > this
    
    # Rapid Ascend thresholds
    rapid_ascend_vol_threshold: float = 0.001     # Θ4: volatility > this
    rapid_ascend_gr_threshold: float = 2.0        # Θ5: growth rate > this
    
    # Steady Ascend thresholds
    steady_ascend_vol_threshold: float = 0.0005   # Θ6: volatility < this
    steady_ascend_gr_threshold: float = 1.0       # Θ7: growth rate > this
    
    # Tuning parameters (ϕ_τ) for each trend
    # Risk multiplier: how much to scale risk_per_trade
    phi_rapid_descend_risk: float = 0.3           # Very defensive
    phi_steady_descend_risk: float = 0.6          # Moderately defensive
    phi_rapid_ascend_risk: float = 1.2            # Aggressive
    phi_steady_ascend_risk: float = 1.0           # Normal
    phi_oscillation_risk: float = 0.7             # Cautious
    
    # Position scale: how much to scale position size
    phi_rapid_descend_pos: float = 0.2            # Minimal position
    phi_steady_descend_pos: float = 0.5           # Half position
    phi_rapid_ascend_pos: float = 1.0             # Full position
    phi_steady_ascend_pos: float = 1.0            # Full position
    phi_oscillation_pos: float = 0.6              # Reduced position
    
    # Confidence adjustment: added to LLM confidence threshold
    phi_rapid_descend_conf: float = 0.2           # Require higher confidence
    phi_steady_descend_conf: float = 0.1          # Slightly higher
    phi_rapid_ascend_conf: float = -0.05          # Slightly lower OK
    phi_steady_ascend_conf: float = 0.0           # No change
    phi_oscillation_conf: float = 0.15            # Higher confidence needed
    
    # Entry gate: 1.0 = allow entries, 0.0 = block entries
    phi_rapid_descend_entry: float = 0.0          # BLOCK entries
    phi_steady_descend_entry: float = 1.0         # Allow
    phi_rapid_ascend_entry: float = 1.0           # Allow
    phi_steady_ascend_entry: float = 1.0          # Allow
    phi_oscillation_entry: float = 1.0            # Allow
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProgramSketchParameters":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})
    
    def param_count(self) -> int:
        return len([f for f in self.__dataclass_fields__])


# ---------------------------------------------------------------------------
# Trend Signal
# ---------------------------------------------------------------------------

@dataclass
class TrendSignal:
    """Output of the program sketch evaluation."""
    trend: MarketTrend
    risk_multiplier: float
    position_scale: float
    confidence_adjustment: float
    entries_allowed: bool
    volatility: float
    downside_risk: float
    growth_rate: float
    confidence_score: float  # How confident we are in the trend identification
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trend": self.trend.value,
            "risk_multiplier": round(self.risk_multiplier, 4),
            "position_scale": round(self.position_scale, 4),
            "confidence_adjustment": round(self.confidence_adjustment, 4),
            "entries_allowed": self.entries_allowed,
            "volatility": round(self.volatility, 8),
            "downside_risk": round(self.downside_risk, 8),
            "growth_rate": round(self.growth_rate, 4),
            "confidence_score": round(self.confidence_score, 4),
        }


# ---------------------------------------------------------------------------
# Program Sketch Evaluator
# ---------------------------------------------------------------------------

class ProgramSketchEvaluator:
    """Evaluates the program sketch to identify market trend.
    
    This is the symbolic part of the neuro-symbolic system.
    It applies the 5 conditional branches from Logic-Q's program sketch
    to determine the current market trend.
    """
    
    def __init__(self, params: Optional[ProgramSketchParameters] = None):
        self.params = params or ProgramSketchParameters()
    
    def evaluate(self, closes: np.ndarray, window: int = DEFAULT_WINDOW) -> TrendSignal:
        """Evaluate the program sketch on recent close prices.
        
        Args:
            closes: Array of historical close prices
            window: Lookback window for indicator calculation
            
        Returns:
            TrendSignal with identified trend and tuning parameters
        """
        vol = calculate_volatility(closes, window)
        dr = calculate_downside_risk(closes, window)
        gr = calculate_growth_rate(closes, window)
        
        p = self.params
        
        # Evaluate conditions in priority order (rapid before steady)
        # Logic-Q Figure 3: program sketch branches
        
        # 1. RAPID_DESCEND: high volatility + high downside risk
        if vol > p.rapid_descend_vol_threshold and dr > p.rapid_descend_dr_threshold:
            return TrendSignal(
                trend=MarketTrend.RAPID_DESCEND,
                risk_multiplier=p.phi_rapid_descend_risk,
                position_scale=p.phi_rapid_descend_pos,
                confidence_adjustment=p.phi_rapid_descend_conf,
                entries_allowed=p.phi_rapid_descend_entry > 0.5,
                volatility=vol, downside_risk=dr, growth_rate=gr,
                confidence_score=min(1.0, (vol / max(p.rapid_descend_vol_threshold, 1e-10)) * 0.5 +
                                    (dr / max(p.rapid_descend_dr_threshold, 1e-10)) * 0.5),
            )
        
        # 2. STEADY_DESCEND: low volatility + high downside risk
        if vol < p.steady_descend_vol_threshold and dr > p.steady_descend_dr_threshold:
            return TrendSignal(
                trend=MarketTrend.STEADY_DESCEND,
                risk_multiplier=p.phi_steady_descend_risk,
                position_scale=p.phi_steady_descend_pos,
                confidence_adjustment=p.phi_steady_descend_conf,
                entries_allowed=p.phi_steady_descend_entry > 0.5,
                volatility=vol, downside_risk=dr, growth_rate=gr,
                confidence_score=min(1.0, (dr / max(p.steady_descend_dr_threshold, 1e-10)) * 0.7 +
                                    (1.0 - vol / max(p.steady_descend_vol_threshold, 1e-10)) * 0.3),
            )
        
        # 3. RAPID_ASCEND: high volatility + high growth
        if vol > p.rapid_ascend_vol_threshold and gr > p.rapid_ascend_gr_threshold:
            return TrendSignal(
                trend=MarketTrend.RAPID_ASCEND,
                risk_multiplier=p.phi_rapid_ascend_risk,
                position_scale=p.phi_rapid_ascend_pos,
                confidence_adjustment=p.phi_rapid_ascend_conf,
                entries_allowed=p.phi_rapid_ascend_entry > 0.5,
                volatility=vol, downside_risk=dr, growth_rate=gr,
                confidence_score=min(1.0, (gr / max(p.rapid_ascend_gr_threshold, 1e-10)) * 0.6 +
                                    (vol / max(p.rapid_ascend_vol_threshold, 1e-10)) * 0.4),
            )
        
        # 4. STEADY_ASCEND: low volatility + positive growth
        if vol < p.steady_ascend_vol_threshold and gr > p.steady_ascend_gr_threshold:
            return TrendSignal(
                trend=MarketTrend.STEADY_ASCEND,
                risk_multiplier=p.phi_steady_ascend_risk,
                position_scale=p.phi_steady_ascend_pos,
                confidence_adjustment=p.phi_steady_ascend_conf,
                entries_allowed=p.phi_steady_ascend_entry > 0.5,
                volatility=vol, downside_risk=dr, growth_rate=gr,
                confidence_score=min(1.0, (gr / max(p.steady_ascend_gr_threshold, 1e-10)) * 0.5 +
                                    0.5),
            )
        
        # 5. OSCILLATION: default
        return TrendSignal(
            trend=MarketTrend.OSCILLATION,
            risk_multiplier=p.phi_oscillation_risk,
            position_scale=p.phi_oscillation_pos,
            confidence_adjustment=p.phi_oscillation_conf,
            entries_allowed=p.phi_oscillation_entry > 0.5,
            volatility=vol, downside_risk=dr, growth_rate=gr,
            confidence_score=0.5,
        )


# ---------------------------------------------------------------------------
# Crash Detector (Main Class)
# ---------------------------------------------------------------------------

@dataclass
class CrashDetectorState:
    """Persistent state for the crash detector."""
    last_trend: str = MarketTrend.OSCILLATION.value
    last_eval_time: float = 0.0
    trend_history: List[Dict[str, Any]] = field(default_factory=list)
    consecutive_rapid_descends: int = 0
    max_consecutive_rapid_descends: int = 0
    total_evaluations: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_trend": self.last_trend,
            "last_eval_time": self.last_eval_time,
            "trend_history": self.trend_history[-50:],  # Keep last 50
            "consecutive_rapid_descends": self.consecutive_rapid_descends,
            "max_consecutive_rapid_descends": self.max_consecutive_rapid_descends,
            "total_evaluations": self.total_evaluations,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CrashDetectorState":
        return cls(
            last_trend=d.get("last_trend", MarketTrend.OSCILLATION.value),
            last_eval_time=d.get("last_eval_time", 0.0),
            trend_history=d.get("trend_history", []),
            consecutive_rapid_descends=d.get("consecutive_rapid_descends", 0),
            max_consecutive_rapid_descends=d.get("max_consecutive_rapid_descends", 0),
            total_evaluations=d.get("total_evaluations", 0),
        )


class CrashDetector(StoreBackedState):
    """Logic-Q inspired neuro-symbolic crash detector.
    
    Combines a lightweight program sketch (symbolic) with Bayesian
    optimization to identify market trends and adjust trading parameters.
    
    Key properties:
    - 15 tunable parameters (extremely lightweight)
    - Identifies 5 market trends
    - Adjusts risk, position size, confidence, and entry gates
    - Optimized via Bayesian Optimization on small validation windows
    """

    store_key = "crash_detector_state"
    
    def __init__(
        self,
        state_file: str = STATE_FILE,
        window: int = DEFAULT_WINDOW,
        params: Optional[ProgramSketchParameters] = None,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.window = window
        self.evaluator = ProgramSketchEvaluator(params)
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)
    
    def _load_state(self) -> CrashDetectorState:
        try:
            data = self._load_state_dict()
            if data:
                return CrashDetectorState.from_dict(data)
        except Exception as exc:
            logger.warning("state load failed: %s", exc)
        return CrashDetectorState()
    
    def _save_state(self) -> None:
        try:
            self._save_state_dict(self.state.to_dict())
        except Exception as exc:
            logger.debug("Failed to save crash detector state: %s", exc)
    
    def evaluate(self, closes: List[float]) -> TrendSignal:
        """Evaluate market trend from close prices.
        
        Args:
            closes: List of historical close prices (at least 5)
            
        Returns:
            TrendSignal with trend identification and tuning parameters
        """
        if len(closes) < 5:
            return TrendSignal(
                trend=MarketTrend.OSCILLATION,
                risk_multiplier=1.0, position_scale=1.0,
                confidence_adjustment=0.0, entries_allowed=True,
                volatility=0.0, downside_risk=0.0, growth_rate=0.0,
                confidence_score=0.0,
            )
        
        arr = np.array(closes, dtype=float)
        signal = self.evaluator.evaluate(arr, self.window)
        
        # Update state
        self.state.total_evaluations += 1
        self.state.last_trend = signal.trend.value
        self.state.last_eval_time = time.time()
        
        # Track consecutive rapid descends (crash streak)
        if signal.trend == MarketTrend.RAPID_DESCEND:
            self.state.consecutive_rapid_descends += 1
            self.state.max_consecutive_rapid_descends = max(
                self.state.max_consecutive_rapid_descends,
                self.state.consecutive_rapid_descends,
            )
        else:
            self.state.consecutive_rapid_descends = 0
        
        # Record in history
        self.state.trend_history.append({
            "trend": signal.trend.value,
            "volatility": round(signal.volatility, 8),
            "downside_risk": round(signal.downside_risk, 8),
            "growth_rate": round(signal.growth_rate, 4),
            "risk_multiplier": round(signal.risk_multiplier, 4),
            "position_scale": round(signal.position_scale, 4),
            "entries_allowed": signal.entries_allowed,
            "timestamp": time.time(),
        })
        
        self._save_state()
        return signal
    
    def get_status(self) -> Dict[str, Any]:
        """Get current crash detector status."""
        return {
            "last_trend": self.state.last_trend,
            "consecutive_rapid_descends": self.state.consecutive_rapid_descends,
            "total_evaluations": self.state.total_evaluations,
            "crash_streak_active": self.state.consecutive_rapid_descends >= 2,
            "params": self.evaluator.params.to_dict(),
        }
    
    def get_recent_trends(self, n: int = 10) -> List[Dict[str, Any]]:
        """Get last N trend evaluations."""
        return self.state.trend_history[-n:]
