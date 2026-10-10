"""Provider scorecard: per-boot source verdicts + cross-session trend.

Turns the boot datasheet's fresh/degraded markers into one
``provider_scorecard`` journal event per boot: per-source FRESH/STALE/
SKIP/NONE/ABORT/UNSEEN verdicts, counts, degraded sources, and a trend
vs the previous boot's scorecard (WORSE/BETTER/SAME/NEW/GONE per source,
``regressed`` overall). Tests pin: verdict derivation (fresh, zero-count
NONE, degraded-wins, worst-outcome-wins), UNSEEN known sources, trend
movement + regression, journal loading (round-trip through a real
AgentOperations DB), severity, the rendered text, and the orchestrator's
one-shot emission tied to the datasheet render (later pulses silent,
best-effort failures never fatal).
"""
import json
from unittest.mock import MagicMock

import pytest

import core.autonomous_bootstrap as bootstrap_mod
import intelligence.provider_scorecard as psc
from intelligence.boot_datasheet import BootDatasheet
from intelligence.provider_scorecard import (
    KNOWN_API_SOURCES, build_event, derive_verdicts, load_recent_summaries,
    render, scorecard, severity_for, trend_vs_previous)


@pytest.fixture(autouse=True)
def _fresh_sheet(monkeypatch):
    """Isolate the process-lifetime datasheet singleton per test."""
    sheet = BootDatasheet()
    from intelligence import boot_datasheet as ds_mod
    monkeypatch.setattr(ds_mod, "_ds_singleton", sheet)
    monkeypatch.delenv("LLM_TRADER_BOOT_DATASHEET", raising=False)
    yield sheet
    sheet.reset()


# ---------------------------------------------------------------------------
# Verdict derivation
# ---------------------------------------------------------------------------

def test_fresh_and_degraded_rows_map_to_verdicts():
    records = [
        {"kind": "fresh", "name": "fred.get_series", "status": "FRESH", "note": "CPIAUCSL (as of 2026-09-29)"},
        {"kind": "fresh", "name": "newsapi.get_news", "status": "FRESH", "note": "5 articles"},
        {"kind": "degraded", "name": "newsapi.get_hot_topics", "status": "STALE", "note": "budget/hang; stale cache served"},
        {"kind": "step", "name": "2b_macro_seed", "status": "OK", "note": ""},  # ignored
    ]
    verdicts = derive_verdicts(records)
    assert verdicts == {
        "fred.get_series": "FRESH", "newsapi.get_news": "FRESH",
        "newsapi.get_hot_topics": "STALE"}


def test_zero_count_fresh_note_is_none_not_fresh():
    records = [{"kind": "fresh", "name": "newsapi.get_news", "status": "FRESH", "note": "0 articles"}]
    assert derive_verdicts(records)["newsapi.get_news"] == "NONE"


def test_degraded_wins_over_fresh_and_worst_outcome_wins():
    records = [
        {"kind": "fresh", "name": "fred.get_series", "status": "FRESH", "note": "3 (as of x)"},
        {"kind": "degraded", "name": "fred.get_series", "status": "STALE", "note": "later failure"},
        {"kind": "degraded", "name": "fred.get_series", "status": "SKIP", "note": "hung series"},
        {"kind": "degraded", "name": "fred.get_series", "status": "ABORT", "note": "net abort"},
    ]
    assert derive_verdicts(records)["fred.get_series"] == "ABORT"


def test_unknown_sources_surface_as_unseen():
    result = scorecard(
        [{"kind": "fresh", "name": "fred.get_series", "status": "FRESH", "note": "2 (as of x)"}],
        known_sources=KNOWN_API_SOURCES)
    assert result["verdicts"]["newsapi.get_news"] == "UNSEEN"
    assert result["verdicts"]["newsapi.get_hot_topics"] == "UNSEEN"
    assert result["verdicts"]["fred.get_series"] == "FRESH"
    assert result["counts"] == {"FRESH": 1, "UNSEEN": 2}
    assert result["degraded_sources"] == [
        "newsapi.get_hot_topics", "newsapi.get_news"]


# ---------------------------------------------------------------------------
# Trend vs previous boot
# ---------------------------------------------------------------------------

def test_trend_movement_directions():
    prev = {"verdicts": {"a": "FRESH", "b": "STALE", "c": "STALE", "d": "FRESH"}, "counts": {"FRESH": 2, "STALE": 2}}
    cur = {"verdicts": {"a": "STALE", "b": "STALE", "c": "FRESH", "d": "FRESH", "e": "FRESH"}, "counts": {"FRESH": 3, "STALE": 1}}
    trend = trend_vs_previous(cur, prev)
    mv = trend["movement"]
    assert mv["a"] == "WORSE" and mv["c"] == "BETTER" and mv["b"] == "SAME"
    assert mv["e"] == "NEW" and "d" in trend["movement"]
    assert trend["regressed"] is False  # score 1 vs previous 2
    assert trend["boot_score"] == 1 and trend["prev_boot_score"] == 2


def test_trend_regression_and_unavailable_trend():
    prev = {"verdicts": {"a": "FRESH"}, "counts": {"FRESH": 1}}
    cur = {"verdicts": {"a": "STALE"}, "counts": {"STALE": 1}}
    trend = trend_vs_previous(cur, prev)
    assert trend["regressed"] is True and trend["worse"] == ["a"]
    assert trend_vs_previous(cur, None) == {"available": False}


def test_unseen_becomes_fresh_counts_as_improvement():
    prev = {"verdicts": {"newsapi.get_news": "UNSEEN"}, "counts": {"UNSEEN": 1}}
    cur = {"verdicts": {"newsapi.get_news": "FRESH"}, "counts": {"FRESH": 1}}
    trend = trend_vs_previous(cur, prev)
    assert trend["movement"]["newsapi.get_news"] == "BETTER"
    assert trend["regressed"] is False


# ---------------------------------------------------------------------------
# Journal round-trip + severity
# ---------------------------------------------------------------------------

def _ops(tmp_path):
    import sys
    for p in ("operations", "."):
        if p not in sys.path:
            sys.path.append(p)
    from operations.agent_operations import AgentOperations
    return AgentOperations(str(tmp_path / "ops.db"))


def test_scorecard_event_round_trips_through_journal(tmp_path):
    ops = _ops(tmp_path)
    severity, payload = build_event(
        scorecard([{"kind": "degraded", "name": "fred.get_series", "status": "STALE", "note": "x"}],
                  known_sources=KNOWN_API_SOURCES),
        {"available": False})
    ops.event("orchestrator", "provider_scorecard", payload, severity=severity)

    with ops._con() as con:
        recent = load_recent_summaries(con, limit=5)
    assert len(recent) == 1
    summary = recent[0]["summary"]
    assert summary["verdicts"]["fred.get_series"] == "STALE"
    assert summary["counts"]["UNSEEN"] == 2


def test_scorecard_event_severity_warning_on_regression(tmp_path):
    ops = _ops(tmp_path)
    # Previous boot: all fresh.
    _, prev_payload = build_event(
        scorecard([{"kind": "fresh", "name": "fred.get_series", "status": "FRESH", "note": "1 (as of x)"}]),
        {"available": False})
    ops.event("orchestrator", "provider_scorecard", prev_payload, severity="INFO")
    # This boot: degraded -> trend sees the previous event and regressed=True.
    current = scorecard([{"kind": "degraded", "name": "fred.get_series", "status": "STALE", "note": "x"}])
    trend = trend_vs_previous(current, prev_payload["summary"])
    severity, payload = build_event(current, trend)
    assert severity == "WARNING" and trend["regressed"] is True
    ops.event("orchestrator", "provider_scorecard", payload, severity=severity)
    with ops._con() as con:
        recent = load_recent_summaries(con, limit=2)
        raw = json.loads(con.execute(
            "SELECT payload FROM operational_events WHERE event_type='provider_scorecard' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()["payload"])
    assert recent[0]["summary"]["counts"] == {"STALE": 1}  # newest = this boot
    assert raw["trend"]["worse"] == ["fred.get_series"]


def test_severity_info_without_regression():
    current = {"verdicts": {"a": "STALE"}, "counts": {"STALE": 1}}
    assert severity_for(current, {"available": False}) == "INFO"
    assert severity_for(current, {"available": True, "regressed": False}) == "INFO"
    assert severity_for(current, {"available": True, "regressed": True}) == "WARNING"


# ---------------------------------------------------------------------------
# Render + orchestrator emission
# ---------------------------------------------------------------------------

def test_render_lists_sources_and_movement():
    current = {"verdicts": {"fred.get_series": "FRESH", "newsapi.get_news": "STALE"},
               "counts": {"FRESH": 1, "STALE": 1}}
    trend = {"available": True, "movement": {"fred.get_series": "BETTER", "newsapi.get_news": "WORSE"}}
    text = render(current, trend)
    assert "PROVIDER SCORECARD" in text
    assert "fred.get_series" in text and "FRESH" in text
    assert "(WORSE vs prev boot)" in text and "(BETTER vs prev boot)" in text


def _orchestrator_with(monkeypatch):
    from intelligence import llm_orchestrator as orch_mod
    from intelligence.llm_orchestrator import LLMOrchestrator

    from data import market_data_provider as mdp
    monkeypatch.setattr(mdp, "collect_calendar_event_rows", lambda calendar, client: ([], None))
    monkeypatch.setattr(orch_mod, "process_cycle_data_sources",
                        lambda **kwargs: {"observations": {}, "macro_entities_extra": []})
    monkeypatch.setattr(orch_mod, "run_realtime_news_ingest",
                        lambda **kwargs: {"macro_entities_extra": []})

    orch = MagicMock(spec=LLMOrchestrator)
    orch.data_provider = MagicMock()
    orch.data_provider.calendar = None
    orch.data_provider.client = None
    orch.data_provider.api_manager = None
    orch.store = MagicMock()
    orch.store.path = "trading_state.db"
    orch.macro_graph = MagicMock()
    orch.macro_graph.ingest_sources_bulk = lambda items: ["s-%d" % i for i in range(len(items))]
    orch.operations = MagicMock()
    orch.operations._con = None  # no journal access in unit tests -> trend unavailable
    orch.pre_pulse_macro_ingest = (
        LLMOrchestrator.pre_pulse_macro_ingest.__get__(orch, LLMOrchestrator))
    orch._emit_boot_event = (
        LLMOrchestrator._emit_boot_event.__get__(orch, LLMOrchestrator))
    orch._emit_provider_scorecard = (
        LLMOrchestrator._emit_provider_scorecard.__get__(orch, LLMOrchestrator))
    return orch


def test_first_pulse_emits_scorecard_once(monkeypatch):
    orch = _orchestrator_with(monkeypatch)
    events = []
    monkeypatch.setattr(orch, "_emit_boot_event",
                        lambda et, p, **kw: events.append((et, kw.get("severity"))))
    monkeypatch.setattr(psc, "load_recent_summaries", lambda con, limit=5: [])

    orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
    sc = [e for e in events if e[0] == "provider_scorecard"]
    assert len(sc) == 1 and sc[0][1] == "INFO"  # no previous -> INFO
    # Second pulse: datasheet already rendered -> no second scorecard.
    events.clear()
    orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
    assert [e for e in events if e[0] == "provider_scorecard"] == []


def test_scorecard_emission_never_fatal(monkeypatch):
    orch = _orchestrator_with(monkeypatch)

    def boom(et, p, **kw):
        raise RuntimeError("journal down")
    monkeypatch.setattr(orch, "_emit_boot_event", boom)
    monkeypatch.setattr(psc, "load_recent_summaries",
                        lambda con, limit=5: (_ for _ in ()).throw(RuntimeError("db closed")))

    entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)  # must not raise
    assert "boot_stage_outcomes" in entry


def test_trend_reads_previous_boot_from_real_journal(tmp_path, monkeypatch):
    ops = _ops(tmp_path)
    _, prev_payload = build_event(
        scorecard([{"kind": "fresh", "name": "fred.get_series", "status": "FRESH", "note": "1 (as of x)"}]),
        {"available": False})
    ops.event("orchestrator", "provider_scorecard", prev_payload, severity="INFO")

    sheet = BootDatasheet()
    sheet.record_degraded("fred.get_series", "STALE", "x")
    current = scorecard(sheet.records(), known_sources=KNOWN_API_SOURCES)
    with ops._con() as con:
        recent = load_recent_summaries(con, limit=2)
    trend = trend_vs_previous(current, recent[0]["summary"])
    assert trend["regressed"] is True and trend["worse"] == ["fred.get_series"]


def test_trend_query_failure_degrades_to_unavailable():
    class BrokenCon:
        def execute(self, *a, **kw):
            raise RuntimeError("db locked")
    assert load_recent_summaries(BrokenCon()) == []
