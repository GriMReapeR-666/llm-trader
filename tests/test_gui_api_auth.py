import pytest
from fastapi.testclient import TestClient
from ui.gui_api import app, get_configured_api_token, is_request_authenticated


def test_api_allows_access_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("GUI_API_TOKEN", raising=False)
    monkeypatch.delenv("GUI_API_KEY", raising=False)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_api_control_blocks_unauthorized_when_token_configured(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post("/api/control/start", json={"symbol": "EURUSD"})
    assert resp.status_code == 401
    assert "Unauthorized" in resp.json().get("error", "")


def test_api_control_allows_authorized_bearer_token(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
        headers={"Authorization": "Bearer secret-test-token-123"},  # secret-scan: allow
    )
    # The request got past the 401 auth gate!
    assert resp.status_code != 401


def test_api_control_allows_authorized_x_api_key(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
        headers={"X-API-Key": "secret-test-token-123"},
    )
    assert resp.status_code != 401


def test_api_control_allows_authorized_query_param(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    resp = client.post(
        "/api/control/start?token=secret-test-token-123",
        json={"symbol": "EURUSD"},
    )
    assert resp.status_code != 401


def test_websocket_rejects_unauthorized_when_token_configured(monkeypatch):
    monkeypatch.setenv("GUI_API_TOKEN", "secret-test-token-123")
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/harness-events") as ws:
            pass
