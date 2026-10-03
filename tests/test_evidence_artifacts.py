"""CRITICAL #10 - fabricated evidence artifacts must declare themselves.

``backtest_report.json`` and ``strategy_registry.json`` sit at the repo root and
are read by live code (``ui/gui_api.py:1924``, ``core/autonomous_bootstrap.py:107``,
``operations/strategy_factory.py:29``). Both recorded performance that nothing
could have produced:

``backtest_report.json``
    annualized_return_pct  13,759,242,345.376
    sharpe_ratio                    31.8175
    calmar_ratio            813,448,795.7443
    5,000 M15 bars is ~52 days on a 1,349.87% total return. A large annualization
    is arithmetically possible; 1.3e10 percent is a broken formula, and Calmar
    is derived from it.

``strategy_registry.json``
    103 entries, 10 at stage FULL, none with a code pointer. kronos_h1 records
    sharpe 2.14 over 342 trades where backtest_report.json records 31.82 over
    324 for the same pipeline.

Both files now carry a top-level ``_EVIDENCE_INVALID`` block. Nothing is
deleted: every pre-existing key and value is preserved, because the audit trail
is worth more than a tidy file. This test keeps the marker honest in both
directions - a file that trips these checks must say so, and a file that stops
tripping them must have had its marker removed.
"""
import json
import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "_EVIDENCE_INVALID"

BACKTEST = ROOT / "backtest_report.json"
REGISTRY = ROOT / "strategy_registry.json"


def _load(path):
    for _ in range(30):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            import time
            time.sleep(1)
    pytest.skip("%s unreadable" % path.name)


# --- the arithmetic that proves the report is broken --------------------

def _implausible(report):
    """Reasons this report cannot be a real measurement."""
    reasons = []
    ann = report.get("annualized_return_pct")
    total = report.get("total_return_pct")
    bars = report.get("bars_processed")
    tf = str(report.get("timeframe", "")).upper()

    # a 5,000-bar M15 run is ~52 days; anything annualised past ~1e6 percent
    # is a formula bug rather than a strategy
    if isinstance(ann, (int, float)) and abs(ann) > 1e6:
        reasons.append("annualized_return_pct=%r is implausible for %s bars"
                      % (ann, bars))
    # consistency: annualised and total must describe the same curve
    if all(isinstance(v, (int, float)) for v in (ann, total, bars)) and bars:
        # crude period estimate: M15 -> 96 bars/day
        per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48,
                   "H1": 24, "H4": 6, "D1": 1}.get(tf, 96)
        years = (bars / per_day) / 365.0
        if years > 0 and isinstance(total, (int, float)) and total > 0:
            naive = ((1 + total / 100.0) ** (1 / years) - 1) * 100.0
            if naive and abs(ann - naive) / max(abs(naive), 1e-9) > 10:
                reasons.append(
                    "annualized_return_pct=%r disagrees with a plain "
                    "compounding of total_return_pct=%r over %s bars by >10x "
                    "(naive ~%.3g)" % (ann, total, bars, naive))

    sharpe = report.get("sharpe_ratio")
    trades = report.get("total_trades")
    if isinstance(sharpe, (int, float)) and isinstance(trades, int) and trades:
        # a per-trade Sharpe above ~10 is not a sampling artifact
        if abs(sharpe) > 10:
            reasons.append("sharpe_ratio=%r over %d trades is not a "
                           "measurement" % (sharpe, trades))
    calmar = report.get("calmar_ratio")
    if isinstance(calmar, (int, float)) and abs(calmar) > 1e4:
        reasons.append("calmar_ratio=%r inherits the annualisation blow-up"
                      % calmar)
    return reasons


def test_backtest_report_is_still_implausible():
    """Guard the premise: if this ever passes, regenerate and drop the marker."""
    report = _load(BACKTEST)
    reasons = _implausible(report)
    assert reasons, (
        "backtest_report.json is now arithmetically self-consistent. If it was "
        "regenerated from a real run, remove _EVIDENCE_INVALID and update "
        "docs/CODEBASE_DEEP_DIVE.md CRITICAL #10.")


def test_backtest_report_declares_itself_invalid():
    report = _load(BACKTEST)
    assert MARKER in report, (
        "backtest_report.json still presents fabricated metrics to "
        "ui/gui_api.py without any invalidation marker")
    m = report[MARKER]
    assert m.get("verdict") == "unusable_as_evidence"
    assert m.get("why"), "marker must explain itself"
    assert m.get("action"), "marker must say what to do instead"


def test_registry_promoted_rows_have_no_evidence():
    reg = _load(REGISTRY)
    strats = reg.get("strategies") or []
    full = [s for s in strats if s.get("stage") == "FULL"]
    assert full, "expected the FULL-stage rows this finding is about"
    for s in full[:5]:
        assert not s.get("code") and not s.get("implementation"), (
            "a FULL strategy now claims an implementation: %r - if that is "
            "real, drop the invalidation marker" % s.get("id"))


def test_registry_declares_itself_invalid():
    reg = _load(REGISTRY)
    assert MARKER in reg, (
        "strategy_registry.json still hands hand-written metrics to "
        "core/autonomous_bootstrap.py and operations/strategy_factory.py")
    m = reg[MARKER]
    assert m.get("verdict") == "promoted_metrics_unbacked"
    assert m.get("strategy_count") == len(reg.get("strategies") or []), (
        "marker count is stale: %r vs %d actual"
        % (m.get("strategy_count"), len(reg.get("strategies") or [])))


def test_marker_is_additive_only():
    """The audit trail must survive: marking cannot drop a field."""
    for path, key in ((BACKTEST, "total_trades"), (REGISTRY, "strategies")):
        d = _load(path)
        assert key in d, "%s lost %r" % (path.name, key)
        assert len(d) > 1, "%s looks stripped" % path.name


def test_registry_and_report_are_reconciled_or_flagged():
    """The contradiction between the two files is the whole point.

    kronos_h1 claims a materially different Sharpe from the same pipeline that
    produced backtest_report.json. While both are marked invalid that is
    recorded; if either marker disappears while the numbers still disagree,
    that is a regression.
    """
    reg = _load(REGISTRY)
    rep = _load(BACKTEST)
    kronos = next((s for s in (reg.get("strategies") or [])
                   if s.get("id") == "kronos_h1"), None)
    if kronos is None:
        pytest.skip("kronos_h1 no longer in the registry")

    a = kronos.get("sharpe")
    b = rep.get("sharpe_ratio")
    disagree = (isinstance(a, (int, float)) and isinstance(b, (int, float))
                and abs(a - b) > 1.0)
    if disagree:
        assert MARKER in rep and MARKER in reg, (
            "registry says sharpe %r over %d trades, report says %r over %d - "
            "two irreconcilable numbers for one pipeline, and at least one "
            "file is no longer flagged" % (a, kronos.get("trades"), b,
                                           rep.get("total_trades")))
