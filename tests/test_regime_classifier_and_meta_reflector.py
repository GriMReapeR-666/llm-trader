"""Tests for HMM Regime Classifier, Bayesian Model Averaging, and Meta-Reflector.

Covers:
- Rule-based, HMM, and Correlation-based regime classification
- Bayesian Model Averaging (BMA) synthesis
- Meta-Reflector architectural health audit & automated risk remediation
"""
from __future__ import annotations

import numpy as np
import pytest

from intelligence.regime_classifier import MacroRegimeClassifier, MacroRegimeResult
from intelligence.meta_reflector import SystemMetaReflector, HealthAuditReport


def test_macro_regime_classifier_bma():
    classifier = MacroRegimeClassifier()

    # 1. Test Crisis Condition
    res_crisis = classifier.classify_current_regime(
        vix=35.0,
        gold_dxy_corr=0.30,
        avg_pairwise_corr=0.75,
    )
    assert isinstance(res_crisis, MacroRegimeResult)
    assert res_crisis.active_regime in ("CRISIS_PANIC", "NORMAL_RISK_OFF")
    assert res_crisis.is_crisis_override is True
    assert np.isclose(sum(res_crisis.regime_probabilities.values()), 1.0, atol=1e-3)

    # 2. Test Normal Expansion Condition
    res_expansion = classifier.classify_current_regime(
        vix=14.0,
        gold_dxy_corr=-0.65,
        avg_pairwise_corr=0.30,
    )
    assert res_expansion.active_regime == "RISK_ON_EXPANSION"
    assert res_expansion.is_crisis_override is False


def test_system_meta_reflector_audit_and_remediation():
    reflector = SystemMetaReflector()

    # 1. Healthy system test
    report_healthy = reflector.audit_system_health(
        train_sharpe=1.8,
        test_sharpe=1.5,
        regime_flips_30d=3,
        rolling_30_trades_expectancy=0.008,
    )
    assert isinstance(report_healthy, HealthAuditReport)
    assert report_healthy.overall_status == "HEALTHY"
    assert report_healthy.risk_multiplier == 1.0

    # 2. Overfitting & Alpha Decay test
    report_degraded = reflector.audit_system_health(
        train_sharpe=2.5,
        test_sharpe=1.0,  # 60% gap
        regime_flips_30d=14,
        rolling_30_trades_expectancy=-0.002,  # Negative expectancy
    )
    assert report_degraded.overall_status in ("WARNING", "DEGRADED")
    assert report_degraded.risk_multiplier <= 0.50
    assert len(report_degraded.remediation_actions) >= 2


def test_macro_regime_classifier_non_finite_inputs():
    classifier = MacroRegimeClassifier()

    # Rule-based NaN handling
    res_nan = classifier.classify_rule_based(vix=float("nan"))
    assert res_nan["NORMAL_RISK_OFF"] == 0.50
    assert res_nan["RISK_ON_EXPANSION"] == 0.50

    # Correlation matrix Inf handling
    res_inf = classifier.classify_correlation_matrix(avg_pairwise_correlation=float("inf"))
    assert res_inf["NORMAL_RISK_OFF"] == 0.50
    assert res_inf["RISK_ON_EXPANSION"] == 0.50
