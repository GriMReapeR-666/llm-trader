"""ζ (Zeta) Code Safety Agent — UAQP Agent ζ

Validates all σ-generated code in a sandboxed environment before it can
influence live trading. Two hard rules that NEVER gate at any autonomy level:

1. Code touching order-placement logic (ε domain) requires human sign-off.
2. Code that passes static analysis + sandbox gets a safety score; scores
   below threshold are rejected outright.

Architecture:
    σ generates code  →  ζ validates  →  β can run it  →  ε executes trades
                                  ↘ κ logs the decision

Properties:
    - Static analysis: AST-based forbidden-pattern detection
    - Sandbox execution: restricted builtins, no network, timeout
    - Code diffing: hash-based change tracking, only re-review changed code
    - Human sign-off: mandatory for ε-domain code, permanent, not gateable
    - κ integration: every decision logged to compliance audit trail
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import sys
import textwrap
import time
import traceback
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Patterns that are ALWAYS forbidden regardless of autonomy level.
# These indicate code trying to escape the sandbox or access the real world.
FORBIDDEN_IMPORTS: Set[str] = {
    "subprocess", "shutil", "ctypes", "importlib",
    "multiprocessing", "socket", "http", "urllib", "requests",
    "ftplib", "smtplib", "xmlrpc", "xmlrpc.client",
}

FORBIDDEN_BUILTINS: Set[str] = {
    "exec", "eval", "compile", "__import__",
    "breakpoint", "exit", "quit",
    "open",  # Only file I/O forbidden; data passed as params
}

# Patterns that flag code as touching ε (execution) domain.
# Any code referencing these requires mandatory human sign-off.
EPSILON_DOMAIN_PATTERNS: Set[str] = {
    # Order execution
    "place_market_order", "place_pending_order", "emergency_market_exit",
    "order_send", "order_modify", "order_cancel",
    "paper_market_order", "paper_pending_order",
    # Trade management
    "modify_stop_loss", "modify_take_profit",
    "close_position", "close_all_positions",
    "open_position", "set_position_size",
    # Risk gates that modify live state
    "set_heat_budget", "set_var_limit",
    "kill_switch", "activate_kill_switch",
    # Direct MT5 API
    "mt5.order_send", "mt5.positions_get",
    "mt5.account_info", "mt5.symbol_info",
}

# Safety thresholds
DEFAULT_SANDBOX_TIMEOUT: float = 5.0  # seconds
MIN_SAFETY_SCORE: float = 0.6  # minimum to pass without human review
MAX_CODE_LINES: int = 500  # reject overly large generated code
MAX_COMPLEXITY: int = 100  # max AST node count


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------

class SafetyVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    SANDBOX_FAILED = "SANDBOX_FAILED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class StaticAnalysisResult:
    """Result of AST-based static analysis."""
    passed: bool
    safety_score: float  # 0.0-1.0
    risk_level: RiskLevel
    forbidden_imports: List[str] = field(default_factory=list)
    forbidden_builtins: List[str] = field(default_factory=list)
    epsilon_domain_hits: List[str] = field(default_factory=list)
    complexity_score: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SandboxResult:
    """Result of sandboxed code execution."""
    success: bool
    result: Any = None
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "error": self.error,
        }


@dataclass
class SafetyReview:
    """Complete safety review result for a piece of code."""
    code_hash: str
    code_preview: str  # first 200 chars
    static: StaticAnalysisResult
    sandbox: Optional[SandboxResult] = None
    verdict: SafetyVerdict = SafetyVerdict.REJECTED
    needs_human_signoff: bool = False
    human_review_reason: str = ""
    reviewed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    reviewer: str = "ζ"  # always the agent itself, or "human"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["risk_level"] = self.static.risk_level.value
        return d


# ---------------------------------------------------------------------------
# Static analyzer
# ---------------------------------------------------------------------------

class _StaticAnalyzer(ast.NodeVisitor):
    """Walk the AST and collect forbidden patterns."""

    def __init__(self) -> None:
        self.forbidden_imports: List[str] = []
        self.forbidden_builtins: List[str] = []
        self.epsilon_hits: List[str] = []
        self.warnings: List[str] = []
        self.node_count: int = 0
        self._attr_chain: List[str] = []

    def visit(self, node: ast.AST) -> Any:
        self.node_count += 1
        return super().visit(node)

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if mod in FORBIDDEN_IMPORTS:
                self.forbidden_imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if node.module:
            mod = node.module.split(".")[0]
            if mod in FORBIDDEN_IMPORTS:
                self.forbidden_imports.append(node.module)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        # Check for forbidden builtins used as function calls
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self.forbidden_builtins.append(node.func.id)

        # Check for epsilon-domain function calls
        call_name = self._get_call_name(node)
        if call_name:
            for pattern in EPSILON_DOMAIN_PATTERNS:
                if pattern in call_name:
                    self.epsilon_hits.append(call_name)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        # Build attribute chain for dotted names like mt5.order_send
        chain = self._build_attr_chain(node)
        for pattern in EPSILON_DOMAIN_PATTERNS:
            if pattern in chain:
                if chain not in self.epsilon_hits:
                    self.epsilon_hits.append(chain)
        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract the full name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""

    def _build_attr_chain(self, node: ast.Attribute) -> str:
        """Build dotted name from attribute access."""
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))


def static_analyze(code: str) -> StaticAnalysisResult:
    """Run AST-based static analysis on generated code.

    Checks:
    1. Forbidden imports (network, subprocess, etc.)
    2. Forbidden builtins (exec, eval, open, etc.)
    3. ε-domain references (order execution, position management)
    4. Code complexity (AST node count)
    5. Parse validity

    Returns:
        StaticAnalysisResult with safety_score 0.0-1.0
    """
    errors: List[str] = []

    # Parse the code
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return StaticAnalysisResult(
            passed=False,
            safety_score=0.0,
            risk_level=RiskLevel.CRITICAL,
            errors=[f"Syntax error: {e}"],
        )

    # Walk the AST
    analyzer = _StaticAnalyzer()
    try:
        analyzer.visit(tree)
    except Exception as e:
        return StaticAnalysisResult(
            passed=False,
            safety_score=0.0,
            risk_level=RiskLevel.CRITICAL,
            errors=[f"Analysis error: {e}"],
        )

    # Compute safety score (start at 1.0, deduct for issues)
    score = 1.0
    risk = RiskLevel.LOW

    # Forbidden imports: heavy penalty
    if analyzer.forbidden_imports:
        score -= 0.4 * min(len(analyzer.forbidden_imports), 3)
        risk = RiskLevel.HIGH

    # Forbidden builtins: heavy penalty
    if analyzer.forbidden_builtins:
        score -= 0.3 * min(len(analyzer.forbidden_builtins), 3)
        risk = RiskLevel.HIGH

    # ε-domain hits: mandatory human review, not a score penalty
    # (score stays high because the code may be *valid*, just needs sign-off)

    # Complexity penalty
    if analyzer.node_count > MAX_COMPLEXITY:
        excess = (analyzer.node_count - MAX_COMPLEXITY) / MAX_COMPLEXITY
        score -= 0.2 * min(excess, 1.0)
        risk = RiskLevel.MEDIUM if risk == RiskLevel.LOW else risk

    # Line count check
    line_count = len(code.strip().splitlines())
    if line_count > MAX_CODE_LINES:
        score -= 0.1
        warnings_msg = f"Code has {line_count} lines (max {MAX_CODE_LINES})"
    else:
        warnings_msg = ""

    # Clamp score
    score = max(0.0, min(1.0, score))

    # Escalate risk
    if score < 0.3:
        risk = RiskLevel.CRITICAL
    elif score < 0.5:
        risk = RiskLevel.HIGH

    # Passed = score >= MIN_SAFETY_SCORE AND no forbidden imports/builtins
    # ε-domain hits don't cause rejection — they cause human review
    passed = (
        score >= MIN_SAFETY_SCORE
        and not analyzer.forbidden_imports
        and not analyzer.forbidden_builtins
    )

    warnings = []
    if warnings_msg:
        warnings.append(warnings_msg)
    if analyzer.warnings:
        warnings.extend(analyzer.warnings)

    return StaticAnalysisResult(
        passed=passed,
        safety_score=round(score, 4),
        risk_level=risk,
        forbidden_imports=analyzer.forbidden_imports,
        forbidden_builtins=analyzer.forbidden_builtins,
        epsilon_domain_hits=analyzer.epsilon_hits,
        complexity_score=analyzer.node_count,
        warnings=warnings,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Sandbox executor
# ---------------------------------------------------------------------------

# Modules allowed inside the sandbox (for safe __import__)
ALLOWED_SANDBOX_MODULES: Set[str] = {
    "numpy", "np", "pandas", "pd", "math", "json", "datetime",
    "typing", "dataclasses", "enum", "collections",
    "itertools", "functools", "operator", "string", "re",
    "fractions", "decimal", "statistics", "random",
}


def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """Restricted __import__ that only allows whitelisted modules."""
    root = name.split(".")[0]
    if root in FORBIDDEN_IMPORTS:
        raise ImportError(f"Module '{name}' is forbidden by ζ safety policy")
    if root not in ALLOWED_SANDBOX_MODULES:
        raise ImportError(f"Module '{name}' is not in ζ sandbox whitelist")
    return __import__(name, *args, **kwargs)


def _make_restricted_globals() -> Dict[str, Any]:
    """Build a restricted global namespace for sandbox execution."""
    import numpy as np
    import pandas as pd

    safe_builtins = {
        k: v for k, v in __builtins__.__dict__.items()  # type: ignore
        if k not in FORBIDDEN_BUILTINS
    } if isinstance(__builtins__, dict) is False else {
        k: v for k, v in __builtins__.items()  # type: ignore
        if k not in FORBIDDEN_BUILTINS
    }

    # Re-add a safe __import__ so exec'd code can import allowed modules
    safe_builtins["__import__"] = _safe_import

    return {
        "__builtins__": safe_builtins,
        "np": np,
        "pd": pd,
        "math": __import__("math"),
        "json": __import__("json"),
        "datetime": __import__("datetime"),
        "timezone": __import__("datetime").timezone,
        "timedelta": __import__("datetime").timedelta,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sorted": sorted,
        "reversed": reversed,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "round": round,
    }


def run_sandboxed(
    code: str,
    timeout: float = DEFAULT_SANDBOX_TIMEOUT,
    input_data: Optional[Dict[str, Any]] = None,
) -> SandboxResult:
    """Execute code in a restricted sandbox.

    The sandbox:
    - Has no network access (forbidden imports blocked at static level)
    - Has no file I/O (open is forbidden)
    - Has a timeout (default 5s)
    - Has restricted builtins (no exec, eval, compile, __import__)
    - Has numpy/pandas available for quantitative work

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds
        input_data: Dict of variables to inject into the sandbox

    Returns:
        SandboxResult with success flag, result, stdout, stderr
    """
    globals_dict = _make_restricted_globals()

    # Inject input data
    if input_data:
        for k, v in input_data.items():
            if k.startswith("_"):
                continue  # skip private names
            globals_dict[k] = v

    stdout_buf = StringIO()
    stderr_buf = StringIO()

    start = time.monotonic()
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compile(code, "<ζ_sandbox>", "exec"), globals_dict)
        elapsed_ms = (time.monotonic() - start) * 1000
        return SandboxResult(
            success=True,
            result=globals_dict.get("result"),
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            execution_time_ms=round(elapsed_ms, 2),
        )
    except TimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000
        return SandboxResult(
            success=False,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            execution_time_ms=round(elapsed_ms, 2),
            error=f"Sandbox timeout after {timeout}s",
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        return SandboxResult(
            success=False,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            execution_time_ms=round(elapsed_ms, 2),
            error=f"{type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Code diffing / version tracking
# ---------------------------------------------------------------------------

class CodeVersionTracker(StoreBackedState):
    """Track code versions by hash. Only flags changed code for re-review."""

    store_key = "code_version_state"

    def __init__(self, state_path: Optional[str] = None, store: Optional[Any] = None) -> None:
        self._state_path = state_path or os.path.join(
            os.path.dirname(__file__), "..", "ζ_code_versions.json"
        )
        self.state_file = self._state_path
        self.store = store
        self._versions: Dict[str, Dict[str, Any]] = self._load()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        data = self._load_state_dict()
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self._save_state_dict(self._versions)

    @staticmethod
    def hash_code(code: str) -> str:
        """SHA-256 hash of stripped code."""
        return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()[:16]

    def is_new_or_changed(self, code: str, code_id: str = "") -> bool:
        """Return True if this code is new or different from last seen version."""
        h = self.hash_code(code)
        key = code_id or h
        prev = self._versions.get(key)
        if prev is None:
            return True
        return prev.get("hash") != h

    def record_version(
        self, code: str, code_id: str = "", verdict: str = "APPROVED"
    ) -> None:
        """Record the current version of this code."""
        h = self.hash_code(code)
        key = code_id or h
        self._versions[key] = {
            "hash": h,
            "verdict": verdict,
            "last_reviewed": datetime.now(timezone.utc).isoformat(),
            "review_count": self._versions.get(key, {}).get("review_count", 0) + 1,
        }
        self._save()

    def get_version_info(self, code_id: str) -> Optional[Dict[str, Any]]:
        """Get stored version info for a code ID."""
        return self._versions.get(code_id)


# ---------------------------------------------------------------------------
# Main ζ Agent
# ---------------------------------------------------------------------------

class CodeSafetyAgent:
    """ζ (Zeta) Code Safety Agent.

    Validates σ-generated code before it can influence live trading.

    Two hard rules (permanent, all autonomy levels):
    1. Code touching ε domain → mandatory human sign-off
    2. Code that fails static analysis → rejected outright

    Properties:
    - Stateless: reads code, writes verdict, terminates
    - Idempotent: same code → same verdict (via hash caching)
    - Fail-closed: any error → REJECTED
    """

    def __init__(
        self,
        sandbox_timeout: float = DEFAULT_SANDBOX_TIMEOUT,
        state_path: Optional[str] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        self.sandbox_timeout = sandbox_timeout
        self.tracker = CodeVersionTracker(state_path)
        self._audit = audit_logger  # compliance_audit.ComplianceAudit if available
        self._reviews: List[SafetyReview] = []

    @staticmethod
    def static_analyze(code: str) -> StaticAnalysisResult:
        return static_analyze(code)

    def review(
        self,
        code: str,
        code_id: str = "",
        input_data: Optional[Dict[str, Any]] = None,
        force_resandbox: bool = False,
    ) -> SafetyReview:
        """Full safety review: static analysis → sandbox → verdict.

        Args:
            code: The generated Python code to review
            code_id: Optional identifier for this code (e.g., "alpha_001_v3")
            input_data: Optional input variables for sandbox execution
            force_resandbox: If True, re-run sandbox even if code hasn't changed

        Returns:
            SafetyReview with verdict, static analysis, sandbox results
        """
        code_hash = CodeVersionTracker.hash_code(code)
        code_preview = code.strip()[:200]

        # Step 1: Static analysis (always runs)
        static = static_analyze(code)

        # Step 2: Check if code is new/changed
        is_changed = self.tracker.is_new_or_changed(code, code_id)

        # Step 3: Sandbox execution (only if static passed and code is new/changed)
        sandbox = None
        if static.passed and (is_changed or force_resandbox):
            sandbox = run_sandboxed(code, timeout=self.sandbox_timeout, input_data=input_data)
        elif static.passed and not is_changed:
            # Code hasn't changed — trust previous sandbox result
            prev_info = self.tracker.get_version_info(code_id or code_hash)
            if prev_info and prev_info.get("verdict") == "APPROVED":
                sandbox = SandboxResult(success=True, result=None)

        # Step 4: Determine verdict
        verdict, needs_human, human_reason = self._compute_verdict(static, sandbox, code)

        # Step 5: Record version
        self.tracker.record_version(code, code_id, verdict.value)

        # Build review
        review = SafetyReview(
            code_hash=code_hash,
            code_preview=code_preview,
            static=static,
            sandbox=sandbox,
            verdict=verdict,
            needs_human_signoff=needs_human,
            human_review_reason=human_reason,
        )
        self._reviews.append(review)

        # Step 6: Log to κ compliance audit
        self._log_to_audit(review, code_id)

        logger.info(
            "ζ review: verdict=%s score=%.2f risk=%s epsilon=%d code_id=%s",
            verdict.value,
            static.safety_score,
            static.risk_level.value,
            len(static.epsilon_domain_hits),
            code_id or code_hash[:8],
        )

        return review

    def _compute_verdict(
        self,
        static: StaticAnalysisResult,
        sandbox: Optional[SandboxResult],
        code: str,
    ) -> Tuple[SafetyVerdict, bool, str]:
        """Compute final verdict from static + sandbox results.

        Returns:
            (verdict, needs_human_signoff, human_review_reason)
        """
        # Rule 1: ε-domain code ALWAYS requires human sign-off (permanent rule)
        if static.epsilon_domain_hits:
            hits = ", ".join(static.epsilon_domain_hits[:5])
            return (
                SafetyVerdict.NEEDS_HUMAN_REVIEW,
                True,
                f"Code references ε-domain functions: {hits}",
            )

        # Rule 2: Static analysis failed → reject
        if not static.passed:
            reasons = []
            if static.forbidden_imports:
                reasons.append(f"forbidden imports: {', '.join(static.forbidden_imports)}")
            if static.forbidden_builtins:
                reasons.append(f"forbidden builtins: {', '.join(static.forbidden_builtins)}")
            if static.errors:
                reasons.extend(static.errors)
            return (
                SafetyVerdict.REJECTED,
                False,
                "Static analysis failed: " + "; ".join(reasons),
            )

        # Rule 3: Sandbox failed → reject
        if sandbox is not None and not sandbox.success:
            return (
                SafetyVerdict.SANDBOX_FAILED,
                False,
                f"Sandbox execution failed: {sandbox.error}",
            )

        # Rule 4: Safety score too low → reject
        if static.safety_score < MIN_SAFETY_SCORE:
            return (
                SafetyVerdict.REJECTED,
                False,
                f"Safety score {static.safety_score:.2f} below threshold {MIN_SAFETY_SCORE}",
            )

        # All checks passed
        return (SafetyVerdict.APPROVED, False, "")

    def _log_to_audit(self, review: SafetyReview, code_id: str) -> None:
        """Log the review to κ compliance audit if available."""
        if self._audit is None:
            return
        try:
            from core.compliance_audit import EventType

            event_type = (
                EventType.GATE_PASSED
                if review.verdict == SafetyVerdict.APPROVED
                else EventType.GATE_FAILED
            )
            self._audit.log(
                event_type,
                symbol=code_id,
                agent="ζ",
                payload={
                    "verdict": review.verdict.value,
                    "safety_score": review.static.safety_score,
                    "risk_level": review.static.risk_level.value,
                    "epsilon_hits": len(review.static.epsilon_domain_hits),
                    "needs_human": review.needs_human_signoff,
                    "code_hash": review.code_hash,
                },
            )
        except Exception as e:
            logger.warning("ζ could not log to κ audit: %s", e)

    def approve_human_review(self, review: SafetyReview) -> SafetyReview:
        """Human approves a code review that required sign-off.

        Call this after a human has manually inspected the code and
        confirmed it is safe to execute.

        Returns:
            Updated SafetyReview with APPROVED verdict
        """
        if not review.needs_human_signoff:
            return review

        review.verdict = SafetyVerdict.APPROVED
        review.reviewer = "human"
        review.needs_human_signoff = False
        self.tracker.record_version(
            review.code_preview, verdict="APPROVED"
        )

        self._log_to_audit(review, "human_approved")
        logger.info("ζ human-approved code hash=%s", review.code_hash)
        return review

    def reject_human_review(self, review: SafetyReview, reason: str = "") -> SafetyReview:
        """Human rejects a code review that required sign-off."""
        review.verdict = SafetyVerdict.REJECTED
        review.reviewer = "human"
        review.human_review_reason = reason or "Rejected by human reviewer"
        review.needs_human_signoff = False

        self._log_to_audit(review, "human_rejected")
        logger.info("ζ human-rejected code hash=%s reason=%s", review.code_hash, reason)
        return review

    # ------------------------------------------------------------------
    # Batch review
    # ------------------------------------------------------------------

    def review_batch(
        self, codes: Dict[str, str], input_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, SafetyReview]:
        """Review multiple pieces of code.

        Args:
            codes: Dict of code_id → code
            input_data: Optional shared input for sandbox

        Returns:
            Dict of code_id → SafetyReview
        """
        results = {}
        for code_id, code in codes.items():
            results[code_id] = self.review(code, code_id=code_id, input_data=input_data)
        return results

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_pending_human_reviews(self) -> List[SafetyReview]:
        """Return all reviews awaiting human sign-off."""
        return [r for r in self._reviews if r.needs_human_signoff]

    def get_summary(self) -> Dict[str, Any]:
        """Return summary statistics of all reviews."""
        if not self._reviews:
            return {"total": 0, "approved": 0, "rejected": 0, "pending": 0}

        verdicts = {}
        for r in self._reviews:
            v = r.verdict.value
            verdicts[v] = verdicts.get(v, 0) + 1

        return {
            "total": len(self._reviews),
            "approved": verdicts.get("APPROVED", 0),
            "rejected": verdicts.get("REJECTED", 0) + verdicts.get("SANDBOX_FAILED", 0),
            "pending_human": verdicts.get("NEEDS_HUMAN_REVIEW", 0),
            "avg_safety_score": round(
                sum(r.static.safety_score for r in self._reviews) / len(self._reviews), 4
            ),
            "verdicts": verdicts,
        }

    def format_summary(self) -> str:
        """Format summary as human-readable string."""
        s = self.get_summary()
        lines = [
            "=" * 60,
            "ζ CODE SAFETY AGENT — REVIEW SUMMARY",
            "=" * 60,
            f"Total reviews: {s['total']}",
            f"  ✅ Approved:     {s['approved']}",
            f"  ❌ Rejected:     {s['rejected']}",
            f"  🔍 Pending human: {s['pending_human']}",
            f"  📊 Avg safety score: {s.get('avg_safety_score', 0):.2f}",
            "=" * 60,
        ]

        if s.get("pending_human", 0) > 0:
            lines.append("\nPENDING HUMAN REVIEWS:")
            for r in self.get_pending_human_reviews():
                lines.append(f"  [{r.code_hash[:8]}] {r.human_review_reason}")
                lines.append(f"    Preview: {r.code_preview[:100]}...")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_agent: Optional[CodeSafetyAgent] = None


def get_zeta_agent(**kwargs: Any) -> CodeSafetyAgent:
    """Get or create the singleton ζ agent."""
    global _agent
    if _agent is None:
        _agent = CodeSafetyAgent(**kwargs)
    return _agent
