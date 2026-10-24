"""Unit tests for automatic model discovery, dead model detection, and dynamic recovery."""
import pytest
from unittest.mock import MagicMock
from data.llm_router import LLMRouter, BaseLLMProvider


def test_dead_model_does_not_trip_circuit_breaker():
    """A 404/410 model error must not trip the circuit breaker for the entire provider."""
    class _MockProvider(BaseLLMProvider):
        provider_name = "test_provider"
        def __init__(self):
            super().__init__(api_keys=["dummy_key"])
            self.model_attempts = []
            
        def generate(self, messages, model, **kwargs):
            self.model_attempts.append(model)
            if model == "dead_model_404":
                raise ValueError("Error code: 404 - {'message': 'no provider serves this model anymore'}")
            return "valid response from new model"
            
    router = LLMRouter.__new__(LLMRouter)
    mock_prov = _MockProvider()
    router.providers = {"test_provider": mock_prov}
    router.agents = {
        "trader": {
            "provider": "test_provider",
            "model": "dead_model_404",
            "fallback_providers": [],
            "allow_failover": False,
        }
    }
    router.FREE_MODEL_RANKING = {"test_provider": ["dead_model_404", "active_fallback_model"]}
    router._catalogue_cache = {}
    
    # Generate should catch the 404 dead model and automatically fallback to active_fallback_model
    resp = router.generate("trader", [{"role": "user", "content": "hello"}])
    assert "valid response from new model" in resp
    assert mock_prov.model_attempts == ["dead_model_404", "active_fallback_model"]
    assert not mock_prov.is_circuit_open()


def test_caller_explicit_model_precedence_over_env():
    """When caller explicitly specifies model in generate(), it must not be clobbered by .env."""
    class _MockProvider:
        provider_name = "test_provider"
        def __init__(self):
            self.last_model = None
        def generate(self, messages, model, **kwargs):
            self.last_model = model
            return "response"

    router = LLMRouter.__new__(LLMRouter)
    mock_prov = _MockProvider()
    router.providers = {"test_provider": mock_prov}
    router.agents = {
        "trader": {
            "provider": "test_provider",
            "model": "agent_default_model",
            "provider_models": {"test_provider": "env_override_model"},
            "fallback_providers": [],
            "allow_failover": False,
        }
    }
    router.FREE_MODEL_RANKING = {"test_provider": ["active_model"]}
    router._catalogue_cache = {}
    
    # Caller explicitly asks for 'explicit_requested_model'
    router.generate("trader", [{"role": "user", "content": "hi"}], model="explicit_requested_model")
    assert mock_prov.last_model == "explicit_requested_model"
