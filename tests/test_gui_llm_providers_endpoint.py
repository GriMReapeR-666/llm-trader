"""`/api/llm-providers` must report the router's real state.

The endpoint used to return a hardcoded list that included Gemini and
DeepSeek (providers with no client implementation anywhere in the tree) and
omitted the ones that do exist. It also used a response shape the TUI never
reads, so `health` silently fell back to the client's hardcoded "healthy" and
`models` to [] -- the deck rendered every provider as a green dot regardless
of the actual state.

The contract these tests pin is the TUI's LLMProvider type
(tui_opentui/src/types.ts:618), consumed by live.ts:1667.
"""

import types as _types

import pytest

from data.llm_router import LLMRouter, OmniRouterProvider, OpenRouterProvider


TUI_PROVIDER_FIELDS = {
    "id", "name", "baseUrl", "models", "health", "rpm", "failoverPriority",
}
VALID_HEALTH = {"healthy", "degraded", "down", "circuit_open"}


@pytest.fixture
def routed(monkeypatch):
    """Point get_router() at a router with one live and one dead provider."""
    from data import llm_router as lr

    monkeypatch.setenv("OMNIROUTER_KEY", "sk-test")

    router = LLMRouter.__new__(LLMRouter)
    router.config = {
        "providers": {
            "openrouter": {"enabled": True, "api_keys": ["sk-or-abc"], "rpm": 60},
            "omnirouter": {"enabled": True, "api_keys": ["sk-omni"], "rpm": 30},
            "cerebras": {"enabled": False, "api_keys": [], "rpm": 30},
        },
        "agents": {"trader": {"provider": "openrouter", "model": "mistralai/mistral-nemo:free"}},
        "global_fallback_order": ["cerebras", "openrouter"],
    }
    router.providers = {
        "openrouter": OpenRouterProvider(["sk-or-abc"]),
        "omnirouter": OmniRouterProvider(["sk-omni"]),
    }
    router.agents = {"trader": {"provider": "openrouter", "model": "mistralai/mistral-nemo:free"}}

    monkeypatch.setattr(lr, "get_router", lambda: router)
    return router


def _call():
    import ui.gui_api as gui_api

    return gui_api._llm_providers()


def _by_id(payload):
    return {p["id"]: p for p in payload}


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_response_matches_the_tui_contract(routed):
    payload = _call()
    assert payload
    for entry in payload:
        assert TUI_PROVIDER_FIELDS <= set(entry), entry["id"]
        assert entry["health"] in VALID_HEALTH, entry
        assert isinstance(entry["models"], list)
        assert isinstance(entry["rpm"], int)


def test_no_phantom_providers(routed):
    """Gemini and DeepSeek have no client implementation anywhere; they must
    not be advertised as available providers."""
    ids = _by_id(_call())
    assert "gemini" not in ids
    assert "deepseek" not in ids


def test_omnirouter_is_advertised(routed):
    entry = _by_id(_call())["omnirouter"]
    assert entry["name"] == "OmniRouter"
    assert entry["health"] == "healthy"


def test_every_configured_provider_is_listed(routed):
    """A configured-but-unusable provider must still be visible, or an
    operator cannot tell 'misconfigured' from 'missing'."""
    assert set(_by_id(_call())) == {"openrouter", "omnirouter", "cerebras"}


# ---------------------------------------------------------------------------
# Health is derived, never defaulted
# ---------------------------------------------------------------------------

def test_provider_without_clients_reports_down(routed):
    assert _by_id(_call())["cerebras"]["health"] == "down"


def test_open_circuit_reports_circuit_open(routed):
    routed.providers["omnirouter"].is_circuit_open = lambda: True
    assert _by_id(_call())["omnirouter"]["health"] == "circuit_open"


def test_quota_pause_reports_degraded(routed):
    routed.providers["omnirouter"].quota_paused_until = 9e12
    assert _by_id(_call())["omnirouter"]["health"] == "degraded"


def test_healthy_provider_is_not_a_hardcoded_default(routed):
    """The TUI defaults a missing `health` field to "healthy", which is how
    this endpoint used to lie. Every entry must carry a real value."""
    for entry in _call():
        assert "health" in entry


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------

def test_base_url_comes_from_the_live_client(routed):
    assert _by_id(_call())["openrouter"]["baseUrl"].rstrip("/").endswith(
        "openrouter.ai/api/v1")


def test_base_url_is_empty_when_not_initialized(routed):
    assert _by_id(_call())["cerebras"]["baseUrl"] == ""


def test_models_are_listed(routed):
    entry = _by_id(_call())["omnirouter"]
    assert "auto" in [m["id"] for m in entry["models"]]


def test_agent_model_is_flagged_default(routed):
    models = {m["id"]: m for m in _by_id(_call())["openrouter"]["models"]}
    assert models["mistralai/mistral-nemo:free"]["isDefault"] is True
    assert models["qwen/qwen3-8b:free"]["isDefault"] is False


def test_failover_priority_follows_the_router_order(routed):
    ids = _by_id(_call())
    assert ids["cerebras"]["failoverPriority"] == 0
    assert ids["openrouter"]["failoverPriority"] == 1
    # Not in the high-reasoning chain at all.
    assert ids["omnirouter"]["failoverPriority"] == -1


def test_rpm_prefers_the_live_value(routed):
    assert _by_id(_call())["openrouter"]["rpm"] == 60


def test_no_api_keys_are_exposed(routed):
    """The old shape published "***abcd" over HTTP; nothing consumed it."""
    import json

    payload = _call()
    blob = json.dumps(payload)
    assert "sk-or-abc" not in blob
    assert "sk-omni" not in blob
    for entry in payload:
        assert "apiKey" not in entry
        assert "api_key" not in entry


# ---------------------------------------------------------------------------
# Failure must not take the API down
# ---------------------------------------------------------------------------

def test_router_import_failure_returns_empty_list(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "data.llm_router":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _call() == []


def test_malformed_router_does_not_raise(monkeypatch):
    from data import llm_router as lr

    broken = _types.SimpleNamespace(providers={}, agents={}, config={})
    monkeypatch.setattr(lr, "get_router", lambda: broken)
    assert _call() == []