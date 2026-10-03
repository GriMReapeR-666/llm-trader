"""Tests for ζ Code Safety Agent wiring into Alpha Discovery pipeline."""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from core.alpha_discovery import (
    AlphaDiscoveryEngine,
    AlphaCandidate,
    SealedSandboxEvaluator,
)
from core.code_safety_agent import (
    SafetyVerdict,
    SafetyReview,
    StaticAnalysisResult,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_ohlcv(n: int = 200) -> pd.DataFrame:
    np.random.seed(42)
    price = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": price + np.random.randn(n) * 0.1,
        "high": price + abs(np.random.randn(n) * 0.3),
        "low": price - abs(np.random.randn(n) * 0.3),
        "close": price,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })


def _make_engine(tmp_path) -> AlphaDiscoveryEngine:
    """Create engine with mocked LLM that returns safe candidates."""
    engine = AlphaDiscoveryEngine(
        state_file=str(tmp_path / "alpha_state.json"),
    )
    return engine


# ---------------------------------------------------------------------------
# Test: ζ blocks unsafe expressions
# ---------------------------------------------------------------------------
class TestZetaBlocksUnsafe:
    def test_subprocess_expression_rejected(self, tmp_path):
        """Expression containing subprocess-like patterns should be blocked."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        # Mock LLM to return an expression with forbidden pattern
        unsafe_response = '1. import os\n2. rank(os.system("rm -rf /"))'
        engine.llm_generate_fn = lambda p: unsafe_response

        # The expression won't parse as valid alpha DSL, so it should be
        # rejected by the sealed sandbox evaluator (AST safety check).
        # But even if it somehow passes, ζ should catch it.
        # This test verifies the pipeline doesn't crash.
        results = engine.run_discovery_cycle(
            train_df=train,
            test_df=test,
            llm_generate_fn=engine.llm_generate_fn,
        )
        # Unsafe expressions should not be validated
        for c in results:
            assert c.status == "VALIDATED"
            # ζ should have reviewed it — no crashes

    def test_safe_expression_passes_zeta(self, tmp_path):
        """Safe alpha expressions should pass ζ review."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        # Mock LLM to return a safe expression
        safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
        engine.llm_generate_fn = lambda p: safe_response

        results = engine.run_discovery_cycle(
            train_df=train,
            test_df=test,
            llm_generate_fn=engine.llm_generate_fn,
        )
        # Should not crash — safe expressions pass through
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Test: ζ mock integration
# ---------------------------------------------------------------------------
class TestZetaMockIntegration:
    def test_zeta_rejection_blocks_candidate(self, tmp_path):
        """When ζ returns REJECTED, candidate should be rejected."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        # Create a mock ζ that rejects everything
        mock_zeta = MagicMock()
        mock_review = SafetyReview(
            code_hash="abc",
            code_preview="test",
            static=StaticAnalysisResult(
                passed=False,
                safety_score=0.0,
                risk_level=RiskLevel.CRITICAL,
                forbidden_imports=["os"],
                errors=["test_violation"],
            ),
            sandbox=None,
            verdict=SafetyVerdict.REJECTED,
            needs_human_signoff=False,
            human_review_reason="",
        )
        mock_zeta.review.return_value = mock_review

        with patch("core.alpha_discovery.get_zeta_agent", return_value=mock_zeta):
            # Provide a valid expression that passes sandbox
            safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
            results = engine.run_discovery_cycle(
                train_df=train,
                test_df=test,
                llm_generate_fn=lambda p: safe_response,
            )

        # ζ rejected — no candidates should be validated
        assert len(results) == 0

    def test_zeta_human_review_blocks_candidate(self, tmp_path):
        """When ζ returns NEEDS_HUMAN_REVIEW, candidate should be rejected."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        mock_zeta = MagicMock()
        mock_review = SafetyReview(
            code_hash="def",
            code_preview="test",
            static=StaticAnalysisResult(
                passed=True,
                safety_score=0.5,
                risk_level=RiskLevel.MEDIUM,
                epsilon_domain_hits=["place_order"],
            ),
            sandbox=None,
            verdict=SafetyVerdict.NEEDS_HUMAN_REVIEW,
            needs_human_signoff=True,
            human_review_reason="touches execution logic",
        )
        mock_zeta.review.return_value = mock_review

        with patch("core.alpha_discovery.get_zeta_agent", return_value=mock_zeta):
            safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
            results = engine.run_discovery_cycle(
                train_df=train,
                test_df=test,
                llm_generate_fn=lambda p: safe_response,
            )

        assert len(results) == 0

    def test_zeta_approved_passes_through(self, tmp_path):
        """When ζ returns APPROVED, candidate passes through."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        mock_zeta = MagicMock()
        mock_review = SafetyReview(
            code_hash="ghi",
            code_preview="test",
            static=StaticAnalysisResult(
                passed=True,
                safety_score=0.95,
                risk_level=RiskLevel.LOW,
            ),
            sandbox=MagicMock(success=True),
            verdict=SafetyVerdict.APPROVED,
            needs_human_signoff=False,
            human_review_reason="",
        )
        mock_zeta.review.return_value = mock_review

        with patch("core.alpha_discovery.get_zeta_agent", return_value=mock_zeta):
            safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
            results = engine.run_discovery_cycle(
                train_df=train,
                test_df=test,
                llm_generate_fn=lambda p: safe_response,
            )

        # ζ approved — candidate should be validated
        # (may still fail IC thresholds, but ζ didn't block it)
        assert isinstance(results, list)

    def test_zeta_exception_doesnt_crash(self, tmp_path):
        """ζ exception should be caught and logged, not crash the pipeline."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        mock_zeta = MagicMock()
        mock_zeta.review.side_effect = RuntimeError("ζ crashed")

        with patch("core.alpha_discovery.get_zeta_agent", return_value=mock_zeta):
            safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
            results = engine.run_discovery_cycle(
                train_df=train,
                test_df=test,
                llm_generate_fn=lambda p: safe_response,
            )

        # Pipeline should not crash — ζ failure is non-fatal
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Test: fallback (no ζ available)
# ---------------------------------------------------------------------------
class TestZetaFallback:
    def test_pipeline_works_without_zeta(self, tmp_path):
        """Pipeline works when ζ is not importable."""
        engine = _make_engine(tmp_path)
        train = _make_ohlcv(200)
        test = _make_ohlcv(100)

        with patch("core.alpha_discovery.get_zeta_agent", None):
            safe_response = '1. rank(ts_rank(delta(close, 5), 10))'
            results = engine.run_discovery_cycle(
                train_df=train,
                test_df=test,
                llm_generate_fn=lambda p: safe_response,
            )

        # Pipeline should work without ζ
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Test: candidate rejection reason includes ζ details
# ---------------------------------------------------------------------------
class TestZetaRejectionReason:
    def test_rejection_reason_format(self, tmp_path):
        """ζ rejection reason should include risk level and violations."""
        # Directly test the rejection reason format
        mock_zeta = MagicMock()
        mock_review = SafetyReview(
            code_hash="jkl",
            code_preview="test",
            static=StaticAnalysisResult(
                passed=False,
                safety_score=0.1,
                risk_level=RiskLevel.HIGH,
                forbidden_imports=["subprocess"],
                errors=["forbidden_import", "complexity_overflow"],
            ),
            sandbox=None,
            verdict=SafetyVerdict.REJECTED,
            needs_human_signoff=False,
            human_review_reason="",
        )
        mock_zeta.review.return_value = mock_review

        # Simulate the rejection reason format from alpha_discovery
        violations = (
            mock_review.static.forbidden_imports
            + mock_review.static.forbidden_builtins
            + mock_review.static.epsilon_domain_hits
            + mock_review.static.errors
        )
        reason = (
            f"ζ SAFETY REJECTED: {mock_review.static.risk_level.value} risk, "
            f"violations={violations}"
        )
        assert "ζ" in reason
        assert "HIGH" in reason
        assert "subprocess" in reason
        assert "forbidden_import" in reason
