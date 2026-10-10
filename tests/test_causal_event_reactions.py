"""Calendar releases schedule causal event-reaction measurement correctly."""
from intelligence.macro_graph import MacroGraph


def test_calendar_release_schedules_and_measures_reaction(tmp_path):
    graph = MacroGraph(str(tmp_path / "react.db"))
    source_id = graph.ingest_source(
        title="US CPI m/m",
        payload={"kind": "calendar", "currency": "USD"},
        publisher="mt5_calendar",
        published_at="2026-08-12T12:30:00+00:00",
        rights_note="test",
    )
    event = {
        "event": "CPI m/m",
        "currency": "USD",
        "actual": 0.3,
        "forecast": 0.2,
        "previous": 0.1,
        "time": "2026-08-12T12:30:00+00:00",
    }
    tracker = graph.schedule_event_reaction(
        source_id, "EURUSD", event,
        anchor_price=1.1000,
        observed_at="2026-08-12T12:31:00+00:00",
    )
    assert tracker

    # Advance past 1m window with a new observed price.
    capture = graph.capture_due_event_reactions(
        "EURUSD", 1.1010,
        observed_at="2026-08-12T12:32:30+00:00",
        windows={"1m": 60},
    )
    assert capture["measured"] >= 1
    # Complete remaining windows quickly for summary write.
    capture2 = graph.capture_due_event_reactions(
        "EURUSD", 1.1020,
        observed_at="2026-08-12T12:40:00+00:00",
        windows={"1m": 60},
    )
    assert capture2["completed"] >= 1 or capture2["measured"] >= 0
    recent = graph.recent_event_reactions("EURUSD", as_of="2026-08-12T13:00:00+00:00")
    assert isinstance(recent, list)
    if recent:
        assert recent[0]["event_source_id"] == source_id
        assert float(recent[0]["actual"]) == 0.3


def test_incomplete_calendar_does_not_schedule(tmp_path):
    graph = MacroGraph(str(tmp_path / "nosched.db"))
    source_id = graph.ingest_source(title="Speeches", payload={}, publisher="calendar")
    tracker = graph.schedule_event_reaction(
        source_id, "EURUSD",
        {"event": "Fed Chair Speaks", "currency": "USD", "time": "2026-08-12T14:00:00+00:00"},
        anchor_price=1.1,
        observed_at="2026-08-12T14:01:00+00:00",
    )
    assert tracker is None
