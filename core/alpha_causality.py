"""Shared causality guard for alpha expressions.

Single source of truth for "does this expression read the future?".

Both alpha-generation stacks need the same answer, and they used to disagree:

  - `core/alpha_discovery.py` (LLM Ralph loop) rejected bare `rank(` because
    a full-sample percentile normalizer uses future bars on single-symbol data.
  - `research/alpha_miner/` (GA + QD mining) only regex-matched `shift(-n)`,
    so it happily generated and registered `cs_rank(...)` / `cs_normalize(...)`
    full-sample normalizers whose "alpha" could not be reproduced live.

That drift is how 15 look-ahead alphas reached `strategy_registry.json` graded
`strong` at IC -0.127. Keeping the rule in one place stops it recurring.

A `\\b` word boundary is deliberate and load-bearing: `_` is a word character, so
`\\brank\\s*\\(` does NOT match inside `cs_rank(` or `ts_rank(`, both of which are
causal rolling windows over past bars only.
"""
from __future__ import annotations

import ast
import re
from typing import List, Tuple

# Calls whose SECOND argument (or any argument) being negative reads the future.
_NEGATIVE_ARG_FUNCS = frozenset({"shift", "delay", "ts_delay", "ts_shift", "deltats"})

# (rule_id, compiled pattern, human-readable reason)
_REGEX_RULES: Tuple[Tuple[str, "re.Pattern[str]", str], ...] = (
    ("forward_return",
     re.compile(r"\bforward|\bfuture|\blead\s*\(|\bnext_bar|\btomorrow", re.IGNORECASE),
     "forward/future reference inside signal code"),
    ("full_sample_rank", re.compile(r"\brank\s*\("),
     "full-sample rank() is a non-causal normalizer on single-symbol data"),
    ("full_sample_zscore", re.compile(r"\b(?:zscore|normalize|standardize)\s*\("),
     "full-sample z-score/normalize uses the whole-sample mean and std"),
)

# Backwards-compatible alias: the judge exposed this name before the rule was
# shared, and it is the pattern set the LLM sandbox relies on.
LOOKAHEAD_RES = tuple(rx for _rid, rx, _why in _REGEX_RULES)


def _negative_shift_violations(tree: ast.AST) -> List[str]:
    """Catch `shift(close, -1)` / `ts_delay(close, -3)`.

    Done on the AST rather than with a regex because the negative number can be
    any argument, not just the first: `shift(-n)` is caught by the obvious
    pattern, `shift(close, -n)` is not. A regex for the general case would false-
    positive on legitimate arithmetic like `ts_delay(close - 1, 5)`.
    """
    hits: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _NEGATIVE_ARG_FUNCS:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            # Unary minus on a numeric literal is the only way to spell -n.
            if (isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub)
                    and isinstance(arg.operand, ast.Constant)
                    and isinstance(arg.operand.value, (int, float))
                    and not isinstance(arg.operand.value, bool)):
                hits.append(f"negative_shift: {name}() called with negative period "
                            f"(-{arg.operand.value}) consumes future bars")
                break
    return hits


def find_lookahead_violations(expression: str) -> List[str]:
    """Return one reason string per causality violation found, else [].

    Pure and side-effect free so it is safe to call from hot filter loops.
    """
    reasons: List[str] = []
    for rule_id, rx, why in _REGEX_RULES:
        if rx.search(expression):
            reasons.append(f"{rule_id}: {why}")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        # Unparseable input is the Logic criterion's problem, not causality's.
        return reasons
    reasons.extend(_negative_shift_violations(tree))
    return reasons


def is_causal(expression: str) -> bool:
    """True when `expression` contains no known future-data construct."""
    return not find_lookahead_violations(expression)