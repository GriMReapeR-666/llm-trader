"""Policy Library with Regime-Gate — Zero-Shot Regime Adaptation.

Inspired by FinPFN (2025-2026) and ReCAP (2026), this module provides:
1. Persistent policy library storing optimized parameters per regime
2. Regime-gate module for smooth, instant policy switching
3. Performance-based policy updates (policies improve over time)

Key insight from ReCAP:
    "Only update regime-gate and current policy vector for rapid adaptation.
     The policy library stores pre-computed policies that can be instantly
     activated when a regime change is detected."

Key insight from FinPFN:
    "Conditions forecasts on recent feature-return relationships without
     explicit regime labels. Adapts in real-time during large volatility shifts."

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │           POLICY LIBRARY + REGIME-GATE                      │
    │                                                              │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Policy Library (persistent)           │                   │
    │  │   LOW_VOL_TREND: {risk=1.2, ...}     │  ← Stored per    │
    │  │   LOW_VOL_RANGE: {risk=0.8, ...}     │    regime,        │
    │  │   HIGH_VOL_TREND: {risk=0.7, ...}    │    updated by     │
    │  │   HIGH_VOL_CRISIS: {risk=0.2, ...}   │    performance    │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Regime-Gate Module                     │                   │
    │  │   Input: regime probabilities          │                   │
    │  │   gate_weights = softmax(probs * T)    │  ← Smooth        │
    │  │   blended_params = sum(gate_i * policy_i)│   blending     │
    │  └──────────────┬───────────────────────┘                   │
    │                 ↓                                            │
    │  ┌──────────────────────────────────────┐                   │
    │  │ Output: Adapted Parameters            │                   │
    │  │   Instant switch (no 3-5 day lag)      │  ← Zero-shot     │
    │  │   Smooth transition (no jarring jumps) │    adaptation    │
    │  └──────────────────────────────────────┘                   │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from state_persistence import StoreBackedState

logger = logging.getLogger("PolicyLibrary")

# ---------------------------------------------------------------------------
# Policy Vector — compact parameter representation
# ---------------------------------------------------------------------------

@dataclass
class PolicyVector:
    """Compact parameter vector for a single regime.
    
    This is what gets stored in the policy library and blended
    by the regime-gate. It's a flat dict of float parameters
    that can be linearly interpolated.
    """
    risk_multiplier: float = 1.0
    position_scale: float = 1.0
    stop_loss_atr: float = 1.5
    take_profit_atr: float = 3.0
    max_positions: int = 3
    entry_confidence_threshold: float = 0.5
    holding_period_bars: int = 20
    trailing_stop_activate_r: float = 1.5
    partial_close_r: float = 2.0
    
    # Performance metrics (updated over time)
    win_rate: float = 0.5
    sharpe_ratio: float = 0.0
    avg_rr: float = 1.0
    total_trades: int = 0
    last_updated: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PolicyVector":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})
    
    def to_flat_array(self) -> np.ndarray:
        """Convert to flat numpy array for blending."""
        return np.array([
            self.risk_multiplier,
            self.position_scale,
            self.stop_loss_atr,
            self.take_profit_atr,
            float(self.max_positions),
            self.entry_confidence_threshold,
            float(self.holding_period_bars),
            self.trailing_stop_activate_r,
            self.partial_close_r,
        ], dtype=float)
    
    @classmethod
    def from_flat_array(cls, arr: np.ndarray) -> "PolicyVector":
        """Create from flat numpy array."""
        return cls(
            risk_multiplier=float(arr[0]),
            position_scale=float(arr[1]),
            stop_loss_atr=float(arr[2]),
            take_profit_atr=float(arr[3]),
            max_positions=int(round(arr[4])),
            entry_confidence_threshold=float(arr[5]),
            holding_period_bars=int(round(arr[6])),
            trailing_stop_activate_r=float(arr[7]),
            partial_close_r=float(arr[8]),
        )


# ---------------------------------------------------------------------------
# Policy Library — persistent store of regime-specific policies
# ---------------------------------------------------------------------------

class PolicyLibrary(StoreBackedState):
    """Persistent store of optimized policy vectors per regime.
    
    Unlike DEFAULT_PROFILES (which are static), the policy library:
    1. Stores policies that improve over time via performance feedback
    2. Persists across sessions (file-backed)
    3. Supports warm-start (initialize from defaults, then optimize)
    4. Tracks per-regime performance metrics
    """
    
    DEFAULT_FILE = "policy_library.json"
    
    # Default initial policies (same as DEFAULT_PROFILES but as PolicyVector)
    DEFAULT_POLICIES: Dict[str, PolicyVector] = {
        "LOW_VOL_TREND": PolicyVector(
            risk_multiplier=1.2, position_scale=1.0, stop_loss_atr=1.5,
            take_profit_atr=3.5, max_positions=3, entry_confidence_threshold=0.45,
            holding_period_bars=30, trailing_stop_activate_r=1.5, partial_close_r=2.5,
        ),
        "LOW_VOL_RANGE": PolicyVector(
            risk_multiplier=0.8, position_scale=0.7, stop_loss_atr=1.0,
            take_profit_atr=2.0, max_positions=2, entry_confidence_threshold=0.55,
            holding_period_bars=15, trailing_stop_activate_r=1.0, partial_close_r=1.5,
        ),
        "HIGH_VOL_TREND": PolicyVector(
            risk_multiplier=0.7, position_scale=0.8, stop_loss_atr=2.0,
            take_profit_atr=4.0, max_positions=2, entry_confidence_threshold=0.60,
            holding_period_bars=25, trailing_stop_activate_r=2.0, partial_close_r=3.0,
        ),
        "HIGH_VOL_CRISIS": PolicyVector(
            risk_multiplier=0.2, position_scale=0.3, stop_loss_atr=1.0,
            take_profit_atr=1.5, max_positions=1, entry_confidence_threshold=0.80,
            holding_period_bars=10, trailing_stop_activate_r=1.0, partial_close_r=1.0,
        ),
    }
    
    store_key = "policy_library_state"

    def __init__(self, state_file: str = DEFAULT_FILE, store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self.policies: Dict[str, PolicyVector] = {}
        self._load()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> None:
        """Load policies from the store/file, falling back to defaults."""
        data = self._load_state_dict()
        loaded = data.get("policies", {}) if isinstance(data, dict) else {}
        if loaded:
            try:
                for regime, policy_dict in loaded.items():
                    self.policies[regime] = PolicyVector.from_dict(policy_dict)
                logger.info("Loaded %d policies from %s", len(self.policies), self.state_file)
                return
            except Exception as exc:
                logger.debug("Failed to decode policy library: %s", exc)

        # Initialize from defaults
        self.policies = {k: PolicyVector(**v.to_dict()) for k, v in self.DEFAULT_POLICIES.items()}

    def _save(self) -> None:
        """Persist policies to the store + file mirror."""
        self._save_state_dict({
            "policies": {k: v.to_dict() for k, v in self.policies.items()},
            "last_saved": time.time(),
        })
    
    def get_policy(self, regime: str) -> PolicyVector:
        """Get policy for a regime, creating from defaults if missing."""
        if regime not in self.policies:
            self.policies[regime] = PolicyVector(**self.DEFAULT_POLICIES.get(
                regime, self.DEFAULT_POLICIES["LOW_VOL_TREND"]
            ).to_dict())
        return self.policies[regime]
    
    def update_policy(
        self,
        regime: str,
        performance: Dict[str, float],
        learning_rate: float = 0.1,
    ) -> PolicyVector:
        """Update a regime's policy based on observed performance.
        
        Uses a simple online update rule:
            new_param = (1 - lr) * old_param + lr * optimal_param
            
        where optimal_param is estimated from recent performance.
        """
        policy = self.get_policy(regime)
        
        # Estimate optimal parameters from performance
        win_rate = performance.get("win_rate", 0.5)
        sharpe = performance.get("sharpe_ratio", 0.0)
        avg_rr = performance.get("avg_rr", 1.0)
        
        # Only update if we have enough data
        if performance.get("total_trades", 0) < 10:
            return policy
        
        # Adjust parameters based on performance
        # High win rate + positive Sharpe → current params are good, small adjustment
        # Low win rate → need larger adjustment
        adjustment_strength = learning_rate * max(0, 1.0 - win_rate)
        
        # Update risk multiplier: if losing, reduce risk
        if win_rate < 0.45:
            policy.risk_multiplier *= (1.0 - adjustment_strength)
            policy.risk_multiplier = max(0.1, policy.risk_multiplier)
        elif win_rate > 0.55 and sharpe > 0.5:
            policy.risk_multiplier *= (1.0 + adjustment_strength * 0.5)
            policy.risk_multiplier = min(2.0, policy.risk_multiplier)
        
        # Update position scale: if drawdown is high, reduce
        if sharpe < 0:
            policy.position_scale *= (1.0 - adjustment_strength)
            policy.position_scale = max(0.2, policy.position_scale)
        
        # Update entry confidence: if win rate is low, require higher confidence
        if win_rate < 0.45:
            policy.entry_confidence_threshold += adjustment_strength * 0.1
            policy.entry_confidence_threshold = min(0.85, policy.entry_confidence_threshold)
        elif win_rate > 0.55:
            policy.entry_confidence_threshold -= adjustment_strength * 0.05
            policy.entry_confidence_threshold = max(0.3, policy.entry_confidence_threshold)
        
        # Update SL/TP based on realized R:R
        if avg_rr > 0:
            policy.partial_close_r = avg_rr * 0.7  # Partial close at 70% of avg R:R
        
        # Record performance
        policy.win_rate = win_rate
        policy.sharpe_ratio = sharpe
        policy.avg_rr = avg_rr
        policy.total_trades = performance.get("total_trades", 0)
        policy.last_updated = time.time()
        
        self.policies[regime] = policy
        self._save()
        return policy
    
    def get_all_policies(self) -> Dict[str, Dict[str, Any]]:
        """Return all policies as dicts."""
        return {k: v.to_dict() for k, v in self.policies.items()}


# ---------------------------------------------------------------------------
# Regime-Gate — smooth blending between policies
# ---------------------------------------------------------------------------

class RegimeGate:
    """Regime-gate module for smooth, instant policy switching.
    
    From ReCAP:
        "The regime-gate module dynamically combines stored policy vectors
         from a policy library based on current market state."
    
    Instead of hard-switching between policies (which causes jarring
    parameter jumps), the regime-gate smoothly blends policies weighted
    by regime probabilities.
    
    blended_params = sum_i(gate_weight_i * policy_i)
    
    where gate_weight_i = softmax(regime_prob_i / temperature)
    
    Temperature T controls blending sharpness:
    - T → 0: hard switch (winner-take-all)
    - T → ∞: uniform blend (all regimes equally)
    - T = 1.0: default (smooth but decisive)
    """
    
    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature
        self.last_gate_weights: Dict[str, float] = {}
    
    def compute_gate_weights(self, regime_probs: Dict[str, float]) -> Dict[str, float]:
        """Compute gate weights from regime probabilities.
        
        Uses softmax with temperature for smooth blending.
        """
        if not regime_probs:
            return {}
        
        # Apply temperature scaling
        logits = {}
        for regime, prob in regime_probs.items():
            logits[regime] = prob / max(self.temperature, 0.01)
        
        # Softmax
        max_logit = max(logits.values())
        exp_logits = {k: math.exp(v - max_logit) for k, v in logits.items()}
        sum_exp = sum(exp_logits.values())
        
        if sum_exp <= 0:
            # Fallback: uniform weights
            n = len(regime_probs)
            return {k: 1.0 / n for k in regime_probs}
        
        weights = {k: v / sum_exp for k, v in exp_logits.items()}
        self.last_gate_weights = weights
        return weights
    
    def blend_policies(
        self,
        policies: Dict[str, PolicyVector],
        gate_weights: Dict[str, float],
    ) -> PolicyVector:
        """Blend multiple policies using gate weights.
        
        Returns a single PolicyVector that is the weighted average
        of all input policies.
        """
        if not policies or not gate_weights:
            # Return first policy or default
            if policies:
                return list(policies.values())[0]
            return PolicyVector()
        
        # Collect all policies that have weights
        active_policies = []
        active_weights = []
        for regime, weight in gate_weights.items():
            if regime in policies and weight > 0.01:  # Skip negligible weights
                active_policies.append(policies[regime])
                active_weights.append(weight)
        
        if not active_policies:
            return list(policies.values())[0]
        
        # Normalize weights
        total_weight = sum(active_weights)
        active_weights = [w / total_weight for w in active_weights]
        
        # Blend flat arrays
        arrays = [p.to_flat_array() for p in active_policies]
        blended_array = sum(w * arr for w, arr in zip(active_weights, arrays))
        
        # Convert back to PolicyVector
        blended = PolicyVector.from_flat_array(blended_array)
        
        # Use the dominant regime's categorical fields
        dominant_idx = int(np.argmax(active_weights))
        dominant = active_policies[dominant_idx]
        blended.max_positions = dominant.max_positions
        blended.holding_period_bars = dominant.holding_period_bars
        
        return blended
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "temperature": self.temperature,
            "last_gate_weights": {k: round(v, 4) for k, v in self.last_gate_weights.items()},
        }
