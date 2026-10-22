"""Hypothesis classes — the multiple-testing families the DSR gate deflates within.

The S.Ξ trial ledger remembers every backtest it has ever seen.  Deflating a
brand-new hypothesis against that global family is *correct* (audit
2026-10-20, §7) and *useless*: at 2818 trials the luck bar sits at
E[max|null] = 3.53 SE (the exact order statistic; the biased Gumbel form
sqrt(2 ln k) would say 3.99), and a real annualized Sharpe of 1.9 is
detected ~1% of the time.  The audit names the lever explicitly — "the lever
that restores power is scoping" — and the ledger already carries a
``strategy_class`` column for it.  What was missing is a *canonical
taxonomy*: without one, every caller had to hand-tag its family, 92% of the
ledger stayed untagged, and the class pools counted only the rows somebody
remembered to tag — so a class bar was computed against a fraction of the
family it was supposed to represent.

This module is the single owner of that taxonomy:

  * A **class** is a research programme — a family of hypotheses the
    researcher had the opportunity to pick from — not a per-run label.
    "alpha101_short" is the alpha-library programme; "momentum_trend" is the
    momentum/time-series-momentum programme; "full_pipeline" is the
    continuous-backtest-pipeline's own strategy runs.
  * ``classify()`` derives the class from the hypothesis identity, so a
    caller cannot quietly under-report its family by forgetting a tag.
  * An identity that cannot be honestly attributed to a family stays
    ``UNCLASSIFIED`` and keeps the **global** bar (fail-closed).  The
    taxonomy never invents a family.

Scoping is only honest if the class pool contains *every* trial of that
family, including the ones recorded before this taxonomy existed.  That is
what ``effective_class()`` is for: a stored ``general`` tag means "no class
claim", and the class is re-derived from the strategy id at read time.

The taxonomy is versioned (``CLASSIFIER_VERSION``) so a row re-derived years
from now lands in the same family it was recorded in.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Bumped whenever the rules below change meaning.  A stored tag is always
# honoured as-is; only rows carrying no claim are re-derived, and those are
# re-derived with the CURRENT version.
CLASSIFIER_VERSION = "v1"

# The fail-closed bucket.  Rows that cannot be attributed to a family land
# here and keep facing the global bar.  The literal "general" is kept for
# wire compatibility with rows already in the ledger.
UNCLASSIFIED = "general"


@dataclass(frozen=True)
class HypothesisClass:
    """One research programme — the family a hypothesis is selected from."""

    class_id: str
    description: str
    patterns: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The taxonomy.  Ordered: the first matching rule wins, so the specific
# programmes come before the catch-all pipeline/cell rule.
# ---------------------------------------------------------------------------
_CLASSES: Tuple[HypothesisClass, ...] = (
    HypothesisClass(
        "alpha101_short",
        "alpha101 library alphas (short-horizon factor research programme)",
        (r"^alpha_\d+_", r"^alpha_harness_", r"^alpha_crossover", r"^ga_alpha_"),
    ),
    HypothesisClass(
        "cpcv",
        "combinatorial purged cross-validation path families",
        (r"^cpcv",),
    ),
    HypothesisClass(
        "news_gate",
        "news-gated hypotheses",
        (r"^news", r"_news_"),
    ),
    HypothesisClass(
        "macro_event",
        "macro-event driven hypotheses",
        (r"^macro_",),
    ),
    HypothesisClass(
        "agent_based",
        "agent-generated hypotheses (execution RL, regime supervision)",
        (r"^agent_",),
    ),
    HypothesisClass(
        "model_based",
        "model-driven hypotheses (state-space, changepoint, neural fusion)",
        (r"^model_", r"^kronos", r"kalman", r"bocpd", r"changepoint"),
    ),
    HypothesisClass(
        "statistical_gate",
        "sequential-test / gate hypotheses",
        (r"^sprt",),
    ),
    HypothesisClass(
        "volatility_derivatives",
        "volatility and derivatives-surface hypotheses",
        (r"volatility", r"skew", r"^vix"),
    ),
    HypothesisClass(
        "mean_reversion",
        "mean-reversion and statistical-arbitrage hypotheses",
        (r"^meanrev", r"^statarb", r"reversion"),
    ),
    HypothesisClass(
        "momentum_trend",
        "momentum / trend / time-series-momentum hypotheses",
        (r"momentum", r"^trend_", r"tsmom", r"^persist"),
    ),
    HypothesisClass(
        "full_pipeline",
        "continuous-backtest-pipeline strategy runs (symbol/timeframe cells)",
        (
            r"^full_pipeline",
            r"^IS_",
            r"^OOS_",
            r"^live_mt5_",
            r"^[A-Z]{6}_[MHD]\d+(?:_backtest)?$",
            r"_backtest$",
        ),
    ),
)

# Case-insensitive on purpose: a family name spelled in another case is the
# SAME family.  Case-sensitivity would silently drop such a row out of its
# pool, and a class pool missing a member of its own family is a bar that
# invents significance (the exact defect this taxonomy exists to remove).
_COMPILED: Tuple[Tuple[HypothesisClass, Tuple[re.Pattern, ...]], ...] = tuple(
    (spec, tuple(re.compile(p, re.IGNORECASE) for p in spec.patterns))
    for spec in _CLASSES
)


def known_classes() -> List[str]:
    """Every class id the taxonomy can produce (excluding the unclassified bucket)."""
    return [spec.class_id for spec in _CLASSES]


def describe_class(class_id: str) -> str:
    """Human-readable description of a class id (unknown ids say so)."""
    for spec in _CLASSES:
        if spec.class_id == class_id:
            return spec.description
    if class_id == UNCLASSIFIED:
        return "Unattributed hypotheses — keep the global (fail-closed) bar."
    return "Unknown hypothesis class."


def _classify_by_id(strategy_id: str) -> Optional[str]:
    """First matching taxonomy rule for a strategy id, or None."""
    sid = (strategy_id or "").strip()
    if not sid:
        return None
    for spec, patterns in _COMPILED:
        for pattern in patterns:
            if pattern.search(sid):
                return spec.class_id
    return None


def classify(
    strategy_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Derive the hypothesis class of a trial from its identity.

    Args:
        strategy_id: The strategy/hypothesis identifier that was recorded.
        metadata: Optional trial metadata.  Only used as a fallback: the
            continuous-backtest-pipeline stamps ``symbol``/``timeframe`` on
            every run it records, which identifies a pipeline cell even when
            the id is uninformative.

    Returns:
        The class id, or ``UNCLASSIFIED`` when the identity cannot be
        honestly attributed to a family.  ``UNCLASSIFIED`` is NOT a licence
        to deflate against a small pool — it keeps the global bar.
    """
    found = _classify_by_id(strategy_id)
    if found:
        return found
    if isinstance(metadata, Mapping):
        symbol = str(metadata.get("symbol") or "").strip()
        timeframe = str(metadata.get("timeframe") or "").strip()
        if symbol and timeframe:
            # The pipeline stamps symbol/timeframe on the trials it owns.
            return "full_pipeline"
    return UNCLASSIFIED


def resolve_class(
    strategy_class: Optional[str],
    strategy_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Resolve the class a hypothesis should be deflated within.

    An explicit tag always wins — the caller knows its own research
    programme better than a name pattern does.  ``"general"`` is NOT a
    claim: it is the historical "no class" tag, so it is treated exactly
    like no tag at all.  Otherwise the class is derived from the identity.

    Returns:
        The class id, or ``None`` when the hypothesis cannot be attributed
        to a family.  ``None`` means "deflate against the GLOBAL family" —
        the strictest, fail-closed bar.  It never means "deflate against the
        small unclassified bucket".
    """
    if strategy_class and str(strategy_class).strip() != UNCLASSIFIED:
        return str(strategy_class)
    derived = classify(strategy_id, metadata)
    if derived and derived != UNCLASSIFIED:
        return derived
    return None


def scope_of(strategy_class: Optional[str]) -> Optional[str]:
    """The deflation scope a stored class tag asks for.

    ``None``/empty/``"general"`` all mean "no class claim" and therefore
    the GLOBAL family (the strictest bar).  The unattributed bucket is an
    observability bucket, not a family to deflate within.
    """
    if not strategy_class:
        return None
    scope = str(strategy_class).strip()
    if not scope or scope == UNCLASSIFIED:
        return None
    return scope


def effective_class(
    stored_class: Optional[str],
    strategy_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """The class a stored row actually counts toward.

    A stored tag that names a family is authoritative — it was written by
    the caller that owned the trial.  A stored ``general`` tag means "no
    class claim", so the class is re-derived from the strategy id; that is
    what pulls the ledger's pre-taxonomy rows into the family they belong
    to instead of leaving the class pool a fraction of its true size.

    Rows that derive to nothing stay in ``UNCLASSIFIED`` and keep facing
    the global bar.
    """
    stored = (stored_class or "").strip()
    if stored and stored != UNCLASSIFIED:
        return stored
    return classify(strategy_id, metadata)


def group_by_effective_class(
    rows: Iterable[Sequence[Any]],
) -> Dict[str, List[float]]:
    """Group trial Sharpes by effective hypothesis class.

    Args:
        rows: ``(strategy_id, stored_class, sharpe, metadata)`` tuples — the
            metadata may be ``None`` or a JSON string.

    Returns:
        ``{class_id: [sharpe, ...]}`` for every class present.
    """
    groups: Dict[str, List[float]] = {}
    for row in rows:
        strategy_id = row[0]
        stored_class = row[1]
        sharpe = float(row[2])
        metadata = row[3] if len(row) > 3 else None
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata) if metadata else None
            except (ValueError, TypeError):
                metadata = None
        cls = effective_class(stored_class, strategy_id, metadata)
        groups.setdefault(cls, []).append(sharpe)
    return groups
