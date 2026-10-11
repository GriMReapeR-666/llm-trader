"""The contextual bandit must work end-to-end once broker evidence exists.

The live journal yields zero usable rows because every trade in it is an
unbrokered fixture - correct fail-closed behaviour. These tests build a
fixture that DOES satisfy the evidence contract and assert the bandit actually
learns, so "no data" can never be confused with "broken bandit".
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intelligence.feature_schema import SCHEMA_HASHES, values_hash
from research.contextual_bandit import bandit_report
from research.research_validation import labelled_dataset

SCHEMA_VERSION = "feature_snapshot_v1"
# Two arms that make money, two that lose it.
PLAN = {"fvg_fill": 1.4, "breakout_retest": 0.9, "mean_rev": -1.2, "news_fade": -0.8}
PROFITABLE = sorted(["fvg_fill", "breakout_retest"])


def _build_evidenced_db(path, n=200):
    """Trades with independent broker evidence, which is what the gate requires."""
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE trades(ticket INTEGER PRIMARY KEY, magic INTEGER, strategy TEXT,
      symbol TEXT, side TEXT, volume REAL, entry REAL, stop_loss REAL,
      take_profit REAL, risk_amount REAL, opened_at TEXT, closed_at TEXT,
      exit_price REAL, pnl REAL, exit_reason TEXT, payload TEXT NOT NULL, setup_id TEXT);
    CREATE TABLE trade_labels(ticket INTEGER PRIMARY KEY, label TEXT,
      r_multiple REAL, label_rule TEXT);
    CREATE TABLE order_intents(intent_key TEXT PRIMARY KEY, created_at TEXT,
      updated_at TEXT, run_id TEXT, proposal_id TEXT, status TEXT, mt5_ticket INTEGER);
    """)
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)
    schema_hash = SCHEMA_HASHES[SCHEMA_VERSION]
    setups = list(PLAN)
    for i in range(n):
        setup_id = setups[i % len(setups)]
        r = PLAN[setup_id] + ((i % 7) - 3) * 0.15
        opened = base + timedelta(hours=i)
        closed = opened + timedelta(hours=2)
        fv = {"rsi": 50.0 + (i % 11), "atr": 0.002}
        payload = {
            "setup_id": setup_id,
            "proposal_features": {
                "feature_schema_version": SCHEMA_VERSION,
                "feature_schema_hash": schema_hash,
                "feature_values": fv,
                "feature_values_hash": values_hash(fv),
                "completed_bar_time": (opened - timedelta(minutes=5)).isoformat(),
                "source": "live_broker",
            },
        }
        ticket = 5000 + i
        con.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ticket, 1, setup_id, "EURUSD", "BUY", 0.1, 1.10, 1.095, 1.110,
                     50.0, opened.isoformat(), closed.isoformat(), 1.10 + r * 0.005,
                     r * 50.0, "TP", json.dumps(payload), setup_id))
        con.execute("INSERT INTO trade_labels VALUES(?,?,?,?)",
                    (ticket, "WIN" if r > 0 else "LOSS", r, "r_multiple>=0"))
        # The independent broker-evidence record the dataset gate demands.
        con.execute("INSERT INTO order_intents VALUES(?,?,?,?,?,?,?)",
                    (f"int_{ticket}", opened.isoformat(), closed.isoformat(),
                     "r1", f"p{ticket}", "FILLED", ticket))
    con.commit()
    con.close()


def test_broker_evidence_is_admitted(tmp_path):
    """With order_intents.mt5_ticket present, rows must NOT be excluded."""
    db = str(tmp_path / "evidenced.db")
    _build_evidenced_db(db)
    ds = labelled_dataset(db)
    assert ds.get("excluded") == {}, f"unexpected exclusions: {ds.get('excluded')}"
    assert len(ds["rows"]) == 200


def test_missing_broker_evidence_is_excluded(tmp_path):
    """Same trades WITHOUT order_intents must all be excluded, not admitted.

    This is the fail-closed guard: a row merely claiming source="broker" is not
    evidence, only the journal's own submission record is.
    """
    db = str(tmp_path / "noevidence.db")
    _build_evidenced_db(db)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE order_intents")
    con.commit(); con.close()

    ds = labelled_dataset(db)
    assert ds["rows"] == []
    assert ds["excluded"].get("no_broker_reconciliation_source") == 1


def test_bandit_learns_and_ranks_setups(tmp_path):
    """The bandit must take the profitable arms and skip the losing ones."""
    db = str(tmp_path / "evidenced.db")
    _build_evidenced_db(db)
    rep = bandit_report(db, train_minimum=100, test_size=40, step=40,
                        min_context_samples=5)

    assert rep["status"] == "READY_FOR_REVIEW"
    assert rep["out_of_sample_status"] == "EVALUATED_PURGED_WALK_FORWARD"
    assert rep["folds"], "no folds produced despite 200 eligible rows"

    final = rep["folds"][-1]
    taken = sorted({d["setup_id"] for d in final["decisions"] if d["action"] == "TAKE"})
    assert taken == PROFITABLE, f"bandit ranked incorrectly: {taken}"
    assert final["selected_expectancy_r"] > 0, "selected arms were not profitable"


def test_bandit_never_claims_authority(tmp_path):
    """Even a fully evaluated report must stay advisory."""
    db = str(tmp_path / "evidenced.db")
    _build_evidenced_db(db)
    rep = bandit_report(db, train_minimum=100, test_size=40, step=40)
    assert rep["execution_authority"] is False
    assert rep["promotion_safe"] is False


def test_insufficient_data_is_reported_not_guessed(tmp_path):
    """Too few rows must return INSUFFICIENT_DATA, never a fabricated ranking."""
    db = str(tmp_path / "small.db")
    _build_evidenced_db(db, n=20)
    rep = bandit_report(db, train_minimum=100, test_size=10, step=10)
    assert rep["status"] == "INSUFFICIENT_DATA"
    assert rep["folds"] == []
    assert rep["out_of_sample_status"] == "NOT_AVAILABLE"


def test_orchestrator_does_not_import_the_bandit():
    """The live pulse must not pull in the offline bandit.

    bandit_report is an operator-invoked research artifact that returns
    promotion_safe=False by construction. Importing it into the orchestrator
    implies an influence on trading it must never have.
    """
    import pathlib
    src = pathlib.Path("intelligence/llm_orchestrator.py").read_text(encoding="utf-8")
    live_imports = [
        line for line in src.splitlines()
        if line.startswith("from research.contextual_bandit")
        or line.startswith("import research.contextual_bandit")
    ]
    assert live_imports == [], f"bandit imported into the live pulse: {live_imports}"