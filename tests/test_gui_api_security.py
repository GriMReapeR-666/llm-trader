import os
import pytest
from starlette.testclient import TestClient
from ui.gui_api import app, get_configured_api_token

client = TestClient(app)

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
    assert resp.status_code == 403
