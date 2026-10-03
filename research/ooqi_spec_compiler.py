"""Objective-Oriented Quantitative Investment (OOQI) Specification Compiler.

Reference:
Liangliang Zhang (2026), "Objective-Oriented Quantitative Investment:
A Specification-Driven Framework for Automated Synthesis of Trading Strategy Pipelines."

Reframes automated quantitative strategy research from optimizing a scalar number
(e.g., maximize Sharpe/IR) to satisfying an identity (satisfaction of a multi-clause
strategy profile specification).

Key Components:
1. Eight Requirement Families (F1 to F8).
2. Hard vs Soft Clause Semantics with Margin Profiles.
3. Deflated Satisfaction Rate (adjusting for search trials N).
4. Specification Adjudication & Cost of Specification (CoS) calculation.
5. Infeasibility Diagnosis & Minimal Relaxation.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class RequirementFamily(str, Enum):
    """Eight requirement families defined in OOQI (Zhang 2026, §3 & App B)."""
    F1_SIGNAL = "F1_SIGNAL"                        # Signal efficacy / predictive power (e.g. RankIC, ICIR)
    F2_RETURN_RISK = "F2_RETURN_RISK"              # Risk-adjusted return (e.g. IR, MDD, Calmar)
    F3_PURITY = "F3_PURITY"                        # Neutrality / factor purity (e.g. beta_abs, corr_abs)
    F4_REGIME_ROBUSTNESS = "F4_REGIME_ROBUSTNESS"  # Downside resilience (e.g. cond_excess_down)
    F5_EXECUTION_FEASIBILITY = "F5_EXECUTION"      # Turnover, capacity, cost-sensitivity
    F6_PORTFOLIO_STRUCTURE = "F6_PORTFOLIO"        # Breadth, concentration, position caps
    F7_TEMPORAL_STABILITY = "F7_STABILITY"          # IC half-life, regime decay
    F8_TRANSPARENCY = "F8_TRANSPARENCY"            # Interpretability ratio, model complexity


class ClauseType(str, Enum):
    """Clause enforcement semantics."""
    HARD = "HARD"  # Must be satisfied; defines the feasible set F(S)
    SOFT = "SOFT"  # Contributes to secondary weighted ranking objective


class RelOp(str, Enum):
    """Relational operator."""
    LE = "<="
    GE = ">="
    EQ = "=="


@dataclass
class SpecClause:
    """Individual requirement clause in a strategy profile specification."""
    clause_id: str
    family: RequirementFamily
    coord: str                     # Metric coordinate name
    op: RelOp | str
    threshold: float
    clause_type: ClauseType = ClauseType.HARD
    priority: int = 1
    weight: float = 1.0            # Used for soft clause objective scoring
    rationale: str = ""

    def __post_init__(self):
        if isinstance(self.family, str) and not isinstance(self.family, RequirementFamily):
            self.family = RequirementFamily(self.family)
        if isinstance(self.clause_type, str) and not isinstance(self.clause_type, ClauseType):
            self.clause_type = ClauseType(self.clause_type)
        if isinstance(self.op, str) and not isinstance(self.op, RelOp):
            self.op = RelOp(self.op)

    def evaluate(self, observed_value: float) -> ClauseEvaluation:
        val = float(observed_value)
        thresh = float(self.threshold)
        
        if self.op == RelOp.LE:
            margin = thresh - val
            satisfied = val <= thresh
        elif self.op == RelOp.GE:
            margin = val - thresh
            satisfied = val >= thresh
        elif self.op == RelOp.EQ:
            margin = -abs(val - thresh)
            satisfied = math.isclose(val, thresh, rel_tol=1e-5, abs_tol=1e-5)
        else:
            margin = 0.0
            satisfied = False

        score_contrib = (margin * self.weight) if satisfied else (-abs(margin) * self.weight)
        return ClauseEvaluation(
            clause_id=self.clause_id,
            family=self.family.value,
            clause_type=self.clause_type.value,
            satisfied=satisfied,
            observed_value=val,
            threshold=thresh,
            margin=margin,
            score_contribution=score_contrib,
        )


@dataclass
class ClauseEvaluation:
    """Evaluation result for a single specification clause."""
    clause_id: str
    family: str
    clause_type: str
    satisfied: bool
    observed_value: float
    threshold: float
    margin: float
    score_contribution: float = 0.0


@dataclass
class StrategyProfileSpec:
    """Specification of an investor's desired strategy identity."""
    name: str
    clauses: List[SpecClause] = field(default_factory=list)

    def add_clause(self, clause: SpecClause) -> "StrategyProfileSpec":
        self.clauses.append(clause)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "clauses": [
                {
                    "clause_id": c.clause_id,
                    "family": c.family.value,
                    "coord": c.coord,
                    "op": c.op.value if hasattr(c.op, "value") else str(c.op),
                    "threshold": c.threshold,
                    "clause_type": c.clause_type.value if hasattr(c.clause_type, "value") else str(c.clause_type),
                    "priority": c.priority,
                    "weight": c.weight,
                    "rationale": c.rationale,
                }
                for c in self.clauses
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyProfileSpec":
        clauses = [
            SpecClause(
                clause_id=c["clause_id"],
                family=RequirementFamily(c["family"]),
                coord=c["coord"],
                op=RelOp(c["op"]),
                threshold=float(c["threshold"]),
                clause_type=ClauseType(c.get("clause_type", "HARD")),
                priority=int(c.get("priority", 1)),
                weight=float(c.get("weight", 1.0)),
                rationale=str(c.get("rationale", "")),
            )
            for c in data.get("clauses", [])
        ]
        return cls(name=str(data.get("name", "unnamed_spec")), clauses=clauses)


@dataclass
class SatisfactionCertificate:
    """Formal audit certificate validating whether an assembly satisfies a specification."""
    spec_name: str
    is_feasible: bool
    evaluations: Dict[str, ClauseEvaluation]
    raw_satisfaction_rate: float
    hard_satisfaction_rate: float
    deflated_satisfaction_rate: float
    cost_of_specification: float = 0.0
    stress_depth: float = 0.0
    unmet_hard_clauses: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec_name": self.spec_name,
            "is_feasible": self.is_feasible,
            "raw_satisfaction_rate": self.raw_satisfaction_rate,
            "hard_satisfaction_rate": self.hard_satisfaction_rate,
            "deflated_satisfaction_rate": self.deflated_satisfaction_rate,
            "cost_of_specification": self.cost_of_specification,
            "stress_depth": self.stress_depth,
            "unmet_hard_clauses": self.unmet_hard_clauses,
            "evaluations": {k: asdict(v) for k, v in self.evaluations.items()},
        }


class OOQICompiler:
    """Specification compiler and adjudicator (Zhang 2026)."""

    def compile(
        self,
        spec: StrategyProfileSpec,
        candidate_metrics: Dict[str, float],
        search_trials: int = 1,
        sample_size: int = 252,
        unconstrained_score: Optional[float] = None,
        candidate_name: str = "",
    ) -> SatisfactionCertificate:
        """Compile and evaluate candidate metrics against the specification."""
        evals: Dict[str, ClauseEvaluation] = {}
        unmet_hard: List[str] = []
        hard_count = 0
        hard_satisfied = 0
        total_satisfied = 0
        hard_margins: List[float] = []

        for clause in spec.clauses:
            obs = candidate_metrics.get(clause.coord, 0.0)
            res = clause.evaluate(obs)
            evals[clause.clause_id] = res

            if res.satisfied:
                total_satisfied += 1

            if clause.clause_type == ClauseType.HARD:
                hard_count += 1
                hard_margins.append(res.margin)
                if res.satisfied:
                    hard_satisfied += 1
                else:
                    unmet_hard.append(clause.clause_id)

        total_clauses = len(spec.clauses) or 1
        raw_sr = total_satisfied / total_clauses
        hard_sr = (hard_satisfied / hard_count) if hard_count > 0 else 1.0
        is_feasible = (len(unmet_hard) == 0)

        # Deflated Satisfaction Rate (Zhang §4.2, Bailey-López de Prado 2014)
        # Accounts for the maximum expected spurious satisfaction across N search trials
        if search_trials > 1 and sample_size > 0:
            deflation_penalty = math.sqrt(2.0 * math.log(max(1, search_trials)) / float(sample_size))
            deflated_sr = max(0.0, min(1.0, raw_sr - (deflation_penalty * 0.5)))
        else:
            deflated_sr = raw_sr

        # Stress depth: minimum margin among hard clauses (certified radius)
        stress_depth = min(hard_margins) if hard_margins else 0.0

        cos = 0.0
        if unconstrained_score is not None and "ir" in candidate_metrics:
            cos = max(0.0, unconstrained_score - float(candidate_metrics["ir"]))

        return SatisfactionCertificate(
            spec_name=candidate_name or spec.name,
            is_feasible=is_feasible,
            evaluations=evals,
            raw_satisfaction_rate=raw_sr,
            hard_satisfaction_rate=hard_sr,
            deflated_satisfaction_rate=deflated_sr,
            cost_of_specification=cos,
            stress_depth=stress_depth,
            unmet_hard_clauses=unmet_hard,
        )

    def adjudicate(
        self,
        spec: StrategyProfileSpec,
        candidates: List[Dict[str, Any]],
        primary_metric: str = "ir",
    ) -> Tuple[Optional[Dict[str, Any]], List[SatisfactionCertificate]]:
        """Adjudicate candidate strategies under specification satisfaction."""
        if not candidates:
            return None, []

        best_unconstrained = max(float(c.get(primary_metric, 0.0)) for c in candidates)
        certs: List[SatisfactionCertificate] = []
        feasible_pairs: List[Tuple[Dict[str, Any], SatisfactionCertificate]] = []

        for cand in candidates:
            cand_name = str(cand.get("id", cand.get("name", "candidate")))
            cert = self.compile(
                spec=spec,
                candidate_metrics=cand,
                search_trials=len(candidates),
                unconstrained_score=best_unconstrained,
                candidate_name=cand_name,
            )
            certs.append(cert)
            if cert.is_feasible:
                feasible_pairs.append((cand, cert))

        if not feasible_pairs:
            return None, certs

        # Rank feasible candidates by primary metric descending, breaking ties by stress depth
        feasible_pairs.sort(
            key=lambda pair: (float(pair[0].get(primary_metric, 0.0)), pair[1].stress_depth),
            reverse=True,
        )

        winner = feasible_pairs[0][0]
        return winner, certs

    def diagnose_infeasibility(
        self,
        spec: StrategyProfileSpec,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Diagnose binding constraints and determine minimal threshold relaxation."""
        if not candidates or not spec.clauses:
            return {}

        hard_clauses = [c for c in spec.clauses if c.clause_type == ClauseType.HARD]
        if not hard_clauses:
            return {}

        clause_failures: Dict[str, int] = {c.clause_id: 0 for c in hard_clauses}
        minimal_relaxations: Dict[str, float] = {}

        for clause in hard_clauses:
            values = [float(cand.get(clause.coord, 0.0)) for cand in candidates]
            # How many candidates failed this clause?
            if clause.op == RelOp.LE:
                failed = sum(1 for v in values if v > clause.threshold)
                best_val = min(values) if values else clause.threshold
            elif clause.op == RelOp.GE:
                failed = sum(1 for v in values if v < clause.threshold)
                best_val = max(values) if values else clause.threshold
            else:
                failed = sum(1 for v in values if not math.isclose(v, clause.threshold))
                best_val = values[0] if values else clause.threshold

            clause_failures[clause.clause_id] = failed
            minimal_relaxations[clause.clause_id] = best_val

        # Most binding clause is the one that failed the most candidates
        binding_clause_id = max(clause_failures, key=clause_failures.get)
        binding_clause = next(c for c in hard_clauses if c.clause_id == binding_clause_id)

        return {
            "binding_clause": binding_clause_id,
            "failure_count": clause_failures[binding_clause_id],
            "current_threshold": binding_clause.threshold,
            "minimal_relaxed_threshold": minimal_relaxations[binding_clause_id],
            "relaxation_deficit": abs(minimal_relaxations[binding_clause_id] - binding_clause.threshold),
        }
