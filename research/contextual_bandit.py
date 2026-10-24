"""Offline contextual take/skip bandit and Thompson Sampling regime adaptation.

The bandit observes only reconciled, point-in-time labelled trades.
It is a research and adaptation report, not an unconstrained live gate,
and maintains strict fail-closed safety semantics.
"""
from __future__ import annotations

import math
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

try:
    from research.research_validation import labelled_dataset, purged_walk_forward_splits
except ImportError:
    from research_validation import labelled_dataset, purged_walk_forward_splits


def _config_error(train_minimum: Any, test_size: Any, step: Any,
                  min_context_samples: Any) -> str | None:
    if (not isinstance(train_minimum, int) or isinstance(train_minimum, bool)
            or train_minimum < 100):
        return "train_minimum must be an integer >= 100"
    if (not isinstance(test_size, int) or isinstance(test_size, bool) or test_size <= 0
            or not isinstance(step, int) or isinstance(step, bool) or step <= 0):
        return "test_size and step must be positive integers"
    if (not isinstance(min_context_samples, int) or isinstance(min_context_samples, bool)
            or min_context_samples < 1):
        return "min_context_samples must be a positive integer"
    return None


class ThompsonSamplingArm:
    """Bayesian Thompson Sampling Arm for trade performance and regime multiplier learning.

    Models trade outcomes with:
      - Beta(alpha, beta) conjugate prior for win probability p = P(R > 0)
      - Predictive Normal distribution for payout magnitude E[R | win] and E[R | loss]
    """

    def __init__(
        self,
        alpha_prior: float = 2.0,
        beta_prior: float = 2.0,
        mean_r_prior: float = 0.0,
        r_scale_prior: float = 1.0,
        prior_weight: float = 3.0,
    ):
        self.alpha_prior = max(0.1, float(alpha_prior))
        self.beta_prior = max(0.1, float(beta_prior))
        self.alpha = self.alpha_prior
        self.beta = self.beta_prior

        self.wins = 0
        self.losses = 0
        self.samples = 0
        self.r_values: List[float] = []
        self.mean_r_prior = float(mean_r_prior)
        self.r_scale_prior = float(r_scale_prior)
        self.prior_weight = max(0.1, float(prior_weight))

    def update(self, r_multiple: float) -> None:
        r = float(r_multiple)
        self.samples += 1
        self.r_values.append(r)
        if r > 0:
            self.wins += 1
            self.alpha += 1.0
        else:
            self.losses += 1
            self.beta += 1.0

    def posterior_win_rate(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def posterior_mean_r(self) -> float:
        if not self.r_values:
            return self.mean_r_prior
        emp_mean = sum(self.r_values) / len(self.r_values)
        shrinkage = self.prior_weight / (len(self.r_values) + self.prior_weight)
        return float((1.0 - shrinkage) * emp_mean + shrinkage * self.mean_r_prior)

    def sample(self, seed: Optional[int] = None) -> float:
        """Sample expected return theta ~ Thompson posterior."""
        rng = np.random.default_rng(seed) if seed is not None else np.random
        win_prob = rng.beta(self.alpha, self.beta)

        pos_r = [r for r in self.r_values if r > 0]
        neg_r = [r for r in self.r_values if r <= 0]

        if pos_r:
            mean_pos = float(np.mean(pos_r))
            std_pos = float(np.std(pos_r)) if len(pos_r) > 1 else 0.5
            sampled_win = max(0.01, rng.normal(mean_pos, std_pos / math.sqrt(len(pos_r))))
        else:
            sampled_win = max(0.1, self.mean_r_prior if self.mean_r_prior > 0 else 1.0)

        if neg_r:
            mean_neg = float(np.mean(neg_r))
            std_neg = float(np.std(neg_r)) if len(neg_r) > 1 else 0.5
            sampled_loss = min(-0.01, rng.normal(mean_neg, std_neg / math.sqrt(len(neg_r))))
        else:
            sampled_loss = -1.0

        return float(win_prob * sampled_win + (1.0 - win_prob) * sampled_loss)


class ContextualThompsonBandit:
    """Contextual multi-armed bandit using Thompson sampling.

    Manages per-context action distributions for:
    1. Setup ranking & gating (context=setup_id, arm=setup_id)
    2. Dynamic Regime-Alpha Multipliers (context=regime, arm=alpha_category)
    """

    def __init__(self, priors: Optional[Dict[str, Dict[str, float]]] = None):
        self.priors = priors or {}
        self.arms: Dict[str, Dict[str, ThompsonSamplingArm]] = defaultdict(dict)
        self._init_priors()

    def _init_priors(self) -> None:
        for ctx, arm_map in self.priors.items():
            for arm, prior_val in arm_map.items():
                self.arms[ctx][arm] = ThompsonSamplingArm(
                    alpha_prior=2.0 * prior_val,
                    beta_prior=2.0,
                    mean_r_prior=prior_val - 1.0,
                )

    def get_arm(self, context: str, arm: str) -> ThompsonSamplingArm:
        if arm not in self.arms[context]:
            prior_mult = self.priors.get(context, {}).get(arm, 1.0)
            self.arms[context][arm] = ThompsonSamplingArm(
                alpha_prior=2.0 * prior_mult,
                beta_prior=2.0,
                mean_r_prior=prior_mult - 1.0,
            )
        return self.arms[context][arm]

    def update(self, context: str, arm: str, r_multiple: float) -> None:
        self.get_arm(context, arm).update(r_multiple)

    def select_arm(self, context: str, candidate_arms: List[str], seed: Optional[int] = None) -> str:
        """Select best arm among candidates using Thompson Sampling."""
        if not candidate_arms:
            return ""
        sampled_utilities = [
            (self.get_arm(context, arm).sample(seed=None if seed is None else seed + idx), arm)
            for idx, arm in enumerate(candidate_arms)
        ]
        sampled_utilities.sort(key=lambda x: x[0], reverse=True)
        return sampled_utilities[0][1]

    def get_expected_multiplier(self, context: str, arm: str) -> float:
        """Compute the posterior multiplier for (context, arm)."""
        arm_obj = self.get_arm(context, arm)
        prior_mult = self.priors.get(context, {}).get(arm, 1.0)
        if arm_obj.samples == 0:
            return float(prior_mult)

        win_rate = arm_obj.posterior_win_rate()
        mean_r = arm_obj.posterior_mean_r()

        raw_mult = max(0.1, 1.0 + (win_rate - 0.5) * 2.0 + 0.5 * mean_r)
        credibility = arm_obj.samples / (arm_obj.samples + 15.0)
        adjusted_mult = (1.0 - credibility) * prior_mult + credibility * raw_mult
        return float(min(3.0, max(0.1, round(adjusted_mult, 4))))

    def export_multipliers(self) -> Dict[str, Dict[str, float]]:
        """Export learned regime multipliers dictionary."""
        exported = {}
        all_contexts = set(self.priors.keys()).union(self.arms.keys())
        for ctx in all_contexts:
            exported[ctx] = {}
            all_arms = set(self.priors.get(ctx, {}).keys()).union(self.arms[ctx].keys())
            for arm in all_arms:
                exported[ctx][arm] = self.get_expected_multiplier(ctx, arm)
        return exported

    def save_multipliers_artifact(self, path: str = "artifacts/mars_alpha_multipliers.json") -> None:
        """Save multipliers artifact for alpha_ensemble consumption."""
        data = self.export_multipliers()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_file = target.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_file.replace(target)


def _arm_stats(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    arm = ThompsonSamplingArm()
    for row in rows:
        arm.update(float(row.get("r_multiple", 0.0)))
    mean_r = arm.posterior_mean_r()
    return {
        "samples": len(rows),
        "posterior_win_rate": arm.posterior_win_rate(),
        "mean_r": mean_r,
        "take_score": mean_r * ((len(rows) + 2) / (len(rows) + 10)),
    }


def bandit_report(path: str = "trading_state.db", *, train_minimum: int = 100,
                  test_size: int = 25, step: int = 25, min_context_samples: int = 5) -> Dict[str, Any]:
    configuration_error = _config_error(train_minimum, test_size, step, min_context_samples)
    if configuration_error:
        return {"status": "INVALID_INPUT", "out_of_sample_status": "NOT_AVAILABLE",
                "execution_authority": False, "folds": [], "reason": configuration_error,
                "configuration": {"train_minimum": train_minimum, "test_size": test_size,
                                   "step": step, "min_context_samples": min_context_samples}}
    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    report: Dict[str, Any] = {"dataset": {
                                  "sample_size": len(rows),
                                  "excluded": dataset.get("excluded", {}),
                                  "point_in_time": dataset.get("point_in_time", False),
                                  "cost_model": dataset.get("cost_model", "unknown"),
                                  "training_data_hash": hashlib.sha256(json.dumps(
                                      [row.get("ticket") for row in rows], sort_keys=True
                                  ).encode("utf-8")).hexdigest(),
                              }, "execution_authority": False, "folds": []}
    folds = purged_walk_forward_splits(rows, train_minimum=train_minimum, test_size=test_size, step=step) if rows else []
    if not folds:
        report.update({"status": "INSUFFICIENT_DATA", "out_of_sample_status": "NOT_AVAILABLE",
                       "reason": "Need a sufficiently large labelled journal for chronological bandit evaluation."})
        return report

    for fold_number, fold in enumerate(folds, start=1):
        train = [rows[i] for i in fold["train"]]
        test = [rows[i] for i in fold["test"]]
        contexts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in train:
            contexts[str(row.get("setup_id", "unknown"))].append(row)
        taken, skipped, taken_r = 0, 0, []
        decisions = []
        eval_contexts = {k: list(v) for k, v in contexts.items()}
        for row in test:
            context = str(row.get("setup_id", "unknown"))
            stats = _arm_stats(eval_contexts.get(context, []))
            take = stats["samples"] >= min_context_samples and stats["take_score"] > 0
            if take:
                taken += 1
                taken_r.append(float(row.get("r_multiple", 0.0)))
            else:
                skipped += 1
            decisions.append({
                "ticket": row.get("ticket"),
                "setup_id": context,
                "action": "TAKE" if take else "SKIP",
                "train_context": stats,
            })
            if context not in eval_contexts:
                eval_contexts[context] = []
            eval_contexts[context].append(row)

        report["folds"].append({
            "fold": fold_number,
            "train_size": len(train),
            "test_size": len(test),
            "taken": taken,
            "skipped": skipped,
            "coverage": taken / len(test) if test else 0.0,
            "selected_expectancy_r": sum(taken_r) / len(taken_r) if taken_r else None,
            "selected_net_r": sum(taken_r),
            "decisions": decisions,
        })
    report.update({
        "status": "READY_FOR_REVIEW",
        "out_of_sample_status": "EVALUATED_PURGED_WALK_FORWARD",
        "promotion_safe": False,
        "reason": "Bandit output is an offline ranking artifact; it needs baseline superiority, stability, and operator approval before any confidence adjustment.",
    })
    return report
