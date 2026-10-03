import pytest
import time
from unittest.mock import patch, MagicMock

from llm_router import BaseLLMProvider, LLMRouter
from risk_manager import RiskManager


class MockFailingProvider(BaseLLMProvider):
    provider_name = "failing_mock"

    def generate(self, messages: list, model: str, **kwargs) -> str:
        def request_fn():
            raise RuntimeError("503 Server Error: Service Unavailable")
        return self._retry_with_backoff(request_fn)


class MockWorkingProvider(BaseLLMProvider):
    provider_name = "working_mock"

    def generate(self, messages: list, model: str, **kwargs) -> str:
        def request_fn():
            return "SUCCESSFUL_LLM_RESPONSE"
        return self._retry_with_backoff(request_fn)


def test_llm_provider_circuit_breaker_trips_and_recovers():
    provider = MockFailingProvider(api_keys=["test_key"], max_retries=1, max_consecutive_failures=3, circuit_cooldown_seconds=10.0)
    assert provider.is_circuit_open() is False

    # Fail 3 consecutive requests
    for _ in range(3):
        res = provider.generate([], "model")
        assert "Error:" in res

    # Circuit should now be open
    assert provider.is_circuit_open() is True
    res_tripped = provider.generate([], "model")
    assert "circuit open" in res_tripped


def test_llm_router_circuit_breaker_fallback():
    router = LLMRouter()
    failing = MockFailingProvider(api_keys=["test_key"], max_retries=1, max_consecutive_failures=1)
    working = MockWorkingProvider(api_keys=["test_key"])

    router.providers = {
        "failing": failing,
        "working": working,
    }
    router.agents = {
        "analyst": {
            "provider": "failing",
            "model": "model_a",
            "fallback_providers": ["working"],
            "allow_failover": True,
            "provider_models": {"failing": "model_a", "working": "model_b"},
        }
    }

    result = router.generate("analyst", [{"role": "user", "content": "hi"}])
    assert result == "SUCCESSFUL_LLM_RESPONSE"
    assert failing.is_circuit_open() is True


def test_risk_manager_daily_and_peak_drawdown_killswitch(tmp_path, monkeypatch):
    # RiskManager persists risk_state.json relative to cwd; isolate so the
    # test cannot read/write the repo-root live risk state.
    monkeypatch.chdir(tmp_path)
    rm = RiskManager(balance=10000.0, max_daily_loss=0.03, max_drawdown=0.10)

    # 1% drawdown -> Normal
    assert rm.check_daily_drawdown(current_equity=9900.0) is True
    assert rm.check_max_drawdown(current_equity=9900.0) is True
    assert rm.pending_flatten_reason() is None

    # 3.5% daily drawdown -> Triggers daily kill-switch AND flatten request
    assert rm.check_daily_drawdown(current_equity=9650.0) is False
    assert rm.pending_flatten_reason() is not None
    assert "daily_loss_limit" in rm.pending_flatten_reason()

    # 11% peak drawdown -> Triggers peak kill-switch
    assert rm.check_max_drawdown(current_equity=8900.0) is False


def test_consecutive_loss_circuit_breaker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rm = RiskManager(balance=10000.0, max_consecutive_losses=5)
    rm.consecutive_losses = 0
    assert rm.check_consecutive_loss_breaker() is True
    for _ in range(4):
        rm.record_trade_outcome(-10.0)
        assert rm.check_consecutive_loss_breaker() is True
    rm.record_trade_outcome(-10.0)
    assert rm.consecutive_losses == 5
    assert rm.check_consecutive_loss_breaker() is False
    # A win resets the streak
    rm.record_trade_outcome(5.0)
    assert rm.consecutive_losses == 0
    assert rm.check_consecutive_loss_breaker() is True


def test_consecutive_loss_breaker_blocks_position_sizing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("risk_manager.mt5.account_info", lambda: MagicMock(equity=10000.0))
    rm = RiskManager(balance=10000.0, max_consecutive_losses=3)
    rm.consecutive_losses = 0
    for _ in range(3):
        rm.record_trade_outcome(-1.0)
    assert rm.calc_position_size("EURUSD", 1.10, 1.09, confidence=1.0) == 0.0
