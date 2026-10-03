"""Unit tests for P17: Objective-Oriented Quantitative Investment (OOQI)
Specification-Driven Strategy Pipeline Compiler (Zhang 2026).

Tests cover:
- Specification language with 8 requirement families (F1-F8).
- Hard vs Soft clause semantics and margin profiles.
- Clause satisfaction, certification, and deflated satisfaction rate (accounting for search trials N).
- Adjudication: Selecting the best feasible strategy (satisfaction of identity)
  vs result-oriented selection (which picks high nominal score but fails constraints).
- Infeasibility diagnosis & minimal relaxation recommendation.
"""
from __future__ import annotations

import math
import pytest

from research.ooqi_spec_compiler import (
    ClauseEvaluation,
    ClauseType,
    OOQICompiler,
    RelOp,
    RequirementFamily,
    SatisfactionCertificate,
    SpecClause,
    StrategyProfileSpec,
)


def test_spec_clause_and_profile_construction():
    """Verify specification building across multiple requirement families."""
    spec = StrategyProfileSpec("institutional_alpha_profile")
    
    spec.add_clause(SpecClause(
        clause_id="purity_beta_mkt",
        family=RequirementFamily.F3_PURITY,
        coord="beta_abs_mkt",
        op=RelOp.LE,
        threshold=0.08,
        clause_type=ClauseType.HARD,
        rationale="Active returns must be pure selection, not disguised market beta",
    ))
    spec.add_clause(SpecClause(
        clause_id="max_drawdown_cap",
        family=RequirementFamily.F2_RETURN_RISK,
        coord="mdd",
        op=RelOp.LE,
        threshold=0.12,
        clause_type=ClauseType.HARD,
        rationale="Downside risk constraint",
    ))
    spec.add_clause(SpecClause(
        clause_id="turnover_budget",
        family=RequirementFamily.F5_EXECUTION_FEASIBILITY,
        coord="annual_turnover",
        op=RelOp.LE,
        threshold=12.0,
        clause_type=ClauseType.SOFT,
        weight=2.0,
        rationale="Turnover cost control",
    ))
    spec.add_clause(SpecClause(
        clause_id="downside_resilience",
        family=RequirementFamily.F4_REGIME_ROBUSTNESS,
        coord="cond_excess_down",
        op=RelOp.GE,
        threshold=0.0,
        clause_type=ClauseType.HARD,
        rationale="Must remain non-negative during market drawdown regimes",
    ))

    assert len(spec.clauses) == 4
    d = spec.to_dict()
    restored = StrategyProfileSpec.from_dict(d)
    assert len(restored.clauses) == 4
    assert restored.name == "institutional_alpha_profile"


def test_compiler_hard_clause_rejection_and_margins():
    """Verify that a candidate violating a hard constraint fails certification."""
    spec = StrategyProfileSpec("purity_and_drawdown")
    spec.add_clause(SpecClause(
        clause_id="purity",
        family=RequirementFamily.F3_PURITY,
        coord="beta_abs_mkt",
        op=RelOp.LE,
        threshold=0.05,
        clause_type=ClauseType.HARD,
    ))
    spec.add_clause(SpecClause(
        clause_id="max_dd",
        family=RequirementFamily.F2_RETURN_RISK,
        coord="mdd",
        op=RelOp.LE,
        threshold=0.10,
        clause_type=ClauseType.HARD,
    ))

    compiler = OOQICompiler()
    
    # Candidate violating purity (beta=0.12 > 0.05)
    failing_metrics = {"beta_abs_mkt": 0.12, "mdd": 0.08}
    cert_fail = compiler.compile(spec, failing_metrics)
    assert cert_fail.is_feasible is False
    assert "purity" in cert_fail.unmet_hard_clauses
    assert cert_fail.evaluations["purity"].satisfied is False
    assert cert_fail.evaluations["purity"].margin < 0  # Negative margin indicates breach
    assert cert_fail.evaluations["max_dd"].satisfied is True
    assert cert_fail.evaluations["max_dd"].margin > 0  # Buffer

    # Candidate satisfying both
    passing_metrics = {"beta_abs_mkt": 0.03, "mdd": 0.07}
    cert_pass = compiler.compile(spec, passing_metrics)
    assert cert_pass.is_feasible is True
    assert len(cert_pass.unmet_hard_clauses) == 0
    assert cert_pass.evaluations["purity"].margin == pytest.approx(0.02)
    assert cert_pass.evaluations["max_dd"].margin == pytest.approx(0.03)
    assert cert_pass.stress_depth > 0.0


def test_compiler_deflated_satisfaction_rate():
    """Verify deflated satisfaction rate penalizes large search widths N (Zhang §4.2, Bailey 2014)."""
    spec = StrategyProfileSpec("multi_clause")
    for i in range(5):
        spec.add_clause(SpecClause(
            clause_id=f"c{i}",
            family=RequirementFamily.F1_SIGNAL,
            coord=f"metric_{i}",
            op=RelOp.GE,
            threshold=1.0,
            clause_type=ClauseType.HARD,
        ))

    compiler = OOQICompiler()
    metrics = {f"metric_{i}": 1.5 for i in range(5)}
    
    # Single trial: deflated SR equals raw SR (1.0)
    cert_n1 = compiler.compile(spec, metrics, search_trials=1, sample_size=252)
    assert cert_n1.raw_satisfaction_rate == 1.0
    assert cert_n1.deflated_satisfaction_rate == pytest.approx(1.0, abs=0.01)

    # 1,000 trials: search deflation kicks in
    cert_n1000 = compiler.compile(spec, metrics, search_trials=1000, sample_size=252)
    assert cert_n1000.raw_satisfaction_rate == 1.0
    assert cert_n1000.deflated_satisfaction_rate < 1.0
    assert cert_n1000.deflated_satisfaction_rate > 0.5


def test_adjudicate_result_oriented_vs_specification_driven():
    """Reproduce Zhang (2026) Table 5 finding:
    Result-oriented selection picks Candidate A with top nominal IR (2.4) but failing purity;
    Specification-driven selection picks Candidate B (IR 2.1) which satisfies 100% of the spec.
    """
    spec = StrategyProfileSpec("institutional_mandate")
    spec.add_clause(SpecClause(
        clause_id="purity",
        family=RequirementFamily.F3_PURITY,
        coord="beta_abs_mkt",
        op=RelOp.LE,
        threshold=0.05,
        clause_type=ClauseType.HARD,
    ))
    spec.add_clause(SpecClause(
        clause_id="drawdown",
        family=RequirementFamily.F2_RETURN_RISK,
        coord="mdd",
        op=RelOp.LE,
        threshold=0.15,
        clause_type=ClauseType.HARD,
    ))
    spec.add_clause(SpecClause(
        clause_id="turnover",
        family=RequirementFamily.F5_EXECUTION_FEASIBILITY,
        coord="annual_turnover",
        op=RelOp.LE,
        threshold=10.0,
        clause_type=ClauseType.SOFT,
    ))

    candidates = [
        # Candidate A: Result-oriented darling (high IR, but fails purity with beta=0.18)
        {"id": "cand_A", "ir": 2.4, "beta_abs_mkt": 0.18, "mdd": 0.12, "annual_turnover": 8.0},
        # Candidate B: Specification-compliant (solid IR, passes all hard clauses)
        {"id": "cand_B", "ir": 2.1, "beta_abs_mkt": 0.03, "mdd": 0.11, "annual_turnover": 6.5},
        # Candidate C: High beta & high drawdown
        {"id": "cand_C", "ir": 1.8, "beta_abs_mkt": 0.25, "mdd": 0.22, "annual_turnover": 15.0},
    ]

    compiler = OOQICompiler()
    winner, certs = compiler.adjudicate(spec, candidates, primary_metric="ir")

    assert winner is not None
    assert winner["id"] == "cand_B"  # B is picked despite lower nominal IR than A
    
    # Cost of Specification (CoS): 2.4 - 2.1 = 0.3 IR points paid for identity purity
    cert_b = next(c for c in certs if c.spec_name == "cand_B")
    assert cert_b.cost_of_specification == pytest.approx(0.3, abs=0.01)


def test_infeasibility_diagnosis_and_minimal_relaxation():
    """Verify that when no candidate is feasible, compiler diagnoses the exact binding clause
    and computes the minimal required relaxation to attain feasibility.
    """
    spec = StrategyProfileSpec("overly_tight_spec")
    spec.add_clause(SpecClause(
        clause_id="impossible_purity",
        family=RequirementFamily.F3_PURITY,
        coord="beta_abs_mkt",
        op=RelOp.LE,
        threshold=0.01,  # Too tight
        clause_type=ClauseType.HARD,
    ))

    candidates = [
        {"id": "cand_1", "ir": 1.5, "beta_abs_mkt": 0.03},
        {"id": "cand_2", "ir": 1.8, "beta_abs_mkt": 0.025},
    ]

    compiler = OOQICompiler()
    winner, certs = compiler.adjudicate(spec, candidates, primary_metric="ir")
    
    assert winner is None
    diagnosis = compiler.diagnose_infeasibility(spec, candidates)
    assert diagnosis["binding_clause"] == "impossible_purity"
    assert diagnosis["current_threshold"] == 0.01
    assert diagnosis["minimal_relaxed_threshold"] == pytest.approx(0.025)
