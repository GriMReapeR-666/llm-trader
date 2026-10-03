"""P07 (Requejo 2026) - Recovery Factor metric (Anexo 1 formula)."""
import pytest

from core.metrics import calculate_recovery_factor


def test_recovery_factor_basic():
    # P07 full-sample ballpark: net profit 6.48x max drawdown
    assert calculate_recovery_factor(648.0, 100.0) == pytest.approx(6.48)


def test_recovery_factor_fail_closed():
    assert calculate_recovery_factor(0.0, 100.0) == 0.0
    assert calculate_recovery_factor(-50.0, 100.0) == 0.0
    assert calculate_recovery_factor(100.0, 0.0) == 999.0
    assert calculate_recovery_factor(float("nan"), 100.0) == 0.0
    assert calculate_recovery_factor("bad", 100.0) == 0.0


def test_recovery_factor_sign_agnostic_drawdown():
    assert calculate_recovery_factor(200.0, -50.0) == pytest.approx(4.0)
