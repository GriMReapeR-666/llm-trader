"""Regime Alpha Selector — selects alphas based on current market regime."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

ALPHA_CATEGORIES = {
    "alpha_001": "momentum", "alpha_005": "momentum", "alpha_009": "volume_price",
    "alpha_012": "reversal", "alpha_014": "momentum", "alpha_017": "volume_price",
    "alpha_019": "composite", "alpha_022": "reversal", "alpha_023": "volume_price",
    "alpha_025": "reversal", "alpha_028": "mean_reversion", "alpha_030": "divergence",
    "alpha_034": "momentum", "alpha_035": "reversal", "alpha_036": "composite",
    "alpha_037": "momentum", "alpha_038": "reversal", "alpha_041": "volume_price",
    "alpha_043": "composite", "alpha_053": "composite", "alpha_055": "momentum",
    "alpha_060": "volume_price", "alpha_101": "composite",
    "alpha_P01": "persistence", "alpha_P02": "persistence", "alpha_P03": "persistence",
    "alpha_P04": "persistence", "alpha_P05": "persistence", "alpha_P06": "persistence",
}

REGIME_ALPHA_AFFINITY = {
    "LOW_VOL_TREND": {"momentum": 1.5, "reversal": 0.5, "volume_price": 1.0, "mean_reversion": 0.3, "divergence": 0.8, "composite": 1.0, "persistence": 1.2},
    "LOW_VOL_RANGE": {"momentum": 0.3, "reversal": 1.5, "volume_price": 0.8, "mean_reversion": 1.5, "divergence": 1.2, "composite": 1.0, "persistence": 0.8},
    "HIGH_VOL_TREND": {"momentum": 1.3, "reversal": 0.7, "volume_price": 1.5, "mean_reversion": 0.4, "divergence": 1.0, "composite": 1.0, "persistence": 1.0},
    "HIGH_VOL_CRISIS": {"momentum": 0.5, "reversal": 1.3, "volume_price": 1.5, "mean_reversion": 1.0, "divergence": 1.5, "composite": 0.8, "persistence": 0.5},
    "MEAN_REVERSION": {"momentum": 0.4, "reversal": 1.5, "volume_price": 0.8, "mean_reversion": 1.5, "divergence": 1.0, "composite": 1.0, "persistence": 0.7},
    "UNKNOWN": {"momentum": 1.0, "reversal": 1.0, "volume_price": 1.0, "mean_reversion": 1.0, "divergence": 1.0, "composite": 1.0, "persistence": 1.0},
}


@dataclass
class SelectionResult:
    regime: str
    selected_alphas: List[str]
    weights: Dict[str, float]
    n_selected: int
    affinity_scores: Dict[str, float]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"regime": self.regime, "selected_alphas": self.selected_alphas, "weights": self.weights, "n_selected": self.n_selected, "reason": self.reason}


class RegimeAlphaSelector:
    def __init__(self, min_alphas: int = 3, max_alphas: int = 15):
        self.min_alphas = min_alphas
        self.max_alphas = max_alphas
        self.alpha_perf: Dict[str, Dict[str, float]] = {}

    def update_alpha_performance(self, alpha_name: str, regime: str, ic: float):
        if alpha_name not in self.alpha_perf:
            self.alpha_perf[alpha_name] = {"overall_ic": ic}
        perf = self.alpha_perf[alpha_name]
        key = f"regime_{regime}_score"
        count_key = f"regime_{regime}_count"
        count = perf.get(count_key, 0)
        perf[key] = (perf.get(key, ic) * count + ic) / (count + 1)
        perf[count_key] = count + 1
        perf["overall_ic"] = ic

    def select_alphas(self, current_regime: str, available_alphas: List[str]) -> SelectionResult:
        affinity = REGIME_ALPHA_AFFINITY.get(current_regime, REGIME_ALPHA_AFFINITY["UNKNOWN"])
        scored = []
        for alpha in available_alphas:
            category = ALPHA_CATEGORIES.get(alpha, "composite")
            affinity_mult = affinity.get(category, 1.0)
            perf = self.alpha_perf.get(alpha, {})
            regime_score = perf.get(f"regime_{current_regime}_score", perf.get("overall_ic", 0.02))
            final_score = regime_score * affinity_mult
            scored.append((alpha, final_score, category, affinity_mult))
        scored.sort(key=lambda x: x[1], reverse=True)
        n_select = min(self.max_alphas, max(self.min_alphas, len(scored) // 2))
        selected = scored[:n_select]
        if not selected:
            return SelectionResult(regime=current_regime, selected_alphas=available_alphas[:self.min_alphas], weights={}, n_selected=min(self.min_alphas, len(available_alphas)), affinity_scores={}, reason="fallback")
        total_score = sum(s[1] for s in selected)
        weights = {s[0]: s[1] / total_score if total_score > 0 else 1.0 / len(selected) for s in selected}
        return SelectionResult(regime=current_regime, selected_alphas=[s[0] for s in selected], weights=weights, n_selected=len(selected), affinity_scores={s[0]: s[3] for s in selected}, reason="regime_conditioned")
