"""Boot fetch watchdog: per-source deadlines on the pre-pulse news/indicators
ingest path so a hung provider can never stall the first trading cycle.

Covers the helper (budget resolution, success/exception/timeout outcomes,
journal-event emission) and the orchestrator wiring (a hung provider fails
open within its budget and the session proceeds on degraded data; a healthy
provider still populates the ingest cache end to end).
"""
import time
from unittest.mock import MagicMock

import pytest

from intelligence.boot_fetch_watchdog import run_stage_with_deadline, stage_budgets


# ---------------------------------------------------------------------------
# Helper: budget resolution
# ---------------------------------------------------------------------------

def test_stage_budgets_defaults(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_BOOT_STAGE_BUDGETS", raising=False)
    budgets = stage_budgets()
    assert budgets["calendar_collect"] == 30.0
    assert budgets["data_source_pipeline"] == 45.0
    assert budgets["realtime_news_ingest"] == 90.0
    assert budgets["bulk_calendar_ingest"] == 30.0


def test_stage_budgets_env_override_and_garbage(monkeypatch):
    monkeypatch.setenv(
        "LLM_TRADER_BOOT_STAGE_BUDGETS",
        "calendar_collect=10, realtime_news_ingest=20, bogus=abc, nope=15")
    budgets = stage_budgets()
    assert budgets["calendar_collect"] == 10.0
    assert budgets["realtime_news_ingest"] == 20.0
    assert budgets["data_source_pipeline"] == 45.0   # untouched default
    assert "nope" not in budgets                      # unknown name reported, dropped
    assert set(budgets) == {"calendar_collect", "bulk_calendar_ingest",
                            "data_source_pipeline", "realtime_news_ingest"}


# ---------------------------------------------------------------------------
# Helper: outcomes
# ---------------------------------------------------------------------------

def test_success_outcome_carries_result_and_timing():
    outcome = run_stage_with_deadline(
        "calendar_collect", lambda: [{"event": "NFP"}],
        budgets={"calendar_collect": 5.0})
    assert outcome["ok"] is True
    assert outcome["result"] == [{"event": "NFP"}]
    assert outcome["error"] is None
    assert outcome["timed_out"] is False
    assert outcome["elapsed_s"] < 5.0


def test_exception_inside_stage_is_contained():
    def boom():
        raise RuntimeError("provider 500")

    outcome = run_stage_with_deadline("data_source_pipeline", boom,
                                      budgets={"data_source_pipeline": 5.0})
    assert outcome["ok"] is False
    assert outcome["timed_out"] is False
    assert "provider 500" in outcome["error"]


def test_hung_stage_is_abandoned_at_budget_and_fails_open():
    events = []

    def hang():
        time.sleep(5)

    started = time.monotonic()
    outcome = run_stage_with_deadline(
        "realtime_news_ingest", hang,
        budgets={"realtime_news_ingest": 0.5},
        emit_event=lambda et, p: events.append((et, p)))
    elapsed = time.monotonic() - started

    assert elapsed < 3.0                    # the caller is NOT blocked 5s
    assert outcome["timed_out"] is True
    assert outcome["ok"] is False
    assert "abandoned" in outcome["error"]
    assert events and events[0][0] == "boot_fetch_stage_timeout"
    assert events[0][1]["stage"] == "realtime_news_ingest"


def test_event_emitter_failure_is_swallowed():
    def hang():
        time.sleep(2)

    outcome = run_stage_with_deadline(
        "calendar_collect", hang,
        budgets={"calendar_collect": 0.3},
        emit_event=lambda et, p: (_ for _ in ()).throw(RuntimeError("journal down")))
    assert outcome["timed_out"] is True     # emitter failure must not raise


# ---------------------------------------------------------------------------
# Orchestrator wiring: hung provider fails open, healthy path unchanged
# ---------------------------------------------------------------------------

def _orchestrator_with(macros=None, monkeypatch=None):
    """Spec-bound orchestrator mock like test_risk_management_hardening.py.

    The heavy pipeline symbols are stubbed at the orchestrator-module level so
    the tests exercise the watchdog wiring, not FRED/NewsAPI network code.
    """
    from intelligence import llm_orchestrator as orch_mod
    from intelligence.llm_orchestrator import LLMOrchestrator

    if monkeypatch is not None:
        monkeypatch.setattr(
            orch_mod, "process_cycle_data_sources",
            lambda **kwargs: {"observations": {}, "macro_entities_extra": []})
        monkeypatch.setattr(
            orch_mod, "run_realtime_news_ingest",
            lambda **kwargs: {"macro_entities_extra": []})

    orch = MagicMock(spec=LLMOrchestrator)
    orch.data_provider = MagicMock()
    orch.data_provider.calendar = None
    orch.data_provider.client = None
    orch.data_provider.api_manager = None
    orch.store = MagicMock()
    orch.store.path = "trading_state.db"
    orch.macro_graph = MagicMock() if macros else None
    if macros:
        orch.macro_graph.ingest_sources_bulk = lambda items: ["src-%d" % i for i in range(len(items))]
    orch.operations = MagicMock()
    # Bind the real method (spec mock has no real behavior).
    orch.pre_pulse_macro_ingest = (
        LLMOrchestrator.pre_pulse_macro_ingest.__get__(orch, LLMOrchestrator))
    orch._emit_boot_event = (
        LLMOrchestrator._emit_boot_event.__get__(orch, LLMOrchestrator))
    return orch


def test_hung_calendar_bridge_fails_open_within_budget(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_BOOT_STAGE_BUDGETS", "calendar_collect=0.5")
    orch = _orchestrator_with(macros=True, monkeypatch=monkeypatch)

    from data import market_data_provider as mdp

    def hung_calendar(calendar, client):
        time.sleep(5)
        return [], None

    monkeypatch.setattr(mdp, "collect_calendar_event_rows", hung_calendar)

    started = time.monotonic()
    entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0                    # no 20-minute stall
    assert entry["boot_stage_outcomes"]["calendar_collect"]["timed_out"] is True
    assert entry["macro_source_ids"] == []  # degraded but present
    # WARNING journaled through the operations event bus (scan all calls:
    # the boot datasheet's boot_datasheet event may fire after the timeout).
    assert orch.operations.event.called
    assert any(
        "boot_fetch_stage_timeout" in c.args
        for c in orch.operations.event.call_args_list)
    # the boot datasheet's boot_datasheet event may fire after the timeout).


def test_healthy_path_populates_cache_with_stage_outcomes(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_BOOT_STAGE_BUDGETS", raising=False)
    orch = _orchestrator_with(macros=True, monkeypatch=monkeypatch)

    from data import market_data_provider as mdp
    monkeypatch.setattr(
        mdp, "collect_calendar_event_rows",
        lambda calendar, client: ([{"event": "NFP", "currency": "USD",
                                    "time": "2030-01-01T00:00:00Z",
                                    "source": "test"}], None))

    entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)

    assert entry["macro_source_ids"]            # bulk ingest ran
    assert "USD" in entry["macro_entities"]
    outcomes = entry["boot_stage_outcomes"]
    assert outcomes["calendar_collect"]["ok"] is True, outcomes["calendar_collect"]
    assert outcomes["bulk_calendar_ingest"]["ok"] is True, outcomes["bulk_calendar_ingest"]
    assert outcomes["data_source_pipeline"]["ok"] is True, outcomes["data_source_pipeline"]
    assert outcomes["realtime_news_ingest"]["ok"] is True, outcomes["realtime_news_ingest"]
    assert not orch.operations.event.called     # no timeout warnings


def test_no_data_provider_still_returns_cache_entry():
    orch = _orchestrator_with()
    orch.data_provider = None
    entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
    assert entry["symbol"] == "EURUSD"
    assert entry["boot_stage_outcomes"] == {}
    assert orch._pulse_macro_cache["EURUSD"] == entry
