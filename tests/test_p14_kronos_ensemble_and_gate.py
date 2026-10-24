"""Tests for P1-4: Kronos + Ensemble AND-Gate Audit and Disagreement Rate Metric."""
import pytest
import numpy as np

from core.kronos_h1 import (
    compute_and_gate_decision,
    disagreement_rate,
    KronosEnsembleAndGate,
)
from core.alpha_ensemble import (
    compute_and_gate_decision as ensemble_and_gate,
    disagreement_rate as ensemble_disagreement_rate,
)


def test_and_gate_mutual_buy_agreement():
    decision = compute_and_gate_decision(
        kronos_signal=0.45,
        ensemble_signal=0.35,
        threshold=0.20,
    )
    assert decision["allowed"] is True
    assert decision["decision"] == "BUY"
    assert decision["agreement"] is True
    assert decision["combined_signal"] == pytest.approx(0.35)  # conservative min
    assert decision["disagreement_reason"] is None


def test_and_gate_mutual_sell_agreement():
    decision = compute_and_gate_decision(
        kronos_signal=-0.50,
        ensemble_signal=-0.30,
        threshold=0.20,
    )
    assert decision["allowed"] is True
    assert decision["decision"] == "SELL"
    assert decision["agreement"] is True
    assert decision["combined_signal"] == pytest.approx(-0.30)  # conservative max of negatives
    assert decision["disagreement_reason"] is None


def test_and_gate_directional_conflict_veto():
    # Kronos says strong BUY, Ensemble says strong SELL
    decision = compute_and_gate_decision(
        kronos_signal=0.60,
        ensemble_signal=-0.40,
        threshold=0.20,
    )
    assert decision["allowed"] is False
    assert decision["decision"] == "VETO_CONFLICT"
    assert decision["agreement"] is False
    assert decision["is_conflict"] is True
    assert "conflict" in decision["disagreement_reason"].lower()


def test_and_gate_sub_threshold_veto():
    # Kronos signals BUY, but Ensemble is weak/neutral
    decision = compute_and_gate_decision(
        kronos_signal=0.50,
        ensemble_signal=0.10,
        threshold=0.20,
    )
    assert decision["allowed"] is False
    assert decision["decision"] == "VETO_THRESHOLD"
    assert decision["agreement"] is False
    assert decision["is_conflict"] is False


def test_disagreement_rate_calculation():
    # 10 observations:
    # 6 agree, 3 strictly disagree, 1 neutral
    kronos =   [0.5,  0.4, -0.3, -0.6,  0.8, -0.5,  0.7, -0.4, 0.0,  0.3]
    ensemble = [0.3,  0.5, -0.4,  0.2,  0.6, -0.7, -0.5,  0.4, 0.0,  0.2]
    # Indices 3, 6, 7 have opposite signs
    rate = disagreement_rate(kronos, ensemble, threshold=0.10)
    assert 0.25 <= rate <= 0.35  # ~30% disagreement

    # Identical series -> 0% disagreement
    assert disagreement_rate(kronos, kronos) == 0.0

    # Inverted series -> 100% active disagreement
    inv_kronos = [-x for x in kronos]
    assert disagreement_rate(kronos, inv_kronos, threshold=0.10) > 0.85


def test_auditor_rolling_state_and_summary():
    auditor = KronosEnsembleAndGate(threshold=0.20)
    
    # Process stream of signals
    auditor.evaluate(0.40, 0.50)   # BUY
    auditor.evaluate(0.50, -0.30)  # CONFLICT
    auditor.evaluate(-0.40, -0.45) # SELL
    auditor.evaluate(0.10, 0.50)   # THRESHOLD VETO
    
    summary = auditor.audit_summary()
    assert summary["total_evaluations"] == 4
    assert summary["buy_count"] == 1
    assert summary["sell_count"] == 1
    assert summary["veto_conflict_count"] == 1
    assert summary["veto_threshold_count"] == 1
    assert summary["agreement_rate"] == 0.50
    assert summary["conflict_rate"] == 0.25


def test_reexport_in_alpha_ensemble():
    # Ensure canonical import from core.alpha_ensemble also works seamlessly
    d = ensemble_and_gate(0.4, 0.3, threshold=0.2)
    assert d["allowed"] is True
    rate = ensemble_disagreement_rate([0.5, -0.5], [0.5, 0.5])
    assert rate == 0.5
