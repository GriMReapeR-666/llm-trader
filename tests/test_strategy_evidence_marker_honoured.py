"""The strategy registry's ``_EVIDENCE_INVALID`` marker must be authoritative.

``strategy_registry.json`` carries a marker saying its promoted (FULL) metrics
are hand-written and unbacked. ``core/file_utils.heal_strategy_registry_marker``
keeps that marker's counts fresh on every write, but healing is not honouring:
before this file existed, the only production readers
(``core/autonomous_bootstrap._load_strategy_registry`` and
``operations.strategy_factory.StrategyFactory.all``) returned the rows
untouched, so a strategy the registry itself calls unvalidated was surfaced as
FULL — Sharpe 2.14 on 342 trades — to the bootstrap, the deck and the sizing
path.

These tests pin the read-side behaviour. They deliberately do not assert that
anything was promoted or demoted *in the file*: the fix is in the reader, and
the registry is runtime state that must keep recording what was recorded.
"""
from __future__ import annotations

import json
import os

import pytest

from core.autonomous_bootstrap import _load_strategy_registry
from operations.strategy_factory import (
    StrategyFactory,
    as_unvalidated,
    registry_evidence_invalid,
    unvalidated_view,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "strategy_registry.json")

MARKER = {
    "verdict": "promoted_metrics_unbacked",
    "strategy_count": 2,
    "stages": {"FULL": 1, "CANDIDATE": 1},
    "why": ["the FULL row has no code pointer, so nothing reproduces its numbers"],
    "action": "treat every stage as unvalidated",
}


def _row(sid, stage, **extra):
    row = {"id": sid, "name": sid.upper(), "stage": stage, "trades": 0,
           "win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0, "max_dd": 0.0}
    row.update(extra)
    return row


#: "no marker at all", distinct from a marker present with a null value.
_ABSENT = object()


def _registry(rows, marker=MARKER):
    reg = {"strategies": rows}
    if marker is not _ABSENT:
        reg["_EVIDENCE_INVALID"] = marker
    return reg


def _write(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return str(path)


def _factory(tmp_path, reg):
    return StrategyFactory(
        _write(tmp_path / "registry.json", reg),
        str(tmp_path / "outbox.jsonl"),
        str(tmp_path / "decisions.jsonl"),
    )


# --- the marker decides the stage a reader is allowed to act on -------------

def test_marked_registry_reports_no_active_strategy(tmp_path):
    """The reported failure: 10 FULL rows with unbacked metrics, read as FULL."""
    f = _factory(tmp_path, _registry([
        _row("kronos_h1", "FULL", sharpe=2.14, trades=342, gate_progress=1.0),
        _row("stat_arb", "EVALUATION", sharpe=1.85),
        _row("alpha_mined", "CANDIDATE"),
    ]))

    rows = f.all()
    by_id = {r["id"]: r for r in rows}

    assert by_id["kronos_h1"]["stage"] == "CANDIDATE", (
        "a strategy whose metrics the registry marks unbacked is still being "
        "surfaced as FULL: %r" % by_id["kronos_h1"]["stage"])
    assert by_id["stat_arb"]["stage"] == "CANDIDATE"
    assert "_evidence_invalid" in by_id["kronos_h1"], (
        "a silently demoted row is indistinguishable from a real CANDIDATE")
    assert [r for r in rows if r["stage"] in ("FULL", "EVALUATION")] == []


def test_bootstrap_loader_applies_the_same_gate(tmp_path, monkeypatch):
    """Both production readers had to move, or the claim still reaches runtime."""
    path = _write(tmp_path / "reg.json", _registry([
        _row("kronos_h1", "FULL", sharpe=2.14),
        _row("sprt", "FULL", sharpe=2.6),
    ]))
    monkeypatch.setattr("core.autonomous_bootstrap.os.path.exists", lambda p: True)
    monkeypatch.setattr(
        "core.autonomous_bootstrap.open",
        lambda p, mode: open(path, mode),
        raising=False,
    )

    rows = _load_strategy_registry()
    assert rows and all(r["stage"] == "CANDIDATE" for r in rows), rows

    # ...which is exactly what empties active_strategies in _phase_validate.
    assert [s for s in rows if s.get("stage") in ("FULL", "EVALUATION")] == []


def test_unmarked_registry_keeps_its_stages(tmp_path):
    """Only a registry that speaks for itself is second-guessed."""
    f = _factory(tmp_path, _registry(
        [_row("real_one", "FULL"), _row("real_two", "EVALUATION")], marker=_ABSENT))

    assert [r["stage"] for r in f.all()] == ["FULL", "EVALUATION"]
    assert all("_evidence_invalid" not in r for r in f.all())


# --- fail closed ------------------------------------------------------------

@pytest.mark.parametrize("marker", ["promoted_metrics_unbacked", None, 42, []])
def test_unparseable_marker_still_invalidates(marker):
    """A marker we cannot parse is a marker we must still honour."""
    reg = _registry([_row("a", "FULL")], marker=marker)
    assert registry_evidence_invalid(reg) is True
    assert [r["stage"] for r in unvalidated_view(reg)] == ["CANDIDATE"]


def test_missing_strategies_key_yields_no_rows_not_a_crash():
    """Malformed input must fail to "nothing surfaced", never to "raise"."""
    assert unvalidated_view({"_EVIDENCE_INVALID": MARKER}) == []
    assert unvalidated_view({"strategies": "nope"}) == []
    assert unvalidated_view(None) == []
    assert unvalidated_view([{"stage": "FULL"}]) == []


# --- the reader must not become a writer ------------------------------------

def test_demotion_never_rewrites_the_recorded_stage(tmp_path):
    """all() hands back copies; the lifecycle path keeps the truth."""
    f = _factory(tmp_path, _registry([_row("kronos_h1", "FULL", sharpe=2.14)]))

    for row in f.all():
        row["stage"] = "RETIRED"
    assert f.get("kronos_h1")["stage"] == "FULL", (
        "the read view is writing through into the stored record")

    f.upsert({"id": "new_one", "name": "fresh", "stage": "CANDIDATE"})
    saved = json.load(open(f.registry_file, encoding="utf-8"))
    assert [s["stage"] for s in saved["strategies"]] == ["FULL", "CANDIDATE"], (
        "a save after a demoted read must not persist the demotion")
    assert saved["_EVIDENCE_INVALID"]["verdict"] == "promoted_metrics_unbacked", (
        "the disclosure was laundered away by an ordinary write")


def test_reading_does_not_touch_the_file(tmp_path):
    path = _write(tmp_path / "registry.json",
                  _registry([_row("kronos_h1", "FULL", sharpe=2.14)]))
    before = open(path, encoding="utf-8").read()
    _factory(tmp_path, json.loads(before)).all()
    assert open(path, encoding="utf-8").read() == before


def test_nothing_is_auto_promoted(tmp_path):
    """Demotion moves stages down only; no reader may raise one."""
    f = _factory(tmp_path, _registry([
        _row("promoted_by_hand", "CANDIDATE", sharpe=9.9, gate_progress=1.0),
        _row("also_candidate", "CANDIDATE"),
    ]))

    assert {r["stage"] for r in f.all()} == {"CANDIDATE"}
    assert not [a for a in f.pending_approvals() if a.get("status") == "PENDING"]


# --- metrics survive the demotion (safety gates read them) -----------------

def test_demotion_keeps_the_numbers_a_sizing_halt_reads(tmp_path):
    """intelligence/llm_orchestrator halts sizing on registry max_dd.

    Percent-vs-fraction makes those rows halt today by accident; that is still
    the safe direction. Dropping max_dd would turn a halt into normal sizing,
    which is a gate weakened to keep something running.
    """
    f = _factory(tmp_path, _registry([
        _row("kronos_h1", "FULL", sharpe=2.14, max_dd=3.8, trades=342)]))

    row = f.all()[0]
    assert row["stage"] == "CANDIDATE"
    assert row["max_dd"] == 3.8 and row["sharpe"] == 2.14 and row["trades"] == 342


def test_as_unvalidated_leaves_other_rows_untouched():
    """Rows already at CANDIDATE are the honest case already — returned as-is."""
    quiet = as_unvalidated(_row("quiet", "CANDIDATE"))
    assert quiet["stage"] == "CANDIDATE"
    assert "_evidence_invalid" not in quiet
    assert as_unvalidated("not a dict") == "not a dict"


# --- the shipped artifact ---------------------------------------------------

def test_repo_registry_surfaces_zero_validated_strategies():
    """Blast radius, asserted: 10 FULL + 3 EVALUATION rows stop being active."""
    with open(REGISTRY, encoding="utf-8") as fh:
        reg = json.load(fh)
    assert registry_evidence_invalid(reg), (
        "the marker is gone from strategy_registry.json — if it was genuinely "
        "regenerated from a real run, drop this test, not the gate")

    raw_full = [s for s in reg["strategies"] if s.get("stage") in ("FULL", "EVALUATION")]
    assert raw_full, "expected the promoted rows this finding is about"
    assert [r for r in unvalidated_view(reg) if r["stage"] in ("FULL", "EVALUATION")] == []
