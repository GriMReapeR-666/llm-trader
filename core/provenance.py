"""Data provenance vocabulary — one source of truth for the evidence chain.

Every backtest declares the data it ran on, and every journal row declares the
evidence behind it.  Validation happens at the WRITE site: a missing or unknown
declaration raises instead of defaulting to something that reads like real
market evidence.  ``"synthetic"`` is a valid, honest declaration — it simply
may never be graded as if it were observed.

Two vocabularies, one module:

``BACKTEST_SOURCES``
    What a backtest may have run on.  ``mt5_history`` is broker bars,
    ``csv_archive`` is an operator-supplied file, ``synthetic`` is a seeded
    generator.  A backtest result must carry one of these.

``JOURNAL_SOURCES``
    What a ``trades`` row may be.  ``mt5_broker`` is a fill reconciled against
    the broker, ``paper_broker`` is the paper venue, ``legacy_import`` is a
    migrated journal row of unknown origin, and the ``*_generator``/
    ``synthetic`` entries are fixtures.

The sets overlap deliberately: ``synthetic`` is valid in both, and
``is_synthetic`` is the single predicate every promotion gate must use.
"""

from __future__ import annotations

import re
from typing import Optional

#: Seeded generators and fixture backtests.  Never authoritative evidence.
SYNTHETIC_SOURCES = frozenset({
    "synthetic",
    "synthetic_demo_generator",
    "demo_sample_generator",
    "backtest",
    "backtest_fixture",
    "fixture",
    "simulated",
})

BACKTEST_SOURCES = frozenset({
    "mt5_history",      # broker historical bars (copy_rates_*)
    "mt5_broker",       # broker tick/execution record replayed
    "paper_broker",     # paper-venue record replayed
    "csv_archive",      # operator-supplied file
    "kronos_simulated",  # model-generated path — simulated, not observed
}) | SYNTHETIC_SOURCES

JOURNAL_SOURCES = frozenset({
    "mt5_broker",       # live/demo fill reconciled against the broker
    "paper_broker",     # paper venue fill
    "manual",           # operator-recorded activity
    "legacy_import",    # migrated from a pre-journal format, unverified
}) | SYNTHETIC_SOURCES

#: Convenience: every token the write paths may store, in one place.
ALL_SOURCES = BACKTEST_SOURCES | JOURNAL_SOURCES

_BASE_RE = re.compile(r"^\s*([a-z0-9_]+)\s*(?:[(:].*)?$", re.IGNORECASE)

#: Honest synonyms callers already use. Resolved per vocabulary so the stored
#: token is always canonical: "mt5" is a broker fill in a journal row and
#: broker history in a backtest.
_ALIASES = {
    "mt5": {"journal": "mt5_broker", "backtest": "mt5_history"},
    "broker": {"journal": "mt5_broker", "backtest": "mt5_broker"},
    "live": {"journal": "mt5_broker", "backtest": "mt5_broker"},
    "paper": {"journal": "paper_broker", "backtest": "paper_broker"},
    "csv": {"journal": "csv_archive", "backtest": "csv_archive"},
    "json": {"journal": "csv_archive", "backtest": "csv_archive"},
    "parquet": {"journal": "csv_archive", "backtest": "csv_archive"},
    "file": {"journal": "csv_archive", "backtest": "csv_archive"},
    "demo": {"journal": "synthetic_demo_generator", "backtest": "synthetic"},
    "mock": {"journal": "synthetic", "backtest": "synthetic"},
    "fake": {"journal": "synthetic", "backtest": "synthetic"},
    "random": {"journal": "synthetic", "backtest": "synthetic"},
}


def provenance_token(value: Optional[str]) -> str:
    """Normalise a declaration to its bare token: ``synthetic(seed=42)`` → ``synthetic``.

    Returns ``""`` for missing/blank values.  Never raises — callers that need
    validation must use :func:`validate_data_source`.
    """
    if not isinstance(value, str):
        return ""
    match = _BASE_RE.match(value)
    return match.group(1).lower() if match else ""


def canonical_source(value: Optional[str], kind: str = "backtest") -> str:
    """Bare token, alias-resolved against the given vocabulary. ``""`` if unknown."""
    token = provenance_token(value)
    if not token:
        return ""
    resolved = _ALIASES.get(token, {}).get(kind, token)
    return resolved


def is_synthetic(value: Optional[str]) -> bool:
    """True when the declaration names a seeded generator or fixture.

    Missing/unknown declarations are *not* synthetic; they are unknown, and
    gates that require evidence must reject them by requiring a valid
    declaration rather than by treating silence as synthetic.  This keeps the
    two refusals — "fixture data" and "no declaration" — distinguishable in
    the audit trail.
    """
    token = provenance_token(value)
    if token in SYNTHETIC_SOURCES:
        return True
    # Any alias that resolves to a fixture is a fixture, whichever vocabulary
    # the caller had in mind.
    return any(target in SYNTHETIC_SOURCES for target in _ALIASES.get(token, {}).values())


def validate_data_source(
    value: Optional[str],
    *,
    kind: str = "backtest",
    field: str = "data_source",
) -> str:
    """Validate and normalise a provenance declaration at a write site.

    Args:
        value: The caller's declaration, e.g. ``"mt5_history"`` or
            ``"synthetic(seed=7)"``.
        kind: ``"backtest"`` or ``"journal"`` — which vocabulary applies.
        field: Name used in the error message, so a failure points at the
            offending argument.

    Returns:
        The normalised bare token to persist.

    Raises:
        ValueError: on a missing, blank, or unrecognised declaration.  There is
            no default: an undeclared source is exactly the condition this
            module exists to refuse.
    """
    token = canonical_source(value, kind)
    if not token:
        raise ValueError(
            f"{field} is required: declare where this data came from "
            f"({sorted(_allowed(kind))})"
        )
    if token not in _allowed(kind):
        raise ValueError(
            f"{field}={value!r} is not a known {kind} source; "
            f"expected one of {sorted(_allowed(kind))}"
        )
    return token


def _allowed(kind: str) -> frozenset:
    if kind == "journal":
        return JOURNAL_SOURCES
    if kind == "backtest":
        return BACKTEST_SOURCES
    raise ValueError(f"unknown provenance kind {kind!r}")


def sql_in_list(kind: str) -> str:
    """Quoted SQL literal list for the CHECK half of a schema constraint.

    The allowlist lives here and nowhere else, so a database trigger and the
    Python validator cannot drift apart.
    """
    return ",".join("'%s'" % token for token in sorted(_allowed(kind)))
