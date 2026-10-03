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
