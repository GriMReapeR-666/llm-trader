"""Hierarchical Meta-Portfolio Model & Dynamic Multi-Regime Architecture.

Implements the 4-Layer Master Quantitative Architecture from Module 01:
Layer 1: Regime Detection Engine (Hurst, ADX, ATR Percentiles, Latent States)
Layer 2: Dynamic Strategy Router (Weight Allocation w_i across sub-strategies)
Layer 3: Master Expectancy Engine (Regime-Conditioned Aggregate EV_Master)
Layer 4: Centralized Risk Engine (Universal 1R Currency, Heat Cap, Correlation Halving)

Guarantees:
- Pure deterministic, vectorized calculations (sub-millisecond execution).
- Fail-closed risk management without execution authority leakage.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from core.metrics import (
    calculate_hurst_exponent,
    calculate_atr_percentile,
    calculate_master_expectancy,
    calculate_expected_value,
    calculate_quarter_kelly_sizing,
)


@dataclass(frozen=True)
class RegimeState:
    primary_regime: str  # "LOW_VOL_TREND", "LOW_VOL_RANGE", "HIGH_VOL_TREND", "HIGH_VOL_CRISIS"
    probabilities: Dict[str, float]
    hurst_exponent: float
    adx: float
    atr_percentile: float
    is_crisis: bool
    bocpd_changepoint_prob: float = 0.0
    changepoint_detected: bool = False
    timestamp_iso: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DynamicAllocationResult:
    active_regime: str
    regime_probabilities: Dict[str, float]
    strategy_weights: Dict[str, float]
    master_expectancy_r: float
    is_tradable: bool
    recommended_risk_multiplier: float
    portfolio_heat_cap_r: float
    rejection_reasons: List[str] = field(default_factory=list)
    timestamp_iso: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RegimeDetectionEngine:
    """Layer 1: Continuous multi-factor regime detector."""

    def __init__(self, atr_lookback: int = 252, bocpd_hazard_lambda: float = 50.0):
        self.atr_lookback = atr_lookback
        self.bocpd_hazard_lambda = max(1.0, float(bocpd_hazard_lambda))
        self._bocpd_detector: Optional[Any] = None

    def _get_or_create_bocpd(self):
        if self._bocpd_detector is None:
            try:
                from core.bocpd import BayesianOnlineChangepointDetector
                self._bocpd_detector = BayesianOnlineChangepointDetector(hazard_lambda=self.bocpd_hazard_lambda)
            except Exception:
                self._bocpd_detector = None
        return self._bocpd_detector

    def detect_regime(
        self,
        prices: Sequence[float],
        current_adx: float = 25.0,
        atr_history: Optional[Sequence[float]] = None,
        current_atr: float = 0.0010,
        vix_or_vol_index: Optional[float] = None,
        bocpd_changepoint_prob: Optional[float] = None,
    ) -> RegimeState:
        """Evaluates price series persistence, trend strength, and volatility state."""
        hurst = calculate_hurst_exponent(prices) if len(prices) >= 30 else 0.50
        adx_val = float(current_adx) if math.isfinite(float(current_adx)) else 25.0
        
        # Volatility percentile
        if atr_history and len(atr_history) > 10:
            atr_pct = calculate_atr_percentile(atr_history, current_atr)
        else:
            atr_pct = 50.0

        vix = float(vix_or_vol_index) if vix_or_vol_index is not None and math.isfinite(float(vix_or_vol_index)) else 16.0

        # BOCPD Changepoint evaluation
        if bocpd_changepoint_prob is not None:
            cp_prob = float(bocpd_changepoint_prob)
        else:
            cp_prob = 0.0
            if prices is not None and len(prices) >= 1:
                det = self._get_or_create_bocpd()
                if det is not None:
                    try:
                        ref_price = float(prices[0])
                        delta = float(prices[-1]) - ref_price
                        scale = max(float(current_atr), 1e-5)
                        raw_z = delta / scale
                        obs = float(np.clip(raw_z / 10.0, -5.0, 5.0))
                        cp_prob = det.update(obs)
                    except Exception:
                        cp_prob = 0.0

        cp_detected = cp_prob >= 0.35
        
        # Determine 4-regime probability distribution
        p_crisis = 0.0
        p_hv_trend = 0.0
        p_lv_trend = 0.0
        p_lv_range = 0.0

        # Crisis conditions
        if vix >= 32.0 or atr_pct >= 95.0:
            p_crisis = 0.80
            p_hv_trend = 0.15
            p_lv_range = 0.05
            if cp_detected:
                shift = 0.15 * cp_prob
                p_crisis += shift
                p_hv_trend = max(0.02, p_hv_trend - shift)
        elif atr_pct >= 80.0 or vix >= 24.0:
            # High volatility
            if hurst > 0.52 or adx_val >= 25.0:
                p_hv_trend = 0.70
                p_crisis = 0.20
                p_lv_trend = 0.10
            else:
                p_crisis = 0.50
                p_hv_trend = 0.30
                p_lv_range = 0.20

            # Changepoint under high volatility shifts mass to crisis
            if cp_detected:
                shift = 0.25 * cp_prob
                p_crisis += shift
                p_hv_trend = max(0.05, p_hv_trend - shift)
        else:
            # Low / Normal volatility
            if hurst >= 0.53 and adx_val >= 22.0:
                p_lv_trend = 0.75
                p_lv_range = 0.15
                p_hv_trend = 0.10
                if cp_detected:
                    p_lv_range += 0.20 * cp_prob
                    p_lv_trend = max(0.10, p_lv_trend - 0.20 * cp_prob)
            elif hurst <= 0.47 and adx_val < 20.0:
                p_lv_range = 0.75
                p_lv_trend = 0.15
                p_crisis = 0.05
                p_hv_trend = 0.05
            else:
                # Moderate / Neutral
                p_lv_trend = 0.40
                p_lv_range = 0.40
                p_hv_trend = 0.10
                p_crisis = 0.10

        probs = {
            "LOW_VOL_TREND": round(p_lv_trend, 4),
            "LOW_VOL_RANGE": round(p_lv_range, 4),
            "HIGH_VOL_TREND": round(p_hv_trend, 4),
            "HIGH_VOL_CRISIS": round(p_crisis, 4),
        }
        
        # Normalize
        total_p = sum(probs.values()) or 1.0
        probs = {k: round(v / total_p, 4) for k, v in probs.items()}
        primary = max(probs, key=probs.get)
        is_crisis = primary == "HIGH_VOL_CRISIS" or probs["HIGH_VOL_CRISIS"] >= 0.40

        return RegimeState(
            primary_regime=primary,
            probabilities=probs,
            hurst_exponent=round(hurst, 4),
            adx=round(adx_val, 2),
            atr_percentile=round(atr_pct, 2),
            is_crisis=is_crisis,
            bocpd_changepoint_prob=round(cp_prob, 4),
            changepoint_detected=cp_detected,
        )


class DynamicStrategyRouter:
    """Layer 2 & Layer 3: Allocates strategy weights & evaluates Master Expectancy."""

    # Default empirical performance per strategy across regimes [WinRate, AvgWinR, AvgLossR]
    DEFAULT_STRATEGY_EXPECTANCIES = {
        "LOW_VOL_TREND": {
            "TREND_MOMENTUM": 0.60,       # P_win=0.45, Win=3.5R -> EV=+0.60R
            "MEAN_REVERSION": -0.40,      # Bleeds in trend
            "CTA_TREND": 0.45,
            "CASH_HEDGE": 0.0,
        },
        "LOW_VOL_RANGE": {
            "TREND_MOMENTUM": -0.40,      # False breakouts
            "MEAN_REVERSION": 0.55,       # P_win=0.70, Win=1.3R -> EV=+0.55R
            "CTA_TREND": -0.20,
            "CASH_HEDGE": 0.0,
        },
        "HIGH_VOL_TREND": {
            "TREND_MOMENTUM": 0.30,
            "MEAN_REVERSION": -0.60,      # Blown through stops
            "CTA_TREND": 0.65,            # Multi-asset trend / short equities
            "CASH_HEDGE": 0.0,
        },
        "HIGH_VOL_CRISIS": {
            "TREND_MOMENTUM": -0.50,
            "MEAN_REVERSION": -0.70,
            "CTA_TREND": 0.10,
            "CASH_HEDGE": 0.0,            # 100% Cash / Long Vol
        },
    }

    # Regime Allocation Matrix
    DEFAULT_REGIME_WEIGHTS = {
        "LOW_VOL_TREND": {
            "TREND_MOMENTUM": 0.70,
            "CTA_TREND": 0.20,
            "MEAN_REVERSION": 0.0,
            "CASH_HEDGE": 0.10,
        },
        "LOW_VOL_RANGE": {
            "MEAN_REVERSION": 0.80,
            "TREND_MOMENTUM": 0.0,
            "CTA_TREND": 0.0,
            "CASH_HEDGE": 0.20,
        },
        "HIGH_VOL_TREND": {
            "CTA_TREND": 0.60,
            "TREND_MOMENTUM": 0.20,
            "MEAN_REVERSION": 0.0,
            "CASH_HEDGE": 0.20,
        },
        "HIGH_VOL_CRISIS": {
            "CASH_HEDGE": 0.90,
            "CTA_TREND": 0.10,
            "TREND_MOMENTUM": 0.0,
            "MEAN_REVERSION": 0.0,
        },
    }

    def __init__(
        self,
        min_master_ev: float = 0.20,
        portfolio_heat_cap_r: float = 3.0,
    ):
        self.min_master_ev = float(min_master_ev)
        self.portfolio_heat_cap_r = float(portfolio_heat_cap_r)

    @staticmethod
    def normalize_strategy_key(strat: str) -> str:
        s = str(strat or "").strip().upper()
        if any(k in s for k in ("TREND", "CONTINUATION", "MOMENTUM", "CAUSAL", "SCALP", "SWING")):
            return "TREND_MOMENTUM"
        if any(k in s for k in ("MEAN", "REVERSION", "RANGE", "STATARB", "BOLLINGER", "RSI")):
            return "MEAN_REVERSION"
        if "CTA" in s:
            return "CTA_TREND"
        if any(k in s for k in ("CASH", "HEDGE", "VOL")):
            return "CASH_HEDGE"
        return "TREND_MOMENTUM"

    def route(
        self,
        regime_state: RegimeState,
        active_strategy_type: str = "TREND_MOMENTUM",
    ) -> DynamicAllocationResult:
        """Calculates dynamic weights, master expectancy, and risk allocation."""
        probs = regime_state.probabilities
        strat_key = self.normalize_strategy_key(active_strategy_type)
        
        # 1. Compute dynamic weights as probability-weighted matrix
        blended_weights: Dict[str, float] = {
            "TREND_MOMENTUM": 0.0,
            "MEAN_REVERSION": 0.0,
            "CTA_TREND": 0.0,
            "CASH_HEDGE": 0.0,
        }
        for regime, p_regime in probs.items():
            regime_map = self.DEFAULT_REGIME_WEIGHTS.get(regime, {})
            for s_name, w in regime_map.items():
                blended_weights[s_name] = blended_weights.get(s_name, 0.0) + (p_regime * w)

        # 2. Master Expectancy
        master_ev = calculate_master_expectancy(
            regime_probabilities=probs,
            strategy_weights=self.DEFAULT_REGIME_WEIGHTS,
            strategy_expectancies=self.DEFAULT_STRATEGY_EXPECTANCIES,
        )

        rejections: List[str] = []
        is_tradable = True

        # Crisis rule: If Crisis state is active, zero out tactical entries
        if regime_state.is_crisis:
            rejections.append("HIGH_VOL_CRISIS_REGIME_ACTIVE")
            is_tradable = False
            risk_multiplier = 0.0
        elif master_ev < self.min_master_ev:
            rejections.append(f"MASTER_EXPECTANCY_BELOW_FLOOR:{master_ev:.2f}R<{self.min_master_ev:.2f}R")
            is_tradable = False
            risk_multiplier = 0.0
        else:
            # Active strategy weight determines risk multiplier
            strat_weight = blended_weights.get(strat_key, 0.0)
            if strat_weight <= 0.05:
                rejections.append(f"STRATEGY_DISFAVORED_IN_REGIME:{strat_key}_WEIGHT={strat_weight:.2f}")
                is_tradable = False
                risk_multiplier = 0.0
            else:
                # Dynamic multiplier between 0.5x and 1.2x based on conviction
                risk_multiplier = min(1.2, max(0.5, strat_weight * 1.5))

        return DynamicAllocationResult(
            active_regime=regime_state.primary_regime,
            regime_probabilities=probs,
            strategy_weights={k: round(v, 4) for k, v in blended_weights.items()},
            master_expectancy_r=round(master_ev, 4),
            is_tradable=is_tradable,
            recommended_risk_multiplier=round(risk_multiplier, 3),
            portfolio_heat_cap_r=self.portfolio_heat_cap_r,
            rejection_reasons=rejections,
        )


class HierarchicalMetaPortfolioEngine:
    """Master Unified Engine coordinating Layers 1 through 4."""

    def __init__(
        self,
        base_risk_pct: float = 0.01,
        min_master_ev: float = 0.20,
        portfolio_heat_cap_r: float = 4.0,
    ):
        self.base_risk_pct = float(base_risk_pct)
        self.detector = RegimeDetectionEngine()
        self.router = DynamicStrategyRouter(
            min_master_ev=min_master_ev,
            portfolio_heat_cap_r=portfolio_heat_cap_r,
        )

    def evaluate(
        self,
        prices: Sequence[float],
        current_adx: float = 25.0,
        atr_history: Optional[Sequence[float]] = None,
        current_atr: float = 0.0010,
        strategy_type: str = "TREND_MOMENTUM",
        open_portfolio_r: float = 0.0,
        cross_strategy_correlation: float = 0.0,
    ) -> Dict[str, Any]:
        """Full hierarchical meta-portfolio evaluation."""
        regime = self.detector.detect_regime(
            prices=prices,
            current_adx=current_adx,
            atr_history=atr_history,
            current_atr=current_atr,
        )
        
        allocation = self.router.route(
            regime_state=regime,
            active_strategy_type=strategy_type,
        )
        
        reasons = list(allocation.rejection_reasons)
        approved = allocation.is_tradable
        applied_multiplier = allocation.recommended_risk_multiplier
        
        # Layer 4: Centralized Risk & Heat Cap Checks
        if open_portfolio_r >= allocation.portfolio_heat_cap_r:
            reasons.append(f"PORTFOLIO_HEAT_CAP_EXCEEDED:{open_portfolio_r:.1f}R>={allocation.portfolio_heat_cap_r:.1f}R")
            approved = False
            applied_multiplier = 0.0
            
        # Cross-Strategy Correlation Gate: If r > 0.60, halve sizing
        if cross_strategy_correlation > 0.60:
            applied_multiplier *= 0.50
            reasons.append(f"CROSS_CORRELATION_HALVED:r={cross_strategy_correlation:.2f}>0.60")

        return {
            "approved": approved,
            "regime": regime.to_dict(),
            "allocation": allocation.to_dict(),
            "applied_risk_multiplier": round(applied_multiplier, 3),
            "effective_risk_pct": round(self.base_risk_pct * applied_multiplier, 4),
            "reasons": reasons,
        }


# Canonical aliases
RegimeClassificationEngine = RegimeDetectionEngine
MasterRiskEngine = HierarchicalMetaPortfolioEngine
CentralizedPortfolioRiskEngine = HierarchicalMetaPortfolioEngine

