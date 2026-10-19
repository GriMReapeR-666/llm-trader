"""OmniRouter provider integration (github.com/Godde3s/omnirouter).

OmniRouter is the project's *cheap hub*: a self-hosted, OpenAI-compatible
gateway in front of its own built-in bridges (Qwen / GLM / DeepSeek / Gemini).
The high-reasoning chain (NVIDIA / TokenRouter / RoutesMe / OpenRouter) stays
authoritative for trade decisions.

The invariant these tests defend is that separation: OmniRouter must be
available on demand, must route cheaply by default, and must never become a
silent fallback for the agents that place trades.
"""

import json
import types as _types

import pytest

from data.llm_router import LLMRouter, OmniRouterProvider


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _RecordingCompletions:
    """Captures the kwargs the provider sends to the OpenAI SDK."""

    def __init__(self, content="ok"):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _types.SimpleNamespace(
            choices=[_types.SimpleNamespace(
                message=_types.SimpleNamespace(content=self.content))],
            error=None,
        )


def _provider(monkeypatch, max_retries=1):
    """Build an OmniRouterProvider with a fake client and no real network."""
    provider = OmniRouterProvider(["sk-test"], max_retries=max_retries, base_delay=0.0)
    completions = _RecordingCompletions()
    provider.clients = [_types.SimpleNamespace(
        chat=_types.SimpleNamespace(completions=completions))]
    return provider, completions


def _bare_router():
    """An LLMRouter with no providers, built without running __init__."""
    return LLMRouter.__new__(LLMRouter)


# ---------------------------------------------------------------------------
# Model resolution: the gateway routes for itself under "auto"
# ---------------------------------------------------------------------------

def test_empty_model_falls_back_to_auto(monkeypatch):
    """An unset model must become "auto" so the gateway picks and fails over
    across its own built-in bridges instead of the caller guessing an id."""
    provider, completions = _provider(monkeypatch)
    provider.generate([{"role": "user", "content": "summarise this"}], "")
    assert completions.calls[0]["model"] == "auto"


def test_none_model_falls_back_to_auto(monkeypatch):
    provider, completions = _provider(monkeypatch)
    provider.generate([{"role": "user", "content": "summarise this"}], None)
    assert completions.calls[0]["model"] == "auto"


def test_explicit_model_is_passed_through(monkeypatch):
    """An explicit bridge id (provider/model) must reach the gateway intact."""
    provider, completions = _provider(monkeypatch)
    provider.generate([{"role": "user", "content": "x"}], "qwen/qwen3.8-max")
    assert completions.calls[0]["model"] == "qwen/qwen3.8-max"


def test_seed_is_not_sent(monkeypatch):
    """The free web bridges behind "auto" ignore seed; sending it risks a
    400 from the gateway that the other OpenAI-compatible providers accept."""
    provider, completions = _provider(monkeypatch)
    provider.generate([{"role": "user", "content": "x"}], "auto", seed=42)
    assert "seed" not in completions.calls[0]


def test_tool_calling_kwargs_are_forwarded(monkeypatch):
    """Tool-calling subagents are a primary OmniRouter use case."""
    provider, completions = _provider(monkeypatch)
    tools = [{"type": "function", "function": {"name": "t"}}]
    provider.generate([{"role": "user", "content": "x"}], "auto",
                      tools=tools, tool_choice="auto")
    assert completions.calls[0]["tools"] == tools
    assert completions.calls[0]["tool_choice"] == "auto"


def test_content_is_returned(monkeypatch):
    """`_extract_message` unwraps to the SDK message object; the router is what
    reads `.content`. Keep the provider consistent with its siblings."""
    provider, _ = _provider(monkeypatch)
    assert provider.generate([{"role": "user", "content": "x"}], "auto").content == "ok"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _base_url(provider):
    """The SDK normalises base_url to a URL object with a trailing slash."""
    return str(provider.clients[0].base_url).rstrip("/")


def test_base_url_defaults_to_local_gateway(monkeypatch):
    """OmniRouter is self-hosted; its default endpoint is the local dashboard."""
    monkeypatch.delenv("OMNIROUTER_BASE_URL", raising=False)
    assert _base_url(OmniRouterProvider(["sk-test"])) == "http://localhost:8080/v1"


def test_base_url_is_configurable(monkeypatch):
    monkeypatch.setenv("OMNIROUTER_BASE_URL", "http://10.0.0.5:9000/v1")
    assert _base_url(OmniRouterProvider(["sk-test"])) == "http://10.0.0.5:9000/v1"


def test_provider_builds_one_client_per_key(monkeypatch):
    monkeypatch.setenv("OMNIROUTER_KEY_1", "sk-one")
    monkeypatch.setenv("OMNIROUTER_KEY_2", "sk-two")
    router = _bare_router()
    keys = router._get_env_keys("OMNIROUTER_KEY")
    assert keys == ["sk-one", "sk-two"]
    assert len(OmniRouterProvider(keys).clients) == 2


def test_api_key_alias_enables_the_provider(monkeypatch):
    """OMNIROUTER_API_KEY alone must be enough to register the provider,
    matching the ROUTESME / NVIDIA alias handling."""
    monkeypatch.setenv("OMNIROUTER_API_KEY", "sk-alias")
    monkeypatch.delenv("OMNIROUTER_KEY", raising=False)
    router = _bare_router()
    assert router._get_env_keys("OMNIROUTER_KEY") == ["sk-alias"]

    config = router._load_config_from_env()
    assert config["providers"]["omnirouter"]["enabled"] is True
    assert config["providers"]["omnirouter"]["api_keys"] == ["sk-alias"]


def test_provider_absent_without_a_key(monkeypatch):
    monkeypatch.delenv("OMNIROUTER_KEY", raising=False)
    monkeypatch.delenv("OMNIROUTER_API_KEY", raising=False)
    router = _bare_router()
    assert router._load_config_from_env()["providers"]["omnirouter"]["enabled"] is False


def test_router_registers_omnirouter_and_advertises_models(monkeypatch):
    monkeypatch.setenv("OMNIROUTER_KEY", "sk-test")
    router = LLMRouter()
    assert "omnirouter" in router.get_available_providers()
    ids = [m["id"] for m in router.get_available_models("omnirouter")]
    assert "auto" in ids


# ---------------------------------------------------------------------------
# Tier separation: the cheap hub must never take over trade decisions
# ---------------------------------------------------------------------------

def test_omnirouter_is_not_a_default_trade_agent_fallback():
    """The architectural invariant. NVIDIA / TokenRouter / RoutesMe /
    OpenRouter remain authoritative for trades; a stopped local gateway must
    never become a silent fallback for them."""
    router = _bare_router()
    agents = router._load_config_from_env()["agents"]
    for name in ("trader", "analyst", "risk"):
        assert "omnirouter" not in agents[name]["fallback_providers"], name
        assert agents[name]["provider"] != "omnirouter", name


def test_omnirouter_is_not_in_the_global_fallback_order():
    router = _bare_router()
    order = router._load_config_from_env()["global_fallback_order"]
    assert "omnirouter" not in order


def test_subagent_can_opt_in_to_the_cheap_hub(monkeypatch):
    """The supported way to use OmniRouter: an explicit subagent registration."""
    monkeypatch.setenv("OMNIROUTER_KEY", "sk-test")
    router = LLMRouter()
    router.register_agent("news_summariser", provider="omnirouter", model="auto")
    assert router.agents["news_summariser"]["provider"] == "omnirouter"
    assert router.agents["news_summariser"].get("provider_unavailable") is not True


def test_missing_gateway_never_breaks_a_trade_decision(monkeypatch):
    """A dead local gateway must fail closed, not raise into the trade path."""
    monkeypatch.setenv("OMNIROUTER_KEY", "sk-test")
    router = LLMRouter()

    def _refuse(messages, model, **kwargs):
        return "Error: omnirouter failed: Connection refused"

    router.providers["omnirouter"] = _refuse
    router.agents["sub"] = {"provider": "omnirouter", "model": "auto",
                            "fallback_providers": [], "allow_failover": False}

    payload = json.loads(router.generate("sub", [{"role": "user", "content": "x"}]))
    assert payload["action"] == "HOLD"
    assert payload["deterministic_fallback"] is True


def test_uninitialised_client_reports_missing_key(monkeypatch):
    monkeypatch.delenv("OMNIROUTER_BASE_URL", raising=False)
    provider = OmniRouterProvider(["sk-test"])
    provider.clients = []
    result = provider.generate([{"role": "user", "content": "x"}], "auto")
    assert "OMNIROUTER_KEY" in result


# ---------------------------------------------------------------------------
# Local gateway down: no pointless retries, and the breaker still trips
# ---------------------------------------------------------------------------

def test_connection_refused_is_not_retried(monkeypatch):
    """A refused connection to a local process will never succeed on retry.
    Only 429/5xx are retried, so a stopped gateway fails fast."""
    provider, completions = _provider(monkeypatch)

    def _refuse(**kwargs):
        raise ConnectionError("All connection attempts failed")

    completions.create = _refuse
    provider.generate([{"role": "user", "content": "x"}], "auto")
    assert completions.calls == []
    assert provider.consecutive_failures == 1


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_gateway_errors_still_retry(monkeypatch, status):
    """Quota/rate limiting on the gateway side must keep the shared backoff.
    `max_retries` is the total attempt count, so 2 = one retry."""
    provider, completions = _provider(monkeypatch, max_retries=2)
    calls = {"n": 0}

    def _flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(f"HTTP {status}")
        return _types.SimpleNamespace(
            choices=[_types.SimpleNamespace(
                message=_types.SimpleNamespace(content="ok"))],
            error=None)

    completions.create = _flaky
    assert provider.generate([{"role": "user", "content": "x"}], "auto").content == "ok"
    assert calls["n"] == 2