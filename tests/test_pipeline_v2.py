"""Phase E1: V2 pipeline runner semantics + envelope integration."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.harness.stage_contracts import (  # noqa: E402
    CycleAbort, RunnerContext, StageSpec,
)
from core.harness.pipeline_v2 import PipelineRunner  # noqa: E402
from operations import event_bridge  # noqa: E402


def _runner(stages, trace="t"):
    class _Orch:
        pass
    ctx = RunnerContext(orchestrator=_Orch(), shared={}, trace_id=trace)
    return PipelineRunner(_Orch(), trace, stages), ctx


def test_done_stage_emits_events_beats_and_result():
    event_bridge.init(None)
    seen = []
    unsub = event_bridge.subscribe(seen.append)
    calls = []

    def work(ctx, inp):
        calls.append(inp["attempt"])
        return "ok"

    runner, ctx = _runner([StageSpec("s.done", "EventAgent", work)])
    results = runner.run(ctx)
    unsub()

    assert results[0].status == "done" and results[0].value == "ok"
    types = [e.event_type for e in seen]
    assert types[:2] == ["STAGE_STARTED", "STAGE_DONE"]
    assert all(e.payload.get("actor") == "EventAgent" for e in types and seen[:1])


def test_skip_fallback_proceeds_without_output():
    def boom(ctx, inp):
        raise RuntimeError("nope")

    runner, ctx = _runner([
        StageSpec("s.skip", "PortfolioAgent", boom, fallback="skip"),
        StageSpec("s.after", "EventAgent", lambda c, i: "ran"),
    ])
    results = runner.run(ctx)
    assert [r.status for r in results] == ["skipped", "done"]
    assert results[1].value == "ran"


def test_degrade_forces_hold_only():
    def boom(ctx, inp):
        raise RuntimeError("llm down")

    runner, ctx = _runner([StageSpec("s.deg", "MarketModel", boom,
                                     fallback="degrade")])
    results = runner.run(ctx)
    assert results[0].status == "degraded"
    assert ctx.flags["hold_only"] is True
    assert "llm down" in ctx.flags["hold_only_why"]


def test_abort_cycle_bubbles_and_stops_pipeline():
    order = []

    def gate(ctx, inp):
        raise CycleAbort("blackout NFP in 12m")

    def never(ctx, inp):
        order.append("never")
        return 1

    runner, ctx = _runner([
        StageSpec("gate", "EventAgent", gate, fallback="abort_cycle"),
        StageSpec("after", "TechnicalNet", never),
    ])
    try:
        runner.run(ctx)
        raised = False
    except CycleAbort as ab:
        raised = True
        assert "NFP" in str(ab)
    assert raised and order == []
    assert runner.results[0].status == "aborted"


def test_retry_recovers_transient_failure():
    state = {"n": 0}

    def flaky(ctx, inp):
        state["n"] += 1
        if state["n"] < 2:
            raise ValueError("transient")
        return "recovered"

    runner, ctx = _runner([StageSpec("s.retry", "TechnicalNet", flaky, retries=2)])
    results = runner.run(ctx)
    assert results[0].status == "done" and state["n"] == 2


def test_sla_miss_emitted_on_slow_success():
    import time as _t
    seen = []
    event_bridge.init(None)
    unsub = event_bridge.subscribe(seen.append)

    def slow(ctx, inp):
        _t.sleep(0.03)
        return None

    runner, ctx = _runner([StageSpec("s.slow", "MacroAnalyst", slow, sla_ms=5)])
    runner.run(ctx)
    unsub()
    assert any(e.event_type == "STAGE_SLA_MISS" for e in seen)


# ---------- envelope integration ----------
import types
from core.harness import pipeline_v2 as pv2_mod


class FakeOrchestrator:
    """Minimal stand-in exposing exactly what the declared stages touch."""

    def __init__(self, legacy_result=({"action": "EXECUTE"}, 0.75)):
        self.legacy_result = legacy_result
        self.legacy_calls = 0
        self.calls: list[str] = []
        self.run_id = "fake-run"
        self.velocity = types.SimpleNamespace(
            submit_slow=lambda key, fn, limit=3: (
                self.calls.append("memory.dispatch"), {"status": "OK"})[1])
        self.operations = types.SimpleNamespace(
            heartbeat=lambda *a, **k: self.calls.append("ops.heartbeat"),
            event=lambda *a, **k: None)

    # --- maintenance surface (mirrors Orchestrator._run_maintenance_prefix) ---
    def _poll_velocity_results(self):
        self.calls.append("velocity.poll")

    def reconcile_pending_orders(self, paper_mode):
        self.calls.append("reconcile.pending")

    def reconcile_open_positions(self):
        self.calls.append("reconcile.open")

    def process_pending_memory(self):
        return {}

    def _monitor_owned_positions_tick(self, symbol, timeframe, paper_mode=True):
        self.calls.append("position.tick")

    # --- analysis/execution body ---
    def execute_cycle(self, **kwargs):
        self.legacy_calls += 1
        self.calls.append("legacy.body")
        assert kwargs["symbol"] == "XAUUSD"      # forwarded untouched
        return self.legacy_result


def _clean_event_manager(monkeypatch, tmp_path, windows=None):
    from operations.agents import event_agent

    mgr = event_agent.EventWindowManager(str(tmp_path / "w.json"))
    if windows:
        mgr.triage(windows)

    monkeypatch.setattr(event_agent, "get_manager", lambda: mgr)
    monkeypatch.setattr(event_agent, "_manager", mgr)
    return mgr


def test_envelope_blackout_blocks_legacy_body(tmp_path, monkeypatch):
    from core.harness import pipeline_v2 as pv2

    # Legacy-body shape is now behind the kill switch (E3 default: staged).
    monkeypatch.setenv("PIPELINE_V2_LEGACY_BODY", "1")
    orch = FakeOrchestrator()
    now = __import__("time").time()
    _clean_event_manager(monkeypatch, tmp_path, windows=[
        {"symbol": "XAUUSD", "title": "FOMC", "impact": "high",
         "time": __import__("datetime").datetime.fromtimestamp(
             now + 600, tz=__import__("datetime").timezone.utc).isoformat()},
    ])

    result = pv2.run_envelope(orch, symbol="XAUUSD", timeframe="H1",
                              cycle_num=1, total_cycles=9)
    assert result[0]["action"] == "HOLD"
    assert "pipeline gate" in result[0]["reasoning"]
    assert orch.legacy_calls == 0            # body never ran during blackout


def test_envelope_clear_market_runs_legacy_and_returns_its_tuple(tmp_path, monkeypatch):
    from core.harness import pipeline_v2 as pv2

    # Legacy-body shape is now behind the kill switch (E3 default: staged).
    monkeypatch.setenv("PIPELINE_V2_LEGACY_BODY", "1")
    orch = FakeOrchestrator(legacy_result=({"action": "EXECUTE"}, 0.8))
    _clean_event_manager(monkeypatch, tmp_path)   # no windows → clear

    decision, pnl = pv2.run_envelope(orch, symbol="XAUUSD", timeframe="H1",
                                     cycle_num=2, total_cycles=9,
                                     strategy="macro_continuation_v1")
    assert decision["action"] == "EXECUTE" and pnl == 0.8
    assert orch.legacy_calls == 1
    assert isinstance(decision, dict)


def test_envelope_portfolio_consult_records_cap(tmp_path, monkeypatch):
    from core.harness import pipeline_v2 as pv2
    from core.harness.stage_contracts import StageSpec as S

    captured = {}

    class CapOrch(FakeOrchestrator):
        pass

    orch = CapOrch()
    _clean_event_manager(monkeypatch, tmp_path)

    original = pv2._stage_portfolio_consult

    def spy(ctx, inp):
        cap = original(ctx, inp)
        captured.update(cap)
        return cap

    stages = [
        S(key="blackout.gate", agent="EventAgent",
          fn=pv2._stage_blackout_gate, fallback="abort_cycle"),
        S(key="portfolio.consult", agent="PortfolioAgent",
          fn=spy, fallback="skip"),
        S(key="legacy.cycle_body", agent="RiskController",
          fn=pv2._stage_legacy_body, emits_done=False),
    ]

    runner = pv2.PipelineRunner(orch, "trace-x", stages)
    ctx = RunnerContext(orchestrator=orch,
                        shared={"legacy_args": {"cycle_num": 1,
                                                "total_cycles": 1}},
                        trace_id="trace-x")
    runner.run(ctx)

    assert captured.get("authority") == "PortfolioAgent"
    assert captured.get("capped_pct", 0) <= captured.get("requested_pct", 1)


# ---------- Phase E2: maintenance prefix equivalence ----------
from core.harness.pipeline_v2 import consume_skip_maintenance

PAPER_SEQUENCE = [
    "velocity.poll",
    "reconcile.pending",
    # reconcile.open is live-only
    "memory.dispatch",       # submit_slow OK → no backpressure event
    "position.tick",
    "ops.heartbeat",
]
LIVE_EXTRA = ["reconcile.open"]


def _reset_skip_token():
    consume_skip_maintenance()


def test_maintenance_stages_match_legacy_prefix_order(tmp_path):
    """Golden equivalence: declared stages execute the exact same calls, in
    the exact same order, as the extracted legacy prefix method."""
    from intelligence import llm_orchestrator as orch_mod  # heavy import

    _reset_skip_token()
    legacy_orch = FakeOrchestrator()
    orch_mod.LLMOrchestrator._run_maintenance_prefix(
        legacy_orch, "EURUSD", "M15", paper_mode=True)
    assert legacy_orch.calls == PAPER_SEQUENCE

    v2_orch = FakeOrchestrator()
    stages = pv2_mod.maintenance_stages(v2_orch, "EURUSD", "M15",
                                        paper_mode=True)
    runner = pv2_mod.PipelineRunner(v2_orch, "trace-eq", stages)
    ctx = RunnerContext(orchestrator=v2_orch, shared={}, trace_id="trace-eq")
    results = runner.run(ctx)

    assert v2_orch.calls == PAPER_SEQUENCE
    assert all(r.status == "done" for r in results)

    # Live mode adds reconcile.open in BOTH paths, right after pending.
    _reset_skip_token()
    live_legacy = FakeOrchestrator()
    orch_mod.LLMOrchestrator._run_maintenance_prefix(
        live_legacy, "EURUSD", "M15", paper_mode=False)
    assert live_legacy.calls[:3] == PAPER_SEQUENCE[:2] + LIVE_EXTRA

    live_v2 = FakeOrchestrator()
    live_stages = pv2_mod.maintenance_stages(live_v2, "EURUSD", "M15",
                                             paper_mode=False)
    pv2_mod.PipelineRunner(live_v2, "trace-eq2", live_stages).run(
        RunnerContext(orchestrator=live_v2, shared={}, trace_id="trace-eq2"))
    assert live_v2.calls[:3] == PAPER_SEQUENCE[:2] + LIVE_EXTRA


def test_skip_token_prevents_double_maintenance(tmp_path):
    """V2 → legacy handoff: consumed token makes the prefix a no-op."""
    from intelligence import llm_orchestrator as orch_mod
    from core.harness.pipeline_v2 import request_skip_maintenance

    dummy = FakeOrchestrator()
    request_skip_maintenance()
    orch_mod.LLMOrchestrator._run_maintenance_prefix(
        dummy, "EURUSD", "M15", paper_mode=True)
    assert dummy.calls == []            # nothing ran twice


def test_envelope_runs_maintenance_once_then_body(tmp_path, monkeypatch):
    from core.harness import pipeline_v2 as pv2

    # Legacy-body shape is now behind the kill switch (E3 default: staged).
    monkeypatch.setenv("PIPELINE_V2_LEGACY_BODY", "1")
    orch = FakeOrchestrator()
    _clean_event_manager(monkeypatch, tmp_path)     # clear market

    decision, pnl = pv2.run_envelope(orch, symbol="XAUUSD", timeframe="H1",
                                     cycle_num=3, total_cycles=9,
                                     paper_mode=True,
                                     strategy="macro_continuation_v1")
    assert decision["action"] == "EXECUTE"

    expected = ["velocity.poll", "reconcile.pending", "memory.dispatch",
                "position.tick", "ops.heartbeat",       # open skipped (paper)
                "legacy.body"]
    assert orch.calls == expected                        # exactly once each
    assert orch.legacy_calls == 1
