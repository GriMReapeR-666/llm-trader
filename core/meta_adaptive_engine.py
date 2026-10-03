"""Meta-Adaptive Regime Engine — MAML-Inspired Few-Shot Adaptation.

Implements rapid adaptation to market regime changes using:
  1. Regime-specific strategy profiles (pre-computed parameter sets)
  2. Transition detection with confidence scoring
  3. Few-shot parameter blending (MAML-inspired: adapt with minimal data)
  4. Policy library with regime-gate switching (ReCAP approach)

The engine maintains a library of regime-specific configurations and
rapidly switches/blends them when regime transitions are detected.

References:
  - Meta-LMPS-Online (2025): MAML for rapid regime adaptation
  - ReCAP (2026): Continual learning with policy library + regime-gate
  - Logic-Q (2025): Program sketch for real-time trend identification
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from state_persistence import StoreBackedState

logger = logging.getLogger("MetaAdaptive")

# ---------------------------------------------------------------------------
# Regime-Specific Strategy Profiles
# ---------------------------------------------------------------------------

@dataclass
class StrategyProfile:
    """A regime-specific strategy configuration."""
    regime: str
    risk_multiplier: float = 1.0
    position_scale: float = 1.0
    stop_loss_atr: float = 1.5
    take_profit_atr: float = 3.0
    max_positions: int = 3
    preferred_alphas: List[str] = field(default_factory=list)
    disfavored_alphas: List[str] = field(default_factory=list)
    entry_confidence_threshold: float = 0.5
    holding_period_bars: int = 20
    trailing_stop_activate_r: float = 1.5
    partial_close_r: float = 2.0
    regime_confidence: float = 0.0
    performance_score: float = 0.0  # Rolling win rate / Sharpe in this regime
    adaptation_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Default profiles for each regime
DEFAULT_PROFILES: Dict[str, StrategyProfile] = {
    "LOW_VOL_TREND": StrategyProfile(
        regime="LOW_VOL_TREND",
        risk_multiplier=1.2,
        position_scale=1.0,
        stop_loss_atr=1.5,
        take_profit_atr=3.5,
        max_positions=3,
        preferred_alphas=["alpha_036", "alpha_037", "alpha_038", "alpha_041", "alpha_060"],
        disfavored_alphas=["alpha_019", "alpha_034"],
        entry_confidence_threshold=0.45,
        holding_period_bars=30,
        trailing_stop_activate_r=1.5,
        partial_close_r=2.5,
    ),
    "LOW_VOL_RANGE": StrategyProfile(
        regime="LOW_VOL_RANGE",
        risk_multiplier=0.8,
        position_scale=0.7,
        stop_loss_atr=1.0,
        take_profit_atr=2.0,
        max_positions=2,
        preferred_alphas=["alpha_019", "alpha_034", "alpha_001", "alpha_014", "alpha_023"],
        disfavored_alphas=["alpha_036", "alpha_037"],
        entry_confidence_threshold=0.55,
        holding_period_bars=15,
        trailing_stop_activate_r=1.0,
        partial_close_r=1.5,
    ),
    "HIGH_VOL_TREND": StrategyProfile(
        regime="HIGH_VOL_TREND",
        risk_multiplier=0.7,
        position_scale=0.8,
        stop_loss_atr=2.0,
        take_profit_atr=4.0,
        max_positions=2,
        preferred_alphas=["alpha_005", "alpha_009", "alpha_022", "alpha_030"],
        disfavored_alphas=["alpha_019", "alpha_034"],
        entry_confidence_threshold=0.60,
        holding_period_bars=25,
        trailing_stop_activate_r=2.0,
        partial_close_r=3.0,
    ),
    "HIGH_VOL_CRISIS": StrategyProfile(
        regime="HIGH_VOL_CRISIS",
        risk_multiplier=0.2,
        position_scale=0.3,
        stop_loss_atr=1.0,
        take_profit_atr=1.5,
        max_positions=1,
        preferred_alphas=[],
        disfavored_alphas=["alpha_036", "alpha_037", "alpha_038", "alpha_041"],
        entry_confidence_threshold=0.80,
        holding_period_bars=10,
        trailing_stop_activate_r=1.0,
        partial_close_r=1.0,
    ),
}


# ---------------------------------------------------------------------------
# Regime Transition Detector
# ---------------------------------------------------------------------------

@dataclass
class RegimeTransition:
    """Record of a regime transition event."""
    from_regime: str
    to_regime: str
    confidence: float
    transition_bars: int  # How many bars the transition took
    timestamp: float
    performance_impact: float = 0.0  # Change in rolling Sharpe after transition

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RegimeTransitionDetector:
    """Detects regime transitions with confidence scoring.

    Uses a combination of:
    - Regime probability threshold (primary regime changes)
    - Confidence decay (old regime confidence must drop below threshold)
    - Stability check (new regime must persist for N bars)
    """

    def __init__(
        self,
        confidence_threshold: float = 0.40,
        min_stability_bars: int = 3,
        cooldown_bars: int = 5,
    ):
        self.confidence_threshold = confidence_threshold
        self.min_stability_bars = min_stability_bars
        self.cooldown_bars = cooldown_bars
        self.current_regime: Optional[str] = None
        self.current_regime_bars: int = 0
        self.bars_since_transition: int = 999
        self.transition_history: List[RegimeTransition] = []
        self.pending_candidate: Optional[str] = None
        self.pending_count: int = 0

    def update(
        self,
        regime_probs: Dict[str, float],
        bar_num: int,
    ) -> Optional[RegimeTransition]:
        """Check for regime transition.

        Args:
            regime_probs: Probability distribution over regimes.
            bar_num: Current bar number.

        Returns:
            RegimeTransition if a transition occurred, None otherwise.
        """
        if not regime_probs:
            return None

        # Find primary regime
        primary = max(regime_probs, key=regime_probs.get)
        primary_conf = regime_probs[primary]

        self.bars_since_transition += 1

        # First call — initialize
        if self.current_regime is None:
            self.current_regime = primary
            self.current_regime_bars = 1
            return None

        # Same regime — increment counter
        if primary == self.current_regime:
            self.current_regime_bars += 1
            self.pending_candidate = None
            self.pending_count = 0
            return None

        # Different regime detected — check if it's a real transition
        if primary != self.pending_candidate:
            # New candidate
            self.pending_candidate = primary
            self.pending_count = 1
        else:
            self.pending_count += 1

        # Require stability: candidate must persist for N bars
        if self.pending_count < self.min_stability_bars:
            return None

        # Require cooldown: don't transition too fast
        if self.bars_since_transition < self.cooldown_bars:
            return None

        # Require minimum confidence
        if primary_conf < self.confidence_threshold:
            return None

        # Transition confirmed
        transition = RegimeTransition(
            from_regime=self.current_regime,
            to_regime=primary,
            confidence=primary_conf,
            transition_bars=self.pending_count,
            timestamp=time.time(),
        )
        self.transition_history.append(transition)
        if len(self.transition_history) > 50:
            self.transition_history = self.transition_history[-50:]

        self.current_regime = primary
        self.current_regime_bars = 1
        self.bars_since_transition = 0
        self.pending_candidate = None
        self.pending_count = 0

        logger.info(
            "Regime transition: %s -> %s (confidence=%.2f, bars=%d)",
            transition.from_regime, transition.to_regime,
            transition.confidence, transition.transition_bars,
        )
        return transition

    def get_transition_summary(self) -> Dict[str, Any]:
        """Return summary of recent transitions."""
        recent = self.transition_history[-5:]
        return {
            "current_regime": self.current_regime,
            "bars_in_regime": self.current_regime_bars,
            "total_transitions": len(self.transition_history),
            "recent_transitions": [t.to_dict() for t in recent],
        }


# ---------------------------------------------------------------------------
# Few-Shot Adapter (MAML-Inspired)
# ---------------------------------------------------------------------------

class FewShotAdapter:
    """Adapts strategy parameters based on recent regime performance.

    MAML-inspired: instead of learning a meta-model, we maintain
    regime-specific parameter sets and blend them based on recent
    performance signals.

    Adaptation formula:
        adapted_param = (1 - alpha) * base_param + alpha * regime_param
        where alpha = f(recent_performance, regime_stability)
    """

    def __init__(self, learning_rate: float = 0.3, min_samples: int = 5):
        self.learning_rate = learning_rate
        self.min_samples = min_samples
        self.regime_performance: Dict[str, List[float]] = {
            r: [] for r in DEFAULT_PROFILES
        }

    def record_performance(self, regime: str, sharpe: float) -> None:
        """Record performance in a regime for future adaptation."""
        if regime not in self.regime_performance:
            self.regime_performance[regime] = []
        self.regime_performance[regime].append(sharpe)
        # Keep last 100 observations
        if len(self.regime_performance[regime]) > 100:
            self.regime_performance[regime] = self.regime_performance[regime][-100:]

    def compute_adaptation_alpha(self, regime: str) -> float:
        """Compute adaptation rate based on regime performance.

        High recent performance → trust regime profile more (alpha → 1)
        Low recent performance → trust base profile more (alpha → 0)
        """
        perf = self.regime_performance.get(regime, [])
        if not perf or len(perf) < self.min_samples:
            return 0.5  # Neutral when insufficient data

        recent = perf[-20:]  # Last 20 observations
        if not recent:
            return 0.5
        avg_sharpe = float(np.nan_to_num(np.mean(recent), nan=0.0))
        # Map Sharpe to adaptation rate: Sharpe=0 → alpha=0.3, Sharpe=1 → alpha=0.8
        alpha = float(np.clip(0.3 + avg_sharpe * 0.5, 0.1, 0.9))
        return alpha

    def adapt_profile(
        self,
        base_profile: StrategyProfile,
        regime: str,
    ) -> StrategyProfile:
        """Adapt a strategy profile based on regime performance history.

        Blends the base profile with the regime-specific profile
        using the computed adaptation rate.
        """
        regime_profile = DEFAULT_PROFILES.get(regime, base_profile)
        alpha = self.compute_adaptation_alpha(regime)
        # Guard against NaN
        if not np.isfinite(alpha):
            alpha = 0.5

        adapted = StrategyProfile(
            regime=regime,
            risk_multiplier=base_profile.risk_multiplier * (1 - alpha) + regime_profile.risk_multiplier * alpha,
            position_scale=base_profile.position_scale * (1 - alpha) + regime_profile.position_scale * alpha,
            stop_loss_atr=base_profile.stop_loss_atr * (1 - alpha) + regime_profile.stop_loss_atr * alpha,
            take_profit_atr=base_profile.take_profit_atr * (1 - alpha) + regime_profile.take_profit_atr * alpha,
            max_positions=int(round(base_profile.max_positions * (1 - alpha) + regime_profile.max_positions * alpha)),
            preferred_alphas=regime_profile.preferred_alphas,
            disfavored_alphas=regime_profile.disfavored_alphas,
            entry_confidence_threshold=base_profile.entry_confidence_threshold * (1 - alpha) + regime_profile.entry_confidence_threshold * alpha,
            holding_period_bars=int(round(base_profile.holding_period_bars * (1 - alpha) + regime_profile.holding_period_bars * alpha)),
            trailing_stop_activate_r=base_profile.trailing_stop_activate_r * (1 - alpha) + regime_profile.trailing_stop_activate_r * alpha,
            partial_close_r=base_profile.partial_close_r * (1 - alpha) + regime_profile.partial_close_r * alpha,
            adaptation_count=base_profile.adaptation_count + 1,
        )
        adapted.adaptation_count = base_profile.adaptation_count + 1
        return adapted


# ---------------------------------------------------------------------------
# Meta-Adaptive Engine
# ---------------------------------------------------------------------------

@dataclass
class MetaAdaptiveState:
    """Persistent state for the meta-adaptive engine."""
    current_profile: Dict[str, Any] = field(default_factory=lambda: DEFAULT_PROFILES["LOW_VOL_TREND"].to_dict())
    current_regime: str = "LOW_VOL_TREND"
    bars_in_regime: int = 0
    total_transitions: int = 0
    adaptation_count: int = 0
    regime_performance: Dict[str, List[float]] = field(default_factory=dict)
    transition_history: List[Dict[str, Any]] = field(default_factory=list)
    detector_state: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_regime": self.current_regime,
            "current_profile": self.current_profile,
            "bars_in_regime": self.bars_in_regime,
            "total_transitions": self.total_transitions,
            "adaptation_count": self.adaptation_count,
            "regime_performance_summary": {
                r: {
                    "count": len(perfs),
                    "avg_sharpe": round(float(np.mean(perfs)), 3) if perfs else 0.0,
                    "recent_sharpe": round(float(np.mean(perfs[-5:])), 3) if len(perfs) >= 5 else 0.0,
                }
                for r, perfs in self.regime_performance.items()
            },
            "recent_transitions": self.transition_history[-5:],
            "detector_state": self.detector_state or {},
        }


class MetaAdaptiveEngine(StoreBackedState):
    """Meta-adaptive regime engine combining:
    - Regime transition detection (ReCAP-style)
    - Few-shot parameter adaptation (MAML-inspired)
    - Strategy profile switching (policy library)
    """

    store_key = "meta_adaptive_state"

    def __init__(self, state_file: str = "meta_adaptive_state.json", store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)
        self.detector = RegimeTransitionDetector()
        self.adapter = FewShotAdapter()
        self.profiles = dict(DEFAULT_PROFILES)

        # Restore performance history
        for regime, perfs in self.state.regime_performance.items():
            self.adapter.regime_performance[regime] = list(perfs)

        # Restore detector state (stability bars, cooldown, pending candidate)
        det_state = self.state.detector_state or {}
        if det_state:
            self.detector.current_regime = det_state.get("current_regime")
            self.detector.current_regime_bars = det_state.get("current_regime_bars", 0)
            self.detector.bars_since_transition = det_state.get("bars_since_transition", 999)
            self.detector.pending_candidate = det_state.get("pending_candidate")
            self.detector.pending_count = det_state.get("pending_count", 0)

    def _load_state(self) -> MetaAdaptiveState:
        try:
            data = self._load_state_dict()
            if data:
                state = MetaAdaptiveState(
                    current_regime=data.get("current_regime", "LOW_VOL_TREND"),
                    bars_in_regime=data.get("bars_in_regime", 0),
                    total_transitions=data.get("total_transitions", 0),
                    adaptation_count=data.get("adaptation_count", 0),
                )
                if "current_profile" in data:
                    state.current_profile = data["current_profile"]
                state.regime_performance = data.get("regime_performance", {})
                state.transition_history = data.get("transition_history", [])
                state.detector_state = data.get("detector_state", None)
                return state
        except Exception as exc:
            logger.debug("regime detection failed: %s", exc)
        return MetaAdaptiveState()

    def _save_state(self) -> None:
        try:
            # Persist detector state for cross-cycle stability
            self.state.detector_state = {
                "current_regime": self.detector.current_regime,
                "current_regime_bars": self.detector.current_regime_bars,
                "bars_since_transition": self.detector.bars_since_transition,
                "pending_candidate": self.detector.pending_candidate,
                "pending_count": self.detector.pending_count,
            }
            self._save_state_dict(self.state.to_dict())
        except Exception as e:
            logger.debug("Failed to save meta-adaptive state: %s", e)

    def update(
        self,
        regime_probs: Dict[str, float],
        bar_num: int,
        rolling_sharpe: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update the meta-adaptive engine with new regime data.

        Args:
            regime_probs: Probability distribution over regimes.
            bar_num: Current bar number.
            rolling_sharpe: Recent rolling Sharpe ratio for performance tracking.

        Returns:
            Dict with adaptation results and recommended profile.
        """
        result = {
            "transition_detected": False,
            "adaptation_applied": False,
            "profile": self.state.current_profile,
        }

        # Record performance in current regime
        if rolling_sharpe is not None:
            self.adapter.record_performance(self.state.current_regime, rolling_sharpe)
            self.state.regime_performance[self.state.current_regime] = (
                self.adapter.regime_performance.get(self.state.current_regime, [])
            )

        # Check for regime transition
        transition = self.detector.update(regime_probs, bar_num)

        if transition is not None:
            result["transition_detected"] = True
            result["from_regime"] = transition.from_regime
            result["to_regime"] = transition.to_regime
            result["confidence"] = transition.confidence

            # Adapt profile to new regime
            base_profile = self.profiles.get(transition.to_regime, DEFAULT_PROFILES["LOW_VOL_TREND"])
            adapted = self.adapter.adapt_profile(base_profile, transition.to_regime)

            self.state.current_profile = adapted.to_dict()
            self.state.current_regime = transition.to_regime
            self.state.bars_in_regime = 0
            self.state.total_transitions += 1
            self.state.adaptation_count += 1
            result["adaptation_applied"] = True
            result["profile"] = self.state.current_profile

            # Record transition
            self.state.transition_history.append(transition.to_dict())
            if len(self.state.transition_history) > 50:
                self.state.transition_history = self.state.transition_history[-50:]
        else:
            self.state.bars_in_regime = self.detector.current_regime_bars
            self.state.current_regime = self.detector.current_regime or "LOW_VOL_TREND"

        self._save_state()
        return result

    def get_active_profile(self) -> StrategyProfile:
        """Get the current active strategy profile."""
        p = self.state.current_profile
        return StrategyProfile(
            regime=p.get("regime", "LOW_VOL_TREND"),
            risk_multiplier=p.get("risk_multiplier", 1.0),
            position_scale=p.get("position_scale", 1.0),
            stop_loss_atr=p.get("stop_loss_atr", 1.5),
            take_profit_atr=p.get("take_profit_atr", 3.0),
            max_positions=p.get("max_positions", 3),
            preferred_alphas=p.get("preferred_alphas", []),
            disfavored_alphas=p.get("disfavored_alphas", []),
            entry_confidence_threshold=p.get("entry_confidence_threshold", 0.5),
            holding_period_bars=p.get("holding_period_bars", 20),
            trailing_stop_activate_r=p.get("trailing_stop_activate_r", 1.5),
            partial_close_r=p.get("partial_close_r", 2.0),
        )

    def get_llm_context(self) -> Dict[str, Any]:
        """Return meta-adaptive context for the LLM decision engine."""
        profile = self.get_active_profile()
        return {
            "regime": self.state.current_regime,
            "bars_in_regime": self.state.bars_in_regime,
            "risk_multiplier": round(profile.risk_multiplier, 2),
            "position_scale": round(profile.position_scale, 2),
            "preferred_alphas": profile.preferred_alphas,
            "disfavored_alphas": profile.disfavored_alphas,
            "entry_confidence_threshold": round(profile.entry_confidence_threshold, 2),
            "holding_period_bars": profile.holding_period_bars,
            "trailing_stop_activate_r": profile.trailing_stop_activate_r,
            "partial_close_r": profile.partial_close_r,
            "adaptation_count": self.state.adaptation_count,
            "total_transitions": self.state.total_transitions,
            "performance_summary": self.state.to_dict().get("regime_performance_summary", {}),
        }

    def get_transition_summary(self) -> Dict[str, Any]:
        """Return transition detector summary."""
        return self.detector.get_transition_summary()

    def get_status(self) -> Dict[str, Any]:
        """Return full engine status."""
        return {
            "meta_adaptive": self.state.to_dict(),
            "detector": self.detector.get_transition_summary(),
            "adapter": {
                "learning_rate": self.adapter.learning_rate,
                "regime_samples": {
                    r: len(perfs) for r, perfs in self.adapter.regime_performance.items()
                },
            },
        }
