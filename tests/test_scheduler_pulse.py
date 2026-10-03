from mt5_trader import LLMOrchestrator
from macro_graph import MacroGraph


class _Operations:
    def __init__(self, scheduled):
        self.scheduled = scheduled
        self.claim_calls = 0
        self.events = []

    def claim_due_schedules(self):
        self.claim_calls += 1
        return list(self.scheduled)

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def agent_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _Store:
    path = ":memory:"

    def __init__(self):
        self.states = {}

    def set_state(self, key, value):
        self.states[key] = value

    def get_state(self, key, default=None):
        # Mirrors the real TradingStore contract used by scheduled stages.
        return self.states.get(key, default)


class _Calendar:
    def get_events(self, **kwargs):
        return [{"event": "CPI", "importance": 3, "currency": "USD"}]


class _MacroGraph:
    def ingest_source(self, **kwargs):
        return "macro:test-source"


class _TradeMemory:
    def memory_quality_metrics(self):
        return {"approved": 1, "candidates": 2}

    def consolidate_weekly_core(self):
        return {"status": "NO_DATA", "execution_authority": False}

    def rebuild_scenarios(self):
        return 0

    def candidate_review_queue(self, limit=20):
        return {
            "status": "EMPTY",
            "counts": {"narrative": 0, "verified": 0, "core": 0},
            "items": [],
            "execution_authority": False,
        }


def test_due_schedules_are_claimed_by_pulse_helper_even_without_candle():
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Operations([])
    instance.run_id = "run"
    instance._process_due_schedules("XAUUSD", True)
    assert instance.operations.claim_calls == 1


def test_event_monitor_persists_timestamped_macro_state(tmp_path):
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Operations([{"schedule_name": "event_monitor", "payload": {}}])
    store = _Store()
    store.path = str(tmp_path / "event_mon.db")
    instance.store = store
    instance.data_provider = type("Provider", (), {"calendar": _Calendar(), "api_manager": None, "client": None})()
    instance.macro_graph = MacroGraph(str(tmp_path / "event_graph.db"))
    instance.trade_memory = _TradeMemory()
    instance.client = None
    instance.run_id = "run"
    instance._get_symbol_keywords = lambda symbol: [symbol, "USD"]
    instance._process_due_schedules("XAUUSD", True)
    state = instance.store.states["event_monitor:XAUUSD"]
    assert state["status"] == "AVAILABLE"
    assert state["high_impact_count"] == 1
    assert len(state["source_ids"]) >= 1
    assert state["execution_authority"] is False
    assert "data_source_pipeline" in state
    assert "realtime_news_ingest" in state


def test_self_improvement_schedule_is_advisory_only(tmp_path, monkeypatch):
    # The executor registry intercepts self_improvement; bypass it so the
    # legacy handler chain (which writes to store) runs for this test.
    monkeypatch.setattr(
        "operations.executors.try_handle", lambda *a, **kw: False,
    )
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Operations([{"schedule_name": "self_improvement", "payload": {"kind": "self_improvement"}}])
    store = _Store()
    store.path = str(tmp_path / "improve.db")
    instance.store = store
    instance.trade_memory = _TradeMemory()
    instance.risk = type("Risk", (), {"risk_per_trade": 0.01})()
    instance.enable_rl = False
    instance.rl_trainer = None
    instance.run_id = "run"
    instance._process_due_schedules("EURUSD", True)
    payload = instance.store.states["last_self_improvement"]
    assert payload["execution_authority"] is False
    assert payload["memory_quality"]["approved"] == 1
    assert payload["kronos_rl"]["status"] == "SKIPPED"
    assert "profitability" in payload or "profitability_error" in payload
    assert payload.get("profitability", {}).get("execution_authority", False) is False
    assert payload["memory_review_queue"]["execution_authority"] is False


def test_weekly_research_persists_research_sprint(tmp_path, monkeypatch):
    # The executor registry intercepts weekly_research; bypass it so the
    # legacy handler chain (which writes to store) runs for this test.
    monkeypatch.setattr(
        "operations.executors.try_handle", lambda *a, **kw: False,
    )
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Operations([{"schedule_name": "weekly_research", "payload": {"kind": "research"}}])
    store = _Store()
    store.path = str(tmp_path / "weekly.db")
    instance.store = store
    instance.trade_memory = _TradeMemory()
    instance.run_id = "run"
    instance._process_due_schedules("EURUSD", True)
    sprint = instance.store.states["last_research_sprint"]
    assert sprint["execution_authority"] is False
    assert sprint.get("ticket_count", 0) >= 1 or sprint.get("status") in {"ERROR", "PLANNED"} or "run_id" in sprint


# ---------------------------------------------------------------------------
# Schedule-batch runner: single-flight, bounded pulse wait, per-handler budgets
# ---------------------------------------------------------------------------

import threading as _th_mod
import time as _time_mod


def _bounded_instance(monkeypatch, claim_items, handler_fn):
    """LLMOrchestrator skeleton with single-flight state + executor stub.

    Uses the _SchedOps stub (list_schedules + claim-release/completion
    recording) so wave tests can assert claim releases and dispatch
    completions alongside _Operations.
    """
    monkeypatch.setattr("operations.executors.try_handle",
                        lambda name, payload, context=None: handler_fn(name))
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    # Enrich claims with restore-identity fields (as the real claim returns).
    enriched = []
    for i, item in enumerate(claim_items):
        row = dict(item)
        row.setdefault("scheduled_for", f"2026-09-27T00:00:0{i}+00:00")
        row.setdefault("claimed_at", "2026-09-27T00:00:30+00:00")
        row.setdefault("last_run_at", None)
        row.setdefault("schedule_state", "ON_CADENCE")
        enriched.append(row)
    instance.operations = _SchedOps(enriched, registry=[])
    instance.run_id = "run"
    instance._schedule_batch_running = False
    instance._schedule_thread_lock = _th_mod.Lock()
    return instance


def test_schedule_batch_single_flight_and_flag_release(monkeypatch):
    """A pulse arriving while a batch runs must skip dispatch, and the flag
    must release when the batch finishes so later pulses dispatch again."""
    release = _th_mod.Event()

    def handler(name):
        release.wait(timeout=5.0)   # batch stays running until the test allows
        return True

    instance = _bounded_instance(
        monkeypatch, [{"schedule_name": "market_health", "payload": {}}], handler)
    instance._SCHEDULE_WAIT = 0.3   # pulse returns while the batch still runs

    t0 = _time_mod.time()
    instance._run_schedules_bounded("XAUUSD", True)   # dispatch + bounded wait
    first = _time_mod.time() - t0
    assert first < 2.0, f"pulse blocked {first:.2f}s; bounded wait regressed"
    assert instance._schedule_batch_running, "batch should be running in background"

    # Pulse arriving while the batch runs must SKIP, not stack a second worker.
    t1 = _time_mod.time()
    instance._run_schedules_bounded("XAUUSD", True)
    skip = _time_mod.time() - t1
    assert skip < 0.4, f"re-dispatch blocked {skip:.2f}s; single-flight missing"
    assert instance.operations.claim_calls == 1, "skipped pulse still claimed schedules"

    # Worker finishes -> flag must release -> fresh pulse dispatches again.
    release.set()
    deadline = _time_mod.time() + 5.0
    while _time_mod.time() < deadline and instance._schedule_batch_running:
        _time_mod.sleep(0.05)
    assert not instance._schedule_batch_running, "single-flight flag leaked"
    instance._run_schedules_bounded("XAUUSD", True)
    assert instance.operations.claim_calls >= 2, "flag release did not re-enable dispatch"


def test_overrunning_handler_defers_only_itself(monkeypatch):
    """Wave semantics (2026-09-27): a handler exceeding its soft budget is
    recorded and the wave CONTINUES — the rest of the wave no longer pays
    batch-boundary latency for one slow handler."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "slow_one":
            _time_mod.sleep(0.8)   # budget below is 0.2s
        return True

    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "slow_one", "payload": {}},
         {"schedule_name": "fast_one", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow_one": 0.2}

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["slow_one", "fast_one"], \
        "wave must continue past an overrunning handler"
    overruns = _overrun_events(instance)
    assert len(overruns) == 1 and overruns[0][0][2]["schedule"] == "slow_one"

    # Flag release after the wave: next dispatch proceeds.
    deadline = _time_mod.time() + 5.0
    while _time_mod.time() < deadline and instance._schedule_batch_running:
        _time_mod.sleep(0.05)
    assert not instance._schedule_batch_running


def test_pulse_wait_returns_before_long_batch_completes(monkeypatch):
    """The pulse proceeds after _SCHEDULE_WAIT even while the batch runs on."""
    release = _th_mod.Event()

    def handler(name):
        release.wait(timeout=5.0)   # batch blocks until the test allows it
        return True

    instance = _bounded_instance(monkeypatch, [{"schedule_name": "market_health", "payload": {}}], handler)
    instance._SCHEDULE_WAIT = 0.3

    t0 = _time_mod.time()
    instance._run_schedules_bounded("XAUUSD", True)
    waited = _time_mod.time() - t0
    assert waited < 2.0, f"pulse waited {waited:.2f}s instead of _SCHEDULE_WAIT"
    assert instance._schedule_batch_running, "batch should still be running in background"

    release.set()
    deadline = _time_mod.time() + 5.0
    while _time_mod.time() < deadline and instance._schedule_batch_running:
        _time_mod.sleep(0.05)
    assert not instance._schedule_batch_running


def _market_health_instance(tmp_path, store):
    """LLMOrchestrator skeleton wired for the legacy market_health handler."""
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Operations([{"schedule_name": "market_health", "payload": {}}])
    store.path = str(tmp_path / "mh.db")
    instance.store = store
    instance.data_provider = type(
        "Provider", (), {"calendar": None, "api_manager": None, "client": None,
                          "check_data_sources": lambda self=None: {"status": "OK"}})()
    instance.macro_graph = None
    instance.client = None
    instance.run_id = "run"
    return instance


def _seed_outbox(path, rows):
    """Seed the alert_outbox table directly (schema from alert_outbox._connect)."""
    import sqlite3
    from alert_outbox import _connect
    con = _connect(path)
    try:
        con.executemany(
            "INSERT INTO alert_outbox(fingerprint, channel, code, severity, status,"
            " attempts, next_attempt_at, created_at, updated_at, payload)"
            " VALUES (?, 'operator', ?, 'WARNING', ?, 0, '', ?, ?, '{}')",
            rows,
        )
        con.commit()
    finally:
        con.close()


def test_market_health_report_embeds_outbox_health(tmp_path):
    """The market_health report carries operator-facing PENDING/EXPIRED counts."""
    store = _Store()
    instance = _market_health_instance(tmp_path, store)
    _seed_outbox(store.path, [
        ("fp_pending_1", "DATABASE_MISSING", "PENDING", "2026-09-26T08:00:00+00:00", "2026-09-26T08:00:00+00:00"),
        ("fp_sent_1", "OLD_ALERT", "SENT", "2026-09-26T07:00:00+00:00", "2026-09-26T07:05:00+00:00"),
    ])
    instance._process_due_schedules("XAUUSD", True)
    health = store.states["last_market_health"]
    assert health["outbox_health"]["status"] == "OK"
    # The seeded db exists, so evaluate() finds no DATABASE_MISSING alert and
    # enqueue stays a no-op: counts are exactly the seeded rows.
    assert health["outbox_health"]["counts"]["PENDING"] == 1
    assert health["outbox_health"]["counts"]["SENT"] == 1
    assert health["outbox_health"]["oldest_pending_at"] == "2026-09-26T08:00:00+00:00"
    assert health["outbox_health"]["execution_authority"] is False
    # The enqueue step also ran (evaluate() DATABASE_MISSING alert landed).
    assert health["alert_outbox"]["execution_authority"] is False


def test_market_health_outbox_health_flags_backlog_as_warn(tmp_path):
    """A stalled consumer (PENDING pile) surfaces as WARN, not silently OK."""
    store = _Store()
    instance = _market_health_instance(tmp_path, store)
    _seed_outbox(store.path, [
        (f"fp_pending_{i}", "ALPHA_REVERSED", "PENDING",
         "2026-09-26T08:00:00+00:00", "2026-09-26T08:00:00+00:00")
        for i in range(60)
    ])
    instance._process_due_schedules("XAUUSD", True)
    outbox = store.states["last_market_health"]["outbox_health"]
    assert outbox["status"] == "WARN"
    assert outbox["pending"] == 60


def test_market_health_outbox_health_survives_store_error(tmp_path, monkeypatch):
    """An outbox read failure degrades to an ERROR entry, never kills the scan."""
    store = _Store()
    instance = _market_health_instance(tmp_path, store)

    def _boom(path):
        raise RuntimeError("outbox read failed")

    # Patch the module that owns LLMOrchestrator's globals (dual-import trap:
    # mt5_trader re-exports the class; its handler reads the defining module's
    # namespace, not mt5_trader's).
    import sys
    owner = sys.modules[LLMOrchestrator.__module__]
    monkeypatch.setattr(owner, "alert_outbox_health", _boom, raising=False)
    instance._process_due_schedules("XAUUSD", True)
    health = store.states["last_market_health"]
    assert health["outbox_health"]["status"] == "ERROR"
    assert "outbox read failed" in health["outbox_health"]["error"]
    # The rest of the health report is still complete.
    assert "data_status" in health and "alerts" in health
    assert health["execution_authority"] is False


def _overrun_events(instance):
    """Extract persisted schedule_handler_overrun events from the stub ops."""
    return [e for e in instance.operations.events
            if e[0] and len(e[0]) > 2 and e[0][1] == "schedule_handler_overrun"]


def test_schedule_overrun_persists_event_and_stats(monkeypatch):
    """A budget-exceeding handler is recorded (event + rolling stats), not
    only used to trim the batch — durable evidence for budget tuning."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "slow_one":
            _time_mod.sleep(0.8)   # budget below is 0.5s (guard floor)
        return True

    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "slow_one", "payload": {}},
         {"schedule_name": "fast_one", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow_one": 0.5}

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["slow_one", "fast_one"], "wave continues past the overrun"
    overruns = _overrun_events(instance)
    assert len(overruns) == 1, "exactly one overrun event expected"
    args, kwargs = overruns[0]
    payload = args[2]
    assert payload["schedule"] == "slow_one"
    assert payload["elapsed_s"] > payload["budget_s"]
    assert payload["over_by_s"] > 0.0
    assert payload["execution_authority"] is False
    assert kwargs.get("severity") == "WARNING"
    stats = instance._schedule_overrun_stats["slow_one"]
    assert stats["overruns"] == 1
    assert stats["worst_over_by_s"] > 0.0
    assert stats["last_budget_s"] == 0.5


def test_last_handler_overrun_is_recorded_at_batch_end(monkeypatch):
    """A handler that trips its budget on the LAST claimed schedule still
    leaves an overrun event — no silent drop at loop exhaustion."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "slow_last":
            _time_mod.sleep(0.8)   # budget below is 0.5s (guard floor)
        return True

    # Fast lane: equal budget lanes keep claim order, so slow_last still
    # dispatches LAST and its overrun is caught at batch end.
    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "fast_first", "payload": {}},
         {"schedule_name": "slow_last", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"fast_first": 0.5, "slow_last": 0.5}

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["fast_first", "slow_last"], \
        "a single last-handler overrun must not abandon an already-empty batch"
    overruns = _overrun_events(instance)
    assert len(overruns) == 1, "batch-end catch-up must record the overrun"
    assert overruns[0][0][2]["schedule"] == "slow_last"
    assert instance._schedule_overrun_stats["slow_last"]["overruns"] == 1


def test_wave_records_each_handler_overrun_exactly_once(monkeypatch):
    """Two overrunning handlers in one wave each leave their own overrun
    event — the per-window latch reset must not swallow the second one."""
    handled = []

    def handler(name):
        handled.append(name)
        if name in ("slow_a", "slow_b"):
            _time_mod.sleep(0.8)
        return True

    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "slow_a", "payload": {}},
         {"schedule_name": "slow_b", "payload": {}},
         {"schedule_name": "fast_tail", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow_a": 0.5, "slow_b": 0.5}

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["slow_a", "slow_b", "fast_tail"]
    overruns = _overrun_events(instance)
    assert [e[0][2]["schedule"] for e in overruns] == ["slow_a", "slow_b"], \
        "each overrunning handler recorded exactly once, in order"
    assert instance._schedule_overrun_stats["slow_a"]["overruns"] == 1
    assert instance._schedule_overrun_stats["slow_b"]["overruns"] == 1


def test_wave_deadline_defers_remainder_with_skip_event_and_restore(monkeypatch):
    """The ONLY remaining abandonment is the wave deadline: handlers already
    dispatched are done, the rest are recorded as skipped, restored to their
    pre-claim due points, and re-claimed next wave."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "hog":
            _time_mod.sleep(0.8)
        return True

    # Fast lane: the hog budget is set EQUAL to the default (45s) so the
    # sort keeps claim order — the wave deadline, not the budget, does the
    # cutting here.
    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "first", "payload": {}},
         {"schedule_name": "hog", "payload": {}},
         {"schedule_name": "deferred", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"hog": 45.0}  # == default: equal lane
    instance._SCHEDULE_TIMEOUT = 0.4                    # wave deadline

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["first", "hog"], "deadline defers the remainder"
    skips = _events_of(instance, "schedule_skipped")
    assert len(skips) == 1
    assert skips[0][0][2]["schedules"] == ["deferred"]
    assert skips[0][0][2]["dispatched"] == 2
    assert skips[0][0][2]["reason"] == "wave_deadline"
    # Cadence preserved for the deferred schedule only (claim released;
    # dispatched items completed inline).
    assert len(instance.operations.releases) == 1
    assert [r["schedule_name"] for r in instance.operations.releases[0]] == ["deferred"]
    assert ("first", instance.operations.scheduled[0].get("claimed_at"), "COMPLETED") \
        in instance.operations.completions


def test_wave_within_deadline_records_no_skip_event(monkeypatch):
    """A normal wave (even with overruns) emits no schedule_skipped and no
    restore — deferral only happens at the deadline."""
    def handler(name):
        if name == "slow":
            _time_mod.sleep(0.8)
        return True

    # Fast lane: the 0.5s budget sorts slow ahead of after's default-45s
    # budget lane — the wave continues past the overrun either way.
    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "slow", "payload": {}},
         {"schedule_name": "after", "payload": {}}],
        handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow": 0.5}

    instance._process_due_schedules("XAUUSD", True)
    assert _events_of(instance, "schedule_skipped") == []
    assert instance.operations.releases == []


def test_fast_lane_dispatches_short_interval_schedules_before_the_deadline(monkeypatch):
    """FAST-LANE (2026-09-28): dispatch order is shortest-cadence-first, so a
    wave-deadline hit defers the slowest cadence — never the 60s fast lanes.
    The claim order below is the historical heavy-first order: without the
    fast lane the 0.8s hog exhausts the deadline and BOTH 60s schedules are
    deferred; with it they dispatch before the deadline can bite."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "hog_900":
            _time_mod.sleep(0.8)
        return True

    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "hog_900", "payload": {}},
         {"schedule_name": "fast_a", "payload": {}},
         {"schedule_name": "fast_b", "payload": {}},
         {"schedule_name": "heavy_last", "payload": {}}],
        registry=[
            {"schedule_name": "hog_900", "interval_seconds": 900, "enabled": 1},
            {"schedule_name": "fast_a", "interval_seconds": 60, "enabled": 1},
            {"schedule_name": "fast_b", "interval_seconds": 60, "enabled": 1},
            {"schedule_name": "heavy_last", "interval_seconds": 86400, "enabled": 1},
        ],
        handler_fn=handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"hog_900": 5.0}   # hog does NOT trip
    instance._SCHEDULE_TIMEOUT = 0.4

    instance._process_due_schedules("XAUUSD", True)
    # Fast lanes dispatched first (claim order is the tiebreak inside the
    # 60s lane), then the 15-min hog; the deadline cut the daily schedule.
    assert handled == ["fast_a", "fast_b", "hog_900"], \
        f"fast lanes must dispatch before the deadline, got {handled}"
    skips = _events_of(instance, "schedule_skipped")
    assert len(skips) == 1
    assert skips[0][0][2]["schedules"] == ["heavy_last"]
    assert skips[0][0][2]["dispatched"] == 3
    assert skips[0][0][2]["reason"] == "wave_deadline"
    # Dispatch order changed, claim semantics did not: exactly the deferred
    # heavy claim is released, every dispatched claim completed inline.
    assert [r["schedule_name"] for r in instance.operations.releases[0]] == ["heavy_last"]
    assert {c[0] for c in instance.operations.completions} == \
        {"fast_a", "fast_b", "hog_900"}


def test_event_monitor_heavy_pipeline_runs_sub_cadence(monkeypatch):
    """The heavy pipeline block (FRED/news/hot-topics/graph ingest/realtime)
    runs on a sub-cadence inside event_monitor: the first dispatch runs it
    (pipeline_mode=full), the next dispatch inside the window defers it
    (calendar_only payload), and the heavy functions are reached through the
    orchestrator module globals so failures there never stall the wave."""
    import sys as _sys

    # The orchestrator module binds these names at import time; the test
    # process imports it as "llm_orchestrator" (top-level aliasing of the
    # intelligence/ module).
    import llm_orchestrator as orch_mod
    assert orch_mod is not None, "orchestrator module must be importable for patching"

    calls = []

    def _fake_pipeline(**kwargs):
        calls.append("pipeline")
        return {"status": "PROCESSED"}

    def _fake_rn(**kwargs):
        calls.append("rn")
        return {"status": "PROCESSED"}

    monkeypatch.setattr(orch_mod, "process_cycle_data_sources", _fake_pipeline)
    monkeypatch.setattr(orch_mod, "run_realtime_news_ingest", _fake_rn)

    def _mk(last_ts):
        # handler_fn's return value IS try_handle's return: the executor
        # registry short-circuits the legacy elif chain when True. False lets
        # the event_monitor body — and its sub-cadence gate — execute.
        inst = _watchdog_instance(
            monkeypatch,
            [{"schedule_name": "event_monitor", "payload": {}}],
            registry=[], handler_fn=lambda name: False)
        inst._event_monitor_last_full_ts = last_ts
        inst.store = _Store()
        inst.data_provider = type("P", (), {"calendar": None, "api_manager": None,
                                             "client": None})()
        inst.macro_graph = None
        inst.client = None
        inst.trade_memory = None
        return inst

    # First dispatch: stamp=0 -> the heavy block RUNS.
    inst = _mk(0.0)
    inst._process_due_schedules("EURUSD", True)
    assert calls == ["pipeline", "rn"], "first pass must run the heavy block"
    state = inst.store.states.get("event_monitor:EURUSD") or {}
    assert (state.get("data_source_pipeline") or {}).get("pipeline_mode") == "full"
    assert inst._event_monitor_last_full_ts > 0.0, "successful full pass must stamp"

    # Second dispatch inside the window: heavy block DEFERRED (calendar-only).
    calls.clear()
    inst2 = _mk(inst._event_monitor_last_full_ts)
    inst2._process_due_schedules("EURUSD", True)
    assert calls == [], "second pass inside the window must defer the heavy block"
    pipe2 = (inst2.store.states.get("event_monitor:EURUSD") or {}).get(
        "data_source_pipeline") or {}
    assert pipe2.get("pipeline_mode") == "calendar_only"
    assert pipe2.get("status") == "DEFERRED"
    assert pipe2.get("execution_authority") is False
    # The stamp must be carried over untouched by the deferred dispatch.
    assert inst2._event_monitor_last_full_ts == inst._event_monitor_last_full_ts


def test_event_monitor_split_windows_alternate(monkeypatch):
    """EVENT_MONITOR_SPLIT_WINDOWS=1 halves the heavy block into alternating
    windows: even windows run the graph pipeline half (realtime-news
    deferred), odd windows run realtime-news (pipeline deferred). Each window
    still stamps and advances the counter, so the halves alternate A/B/A."""
    import sys as _sys

    monkeypatch.setenv("EVENT_MONITOR_SPLIT_WINDOWS", "1")
    import llm_orchestrator as orch_mod
    assert orch_mod is not None, "orchestrator module must be importable for patching"

    calls = []

    def _fake_pipeline(**kwargs):
        calls.append("pipeline")
        return {"status": "PROCESSED"}

    def _fake_rn(**kwargs):
        calls.append("rn")
        return {"status": "PROCESSED"}

    monkeypatch.setattr(orch_mod, "process_cycle_data_sources", _fake_pipeline)
    monkeypatch.setattr(orch_mod, "run_realtime_news_ingest", _fake_rn)

    def _mk(last_ts, window_no=0):
        # handler_fn returns False so the event_monitor body executes.
        inst = _watchdog_instance(
            monkeypatch,
            [{"schedule_name": "event_monitor", "payload": {}}],
            registry=[], handler_fn=lambda name: False)
        inst._event_monitor_last_full_ts = last_ts
        inst._event_monitor_window_no = window_no
        inst.store = _Store()
        inst.data_provider = type("P", (), {"calendar": None, "api_manager": None,
                                            "client": None})()
        inst.macro_graph = None
        inst.client = None
        inst.trade_memory = None
        return inst

    # Window 0 (even): the PIPELINE half runs, realtime-news is deferred.
    inst = _mk(0.0)
    inst._process_due_schedules("EURUSD", True)
    assert calls == ["pipeline"], "even window must run the pipeline half only"
    state = inst.store.states.get("event_monitor:EURUSD") or {}
    pipe = state.get("data_source_pipeline") or {}
    assert pipe.get("pipeline_mode") == "pipeline_window"
    assert pipe.get("split_windows") is True
    rn = state.get("realtime_news_ingest") or {}
    assert rn.get("status") == "DEFERRED" and rn.get("reason") == "split_windows"
    assert inst._event_monitor_last_full_ts > 0.0, "window ran, so it must stamp"
    assert inst._event_monitor_window_no == 1

    # Window 1 (odd): the REALTIME-NEWS half runs, pipeline deferred.
    # last_ts stepped back 400s to simulate the elapsed 300s window.
    calls.clear()
    inst2 = _mk(inst._event_monitor_last_full_ts - 400.0, window_no=1)
    inst2._process_due_schedules("EURUSD", True)
    assert calls == ["rn"], "odd window must run realtime-news only"
    state2 = inst2.store.states.get("event_monitor:EURUSD") or {}
    pipe2 = state2.get("data_source_pipeline") or {}
    assert pipe2.get("pipeline_mode") == "realtime_window"
    assert pipe2.get("split_windows") is True
    rn2 = state2.get("realtime_news_ingest") or {}
    assert rn2.get("pipeline_mode") == "realtime_window"
    assert inst2._event_monitor_window_no == 2

    # Back to even: pipeline half again (A/B/A alternation).
    calls.clear()
    inst3 = _mk(inst2._event_monitor_last_full_ts - 400.0, window_no=2)
    inst3._process_due_schedules("EURUSD", True)
    assert calls == ["pipeline"]


def test_event_monitor_split_window_failure_retries_same_parity(monkeypatch):
    """A failed window must NOT advance the window counter (nor stamp), so
    the next dispatch retries the SAME parity instead of silently skipping
    that half of the heavy block."""
    import sys as _sys

    monkeypatch.setenv("EVENT_MONITOR_SPLIT_WINDOWS", "1")
    import llm_orchestrator as orch_mod
    assert orch_mod is not None, "orchestrator module must be importable for patching"

    calls = []

    def _fake_pipeline(**kwargs):
        calls.append("pipeline")
        raise RuntimeError("boom")

    def _fake_rn(**kwargs):
        calls.append("rn")
        return {"status": "PROCESSED"}

    monkeypatch.setattr(orch_mod, "process_cycle_data_sources", _fake_pipeline)
    monkeypatch.setattr(orch_mod, "run_realtime_news_ingest", _fake_rn)

    def _mk(last_ts, window_no=0):
        inst = _watchdog_instance(
            monkeypatch,
            [{"schedule_name": "event_monitor", "payload": {}}],
            registry=[], handler_fn=lambda name: False)
        inst._event_monitor_last_full_ts = last_ts
        inst._event_monitor_window_no = window_no
        inst.store = _Store()
        inst.data_provider = type("P", (), {"calendar": None, "api_manager": None,
                                            "client": None})()
        inst.macro_graph = None
        inst.client = None
        inst.trade_memory = None
        return inst

    # Window 0 (even) raises inside the pipeline half: ERROR payload, and
    # neither the stamp nor the window counter may advance.
    inst = _mk(0.0, window_no=0)
    inst._process_due_schedules("EURUSD", True)
    assert calls == ["pipeline"]
    state = inst.store.states.get("event_monitor:EURUSD") or {}
    pipe = state.get("data_source_pipeline") or {}
    assert pipe.get("status") == "ERROR" and "boom" in str(pipe.get("error"))
    assert inst._event_monitor_last_full_ts == 0.0
    assert getattr(inst, "_event_monitor_window_no", 0) == 0

    # Retry re-runs the SAME (pipeline) parity.
    calls.clear()
    inst2 = _mk(0.0, window_no=0)
    inst2._process_due_schedules("EURUSD", True)
    assert calls == ["pipeline"], "retry must re-run the same parity"


def test_fast_lane_budget_fallback_and_claim_order_tiebreak(monkeypatch):
    """Schedules missing from the registry ride the handler-budget lane
    (still ahead of known slow cadences), and equal lanes keep claim order."""
    handled = []

    def handler(name):
        handled.append(name)
        return True

    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "orphan_daily", "payload": {}},
         {"schedule_name": "b_quick", "payload": {}},
         {"schedule_name": "a_quick", "payload": {}}],
        registry=[
            {"schedule_name": "orphan_daily", "interval_seconds": 86400, "enabled": 1},
        ],
        handler_fn=handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"b_quick": 5.0, "a_quick": 5.0}

    instance._process_due_schedules("XAUUSD", True)
    # Registry-missed orphans fall back to the default-budget lane (45s) and
    # still beat the registered daily cadence; equal lanes keep claim order.
    assert handled == ["b_quick", "a_quick", "orphan_daily"]
    assert _events_of(instance, "schedule_skipped") == []
    assert instance.operations.releases == []


# ---------------------------------------------------------------------------
# Session-skip watchdog: claimed-but-undispatched schedules must be visible
# (2026-09-26 audit: position_reconcile starved a whole session because every
# wave abandoned right before its dispatch slot, while claim_due_schedules
# had already consumed its cadence turn).
# ---------------------------------------------------------------------------

class _SchedOps(_Operations):
    """Operations stub that also exposes list_schedules (watchdog registry)
    and records claim releases + dispatch completions (the new claim-token
    contract: claim marks a lease, completion advances cadence)."""

    def __init__(self, scheduled, registry):
        super().__init__(scheduled)
        self._registry = registry
        self.releases = []
        self.completions = []

    def list_schedules(self):
        return list(self._registry)

    def release_schedule_claims(self, records):
        self.releases.append(list(records or []))
        return len(self.releases[-1])

    def complete_schedule_dispatch(self, schedule_name, claimed_at, status="COMPLETED"):
        self.completions.append((schedule_name, claimed_at, status))
        return True


def _watchdog_instance(monkeypatch, claim_items, registry, handler_fn=None):
    """Orchestrator skeleton with watchdog state initialized like __init__.

    store.path stays ':memory:' so any operator-alert enqueues are hermetic.
    """
    if handler_fn is None:
        def handler_fn(name):
            return True
    monkeypatch.setattr("operations.executors.try_handle",
                        lambda name, payload, context=None: handler_fn(name))
    # Claim records carry the identity fields the real claim returns, so
    # cadence-restore records are exercised end to end.
    enriched = []
    for i, item in enumerate(claim_items):
        row = dict(item)
        row.setdefault("scheduled_for", f"2026-09-27T00:00:0{i}+00:00")
        row.setdefault("claimed_at", "2026-09-27T00:00:30+00:00")
        row.setdefault("last_run_at", None)
        row.setdefault("schedule_state", "ON_CADENCE")
        enriched.append(row)
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _SchedOps(enriched, registry)
    instance.store = _Store()          # path=':memory:' -> hermetic outbox
    instance.run_id = "run"
    instance._schedule_batch_running = False
    instance._schedule_thread_lock = _th_mod.Lock()
    instance._schedule_overrun_stats = {}
    instance._schedule_last_dispatch = {}
    instance._schedule_skip_alerted = {}
    instance._schedule_watchdog_ready = False
    return instance


def _events_of(instance, event_type):
    return [e for e in instance.operations.events
            if e[0] and len(e[0]) > 2 and e[0][1] == event_type]


def test_wave_deadline_defers_multiple_schedules_and_releases_all(monkeypatch):
    """A deadline breach records and releases EVERY undispatched schedule
    (the deadline is the only deferral source; released claims keep their
    cadence turn because claims no longer advance it)."""
    handled = []

    def handler(name):
        handled.append(name)
        if name == "hog":
            _time_mod.sleep(0.8)
        return True

    instance = _bounded_instance(
        monkeypatch,
        [{"schedule_name": "first", "payload": {}},
         {"schedule_name": "hog", "payload": {}},
         {"schedule_name": "deferred_a", "payload": {}},
         {"schedule_name": "deferred_b", "payload": {}}],
        handler)
    # Fast lane: hog's budget == default so equal lanes keep claim order.
    instance._SCHEDULE_HANDLER_BUDGETS = {"hog": 45.0}
    instance._SCHEDULE_TIMEOUT = 0.4

    instance._process_due_schedules("XAUUSD", True)
    assert handled == ["first", "hog"]
    skips = _events_of(instance, "schedule_skipped")
    assert len(skips) == 1
    assert skips[0][0][2]["schedules"] == ["deferred_a", "deferred_b"]
    assert skips[0][0][2]["reason"] == "wave_deadline"
    assert len(instance.operations.releases) == 1
    assert sorted(r["schedule_name"] for r in instance.operations.releases[0]) == \
        ["deferred_a", "deferred_b"]
    # Dispatched items completed inline; deferred ones never completed.
    completed_names = {c[0] for c in instance.operations.completions}
    assert completed_names == {"first", "hog"}


def test_session_skip_watchdog_raises_then_dampens(monkeypatch):
    """A schedule never dispatched far beyond its cadence raises one alert;
    the alert re-fires at most once per realert window while it persists."""
    registry = [
        {"schedule_name": "pos_rec", "interval_seconds": 60, "enabled": 1},
        {"schedule_name": "market_health", "interval_seconds": 30, "enabled": 1},
    ]
    instance = _watchdog_instance(
        monkeypatch, [{"schedule_name": "market_health", "payload": {}}], registry)

    instance._process_due_schedules("XAUUSD", True)   # builds registry; warmup active
    assert not _events_of(instance, "schedule_session_skip"), "warmup must suppress alerts"

    # Rewind the watchdog clock so the warmup has passed; pos_rec still has
    # no dispatch attempt this session (try_handle claims only market_health).
    instance._schedule_watchdog_started = _time_mod.time() - 1900.0
    instance._schedule_last_dispatch.pop("pos_rec", None)
    instance._process_due_schedules("XAUUSD", True)
    skips = _events_of(instance, "schedule_session_skip")
    assert [e[0][2]["schedule"] for e in skips] == ["pos_rec"]
    assert skips[0][0][2]["silence_s"] >= 600.0
    assert skips[0][0][2]["execution_authority"] is False

    # Immediately re-running must NOT re-alert (dampened).
    instance._process_due_schedules("XAUUSD", True)
    assert len(_events_of(instance, "schedule_session_skip")) == 1


def test_session_skip_clears_on_next_dispatch(monkeypatch):
    """Dispatching a previously-flagged schedule ends the episode: exactly one
    recovery event + INFO alert, and healthy dispatches stay silent."""
    registry = [{"schedule_name": "pos_rec", "interval_seconds": 60, "enabled": 1}]
    instance = _watchdog_instance(
        monkeypatch, [{"schedule_name": "pos_rec", "payload": {}}], registry)
    instance._schedule_skip_alerted["pos_rec"] = _time_mod.time()  # active episode

    instance._process_due_schedules("XAUUSD", True)
    cleared = _events_of(instance, "schedule_skips_cleared")
    assert [e[0][2]["schedule"] for e in cleared] == ["pos_rec"]
    assert "pos_rec" not in instance._schedule_skip_alerted

    instance._process_due_schedules("XAUUSD", True)
    assert len(_events_of(instance, "schedule_skips_cleared")) == 1, \
        "recovery must fire once per episode, not per dispatch"


# ---------------------------------------------------------------------------
# Macro event evidence stage: calendar observations collected by event_monitor
# must become macro_events + event_reactions rows on a maintenance cadence
# (2026-09-26 audit: 229k macro_sources, 0 trackers, 0 reactions).
# ---------------------------------------------------------------------------

_CALENDAR = [
    {"time": "2026-09-26 13:30:00", "currency": "USD", "event": "CPI",
     "actual": 0.3, "forecast": 0.2, "previous": 0.1},
    {"time": "2026-09-26 15:00:00", "currency": "EUR", "event": "ECB Speech"},
]


class _EvidenceMemory:
    """Trade-memory stub: records calls, serves the dedup gate."""

    def __init__(self, known_keys=()):
        self.known = set(known_keys)
        self.recorded = []

    def retrieve_facts(self, keys, limit=20):
        return [{"fact_key": k} for k in keys if k in self.known]

    def record_events(self, events):
        self.recorded.extend(events)


class _EvidenceGraph:
    """MacroGraph stub: count ingest/schedule/capture calls."""

    def __init__(self):
        self.ingested = 0
        self.scheduled = []
        self.captured = []

    def ingest_source(self, **kwargs):
        self.ingested += 1
        return f"macro:src-{self.ingested}"

    def schedule_event_reaction(self, source_id, symbol, event, *, anchor_price, observed_at=None):
        # Mirror MacroGraph.schedule_event_reaction's gate: a release without
        # a numeric actual (or without forecast/previous) is not schedulable.
        if event.get("actual") in (None, "") or (
                event.get("forecast") in (None, "") and event.get("previous") in (None, "")):
            return None
        self.scheduled.append((source_id, symbol, anchor_price))
        return f"tracker-{source_id}"

    def capture_due_event_reactions(self, symbol, anchor_price, observed_at=None):
        self.captured.append((symbol, anchor_price))
        return {"scheduled": 0, "measured": 1, "completed": 1}


def _evidence_instance(monkeypatch, memory, graph, calendar=_CALENDAR, registry=()):
    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "macro_event_evidence", "payload": {"kind": "macro_event_evidence"}}],
        list(registry), handler_fn=lambda name: False)  # legacy chain must run
    instance.store.set_state("event_monitor:EURUSD", {"events": calendar})
    instance.trade_memory = memory
    instance.macro_graph = graph
    return instance


def _evidence_summaries(instance):
    return [e[0][4] for e in instance.operations.events
            if e[0] and len(e[0]) > 4 and e[0][3] == "macro_event_evidence_updated"]


def test_macro_event_evidence_stage_runs_end_to_end(monkeypatch):
    """Calendar -> graph sources -> macro_events (completed releases) ->
    reaction trackers with a live anchor -> capture."""
    import sys
    owner = sys.modules[LLMOrchestrator.__module__]

    class _Tick:
        bid, ask = 1.1010, 1.1012

    class _FakeMT5:
        @staticmethod
        def symbol_info_tick(symbol):
            return _Tick()

    monkeypatch.setattr(owner, "mt5", _FakeMT5, raising=False)
    memory, graph = _EvidenceMemory(), _EvidenceGraph()
    instance = _evidence_instance(monkeypatch, memory, graph)

    instance._process_due_schedules("EURUSD", False)   # paper_mode=False -> live tick
    summaries = _evidence_summaries(instance)
    assert len(summaries) == 1
    s = summaries[0]
    assert s["calendar_events"] == 2 and s["sources_ingested"] == 2
    assert s["macro_events_recorded"] == 1, "only the completed release is recorded"
    assert memory.recorded and memory.recorded[0]["event"] == "CPI"
    assert len(graph.scheduled) == 1, "only the completed release schedules a tracker"
    assert graph.scheduled[0][2] == 1.1011, "anchor must be the live tick mid"
    assert graph.captured and graph.captured[0][0] == "EURUSD"
    assert s["reaction_capture"]["measured"] == 1
    assert s["anchor"] == "live_tick"
    assert s["execution_authority"] is False


def test_macro_event_evidence_dedups_completed_releases(monkeypatch):
    """record_events is append-only: a release whose atomic-fact key already
    exists must NOT be recorded again on the next cadence pass."""
    memory = _EvidenceMemory(
        known_keys={"macro_event:2026-09-26 13:30:00:USD:CPI"})
    graph = _EvidenceGraph()
    instance = _evidence_instance(monkeypatch, memory, graph)

    instance._process_due_schedules("EURUSD", True)
    assert memory.recorded == [], "dedup gate must suppress known releases"
    assert _evidence_summaries(instance)[0]["macro_events_recorded"] == 0


def test_macro_event_evidence_requires_anchor_for_trackers(monkeypatch):
    """Without a point-in-time anchor (paper mode), trackers and captures are
    skipped entirely — no reaction evidence without a real price anchor."""
    memory, graph = _EvidenceMemory(), _EvidenceGraph()
    instance = _evidence_instance(monkeypatch, memory, graph)

    instance._process_due_schedules("EURUSD", True)   # paper_mode=True -> no tick
    s = _evidence_summaries(instance)[0]
    assert s["anchor"] == "unavailable"
    assert graph.scheduled == [] and graph.captured == []
    assert s["reaction_capture"] == {"scheduled": 0, "measured": 0, "completed": 0}
    # Source ingest + completed-release recording still happen (no anchor needed).
    assert s["sources_ingested"] == 2 and s["macro_events_recorded"] == 1


def test_macro_event_evidence_is_registered_on_cadence():
    """The stage is wired: schedule entry, handler budget, budget < interval."""
    from agent_scheduler import SCHEDULES
    entries = [s for s in SCHEDULES if s["name"] == "macro_event_evidence"]
    assert len(entries) == 1
    assert entries[0]["agent"] == "MacroAnalyst"
    assert entries[0]["interval"] == 900
    budget = LLMOrchestrator._SCHEDULE_HANDLER_BUDGETS["macro_event_evidence"]
    assert budget == 15.0 and budget < entries[0]["interval"]


# ---------------------------------------------------------------------------
# Claim-time cadence restore: a batch abandoned on a budget overrun must no
# longer consume the cadence turn of every claimed-but-undispatched schedule.
# ---------------------------------------------------------------------------

def test_overrun_wave_emits_no_skip_and_no_restore(monkeypatch):
    """Wave continuation means an overrunning handler no longer defers its
    followers: no schedule_skipped event, no cadence restore (the deadline
    is the only deferral source)."""
    def handler(name):
        if name == "slow_one":
            _time_mod.sleep(0.8)
        return True

    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "slow_one", "payload": {}},
         {"schedule_name": "next_in_line", "payload": {}},
         {"schedule_name": "tail", "payload": {}}],
        registry=[], handler_fn=handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow_one": 0.5}

    instance._process_due_schedules("XAUUSD", True)
    assert _events_of(instance, "schedule_skipped") == []
    assert instance.operations.releases == []
    assert len(_overrun_events(instance)) == 1


def test_full_dispatch_and_last_handler_trip_complete_every_claim(monkeypatch):
    """A fully dispatched batch releases nothing (no false positives), and a
    last-handler trip also releases nothing because every claim dispatched —
    completion is recorded at dispatch acceptance, so even the tripped last
    handler's cadence turn is consumed."""
    def fast_handler(name):
        return True

    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "a", "payload": {}},
         {"schedule_name": "b", "payload": {}}],
        registry=[], handler_fn=fast_handler)
    instance._process_due_schedules("XAUUSD", True)
    assert instance.operations.releases == []
    assert {c[0] for c in instance.operations.completions} == {"a", "b"}

    # Last handler trips its budget AFTER dispatch: every claimed item did
    # dispatch (and completed at acceptance), so there is nothing undispatched
    # to release (only the batch-end overrun telemetry fires).
    def trip_last(name):
        if name == "b":
            _time_mod.sleep(0.8)
        return True

    tripper = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "a", "payload": {}},
         {"schedule_name": "b", "payload": {}}],
        registry=[], handler_fn=trip_last)
    tripper._SCHEDULE_HANDLER_BUDGETS = {"a": 0.5, "b": 0.5}  # equal lanes keep claim order
    tripper._process_due_schedules("XAUUSD", True)
    assert tripper.operations.releases == []
    assert {c[0] for c in tripper.operations.completions} == {"a", "b"}
    overruns = _overrun_events(tripper)
    assert len(overruns) == 1 and overruns[0][0][2]["schedule"] == "b"


def test_release_skips_malformed_recovered_rows(monkeypatch):
    """Malformed-recovered rows keep their operator-review recovery stamp;
    at a wave deadline they are excluded from claim release while normal
    deferred rows are released."""
    def handler(name):
        if name == "hog":
            _time_mod.sleep(0.8)
        return True

    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": "hog", "payload": {}},
         {"schedule_name": "normal", "payload": {}},
         {"schedule_name": "recovered", "payload": {}, "schedule_state": "MALFORMED_RECOVERED"}],
        registry=[], handler_fn=handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"hog": 5.0}
    instance._SCHEDULE_TIMEOUT = 0.4

    instance._process_due_schedules("XAUUSD", True)
    # Deadline fires after 'hog': 'normal' is deferred and released;
    # 'recovered' is excluded from the release records.
    assert len(instance.operations.releases) == 1
    names = [r["schedule_name"] for r in instance.operations.releases[0]]
    assert names == ["normal"]


def test_multi_overrun_wave_resolves_every_claim(monkeypatch):
    """STRESS: several handlers overrun in the same wave while the wave
    deadline also trips. Invariant: every claimed schedule RESOLVES —
    dispatched (completed inline; exactly one overrun event per slow
    handler) or deferred at the deadline (claim released for the next
    wave). A dangling claim — neither completed nor released — is the
    starvation regression this test exists to catch."""
    handled = []

    def handler(name):
        handled.append(name)
        if name.startswith("slow"):
            _time_mod.sleep(0.5)
        return True

    # Fast lane: unregistered names dispatch by handler-budget lane — the
    # wave order is slow_a, slow_b, slow_c (0.2s lanes), then fast_1..3 and
    # the deferred_* tail (default-45s lanes). The 0.6s deadline cuts after
    # the second 0.5s slow handler.
    all_names = ["fast_1", "slow_a", "fast_2", "slow_b", "fast_3",
                 "slow_c", "deferred_1", "deferred_2"]
    instance = _watchdog_instance(
        monkeypatch,
        [{"schedule_name": n, "payload": {}} for n in all_names],
        registry=[], handler_fn=handler)
    instance._SCHEDULE_HANDLER_BUDGETS = {"slow_a": 0.2, "slow_b": 0.2,
                                          "slow_c": 0.2}
    instance._SCHEDULE_TIMEOUT = 0.6

    instance._process_due_schedules("XAUUSD", True)

    claimed = set(all_names)
    completed = {c[0] for c in instance.operations.completions}
    released = {r["schedule_name"]
                for call in instance.operations.releases for r in call}

    # 1) The wave dispatches a prefix of the FAST-LANE order: the deadline
    #    defers the tail, nothing in the middle is skipped silently.
    fast_lane_order = ["slow_a", "slow_b", "slow_c", "fast_1", "fast_2",
                       "fast_3", "deferred_1", "deferred_2"]
    assert handled and fast_lane_order[:len(handled)] == handled
    # 2) Every claim resolved exactly once: completed XOR released —
    #    no dangling claims, no double resolution.
    assert completed | released == claimed, "dangling claim(s) left unresolved"
    assert not completed & released, "claim both completed and released"
    assert len(instance.operations.completions) == len(handled)
    # 3) Overrun telemetry: exactly one event per DISPATCHED slow handler
    #    (deferred slow handlers never ran, so they must not appear).
    overrun_names = [e[0][2]["schedule"] for e in _overrun_events(instance)]
    assert sorted(overrun_names) == sorted(
        n for n in handled if n.startswith("slow"))
    # 4) The deferral is recorded once, with every deferred schedule and
    #    the wave_deadline reason.
    skips = _events_of(instance, "schedule_skipped")
    assert len(skips) == 1
    assert sorted(skips[0][0][2]["schedules"]) == sorted(claimed - set(handled))
    assert skips[0][0][2]["reason"] == "wave_deadline"
