"""Regression: the meta-improvement ledger must actually exist and be checked.

`_apply_improvements` inserted into `meta_improvements` while no `CREATE TABLE`
for it existed anywhere in the tree. Every write raised "no such table" and was
swallowed at `log.debug`, so the orchestrator reported "no improvements" forever
and its applied-counter stayed at zero -- while `health_check()` (which never
inspected storage) still returned HEALTHY.

Separately, `_validate_hypotheses` gated on `hyp["confidence"] >= 0.6` where
`confidence` is a literal written by the generator itself. That compared a
constant against itself: every hypothesis passed by construction, and survivors
were stamped `validation_method="confidence_threshold"` as though a gate had run.
"""
from __future__ import annotations

import sqlite3

import pytest

from operations.meta_improvement_orchestrator import MetaImprovementOrchestrator


@pytest.fixture()
def orch(tmp_path):
    return MetaImprovementOrchestrator(db_path=str(tmp_path / "meta.db"))


# --- the missing table ------------------------------------------------------

def test_ledger_table_is_created(orch):
    with sqlite3.connect(orch.db_path) as con:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "meta_improvements" in names


def test_improvements_actually_persist(orch):
    """The write path that used to fail silently."""
    validated = orch._validate_hypotheses([
        {"type": "strategy_tuning", "strategy": "smc_fvg",
         "issue": "low_win_rate", "confidence": 0.6},
    ])

    applied = orch._apply_improvements(validated)

    assert applied == 1
    with sqlite3.connect(orch.db_path) as con:
        rows = con.execute(
            "SELECT strategy,validation_state FROM meta_improvements").fetchall()
    assert rows == [("smc_fvg", "UNVALIDATED")]


def test_schema_creation_is_idempotent(tmp_path):
    db = str(tmp_path / "twice.db")
    first = MetaImprovementOrchestrator(db_path=db)
    first._apply_improvements(first._validate_hypotheses(
        [{"type": "t", "strategy": "s", "confidence": 0.9}]))

    second = MetaImprovementOrchestrator(db_path=db)

    assert second.persistence_ok is True
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master"
                           " WHERE type='table' AND name='meta_improvements'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM meta_improvements").fetchone()[0] == 1


# --- the tautological "validation" ------------------------------------------

def test_hypotheses_are_marked_unvalidated_not_passed(orch):
    out = orch._validate_hypotheses([
        {"type": "t", "strategy": "s", "confidence": 0.95},
    ])
    assert out[0]["validation_state"] == "UNVALIDATED"
    assert out[0]["validation_method"] == "author_confidence_only"
    assert out[0]["execution_authority"] is False


def test_low_confidence_is_retained_but_still_unvalidated(orch):
    """Tiering is retained for operator review; it is just not called a gate."""
    out = orch._validate_hypotheses([
        {"type": "t", "strategy": "s", "confidence": 0.1},
    ])
    assert len(out) == 1
    assert out[0]["validation_state"] == "UNVALIDATED"


def test_missing_or_non_numeric_confidence_is_dropped_not_defaulted(orch):
    """A bare `.get("confidence", 0) >= 0.6` would silently drop these anyway;
    what matters is that a bool never scores as 1.0."""
    out = orch._validate_hypotheses([
        {"type": "a"},
        {"type": "b", "confidence": True},
        {"type": "c", "confidence": "0.9"},
        {"type": "d", "confidence": 0.4},
    ])
    assert len(out) == 1
    assert out[0]["type"] == "d"
    assert out[0]["author_confidence"] == 0.4


# --- health check must see its own storage ----------------------------------

def test_health_check_reports_the_ledger(orch):
    health = orch.health_check()
    assert "improvement_ledger" in health["subsystems"]
    assert health["subsystems"]["improvement_ledger"]["status"] == "available"


def test_health_check_is_degraded_when_ledger_is_broken(orch, monkeypatch):
    monkeypatch.setattr(orch, "persistence_ok", False)
    health = orch.health_check()
    assert health["subsystems"]["improvement_ledger"]["status"] == "error"
    assert health["overall_status"] == "DEGRADED"


def test_apply_is_a_noop_and_logs_when_ledger_unavailable(orch, monkeypatch, caplog):
    monkeypatch.setattr(orch, "persistence_ok", False)
    validated = orch._validate_hypotheses([{"type": "t", "confidence": 0.8}])

    with caplog.at_level("ERROR", logger="MetaImprovementOrchestrator"):
        applied = orch._apply_improvements(validated)

    assert applied == 0
    assert any("ledger unavailable" in r.message for r in caplog.records)