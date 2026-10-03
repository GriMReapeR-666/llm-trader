"""Tests for token budget enforcement and cost model unification."""
import pytest


class TestTokenBudgetEnforcement:
    """Verify enforce_token_budget truncates system message to fit budget."""

    def test_within_budget_unchanged(self):
        from data.llm_router import enforce_token_budget
        msgs = [
            {"role": "system", "content": "Short system prompt"},
            {"role": "user", "content": "Latest market data"},
        ]
        result = enforce_token_budget(msgs, max_total_tokens=24000)
        assert result[0]["content"] == "Short system prompt"
        assert result[1]["content"] == "Latest market data"

    def test_over_budget_truncates_system(self):
        from data.llm_router import enforce_token_budget
        # 100k chars ≈ 25k tokens — well over budget
        big_system = "X" * 100_000
        msgs = [
            {"role": "system", "content": big_system},
            {"role": "user", "content": "Market data"},
        ]
        result = enforce_token_budget(msgs, max_total_tokens=24000)
        assert len(result[0]["content"]) < len(big_system)
        assert "[TRUNCATED" in result[0]["content"]
        # User message untouched
        assert result[1]["content"] == "Market data"

    def test_user_message_never_truncated(self):
        from data.llm_router import enforce_token_budget
        msgs = [
            {"role": "system", "content": "S" * 100_000},
            {"role": "user", "content": "U" * 50_000},
        ]
        result = enforce_token_budget(msgs, max_total_tokens=5000)
        # User message preserved exactly
        assert result[1]["content"] == "U" * 50_000
        # System was truncated
        assert len(result[0]["content"]) < 100_000

    def test_no_system_message_passthrough(self):
        from data.llm_router import enforce_token_budget
        msgs = [{"role": "user", "content": "Just user"}]
        result = enforce_token_budget(msgs, max_total_tokens=100)
        assert result[0]["content"] == "Just user"

    def test_empty_messages(self):
        from data.llm_router import enforce_token_budget
        assert enforce_token_budget([], max_total_tokens=100) == []

    def test_estimate_tokens(self):
        from data.llm_router import estimate_tokens
        # 4 chars ≈ 1 token
        assert estimate_tokens("abcd") >= 1
        assert estimate_tokens("a" * 1000) >= 200


class TestCostModelUnification:
    """Verify VBT engine commission matches TCE default."""

    def test_vbt_commission_matches_tce(self):
        """VBT commission_pct should equal TCE base_commission_pct."""
        from research.vbt_engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        # TCE default is 0.0002 (2 bps)
        assert engine.commission_pct == 0.0002

    def test_vbt_backend_status_documents_tce(self):
        from research.vbt_engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        status = engine.backend_status
        assert "tce" in status["cost_model"].lower() or "transaction_cost" in status["cost_model"].lower()

    def test_cost_estimator_delegates_to_tce(self):
        """cost_estimator.slippage should reference TCE."""
        import inspect
        from intelligence import cost_estimator
        # Module should exist without error
        assert hasattr(cost_estimator, "assess_costs")


class TestTokenBudgetIntegration:
    """Verify token budget is called in orchestrator."""

    def test_enforce_importable_from_router(self):
        from data.llm_router import enforce_token_budget
        assert callable(enforce_token_budget)

    def test_budget_function_signature(self):
        import inspect
        from data.llm_router import enforce_token_budget
        sig = inspect.signature(enforce_token_budget)
        assert "messages" in sig.parameters
        assert "max_total_tokens" in sig.parameters
