"""Backfill of historical FRED releases into macro evidence (dry-run + apply).

The backfill must be structurally identical to the live macro_event_evidence
stage: same fact-key gate, same tracker scheduling, same completion contract —
just driven by historical data and historical tick anchors.
"""
import json
import sqlite3

import pytest

from macro_evidence_backfill import (
    BACKFILL_SOURCE_QUALITY,
    backfill_macro_evidence_from_history,
)
from macro_graph import MacroGraph
from macro_observations import ensure_schema as ensure_observations_schema
from trading_memory import TradingMemory

sys_path_roots = ("intelligence", "operations", "core", ".")
import sys
for root in sys_path_roots:
    if root not in sys.path:
        sys.path.insert(0, root)

from core.trading_safety import TradingStore  # noqa: E402  (path setup above)


def _seed_observation(path: str, series: str, date: str, value: float, previous: float,
                      entity: str = "GROWTH") -> None:
    con = sqlite3.connect(path)
    try:
        ensure_observations_schema(path)
        con.execute(
            """INSERT INTO macro_observations(captured_at, entity, provider, series,
               observed_at, value, previous, surprise, source_id, payload, execution_authority)
               VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
            ("2026-09-26T00:00:00+00:00", entity, "FRED", series, date, value, previous,
             value - previous, f"FRED:{series}", "{}"),
        )
        con.commit()
    finally:
        con.close()


def _seed_tick(store: TradingStore, symbol: str, iso: str, bid: float, ask: float) -> None:
    from datetime import datetime, timezone
    stamp = datetime.fromisoformat(iso)
    store.record_market_tick(symbol, bid=bid, ask=ask, source="test",
                             quality="FRESH", observed_at=stamp)


def _fixture(tmp_path):
    db = str(tmp_path / "journal.db")
    store = TradingStore(db)
    operations = AgentOperations(db)
    trade_memory = TradingMemory(db)
    graph = MacroGraph(db)
    return db, store, operations, trade_memory, graph


def _iso(date: str, time: str = "14:00:00") -> str:
    return f"{date}T{time}+00:00"


import sys  # noqa: E402
from agent_operations import AgentOperations  # noqa: E402


def test_dry_run_makes_zero_writes(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "DGS10", "2026-09-01", 4.10, 4.00)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    report = backfill_macro_evidence_from_history(
        operations, trade_memory, graph, "EURUSD", apply=False)

    assert report["releases_scanned"] == 1
    assert report["releases_eligible"] == 1
    assert report["apply"] is False
    # Nothing was written anywhere.
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM macro_sources").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM event_reaction_trackers").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM event_reactions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM atomic_facts").fetchone()[0] == 0


def test_apply_records_event_source_tracker_and_completed_reaction(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "DGS10", "2026-09-01", 4.10, 4.00)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    report = backfill_macro_evidence_from_history(
        operations, trade_memory, graph, "EURUSD", apply=True)

    assert report["releases_eligible"] == 1
    assert report["macro_events_recorded"] == 1
    assert report["sources_ingested"] == 1
    assert report["trackers_scheduled"] == 1
    assert report["reactions_completed"] == 1
    assert report["trackers_still_tracking"] == 0

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        event = con.execute("SELECT * FROM macro_events").fetchone()
        assert event["title"] == "FRED DGS10"
        assert event["currency"] == "USD"
        assert json.loads(event["payload"])["actual"] == 4.10

        source = con.execute("SELECT * FROM macro_sources WHERE payload LIKE '%macro_evidence_backfill%'").fetchone()
        assert source is not None
        source_id = source["source_id"]

        tracker = con.execute("SELECT * FROM event_reaction_trackers").fetchone()
        assert tracker["event_source_id"] == source_id
        assert tracker["status"] == "COMPLETE"
        assert tracker["anchor_price"] == pytest.approx(1.1001)

        reaction = con.execute("SELECT * FROM event_reactions").fetchone()
        assert reaction["event_source_id"] == source_id
        assert reaction["id"] == tracker["final_reaction_id"]
        assert reaction["source_quality"] == BACKFILL_SOURCE_QUALITY
        windows = json.loads(reaction["windows"])
        day = windows["1d"]
        assert day["resolution"] == "daily"
        assert day["observed_price"] == pytest.approx(1.1051)
        assert day["return_fraction"] == pytest.approx((1.1051 - 1.1001) / 1.1001)


def test_fact_key_gate_prevents_double_backfill(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "DGS10", "2026-09-01", 4.10, 4.00)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    first = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert first["macro_events_recorded"] == 1
    second = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert second["macro_events_recorded"] == 0
    assert second["macro_events_already_gated"] == 1
    assert second["releases_eligible"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM event_reactions").fetchone()[0] == 1


def test_zero_change_releases_are_skipped(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "DFEDTARU", "2026-09-01", 4.00, 4.00)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    report = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert report["releases_eligible"] == 0
    assert report["skipped_series"]["zero_change:DFEDTARU"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0


def test_unmapped_series_and_stale_anchors_are_skipped(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "VIXCLS", "2026-09-01", 18.0, 17.0)   # unmapped series
    _seed_observation(db, "DGS2", "2026-09-10", 3.90, 3.80)     # tick arrives >2 days later
    _seed_tick(store, "EURUSD", _iso("2026-09-13", "14:30:00"), 1.0700, 1.0702)
    _seed_tick(store, "EURUSD", _iso("2026-09-14", "14:30:00"), 1.0750, 1.0752)

    report = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert report["skipped_series"]["unmapped_series:VIXCLS"] == 1
    assert report["anchors_skipped_stale"] == 1
    assert report["releases_eligible"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0


def test_no_tick_at_all_leaves_everything_unwritten(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "UNRATE", "2026-09-01", 4.2, 4.1)

    report = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert report["anchors_skipped_no_tick"] == 1
    assert report["releases_eligible"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0


def test_window_without_tick_is_abandoned_never_faked(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "UNRATE", "2026-09-01", 4.2, 4.1)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    # No tick 1 day after the anchor: window unmeasurable.

    report = backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)
    assert report["releases_eligible"] == 1
    assert report["trackers_scheduled"] == 1
    assert report["windows_unmeasured"] == 1
    assert report["trackers_abandoned"] == 1
    assert report["reactions_completed"] == 0
    with sqlite3.connect(db) as con:
        tracker = con.execute("SELECT status FROM event_reaction_trackers").fetchone()
        assert tracker[0] == "ABANDONED"
        assert con.execute("SELECT COUNT(*) FROM event_reactions").fetchone()[0] == 0


def test_abandoned_tracker_cannot_be_claimed_by_a_live_capture_pulse(tmp_path):
    """A backfilled tracker must never be completed from a live spot price.

    capture_due_event_reactions has no upper bound on elapsed time, so a
    TRACKING tracker whose 1d window is unmeasurable gets *every* window
    filled from one current price — a multi-day return recorded as the 1m
    reaction — and that fabricated reaction then reinforces causal edges.
    Retiring the tracker is the only honest terminal state.
    """
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "UNRATE", "2026-09-01", 4.2, 4.1)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)

    backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)

    # A live pulse much later, with a completely different spot price.
    capture = graph.capture_due_event_reactions("EURUSD", 1.5000)
    assert capture["scheduled"] == 0
    assert capture["measured"] == 0
    assert capture["completed"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM event_reactions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM event_reaction_measurements").fetchone()[0] == 0


def test_backfilled_reactions_are_retrievable_and_directional(tmp_path):
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "DGS10", "2026-09-01", 4.10, 4.00)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)

    reactions = graph.recent_event_reactions("EURUSD", limit=10)
    assert len(reactions) == 1
    assert reactions[0]["title"].startswith("FRED level release")
    # surprise = actual - forecast = value - previous = +0.10
    assert reactions[0]["surprise"] == pytest.approx(0.10)

    # And the directional parser accepts the daily-resolution window.
    from autonomous_causal_engine import _reaction_directional_return
    assert _reaction_directional_return(reactions[0]["windows"]) == pytest.approx(
        (1.1051 - 1.1001) / 1.1001)


def test_live_and_backfill_paths_interlock_via_fact_gate(tmp_path):
    """Live stage uses event-monitor calendar rows; simulate one for the same release."""
    db, store, operations, trade_memory, graph = _fixture(tmp_path)
    _seed_observation(db, "UNRATE", "2026-09-01", 4.2, 4.1)
    _seed_tick(store, "EURUSD", _iso("2026-09-01", "14:30:00"), 1.1000, 1.1002)
    _seed_tick(store, "EURUSD", _iso("2026-09-02", "14:30:00"), 1.1050, 1.1052)

    # Backfill first.
    backfill_macro_evidence_from_history(operations, trade_memory, graph, "EURUSD", apply=True)

    # Live-stage equivalent for the same release: same fact-key convention.
    fact_key = "macro_event:2026-09-01T00:00:00+00:00:USD:FRED UNRATE"
    assert trade_memory.retrieve_facts([fact_key], limit=1)  # present -> live stage skips it
    assert trade_memory.retrieve_facts(["macro_event:2026-09-02T00:00:00+00:00:USD:FRED UNRATE"], limit=1) == []
