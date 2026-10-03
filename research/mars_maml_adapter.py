"""MARS/MAML Adapter — Meta-Adaptive Controller for regime→alpha multipliers.

Wraps research/meta_rl_trainer.py PEARL latent context for MARS-style heterogeneous
agent ensemble + MAML fast adaptation of REGIME_ALPHA_MULTIPLIERS.

Based on:
- MARS: Meta-Adaptive RL 2025 (Meta-Adaptive Controller + Safety-Critic)
- MAML: Finn et al. 2017 Model-Agnostic Meta-Learning

Adapts core/alpha_ensemble.py REGIME_ALPHA_MULTIPLIERS via offline meta-training
on toon splits by hierarchical_portfolio regime. Safety-Critic enforces risk overlay.

Offline research only: execution_authority=False.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, List
import numpy as np

log = logging.getLogger("MARSAdapter")

# MARS Safety-Critic thresholds per regime (from paper)
SAFETY_THRESHOLDS = {
    "LOW_VOL_TREND": 0.30,
    "LOW_VOL_RANGE": 0.30,
    "HIGH_VOL_TREND": 0.50,
    "HIGH_VOL_CRISIS": 0.80,
}

class SafetyCritic:
    """Per-regime risk critic — enforces intrinsic risk profile."""
    def __init__(self, threshold: float = 0.5):
        self.threshold = float(threshold)
        self.risk_history: List[float] = []

    def score(self, returns: List[float]) -> float:
        if not returns:
            return 0.0
        arr = np.array(returns, dtype=float)
        # Downside deviation / VaR 95 proxy
        var95 = float(np.percentile(arr, 5))
        downside = float(np.sqrt(np.mean(np.square(np.minimum(arr, 0.0)))))
        # Risk score in [0,1]: higher = riskier
        risk = min(1.0, max(0.0, (-var95 * 2) + downside))
        self.risk_history.append(risk)
        return risk

    def is_safe(self, returns: List[float]) -> bool:
        return self.score(returns) < self.threshold


class MARSAdapter:
    """Offline MARS loop for alpha multiplier meta-learning."""
    def __init__(self, alpha_ensemble_path: str = "core/alpha_ensemble.py"):
        self.alpha_ensemble_path = alpha_ensemble_path

    def train_regime_multipliers(self, toons_by_regime: Dict[str, List[Dict]], lr: float = 0.05) -> Dict[str, Any]:
        """One MAML outer-loop step: compute per-regime IC and update multipliers."""
        from core.alpha_ensemble import REGIME_ALPHA_MULTIPLIERS
        import copy
        updated = copy.deepcopy(REGIME_ALPHA_MULTIPLIERS)
        report: Dict[str, Any] = {"regimes": {}, "updated_multipliers": {}}
        for regime, toons in toons_by_regime.items():
            # Collect IC per category
            cat_ics: Dict[str, List[float]] = {}
            for toon in toons:
                ensemble = toon.get("indicators", {}).get("alpha_ensemble", {})
                weights = ensemble.get("weights", {})
                overall_ic = ensemble.get("overall_ic", 0)
                # Attribute overall_ic to categories via ALPHA_CATEGORIES
                from core.alpha_ensemble import ALPHA_CATEGORIES
                for alpha, w in weights.items():
                    cat = ALPHA_CATEGORIES.get(alpha, "composite")
                    cat_ics.setdefault(cat, []).append(overall_ic * w)
            # MAML inner: gradient = mean IC per category
            for cat, ics in cat_ics.items():
                mean_ic = float(np.mean(ics)) if ics else 0
                old_mult = float(updated.get(regime, {}).get(cat, 1.0))
                # Safety-Critic gate
                critic = SafetyCritic(threshold=SAFETY_THRESHOLDS.get(regime, 0.5))
                safe = critic.is_safe(ics)
                if not safe:
                    # Reduce aggressive categories in unsafe regime
                    if cat in ("momentum", "composite"):
                        mean_ic = -abs(mean_ic) * 0.5
                # Update: multiplier += lr * mean_ic, clipped [0.1, 2.0]
                new_mult = float(np.clip(old_mult + lr * mean_ic * 5, 0.1, 2.0))
                if regime not in updated:
                    updated[regime] = {}
                updated[regime][cat] = round(new_mult, 3)
                report["regimes"].setdefault(regime, {})[cat] = {"mean_ic": round(mean_ic, 4), "old": old_mult, "new": new_mult, "safe": safe}
        report["updated_multipliers"] = updated
        report["execution_authority"] = False
        return report

    def save_multipliers(self, updated: Dict[str, Any], path: str = "artifacts/mars_alpha_multipliers.json") -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        return str(p)
