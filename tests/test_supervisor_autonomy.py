"""Supervisor, autonomy doctrine and event bridge unit tests."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.autonomy import AutonomyManager, LEVELS, CONTROL_ACTIONS  # noqa: E402
from operations.supervisor import Supervisor  # noqa: E402
from operations import event_bridge  # noqa: E402


def _tmp_paths(tmp_path):
    return (
        str(tmp_path / "autonomy_state.json"),
        str(tmp_path / "control_plane.jsonl"),
    )


def test_level_escalation_requires_signature_for_l3(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    denied = mgr.escalate("L3_FULL", "test", manifest_signature="")
    assert denied["granted"] is False
    assert "manifest" in denied["reason"]
    ok = mgr.escalate("L3_FULL", "operator approved", manifest_signature="signed-manifest-1")
    assert ok["granted"] is True and mgr.level().key == "L3_FULL"


def test_escalation_l2_allowed_without_signature(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    res = mgr.escalate("L2_MICRO", "micro rollout")
    assert res["granted"] is True
    assert mgr.level().allows_live is True
    assert mgr.level().max_risk_pct_per_trade == 0.25


def test_auto_demotion_and_circuits(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    mgr.escalate("L2_MICRO", "test")
    trips = mgr.evaluate_circuits({"slippage_breach_streak": 4})
    assert trips and trips[0]["action"] == "DEMOTE"
    assert mgr.level().key == "L1_PAPER"

    trips = mgr.evaluate_circuits({"drawdown_pct": 7.5})
    assert trips and trips[0]["action"] == "HALT"
    allowed, why = mgr.trading_allowed()
    assert allowed is False and "drawdown" in why


def test_pause_blocks_but_resume_clears(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    mgr.issue_control("pause", actor="deck")
    pending = mgr.drain_controls()
    assert [p["action"] for p in pending] == ["pause"]
    allowed, _why = mgr.trading_allowed()
    assert allowed is False
    mgr.issue_control("resume", actor="deck")
    mgr.drain_controls()
    allowed, _why = mgr.trading_allowed()
    assert allowed is True


def test_control_plane_roundtrip_rejects_unknown(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    bad = mgr.issue_control("self_destruct")
    assert bad["ok"] is False
    for action in CONTROL_ACTIONS:
        assert mgr.issue_control(action)["ok"] is True
    drained = mgr.drain_controls()
    assert len(drained) == len(CONTROL_ACTIONS)


def test_state_survives_reload(tmp_path):
    state, control = _tmp_paths(tmp_path)
    mgr = AutonomyManager(state, control)
    mgr.halt("manual test")
    reloaded = AutonomyManager(state, control)
    snap = reloaded.snapshot()
    assert snap.halted is True and snap.halt_reason == "manual test"


def test_supervisor_happy_boot_path(tmp_path):
    sup = Supervisor(str(tmp_path / "sup.json"))
    report = sup.boot_sequence({
        "mt5_connect": lambda: True,
        "journal_integrity": lambda: True,
        "provider_ping": lambda: True,
    })
    assert report["ok"] is True
    ready = sup.ready()
    assert ready["ok"] is True and sup.phase == "RUNNING"
    health = sup.overall_health()
    assert health["healthy"] is True and health["phase"] == "RUNNING"


def test_supervisor_failing_check_degrades_not_runs(tmp_path):
    sup = Supervisor(str(tmp_path / "sup.json"))

    def boom():
        raise RuntimeError("mt5 login refused")

    report = sup.boot_sequence({"connect": boom})
    assert report["ok"] is False
    assert sup.phase == "PREFLIGHT"
    # RUNNING must be unreachable directly from PREFLIGHT failure path
    assert sup.ready()["ok"] is False


def test_supervisor_transition_guardrails(tmp_path):
    sup = Supervisor(str(tmp_path / "sup.json"))
    illegal = sup.transition("RUNNING")  # OFFLINE -> RUNNING must be rejected
    assert illegal["ok"] is False
    sup.transition("BOOTING")
    sup.transition("SHUTDOWN_REQUESTED")
    assert sup.complete_shutdown()["ok"] is True
    assert sup.phase == "STOPPED"


def test_event_bridge_pubsub_and_persistence(tmp_path):
    db = str(tmp_path / "events.db")
    seen = []
    event_bridge.init(db)
    unsub = event_bridge.subscribe(seen.append)
    event_bridge.emit("CYCLE_STARTED", {"symbol": "XAUUSD"}, correlation_id="c1")
    event_bridge.emit("ORDER_ROUTED", {"ticket": 1}, correlation_id="c1")
    unsub()
    event_bridge.emit("AFTER_UNSUB", {})
    assert [e.event_type for e in seen] == ["CYCLE_STARTED", "ORDER_ROUTED"]

    import sqlite3
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT event_type FROM harness_events WHERE correlation_id='c1' ORDER BY timestamp"
        ).fetchall()
    assert {r[0] for r in rows} == {"CYCLE_STARTED", "ORDER_ROUTED"}


def test_event_bridge_safe_before_init():
    from operations import event_bridge as bridge
    bridge._stream = None  # force uninitialized view of the module singleton
    bridge.emit("NOOP", {})  # must not raise
    assert bridge.stats()["dropped"] >= 1
