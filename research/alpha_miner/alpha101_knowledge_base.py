"""101 Alpha Knowledge Base (Psi) + nested-loop parameters.

Implements Paper P01 Section 3.5: the WorldQuant 101 Formulaic Alphas as a
*static, pre-loaded structured prior* (Psi) grounding generation, complementary
to the dynamic Main KB (Phi) of validated discoveries.

Each record stores three representations (paper Sec. 3.5.1):
  1. executable code   - source of the factor function (directly executable)
  2. structural fingerprint - operator set + lookback-window set (static analysis)
  3. natural-language description - docstring + vocab-mapped summary (retrieval surface)

Retrieval (paper Sec. 3.5.2) selects top_k=1 by cosine similarity. Deviation:
the paper embeds with all-MiniLM-L6-v2 (384-d, external model dependency).
This module scores L2-normalised token/fingerprint overlap instead - fully
deterministic, zero new dependencies (ponytail: stdlib first). Swap in an
embedding backend later behind `RetrievalBackend` if measured retrieval
precision justifies it.

Formulas are REUSED from `core.alpha_factors._ALPHA_REGISTRY` (Kakushadze
2016, 20+ FX-adapted factors) - never duplicated here.

References:
    Vu, Pham & Tran (2026). "Self-Improving Alpha Mining ..." SSRN 6906675.
    Kakushadze, Z. (2016). "101 Formulaic Alphas." Wilmott.
"""
from __future__ import annotations

import inspect
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paper parameters (Appendix A.4) - single source of truth for loop config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NestedLoopParams:
    """Refinement/backtest configuration from P01 Table 2 (App. A.4.2)."""
    k_inner: int = 5              # max inner refinement iterations
    judge_threshold: float = 7.5  # accept when s_t >= 7.5/10
    k_outer: int = 5              # max outer accumulation iterations
    tau_ic: float = 0.01          # outer-loop IC acceptance threshold
    signal_clip: Tuple[float, float] = (-5.0, 5.0)  # Eq. 8/12 normalisation
    forward_horizon: int = 5      # 5-day forward returns (Eq. 9)
    vol_ratio_min: float = 1.0    # liquidity filter (Eq. 15)
    coverage_min: float = 0.80    # 80% data-coverage universe filter
    retrieval_top_k: int = 1      # RETRIEVE(..., top_k=1)


PAPER_DEFAULTS = NestedLoopParams()


# ---------------------------------------------------------------------------
# Operator vocabulary (paper Sec. 3.5.1: rank, rolling, corr, diff, shift, ...)
# ---------------------------------------------------------------------------

OPERATOR_VOCAB = (
    "ts_rank", "ts_argmax", "ts_argmin", "ts_min", "ts_max",
    "rolling_corr", "rolling_mean", "rolling_std", "rolling_max", "rolling_min",
    "decay_linear", "signed_power", "delay", "delta", "rank", "scale",
    "rolling", "corr", "diff", "shift", "pct_change", "clip", "fillna",
    "mean", "std", "min", "max", "abs", "sign", "log", "sqrt",
)

_NL_VOCAB = {  # token -> plain-English (paper Sec. 3.5.1 vocab mapping)
    "rolling": "rolling window", "rank": "cross-sectional rank",
    "corr": "correlation", "diff": "difference", "shift": "lagged shift",
    "delay": "delayed", "delta": "momentum change", "decay_linear": "linear decay weighting",
    "ts_rank": "time-series rank", "scale": "rescaled", "clip": "clipped",
    "pct_change": "percent change", "std": "volatility", "mean": "average",
}

_WINDOW_RE = re.compile(r"(?:rolling\s*\(\s*|window\s*=\s*|period\s*=\s*)(\d+)")
_INPUT_RE = re.compile(r"""df\s*\[\s*['"](\w+)['"]\s*\]|df\.(\w+)""")


def extract_operators(source: str) -> Set[str]:
    """Static-analysis operator fingerprint of a factor implementation."""
    return {op for op in OPERATOR_VOCAB if re.search(r"\b" + re.escape(op) + r"\b", source)}


def extract_windows(source: str) -> Set[int]:
    """Lookback windows appearing in rolling()/window/period calls."""
    return {int(m.group(1)) for m in _WINDOW_RE.finditer(source)}


def extract_inputs(source: str) -> Set[str]:
    """OHLCV column names referenced by the factor."""
    found: Set[str] = set()
    for m in _INPUT_RE.finditer(source):
        found.add(m.group(1) or m.group(2))
    return {c for c in found if not c.startswith("_")}


def describe_record(alpha_id: str, doc: str, operators: Set[str],
                    windows: Set[int], inputs: Set[str]) -> str:
    """Primary text surface for retrieval: docstring + vocab-mapped summary."""
    parts = [doc.strip().splitlines()[0] if doc.strip() else f"Formulaic alpha {alpha_id}"]
    nl_ops = sorted({_NL_VOCAB.get(op, op) for op in operators})
    if nl_ops:
        parts.append("Uses " + ", ".join(nl_ops) + ".")
    if windows:
        parts.append("Lookback windows " + ", ".join(map(str, sorted(windows))) + ".")
    if inputs:
        parts.append("Inputs " + ", ".join(sorted(inputs)) + ".")
    return " ".join(parts)


@dataclass
class Alpha101Record:
    """One retrievable 101-alpha record (paper Sec. 3.5.1)."""
    alpha_id: str
    inputs: Set[str] = field(default_factory=set)
    code: str = ""                 # executable source (retrieval + execution)
    operators: Set[str] = field(default_factory=set)
    windows: Set[int] = field(default_factory=set)
    description: str = ""          # NL surface for semantic matching

    def retrieval_text(self) -> str:
        return f"{self.description} operators {' '.join(sorted(self.operators))}"


def _tokenize(text: str) -> Dict[str, int]:
    return {t: text.lower().count(t) for t in set(re.findall(r"[a-z_]+", text.lower()))}


def _cosine(a: Dict[str, int], b: Dict[str, int]) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class Alpha101KnowledgeBase:
    """Static pre-loaded Psi (paper Sec. 3.5): retrieve-only, never mutated by loops."""

    def __init__(self, records: List[Alpha101Record]):
        self._records = list(records)
        self._vectors = [_tokenize(r.retrieval_text()) for r in self._records]

    def __len__(self) -> int:
        return len(self._records)

    def retrieve(self, query: str, top_k: int = 1) -> List[Tuple[Alpha101Record, float]]:
        """RETRIEVE(ctx, Psi, top_k): cosine-ranked records (paper Eq. 4-5)."""
        q = _tokenize(query)
        scored = [(r, _cosine(q, v)) for r, v in zip(self._records, self._vectors)]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:max(1, top_k)]

    def get(self, alpha_id: str) -> Optional[Alpha101Record]:
        key = alpha_id.upper().strip()
        return next((r for r in self._records if r.alpha_id == key), None)


def build_knowledge_base() -> Alpha101KnowledgeBase:
    """Load Psi from the existing `core.alpha_factors` registry (reuse, no copies)."""
    from core import alpha_factors  # lazy: keeps research->core edge out of import time

    records = []
    for alpha_id, fn in sorted(alpha_factors._ALPHA_REGISTRY.items()):
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):
            source = ""
        doc = inspect.getdoc(fn) or ""
        ops = extract_operators(source)
        wins = extract_windows(source)
        inputs = extract_inputs(source)
        records.append(Alpha101Record(
            alpha_id=alpha_id.upper(),
            inputs=inputs,
            code=source,
            operators=ops,
            windows=wins,
            description=describe_record(alpha_id, doc, ops, wins, inputs),
        ))
    logger.info("Alpha101KnowledgeBase loaded %d records", len(records))
    return Alpha101KnowledgeBase(records)
