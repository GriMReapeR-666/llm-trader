"""Pin tests: Laya DecisionBundle → cycle-prompt advisory wiring.

The MacroAnalyst graph node publishes its typed-decision bundle (D3 regime,
D4 liquidity, D8 arbitration, D9 kill switch, D12 narrative decay, …) to
``core.shared_state.ui_state.last_decision_bundle``. The orchestrator
projects a compact advisory onto the cycle toon (``laya_decision_bundle``)
so the scalp/swing candle engines see macro state directly.

Pinned here:
- projection: per-head selected_option + top-k probabilities + confidence +
  veto flag; bundle-level verdict/risk multiplier; malformed inputs -> None;
- attachment: AVAILABLE for a fresh bundle, UNAVAILABLE (stale_bundle) past
  the 10-minute staleness bound, UNAVAILABLE when never published or
  malformed, ERROR-path containment when shared_state itself fails;
- authority invariant: every emitted shape carries execution_authority=False;
- prompt plumbing: the sanitize whitelist passes the advisory through.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from mt5_trader import LLMOrchestrator


def _bundle(age_s: int = 0) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {
        "bundle_id": "BUNDLE_abc123_1727500000",
        "timestamp": ts,
        "arbitration_verdict": "PROCEED",
        "risk_multiplier": 0.5,
        "veto_enforced": True,
        "decisions": {
            "D3_macro_regime": {
                "selected_option": "RISK_ON_EXPANSION",
                "probabilities": {"RISK_ON_EXPANSION": 0.62, "NORMAL_RISK_OFF": 0.30,
                                  "STAGFLATION_SHOCK": 0.05, "CRISIS_PANIC": 0.02,
                                  "JUNK": 0.01},
                "confidence": 0.9, "is_veto": False,
            },
            "D9_emergency_kill_switch": {
                "selected_option": "KILL_SWITCH_ACTIVE",
                "probabilities": {"KILL_SWITCH_ACTIVE": 0.9, "NORMAL_OPERATION": 0.1},
                "confidence": 0.95, "is_veto": True,
            },
        },
    }


class _UIStub:
    def __init__(self, bundle):
        self.last_decision_bundle = bundle


def test_projection_shapes_heads_and_bundle_level_fields():
    proj = LLMOrchestrator._project_decision_bundle(_bundle())
    assert proj is not None
    d3 = proj["heads"]["D3_macro_regime"]
    assert d3["selected_option"] == "RISK_ON_EXPANSION"
    # top-k only, sorted desc, junk dropped
    assert list(d3["probabilities"]) == ["RISK_ON_EXPANSION", "NORMAL_RISK_OFF",
                                         "STAGFLATION_SHOCK", "CRISIS_PANIC"]
    assert d3["confidence"] == 0.9 and d3["is_veto"] is False
    d9 = proj["heads"]["D9_emergency_kill_switch"]
    assert d9["is_veto"] is True
    assert proj["arbitration_verdict"] == "PROCEED"
    assert proj["risk_multiplier"] == 0.5
    assert proj["veto_enforced"] is True
    assert proj["execution_authority"] is False
    assert proj["source"] == "laya_decision_bundle"


def test_projection_rejects_malformed_payloads():
    assert LLMOrchestrator._project_decision_bundle(None) is None
    assert LLMOrchestrator._project_decision_bundle({}) is None
    assert LLMOrchestrator._project_decision_bundle({"decisions": {"D3": "junk"}}) is None
    # no recognized decision heads -> None
    assert LLMOrchestrator._project_decision_bundle(
        {"decisions": {"D99_unknown": {"selected_option": "X"}}}) is None


def test_attach_available_fresh_bundle(monkeypatch):
    import core.shared_state as ss
    monkeypatch.setattr(ss, "ui_state", _UIStub(_bundle(age_s=30)), raising=False)
    toon = {}
    LLMOrchestrator._attach_decision_bundle_advisory(toon)
    adv = toon["laya_decision_bundle"]
    assert adv["status"] == "AVAILABLE"
    assert adv["bundle_age_s"] is not None and adv["bundle_age_s"] >= 0
    assert "D3_macro_regime" in adv["heads"]
    assert adv["execution_authority"] is False


def test_attach_stale_bundle_is_unavailable(monkeypatch):
    import core.shared_state as ss
    monkeypatch.setattr(ss, "ui_state", _UIStub(_bundle(age_s=3600)), raising=False)
    toon = {}
    LLMOrchestrator._attach_decision_bundle_advisory(toon)
    adv = toon["laya_decision_bundle"]
    assert adv["status"] == "UNAVAILABLE"
    assert adv["reason"] == "stale_bundle"
    assert adv["execution_authority"] is False


def test_attach_missing_or_malformed_bundle_is_unavailable(monkeypatch):
    import core.shared_state as ss
    monkeypatch.setattr(ss, "ui_state", _UIStub(None), raising=False)
    toon = {}
    LLMOrchestrator._attach_decision_bundle_advisory(toon)
    assert toon["laya_decision_bundle"]["status"] == "UNAVAILABLE"

    monkeypatch.setattr(ss, "ui_state", _UIStub({"decisions": "junk"}), raising=False)
    toon2 = {}
    LLMOrchestrator._attach_decision_bundle_advisory(toon2)
    assert toon2["laya_decision_bundle"]["reason"] == "malformed_bundle"


def test_attach_survives_shared_state_failure(monkeypatch):
    import core.shared_state as ss

    class _Boom:
        @property
        def last_decision_bundle(self):
            raise RuntimeError("ui_state exploded")

    monkeypatch.setattr(ss, "ui_state", _Boom(), raising=False)
    toon = {}
    LLMOrchestrator._attach_decision_bundle_advisory(toon)
    assert toon["laya_decision_bundle"]["status"] == "UNAVAILABLE"
    assert toon["laya_decision_bundle"]["execution_authority"] is False


def test_sanitize_whitelist_passes_bundle_advisory_through():
    toon = {"laya_decision_bundle": {"status": "AVAILABLE", "heads": {},
                                      "execution_authority": False}}
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60000)
    assert out["laya_decision_bundle"]["status"] == "AVAILABLE"
    # and it is JSON-serializable for the prompt builder
    assert json.dumps(out["laya_decision_bundle"])
