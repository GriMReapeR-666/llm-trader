"""
Tests for quant module registry population and the QuantAgent bridge.

The original implementation never populated the registry (modules only
registered on instantiation, which never happened) and QuantAgent could not
see module tools at all.  These tests pin the README's "agent-executable
universal modules" story end to end.
"""

import numpy as np
import pytest

from quant_framework.modules import ensure_builtin_modules_registered, registry


EXPECTED_TOOLS = {
    "regime_detection.hmm.fit",
    "regime_detection.hmm.classify",
    "regime_detection.hurst.calculate",
    "strategy_development.wfo.optimize",
    "strategy_development.dsr.calculate",
    "strategy_development.triple_barrier.label",
    "portfolio_management.kelly.calculate",
    "portfolio_management.hrp.optimize",
    "order_execution.spread.calculate",
    "order_execution.almgren_chriss.calculate",
    "quant_utilities.expectancy.calculate",
    "quant_utilities.expectancy.r_multiple",
    "quant_utilities.drawdown.analyze",
}


@pytest.fixture(autouse=True)
def populated_registry():
    # test_modules.py's autouse fixture clears the global registry around its
    # own tests; repopulate before every test here so order never matters.
    ensure_builtin_modules_registered()
    yield


class TestRegistryPopulation:
    def test_registry_contains_all_module_tools(self):
        names = {t.name for t in registry.list_tools()}
        assert EXPECTED_TOOLS <= names

    def test_registry_counts(self):
        assert len(registry.list_tools()) == len(EXPECTED_TOOLS)
        assert len(registry._modules) >= 11

    def test_every_registered_module_discoverable(self):
        for module_id in {t.module_id for t in registry.list_tools()}:
            module = registry.get_module(module_id)
            assert module is not None, module_id
            assert module.discover_tools(), module_id

    def test_ensure_builtin_modules_registered_is_idempotent(self):
        before = {t.name for t in registry.list_tools()}
        assert ensure_builtin_modules_registered() is registry
        assert {t.name for t in registry.list_tools()} == before


class TestAgentBridge:
    def _agent(self):
        from quant_framework.agent import QuantAgent
        return QuantAgent("config/sources.yaml")

    def test_agent_discovers_all_module_tools(self):
        names = {t["name"] for t in self._agent().discover_tools()}
        assert EXPECTED_TOOLS <= names

    def test_agent_executes_hurst_trending(self):
        prices = [100.0 + i * 0.5 for i in range(300)]
        out = self._agent().execute_tool("regime_detection.hurst.calculate", prices=prices)
        assert out["success"] is True
        assert out["result"]["hurst_exponent"] > 0.55
        assert out["result"]["interpretation"] == "trending"

    def test_agent_executes_kelly(self):
        out = self._agent().execute_tool(
            "portfolio_management.kelly.calculate",
            win_rate=0.6, win_loss_ratio=1.5, fraction=0.25,
        )
        assert out["success"] is True
        assert out["result"]["kelly_fraction"] == pytest.approx(1 / 3, abs=1e-9)
        assert out["result"]["applied_fraction"] == pytest.approx(0.25 / 3, abs=1e-9)

    def test_agent_executes_hmm_autofit_flow(self):
        rs = np.random.RandomState(42)
        data = np.concatenate([rs.randn(150) * 0.005, rs.randn(150) * 0.05]).tolist()
        out = self._agent().execute_tool("regime_detection.hmm.classify", returns=data)
        assert out["success"] is True
        assert out["result"]["regime_label"] in ("calm", "normal", "high_vol")
        assert 0.0 <= out["result"]["confidence"] <= 1.0

    def test_agent_executes_expectancy(self):
        out = self._agent().execute_tool(
            "quant_utilities.expectancy.calculate",
            win_rate=0.55, avg_win_r=2.0, avg_loss_r=1.0,
        )
        assert out["success"] is True
        assert out["result"]["ev"] == pytest.approx(0.65)
        assert out["result"]["profitable"] is True

    def test_agent_executes_r_multiple(self):
        out = self._agent().execute_tool(
            "quant_utilities.expectancy.r_multiple",
            entry=100.0, exit=110.0, stop=95.0,
        )
        assert out["success"] is True
        assert out["result"]["r_multiple"] == pytest.approx(2.0)

    def test_agent_executes_drawdown(self):
        out = self._agent().execute_tool(
            "quant_utilities.drawdown.analyze",
            equity_curve=[100, 110, 90, 95, 120, 80, 130],
        )
        assert out["success"] is True
        assert out["result"]["max_drawdown"] > 0

    def test_agent_executes_spread(self):
        out = self._agent().execute_tool(
            "order_execution.spread.calculate",
            base_spread_bps=2.0, volatility_ratio=2.0, volume_ratio=1.0,
        )
        assert out["success"] is True
        assert out["result"]["dynamic_spread_bps"] == pytest.approx(2.0 * (1 + 0.75))

    def test_agent_executes_almgren_chriss(self):
        out = self._agent().execute_tool(
            "order_execution.almgren_chriss.calculate",
            order_size=10_000, adv=1_000_000, price=100.0, volatility=0.02,
        )
        assert out["success"] is True
        assert out["result"]["total_impact_bps"] > 0

    def test_agent_executes_wfo(self):
        rs = np.random.RandomState(1)
        out = self._agent().execute_tool(
            "strategy_development.wfo.optimize", returns=(rs.randn(500) * 0.01).tolist()
        )
        assert out["success"] is True
        assert "wfe" in out["result"]

    def test_agent_executes_dsr(self):
        out = self._agent().execute_tool(
            "strategy_development.dsr.calculate", sharpe_ratio=1.0, n=252, trials=10
        )
        assert out["success"] is True
        assert "dsr" in out["result"]

    def test_agent_executes_triple_barrier(self):
        out = self._agent().execute_tool(
            "strategy_development.triple_barrier.label",
            prices=[100, 101, 102, 103, 104, 105, 106],
            entry_price=100.0, take_profit=0.05, stop_loss=-0.05, max_hold=20,
        )
        assert out["success"] is True
        assert out["result"]["label"] in (-1, 0, 1)

    def test_module_tool_invalid_params_yield_error_dict(self):
        out = self._agent().execute_tool(
            "portfolio_management.kelly.calculate", win_rate=1.5, win_loss_ratio=1.0
        )
        assert out["success"] is True
        assert "error" in out["result"]

    def test_module_tools_work_without_config(self):
        # Module tools must not depend on a config file or knowledge base.
        from quant_framework.agent import QuantAgent
        agent = QuantAgent("does/not/exist.yaml")
        out = agent.execute_tool(
            "portfolio_management.kelly.calculate", win_rate=0.6, win_loss_ratio=1.5
        )
        assert out["success"] is True

    def test_chain_tools_over_module_tool(self):
        chained = self._agent().chain_tools([
            {
                "tool": "portfolio_management.kelly.calculate",
                "params": {"win_rate": 0.6, "win_loss_ratio": 1.5},
                "save_as": "kelly",
            },
        ])
        assert chained["pipeline_results"][0]["result"]["success"] is True
        kelly = chained["final_context"]["kelly"]["result"]["kelly_fraction"]
        assert kelly == pytest.approx(1 / 3, abs=1e-9)
