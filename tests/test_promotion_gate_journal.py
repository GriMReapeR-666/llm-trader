"""Hermetic proof: promotion gate rejections journal a concrete verdict.

Seeds a temp DB with a CANDIDATE strategy and losing trades so the
production AutoPromoter's gates fail — then asserts the new
IMPROVEMENT_PROMOTION_GATE journal event names the failed gates.
Thresholds untouched; this only proves the honest-verdict plumbing.
"""
from __future__ import annotations

import json
import sqlite3

from research.auto_promotion import AutoPromoter


def _make_tmp_env(tmp_path, monkeypatch):
    """Isolated AutoPromoter with a temp DB + temp journal, real gates."""
    db = tmp_path / "trading_state.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE strategy_lifecycle (setup_id TEXT PRIMARY KEY, state TEXT, "
        "version_fingerprint TEXT, updated_at TEXT)"
    )
    con.execute(
        "INSERT INTO strategy_lifecycle (setup_id, state, version_fingerprint, updated_at) "
        "VALUES ('gap3_test_strategy', 'CANDIDATE', '1.0.0', '2026-09-25T12:00:00Z')"
    )
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, ticket INTEGER, symbol TEXT, strategy TEXT, "
        "closed_at TEXT, r_multiple REAL)"
    )
    # 120 losing trades → sample_size passes (>=100) but performance gates fail.
    for i in range(120):
        con.execute(
            "INSERT INTO trades (ticket, symbol, strategy, closed_at, r_multiple) "
            "VALUES (?, 'EURUSD', ?, ?, ?)",
            (100000 + i, "gap3_test_strategy",
             f"2026-09-2{i % 10}T12:00:0{i % 10}Z", -0.4),
        )
    con.commit()
    con.close()

    events = []

    import operations.event_bridge as bridge

    monkeypatch.setattr(
        bridge, "emit",
        lambda et, payload=None, **kw: events.append((et, dict(payload or {}))),
        raising=True,
    )

    promoter = AutoPromoter(base_dir=str(tmp_path))
    promoter.db_path = str(db)
    return promoter, events


def test_gate_failure_journals_concrete_verdict(tmp_path, monkeypatch):
    promoter, events = _make_tmp_env(tmp_path, monkeypatch)

    result = promoter.promote_strategy("gap3_test_strategy")

    # Real gates, unchanged thresholds: must reject with named gates
    assert result["promoted"] is False
    assert result["reason"].startswith("gates_failed:")
    failed = result["reason"].replace("gates_failed: ", "").split(", ")
    assert failed, "no failed gates recorded"

    # The new honest-verdict branch fired with the concrete failed gates
    gate_events = [e for et, e in events if et == "IMPROVEMENT_PROMOTION_GATE"]
    assert gate_events, "gate rejection was not journaled"
    ev = gate_events[-1]
    assert ev["strategy"] == "gap3_test_strategy"
    assert ev["verdict"] == "GATES_FAILED"
    assert ev["failed_gates"] == failed
    assert ev["gate_details"], "failed gate details missing"
    for k in failed:
        assert k in ev["gate_details"]
        assert ev["gate_details"][k]["passed"] is False


def test_gate_failure_event_payload_shape(tmp_path, monkeypatch):
    promoter, events = _make_tmp_env(tmp_path, monkeypatch)
    promoter.promote_strategy("gap3_test_strategy")
    gate_events = [e for et, e in events if et == "IMPROVEMENT_PROMOTION_GATE"]
    assert gate_events
    payload = gate_events[-1]
    assert set(payload.keys()) >= {"strategy", "status", "verdict", "failed_gates", "gate_details"}


def test_promotion_records_gate_evidence_in_audit_trail(tmp_path, monkeypatch):
    """Automated transitions must persist the gate results, not "{}".

    The gate evidence used to live only in artifacts/promotion_state.json, so
    strategy_lifecycle_history.evidence - the table a reviewer actually reads -
    was empty for every auto_promoter transition.
    """
    import sqlite3

    promoter, _ = _make_tmp_env(tmp_path, monkeypatch)
    db = promoter.db_path

    # The shared harness builds a deliberately minimal strategy_lifecycle; the
    # real writer also touches updated_by/reason, so widen it here.
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE strategy_lifecycle ADD COLUMN updated_by TEXT")
    con.execute("ALTER TABLE strategy_lifecycle ADD COLUMN reason TEXT")
    con.execute(
        "CREATE TABLE IF NOT EXISTS strategy_lifecycle_history ("
        "id INTEGER PRIMARY KEY, setup_id TEXT, from_state TEXT, to_state TEXT, "
        "actor TEXT, reason TEXT, evidence TEXT, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS model_registry ("
        "model_key TEXT, version TEXT, kind TEXT, status TEXT, metrics TEXT, "
        "created_at TEXT, reviewed_at TEXT, reviewed_by TEXT, review_note TEXT)"
    )
    con.commit()
    con.close()

    promoter._update_strategy_status(
        "gap3_test_strategy", "PAPER_PROVEN",
        {"sample_size": {"passed": True, "n": 120},
         "profit_factor": {"passed": False, "value": 0.8}})

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM strategy_lifecycle_history WHERE setup_id=? "
        "ORDER BY id DESC LIMIT 1", ("gap3_test_strategy",)).fetchone()
    assert row is not None, "no lifecycle history row written"
    assert row["actor"] == "auto_promoter"
    assert row["to_state"] == "PAPER_PROVEN"

    evidence = json.loads(row["evidence"])
    assert evidence["gates"], "gate evidence missing"
    assert evidence["gates_passed"] == ["sample_size"]
    assert evidence["gates_failed"] == ["profit_factor"]
    assert "1/2 gates passed" in row["reason"]
    assert "profit_factor" in row["reason"]

    mrow = con.execute(
        "SELECT metrics FROM model_registry WHERE model_key=?",
        ("gap3_test_strategy",)).fetchone()
    assert mrow is not None
    assert json.loads(mrow["metrics"])["gates"], "model_registry metrics empty"
    con.close()


def _mark_broker_confirmed(db_path, strategy, count):
    """Give `count` of a strategy's trades independent broker evidence."""
    import sqlite3 as _s
    con = _s.connect(db_path)
    tickets = [r[0] for r in con.execute(
        "SELECT ticket FROM trades WHERE strategy=? LIMIT ?", (strategy, count))]
    con.executemany("INSERT INTO order_intents (mt5_ticket) VALUES (?)",
                    [(t,) for t in tickets])
    con.commit()
    con.close()
    return len(tickets)


def test_gates_are_scoped_to_the_strategy_under_evaluation(tmp_path, monkeypatch):
    """One strategy's trades must never satisfy another strategy's gates.

    The gate queries read the whole journal while ignoring strategy_key, so
    every strategy inherited the 150 `scalp` trades and 32 alpha_* rows were
    promoted to PAPER_PROVEN on someone else's track record.
    """
    import sqlite3

    promoter, _ = _make_tmp_env(tmp_path, monkeypatch)
    con = sqlite3.connect(promoter.db_path)
    # An unrelated strategy's trade, which must never satisfy any gate.
    con.execute(
        "INSERT INTO trades (ticket, symbol, strategy, closed_at, r_multiple) "
        "VALUES (900001, 'EURUSD', 'someone_else', '2026-09-01T00:00:00Z', 2.0)")
    con.commit()
    con.close()

    other = promoter._check_sample_size("alpha_055_orderflow")
    assert other["count"] == 0, other
    assert other["passed"] is False, "unrelated strategy inherited the sample"

    live_other = promoter._check_live_performance("alpha_055_orderflow")
    assert live_other.get("passed") is not True, live_other

    # With no broker reconciliation recorded anywhere, no strategy has evidence
    # at all - including the one that owns the fixture trades.
    own = promoter._check_sample_size("gap3_test_strategy")
    assert own["count"] == 0, own
    assert own["passed"] is False

    # Now record genuine broker evidence and confirm the gates scope to it.
    con = sqlite3.connect(promoter.db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS order_intents (mt5_ticket INTEGER)")
    con.commit()
    con.close()
    _mark_broker_confirmed(promoter.db_path, "gap3_test_strategy", 120)
    _mark_broker_confirmed(promoter.db_path, "someone_else", 1)

    own = promoter._check_sample_size("gap3_test_strategy")
    assert own["count"] == 120, own
    assert own["passed"] is True
    # The other strategy's own reconciled trade still must not leak across.
    still_other = promoter._check_sample_size("alpha_055_orderflow")
    assert still_other["count"] == 0, still_other
