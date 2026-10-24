"""Unit tests for Contrarian Alpha Factor Auto-Negation (Flag 2)."""
import numpy as np
import pandas as pd
import pytest

from core.alpha_discovery import SealedSandboxEvaluator, AlphaCandidate


def _make_sinusoidal_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    t = np.linspace(0, 50, n)
    close = 100.0 + np.sin(t) * 10.0
    return pd.DataFrame({
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.ones(n) * 1000.0,
    })


def test_negative_ic_contrarian_factor_auto_inverted():
    """Negative IC candidate with strong predictive power is auto-inverted to positive alpha."""
    evaluator = SealedSandboxEvaluator(min_ic_ir=0.15, min_ic_mean=0.02, allow_contrarian=True)
    df = _make_sinusoidal_ohlcv(300)
    train = df.iloc[:200]
    test = df.iloc[200:]

    candidate = AlphaCandidate(
        expression="0 - delta(close, 1)",
        name="contrarian_sine_alpha",
        category="reversal",
    )
    result = evaluator.evaluate_candidate(candidate, train, test)

    assert result.status == "VALIDATED", f"Expected VALIDATED but got {result.status}: {result.rejection_reason}"
    assert result.metadata.get("is_inverted_contrarian") is True
    assert result.expression.startswith("-1.0 * (")
    assert result.ic_mean > 0.02
    assert result.ic_ir > 0.15


def test_negative_ic_too_small_magnitude_rejected():
    """Candidate with |IC| < min_ic_mean must be rejected for low magnitude."""
    evaluator = SealedSandboxEvaluator(min_ic_ir=0.15, min_ic_mean=0.30, allow_contrarian=True)
    np.random.seed(42)
    n = 300
    close = 100.0 + np.cumsum(np.random.normal(0, 1, n))
    df = pd.DataFrame({
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.random.uniform(500, 1500, n),
    })
    train = df.iloc[:200]
    test = df.iloc[200:]

    candidate = AlphaCandidate(
        expression="delta(volume, 1)",
        name="random_noise_alpha",
        category="volume_price",
    )
    result = evaluator.evaluate_candidate(candidate, train, test)
    assert result.status == "REJECTED"
    assert "|IC| mean too low" in result.rejection_reason


def test_negative_ic_disallowed_when_contrarian_disabled():
    """When allow_contrarian=False, negative IC must be rejected."""
    evaluator = SealedSandboxEvaluator(min_ic_ir=0.15, min_ic_mean=0.02, allow_contrarian=False)
    df = _make_sinusoidal_ohlcv(300)
    train = df.iloc[:200]
    test = df.iloc[200:]

    candidate = AlphaCandidate(
        expression="0 - delta(close, 1)",
        name="neg_alpha_disabled_contrarian",
        category="reversal",
    )
    result = evaluator.evaluate_candidate(candidate, train, test)
    assert result.status == "REJECTED"
    assert "too low" in result.rejection_reason
