"""The autonomous sizing route — evidence decides, fail-closed.

The route is the contract's Phase 2 exit criteria turned into a machine
check (docs/PROJECT_SUCCESS_CONTRACT.md). A snapshot that does not prove
every precondition keeps the legacy route; nothing else can.
"""
import json
import time

import pytest

from core import sizing_route
from core.sizing_route import CARVER, LEGACY, RouteDecision, select_route


def _snapshot(**overrides):
    snapshot = {
        "taken_at": 1_000_000.0,
        "track_record": {
            "ready": True,
            "broker_reconciled_fills": 200,
            "admissible_rows": 200,
            "execution_authority": True,
        },
        "rollout": {"active": True, "evidence_valid": True},
        "null_provenance_rows": 0,
        "absolute_path_artifacts": 0,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(snapshot.get(key), dict):
            snapshot[key] = {**snapshot[key], **value}
        else:
            snapshot[key] = value
    return snapshot


def test_all_evidence_green_flips_to_the_carver_route():
    decision = select_route(_snapshot(), now=1_000_000.0)
    assert decision.route == CARVER
    assert decision.is_carver
    assert decision.reasons == ()


def test_no_snapshot_keeps_the_legacy_route():
    decision = select_route(None, now=1_000_000.0)
    assert decision.route == LEGACY
    assert "no evidence snapshot" in decision.reasons[0]


def test_empty_snapshot_keeps_the_legacy_route():
    decision = select_route({}, now=1_000_000.0)
    assert decision.route == LEGACY
    assert decision.reasons


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"track_record": {"ready": False}}, "not ready"),
        ({"track_record": {"broker_reconciled_fills": 199}}, "broker-reconciled"),
        ({"track_record": {"admissible_rows": 199}}, "admissible"),
        ({"track_record": {"execution_authority": False}}, "execution authority"),
        ({"rollout": {"active": False}}, "rollout approval"),
        ({"rollout": {"evidence_valid": False}}, "evidence is missing or has drifted"),
        ({"null_provenance_rows": 1}, "no provenance"),
        ({"absolute_path_artifacts": 1}, "absolute path"),
    ],
)
def test_each_unproven_precondition_keeps_the_legacy_route(overrides, expected):
    decision = select_route(_snapshot(**overrides), now=1_000_000.0)
    assert decision.route == LEGACY
    assert any(expected in reason for reason in decision.reasons), decision.reasons


def test_a_stale_snapshot_cannot_authorise_the_switch():
    decision = select_route(_snapshot(taken_at=1_000_000.0), now=1_000_000.0 + 7200.0)
    assert decision.route == LEGACY
    assert any("old" in reason for reason in decision.reasons)


def test_current_route_reads_the_snapshot(tmp_path):
    path = tmp_path / "route.json"
    path.write_text(json.dumps(_snapshot(taken_at=time.time())), encoding="utf-8")
    sizing_route.reset_cache()
    decision = sizing_route.current_route(snapshot_path=str(path))
    assert decision.route == CARVER


def test_current_route_survives_a_broken_snapshot(tmp_path):
    path = tmp_path / "route.json"
    path.write_text("{not json", encoding="utf-8")
    sizing_route.reset_cache()
    decision = sizing_route.current_route(snapshot_path=str(path))
    assert decision.route == LEGACY


def test_current_route_never_raises_when_the_snapshot_is_missing(tmp_path):
    sizing_route.reset_cache()
    decision = sizing_route.current_route(snapshot_path=str(tmp_path / "absent.json"))
    assert decision.route == LEGACY


def test_route_decision_serialises():
    decision = RouteDecision(route=LEGACY, reasons=("because",), evidence={"a": 1})
    payload = decision.to_dict()
    assert payload["route"] == LEGACY
    assert payload["reasons"] == ["because"]
    assert payload["evidence"] == {"a": 1}
