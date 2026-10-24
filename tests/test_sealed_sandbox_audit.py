"""Tests for Task 4 (P0-3: AQuA-style sealed sandbox audit).

Verifies:
1. Proposed alphas in alpha-gen loops use ONLY primitives in core/alpha_factors.py DSL (no arbitrary exec/import).
2. feature_schema and evaluator are sealed; changes without OPERATOR_SIGNATURE cause promotion_package to fail.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from core.alpha_factors import ALLOWED_DSL_PRIMITIVES, validate_dsl_expression
from intelligence.feature_schema import verify_feature_schema_sealed, schema_hash
import research.promotion_package as promo
from core.trading_safety import TradingStore


class TestAlphaDSLSealedAudit:
    """Verifies that proposed alphas use only allowed primitives."""

    def test_allowed_primitives_whitelist(self):
        assert isinstance(ALLOWED_DSL_PRIMITIVES, frozenset)
        # Verify key primitives exist
        expected = {"ts_rank", "ts_argmax", "ts_argmin", "delta", "delay", "rank", "scale", "decay_linear"}
        assert expected.issubset(ALLOWED_DSL_PRIMITIVES)

    def test_validate_dsl_expression_accepts_safe_expressions(self):
        safe_exprs = [
            "ts_rank(close, 10)",
            "delta(close, 1) / delay(close, 1)",
            "decay_linear(delta(close, 1), 10) * rank(volume)",
            "sign(delta(volume, 1)) * (ts_argmax(close, 20) - ts_argmin(close, 20))",
            "signed_power(ts_rank(delta(close, 5), 10), 2)",
        ]
        for expr in safe_exprs:
            valid, reason = validate_dsl_expression(expr)
            assert valid is True, f"Expected {expr} to be valid, got: {reason}"

    def test_validate_dsl_expression_rejects_arbitrary_exec_and_imports(self):
        unsafe_exprs = [
            "__import__('os').system('ls')",
            "exec('import sys')",
            "open('passwords.txt').read()",
            "close.__class__.__bases__[0]",
            "eval('1 + 1')",
            "globals()",
            "lambda x: x + 1",
            "close.append(1)",
            "def malicious(): pass",
            "unknown_function(close)",
        ]
        for expr in unsafe_exprs:
            valid, reason = validate_dsl_expression(expr)
            assert valid is False, f"Expected {expr} to be rejected, but passed"
            assert any(term in reason.lower() for term in ["not allowed", "syntax", "forbidden", "disallowed", "dunder", "unsafe"])


class TestSealedFeatureSchemaAudit:
    """Verifies that changes to feature schema without OPERATOR_SIGNATURE block promotion."""

    def test_unmodified_schema_is_sealed(self):
        res = verify_feature_schema_sealed()
        assert res["sealed"] is True
        assert "matches" in res["reason"].lower() or "verified" in res["reason"].lower()

    def test_tampered_schema_fails_without_signature(self, monkeypatch):
        monkeypatch.setattr(
            "intelligence.feature_schema.SEALED_SCHEMA_HASH",
            "0000000000000000000000000000000000000000000000000000000000000000",
        )
        monkeypatch.delenv("OPERATOR_SIGNATURE", raising=False)

        res = verify_feature_schema_sealed()
        assert res["sealed"] is False
        assert "SEALED_SANDBOX_VIOLATION" in res["reason"]

    def test_tampered_schema_passes_with_operator_signature(self, monkeypatch):
        monkeypatch.setattr(
            "intelligence.feature_schema.SEALED_SCHEMA_HASH",
            "0000000000000000000000000000000000000000000000000000000000000000",
        )
        monkeypatch.setenv("OPERATOR_SIGNATURE", "OPERATOR_AUTHORIZED_MIGRATION_OCT2026")

        res = verify_feature_schema_sealed(signature="OPERATOR_AUTHORIZED_MIGRATION_OCT2026")
        assert res["sealed"] is True
        assert res.get("operator_override") is True

    def test_promotion_package_fails_if_sealed_schema_violated(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        TradingStore(str(path))
        monkeypatch.setattr(
            promo, "security_audit",
            lambda *a, **k: {"status": "REVIEW_REQUIRED", "execution_authority": False}
        )
        monkeypatch.setattr(
            "intelligence.feature_schema.SEALED_SCHEMA_HASH",
            "0000000000000000000000000000000000000000000000000000000000000000",
        )
        monkeypatch.delenv("OPERATOR_SIGNATURE", raising=False)

        package = promo.build(str(path))
        assert any("SEALED_SANDBOX_VIOLATION" in str(b) for b in package.get("blockers", []))
        assert package.get("feature_schema_seal", {}).get("sealed") is False
