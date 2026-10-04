"""Boot datasheet: one table for at-a-glance boot data health.

Merges three previously-separate signals — bootstrap PREPARE step rows,
boot-fetch watchdog stage outcomes, and APIManager degradation markers —
into a single fixed-width table rendered once per boot and journaled as a
``boot_datasheet`` event. These tests pin: the collector API (record,
render, one-shot render, opt-out, row cap), the bootstrap row recording
(warning matching, bounded-timeout status), the orchestrator fold at the
first pre-pulse (single render + journal event, later pulses silent), and
end-to-end that a hung FRED fetch lands in the rendered table.
"""
import time
from unittest.mock import MagicMock

import pytest

import core.autonomous_bootstrap as bootstrap_mod
from intelligence.boot_datasheet import BootDatasheet, boot_datasheet_enabled, get_datasheet


@pytest.fixture(autouse=True)
def _fresh_sheet(monkeypatch):
    """Isolate the process-lifetime singleton per test.

    Patching ``boot_datasheet._ds_singleton`` covers every consumer
    (bootstrap, orchestrator, APIManager's local imports) because they all
    resolve the global at call time.
    """
    monkeypatch.delenv("LLM_TRADER_BOOT_DATASHEET", raising=False)
    sheet = BootDatasheet()
    from intelligence import boot_datasheet as ds_mod
    monkeypatch.setattr(ds_mod, "_ds_singleton", sheet)
    yield sheet
    sheet.reset()


def _rendered_table() -> str:
    return get_datasheet().render()


# ---------------------------------------------------------------------------
# Collector core: record + render
# ---------------------------------------------------------------------------

def test_render_merges_all_three_kinds():
    sheet = get_datasheet()
    sheet.record_step("2b_macro_seed", 94.5, "OK", "entities=3")
    sheet.record_pulse_stage("realtime_news_ingest", {
        "ok": False, "timed_out": True, "error": "stage exceeded 45s budget",
        "elapsed_s": 45.0, "budget_s": 45.0})
    sheet.record_degraded("fred.get_series", "SKIP", "CPIAUCSL budget/hang")

    table = _rendered_table()
    assert "2b_macro_seed" in table and "94.5" in table
    assert "realtime_news_ingest" in table and "TIMEOUT" in table
    assert "fred.get_series" in table and "SKIP" in table
    assert "steps=1 pulse-stages=1 degraded-markers=1" in table
    lines = table.splitlines()
    assert all(len(line) <= 80 for line in lines)


def test_summary_counts_and_slowest_step():
    sheet = get_datasheet()
    sheet.record_step("a", 1.0, "OK")
    sheet.record_step("b", 30.5, "TIMEOUT", "budget")
    sheet.record_pulse_stage("s1", {"ok": True, "elapsed_s": 0.4})
    sheet.record_degraded("newsapi.get_news", "STALE")
    sheet.record_degraded("newsapi.get_news", "STALE")
    summary = sheet.summary()
    assert summary["steps"] == 2
    assert summary["pulse_stages"] == 1
    assert summary["degraded_markers"] == 2
    assert summary["degraded_sources"] == ["newsapi.get_news"]
    assert summary["slowest_step"]["name"] == "b" and summary["slowest_step"]["elapsed_s"] == 30.5


def test_degraded_marker_cap():
    sheet = get_datasheet()
    for i in range(30):
        sheet.record_degraded("x", "STALE", "row %d" % i)
    assert sheet.summary()["degraded_markers"] == 24


def test_maybe_render_once_is_one_shot(monkeypatch):
    sheet = get_datasheet()
    sheet.record_step("s", 1.0, "OK")
    events = []
    first = sheet.maybe_render_once(emit_event=lambda t, p: events.append((t, p)))
    assert first is not None and "s" in first
    assert events and events[0][0] == "boot_datasheet"
    # Second call: silent no-op.
    sheet.record_step("later", 1.0, "OK")
    assert sheet.maybe_render_once() is None
    assert "later" not in first


def test_emit_event_failure_never_fatal():
    sheet = get_datasheet()
    sheet.record_step("s", 1.0, "OK")

    def boom(t, p):
        raise RuntimeError("journal down")
    assert sheet.maybe_render_once(emit_event=boom) is not None  # still returns table


def test_env_opt_out(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_BOOT_DATASHEET", "0")
    assert boot_datasheet_enabled() is False
    monkeypatch.setenv("LLM_TRADER_BOOT_DATASHEET", "1")
    assert boot_datasheet_enabled() is True


# ---------------------------------------------------------------------------
# Bootstrap row recording
# ---------------------------------------------------------------------------

def test_record_prepare_rows_maps_status_elapsed_and_notes():
    bootstrap_mod.record_prepare_rows(
        details={
            "macro_graph": {"status": "INITIALIZED", "entities": 3},
            "eia_petroleum": {"status": "SKIPPED"},
            "data_sources": {"status": "ERROR", "error": "catalog down"},
        },
        step_elapsed={
            "2b_macro_seed": 94.5, "2c6_eia": 0.2, "2a_data_sources": 1.1},
        warnings=["EIA: service down"])
    table = _rendered_table()
    assert "2b_macro_seed" in table and "OK" in table
    assert "2c6_eia" in table and "SKIP" in table
    assert "2a_data_sources" in table and "catalog down" in table


def test_record_prepare_rows_budget_warning_becomes_timeout():
    bootstrap_mod.record_prepare_rows(
        details={"global_macro_bridge": {"status": "SKIPPED"}},
        step_elapsed={"2c2_macro_bridge": 30.1},
        warnings=["GlobalMacroBridge: stage exceeded 30s budget; failing open (hung provider?)"])
    table = _rendered_table()
    assert "2c2_macro_bridge" in table
    assert "TIMEOUT" in table
    assert "exceeded 30s budget" in table


def test_record_prepare_rows_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(bootstrap_mod, "boot_datasheet_enabled", lambda: False)
    bootstrap_mod.record_prepare_rows(
        details={"macro_graph": {"status": "OK"}}, step_elapsed={"2b_macro_seed": 1.0},
        warnings=[])
    assert get_datasheet().summary()["steps"] == 0


# ---------------------------------------------------------------------------
# Orchestrator fold: single render at first pre-pulse
# ---------------------------------------------------------------------------

def _orchestrator_with(monkeypatch, api_manager=None):
    from intelligence import llm_orchestrator as orch_mod
    from intelligence.llm_orchestrator import LLMOrchestrator

    from data import market_data_provider as mdp
    monkeypatch.setattr(
        mdp, "collect_calendar_event_rows", lambda calendar, client: ([], None))
    monkeypatch.setattr(
        orch_mod, "process_cycle_data_sources",
        lambda **kwargs: {"observations": {}, "macro_entities_extra": []})
    monkeypatch.setattr(
        orch_mod, "run_realtime_news_ingest", lambda **kwargs: {"macro_entities_extra": []})

    orch = MagicMock(spec=LLMOrchestrator)
    orch.data_provider = MagicMock()
    orch.data_provider.calendar = None
    orch.data_provider.client = None
    orch.data_provider.api_manager = api_manager
    orch.store = MagicMock()
    orch.store.path = "trading_state.db"
    orch.macro_graph = MagicMock()
    orch.macro_graph.ingest_sources_bulk = lambda items: ["s-%d" % i for i in range(len(items))]
    orch.operations = MagicMock()
    orch.pre_pulse_macro_ingest = (
        LLMOrchestrator.pre_pulse_macro_ingest.__get__(orch, LLMOrchestrator))
    orch._emit_boot_event = (
        LLMOrchestrator._emit_boot_event.__get__(orch, LLMOrchestrator))
    return orch


def test_first_pulse_folds_outcomes_and_renders_once(monkeypatch):
    orch = _orchestrator_with(monkeypatch)
    events = []
    monkeypatch.setattr(
        orch, "_emit_boot_event",
        lambda et, p, **kw: events.append((et, p, kw.get("severity"))))

    entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)

    stages = entry["boot_stage_outcomes"]
    assert "realtime_news_ingest" in stages  # the one stage that runs without calendar
    # Table rendered exactly once with the pulse rows folded in.
    sheet = get_datasheet()
    assert sheet.summary()["pulse_stages"] == len(stages)
    assert events and events[0][0] == "boot_datasheet"
    assert events[0][2] == "INFO"
    # Second pulse: no second render/event.
    events.clear()
    orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
    assert events == []


def test_hung_fred_get_series_marks_datasheet(monkeypatch):
    # End-to-end: a hung FRED series fetch fails open (bounded) and records
    # a degradation marker that lands in the rendered table.
    from data.api_manager import APIManager

    monkeypatch.delenv("NEWSAPI_KEYS", raising=False)
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEYS", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    mgr = APIManager()
    mgr.fred = MagicMock()
    mgr.fred.get_series = lambda *a, **kw: time.sleep(5)
    # 5s hang must exceed the bounded budget to be abandoned (default is 20s).
    monkeypatch.setenv("FRED_BUDGET_S", "1")

    started = time.monotonic()
    results = mgr.get_fred_data(["CPIAUCSL"])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert results == {}
    sheet = get_datasheet()
    assert sheet.summary()["degraded_markers"] >= 1
    table = sheet.render()
    assert "fred.get_series" in table
    assert "SKIP" in table
