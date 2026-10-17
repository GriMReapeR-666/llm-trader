"""Guardian pages must reach a live drain loop (2026-10-16 regression).

The external guardian paged the operator through
``GUARDIAN_ALERTS_DB``, a database with no consumer: ``core.external_guardian``
was the only module that ever touched it, and it only ever *wrote*. Every
guardian trip therefore accumulated undelivered forever -- 217 CRITICAL rows,
``attempts=0``, oldest 2026-10-04, never once read. It was the highest-severity
page in the system pointing at a dead-letter sink.

The literal shipped in the initial commit, so the split was an accident rather
than a design decision, and ``guardian_alerts.db`` has no tracked history.

These tests pin the two properties that matter:
  1. a trip lands in the journal outbox that ``alert_dispatch`` actually drains;
  2. that dispatcher emits and acknowledges the page, so it is not merely
     stored (storing in a drained outbox is the fix; storing in a fresh dead
     one would reintroduce the same bug).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import external_guardian as eg  # noqa: E402
from core.external_guardian import TripSeverity  # noqa: E402


def test_guardian_default_outbox_is_the_drained_journal():
    """The declared default must be the outbox the alert_dispatch schedule drains.

    Read from source via AST rather than the imported attribute: the autouse
    ``isolate_shared_root_databases`` fixture repoints ``GUARDIAN_ALERTS_DB`` at
    ``tmp_path``, so the live attribute is the sandbox value and tells us
    nothing about the default. Asserted against ``alert_agent.DEFAULT_JOURNAL``
    rather than a literal, so a future change to the dispatcher's target cannot
    silently re-split them.
    """
    import ast

    from operations.agents import alert_agent

    source = Path(eg.__file__).read_text(encoding="utf-8")
    declared = None
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "GUARDIAN_ALERTS_DB":
                    declared = ast.literal_eval(node.value)
    assert declared == alert_agent.DEFAULT_JOURNAL, (
        "the guardian must page the same outbox the dispatcher drains; "
        f"declared {declared!r} vs dispatcher {alert_agent.DEFAULT_JOURNAL!r}")


def test_guardian_trip_pages_into_journal_outbox(tmp_path, monkeypatch):
    """A RED trip must be PENDING in the drained outbox, not in a private file."""
    from operations import alert_outbox

    journal = str(tmp_path / "trading_state.db")
    monkeypatch.setattr(eg, "GUARDIAN_ALERTS_DB", journal)

    g = eg.ExternalGuardian()
    g._page_human("drawdown breach", TripSeverity.RED)

    rows = alert_outbox.status(journal)
    assert rows["counts"].get("PENDING", 0) == 1, rows
    claimed = alert_outbox.claim(journal, limit=10)
    assert len(claimed) == 1
    assert claimed[0]["code"] == "\u03a8_GUARDIAN_RED"
    assert claimed[0]["severity"] == "CRITICAL"
    assert "drawdown breach" in str(claimed[0]["payload"])


def test_guardian_page_is_delivered_by_the_real_dispatcher(tmp_path, monkeypatch):
    """End-to-end: page -> dispatched -> SENT.

    This is the property the dead sink broke. Storing the page in the drained
    outbox is necessary but not sufficient; the dispatcher must claim, emit and
    acknowledge it.
    """
    from operations import alert_outbox
    from operations.agents import alert_agent

    journal = str(tmp_path / "trading_state.db")
    monkeypatch.setattr(eg, "GUARDIAN_ALERTS_DB", journal)

    g = eg.ExternalGuardian()
    g._page_human("equity floor breached", TripSeverity.FLOOR)

    result = alert_agent.handle_alert_dispatch({"journal_path": journal})

    assert result["status"] == "OK"
    assert result["claimed"] == 1, result
    assert result["sent"] == 1, result
    assert result["failed"] == 0, result

    with alert_outbox._connect(journal) as con:
        con.row_factory = __import__("sqlite3").Row
        row = con.execute(
            "SELECT status, attempts, sent_at FROM alert_outbox").fetchone()
    assert row["status"] == "SENT", dict(row)
    assert row["attempts"] >= 1, dict(row)
    assert row["sent_at"], dict(row)


def test_every_severity_pages_to_the_drained_outbox(tmp_path, monkeypatch):
    """No severity may bypass the drain loop (a partial fix is the same bug)."""
    from operations import alert_outbox
    from operations.agents import alert_agent

    journal = str(tmp_path / "trading_state.db")
    monkeypatch.setattr(eg, "GUARDIAN_ALERTS_DB", journal)

    g = eg.ExternalGuardian()
    for severity in TripSeverity:
        g._page_human(f"probe {severity.value}", severity)

    assert alert_outbox.status(journal)["counts"].get("PENDING", 0) == len(TripSeverity)

    result = alert_agent.handle_alert_dispatch({"journal_path": journal})
    assert result["failed"] == 0, result
    assert alert_outbox.status(journal)["counts"].get("PENDING", 0) == 0
    assert alert_outbox.status(journal)["counts"].get("SENT", 0) == len(TripSeverity)