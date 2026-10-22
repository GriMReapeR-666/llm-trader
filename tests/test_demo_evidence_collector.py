"""The demo-account evidence collector.

Every demo fill has to be on record with honest provenance, reconciled
against a record that is not the journal, and counted towards the 200-trade
floor the research gates ask for.  These tests pin that behaviour, including
the parts that are easy to get wrong: a fill the engine never journalled, a
P&L that disagrees with the broker, and a "broker history" that is really
the journal in disguise.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from core.order_execution import PaperBroker
from core.trading_safety import SCALP_MAGIC, TradeProposal, TradingStore, ValidationResult
from intelligence.feature_schema import REQUIRED_VALUES, SCHEMA_HASHES, values_hash
from operations.daily_reconciliation import adapt_paper_deals
from operations.demo_evidence_collector import (
    FILL_CLOSE_EVENT,
    FILL_OPEN_EVENT,
    DemoEvidenceCollector,
    demo_track_record,
    normalize_fill,
)


def _features():
    values = {name: 1.0 for name in REQUIRED_VALUES}
    return {
        "setup_id": "demo_evidence_v1",
        "feature_schema_version": "feature_snapshot_v1",
        "feature_schema_hash": SCHEMA_HASHES["feature_snapshot_v1"],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
        "completed_bar_time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "confluence_score": 4,
    }


def _proposal():
    return TradeProposal("scalp", "EURUSD", "BUY", 1.1000, 1.0990, 1.1020, 0.8, _features())


def _link_intent(store, ticket, setup_key, risk_amount=0.001, magic=SCALP_MAGIC):
    """Link the order intent to the fill, the way the engine does after submission."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(store.path) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS order_intents ("
            "intent_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            " run_id TEXT, proposal_id TEXT, status TEXT NOT NULL, mt5_ticket INTEGER,"
            " payload TEXT NOT NULL, result TEXT, error TEXT)")
        con.execute(
            "INSERT OR REPLACE INTO order_intents"
            "(intent_key, created_at, updated_at, status, mt5_ticket, payload) VALUES(?,?,?,?,?,?)",
            (f"intent-{int(ticket)}", now, now, "PENDING", int(ticket), json.dumps({
                "symbol": "EURUSD", "side": "BUY", "volume": 0.1, "price": 1.1000,
                "sl": 1.0990, "tp": 1.1020, "risk_amount": risk_amount,
                "setup_key": setup_key, "strategy": "scalp", "magic": int(magic),
            })))


def _rows(store, sql, args=()):
    with sqlite3.connect(store.path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, args)]


def _demo_session(tmp_path, name="journal.db", risk_amount=0.001):
    """One demo round trip: proposal, intent, fill, close."""
    store = TradingStore(str(tmp_path / name))
    proposal = _proposal()
    store.log_proposal(proposal, ValidationResult(
        approved=True, reasons=["demo"], confluence_score=4, setup_key=proposal.setup_key()))
    collector = DemoEvidenceCollector(store)
    broker = PaperBroker(journal_callback=None, fill_observer=collector.observe_fill)
    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    ticket = int(result["order"])
    _link_intent(store, ticket, proposal.setup_key(), risk_amount=risk_amount)
    closed = broker.close_position(ticket, 1.1020, magic=SCALP_MAGIC)
    assert closed["retcode"] == 10009
    return store, collector, broker, ticket


def _round_trip(store, broker):
    """One more demo round trip with a decision behind it."""
    proposal = _proposal()
    store.log_proposal(proposal, ValidationResult(
        approved=True, reasons=["demo"], confluence_score=4, setup_key=proposal.setup_key()))
    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    ticket = int(result["order"])
    _link_intent(store, ticket, proposal.setup_key())
    broker.close_position(ticket, 1.1020, magic=SCALP_MAGIC)
    return ticket


def test_demo_fill_is_journalled_with_paper_broker_provenance(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    collector = DemoEvidenceCollector(store)
    broker = PaperBroker(journal_callback=None, fill_observer=collector.observe_fill)

    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    ticket = int(result["order"])

    trades = _rows(store, "SELECT * FROM trades WHERE ticket=?", (ticket,))
    evidence = _rows(store, "SELECT event_type FROM broker_execution_reports WHERE position_ticket=?",
                     (ticket,))
    assert len(trades) == 1
    assert trades[0]["provenance"] == "paper_broker"
    assert trades[0]["entry"] == 1.1000
    assert trades[0]["closed_at"] is None
    # The opening fill is on record too: a position that never closes must
    # still leave a trace.
    assert [row["event_type"] for row in evidence] == [FILL_OPEN_EVENT]


def test_demo_close_is_journalled_labelled_and_evidenced(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    trades = _rows(store, "SELECT * FROM trades WHERE ticket=?", (ticket,))
    labels = _rows(store, "SELECT * FROM trade_labels WHERE ticket=?", (ticket,))
    evidence = _rows(store, "SELECT event_type FROM broker_execution_reports WHERE position_ticket=?",
                     (ticket,))
    assert trades[0]["closed_at"] is not None
    assert trades[0]["exit_price"] == 1.1020
    assert trades[0]["pnl"] > 0
    assert trades[0]["provenance"] == "paper_broker"
    # The exit is named from the venue's own numbers, so the deterministic
    # labeller can tell a target exit from a scratch.
    assert trades[0]["exit_reason"] == "paper venue take profit"
    assert labels[0]["label"] == "WIN_TARGET"
    assert labels[0]["label_rule"] == "exit_reason_target_v1"
    assert {row["event_type"] for row in evidence} == {FILL_OPEN_EVENT, FILL_CLOSE_EVENT}


def test_a_fill_the_engine_never_journalled_is_still_logged(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    collector = DemoEvidenceCollector(store)
    broker = PaperBroker(journal_callback=None, fill_observer=collector.observe_fill)

    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    ticket = int(result["order"])
    broker.close_position(ticket, 1.1020, magic=SCALP_MAGIC)

    trades = _rows(store, "SELECT * FROM trades WHERE ticket=?", (ticket,))
    assert len(trades) == 1
    assert trades[0]["provenance"] == "paper_broker"
    assert trades[0]["closed_at"] is not None
    # No intent means no decision behind the fill: the trade is on record,
    # but the collector says it cannot attribute it.
    assert collector.captured[ticket]["attributed"] is False


def test_a_malformed_fill_is_rejected_and_counted(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    collector = DemoEvidenceCollector(store)

    outcome = collector.observe_fill({"ticket": 0, "symbol": "EURUSD", "volume": 1.0, "price": 1.1})
    assert outcome["status"] == "REJECTED"
    assert len(collector.rejected) == 1
    assert _rows(store, "SELECT * FROM trades") == []


def test_normalize_fill_refuses_a_record_without_a_price():
    assert normalize_fill({"ticket": 7, "volume": 1.0, "price": 0.0}) is None
    assert normalize_fill({"ticket": 7, "volume": 0.0, "price": 1.1}) is None
    record = normalize_fill({"ticket": 7, "volume": 1.0, "price": 1.1, "magic": SCALP_MAGIC})
    assert record is not None
    assert record.ticket == 7
    assert record.status == "OPEN"


def test_reconciliation_against_broker_history_confirms_the_fill(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    report = collector.reconcile_venue_history(broker.history)

    assert report["fills"]["matched"] == [ticket]
    assert report["fills"]["pnl_mismatches"] == []
    assert report["fills"]["unwitnessed"] == []
    assert report["reconciliation"]["status"] == "CLEAN"
    assert report["execution_authority"] is False
    reconciled = _rows(store, "SELECT * FROM broker_execution_reports WHERE event_type='RECONCILED_CLOSE'")
    assert len(reconciled) == 1
    assert reconciled[0]["position_ticket"] == ticket


def test_reconciliation_reports_a_pnl_disagreement(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    report = collector.reconcile(
        [{"magic": SCALP_MAGIC, "position_id": ticket, "entry": 1, "profit": 42.0,
          "commission": 0.0, "swap": 0.0, "time": datetime.now(timezone.utc).timestamp()}])

    assert [item["ticket"] for item in report["fills"]["pnl_mismatches"]] == [ticket]
    assert report["reconciliation"]["status"] == "ATTENTION"
    # A disagreement is an operator work item, never a smoothed-over number.
    reviews = _rows(store, "SELECT * FROM execution_reviews")
    assert reviews and reviews[0]["status"] == "OPEN"


def test_reconciliation_refuses_a_journal_derived_broker_history(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    journal_rows = _rows(store, "SELECT * FROM trades WHERE ticket=?", (ticket,))
    deals = adapt_paper_deals([journal_rows[0]])

    report = collector.reconcile(deals)

    # The journal cannot vouch for itself: no reconciled evidence is minted.
    assert report["reconciliation"]["reconciled_execution_evidence"]["refused_journal_derived"] == 1
    reconciled = _rows(store, "SELECT * FROM broker_execution_reports WHERE event_type='RECONCILED_CLOSE'")
    assert reconciled == []


def test_track_record_measures_progress_against_the_two_hundred_trade_floor(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    report = collector.track_record()

    assert report["target_trades"] == 200
    assert report["closed_fills"] == 1
    assert report["admissible_rows"] == 1
    assert report["remaining"] == 199
    assert report["progress"] == 0.005
    assert report["ready"] is False
    assert report["statistics"]["win_rate"] == 1.0
    assert report["execution_authority"] is False


def test_track_record_is_ready_once_the_floor_is_reached(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)
    collector.target_trades = 1

    report = collector.track_record()

    assert report["admissible_rows"] == 1
    assert report["remaining"] == 0
    assert report["ready"] is True


def test_a_fill_without_decision_context_is_not_research_evidence(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    collector = DemoEvidenceCollector(store)
    broker = PaperBroker(journal_callback=None, fill_observer=collector.observe_fill)
    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    broker.close_position(int(result["order"]), 1.1020, magic=SCALP_MAGIC)

    report = collector.track_record()

    assert report["closed_fills"] == 1
    assert report["admissible_rows"] == 0
    assert report["excluded"].get("unknown_or_legacy_feature_schema") == 1


def test_collector_does_not_overwrite_a_row_the_engine_wrote(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    proposal = _proposal()
    store.log_proposal(proposal, ValidationResult(
        approved=True, reasons=["demo"], confluence_score=4, setup_key=proposal.setup_key()))
    store.transition_latest_setup(proposal.setup_key(), "ACCEPTED", ticket=4242)
    store.record_open_trade(4242, proposal, 0.1, 0.001, {"written_by": "engine"},
                             provenance="paper_broker")
    collector = DemoEvidenceCollector(store)

    collector.observe_fill({"ticket": 4242, "symbol": "EURUSD", "side": "BUY", "volume": 0.1,
                            "price": 1.1000, "sl": 1.0990, "tp": 1.1020, "magic": SCALP_MAGIC})

    trades = _rows(store, "SELECT * FROM trades WHERE ticket=?", (4242,))
    assert json.loads(trades[0]["payload"])["written_by"] == "engine"


def test_paper_broker_reports_every_fill_to_the_observer():
    seen = []
    broker = PaperBroker(fill_observer=seen.append)

    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)
    broker.close_position(int(result["order"]), 1.1020, magic=SCALP_MAGIC)

    assert [record["status"] for record in seen] == ["OPEN", "CLOSED"]


def test_a_failing_observer_cannot_stop_the_venue_from_filling():
    def boom(_record):
        raise RuntimeError("journal unavailable")

    broker = PaperBroker(fill_observer=boom)
    result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0990, 1.1020,
                                 "demo", magic=SCALP_MAGIC)

    assert result["retcode"] == 10009
    assert int(result["order"]) > 0


def test_track_record_can_be_read_without_collecting(tmp_path):
    store, collector, broker, ticket = _demo_session(tmp_path)

    report = demo_track_record(store.path)

    assert report["admissible_rows"] == 1
    assert report["target_trades"] == 200


def test_the_sample_accumulates_across_sessions(tmp_path):
    """A track record is built over many sessions, not one long run.

    The collector holds no state of its own between sessions -- the journal
    is the accumulator -- so a fresh collector over the same journal must
    see the trades an earlier session captured.
    """
    store, collector, broker, ticket = _demo_session(tmp_path)

    later = DemoEvidenceCollector(store)
    later.target_trades = 2
    _round_trip(store, broker)

    report = later.track_record()

    assert report["closed_fills"] == 2
    assert report["admissible_rows"] == 2
    assert report["ready"] is True
    # The earlier session's capture is still counted even though this
    # collector instance witnessed none of it: the journal is the
    # accumulator, not the process.
    assert report["captured_this_session"] == 0


def test_several_fills_move_the_floor_closer(tmp_path):
    store = TradingStore(str(tmp_path / "journal.db"))
    collector = DemoEvidenceCollector(store)
    broker = PaperBroker(journal_callback=None, fill_observer=collector.observe_fill)

    for _ in range(3):
        _round_trip(store, broker)

    report = collector.track_record()

    assert report["closed_fills"] == 3
    assert report["remaining"] == 197
    assert report["progress"] == 0.015
    assert report["statistics"]["count"] == 3
