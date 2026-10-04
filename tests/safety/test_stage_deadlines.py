import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from core.harness.stage_contracts import RunnerContext, StageSpec
from core.harness.pipeline_v2 import (
    PipelineRunner, _abandoned_threads, _abandoned_threads_lock,
    _stage_cycle_execute, set_tls_hold_only, get_tls_hold_only,
)
from operations import event_bridge
from operations.autonomy import get_autonomy


@pytest.fixture(autouse=True)
def clean_pipeline_state():
    event_bridge.init(None)
    with _abandoned_threads_lock:
        _abandoned_threads.clear()
    set_tls_hold_only(False)
    autonomy = get_autonomy()
    with autonomy._lock:
        autonomy._state["paused"] = False
        autonomy._state["pause_reason"] = ""
        autonomy._state["halted"] = False
        autonomy._state["halt_reason"] = ""
        autonomy._state.setdefault("flags", {})
        autonomy._state["flags"]["hold_only"] = False
    yield
    with _abandoned_threads_lock:
        _abandoned_threads.clear()
    set_tls_hold_only(False)


def _make_context(orch=None):
    if orch is None:
        orch = MagicMock()
        orch.run_id = "test-run"
        orch.operations = MagicMock()
    return RunnerContext(orchestrator=orch, shared={}, trace_id="trace-test")


def test_abandonable_stage_timeout_bounds_execution_time():
    """A stage that sleeps past its deadline returns within timeout + 0.5s and emits STAGE_TIMEOUT."""
    events = []
    unsub = event_bridge.subscribe(events.append)

    def slow_fn(ctx, inp):
        time.sleep(2.0)
        return "slow_success"

    spec = StageSpec(
        key="velocity.poll",
        agent="TechnicalNet",
        fn=slow_fn,
        timeout_s=0.2,
        sla_ms=100,
        abandonable=True,
        fallback="continue",
    )

    ctx = _make_context()
    runner = PipelineRunner(ctx.orchestrator, ctx.trace_id, [spec])

    t0 = time.monotonic()
    results = runner.run(ctx)
    elapsed = time.monotonic() - t0

    unsub()

    # Returns within timeout + 0.5s
    assert elapsed < 0.7, f"Stage took {elapsed}s; expected < 0.7s"
    assert len(results) == 1
    assert results[0].status == "failed"
    assert "exceeded" in results[0].error or "deadline" in results[0].error

    # Verify STAGE_TIMEOUT was emitted
    timeout_evts = [e for e in events if e.event_type == "STAGE_TIMEOUT"]
    assert len(timeout_evts) == 1
    assert timeout_evts[0].payload["stage"] == "velocity.poll"


def test_non_abandonable_stage_is_never_abandoned():
    """A non-abandonable stage runs inline and is never abandoned even if it exceeds timeout_s."""
    events = []
    unsub = event_bridge.subscribe(events.append)

    def normal_fn(ctx, inp):
        time.sleep(0.15)
        return "completed_normally"

    spec = StageSpec(
        key="analysis.execute",
        agent="RiskController",
        fn=normal_fn,
        timeout_s=0.05,
        sla_ms=50,
        abandonable=False,
    )

    ctx = _make_context()
    runner = PipelineRunner(ctx.orchestrator, ctx.trace_id, [spec])

    results = runner.run(ctx)
    unsub()

    assert len(results) == 1
    assert results[0].status == "done"
    assert results[0].value == "completed_normally"

    # Must NOT emit STAGE_TIMEOUT
    timeout_evts = [e for e in events if e.event_type == "STAGE_TIMEOUT"]
    assert len(timeout_evts) == 0


def test_stage_timeout_degrade_fallback_forces_hold_only():
    """Timeout with fallback='degrade' sets hold_only flag on context and TLS."""
    def hung_fn(ctx, inp):
        time.sleep(2.0)
        return "result"

    spec = StageSpec(
        key="analysis.llm",
        agent="MarketModel",
        fn=hung_fn,
        timeout_s=0.1,
        sla_ms=50,
        abandonable=True,
        fallback="degrade",
    )

    ctx = _make_context()
    runner = PipelineRunner(ctx.orchestrator, ctx.trace_id, [spec])

    results = runner.run(ctx)
    assert len(results) == 1
    assert results[0].status == "degraded"
    assert ctx.flags.get("hold_only") is True
    assert "analysis.llm degraded" in ctx.flags.get("hold_only_why", "")
    assert get_tls_hold_only() is True

    # analysis.execute in staged mode must observe hold_only and return HOLD without executing
    exec_result = _stage_cycle_execute(ctx, {})
    assert exec_result["action"] == "HOLD"
    assert "hold_only" in exec_result["reasoning"]


def test_llm_stage_timeout_releases_budget_reservation():
    """Timed-out analysis.llm stage releases active budget reservation via finish_llm."""
    orch = MagicMock()
    orch.run_id = "test-run"
    orch.operations = MagicMock()
    orch._active_llm_request_id = "llm-req-pr36"

    def hung_llm(ctx, inp):
        time.sleep(2.0)
        return "never"

    spec = StageSpec(
        key="analysis.llm",
        agent="MarketModel",
        fn=hung_llm,
        timeout_s=0.1,
        sla_ms=50,
        abandonable=True,
        fallback="degrade",
    )

    ctx = _make_context(orch)
    runner = PipelineRunner(ctx.orchestrator, ctx.trace_id, [spec])

    results = runner.run(ctx)
    assert len(results) == 1
    assert results[0].status == "degraded"

    # finish_llm must have been called to release the budget reservation
    orch.operations.finish_llm.assert_called_once()
    call_args = orch.operations.finish_llm.call_args
    assert call_args[0][0] == "llm-req-pr36"
    assert call_args[1].get("success") is False
    assert call_args[1].get("parse_status") == "TIMEOUT"
    assert orch._active_llm_request_id is None


def test_legacy_body_checks_hold_only_and_skips_order_submission():
    """Legacy body and staged execute check hold_only and return HOLD without order submission."""
    from core.harness.pipeline_v2 import _stage_legacy_body

    events = []
    unsub = event_bridge.subscribe(events.append)

    orch = MagicMock()
    orch.execute_cycle = MagicMock(return_value=({"action": "BUY", "entry": 1.1000}, 0.8))

    ctx = _make_context(orch)
    ctx.shared["legacy_args"] = {"symbol": "EURUSD", "timeframe": "M15"}

    # 1. With autonomy hold_only set
    get_autonomy().set_hold_only(True, why="circuit:llm_outage")

    res = _stage_legacy_body(ctx, {})
    decision, conf = res if isinstance(res, tuple) else (res, 0.0)
    assert decision["action"] == "HOLD"
    assert "hold_only" in decision["reasoning"]
    orch.execute_cycle.assert_not_called()

    # Verify HOLD_ONLY_TRIGGERED event emitted
    hold_evts = [e for e in events if e.event_type == "HOLD_ONLY_TRIGGERED"]
    assert len(hold_evts) >= 1
    assert hold_evts[0].payload["stage"] == "legacy.body"

    # 2. _stage_cycle_execute in staged mode
    exec_res = _stage_cycle_execute(ctx, {})
    assert exec_res["action"] == "HOLD"
    assert "hold_only" in exec_res["reasoning"]
    orch.operations.create_order_intent.assert_not_called()

    unsub()


def test_leak_bound_trips_autonomy_circuit_on_third_abandonment():
    """At most 2 abandoned threads per stage key; 3rd attempt trips autonomy pause without starting another thread."""
    hung_threads = []
    stop_event = threading.Event()

    def hang_forever():
        stop_event.wait(5.0)

    for i in range(2):
        t = threading.Thread(target=hang_forever, daemon=True)
        t.start()
        hung_threads.append(t)

    with _abandoned_threads_lock:
        _abandoned_threads["velocity.poll"] = list(hung_threads)

    worker_started = False

    def spy_fn(ctx, inp):
        nonlocal worker_started
        worker_started = True
        return "never"

    spec = StageSpec(
        key="velocity.poll",
        agent="TechnicalNet",
        fn=spy_fn,
        timeout_s=0.2,
        abandonable=True,
        fallback="continue",
    )

    ctx = _make_context()
    runner = PipelineRunner(ctx.orchestrator, ctx.trace_id, [spec])

    results = runner.run(ctx)

    # Clean up hung threads
    stop_event.set()

    # Worker was NOT started because 2 abandoned threads were already alive
    assert not worker_started

    # Autonomy circuit must have been paused!
    autonomy = get_autonomy()
    assert autonomy._state["paused"] is True
    assert "abandon_leak:velocity.poll" in autonomy._state["pause_reason"]

    # Result records abandon_leak error
    assert len(results) == 1
    assert results[0].status == "failed"
    assert "abandon_leak" in results[0].error
