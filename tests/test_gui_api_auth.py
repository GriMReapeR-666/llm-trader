import os
import sys
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from ui.gui_api import (
    app,
    get_configured_api_token,
    get_engine_internal_token,
    is_request_authenticated,
    is_valid_host,
    get_token_file_path,
)
from operations.strategy_factory import get_factory


@pytest.fixture
def valid_token(monkeypatch):
    token = "secret-test-token-123"  # secret-scan: allow
    monkeypatch.setenv("GUI_API_TOKEN", token)
    return token


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_api_health_allows_access_without_token(client):
    """Health route is public and does not require authentication."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_wrong_host_header_returns_400(client, valid_token):
    """Host header must be 127.0.0.1:<port> or localhost:<port>; any other returns 400 Bad Request."""
    # 1. External domain (DNS rebinding attempt)
    resp = client.get("/api/health", headers={"Host": "attacker.com:8000"})
    assert resp.status_code == 400
    assert "Host" in resp.json().get("error", "")

    # 2. Evil subdomain
    resp2 = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
        headers={"Host": "rebind.attacker.org", "Authorization": f"Bearer {valid_token}"},  # secret-scan: allow
    )
    assert resp2.status_code == 400
    assert "Host" in resp2.json().get("error", "")

    # 3. LAN IP
    resp3 = client.get("/api/health", headers={"Host": "192.168.1.100:8000"})
    assert resp3.status_code == 400


def test_valid_host_headers_accepted(client):
    """Valid Host headers: 127.0.0.1:<port> and localhost:<port>."""
    resp1 = client.get("/api/health", headers={"Host": "127.0.0.1:8000"})
    assert resp1.status_code == 200

    resp2 = client.get("/api/health", headers={"Host": "localhost:8000"})
    assert resp2.status_code == 200

    resp3 = client.get("/api/health", headers={"Host": "127.0.0.1"})
    assert resp3.status_code == 200


def test_unauthenticated_requests_to_control_routes_get_401(client, valid_token):
    """Unauthenticated requests to control routes must get 401."""
    # /api/control/start
    resp_start = client.post("/api/control/start", json={"symbol": "EURUSD"})
    assert resp_start.status_code == 401
    assert "Unauthorized" in resp_start.json().get("error", "")

    # /api/control/approve
    resp_appr = client.post("/api/control/approve", json={"approval_id": "appr-1", "decision": "APPROVED"})
    assert resp_appr.status_code == 401

    # /api/config/accounts
    resp_cfg = client.post("/api/config/accounts", json={"name": "test"})
    assert resp_cfg.status_code == 401


def test_query_param_token_is_rejected(client, valid_token):
    """Authentication via ?token= query parameter is strictly rejected."""
    resp = client.post(
        f"/api/control/start?token={valid_token}",
        json={"symbol": "EURUSD"},
    )
    assert resp.status_code == 401
    assert "Unauthorized" in resp.json().get("error", "")


def test_valid_bearer_token_and_valid_host_succeeds(client, valid_token):
    """Valid Bearer token and valid Host succeeds."""
    resp = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
        headers={"Authorization": f"Bearer {valid_token}", "Host": "127.0.0.1:8000"},  # secret-scan: allow
    )
    # Passed authentication gate
    assert resp.status_code != 401
    assert resp.status_code != 400


def test_valid_x_api_key_header_succeeds(client, valid_token):
    """X-API-Key header is accepted as alternate header authentication."""
    resp = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
        headers={"X-API-Key": valid_token, "Host": "localhost:8000"},
    )
    assert resp.status_code != 401
    assert resp.status_code != 400


def test_actor_cannot_be_forged_in_control_approve(client, valid_token, tmp_path, monkeypatch):
    """Actor in request body is ignored; derived from authenticated session."""
    factory = get_factory()
    test_decisions = str(tmp_path / "decisions.jsonl")
    monkeypatch.setattr(factory, "decisions_file", test_decisions)
    forged_actor = "operator_antigravity"
    resp = client.post(
        "/api/control/approve",
        json={
            "approval_id": "forged_test_appr_1",
            "decision": "APPROVED",
            "actor": forged_actor,
        },
        headers={"Authorization": f"Bearer {valid_token}"},  # secret-scan: allow
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True

    decisions_path = factory.decisions_file
    with open(decisions_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    matching = [r for r in records if r.get("approval_id") == "forged_test_appr_1"]
    assert len(matching) > 0
    actual_record = matching[-1]
    assert actual_record["actor"] != forged_actor
    assert actual_record["actor"] == "authenticated_operator"


def test_cookie_session_authenticates_requests(client, valid_token):
    """POST /api/auth/login mints a SameSite=Strict; HttpOnly session cookie; public GET / never does."""
    # 1. Public index never mints auth material (single-door auth rule).
    resp = client.get("/")
    assert resp.status_code == 200
    assert "gui_session" not in resp.cookies

    # 2. Login with the install-time token mints the cookie.
    resp_login = client.post("/api/auth/login", json={"token": valid_token})
    assert resp_login.status_code == 200
    assert "gui_session" in resp_login.cookies
    assert resp_login.cookies["gui_session"] == valid_token

    # 3. Control route accepts the session cookie
    client.cookies.set("gui_session", valid_token)
    resp_ctrl = client.post(
        "/api/control/start",
        json={"symbol": "EURUSD"},
    )
    assert resp_ctrl.status_code != 401
    assert resp_ctrl.status_code != 400


def test_internal_update_requires_engine_token(client, monkeypatch):
    """POST /api/internal/update requires separate ENGINE_INTERNAL_TOKEN."""
    engine_token = "engine-token-xyz-123"  # secret-scan: allow
    gui_token = "gui-token-abc-456"  # secret-scan: allow
    monkeypatch.setenv("ENGINE_INTERNAL_TOKEN", engine_token)
    monkeypatch.setenv("GUI_API_TOKEN", gui_token)

    # 1. No token -> 401
    r_none = client.post("/api/internal/update", json={"type": "macro_state", "data": {}})
    assert r_none.status_code == 401
    assert "engine token" in r_none.json().get("error", "").lower()

    # 2. GUI API token -> 401 (GUI token cannot modify internal engine state)
    r_gui = client.post(
        "/api/internal/update",
        json={"type": "macro_state", "data": {}},
        headers={"Authorization": f"Bearer {gui_token}"},  # secret-scan: allow
    )
    assert r_gui.status_code == 401

    # 3. Valid engine token -> 200
    r_eng = client.post(
        "/api/internal/update",
        json={"type": "macro_state", "data": {}},
        headers={"Authorization": f"Bearer {engine_token}"},  # secret-scan: allow
    )
    assert r_eng.status_code == 200
    assert r_eng.json() == {"status": "ok"}


def test_strict_auth_on_read_routes(client, valid_token, monkeypatch):
    """When GUI_STRICT_AUTH is on (default), read routes require authentication."""
    # 1. Default strict auth (on): unauthenticated /api/snapshot -> 401
    r_unauth = client.get("/api/snapshot")
    assert r_unauth.status_code == 401

    # 2. Authenticated with Bearer token -> 200
    r_auth = client.get("/api/snapshot", headers={"Authorization": f"Bearer {valid_token}"})  # secret-scan: allow
    assert r_auth.status_code == 200

    # 3. Strict auth disabled (GUI_STRICT_AUTH=0) -> 200 unauthenticated
    monkeypatch.setenv("GUI_STRICT_AUTH", "0")
    r_relaxed = client.get("/api/snapshot")
    assert r_relaxed.status_code == 200


def test_token_file_generation_and_permissions(tmp_path, monkeypatch):
    """If no token env var and no file exists, a secure 32-byte hex token is generated with 0600 permissions."""
    monkeypatch.delenv("GUI_API_TOKEN", raising=False)
    monkeypatch.delenv("GUI_API_KEY", raising=False)
    test_token_file = tmp_path / "subdir" / "api.token"
    monkeypatch.setenv("GUI_API_TOKEN_FILE", str(test_token_file))

    assert not test_token_file.exists()
    token = get_configured_api_token()
    assert token is not None
    assert len(token) == 64  # 32 bytes hex = 64 hex chars
    assert test_token_file.exists()
    assert test_token_file.read_text(encoding="utf-8").strip() == token

    if sys.platform != "win32":
        import stat
        file_mode = test_token_file.stat().st_mode & 0o777
        assert file_mode == 0o600

    # Second read returns the exact same token from file
    assert get_configured_api_token() == token


def test_websocket_rejects_unauthorized_and_wrong_host(monkeypatch):
    """WebSocket connection validates Host header and rejects unauthenticated connections."""
    monkeypatch.setenv("GUI_API_TOKEN", "ws-secret-token-789")  # secret-scan: allow
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. Unauthenticated -> rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/harness-events"):
            pass

    # 2. Wrong host -> rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/harness-events", headers={"Host": "attacker.com"}):
            pass

    # 3. Authenticated with valid Bearer token -> succeeds
    with client.websocket_connect(
        "/ws/harness-events",
        headers={"Authorization": "Bearer ws-secret-token-789", "Host": "127.0.0.1:8000"},  # secret-scan: allow
    ) as ws:
        assert ws is not None
