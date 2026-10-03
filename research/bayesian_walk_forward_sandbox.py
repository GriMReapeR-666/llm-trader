"""Automated Bayesian Walk-Forward Parameter Tuning Sandbox.

Performs chronological walk-forward validation and Beta-Binomial Bayesian
updating across candidate parameter configurations.
Strictly offline and exploratory: execution_authority is always False.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class SandboxFoldResult:
    fold_idx: int
    train_size: int
    test_size: int
    train_win_rate: float
    test_win_rate: float
    test_sharpe: float
    test_expectancy_r: float
    test_profit_factor: float


@dataclass
class CandidateEvaluation:
    params: Dict[str, Any]
    oos_win_rate: float
    oos_sharpe_mean: float
    oos_sharpe_std: float
    oos_expectancy_r: float
    oos_profit_factor: float
    posterior_alpha: float
    posterior_beta: float
    posterior_mean_win_rate: float
    bayesian_kelly_f: float
    stability_score: float
    folds: List[SandboxFoldResult]


class BayesianWalkForwardSandbox:
    """Chronological walk-forward tuning sandbox with Bayesian edge estimation."""

    def __init__(
        self,
        train_minimum: int = 40,
        test_size: int = 15,
        step: int = 15,
        prior_alpha: float = 2.0,
        prior_beta: float = 2.0,
    ):
        self.train_minimum = max(10, int(train_minimum))
        self.test_size = max(5, int(test_size))
        self.step = max(5, int(step))
        self.prior_alpha = max(0.1, float(prior_alpha))
        self.prior_beta = max(0.1, float(prior_beta))

    def generate_splits(self, n_samples: int) -> List[Tuple[List[int], List[int]]]:
        """Generate chronological train/test index splits."""
        if n_samples < self.train_minimum + self.test_size:
            return []
        splits = []
        for end in range(self.train_minimum, n_samples - self.test_size + 1, self.step):
            train_idx = list(range(0, end))
            test_idx = list(range(end, end + self.test_size))
            splits.append((train_idx, test_idx))
        return splits

    def evaluate_candidate(
        self,
        returns: Sequence[float],
        params: Dict[str, Any],
        rr_multiplier: float = 1.0,
    ) -> CandidateEvaluation:
        """Evaluate a candidate parameter configuration across walk-forward splits."""
        n = len(returns)
        splits = self.generate_splits(n)

        # Base case: if not enough splits, evaluate on single train/test split
        if not splits:
            split_point = max(5, int(n * 0.7))
            splits = [(list(range(0, split_point)), list(range(split_point, n)))]

        fold_results: List[SandboxFoldResult] = []
        all_test_returns: List[float] = []
        test_sharpes: List[float] = []

        for idx, (train_idx, test_idx) in enumerate(splits):
            train_rets = [returns[i] * rr_multiplier for i in train_idx]
            test_rets = [returns[i] * rr_multiplier for i in test_idx]

            train_wr = (sum(1 for r in train_rets if r > 0) / float(max(1, len(train_rets))))
            test_wins = sum(1 for r in test_rets if r > 0)
            test_losses = sum(1 for r in test_rets if r < 0)
            test_wr = test_wins / float(max(1, len(test_rets)))

            mean_test = sum(test_rets) / float(max(1, len(test_rets)))
            var_test = sum((r - mean_test) ** 2 for r in test_rets) / float(max(1, len(test_rets) - 1))
            std_test = math.sqrt(var_test) if var_test > 0 else 1e-6
            sharpe_test = (mean_test / std_test) * math.sqrt(252.0 / max(1, len(test_rets)))

            win_sum = sum(r for r in test_rets if r > 0)
            loss_sum = abs(sum(r for r in test_rets if r < 0))
            pf = (win_sum / loss_sum) if loss_sum > 0 else (2.0 if win_sum > 0 else 1.0)

            fold_results.append(
                SandboxFoldResult(
                    fold_idx=idx,
                    train_size=len(train_rets),
                    test_size=len(test_rets),
                    train_win_rate=round(train_wr, 4),
                    test_win_rate=round(test_wr, 4),
                    test_sharpe=round(sharpe_test, 4),
                    test_expectancy_r=round(mean_test, 4),
                    test_profit_factor=round(pf, 4),
                )
            )
            all_test_returns.extend(test_rets)
            test_sharpes.append(sharpe_test)

        total_test_wins = sum(1 for r in all_test_returns if r > 0)
        total_test_losses = sum(1 for r in all_test_returns if r < 0)
        oos_wr = total_test_wins / float(max(1, len(all_test_returns)))

        # Bayesian Beta-Binomial Posterior
        post_alpha = self.prior_alpha + total_test_wins
        post_beta = self.prior_beta + total_test_losses
        post_mean = post_alpha / (post_alpha + post_beta)

        # Payoff ratio b
        wins_r = [r for r in all_test_returns if r > 0]
        losses_r = [abs(r) for r in all_test_returns if r < 0]
        avg_w = (sum(wins_r) / float(len(wins_r))) if wins_r else 1.5
        avg_l = (sum(losses_r) / float(len(losses_r))) if losses_r else 1.0
        b = (avg_w / avg_l) if avg_l > 0 else 1.5

        # Kelly: f* = (p*b - q) / b
        p = post_mean
        q = 1.0 - p
        raw_kelly = (p * b - q) / b if b > 0 else 0.0
        quarter_kelly = max(0.0, min(0.25, raw_kelly * 0.25))

        # Stability: mean Sharpe / max(0.01, std Sharpe)
        mean_sharpe = sum(test_sharpes) / float(max(1, len(test_sharpes)))
        var_sharpe = sum((s - mean_sharpe) ** 2 for s in test_sharpes) / float(max(1, len(test_sharpes) - 1))
        std_sharpe = math.sqrt(var_sharpe) if var_sharpe > 0 else 0.01
        stability = mean_sharpe / max(0.05, std_sharpe)

        total_win_sum = sum(r for r in all_test_returns if r > 0)
        total_loss_sum = abs(sum(r for r in all_test_returns if r < 0))
        total_pf = (total_win_sum / total_loss_sum) if total_loss_sum > 0 else (2.0 if total_win_sum > 0 else 1.0)
        mean_r = sum(all_test_returns) / float(max(1, len(all_test_returns)))

        return CandidateEvaluation(
            params=params,
            oos_win_rate=round(oos_wr, 4),
            oos_sharpe_mean=round(mean_sharpe, 4),
            oos_sharpe_std=round(std_sharpe, 4),
            oos_expectancy_r=round(mean_r, 4),
            oos_profit_factor=round(total_pf, 4),
            posterior_alpha=round(post_alpha, 2),
            posterior_beta=round(post_beta, 2),
            posterior_mean_win_rate=round(post_mean, 4),
            bayesian_kelly_f=round(quarter_kelly, 4),
            stability_score=round(stability, 4),
            folds=fold_results,
        )

    def tune(
        self,
        setup_id: str,
        returns: Sequence[float],
        candidate_grid: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Run walk-forward simulation across parameter grid and return ranked candidates."""
        if not candidate_grid:
            # Default candidate grid for exploration
            candidate_grid = [
                {"min_rr": 1.5, "rr_multiplier": 0.9, "name": "tight_rr"},
                {"min_rr": 2.0, "rr_multiplier": 1.0, "name": "baseline_rr"},
                {"min_rr": 2.5, "rr_multiplier": 1.15, "name": "wide_rr"},
            ]

        evaluations: List[CandidateEvaluation] = []
        for candidate in candidate_grid:
            rr_mult = float(candidate.get("rr_multiplier", 1.0))
            ev = self.evaluate_candidate(returns, candidate, rr_multiplier=rr_mult)
            evaluations.append(ev)

        # Rank by stability score * bayesian kelly
        evaluations.sort(key=lambda e: (e.stability_score * e.bayesian_kelly_f), reverse=True)
        best = evaluations[0] if evaluations else None

        return {
            "setup_id": setup_id,
            "status": "COMPLETED",
            "n_samples": len(returns),
            "execution_authority": False,
            "note": "Sandbox tuning results are advisory evidence; promotion requires explicit operator review.",
            "optimal_parameters": best.params if best else {},
            "optimal_metrics": {
                "oos_win_rate": best.oos_win_rate,
                "oos_sharpe_mean": best.oos_sharpe_mean,
                "oos_expectancy_r": best.oos_expectancy_r,
                "oos_profit_factor": best.oos_profit_factor,
                "posterior_mean_win_rate": best.posterior_mean_win_rate,
                "bayesian_kelly_f": best.bayesian_kelly_f,
                "stability_score": best.stability_score,
            } if best else {},
            "candidates_ranked": [
                {
                    "params": ev.params,
                    "oos_win_rate": ev.oos_win_rate,
                    "oos_sharpe_mean": ev.oos_sharpe_mean,
                    "posterior_mean_win_rate": ev.posterior_mean_win_rate,
                    "bayesian_kelly_f": ev.bayesian_kelly_f,
                    "stability_score": ev.stability_score,
                }
                for ev in evaluations
            ],
        }
