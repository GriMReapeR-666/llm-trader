"""Tests: LLM router free-model catalogue + best-free selection."""
from data.llm_router import LLMRouter


def test_select_best_free_model_returns_ranked_default():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {}
    assert LLMRouter.select_best_free_model(r, "openrouter") == "nvidia/nemotron-3-super-120b-a12b:free"
    assert LLMRouter.select_best_free_model(r, "cerebras") == "qwen-3-27b"
    assert LLMRouter.select_best_free_model(r, "nvidia_nim") == "nvidia/nemotron-3-super-120b-a12b"
    assert LLMRouter.select_best_free_model(r, "omnirouter") == "auto"


def test_select_best_free_model_prefers_cached_available():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {"openrouter": [{"id": "qwen/qwen3-8b:free"}, {"id": "meta-llama/llama-3.3-70b-instruct:free"}]}
    assert LLMRouter.select_best_free_model(r, "openrouter") == "meta-llama/llama-3.3-70b-instruct:free"


def test_select_best_free_model_unknown_provider():
    r = LLMRouter.__new__(LLMRouter)
    r._catalogue_cache = {}
    assert LLMRouter.select_best_free_model(r, "nope") is None


def test_is_free_model():
    assert LLMRouter._is_free_model("nvidia/nemotron-3-super-120b-a12b:free") is True
    assert LLMRouter._is_free_model("qwen-3-27b") is True
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


def test_failover_uses_provider_appropriate_model():
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
    assert router.providers["cerebras"].last_model == "qwen-3-27b"