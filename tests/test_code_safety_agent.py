"""Tests for ζ (Zeta) Code Safety Agent — core/code_safety_agent.py"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from core.code_safety_agent import (
    CodeSafetyAgent,
    CodeVersionTracker,
    RiskLevel,
    SafetyReview,
    SafetyVerdict,
    SandboxResult,
    StaticAnalysisResult,
    static_analyze,
    run_sandboxed,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_BUILTINS,
    EPSILON_DOMAIN_PATTERNS,
    MIN_SAFETY_SCORE,
)


# ---------------------------------------------------------------------------
# Static Analysis Tests
# ---------------------------------------------------------------------------

class TestStaticAnalysis:
    def test_clean_code_passes(self):
        code = """
result = sum([1, 2, 3])
"""
        r = static_analyze(code)
        assert r.passed is True
        assert r.safety_score >= MIN_SAFETY_SCORE
        assert r.risk_level == RiskLevel.LOW
        assert r.forbidden_imports == []
        assert r.forbidden_builtins == []
        assert r.epsilon_domain_hits == []

    def test_numpy_pandas_allowed(self):
        code = """
import numpy as np
import pandas as pd
arr = np.array([1, 2, 3])
df = pd.DataFrame({"a": [1, 2, 3]})
result = float(np.mean(arr))
"""
        r = static_analyze(code)
        assert r.passed is True
        assert r.forbidden_imports == []

    def test_subprocess_forbidden(self):
        code = """
import subprocess
result = subprocess.run(["ls"], capture_output=True)
"""
        r = static_analyze(code)
        assert r.passed is False
        assert "subprocess" in r.forbidden_imports
        # Score is penalized; passed=False because forbidden_imports is non-empty
        assert r.safety_score <= MIN_SAFETY_SCORE

    def test_requests_forbidden(self):
        code = """
import requests
result = requests.get("https://evil.com")
"""
        r = static_analyze(code)
        assert r.passed is False
        assert "requests" in r.forbidden_imports

    def test_exec_forbidden(self):
        code = """
x = 42
exec("x = 99")
result = x
"""
        r = static_analyze(code)
        assert r.passed is False
        assert "exec" in r.forbidden_builtins

    def test_eval_forbidden(self):
        code = """
result = eval("2 + 2")
"""
        r = static_analyze(code)
        assert r.passed is False
        assert "eval" in r.forbidden_builtins

    def test_open_forbidden(self):
        code = """
with open("secret.txt") as f:
    result = f.read()
"""
        r = static_analyze(code)
        assert r.passed is False
        assert "open" in r.forbidden_builtins

    def test_epsilon_domain_detected(self):
        code = """
def compute_signal(data):
    signal = data["close"].pct_change()
    return signal
# This function references order placement
place_market_order("EURUSD", 0.1, "BUY")
"""
        r = static_analyze(code)
        assert len(r.epsilon_domain_hits) > 0
        assert any("place_market_order" in h for h in r.epsilon_domain_hits)

    def test_epsilon_domain_attribute_access(self):
        code = """
mt5.order_send(request)
"""
        r = static_analyze(code)
        assert len(r.epsilon_domain_hits) > 0

    def test_syntax_error_returns_critical(self):
        code = """
def broken(
    result = 42
"""
        r = static_analyze(code)
        assert r.passed is False
        assert r.safety_score == 0.0
        assert r.risk_level == RiskLevel.CRITICAL
        assert len(r.errors) > 0

    def test_complexity_high(self):
        # Generate code with many nodes
        lines = ["x_{} = {}".format(i, i) for i in range(120)]
        code = "\n".join(lines)
        r = static_analyze(code)
        assert r.complexity_score > 100

    def test_risk_escalation_on_low_score(self):
        code = """
import subprocess
import requests
import socket
exec("test")
eval("test")
open("file")
"""
        r = static_analyze(code)
        assert r.safety_score < 0.3
        assert r.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Sandbox Execution Tests
# ---------------------------------------------------------------------------

class TestSandboxExecution:
    def test_simple_execution(self):
        code = "result = 2 + 2"
        r = run_sandboxed(code)
        assert r.success is True
        assert r.result == 4

    def test_numpy_in_sandbox(self):
        code = """
import numpy as np
result = float(np.mean([1, 2, 3, 4, 5]))
"""
        r = run_sandboxed(code)
        assert r.success is True
        assert r.result == 3.0

    def test_pandas_in_sandbox(self):
        code = """
import pandas as pd
df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
result = df["a"].sum()
"""
        r = run_sandboxed(code)
        assert r.success is True
        assert r.result == 6

    def test_input_data_injection(self):
        code = "result = close_prices.mean()"
        data = {"close_prices": pd.Series([1.0, 2.0, 3.0])}
        r = run_sandboxed(code, input_data=data)
        assert r.success is True
        assert r.result == pytest.approx(2.0)

    def test_execution_error(self):
        code = "result = 1 / 0"
        r = run_sandboxed(code)
        assert r.success is False
        assert "ZeroDivisionError" in r.error

    def test_syntax_error_in_sandbox(self):
        code = "def broken("
        r = run_sandboxed(code)
        assert r.success is False

    def test_execution_time_recorded(self):
        code = "result = sum(range(1000))"
        r = run_sandboxed(code)
        assert r.execution_time_ms > 0

    def test_stdout_captured(self):
        code = """
print("hello from sandbox")
result = 42
"""
        r = run_sandboxed(code)
        assert r.success is True
        assert "hello from sandbox" in r.stdout

    def test_private_input_data_blocked(self):
        code = "result = _secret"
        data = {"_secret": "should not be injected"}
        r = run_sandboxed(code, input_data=data)
        assert r.success is False

    def test_forbidden_open_in_sandbox(self):
        # Even if static analysis passes, open should not be available
        code = """
try:
    result = open("test.txt")
except NameError:
    result = "blocked"
"""
        r = run_sandboxed(code)
        assert r.success is True
        assert r.result == "blocked"


# ---------------------------------------------------------------------------
# Code Version Tracker Tests
# ---------------------------------------------------------------------------

class TestCodeVersionTracker:
    def test_is_new_code(self, tmp_path):
        tracker = CodeVersionTracker(state_path=str(tmp_path / "versions.json"))
        assert tracker.is_new_or_changed("x = 1", "test1") is True

    def test_same_code_not_changed(self, tmp_path):
        tracker = CodeVersionTracker(state_path=str(tmp_path / "versions.json"))
        tracker.record_version("x = 1", "test1", "APPROVED")
        assert tracker.is_new_or_changed("x = 1", "test1") is False

    def test_changed_code_detected(self, tmp_path):
        tracker = CodeVersionTracker(state_path=str(tmp_path / "versions.json"))
        tracker.record_version("x = 1", "test1", "APPROVED")
        assert tracker.is_new_or_changed("x = 2", "test1") is True

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "versions.json")
        tracker1 = CodeVersionTracker(state_path=path)
        tracker1.record_version("x = 1", "test1", "APPROVED")

        tracker2 = CodeVersionTracker(state_path=path)
        assert tracker2.is_new_or_changed("x = 1", "test1") is False

    def test_version_info(self, tmp_path):
        tracker = CodeVersionTracker(state_path=str(tmp_path / "versions.json"))
        tracker.record_version("x = 1", "test1", "APPROVED")
        info = tracker.get_version_info("test1")
        assert info is not None
        assert info["verdict"] == "APPROVED"
        assert info["review_count"] == 1


# ---------------------------------------------------------------------------
# Full Review Tests
# ---------------------------------------------------------------------------

class TestCodeSafetyAgent:
    def _make_agent(self, tmp_path):
        return CodeSafetyAgent(
            state_path=str(tmp_path / "versions.json"),
            sandbox_timeout=2.0,
        )

    def test_approve_clean_code(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = """
import numpy as np
data = np.array([1, 2, 3, 4, 5])
result = float(np.mean(data))
"""
        review = agent.review(code, code_id="clean_test")
        assert review.verdict == SafetyVerdict.APPROVED
        assert review.needs_human_signoff is False
        assert review.static.safety_score >= MIN_SAFETY_SCORE

    def test_reject_forbidden_import(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = "import subprocess; result = 42"
        review = agent.review(code, code_id="bad_import")
        assert review.verdict == SafetyVerdict.REJECTED
        assert review.needs_human_signoff is False

    def test_human_review_for_epsilon_domain(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = """
def compute_signal():
    return 1.0
place_market_order("EURUSD", 0.1, "BUY")
result = compute_signal()
"""
        review = agent.review(code, code_id="epsilon_test")
        assert review.verdict == SafetyVerdict.NEEDS_HUMAN_REVIEW
        assert review.needs_human_signoff is True
        assert "place_market_order" in review.human_review_reason

    def test_human_approval(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = """
def compute():
    return 1.0
modify_stop_loss(12345, 1.0900)
result = compute()
"""
        review = agent.review(code, code_id="needs_review")
        assert review.verdict == SafetyVerdict.NEEDS_HUMAN_REVIEW

        approved = agent.approve_human_review(review)
        assert approved.verdict == SafetyVerdict.APPROVED
        assert approved.reviewer == "human"

    def test_human_rejection(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = """
place_market_order("EURUSD", 0.1, "BUY")
result = 42
"""
        review = agent.review(code, code_id="reject_test")
        assert review.verdict == SafetyVerdict.NEEDS_HUMAN_REVIEW

        rejected = agent.reject_human_review(review, reason="Too risky")
        assert rejected.verdict == SafetyVerdict.REJECTED
        assert rejected.human_review_reason == "Too risky"

    def test_code_change_triggers_resandbox(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code1 = "result = 1"
        code2 = "result = 2"

        r1 = agent.review(code1, code_id="change_test")
        assert r1.verdict == SafetyVerdict.APPROVED

        # Same code — no re-sandbox (cached)
        r1b = agent.review(code1, code_id="change_test")
        assert r1b.verdict == SafetyVerdict.APPROVED

        # Changed code — should be re-reviewed
        r2 = agent.review(code2, code_id="change_test")
        assert r2.verdict == SafetyVerdict.APPROVED
        assert r2.code_hash != r1.code_hash

    def test_batch_review(self, tmp_path):
        agent = self._make_agent(tmp_path)
        codes = {
            "alpha_1": "result = 1",
            "alpha_2": "result = 2",
            "alpha_3": "import subprocess; result = 3",
        }
        results = agent.review_batch(codes)
        assert len(results) == 3
        assert results["alpha_1"].verdict == SafetyVerdict.APPROVED
        assert results["alpha_2"].verdict == SafetyVerdict.APPROVED
        assert results["alpha_3"].verdict == SafetyVerdict.REJECTED

    def test_pending_human_reviews(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.review("result = 1", code_id="ok")
        agent.review("close_position(123)\nresult = 42", code_id="needs_review")

        pending = agent.get_pending_human_reviews()
        assert len(pending) == 1
        assert pending[0].needs_human_signoff is True

    def test_summary(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.review("result = 1", code_id="s1")
        agent.review("import subprocess; result = 2", code_id="s2")
        agent.review("close_position(123)\nresult = 3", code_id="s3")

        summary = agent.get_summary()
        assert summary["total"] == 3
        assert summary["approved"] >= 1
        assert summary["rejected"] >= 1
        assert summary["pending_human"] >= 1

    def test_format_summary(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.review("result = 42", code_id="fmt_test")
        output = agent.format_summary()
        assert "ζ CODE SAFETY AGENT" in output
        assert "Total reviews:" in output

    def test_audit_logging(self, tmp_path):
        mock_audit = MagicMock()
        agent = self._make_agent(tmp_path)
        agent._audit = mock_audit

        agent.review("result = 42", code_id="audit_test")
        mock_audit.log.assert_called_once()

    def test_audit_logging_epsilon(self, tmp_path):
        mock_audit = MagicMock()
        agent = self._make_agent(tmp_path)
        agent._audit = mock_audit

        agent.review("place_market_order('EURUSD', 0.1, 'BUY')\nresult = 1", code_id="audit_eps")
        # Should log GATE_FAILED since it needs human review
        assert mock_audit.log.call_count == 1

    def test_syntax_error_rejected(self, tmp_path):
        agent = self._make_agent(tmp_path)
        code = "def broken("
        review = agent.review(code, code_id="syntax_err")
        assert review.verdict == SafetyVerdict.REJECTED
        assert review.static.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Integration / Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_code(self):
        r = static_analyze("")
        assert r.passed is True  # empty code is safe
        assert r.safety_score == 1.0

    def test_whitespace_only(self):
        r = static_analyze("   \n\n   ")
        assert r.passed is True

    def test_single_comment(self):
        r = static_analyze("# just a comment")
        assert r.passed is True

    def test_many_forbidden_imports_cap_penalty(self):
        code = """
import subprocess
import requests
import socket
import shutil
import ctypes
"""
        r = static_analyze(code)
        # Penalty is capped at 3 imports
        assert r.safety_score >= 0.0
        assert len(r.forbidden_imports) == 5

    def test_epsilon_only_needs_review_not_reject(self):
        """ε-domain code gets human review, not rejection."""
        code = """
result = modify_stop_loss(123, 1.1000)
"""
        r = static_analyze(code)
        # Score should be fine — it's the ε-domain that needs review
        assert r.passed is True
        assert r.safety_score >= MIN_SAFETY_SCORE
        assert len(r.epsilon_domain_hits) > 0

    def test_mixed_forbidden_and_epsilon(self):
        """Both forbidden imports AND ε-domain → rejected (forbidden takes precedence)."""
        code = """
import subprocess
place_market_order("EURUSD", 0.1, "BUY")
result = 42
"""
        r = static_analyze(code)
        assert r.passed is False  # forbidden import → reject
        assert len(r.epsilon_domain_hits) > 0  # also has ε hits
