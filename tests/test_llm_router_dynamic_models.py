"""Tests for dynamic model fetching and automatic model resolution in LLMRouter."""
import pytest
from data.llm_router import LLMRouter


def test_updated_static_models_and_rankings_contain_valid_models():
    router = LLMRouter.__new__(LLMRouter)
    router._catalogue_cache = {}

    # OpenRouter
    or_models = [m["id"] for m in router._get_static_models("openrouter")]
    assert "deepseek/deepseek-r1:free" in or_models
    assert "meta-llama/llama-3.3-70b-instruct:free" in or_models
    assert "gpt-oss-120b" not in or_models
    assert "nvidia/nemotron-3-super-120b-a12b:free" not in or_models

    # Cerebras
    cer_models = [m["id"] for m in router._get_static_models("cerebras")]
    assert "llama-3.3-70b" in cer_models
    assert "gpt-oss-120b" not in cer_models
    assert "qwen-3.8-27b" not in cer_models

    # NVIDIA NIM
    nv_models = [m["id"] for m in router._get_static_models("nvidia_nim")]
    assert "meta/llama-3.3-70b-instruct" in nv_models
    assert "openai/gpt-oss-20b" not in nv_models
    assert "meta/llama-3.2-11b-vision-instruct" not in nv_models

    # Free model rankings
    assert router.FREE_MODEL_RANKING["cerebras"][0] == "llama-3.3-70b"
    assert "deepseek/deepseek-r1:free" in router.FREE_MODEL_RANKING["openrouter"]


def test_dynamic_model_resolution_replaces_foreign_or_stale_models():
    router = LLMRouter.__new__(LLMRouter)
    router._catalogue_cache = {}

    # Cerebras does not support ':free' suffixes or vision-instruct
    res_cer = router.resolve_model_for_provider("cerebras", "meta/llama-3.2-11b-vision-instruct")
    assert res_cer in {"llama-3.3-70b", "llama3.1-70b", "llama3.1-8b", "deepseek-r1-distill-llama-70b"}
    assert res_cer == "llama-3.3-70b"

    # NVIDIA NIM requires meta/ or nvidia/ namespace and does not support :free suffix
    res_nv = router.resolve_model_for_provider("nvidia_nim", "meta-llama/llama-3.3-70b-instruct:free")
    assert res_nv == "meta/llama-3.3-70b-instruct"

    # OpenRouter stale/nonexistent models are replaced by top available free model
    res_or = router.resolve_model_for_provider("openrouter", "gpt-oss-120b")
    assert res_or == router.FREE_MODEL_RANKING["openrouter"][0]


def test_generate_substitutes_invalid_model_for_primary_and_failover():
    class _MockProvider:
        def __init__(self, name, result):
            self.provider_name = name
            self.result = result
            self.calls = 0
            self.last_model = None

        def generate(self, messages, model, **kwargs):
            self.calls += 1
            self.last_model = model
            return self.result

    router = LLMRouter.__new__(LLMRouter)
    router.providers = {
        "cerebras": _MockProvider("cerebras", "response from cerebras"),
    }
    # Configure agent with invalid / stale model
    router.agents = {
        "trader": {
            "provider": "cerebras",
            "model": "meta/llama-3.2-11b-vision-instruct",
            "fallback_providers": [],
            "allow_failover": False,
        }
    }
    router._catalogue_cache = {}
    router.FREE_MODEL_RANKING = LLMRouter.FREE_MODEL_RANKING

    resp = router.generate("trader", [{"role": "user", "content": "ping"}])
    assert resp == "response from cerebras"
    # Even though agent was configured with meta/llama-3.2-11b-vision-instruct,
    # the provider received Cerebras's valid model llama-3.3-70b
    assert router.providers["cerebras"].last_model == "llama-3.3-70b"
