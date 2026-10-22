"""Legacy rows with no declared provenance are quarantined, never labelled.

The audit's residual was honest: a journal row written before the
``trades.provenance`` constraint existed carries NULL, and it is excluded
from promotion by the undeclared-row rule *rather than retro-labelled*.

Phase 1 makes that exclusion explicit and auditable:

* the quarantine records who parked the row, when and why;
* the row itself is never touched — ``provenance`` stays NULL, because
  "we do not know where this came from" must not become "we know it came
  from the broker" by administrative act;
* every admission reader (promotion gates, the research dataset) excludes a
  quarantined ticket, so a parked row can never contribute evidence;
* the counts are reported, so the operator sees what was parked instead of
  watching a gate fail with an unexplained row count.
"""
from __future__ import annotations

import sqlite3

import pytest

from core.trading_safety import TradeProposal, TradingStore
from intelligence.feature_schema import FEATURE_SCHEMA_VERSION, SCHEMA_HASHES, values_hash
from intelligence.trading_memory import TradingMemory
from operations.provenance_quarantine import (
    CONFIRMATION,
    quarantine_legacy_rows,
    quarantined_tickets,
    report,
)


def _mark_broker_submitted(path: str, tickets) -> None:
    """The journal's own submission record — the only independent evidence."""
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS order_intents (
            intent_key TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
            run_id TEXT, proposal_id TEXT, status TEXT, mt5_ticket INTEGER,
            payload TEXT, result TEXT, error TEXT)""")
        for ticket in tickets:
            con.execute(
                "INSERT OR REPLACE INTO order_intents"
                "(intent_key,created_at,status,mt5_ticket) VALUES(?,?,?,?)",
                (f"intent:{ticket}", "2026-01-01T00:00:00+00:00", "FILLED", ticket))


def _closed_trade(path: str, ticket: int, provenance: str = "mt5_broker") -> None:
    """A fully evidenced closed trade: label, features and broker record."""
    store = TradingStore(path)
    values = {"close": 1.1}
    features = {
        "setup_id": f"setup-{ticket}",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8, features)
    store.record_open_trade(ticket, proposal, 1.0, 1.0,
                            {"proposal_features": features}, provenance=provenance)
    store.record_close(ticket, 1.2, 1.0, "target")
    TradingMemory(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,"
            "label_rule,review_status) VALUES(?,?,?,?,?,?,?)",
            (ticket, "WIN", "target", 1.0, "2026-01-01T00:00:00+00:00",
             "deterministic_v1", "AUTO"))
    _mark_broker_submitted(path, [ticket])


def _journal(tmp_path, name="trading_state.db"):
    path = str(tmp_path / name)
    _closed_trade(path, 1)
    _closed_trade(path, 2)
    with sqlite3.connect(path) as con:
        # Ticket 1 predates the provenance constraint; ticket 2 declared it.
        con.execute("UPDATE trades SET provenance=NULL WHERE ticket=1")
    return path


# ── The quarantine itself ───────────────────────────────────────────────────

def test_quarantine_requires_the_confirmation_token(tmp_path):
    path = _journal(tmp_path)
    with pytest.raises(ValueError, match="confirmation token"):
        quarantine_legacy_rows(path, operator="reviewer", note="legacy rows")


def test_quarantine_requires_an_operator_and_a_note(tmp_path):
    path = _journal(tmp_path)
    with pytest.raises(ValueError, match="operator and note"):
        quarantine_legacy_rows(path, operator="", note="", confirmation=CONFIRMATION)


def test_quarantine_of_a_missing_journal_raises(tmp_path):
    with pytest.raises(ValueError, match="journal database is missing"):
        quarantine_legacy_rows(str(tmp_path / "nope.db"), operator="r", note="n",
                               confirmation=CONFIRMATION)


def test_quarantine_never_labels_the_row(tmp_path):
    """The row's provenance stays NULL — a quarantine is not a declaration."""
    path = _journal(tmp_path)
    result = quarantine_legacy_rows(path, operator="reviewer",
                                    note="rows predate the provenance column",
                                    confirmation=CONFIRMATION)
    assert result["undeclared_rows"] == 1
    assert result["newly_quarantined"] == 1
    assert result["provenance_modified"] is False
    with sqlite3.connect(path) as con:
        provenance = con.execute(
            "SELECT provenance FROM trades WHERE ticket=1").fetchone()[0]
        quarantined_at = con.execute(
            "SELECT quarantined_at, operator, reason FROM provenance_quarantine "
            "WHERE ticket=1").fetchone()
    assert provenance is None, "the row must not be retro-labelled"
    assert quarantined_at[0]
    assert quarantined_at[1] == "reviewer"
    assert quarantined_at[2] == "undeclared_provenance"


def test_quarantine_leaves_declared_rows_alone(tmp_path):
    path = _journal(tmp_path)
    result = quarantine_legacy_rows(path, operator="reviewer", note="legacy",
                                    confirmation=CONFIRMATION)
    assert result["tickets"] == [1]
    assert 2 not in quarantined_tickets(path)
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT provenance FROM trades WHERE ticket=2").fetchone()[0] == "mt5_broker"


def test_quarantine_is_idempotent(tmp_path):
    path = _journal(tmp_path)
    first = quarantine_legacy_rows(path, operator="r", note="n", confirmation=CONFIRMATION)
    second = quarantine_legacy_rows(path, operator="r", note="n", confirmation=CONFIRMATION)
    assert first["newly_quarantined"] == 1
    assert second["newly_quarantined"] == 0
    assert second["already_quarantined"] == 1
    with sqlite3.connect(path) as con:
        count = con.execute("SELECT COUNT(*) FROM provenance_quarantine").fetchone()[0]
    assert count == 1


def test_report_counts_pending_and_quarantined(tmp_path):
    path = _journal(tmp_path)
    before = report(path)
    assert before["undeclared"] == 1
    assert before["pending"] == 1
    assert before["quarantined"] == 0
    quarantine_legacy_rows(path, operator="r", note="n", confirmation=CONFIRMATION)
    after = report(path)
    assert after["pending"] == 0
    assert after["quarantined"] == 1
    assert after["status"] == "CLEAN"


def test_report_of_a_missing_journal(tmp_path):
    result = report(str(tmp_path / "nope.db"))
    assert result["status"] == "DATABASE_MISSING"
    assert result["undeclared"] == 0


def test_quarantined_tickets_of_a_journal_without_the_table(tmp_path):
    path = _journal(tmp_path)
    assert quarantined_tickets(path) == set()


# ── The admission readers ───────────────────────────────────────────────────

def test_quarantined_row_leaves_the_promotion_evidence(tmp_path):
    from research.auto_promotion import AutoPromoter

    path = _journal(tmp_path)
    promoter = AutoPromoter(base_dir=str(tmp_path))
    promoter.db_path = path

    refused = promoter._check_provenance("scalp")
    assert refused["passed"] is False
    assert refused["reason"] == "undeclared_journal_provenance"

    quarantine_legacy_rows(path, operator="reviewer", note="legacy rows",
                           confirmation=CONFIRMATION)

    accepted = promoter._check_provenance("scalp")
    assert accepted["passed"] is True
    assert accepted["journal_sources"] == ["mt5_broker"]
    assert accepted["quarantined_rows"] == 1


def test_quarantined_row_leaves_the_research_dataset(tmp_path):
    from research.research_validation import labelled_dataset

    path = _journal(tmp_path)
    before = labelled_dataset(path)
    assert before["sample_size"] == 2
    assert before["excluded"].get("quarantined_row") is None

    quarantine_legacy_rows(path, operator="reviewer", note="legacy rows",
                           confirmation=CONFIRMATION)

    after = labelled_dataset(path)
    assert after["sample_size"] == 1
    assert after["excluded"]["quarantined_row"] == 1
    assert [row["ticket"] for row in after["rows"]] == [2]
