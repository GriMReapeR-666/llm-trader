import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from ui.gui_api import app, get_configured_api_token

client = TestClient(app)

ROOT_REGISTRY = Path(__file__).resolve().parents[1] / "strategy_registry.json"


@pytest.fixture(autouse=True)
def isolated_strategy_registry(tmp_path, monkeypatch):
    """Keep the strategy registry out of the repo for the whole module.

    ``/api/control/strategy_stage`` calls ``get_factory()``, the module-level
    singleton whose default ``registry_file`` is the TRACKED
    ``<repo>/strategy_registry.json``.  These tests post a payload with no
    ``strategy_id``, so the endpoint set an empty id's stage and rewrote the
    real registry on every suite run - appending an ``id: ""`` record and
    bumping the evidence-marker counts.  Point the singleton at tmp_path.
    """
    from operations import strategy_factory as sf

    registry = tmp_path / "strategy_registry.json"
    registry.write_text(json.dumps({"strategies": []}), encoding="utf-8")
    factory = sf.StrategyFactory(
        str(registry),
        str(tmp_path / "outbox.jsonl"),
        str(tmp_path / "decisions.jsonl"),
    )
    monkeypatch.setattr(sf, "_factory", factory, raising=False)
    yield factory


def test_unauthenticated_request_to_control_route_is_rejected():
    resp = client.post("/api/control/approve", json={"actor": "attacker", "decision": "APPROVED"})
    assert resp.status_code == 401
    assert "unauthorized" in resp.text.lower()

def test_authenticated_request_to_control_route_is_admitted():
    token = get_configured_api_token()
    assert token is not None and len(token) >= 16
    resp = client.post(
        "/api/control/strategy_stage",
        json={"strategy": "test_strat", "stage": "CANDIDATE"},
        headers={"Authorization": f"Bearer {token}"}
    )
    # Status should not be 401 Unauthorized
    assert resp.status_code != 401


def test_strategy_stage_control_never_writes_the_repo_registry():
    """The admitted-request path must not reach the tracked registry.

    Regression: this module used to append an ``id: ""`` record to
    ``<repo>/strategy_registry.json`` on every run.  Assert the isolation
    fixture is doing the work, so a future edit that drops it fails here
    instead of silently dirtying production data.
    """
    assert ROOT_REGISTRY.exists(), "the tracked registry should still be present"
    before = ROOT_REGISTRY.read_bytes()
    token = get_configured_api_token()
    client.post(
        "/api/control/strategy_stage",
        json={"stage": "CANDIDATE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ROOT_REGISTRY.read_bytes() == before, (
        "the control endpoint mutated the tracked strategy_registry.json")

def test_cors_does_not_wildcard_all_origins():
    resp = client.options(
        "/api/control/approve",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        }
    )
    allow_origin = resp.headers.get("access-control-allow-origin", "")
    assert allow_origin != "*"
    assert "evil.example.com" not in allow_origin

def test_host_header_validation_rejects_rebinding():
    token = get_configured_api_token()
    resp = client.post(
        "/api/control/strategy_stage",
        json={"strategy": "test_strat", "stage": "CANDIDATE"},
        headers={"Authorization": f"Bearer {token}", "Host": "evil.example.com"}
    )
    # 400, not 403: origin/main's contract is authoritative after the merge
    # (see tests/test_gui_api_auth.py::test_wrong_host_header_returns_400,
    # which pins 400 explicitly for the same condition). The security property
    # asserted here is that a rebinding host is refused before the handler
    # runs - the exact status class is the part that had to be reconciled.
    assert resp.status_code == 400
