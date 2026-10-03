"""Phase D: task-graph delegation + per-principal supervisor freshness."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_operations import AgentOperations  # noqa: E402
from operations.delegation import Delegator, spawn_fanout  # noqa: E402
from operations.supervisor import Supervisor  # noqa: E402
from operations import executors as exec_registry  # noqa: E402


def _ops(tmp_path):
    return AgentOperations(str(tmp_path / "journal.db"))


# ---------- delegation happy path ----------
def test_child_success_creates_edge_and_terminal_state(tmp_path):
    ops = _ops(tmp_path)
    d = Delegator(ops)
    with d.child(parent_key="cycle:1", key="t:triage:9",
                 principal="EventAgent", task_type="triage",
                 payload={"symbol": "XAUUSD"}) as task:
        assert task["claimed"] is True
        task["result"]["windows"] = 2

    graph_child = ops.task_graph("t:triage:9")
    graph_parent = ops.task_graph("cycle:1")
    assert graph_child["status"] == "COMPLETED"
    assert any(e["parent_key"] == "cycle:1" and e["child_key"] == "t:triage:9"
               for e in graph_child["edges"])
    assert graph_parent["task_type"] == "delegation.parent"


def test_child_failure_marks_error_and_reraises(tmp_path):
    ops = _ops(tmp_path)
    d = Delegator(ops)
    raised = False
    try:
        with d.child(parent_key="p", key="k:boom", principal="ResearchAgent",
                     task_type="sprint"):
            raise RuntimeError("replay exploded")
    except RuntimeError:
        raised = True
    assert raised

    con = ops._con()
    try:
        status = con.execute(
            "SELECT status,error FROM agent_tasks WHERE idempotency_key='k:boom'"
        ).fetchone()
    finally:
        con.close()
    assert status["status"] == "ERROR" and "exploded" in status["error"]


def test_deduped_child_yields_not_claimed(tmp_path):
    ops = _ops(tmp_path)
    d = Delegator(ops)
    with d.child(parent_key="p", key="same:key", principal="EventAgent",
                 task_type="t") as first:
        assert first["claimed"] is True
    with d.child(parent_key="p", key="same:key", principal="EventAgent",
                 task_type="t") as second:
        assert second["claimed"] is False


def test_expired_lease_is_reclaimable_within_retry_cap(tmp_path):
    """Crash simulation: worker claims, process dies before completion,
    lease expires → reclaim succeeds inside retry cap."""
    ops = _ops(tmp_path)
    first = ops.claim_task("long:job", "ResearchAgent", "backtest", {},
                           lease_seconds=1, max_attempts=3)
    assert first is True

    time.sleep(1.15)  # lease expired; no complete/fail was ever recorded

    reclaimed = ops.claim_task("long:job", "ResearchAgent", "backtest", {},
                               lease_seconds=30, max_attempts=3)
    assert reclaimed is True

    recovered = ops.recover_stale_tasks(older_than_seconds=0)
    assert isinstance(recovered, int)


def test_spawn_fanout_links_children(tmp_path):
    ops = _ops(tmp_path)
    d = Delegator(ops)
    items = [{"sym": "A"}, {"sym": "B"}, {"sym": "C"}]
    created = spawn_fanout(d, parent_key="sprint:42", items=items,
                           principal="ResearchAgent", task_type="backtest",
                           key_fn=lambda it: f"sprint:42:{it['sym']}")
    assert len(created) == 3 and all(c["claimed"] for c in created)

    graph = ops.task_graph("sprint:42")
    n_edges = sum(1 for e in graph["edges"]
                  if e["parent_key"] == "sprint:42" and e["relation"] == "DEPENDS_ON")
    assert n_edges == 3


# ---------- per-principal supervisor freshness ----------
def test_principal_beats_and_staleness(tmp_path):
    sup = Supervisor(str(tmp_path / "sup.json"))
    sup.note_principal("EventAgent", ok=True)
    sup.note_principal("ResearchAgent", ok=False, detail="boom")
    health = sup.overall_health()
    assert health["principals"]["EventAgent"]["ok"] is True
    assert "ResearchAgent" in health["stale_principals"]
    assert health["healthy"] is False


def test_executor_notes_principal_on_success_and_failure(tmp_path, monkeypatch):
    from operations.agents import research_agent
    from operations import event_bridge, supervisor as sup_mod

    sup = Supervisor(str(tmp_path / "sup.json"))
    monkeypatch.setattr(sup_mod, "_default_supervisor", sup)  # executors use singleton

    seen_events = []
    event_bridge.init(str(tmp_path / "ev.db"))
    unsub = event_bridge.subscribe(seen_events.append)

    import operations.executors as ex
    monkeypatch.setattr(ex, "REGISTRY", {
        "candidate_sprint": ex.ExecutorSpec(
            "candidate_sprint", "ResearchAgent",
            "operations.agents.research_agent:handle_sprint", 604800),
    })

    ok_payload = {"metrics": {}}
    assert ex.try_handle("candidate_sprint", ok_payload) is True
    beats_ok = sup.overall_health()["principals"].get("ResearchAgent")

    def _boom(payload, context=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(research_agent, "handle_sprint", _boom)
    # Failure must NOT be consumed: try_handle returns False so the legacy
    # chain picks the work up (work is never silently lost), while the
    # failure is still journaled via supervisor beat + EXECUTOR_FAILED.
    assert ex.try_handle("candidate_sprint", {}) is False

    unsub()
    assert any(e.event_type == "EXECUTOR_FAILED" for e in seen_events)
    assert beats_ok is not None
    assert sup.overall_health()["principals"]["ResearchAgent"]["ok"] is False
