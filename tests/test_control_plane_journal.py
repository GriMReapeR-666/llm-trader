"""K5: journal-backed operator control queue with compare-and-set drain.

Replaces the append-and-rewrite control_plane.jsonl queue that lost 1,826 of
2,000 cross-process controls (AUDIT_2026-10-04_DEEP 06 F7).
"""
import json
import os
import threading

from operations.autonomy import AutonomyManager, CONTROL_ACTIONS


def _paths(tmp_path):
    return (
        str(tmp_path / "autonomy_state.json"),
        str(tmp_path / "control_plane.jsonl"),
    )


def test_controls_survive_between_manager_instances(tmp_path):
    """An API-process manager issues; a separate engine-process manager drains."""
    state, control = _paths(tmp_path)
    api = AutonomyManager(state, control)
    engine = AutonomyManager(state, control)

    res = api.issue_control("killswitch", actor="deck", source="command-deck")
    assert res["ok"] is True
    # Emergency controls are honestly reported as queued, not executed (D1).
    assert res["status"] == "queued"

    drained = engine.drain_controls()
    assert [r["action"] for r in drained] == ["killswitch"]
    assert drained[0]["actor"] == "deck"
    # Exactly once: a second drain claims nothing.
    assert engine.drain_controls() == []
    assert api.drain_controls() == []


def test_drain_is_exactly_once_under_concurrency(tmp_path):
    """Compare-and-set: N threads racing one queue deliver each control once."""
    state, control = _paths(tmp_path)
    api = AutonomyManager(state, control)
    for i in range(20):
        api.issue_control("flatten", actor=f"op{i}", source="test")

    seen = []
    barrier = threading.Barrier(8)

    def drain():
        mgr = AutonomyManager(state, control)
        barrier.wait()
        seen.extend(mgr.drain_controls())

    threads = [threading.Thread(target=drain) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(seen) == 20, f"expected 20 claims, got {len(seen)}"
    assert len({r['actor'] for r in seen}) == 20


def test_pause_reaches_second_instance(tmp_path):
    """A pause issued by one process is applied when the other drains (K5)."""
    state, control = _paths(tmp_path)
    api = AutonomyManager(state, control)
    engine = AutonomyManager(state, control)

    api.issue_control("pause", actor="deck")
    assert api.trading_allowed()[0] is False

    pending = engine.drain_controls()
    assert [r["action"] for r in pending] == ["pause"]
    allowed, why = engine.trading_allowed()
    assert allowed is False and "operator:" in why

    api.issue_control("resume", actor="deck")
    engine.drain_controls()
    assert engine.trading_allowed()[0] is True


def test_legacy_jsonl_controls_are_migrated(tmp_path):
    """Unconsumed records in the old JSONL queue survive the format change."""
    state, control = _paths(tmp_path)
    legacy = [
        {"ts": 1.0, "action": "flatten", "actor": "deck", "source": "command-deck", "consumed": False},
        {"ts": 2.0, "action": "pause", "actor": "deck", "source": "command-deck", "consumed": True},
        "not-json",
    ]
    with open(control, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in legacy) + "\n")

    mgr = AutonomyManager(state, control)
    # Legacy file is archived, not deleted, and only the unconsumed control
    # is queued.
    assert os.path.exists(control + ".migrated")
    assert not os.path.exists(control)
    drained = mgr.drain_controls()
    assert [r["action"] for r in drained] == ["flatten"]


def test_issue_roundtrip_for_every_action(tmp_path):
    state, control = _paths(tmp_path)
    mgr = AutonomyManager(state, control)
    assert mgr.issue_control("self_destruct")["ok"] is False
    statuses = {}
    for action in CONTROL_ACTIONS:
        res = mgr.issue_control(action)
        assert res["ok"] is True
        statuses[action] = res["status"]
    assert statuses["pause"] == "applied"
    assert statuses["resume"] == "applied"
    assert statuses["flatten"] == "queued"
    assert statuses["killswitch"] == "queued"
    drained = mgr.drain_controls()
    assert len(drained) == len(CONTROL_ACTIONS)
