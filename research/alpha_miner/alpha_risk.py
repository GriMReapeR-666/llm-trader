"""Alpha Risk Overlay for Risk Management.

Implements Phase 6 of the alpha mining integration plan.
Provides risk assessment and position sizing adjustments for alpha-mined strategies.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms."
    Wangpratham, N. (2026). "BrainAlpha: An Autonomous Multi-Agent System."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AlphaRiskConfig:
    """Configuration for alpha risk management."""
    min_ic_threshold: float = 0.02
    good_ic_threshold: float = 0.05
    strong_ic_threshold: float = 0.10
    max_alpha_concentration: float = 0.25
    min_alpha_diversity: float = 0.7
    alpha_decay_threshold: float = 0.5
    max_lookback: int = 60
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_ic_threshold": self.min_ic_threshold,
            "good_ic_threshold": self.good_ic_threshold,
            "strong_ic_threshold": self.strong_ic_threshold,
            "max_alpha_concentration": self.max_alpha_concentration,
            "min_alpha_diversity": self.min_alpha_diversity,
            "alpha_decay_threshold": self.alpha_decay_threshold,
            "max_lookback": self.max_lookback,
        }


# ---------------------------------------------------------------------------
# Alpha Risk Assessment
# ---------------------------------------------------------------------------

@dataclass
class AlphaRiskProfile:
    """Risk profile for an alpha strategy."""
    alpha_id: str
    ic_mean: float
    ic_std: float
    ic_ir: float
    ic_quality: str
    turnover: float
    max_drawdown: float
    concentration: float
    decay_risk: float
    risk_score: float
    sizing_multiplier: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "ic_mean": round(self.ic_mean, 6),
            "ic_std": round(self.ic_std, 6),
            "ic_ir": round(self.ic_ir, 4),
            "ic_quality": self.ic_quality,
            "turnover": round(self.turnover, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "concentration": round(self.concentration, 4),
            "decay_risk": round(self.decay_risk, 4),
            "risk_score": round(self.risk_score, 4),
            "sizing_multiplier": round(self.sizing_multiplier, 4),
        }


class AlphaRiskManager:
    """Risk management for alpha-mined strategies."""
    
    def __init__(self, config: Optional[AlphaRiskConfig] = None):
        self.config = config or AlphaRiskConfig()
        self.alpha_history: Dict[str, List[Dict[str, Any]]] = {}
    
    def assess_alpha_risk(self, alpha_id: str,
                         ic_metrics: Dict[str, float],
                         backtest_results: Optional[Dict[str, float]] = None) -> AlphaRiskProfile:
        """Assess risk characteristics of a mined alpha.
        
        Args:
            alpha_id: Unique alpha identifier
            ic_metrics: Dictionary with ic_mean, ic_std, ic_ir, ic_quality
            backtest_results: Optional backtest metrics
            
        Returns:
            AlphaRiskProfile with risk assessment
        """
        ic_mean = ic_metrics.get("ic_mean", 0.0)
        ic_std = ic_metrics.get("ic_std", 1.0)
        ic_ir = ic_metrics.get("ic_ir", 0.0)
        ic_quality = ic_metrics.get("ic_quality", "weak")
        
        backtest = backtest_results or {}
        turnover = backtest.get("turnover", 0.3)
        max_dd = backtest.get("max_drawdown", 0.1)
        concentration = backtest.get("concentration", 0.15)
        
        # Calculate decay risk
        decay_risk = self._calculate_decay_risk(alpha_id)
        
        # Calculate composite risk score
        risk_score = self._calculate_risk_score(
            ic_mean, ic_std, turnover, max_dd, concentration, decay_risk
        )
        
        # Calculate sizing multiplier
        sizing_multiplier = self._calculate_sizing_multiplier(
            ic_mean, ic_ir, risk_score
        )
        
        return AlphaRiskProfile(
            alpha_id=alpha_id,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_ir=ic_ir,
            ic_quality=ic_quality,
            turnover=turnover,
            max_drawdown=max_dd,
            concentration=concentration,
            decay_risk=decay_risk,
            risk_score=risk_score,
            sizing_multiplier=sizing_multiplier,
        )
    
    def _calculate_decay_risk(self, alpha_id: str) -> float:
        """Calculate alpha decay risk from historical IC values."""
        if alpha_id not in self.alpha_history:
            return 0.0
        
        history = self.alpha_history[alpha_id]
        if len(history) < 3:
            return 0.0
        
        # Extract IC values
        ic_values = [h.get("ic_mean", 0.0) for h in history[-10:]]
        
        if len(ic_values) < 2:
            return 0.0
        
        # Calculate trend
        x = np.arange(len(ic_values))
        slope = np.polyfit(x, ic_values, 1)[0]
        
        # Decay risk is negative slope (declining IC)
        decay_risk = max(0.0, -slope / max(abs(ic_values[-1]), 0.001))
        
        return min(1.0, decay_risk)
    
    def _calculate_risk_score(self, ic_mean: float, ic_std: float,
                             turnover: float, max_dd: float,
                             concentration: float, decay_risk: float) -> float:
        """Calculate composite risk score (0-1, higher = riskier)."""
        # IC quality component (lower IC = higher risk)
        ic_score = max(0.0, 1.0 - abs(ic_mean) / 0.10)
        
        # Volatility component (higher IC std = higher risk)
        vol_score = min(1.0, ic_std / 0.15)
        
        # Turnover component (higher turnover = higher risk)
        turnover_score = min(1.0, turnover / 0.5)
        
        # Drawdown component
        dd_score = min(1.0, max_dd / 0.2)
        
        # Concentration component
        conc_score = min(1.0, concentration / 0.3)
        
        # Weighted combination
        risk_score = (
            0.25 * ic_score +
            0.20 * vol_score +
            0.15 * turnover_score +
            0.25 * dd_score +
            0.10 * conc_score +
            0.05 * decay_risk
        )
        
        return min(1.0, risk_score)
    
    def _calculate_sizing_multiplier(self, ic_mean: float, ic_ir: float,
                                    risk_score: float) -> float:
        """Calculate position sizing multiplier based on alpha quality."""
        # Base multiplier from IC quality
        if abs(ic_mean) >= self.config.strong_ic_threshold:
            base = 1.0
        elif abs(ic_mean) >= self.config.good_ic_threshold:
            base = 0.75
        elif abs(ic_mean) >= self.config.min_ic_threshold:
            base = 0.5
        else:
            base = 0.25
        
        # Adjust for IC stability (IC IR)
        ir_factor = min(1.0, max(0.5, ic_ir / 2.0))
        
        # Adjust for risk (higher risk = lower multiplier)
        risk_factor = max(0.3, 1.0 - risk_score * 0.5)
        
        return base * ir_factor * risk_factor
    
    def update_alpha_history(self, alpha_id: str, metrics: Dict[str, float]):
        """Update alpha history for decay detection."""
        if alpha_id not in self.alpha_history:
            self.alpha_history[alpha_id] = []
        
        self.alpha_history[alpha_id].append({
            "ic_mean": metrics.get("ic_mean", 0.0),
            "timestamp": metrics.get("timestamp", 0.0),
        })
        
        # Keep only last 20 observations
        if len(self.alpha_history[alpha_id]) > 20:
            self.alpha_history[alpha_id] = self.alpha_history[alpha_id][-20:]
    
    def check_alpha_decay(self, alpha_id: str,
                         current_ic: float,
                         lookback: int = 10) -> Tuple[bool, float]:
        """Check if alpha IC is decaying.
        
        Args:
            alpha_id: Alpha identifier
            current_ic: Current IC value
            lookback: Number of historical observations to check
            
        Returns:
            Tuple of (is_decaying, decay_rate)
        """
        if alpha_id not in self.alpha_history:
            return False, 0.0
        
        history = self.alpha_history[alpha_id][-lookback:]
        if len(history) < 3:
            return False, 0.0
        
        ic_values = [h.get("ic_mean", 0.0) for h in history]
        ic_values.append(current_ic)
        
        # Fit linear trend
        x = np.arange(len(ic_values))
        slope = np.polyfit(x, ic_values, 1)[0]
        
        # Check if decaying (negative slope exceeding threshold)
        is_decaying = slope < -self.config.alpha_decay_threshold
        
        return is_decaying, slope
    
    def get_sizing_adjustment(self, alpha_quality: Dict[str, float]) -> float:
        """Return position sizing multiplier based on alpha quality.
        
        This is the main interface for RiskManager integration.
        """
        profile = self.assess_alpha_risk(
            alpha_id=alpha_quality.get("alpha_id", "unknown"),
            ic_metrics=alpha_quality,
            backtest_results=alpha_quality,
        )
        
        return profile.sizing_multiplier
    
    def assess_portfolio_concentration(self, alpha_weights: Dict[str, float]) -> Dict[str, Any]:
        """Assess portfolio concentration risk."""
        if not alpha_weights:
            return {"concentration": 0.0, "is_concentrated": False}
        
        weights = np.array(list(alpha_weights.values()))
        weights = np.abs(weights) / np.sum(np.abs(weights))
        
        # Herfindahl-Hirschman Index
        hhi = np.sum(weights ** 2)
        
        # Effective number of alphas
        effective_n = 1.0 / hhi if hhi > 0 else 0.0
        
        return {
            "concentration": float(hhi),
            "effective_n_alphas": float(effective_n),
            "is_concentrated": hhi > self.config.max_alpha_concentration,
            "n_alphas": len(alpha_weights),
        }
