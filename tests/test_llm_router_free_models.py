"""Tests: LLM router free-model catalogue + best-free selection."""
from data.llm_router import LLMRouter


def test_select_best_free_model_returns_ranked_default():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {}
    assert LLMRouter.select_best_free_model(r, "openrouter") == "deepseek/deepseek-r1:free"
    assert LLMRouter.select_best_free_model(r, "cerebras") == "llama-3.3-70b"
    assert LLMRouter.select_best_free_model(r, "nvidia_nim") == "meta/llama-3.3-70b-instruct"
    assert LLMRouter.select_best_free_model(r, "omnirouter") == "auto"


def test_select_best_free_model_prefers_cached_available():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {"openrouter": [{"id": "meta-llama/llama-3.1-8b-instruct:free"}, {"id": "meta-llama/llama-3.3-70b-instruct:free"}]}
    assert LLMRouter.select_best_free_model(r, "openrouter") == "meta-llama/llama-3.3-70b-instruct:free"


def test_select_best_free_model_unknown_provider():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {}
    assert LLMRouter.select_best_free_model(r, "nope") is None


def test_is_free_model():
    assert LLMRouter._is_free_model("deepseek/deepseek-r1:free") is True
    assert LLMRouter._is_free_model("llama-3.3-70b") is True
    assert LLMRouter._is_free_model("meta/llama-3.3-70b-instruct") is True
    assert LLMRouter._is_free_model("meta/llama-3.2-11b-vision-instruct") is True
    assert LLMRouter._is_free_model("openai/gpt-4-turbo") is False
    assert LLMRouter._is_free_model("") is False


def test_static_catalogue_only_free_models():
    r = LLMRouter.__new__(LLMRouter)
    for p in ("openrouter", "tokenrouter", "cerebras", "nvidia_nim"):
        models = r._get_static_models(p)
        assert len(models) > 0, p
        for m in models:
            assert LLMRouter._is_free_model(m["id"]), (p, m["id"])


def test_failover_uses_provider_appropriate_model(monkeypatch):
    # Production .env pins a per-provider model; this test exercises the
    # fallback selection path, so the env override is intentionally cleared.
    monkeypatch.delenv("LLM_ROUTER_MODEL_CEREBRAS", raising=False)

    class _Provider:
        def __init__(self, result):
            self.result = result
            self.calls = 0
            self.last_model = None

        def generate(self, messages, model, **kwargs):
            self.calls += 1
            self.last_model = model
            return self.result

    router = LLMRouter.__new__(LLMRouter)
    router.providers = {
        "primary": _Provider("Error: primary unavailable"),
        "cerebras": _Provider("fallback result"),
    }
    router.agents = {"trader": {"provider": "primary", "model": "meta/llama-3.2-11b-vision-instruct",
                                "fallback_providers": ["cerebras"],
                                "allow_failover": True}}
    router._catalogue_cache = {}
    router.FREE_MODEL_RANKING = LLMRouter.FREE_MODEL_RANKING
    result = router.generate("trader", [{"role": "user", "content": "x"}])
    assert result == "fallback result"
    assert router.providers["cerebras"].calls == 1
    assert router.providers["cerebras"].last_model == "llama-3.3-70b"


def test_failover_respects_per_provider_env_override(monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_MODEL_CEREBRAS", "llama3.1-70b")

    class _Provider:
        def __init__(self, result):
            self.result = result
            self.last_model = None

        def generate(self, messages, model, **kwargs):
            self.last_model = model
            return self.result

    router = LLMRouter.__new__(LLMRouter)
    router.providers = {
        "primary": _Provider("Error: primary unavailable"),
        "cerebras": _Provider("fallback result"),
    }
    router.agents = {"trader": {"provider": "primary", "model": "meta/llama-3.2-11b-vision-instruct",
                                "fallback_providers": ["cerebras"],
                                "allow_failover": True}}
    router._catalogue_cache = {}
    router.FREE_MODEL_RANKING = LLMRouter.FREE_MODEL_RANKING
    assert router.generate("trader", [{"role": "user", "content": "x"}]) == "fallback result"
    assert router.providers["cerebras"].last_model == "llama3.1-70b"


def test_cached_catalogue_picks_largest_general_model_not_first():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {"cerebras": [
        {"id": "nvidia/nemotron-3-embed-1b"},
        {"id": "acme/tiny-7b:free"},
        {"id": "acme/huge-chat-70b:free"},
    ]}
    # huge-chat-70b has 70B parameters and is not a non-chat marker -> wins
    best = r.select_best_free_model("cerebras")
    assert best == "acme/huge-chat-70b:free"


def test_non_chat_markers_filter_embeddings():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {"openrouter": [
        {"id": "baai/bge-large-en-v1.5:free"},
        {"id": "meta/llama-guard-3-8b:free"},
        {"id": "mistralai/mistral-small-24b:free"},
    ]}
    best = r.select_best_free_model("openrouter")
    assert best == "mistralai/mistral-small-24b:free"
