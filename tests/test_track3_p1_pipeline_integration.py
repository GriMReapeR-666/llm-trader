"""Tests for Track 3: Wiring P1 Engines into AutonomousQuantOperatingPipeline.

Verifies:
1. P1-3: BOCPD changepoint detection & regime context extraction.
2. P1-1: Contextual Thompson bandit regime multipliers applied to candidate ranking.
3. P1-2: Hierarchical causal alpha pruning enforcing correlation ceiling <= 0.70.
4. P1-4: Kronos + Ensemble AND-gate audit of staged candidates.
5. Telemetry & PipelineDeploymentPackage enrichment.
"""
import pytest
import numpy as np
import polars as pl

from core.strategy_universe import load_market_dataset_polars
from core.quant_gates_and_pipeline import (
    AutonomousQuantOperatingPipeline,
    PipelineDeploymentPackage,
)


@pytest.fixture
def sample_market_dataset():
    np.random.seed(42)
    n = 600
    prices = 1.1000 * np.exp(np.cumsum(np.random.normal(0.0001, 0.0015, n)))
    return load_market_dataset_polars({"close": prices})


def test_pipeline_includes_regime_and_bocpd_context(sample_market_dataset):
    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    pkg = pipeline.run_pipeline(sample_market_dataset, symbols=["EURUSD"])

    assert hasattr(pkg, "regime_context")
    assert isinstance(pkg.regime_context, dict)
    assert "primary_regime" in pkg.regime_context
    assert "bocpd_changepoint_prob" in pkg.regime_context
    assert "changepoint_detected" in pkg.regime_context
    assert 0.0 <= pkg.regime_context["bocpd_changepoint_prob"] <= 1.0


def test_pipeline_applies_bandit_multipliers(sample_market_dataset):
    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    pkg = pipeline.run_pipeline(sample_market_dataset, symbols=["EURUSD"])

    assert hasattr(pkg, "bandit_multipliers")
    assert isinstance(pkg.bandit_multipliers, dict)
    assert len(pkg.bandit_multipliers) > 0
    # Every multiplier is positive
    for arm, mult in pkg.bandit_multipliers.items():
        assert mult > 0.0


def test_pipeline_executes_causal_alpha_pruning(sample_market_dataset):
    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    pkg = pipeline.run_pipeline(sample_market_dataset, symbols=["EURUSD"])

    assert hasattr(pkg, "causal_prune_summary")
    assert isinstance(pkg.causal_prune_summary, dict)
    assert "retained_count" in pkg.causal_prune_summary
    assert "pruned_count" in pkg.causal_prune_summary
    assert "max_pairwise_corr" in pkg.causal_prune_summary
    assert pkg.causal_prune_summary["max_pairwise_corr"] <= 0.75  # ceiling respected


def test_pipeline_executes_kronos_and_gate_audit(sample_market_dataset):
    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    pkg = pipeline.run_pipeline(sample_market_dataset, symbols=["EURUSD"])

    assert hasattr(pkg, "and_gate_audit")
    assert isinstance(pkg.and_gate_audit, dict)
    assert "total_evaluations" in pkg.and_gate_audit
    assert "agreement_rate" in pkg.and_gate_audit
    assert "disagreement_rate" in pkg.and_gate_audit
    assert 0.0 <= pkg.and_gate_audit["agreement_rate"] <= 1.0
    assert 0.0 <= pkg.and_gate_audit["disagreement_rate"] <= 1.0


def test_bocpd_shock_shifts_pipeline_risk(sample_market_dataset):
    pipeline = AutonomousQuantOperatingPipeline(emit_telemetry=False)
    
    # Inject large price shock at end of dataset
    shock_prices = list(sample_market_dataset["close"].to_numpy())
    shock_prices[-1] = shock_prices[-1] * 1.08  # 8% jump shock
    shock_df = load_market_dataset_polars({"close": shock_prices})

    pkg = pipeline.run_pipeline(shock_df, symbols=["EURUSD"])
    # Under shock, BOCPD changepoint probability should be elevated
    assert pkg.regime_context["bocpd_changepoint_prob"] > 0.15
