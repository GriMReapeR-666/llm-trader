import os
import pytest
from fastapi.testclient import TestClient
from gui_api import app, ui_state


@pytest.fixture(autouse=True)
def _setup_api_test_env(monkeypatch):
    engine_token = "test-engine-internal-token-1234"  # secret-scan: allow
    gui_token = "test-gui-api-token-1234"  # secret-scan: allow
    monkeypatch.setenv("ENGINE_INTERNAL_TOKEN", engine_token)
    monkeypatch.setenv("GUI_API_TOKEN", gui_token)
    monkeypatch.setenv("GUI_STRICT_AUTH", "0")


def _get_client() -> TestClient:
    engine_token = "test-engine-internal-token-1234"  # secret-scan: allow
    c = TestClient(app, base_url="http://127.0.0.1:8000")
    c.headers.update({"Authorization": f"Bearer {engine_token}"})
    return c


def test_gui_api_graph_status():
    client = _get_client()

    # Update the gui_api module's own ui_state directly
    ui_state.active_graph_node = "technical_net"
    ui_state.graph_audit_trail.append("TechnicalNet: Indicators calculated.")
    ui_state.node_statuses["technical_net"] = "RUNNING"

    response = client.get("/api/graph/status")
    assert response.status_code == 200
    data = response.json()
    assert "activeNode" in data
    assert data["activeNode"] == "technical_net"
    assert "nodeStatuses" in data
    assert data["nodeStatuses"]["technical_net"] in ("RUNNING", "COMPLETED")
    assert len(data["auditTrail"]) >= 1


def test_gui_api_opentui_macro_endpoints():
    client = _get_client()

    # 1. Snapshot
    res_snap = client.get("/api/snapshot")
    assert res_snap.status_code == 200

    # 2. Economic Events
    res_events = client.get("/api/economic-events")
    assert res_events.status_code == 200
    assert isinstance(res_events.json(), list)

    # 3. Intermarket
    res_inter = client.get("/api/intermarket")
    assert res_inter.status_code == 200

    # 4. Model Registry
    res_models = client.get("/api/model-registry")
    assert res_models.status_code == 200

    # 5. Causal Fusion
    res_causal = client.get("/api/causal-fusion")
    assert res_causal.status_code == 200
    causal_data = res_causal.json()
    assert "causalChainPaths" in causal_data


def test_gui_api_internal_update_macro_state_bridge():
    """Trader-process macro telemetry must reach the GUI snapshot via the bridge."""
    client = _get_client()

    res = client.post("/api/internal/update", json={
        "type": "macro_state",
        "data": {
            "macro_regime": "CRISIS_PANIC",
            "net_liquidity_stance": "LIQUIDITY_CRUNCH",
            "active_narrative_summary": "Liquidity crunch: reserves draining",
            "active_tripwires_status": [{"symbol": "GLOBAL_MACRO", "condition": "vix > 35", "armed": True}],
            "last_decision_bundle": {"bundle_id": "BUNDLE_test", "d3_regime": "CRISIS_PANIC"},
            "last_causal_attribution": {"primary_driver": "liquidity"},
        },
    })
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    snap = client.get("/api/snapshot").json()
    macro = snap["macro_state"]
    assert macro["regime"] == "CRISIS_PANIC"
    assert macro["net_liquidity"] == "LIQUIDITY_CRUNCH"
    assert macro["summary"] == "Liquidity crunch: reserves draining"
    assert macro["tripwires"][0]["condition"] == "vix > 35"
    assert macro["last_bundle"]["bundle_id"] == "BUNDLE_test"
    assert macro["causal_attribution"]["primary_driver"] == "liquidity"

    # Partial update: only the sent keys change; others keep their last value.
    res = client.post("/api/internal/update", json={
        "type": "macro_state",
        "data": {"macro_regime": "RISK_ON_EXPANSION"},
    })
    assert res.status_code == 200
    snap = client.get("/api/snapshot").json()
    macro = snap["macro_state"]
    assert macro["regime"] == "RISK_ON_EXPANSION"
    assert macro["net_liquidity"] == "LIQUIDITY_CRUNCH"
    assert macro["last_bundle"]["bundle_id"] == "BUNDLE_test"

    # Missing 'data' must not 500 (boundary: empty payload).
    assert client.post("/api/internal/update", json={"type": "macro_state"}).status_code == 200
    # Unknown type is a no-op, not an error.
    assert client.post("/api/internal/update", json={"type": "unknown_type", "data": {}}).status_code == 200


def test_gui_api_no_fake_data_and_laya_registry():
    """Verify no fake/mock fallbacks: Laya metrics default to honest zeros and
    only become live values when the trader reports them via /api/internal/update."""
    client = _get_client()

    # 1. Model Registry includes Laya ModernBERT-421M with honest defaults
    res_models = client.get("/api/model-registry")
    assert res_models.status_code == 200
    models = res_models.json()
    assert isinstance(models, list)
    laya = next((m for m in models if m["id"] == "laya-modernbert-421m"), None)
    assert laya is not None, "Laya ModernBERT-421M must be registered in model-registry"
    # No fake seeded metrics: absent trader data, values are 0 — never fabricated.
    assert laya["metrics"]["validated_accuracy"] == 0
    assert laya["metrics"]["freeze_recall"] == 0
    assert laya["metrics"]["win_rate"] == 0

    # 1b. Trader-reported metrics flow through the internal update bridge.
    res_upd = client.post("/api/internal/update", json={
        "type": "laya_metrics",
        "data": {"validated_accuracy": 1.0, "freeze_recall": 1.0, "win_rate": 0.625},
    })
    assert res_upd.status_code == 200
    laya_live = next(
        (m for m in client.get("/api/model-registry").json()
         if m["id"] == "laya-modernbert-421m"), None)
    assert laya_live is not None
    assert laya_live["metrics"]["validated_accuracy"] == 1.0
    assert laya_live["metrics"]["freeze_recall"] == 1.0
    assert laya_live["metrics"]["win_rate"] == 0.625
    # Non-numeric / NaN fields are rejected — the registry must surface
    # honest zeros, never the garbage the sender tried to push.
    client.post("/api/internal/update", json={
        "type": "laya_metrics",
        "data": {"ece": "garbage", "raw_accuracy": None},
    })
    laya_clean = next(
        (m for m in client.get("/api/model-registry").json()
         if m["id"] == "laya-modernbert-421m"), None)
    assert laya_clean["metrics"]["ece"] == 0
    assert laya_clean["metrics"]["raw_accuracy"] == 0

    # 2. Win probability is empirical from DB or None/awaiting (never fake 0.58)
    res_win = client.get("/api/win-probability")
    assert res_win.status_code == 200
    win_data = res_win.json()
    if win_data.get("status") == "AWAITING_CLOSED_TRADES":
        assert win_data.get("winProbability") is None
    else:
        assert isinstance(win_data.get("winProbability"), (int, float))
        assert win_data.get("status") == "EMPIRICAL_BASELINE"
        assert win_data.get("sampleCount", 0) > 0

    # 3. RL training returns empty ppoUpdates when trainer is idle (never synthetic updates)
    res_rl = client.get("/api/rl-training")
    assert res_rl.status_code == 200
    rl_data = res_rl.json()
    assert isinstance(rl_data.get("ppoUpdates"), list)
    if not rl_data.get("isTraining", False):
        assert len(rl_data["ppoUpdates"]) == 0

    # 4. Growth returns empirical stats or awaiting state (no synthetic Monte Carlo)
    res_growth = client.get("/api/growth")
    assert res_growth.status_code == 200
    growth_data = res_growth.json()
    assert "monteCarloSims" in growth_data
    assert "projections" in growth_data
    if growth_data.get("status") == "AWAITING_CLOSED_TRADES":
        assert len(growth_data["monteCarloSims"]) == 0
    else:
        assert len(growth_data["monteCarloSims"]) > 0
