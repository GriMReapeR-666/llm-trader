"""Autonomous Alpha Discovery Engine — FactorMiner Ralph Loop + AlphaAgent Regularization.

Implements the self-evolving alpha discovery pipeline:
  1. RETRIEVE: Load experience memory of past successful/failed alphas
  2. GENERATE: LLM proposes new alpha expressions using safe DSL
  3. EVALUATE: Sealed sandbox computes IC/IC_IR with strict temporal isolation
  4. DISTILL: Store validated alphas, update experience memory

Safety constraints (AlphaAgent regularization):
  - Originality enforcement: AST-based similarity prevents near-duplicates
  - Complexity control: max expression depth limits overfitting
  - Sealed sandbox: evaluator cannot be modified by the discovery agent

References:
  - FactorMiner (2026): Self-evolving agent with experience memory
  - AlphaAgent (2025): Decay-resistant alpha mining with regularization
  - AQuA (2026): Sealed sandbox principle
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import re
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from core.state_persistence import StoreBackedState
except ImportError:
    from state_persistence import StoreBackedState

import numpy as np
import pandas as pd

logger = logging.getLogger("AlphaDiscovery")

# Optional ζ Code Safety Agent import
try:
    from core.code_safety_agent import get_zeta_agent, SafetyVerdict
except ImportError:
    get_zeta_agent = None
    SafetyVerdict = None

# ---------------------------------------------------------------------------
# Safe Alpha Expression DSL
# ---------------------------------------------------------------------------
# Allowed operations — no arbitrary code execution
_ALLOWED_NAMES = {
    "ts_rank", "ts_argmax", "ts_argmin", "ts_min", "ts_max",
    "delta", "delay", "rank", "scale", "decay_linear",
    "signed_power", "log_safe",
    "abs", "sign", "mean", "std", "sqrt", "log", "exp",
    "rolling_mean", "rolling_std", "rolling_corr", "rolling_max", "rolling_min",
}

_ALLOWED_COLUMNS = {"open", "high", "low", "close", "volume", "returns"}


class SafeAlphaEvaluator:
    """Evaluates alpha expressions in a sandboxed environment.

    Only allows whitelisted functions and column references.
    No import, no assignment, no loops, no function definitions.
    """

    def __init__(self, max_complexity: int = 30):
        self.max_complexity = max_complexity

    def _check_ast_safety(self, expr: str) -> Tuple[bool, str]:
        """Validate expression安全性 via AST analysis."""
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

        for node in ast.walk(tree):
            # Block dangerous constructs
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "Import statements not allowed"
            if isinstance(node, ast.FunctionDef):
                return False, "Function definitions not allowed"
            if isinstance(node, ast.ClassDef):
                return False, "Class definitions not allowed"
            if isinstance(node, (ast.While, ast.For)):
                return False, "Loops not allowed"
            if isinstance(node, ast.ListComp):
                return False, "List comprehensions not allowed"
            if isinstance(node, ast.Attribute):
                # Only allow method calls on allowed objects
                if isinstance(node.value, ast.Name):
                    if node.value.id not in ("df", "np", "pd"):
                        return False, f"Attribute access on '{node.value.id}' not allowed"
            if isinstance(node, ast.Name) and node.id.startswith("_"):
                return False, "Private names not allowed"

        return True, "OK"

    def _compute_complexity(self, expr: str) -> int:
        """Estimate expression complexity (tree depth)."""
        try:
            tree = ast.parse(expr, mode="eval")
            return sum(1 for _ in ast.walk(tree))
        except SyntaxError:
            return 999

    def evaluate(self, expression: str, df: pd.DataFrame) -> Optional[pd.Series]:
        """Safely evaluate an alpha expression on OHLCV data.

        Returns pd.Series of alpha values, or None if evaluation fails.
        """
        # Safety check
        safe, msg = self._check_ast_safety(expression)
        if not safe:
            logger.debug("Expression rejected: %s", msg)
            return None

        # Complexity check
        if self._compute_complexity(expression) > self.max_complexity:
            logger.debug("Expression too complex: depth=%d", self._compute_complexity(expression))
            return None

        # Causality guard: rank() is a full-sample percentile normalizer — on
        # single-symbol data it uses future information (look-ahead bias) and
        # would inflate in-sample IC. Reject it explicitly.
        if re.search(r"\brank\s*\(", expression):
            logger.debug("Expression rejected: non-causal primitive 'rank' (look-ahead)")
            return None

        # Build safe namespace
        safe_ns = self._build_namespace(df)

        try:
            result = eval(expression, {"__builtins__": {}}, safe_ns)
            if isinstance(result, pd.Series):
                # Validate output
                if result.isna().all():
                    return None
                if not np.isfinite(result.dropna()).all():
                    result = result.replace([np.inf, -np.inf], np.nan)
                return result
            return None
        except Exception as e:
            logger.debug("Expression evaluation failed: %s", e)
            return None

    def _build_namespace(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Build safe evaluation namespace with primitives + columns."""
        ns = {}
        # Add primitives
        from core.alpha_factors import (
            ts_rank, ts_argmax, ts_argmin, ts_min, ts_max,
            delta, delay, rank, scale, decay_linear,
            signed_power, log_safe,
        )
        ns["ts_rank"] = ts_rank
        ns["ts_argmax"] = ts_argmax
        ns["ts_argmin"] = ts_argmin
        ns["ts_min"] = ts_min
        ns["ts_max"] = ts_max
        ns["delta"] = delta
        ns["delay"] = delay
        ns["rank"] = rank
        ns["scale"] = scale
        ns["decay_linear"] = decay_linear
        ns["signed_power"] = signed_power
        ns["log_safe"] = log_safe

        # Add numpy/pandas safe functions
        ns["abs"] = np.abs
        ns["sign"] = np.sign
        ns["sqrt"] = np.sqrt
        ns["log"] = np.log
        ns["exp"] = np.exp
        ns["mean"] = pd.Series.mean
        ns["std"] = pd.Series.std

        # Add columns
        for col in _ALLOWED_COLUMNS:
            if col in df.columns:
                ns[col] = df[col]
            elif col == "returns" and "close" in df.columns:
                ns["returns"] = df["close"].pct_change()

        return ns


# ---------------------------------------------------------------------------
# Originality Enforcement (AlphaAgent AST Similarity)
# ---------------------------------------------------------------------------

def _normalize_ast(expr: str) -> str:
    """Normalize expression to canonical AST form for similarity comparison."""
    try:
        tree = ast.parse(expr, mode="eval")
        return ast.dump(tree)
    except SyntaxError:
        return expr


def _ast_similarity(expr_a: str, expr_b: str) -> float:
    """Compute structural similarity between two alpha expressions.

    Returns 0.0 (identical) to 1.0 (completely different).
    """
    dump_a = _normalize_ast(expr_a)
    dump_b = _normalize_ast(expr_b)
    if dump_a == dump_b:
        return 0.0
    # Simple token-based Jaccard similarity
    tokens_a = set(dump_a.split())
    tokens_b = set(dump_b.split())
    if not tokens_a or not tokens_b:
        return 1.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return 1.0 - len(intersection) / len(union)


def check_originality(
    new_expr: str,
    existing_expressions: List[str],
    similarity_threshold: float = 0.70,
) -> Tuple[bool, float]:
    """Check if a new alpha expression is sufficiently original.

    Args:
        new_expr: The proposed alpha expression.
        existing_expressions: List of already-validated alpha expressions.
        similarity_threshold: Max allowed similarity (0-1). Below this = original.

    Returns:
        (is_original, max_similarity) — True if original enough.
    """
    if not existing_expressions:
        return True, 0.0

    max_sim = 0.0
    for existing in existing_expressions:
        sim = _ast_similarity(new_expr, existing)
        max_sim = max(max_sim, sim)

    # similarity = 0 means identical, 1 means completely different
    # is_original when max_sim is LOW (very similar to existing = NOT original)
    is_original = max_sim > similarity_threshold
    return is_original, max_sim


# ---------------------------------------------------------------------------
# Sealed Sandbox Evaluation (AQuA Principle)
# ---------------------------------------------------------------------------

@dataclass
class AlphaCandidate:
    """A candidate alpha factor proposed by the discovery engine."""
    expression: str
    name: str
    category: str  # momentum, reversal, mean_reversion, volume_price, composite
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    turnover: float = 0.0
    complexity: int = 0
    similarity_to_existing: float = 0.0
    status: str = "PENDING"  # PENDING, VALIDATED, REJECTED
    rejection_reason: str = ""
    discovered_at: float = 0.0
    hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SealedSandboxEvaluator:
    """Evaluates alpha candidates in a sealed sandbox (AQuA principle).

    The evaluator is FIXED — the discovery agent cannot modify it.
    This prevents "cheating" the evaluation metrics.
    """

    def __init__(
        self,
        min_ic_ir: float = 0.15,
        min_ic_mean: float = 0.02,
        max_turnover: float = 0.5,
        min_sharpe: float = 0.3,
        embargo_pct: float = 0.02,
    ):
        self.min_ic_ir = min_ic_ir
        self.min_ic_mean = min_ic_mean
        self.max_turnover = max_turnover
        self.min_sharpe = min_sharpe
        self.embargo_pct = embargo_pct
        self.evaluator = SafeAlphaEvaluator()

    def evaluate_candidate(
        self,
        candidate: AlphaCandidate,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
    ) -> AlphaCandidate:
        """Evaluate a candidate alpha in the sealed sandbox.

        Uses temporal isolation: train_df for IC computation,
        test_df for out-of-sample validation.
        """
        # Evaluate expression on training data
        alpha_series = self.evaluator.evaluate(candidate.expression, train_df)
        if alpha_series is None:
            candidate.status = "REJECTED"
            candidate.rejection_reason = "Expression evaluation failed"
            return candidate

        # Compute forward returns
        if "close" in train_df.columns:
            fwd_ret = train_df["close"].pct_change().shift(-1)
        else:
            candidate.status = "REJECTED"
            candidate.rejection_reason = "No close prices"
            return candidate

        # Compute IC on training set
        valid_mask = alpha_series.notna() & fwd_ret.notna()
        if valid_mask.sum() < 30:
            candidate.status = "REJECTED"
            candidate.rejection_reason = f"Insufficient valid data: {valid_mask.sum()}"
            return candidate

        from scipy import stats as sp_stats
        ic_values = []
        window = 20
        alpha_vals = alpha_series[valid_mask].values
        ret_vals = fwd_ret[valid_mask].values

        for i in range(window, len(alpha_vals)):
            a = alpha_vals[i - window:i]
            r = ret_vals[i - window:i]
            if np.std(a) > 0 and np.std(r) > 0:
                corr, _ = sp_stats.spearmanr(a, r)
                if np.isfinite(corr):
                    ic_values.append(corr)

        if len(ic_values) < 10:
            candidate.status = "REJECTED"
            candidate.rejection_reason = f"Too few IC values: {len(ic_values)}"
            return candidate

        candidate.ic_mean = float(np.mean(ic_values))
        candidate.ic_std = float(np.std(ic_values))
        candidate.ic_ir = candidate.ic_mean / max(candidate.ic_std, 1e-6)

        # Out-of-sample validation on test set
        test_alpha = self.evaluator.evaluate(candidate.expression, test_df)
        if test_alpha is not None and "close" in test_df.columns:
            test_fwd = test_df["close"].pct_change().shift(-1)
            test_valid = test_alpha.notna() & test_fwd.notna()
            if test_valid.sum() >= 20:
                test_ic_values = []
                t_alpha = test_alpha[test_valid].values
                t_ret = test_fwd[test_valid].values
                for i in range(window, len(t_alpha)):
                    a = t_alpha[i - window:i]
                    r = t_ret[i - window:i]
                    if np.std(a) > 0 and np.std(r) > 0:
                        corr, _ = sp_stats.spearmanr(a, r)
                        if np.isfinite(corr):
                            test_ic_values.append(corr)
                if test_ic_values:
                    test_ic_mean = np.mean(test_ic_values)
                    # Penalize if test IC is much lower than train IC (overfitting)
                    if test_ic_mean < candidate.ic_mean * 0.3:
                        candidate.status = "REJECTED"
                        candidate.rejection_reason = (
                            f"Overfitting: train IC={candidate.ic_mean:.4f}, "
                            f"test IC={test_ic_mean:.4f}"
                        )
                        return candidate

        # Turnover check
        if alpha_series.notna().sum() > 1:
            changes = alpha_series.diff().abs()
            candidate.turnover = float(changes.mean())

        # Apply thresholds
        if abs(candidate.ic_ir) < self.min_ic_ir:
            candidate.status = "REJECTED"
            candidate.rejection_reason = f"IC_IR too low: {candidate.ic_ir:.4f} < {self.min_ic_ir}"
            return candidate

        if abs(candidate.ic_mean) < self.min_ic_mean:
            candidate.status = "REJECTED"
            candidate.rejection_reason = f"IC mean too low: {candidate.ic_mean:.4f}"
            return candidate

        candidate.status = "VALIDATED"
        candidate.discovered_at = time.time()
        candidate.hash = hashlib.sha256(candidate.expression.encode()).hexdigest()[:16]
        return candidate


# ---------------------------------------------------------------------------
# Experience Memory (FactorMiner)
# ---------------------------------------------------------------------------

@dataclass
class ExperienceMemory:
    """Persistent memory of alpha discovery attempts.

    Stores both successful and failed alphas to guide future generation.
    """
    validated_alphas: List[Dict[str, Any]] = field(default_factory=list)
    rejected_alphas: List[Dict[str, Any]] = field(default_factory=list)
    generation_count: int = 0
    total_validated: int = 0
    total_rejected: int = 0
    # Observability: how the LAST cycle produced its candidates. Values:
    # "LLM" (router/adapter generation), "FALLBACK_STATIC" (5 static),
    # "EXPLICIT_FN" (caller-supplied llm_generate_fn), "unknown" (pre-
    # instrumentation state). Surfaced on the compliance dashboard.
    last_generation_source: str = "unknown"
    last_generation_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validated_count": len(self.validated_alphas),
            "rejected_count": len(self.rejected_alphas),
            "generation_count": self.generation_count,
            "total_validated": self.total_validated,
            "total_rejected": self.total_rejected,
            "last_generation_source": self.last_generation_source,
            "last_generation_at": self.last_generation_at,
            "validated_alphas": self.validated_alphas[-100:],
            "rejected_alphas": self.rejected_alphas[-100:],
            "validated_expressions": [a["expression"] for a in self.validated_alphas[-20:]],
            "validated_categories": [a.get("category", "unknown") for a in self.validated_alphas[-20:]],
            "validated_ic_irs": [a.get("ic_ir", 0) for a in self.validated_alphas[-20:]],
            "rejection_reasons": [a.get("rejection_reason", "") for a in self.rejected_alphas[-10:]],
        }


class AlphaExperienceMemory(StoreBackedState):
    """Persistent file-backed experience memory."""

    store_key = "alpha_discovery_state"

    def __init__(self, state_file: str = "alpha_discovery_state.json", store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self.memory = self._load()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> ExperienceMemory:
        try:
            data = self._load_state_dict()
            if data:
                mem = ExperienceMemory(
                    generation_count=data.get("generation_count", 0),
                    total_validated=data.get("total_validated", 0),
                    total_rejected=data.get("total_rejected", 0),
                )
                mem.validated_alphas = data.get("validated_alphas", [])
                mem.rejected_alphas = data.get("rejected_alphas", [])
                return mem
        except Exception as exc:
            logger.debug("memory operation failed: %s", exc)
        return ExperienceMemory()

    def save(self) -> None:
        try:
            self._save_state_dict(self.memory.to_dict())
        except Exception as e:
            logger.debug("Failed to save alpha discovery state: %s", e)

    def record_validated(self, candidate: AlphaCandidate) -> None:
        self.memory.validated_alphas.append(candidate.to_dict())
        self.memory.total_validated += 1
        if len(self.memory.validated_alphas) > 100:
            self.memory.validated_alphas = self.memory.validated_alphas[-100:]
        self.save()

    def record_rejected(self, candidate: AlphaCandidate) -> None:
        self.memory.rejected_alphas.append(candidate.to_dict())
        self.memory.total_rejected += 1
        # Keep only last 100 rejections
        if len(self.memory.rejected_alphas) > 100:
            self.memory.rejected_alphas = self.memory.rejected_alphas[-100:]
        self.save()

    def increment_generation(self) -> None:
        self.memory.generation_count += 1
        self.save()

    def get_existing_expressions(self) -> List[str]:
        return [a["expression"] for a in self.memory.validated_alphas]

    def get_context_for_llm(self) -> Dict[str, Any]:
        """Build context for LLM to generate new alphas."""
        return self.memory.to_dict()


# ---------------------------------------------------------------------------
# llm_router adapter: shared generation fn for every AlphaDiscoveryEngine
# ---------------------------------------------------------------------------

class _RouterLLMError(RuntimeError):
    """Raised when the llm_router cannot produce usable generation text.

    The router's ``generate()`` deliberately never raises — it returns
    ``"Error: ..."`` strings from provider retries and a deterministic-HOLD
    JSON payload when every provider fails. AlphaDiscovery's loop treats a
    raised exception as "fall back to static candidates this cycle", so the
    adapter translates every router failure mode into an exception instead
    of silently feeding unparseable text into the cycle.
    """


def _router_llm_generate(prompt: str) -> str:
    """Generate one FactorMiner prompt completion through ``llm_router``.

    Registered as a dedicated ``alpha_discovery`` router agent so provider
    selection is configured in one place (default openrouter with failover
    across every other key-carrying provider; both overridable via
    ``ALPHA_LLM_PROVIDER`` / ``ALPHA_LLM_MODEL`` envs). Using the shared
    router means OpenRouter, TokenRouter, RoutesMe, Cerebras and NVIDIA NIM
    keys all serve factor mining, with the router's rate limiting, key
    rotation and circuit breakers — instead of AlphaDiscovery falling back
    to 5 static heuristic expressions whenever its own hook is unwired.
    """
    try:
        from llm_router import get_router
    except Exception as exc:  # pragma: no cover - import env dependent
        raise _RouterLLMError(f"llm_router unavailable: {exc}") from exc

    router = get_router()
    if "alpha_discovery" not in getattr(router, "agents", {}):
        available = list(getattr(router, "providers", {}).keys())
        if not available:
            raise _RouterLLMError(
                "llm_router has no initialized providers — set OPENROUTER_API_KEY "
                "(or TOKENROUTER_KEY / ROUTESME_KEY / CEREBRAS_API_KEY / NVIDIA_KEY) in .env")
        primary = os.getenv("ALPHA_LLM_PROVIDER") or (
            "openrouter" if "openrouter" in available else available[0])
        if primary not in available:
            primary = available[0]
        router.register_agent(
            "alpha_discovery",
            provider=primary,
            model=os.getenv("ALPHA_LLM_MODEL", "meta-llama/llama-3.2-11b-vision-instruct:free"),
            fallback_providers=[p for p in available if p != primary],
            allow_failover=True,
        )

    response = router.generate(
        "alpha_discovery",
        [{"role": "user", "content": prompt}],
    )
    text = (response or "").strip()
    if text.startswith("{") and '"deterministic_fallback": true' in text:
        raise _RouterLLMError("llm_router returned deterministic HOLD (all providers failed)")
    if text.startswith("Error:") or not text:
        raise _RouterLLMError(f"llm_router generation failed: {text[:200] or 'empty response'}")
    return text


# ---------------------------------------------------------------------------
# Alpha Discovery Engine (Ralph Loop)
# ---------------------------------------------------------------------------

class AlphaDiscoveryEngine:
    """Autonomous alpha discovery using FactorMiner's Ralph Loop.

    Loop:
      1. RETRIEVE: Load experience memory
      2. GENERATE: Build LLM prompt with context → propose new alpha
      3. EVALUATE: Sealed sandbox IC/IC_IR validation
      4. DISTILL: Store validated alpha, update memory
    """

    def __init__(
        self,
        state_file: str = "alpha_discovery_state.json",
        min_ic_ir: float = 0.15,
        max_complexity: int = 30,
        similarity_threshold: float = 0.70,
        max_alphas_per_cycle: int = 3,
        store: Optional[Any] = None,
    ):
        self.memory = AlphaExperienceMemory(state_file, store=store)
        self.evaluator = SealedSandboxEvaluator(min_ic_ir=min_ic_ir)
        self.safe_eval = SafeAlphaEvaluator(max_complexity=max_complexity)
        self.similarity_threshold = similarity_threshold
        self.max_alphas_per_cycle = max_alphas_per_cycle

    def build_generation_prompt(self) -> str:
        """Build LLM prompt for alpha generation using experience memory."""
        ctx = self.memory.get_context_for_llm()

        prompt = f"""You are an autonomous alpha factor researcher. Generate NEW formulaic alpha expressions.

EXPERIENCE CONTEXT:
- Total generations: {ctx['generation_count']}
- Validated alphas: {ctx['validated_count']}
- Rejected alphas: {ctx['rejected_count']}
- Best IC_IRs: {ctx.get('validated_ic_irs', [])[:5]}
- Categories found: {list(set(ctx.get('validated_categories', [])))}
- Recent rejections: {ctx.get('rejection_reasons', [])[:3]}

ALLOWED PRIMITIVES (use these exact function names):
- ts_rank(series, window) — rolling percentile rank
- ts_argmax(series, window) — argmax position in window
- ts_argmin(series, window) — argmin position in window
- delta(series, period) — difference
- delay(series, period) — lagged value
- scale(series, k) — rescale to sum(abs)=k
- decay_linear(series, period) — linear-weighted MA
- signed_power(series, exp) — sign(x) * |x|^exp
- log_safe(series) — guarded log
- sign(series), abs(series), sqrt(series), log(series), exp(series)

NOTE: rank() is NOT allowed — it is a full-sample normalizer (look-ahead bias)
and is rejected by the sandbox evaluator.

AVAILABLE COLUMNS: open, high, low, close, volume, returns

RULES:
1. Each expression must be a SINGLE line returning a pd.Series
2. Max 3 nested function calls
3. Must combine at least 2 different primitives
4. Must use at least 2 different columns
5. NO constants as the only input (must transform data)
6. CAUSAL ONLY: every operation must use trailing windows — no full-sample
   normalization, no future data
7. Generate expressions that are STRUCTURALLY DIFFERENT from past ones

Generate {self.max_alphas_per_cycle} NEW alpha expressions, one per line.
Format: EXPRESSION | CATEGORY | BRIEF_DESCRIPTION
Categories: momentum, reversal, mean_reversion, volume_price, composite

Example:
delta(ts_rank(close, 20), 1) * ts_rank(volume, 20) | volume_price | Volume-ranked short-term price momentum
"""
        return prompt

    def parse_llm_response(self, response: str) -> List[Dict[str, str]]:
        """Parse LLM response into alpha candidates."""
        candidates = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("EXPERIENCE"):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                expr = parts[0].strip()
                category = parts[1].strip().lower()
                description = parts[2].strip() if len(parts) > 2 else ""
                if expr and category in ("momentum", "reversal", "mean_reversion", "volume_price", "composite"):
                    candidates.append({
                        "expression": expr,
                        "category": category,
                        "description": description,
                    })
        return candidates

    def run_discovery_cycle(
        self,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        llm_generate_fn: Optional[Callable[[str], str]] = None,
    ) -> List[AlphaCandidate]:
        """Execute one Ralph Loop cycle.

        Args:
            train_df: Training OHLCV data (in-sample).
            test_df: Test OHLCV data (out-of-sample).
            llm_generate_fn: Function that takes a prompt and returns LLM response.
                             If None, generation goes through the shared
                             ``llm_router`` (``_router_llm_generate``); if that
                             cannot produce candidates, the pre-defined static
                             expressions are used for this cycle.

        Returns:
            List of validated AlphaCandidate objects.
        """
        self.memory.increment_generation()
        validated = []

        # Step 1: RETRIEVE
        existing = self.memory.get_existing_expressions()
        logger.info(
            "Ralph Loop cycle %d: %d existing alphas, %d validated",
            self.memory.memory.generation_count, len(existing),
            self.memory.memory.total_validated,
        )

        # Step 2: GENERATE — auto-clear fallback warning when LLM recovers.
        # Default generation path is the shared llm_router adapter (every
        # configured provider key serves factor mining); an explicit
        # llm_generate_fn overrides it. Any failure — router down, no keys,
        # unparseable reply — falls back to the 5 static candidates exactly
        # as before, and auto-clears on the next successful cycle.
        prompt = self.build_generation_prompt()
        if llm_generate_fn is None:
            llm_generate_fn = _router_llm_generate
            default_fn = True
        else:
            default_fn = False
        try:
            response = llm_generate_fn(prompt)
            raw_candidates = self.parse_llm_response(response)
            if not raw_candidates:
                raise ValueError("LLM response contained no parseable candidates")
            self.memory.memory.last_generation_source = (
                "EXPLICIT_FN" if not default_fn else "LLM")
            self.memory.memory.last_generation_at = datetime.now(timezone.utc).isoformat()
            self.memory.save()
            if hasattr(self, "_fallback_warned") and raw_candidates:
                logger.info("AlphaDiscovery LLM recovered — fallback cleared, using LLM-generated candidates")
                self._fallback_warned = False  # auto-clear
        except Exception as e:
            logger.warning("LLM generation failed: %s — fallback to 5 static, auto-clears on next success. Fix: check LLM provider key/endpoint", e)
            raw_candidates = self._fallback_candidates()
            self.memory.memory.last_generation_source = "FALLBACK_STATIC"
            self.memory.memory.last_generation_at = datetime.now(timezone.utc).isoformat()
            self.memory.save()

        # Step 3: EVALUATE (sealed sandbox)
        for raw in raw_candidates[:self.max_alphas_per_cycle]:
            candidate = AlphaCandidate(
                expression=raw["expression"],
                name=f"alpha_d{self.memory.memory.generation_count}_{len(validated)}",
                category=raw["category"],
            )

            # Originality check
            is_original, max_sim = check_originality(
                candidate.expression, existing, self.similarity_threshold
            )
            candidate.similarity_to_existing = max_sim

            if not is_original:
                candidate.status = "REJECTED"
                candidate.rejection_reason = f"Too similar to existing (sim={max_sim:.3f})"
                self.memory.record_rejected(candidate)
                logger.info("Rejected (similar): %s (sim=%.3f)", candidate.expression[:50], max_sim)
                continue

            # Sealed sandbox evaluation
            candidate = self.evaluator.evaluate_candidate(candidate, train_df, test_df)

            if candidate.status == "VALIDATED":
                # --- ζ Code Safety Agent: review expression before acceptance ---
                if get_zeta_agent is not None:
                    try:
                        zeta = get_zeta_agent()
                        review = zeta.review(
                            candidate.expression,
                            code_id=candidate.hash or candidate.name,
                        )
                        if review.verdict == SafetyVerdict.REJECTED:
                            candidate.status = "REJECTED"
                            violations = (
                                review.static.forbidden_imports
                                + review.static.forbidden_builtins
                                + review.static.epsilon_domain_hits
                                + review.static.errors
                            )
                            candidate.rejection_reason = (
                                f"ζ SAFETY REJECTED: {review.static.risk_level.value} risk, "
                                f"violations={violations}"
                            )
                            self.memory.record_rejected(candidate)
                            logger.info(
                                "Rejected (ζ safety): %s | %s",
                                candidate.expression[:50], candidate.rejection_reason,
                            )
                            continue
                        elif review.verdict == SafetyVerdict.NEEDS_HUMAN_REVIEW:
                            candidate.status = "REJECTED"
                            candidate.rejection_reason = (
                                f"ζ REQUIRES HUMAN REVIEW: {review.static.risk_level.value} risk, "
                                f"reason={review.human_review_reason}"
                            )
                            self.memory.record_rejected(candidate)
                            logger.info(
                                "Rejected (ζ human review needed): %s",
                                candidate.expression[:50],
                            )
                            continue
                        # APPROVED or SANDBOX_FAILED — log and continue
                        if review.verdict != SafetyVerdict.APPROVED:
                            logger.info(
                                "ζ verdict=%s for %s (proceeding)",
                                review.verdict.value, candidate.name,
                            )
                    except Exception as e:
                        logger.warning("ζ review failed (proceeding): %s", e)

                validated.append(candidate)
                self.memory.record_validated(candidate)
                logger.info(
                    "VALIDATED: %s | IC_IR=%.3f IC=%.4f cat=%s",
                    candidate.name, candidate.ic_ir, candidate.ic_mean, candidate.category,
                )
            else:
                self.memory.record_rejected(candidate)
                logger.info(
                    "Rejected: %s | %s",
                    candidate.expression[:50], candidate.rejection_reason,
                )

        logger.info(
            "Ralph Loop complete: %d/%d candidates validated",
            len(validated), len(raw_candidates),
        )
        return validated

    def _fallback_candidates(self) -> List[Dict[str, str]]:
        """Pre-defined fallback candidates when LLM is unavailable — clearly labeled DUMMY, auto-clears when LLM returns."""
        # Log once per process that fallback is active — Postel recovery guidance
        if not hasattr(self, "_fallback_warned"):
            self._fallback_warned = True
            logger.warning(
                "AlphaDiscovery LLM unavailable — using 5 static causal fallback expressions "
                "(trailing-window momentum/volume heuristics). "
                "Not FactorMiner DSL. Fix: set OPENROUTER_API_KEY (or TOKENROUTER_KEY / "
                "ROUTESME_KEY / CEREBRAS_API_KEY / NVIDIA_KEY) in .env so llm_router "
                "can serve factor mining. "
                "Error auto-clears when llm_generate_fn returns candidates."
            )
        return [
            {
                "expression": "decay_linear(delta(close, 1), 10) * ts_rank(volume, 20)",
                "category": "volume_price",
                "description": "Volume-weighted price momentum decay",
            },
            {
                "expression": "sign(delta(volume, 1)) * (ts_argmax(close, 20) - ts_argmin(close, 20))",
                "category": "momentum",
                "description": "Range position times volume direction",
            },
            {
                "expression": "signed_power(ts_rank(delta(close, 5), 10), 2) * ts_rank(delta(volume, 5), 10)",
                "category": "volume_price",
                "description": "Squared momentum rank with volume confirmation",
            },
            {
                "expression": "scale(ts_rank(delta(close, 5), 10), 1.0) * (-1 * ts_rank(volume, 20))",
                "category": "reversal",
                "description": "Short-term reversal with volume confirmation",
            },
            {
                "expression": "decay_linear(sign(delta(close, 2)) * ts_rank(volume, 15), 5)",
                "category": "mean_reversion",
                "description": "Direction-gated volume decay",
            },
        ]

    def get_status(self) -> Dict[str, Any]:
        """Return current discovery status."""
        return {
            "memory": self.memory.get_context_for_llm(),
            "min_ic_ir": self.evaluator.min_ic_ir,
            "similarity_threshold": self.similarity_threshold,
            "max_complexity": self.safe_eval.max_complexity,
        }
