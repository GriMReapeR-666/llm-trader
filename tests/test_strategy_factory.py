"""Strategy factory governance round-trip tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.strategy_factory import StrategyFactory  # noqa: E402
from operations.autonomy import AutonomyManager  # noqa: E402


def _paths(tmp_path):
    return (
        str(tmp_path / "registry.json"),
        str(tmp_path / "outbox.jsonl"),
        str(tmp_path / "decisions.jsonl"),
    )


def test_candidate_promotion_roundtrip(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "s1", "name": "alpha_v1", "stage": "CANDIDATE",
                    "trades": 0, "win_rate": 0, "profit_factor": 0})
    res = factory.evaluate("s1", {"trades": 45, "win_rate": 55.0,
                                  "profit_factor": 1.4, "max_dd": 8.0})
    assert res["ok"] is True and res["gate_progress"] == 1.0
    pending = [a for a in factory.pending_approvals() if a["kind"] == "PROMOTION"]
    assert len(pending) == 1 and pending[0]["status"] == "PENDING"

    factory.write_decision(pending[0]["id"], "APPROVED", actor="op")
    s = factory.get("s1")
    assert s["stage"] == "EVALUATION" and s["promotion_pending"] == "DONE"
    statuses = {a["id"]: a["status"] for a in factory.pending_approvals()}
    assert statuses[pending[0]["id"]] == "APPROVED"


def test_evaluation_gate_creates_full_request_and_applies(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "s2", "name": "beta_v2", "stage": "EVALUATION"})
    res = factory.evaluate("s2", {"trades": 200, "win_rate": 57.0,
                                  "profit_factor": 1.6, "sharpe": 1.3,
                                  "max_dd": 9.0})
    assert res["gate_progress"] == 1.0
    pending = [a for a in factory.pending_approvals() if a["strategyId"] == "s2"]
    assert pending and pending[0]["title"].endswith("→ FULL")
    factory.write_decision(pending[0]["id"], "APPROVED")
    assert factory.get("s2")["stage"] == "FULL"


def test_auto_quarantine_on_broken_pf(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "s3", "name": "gamma_v0", "stage": "FULL"})
    res = factory.evaluate("s3", {"trades": 40, "profit_factor": 0.7, "max_dd": 5.0})
    assert res["transition"] == "QUARANTINE"
    assert factory.get("s3")["stage"] == "QUARANTINE"


def test_l3_rollout_requires_signature_via_autonomy(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    state, control = str(tmp_path / "auto.json"), str(tmp_path / "ctl.jsonl")
    autonomy = AutonomyManager(state, control)
    autonomy.escalate("L2_MICRO", "baseline")

    factory = StrategyFactory(reg, outbox, decisions, autonomy_manager=autonomy)
    approval = factory.request_l3_rollout("XAUUSD book", "mf-test-42")

    factory.write_decision(approval["id"], "APPROVED")
    snap = autonomy.snapshot()
    assert snap.level == "L3_FULL"


def test_rejected_promotion_keeps_stage(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "s4", "name": "delta_v1", "stage": "EVALUATION"})
    factory.evaluate("s4", {"trades": 300, "win_rate": 60.0,
                            "profit_factor": 1.9, "sharpe": 1.5, "max_dd": 6.0})
    pending = [a for a in factory.pending_approvals() if a["strategyId"] == "s4"]
    factory.write_decision(pending[0]["id"], "REJECTED")
    assert factory.get("s4")["stage"] == "EVALUATION"


def test_decisions_idempotent_across_calls(tmp_path):
    reg, outbox, decisions = _paths(tmp_path)
    factory = StrategyFactory(reg, outbox, decisions)
    factory.request_approval({"kind": "CONFIG_CHANGE", "title": "tweak",
                              "detail_lines": ["x"], "signature_required": False})
    first = factory.pending_approvals()[0]
    factory.write_decision(first["id"], "APPROVED")
    applied_twice = factory.process_decisions()
    assert applied_twice == []


# --- CRITICAL #10: the invalidation marker must describe the rows next to it ---

def _marked_registry(n_strategies):
    """A registry shaped like strategy_registry.json: rows plus a marker whose
    counts were captured when the verdict was written."""
    return {
        "strategies": [
            {"id": "s%d" % i, "stage": "CANDIDATE" if i else "FULL"}
            for i in range(n_strategies)
        ],
        "_EVIDENCE_INVALID": {
            "verdict": "promoted_metrics_unbacked",
            "strategy_count": n_strategies,
            "stages": {"FULL": 1, "CANDIDATE": n_strategies - 1},
            "why": ["no code pointer"],
            "action": "do not trade this",
        },
    }


def _write(path, data):
    from core.file_utils import safe_atomic_write_json
    safe_atomic_write_json(path, data)


def test_write_heals_marker_count(tmp_path):
    """Appending a strategy must move the marker's count with it."""
    import json

    reg = str(tmp_path / "registry.json")
    _write(reg, _marked_registry(3))
    factory = StrategyFactory(reg, str(tmp_path / "o.jsonl"), str(tmp_path / "d.jsonl"))

    factory.upsert({"id": "new", "name": "gamma", "stage": "FULL"})

    saved = json.load(open(reg, encoding="utf-8"))
    m = saved["_EVIDENCE_INVALID"]
    assert m["strategy_count"] == len(saved["strategies"]) == 4, (
        "marker count went stale on write: %r" % m["strategy_count"])
    assert m["stages"]["FULL"] == 2 and m["stages"]["CANDIDATE"] == 2, m["stages"]


def test_heal_never_fabricates_or_downgrades_the_marker(tmp_path):
    """Self-healing must not invent a verdict, nor edit the audit text."""
    from core.file_utils import heal_strategy_registry_marker

    # 1. No marker in, no marker out — nobody reviewed this file.
    plain = {"strategies": [{"id": "a", "stage": "CANDIDATE"}]}
    assert "_EVIDENCE_INVALID" not in heal_strategy_registry_marker(plain)

    # 2. Verdict text is untouched; only the derived counts move.
    marked = _marked_registry(5)
    marked["strategies"].append({"id": "b", "stage": "RETIRED"})
    heal_strategy_registry_marker(marked)
    m = marked["_EVIDENCE_INVALID"]
    assert m["verdict"] == "promoted_metrics_unbacked"
    assert m["why"] == ["no code pointer"] and m["action"] == "do not trade this"
    assert m["strategy_count"] == 6
    # a stage the marker never mentioned still shows up honestly
    assert m["stages"]["RETIRED"] == 1

    # 3. A registry with no marker key at all is left completely alone.
    assert heal_strategy_registry_marker({"strategies": []}) == {"strategies": []}


def test_malformed_registry_does_not_lose_the_marker(tmp_path):
    """A strategies key of the wrong type must not strip the disclosure."""
    import json

    reg = str(tmp_path / "registry.json")
    broken = _marked_registry(2)
    broken["strategies"] = "not-a-list"
    _write(reg, broken)

    factory = StrategyFactory(reg, str(tmp_path / "o.jsonl"), str(tmp_path / "d.jsonl"))
    factory.upsert({"id": "s1", "name": "alpha", "stage": "CANDIDATE"})

    saved = json.load(open(reg, encoding="utf-8"))
    assert saved["_EVIDENCE_INVALID"]["verdict"] == "promoted_metrics_unbacked", (
        "the invalidation marker was dropped from a file live code still reads")
    assert saved["_EVIDENCE_INVALID"]["strategy_count"] == len(saved["strategies"])
