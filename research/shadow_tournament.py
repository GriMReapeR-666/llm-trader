"""Parallel Shadow-Book A/B Tournament Arena (Stage 2b).

Runs competing candidate StrategyGit branches side-by-side on live incoming market data
without risking capital, scoring them via Bayesian Bootstrap Probability of Superiority.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class ShadowTournamentArena:
    """Evaluates parallel shadow strategy branches and selects the statistically dominant branch."""

    def __init__(self, min_shadow_trades: int = 15):
        self.min_shadow_trades = int(min_shadow_trades)
        # {(strategy_id, branch_name): [r_multiple_1, r_multiple_2, ...]}
        self.shadow_ledgers: Dict[Tuple[str, str], List[float]] = {}

    def record_shadow_trade(self, strategy_id: str, branch_name: str, net_r_multiple: float) -> None:
        """Record a completed shadow paper trade net of spread, commission, and slippage."""
        key = (str(strategy_id), str(branch_name))
        self.shadow_ledgers.setdefault(key, []).append(float(net_r_multiple))

    def evaluate_tournament(self, strategy_id: str, candidate_branches: List[str]) -> Dict[str, Any]:
        """Compare competing shadow branches using Monte Carlo Bootstrap Probability of Best Arm."""
        eligible: Dict[str, np.ndarray] = {}
        pruned: List[str] = []

        for br in candidate_branches:
            r_list = self.shadow_ledgers.get((strategy_id, br), [])
            if len(r_list) < self.min_shadow_trades:
                continue
            r_arr = np.array(r_list, dtype=float)
            mean_r = float(np.mean(r_arr))
            if mean_r <= 0.0:
                pruned.append(br)
            else:
                eligible[br] = r_arr

        if not eligible:
            return {
                "winner_branch": None,
                "win_probabilities": {},
                "pruned_branches": pruned,
                "status": "AWAITING_SHADOW_SAMPLES",
            }

        # Bootstrap probability that each branch has the highest true Sharpe ratio
        n_boot = 2000
        boot_scores: Dict[str, np.ndarray] = {}
        for br, r_arr in eligible.items():
            idx = np.random.randint(0, len(r_arr), size=(n_boot, len(r_arr)))
            samples = r_arr[idx]
            boot_sr = np.mean(samples, axis=1) / np.maximum(np.std(samples, axis=1, ddof=1), 1e-6)
            boot_scores[br] = boot_sr

        branch_names = list(boot_scores.keys())
        if len(branch_names) == 1:
            return {
                "winner_branch": branch_names[0],
                "win_probability": 1.0,
                "pruned_branches": pruned,
                "status": "TOURNAMENT_WINNER_DECIDED",
            }

        score_matrix = np.column_stack([boot_scores[b] for b in branch_names])
        winner_indices = np.argmax(score_matrix, axis=1)

        win_probs = {
            branch_names[i]: round(float(np.mean(winner_indices == i)), 3)
            for i in range(len(branch_names))
        }
        best_branch = max(win_probs, key=win_probs.get)

        if win_probs[best_branch] >= 0.70:
            return {
                "winner_branch": best_branch,
                "win_probability": win_probs[best_branch],
                "win_probabilities": win_probs,
                "pruned_branches": pruned,
                "status": "TOURNAMENT_WINNER_DECIDED",
            }

        return {
            "winner_branch": None,
            "win_probabilities": win_probs,
            "pruned_branches": pruned,
            "status": "TOURNAMENT_IN_PROGRESS",
        }
