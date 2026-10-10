from pathlib import Path
import sqlite3

from research.research_validation import promotion_gate, setup_metrics
from intelligence.feature_schema import FEATURE_SCHEMA_VERSION, SCHEMA_HASHES, values_hash
from intelligence.trading_memory import TradingMemory
from core.trading_safety import TradeProposal, TradingStore


def _mark_broker_submitted(path: str, tickets) -> None:
    """Record the journal's own submission evidence for these tickets.

    Admissible research evidence needs an independent broker record. A trade
    row that merely asserts ``source="broker"`` is written by the same party
    that would forge it, so it proves nothing. In a real journal this comes
    from the order-execution path; the fixtures below need it explicitly to
    reach the gate each test is actually exercising.
    """
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


def test_promotion_metrics_exclude_unlabelled_closed_rows(tmp_path: Path):
    path = str(tmp_path / "research.db")
    store = TradingStore(path)
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
        {"setup_id": "legacy-test", "feature_schema_version": "legacy"},
    )
    store.record_open_trade(1, proposal, 1.0, 0.01, {"features": {}})
    store.record_close(1, 1.05, 0.01, "target")

    assert setup_metrics(path) == []
    report = promotion_gate(path)
    assert report["dataset"]["sample_size"] == 0
    assert report["eligible_setups"] == []
    assert report["allowed_to_influence_execution"] is False


def test_labelled_dataset_excludes_nonfinite_point_in_time_features(tmp_path: Path):
    path = str(tmp_path / "nonfinite-research.db")
    store = TradingStore(path)
    values = {"close": float("nan")}
    features = {
        "setup_id": "nan-setup",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8, features,
    )
    store.record_open_trade(7, proposal, 1.0, 1.0, {"proposal_features": features})
    store.record_close(7, 1.2, 1.0, "target")
    TradingMemory(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status) VALUES(?,?,?,?,?,?,?)",
            (7, "WIN", "target", 1.0, "2026-01-01T00:00:00+00:00", "deterministic_v1", "AUTO"),
        )

    _mark_broker_submitted(path, [7])

    from research.research_validation import labelled_dataset
    result = labelled_dataset(path)
    assert result["sample_size"] == 0
    assert result["excluded"]["nonfinite_or_malformed_point_in_time_values"] == 1


def test_labelled_dataset_excludes_future_feature_timestamp(tmp_path: Path):
    path = str(tmp_path / "future-feature.db")
    store = TradingStore(path)
    values = {"close": 1.1}
    features = {
        "setup_id": "future-setup",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
        "completed_bar_time": "2099-01-01T00:00:00+00:00",
    }
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8, features,
    )
    store.record_open_trade(8, proposal, 1.0, 1.0, {"proposal_features": features})
    store.record_close(8, 1.2, 1.0, "target")
    TradingMemory(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status) VALUES(?,?,?,?,?,?,?)",
            (8, "WIN", "target", 1.0, "2026-01-01T00:00:00+00:00", "deterministic_v1", "AUTO"),
        )

    _mark_broker_submitted(path, [8])

    from research.research_validation import labelled_dataset
    result = labelled_dataset(path)
    assert result["sample_size"] == 0
    assert result["excluded"]["future_feature_timestamp"] == 1


def test_labelled_dataset_excludes_synthetic_fixture_rows(tmp_path: Path):
    """Random fixture rows must never satisfy sample/promotion evidence."""
    from research.research_validation import labelled_dataset
    from research.demo_sample_generator import inject_samples

    path = str(tmp_path / "synthetic.db")
    injected = inject_samples(path, count=6)
    assert injected["injected"] > 0

    result = labelled_dataset(path)
    assert result["sample_size"] == 0
    # The generator's journal has no reconciliation source at all, so it is
    # refused wholesale -- a stronger refusal than the marker test alone.
    assert result["excluded"].get("no_broker_reconciliation_source") == 1

    # Defense in depth: even a hand-crafted payload with only the source
    # marker, or only the reserved exit reason, is refused.
    import json as _json
    raw = str(tmp_path / "manual.db")
    con = sqlite3.connect(raw)
    con.execute("""CREATE TABLE trades (
        ticket INTEGER PRIMARY KEY, magic INTEGER, strategy TEXT, symbol TEXT,
        side TEXT, volume REAL, entry REAL, stop_loss REAL, take_profit REAL,
        risk_amount REAL, opened_at TEXT, closed_at TEXT, exit_price REAL,
        pnl REAL, exit_reason TEXT, payload TEXT NOT NULL, setup_id TEXT)""")
    con.execute("""CREATE TABLE trade_labels (
        ticket INTEGER PRIMARY KEY, label TEXT, rationale TEXT, r_multiple REAL,
        created_at TEXT, label_rule TEXT, review_status TEXT)""")
    values = {"close": 1.1}
    features = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }
    rows = [
        (11, '{"source": "synthetic_demo_generator"}', "synthetic_close"),
        (12, _json.dumps({"proposal_features": {**features, "source": "demo_sample_generator"}}), "target"),
        (13, "{}", "synthetic_close"),
    ]
    for ticket, payload, reason in rows:
        con.execute(
            """INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticket, 1, "scalp", "EURUSD", "BUY", 0.1, 1.1, 1.0, 1.3,
             10.0, "2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00",
             1.2, 5.0, reason, payload, "trend_follow"))
        con.execute(
            "INSERT INTO trade_labels VALUES (?,?,?,?,?,?,?)",
            (ticket, "WIN", "r", 1.0, "2026-01-01T01:00:00+00:00",
             "deterministic_v1", "AUTO"))
    con.commit()
    con.close()

    # Broker-link them so the SYNTHETIC gate is what refuses them, proving the
    # marker check still works independently of the reconciliation gate.
    _mark_broker_submitted(raw, [11, 12, 13])

    manual = labelled_dataset(raw)
    assert manual["sample_size"] == 0
    assert manual["excluded"]["synthetic_row_excluded"] == 3


def test_labelled_dataset_requires_independent_broker_evidence(tmp_path: Path):
    """A self-declared 'broker' source is not evidence.

    Every field on a trade row is written by the party that would forge it, so
    a fully schema-conformant payload claiming source="broker" must still be
    refused until the journal independently records the submission.
    """
    import json as _json
    path = str(tmp_path / "self-declared.db")
    store = TradingStore(path)
    values = {"close": 1.1}
    features = {
        "setup_id": "self-declared",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8, features,
    )
    store.record_open_trade(21, proposal, 1.0, 10.0, {
        "source": "broker",
        "proposal_features": {**features, "source": "mt5_live_broker"},
    })
    store.record_close(21, 1.2, 15.0, "target")
    TradingMemory(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status) VALUES(?,?,?,?,?,?,?)",
            (21, "WIN", "target", 1.5, "2026-01-01T00:00:00+00:00", "deterministic_v1", "AUTO"),
        )

    from research.research_validation import labelled_dataset
    refused = labelled_dataset(path)
    assert refused["sample_size"] == 0
    assert refused["excluded"]["unbrokered_trade"] == 1

    # Once the journal records the submission, the same row is admissible --
    # the gate tracks evidence, not suspicion.
    _mark_broker_submitted(path, [21])
    admitted = labelled_dataset(path)
    assert admitted["sample_size"] == 1
    assert admitted["rows"][0]["r_multiple"] == 1.5


def test_inject_samples_refuses_journal_with_real_trades(tmp_path: Path):
    """The generator may never mix fixtures into a broker-reconciled journal."""
    from research.demo_sample_generator import inject_samples

    path = str(tmp_path / "real-journal.db")
    store = TradingStore(path)
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8, {"setup_id": "real"},
    )
    store.record_open_trade(500, proposal, 1.0, 10.0, {})
    store.record_close(500, 1.2, 10.0, "target")

    with __import__("pytest").raises(ValueError):
        inject_samples(path, count=3)

    injected = inject_samples(path, count=3, allow_mixed_journal=True)
    assert injected["injected"] == 300  # 3 setups x max(100, 3//3) floor

    # Clean throwaway journals remain allowed without the override.
    clean = str(tmp_path / "clean.db")
    assert inject_samples(clean, count=3)["injected"] == 300
