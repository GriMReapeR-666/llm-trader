from llm_router import LLMRouter
import json


class _Provider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate(self, messages, model, **kwargs):
        self.calls += 1
        return self.result


def _router(allow_failover):
    router = LLMRouter.__new__(LLMRouter)
    router.providers = {"primary": _Provider("Error: primary unavailable"),
                        "fallback": _Provider("fallback result")}
    router.agents = {"trader": {"provider": "primary", "model": "model-a",
                                 "fallback_providers": ["fallback"],
                                 "allow_failover": allow_failover}}
    return router


def test_provider_failover_is_disabled_by_default_for_trade_decisions():
    router = _router(False)
    result = router.generate("trader", [{"role": "user", "content": "x"}])
    payload = json.loads(result)
    assert payload["action"] == "HOLD"
    assert payload["deterministic_fallback"] is True
    assert router.providers["fallback"].calls == 0


def test_provider_failover_requires_explicit_configuration():
    router = _router(True)
    assert router.generate("trader", [{"role": "user", "content": "x"}]) == "fallback result"
    assert router.providers["fallback"].calls == 1


def test_missing_selected_provider_is_not_silently_replaced():
    router = LLMRouter.__new__(LLMRouter)
    router.providers = {"fallback": _Provider("fallback result")}
    router.config = {"agents": {"trader": {"provider": "missing", "fallback_providers": ["fallback"]}}}
    router._init_agents()
    assert router.agents["trader"]["provider"] == "missing"
    assert router.agents["trader"]["provider_unavailable"] is True


def test_total_provider_failure_returns_deterministic_hold_json():
    router = LLMRouter.__new__(LLMRouter)
    router.providers = {"primary": _Provider("Error: boom")}
    router.agents = {"trader": {"provider": "primary", "model": "m",
                                 "fallback_providers": [], "allow_failover": False}}
    payload = json.loads(router.generate("trader", [{"role": "user", "content": "x"}]))
    assert payload["action"] == "HOLD"
    assert payload["confidence"] == 0.0
    assert payload["deterministic_fallback"] is True
    assert "boom" in payload["provider_errors"][0]


# ---------------------------------------------------------------------------
# OpenRouter-style HTTP-200-with-null-choices upstream failures
# ---------------------------------------------------------------------------

import types as _types


class _NullChoicesCompletions:
    """Mimics OpenRouter returning HTTP 200 with choices=null on upstream
    model failure (optionally carrying an ``error`` payload)."""

    def __init__(self, error=None):
        self._error = error

    def create(self, **kwargs):
        return _types.SimpleNamespace(choices=None, error=self._error)


def _broken_provider(error=None):
    from llm_router import OpenRouterProvider

    provider = OpenRouterProvider(["k"], max_retries=1, base_delay=0.0)
    provider.clients = [_types.SimpleNamespace(
        chat=_types.SimpleNamespace(completions=_NullChoicesCompletions(error)))]
    return provider


def test_null_choices_completion_surfaces_upstream_error():
    """A 200-with-null-choices completion must return a descriptive provider
    error (never crash with 'NoneType' object is not subscriptable)."""
    provider = _broken_provider(error={"message": "upstream model down"})
    result = provider.generate([{"role": "user", "content": "x"}], "model-x")
    assert result.startswith("Error: openrouter failed:")
    assert "no choices" in result
    assert "upstream model down" in result


def test_null_choices_without_error_payload_gets_generic_diagnosis():
    provider = _broken_provider(error=None)
    result = provider.generate([{"role": "user", "content": "x"}], "model-x")
    assert result.startswith("Error: openrouter failed:")
    assert "no choices" in result
    assert "upstream model failure" in result


def test_null_choices_provider_failure_feeds_router_failover():
    """The provider error string must flow into the router's normal failover
    path instead of killing the request."""
    broken = _broken_provider(error={"message": "upstream exploded"})
    router = LLMRouter.__new__(LLMRouter)
    router.providers = {"openrouter": broken,
                        "fallback": _Provider("fallback result")}
    router.agents = {"trader": {"provider": "openrouter", "model": "m",
                                "fallback_providers": ["fallback"],
                                "allow_failover": True}}
    result = router.generate("trader", [{"role": "user", "content": "x"}])
    assert result == "fallback result"
    assert router.providers["fallback"].calls == 1


def test_all_providers_down_on_null_choices_returns_deterministic_hold():
    broken = _broken_provider(error={"message": "upstream exploded"})
    router = LLMRouter.__new__(LLMRouter)
    router.providers = {"openrouter": broken}
    router.agents = {"trader": {"provider": "openrouter", "model": "m",
                                "fallback_providers": [],
                                "allow_failover": True}}
    payload = json.loads(router.generate(
        "trader", [{"role": "user", "content": "x"}]))
    assert payload["action"] == "HOLD"
    assert payload["deterministic_fallback"] is True
    assert any("no choices" in err for err in payload["provider_errors"])
