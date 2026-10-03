"""
Extra regression tests added with the README alignment: the Kelly drawdown
risk formula (Thorp 2006) and the agent's module-tool discovery contract.
"""

import numpy as np
import pytest

from quant_framework.modules.portfolio_management import KellyCriterion


class TestKellyDrawdownRisk:
    def test_quarter_kelly_half_dd_risk_is_half_pow_seven(self):
        # f = 0.25 -> P(ever below 50%) = 0.5**(2/0.25 - 1) = 0.5**7 = 0.0078125.
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=0.25)
        assert out["prob_ever_half"] == pytest.approx(0.5 ** 7, rel=1e-12)

    def test_full_kelly_gives_one_half(self):
        # f = 1.0 -> 0.5**(2/1 - 1) = 0.5 -> 50% chance of ever being down 50%.
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=1.0)
        assert out["prob_ever_half"] == pytest.approx(0.5, rel=1e-12)

    def test_half_kelly_gives_one_eighth(self):
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=0.5)
        assert out["prob_ever_half"] == pytest.approx(0.5 ** 3, rel=1e-12)

    def test_risk_monotone_in_fraction(self):
        k = KellyCriterion()
        probs = [k.calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=f)["prob_ever_half"]
                 for f in (0.1, 0.25, 0.5, 1.0)]
        assert probs == sorted(probs)

    def test_no_edge_returns_certain_dd_with_warning(self):
        # win_rate * b < 1 - win_rate  =>  kelly <= 0.
        out = KellyCriterion().calculate(win_rate=0.3, win_loss_ratio=1.5)
        assert out["kelly_fraction"] <= 0
        assert out["prob_ever_half"] == 1.0
        assert "warning" in out

    def test_zero_fraction_nothing_risked(self):
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=0.0)
        assert out["prob_ever_half"] == 0.0

    def test_negative_fraction_rejected(self):
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5, fraction=-1.0)
        assert "error" in out

    def test_old_invented_heuristic_gone(self):
        # Regression guard for the removed 0.80*sqrt(fraction) field.
        out = KellyCriterion().calculate(win_rate=0.6, win_loss_ratio=1.5)
        assert "max_drawdown_estimate" not in out


class TestAgentSurface:
    def test_agent_discovers_19_tools(self):
        from quant_framework.agent import QuantAgent
        names = {t["name"] for t in QuantAgent("config/sources.yaml").discover_tools()}
        assert len(names) == 19
        assert names == {
            "knowledge.search", "knowledge.classify", "knowledge.list_topics",
            "knowledge.get_content", "pipeline.run", "knowledge.smoke_test",
            "regime_detection.hmm.fit", "regime_detection.hmm.classify",
            "regime_detection.hurst.calculate",
            "strategy_development.wfo.optimize", "strategy_development.dsr.calculate",
            "strategy_development.triple_barrier.label",
            "portfolio_management.kelly.calculate", "portfolio_management.hrp.optimize",
            "order_execution.spread.calculate", "order_execution.almgren_chriss.calculate",
            "quant_utilities.expectancy.calculate", "quant_utilities.expectancy.r_multiple",
            "quant_utilities.drawdown.analyze",
        }

    def test_removed_kelly_field_not_promised_in_tool_schema(self):
        from quant_framework.agent import QuantAgent
        agent = QuantAgent("config/sources.yaml")
        tool = next(t for t in agent.discover_tools() if t["name"] == "portfolio_management.kelly.calculate")
        assert "max_drawdown_estimate" not in tool["returns"]
        assert "prob_ever_half" in tool["returns"]
