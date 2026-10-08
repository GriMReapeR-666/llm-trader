"""Deterministic JudgeAgent rubric (P01 Sec. 3.6.2) for pre-execution code review.

Scores a candidate alpha on four criteria, each 0-10, equal-weighted
(paper Eq. 6: s_t = (Logic + LookAhead + Robustness + Clarity) / 4).
Accept when s_t >= 7.5 (Appendix Table 2).

This is the *deterministic* stand-in for the paper's LLaMA-3.1-8B judge
(temp 0.2): static checks + optional evaluated-signal diagnostics. An LLM
judge may replace individual criterion scorers later behind the same
`JudgeScore` contract; thresholds stay.

Absolute rules enforced (paper App. A.3):
  - no shift(-n), n > 0 (look-ahead bias forbidden)
  - no forward returns inside calc()
  - signal must be continuous float, not binary
  - vectorised pandas/numpy only (no Python loops over bars)
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.alpha_causality import find_lookahead_violations

from .alpha101_knowledge_base import PAPER_DEFAULTS

logger = logging.getLogger(__name__)

# Causality rules (negative shift, forward returns, full-sample normalizers) are
# owned by `core.alpha_causality` so the LLM sandbox and the GA miner cannot
# drift apart again. A previous version of this module only matched `shift(-n)`
# and `forward|future|tomorrow`, which let full-sample `rank()` / `cs_normalize()`
# normalizers through to registration graded "strong".
_LOOP_RE = re.compile(r"^\s*for\s+\w+\s+in\b|^\s*while\b", re.MULTILINE)
_BINARY_HINT = re.compile(r"np\.where|astype\s*\(\s*int|astype\s*\(\s*bool")


@dataclass
class JudgeScore:
    """Itemised verdict for one candidate (paper Sec. 3.6.2)."""
    logic: float = 0.0
    lookahead: float = 0.0
    robustness: float = 0.0
    clarity: float = 0.0
    feedback: List[str] = field(default_factory=list)

    @property
    def aggregate(self) -> float:
        return round((self.logic + self.lookahead + self.robustness + self.clarity) / 4.0, 2)

    @property
    def accepted(self) -> bool:
        return self.aggregate >= PAPER_DEFAULTS.judge_threshold

    def to_dict(self) -> Dict[str, object]:
        return {"logic": self.logic, "lookahead": self.lookahead,
                "robustness": self.robustness, "clarity": self.clarity,
                "aggregate": self.aggregate, "accepted": self.accepted,
                "feedback": list(self.feedback)}


def _score_logic(expression: str, feedback: List[str]) -> float:
    """Parses as a side-effect-free expression over a sane identifier set."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        feedback.append(f"Logic: expression does not parse ({e}).")
        return 1.0
    score = 8.0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Lambda,
                             ast.ListComp, ast.GeneratorExp, ast.Await, ast.Yield)):
            feedback.append(f"Logic: disallowed construct {type(node).__name__}.")
            score -= 3.0
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "open", "compile", "__import__"}:
                feedback.append(f"Logic: dangerous call {node.func.id}().")
                score -= 4.0
    if _LOOP_RE.search(expression):
        feedback.append("Logic: explicit Python loop over bars; vectorise with pandas/numpy.")
        score -= 3.0
    return round(max(0.0, min(10.0, score)), 2)


def _score_lookahead(expression: str, feedback: List[str]) -> float:
    """Hard-reject any future-data construct; causality is not a matter of degree.

    Delegates to `core.alpha_causality` so this stack and the LLM sandbox in
    `core/alpha_discovery.py` enforce one identical rule set. Any hit scores 0.0,
    which drags the aggregate to <= 5.0 and fails the 7.5 gate outright.
    """
    violations = find_lookahead_violations(expression)
    if violations:
        for v in violations:
            feedback.append(f"LookAhead: {v} (forbidden).")
        return 0.0
    if re.search(r"shift\s*\(\s*[1-9]", expression):
        feedback.append("LookAhead: positive shift() lags correctly.")
    return 10.0


def _score_robustness(expression: str, signal: Optional[pd.Series],
                      feedback: List[str]) -> float:
    score = 7.0  # neutral prior when only static text is available
    static_ok = ("fillna" in expression) or ("clip" in expression) or ("dropna" in expression)
    if static_ok:
        score += 1.0
    else:
        feedback.append("Robustness: no NaN/outlier handling (fillna/clip) detected.")
    if _BINARY_HINT.search(expression):
        feedback.append("Robustness: signal looks binary; paper requires continuous float.")
        score -= 2.0
    if signal is not None and len(signal):
        s = pd.Series(np.asarray(signal, dtype=float))
        nan_ratio = float(s.isna().mean())
        inf_ratio = float(np.isinf(s.to_numpy()).mean()) if len(s) else 0.0
        nunique = int(s.nunique(dropna=True))
        if nunique <= 1:
            feedback.append("Robustness: constant signal (IC undefined) - paper failure mode.")
            return 0.0
        score -= min(5.0, nan_ratio * 10.0)
        score -= min(3.0, inf_ratio * 10.0)
        if nan_ratio > 0.2:
            feedback.append(f"Robustness: NaN ratio {nan_ratio:.1%} too high.")
    return round(max(0.0, min(10.0, score)), 2)


def _score_clarity(expression: str, feedback: List[str]) -> float:
    score = 7.0
    lines = [ln for ln in expression.splitlines() if ln.strip()]
    if any(ln.strip().startswith("#") for ln in lines) or '"""' in expression or "'''" in expression:
        score += 1.5
    else:
        feedback.append("Clarity: no comments/docstring.")
    if len(lines) > 60:
        feedback.append("Clarity: overlong implementation; factor into helpers.")
        score -= 2.0
    if len(expression) > 4000:
        score -= 1.0
    return round(max(0.0, min(10.0, score)), 2)


def judge_candidate(expression: str,
                    signal: Optional[pd.Series] = None) -> JudgeScore:
    """Evaluate one candidate alpha; returns itemised score + feedback.

    Mirrors the inner-loop gate (Alg. 1, line 12): score, feedback =
    Judge.eval(candidate, ctx). Iteration control (K_inner=5, accept >=7.5)
    lives with the caller via `PAPER_DEFAULTS`.
    """
    feedback: List[str] = []
    result = JudgeScore(
        logic=_score_logic(expression, feedback),
        lookahead=_score_lookahead(expression, feedback),
        robustness=_score_robustness(expression, signal, feedback),
        clarity=_score_clarity(expression, feedback),
    )
    result.feedback = feedback
    logger.debug("judge_candidate aggregate=%.2f accepted=%s", result.aggregate, result.accepted)
    return result
