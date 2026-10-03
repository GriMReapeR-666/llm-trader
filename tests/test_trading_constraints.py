"""Tests for TensorLogic-inspired TradingConstraintEngine.

Covers all 14 constraint rules:
1. SL valid
2. TP direction
3. Position size bounds
4. Risk per trade bounds
5. R:R ratio minimum
6. Spread tolerance
7. Price range (fat-finger)
8. Cooldown
9. Max active trades
10. Daily loss limit (kill switch)
11. Consecutive loss breaker
12. Crash detector gate
13. Cross-market robustness
14. CPCV escalation
"""
from __future__ import annotations

import time

import pytest

from core.trading_constraints import (
    ConstraintEvaluation,
    ConstraintResult,
    ConstraintVerdict,
    TradingConstraintEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_proposal(entry=1.1000, sl=1.0950, tp=1.1100, lots=0.1, symbol="EURUSD"):
    return {"action": "BUY", "entry": entry, "stop_loss": sl, "take_profit": tp, "lots": lots, "symbol": symbol}


def _sell_proposal(entry=1.1000, sl=1.1050, tp=1.0900, lots=0.1, symbol="EURUSD"):
    return {"action": "SELL", "entry": entry, "stop_loss": sl, "take_profit": tp, "lots": lots, "symbol": symbol}


def _empty_context():
    return {}


# ---------------------------------------------------------------------------
# Rule 1: SL Valid
# ---------------------------------------------------------------------------

class TestRuleSLValid:
    def test_buy_sl_below_entry(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(sl=1.0950), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.PASS

    def test_buy_sl_above_entry_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(sl=1.1050), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT
        sl_rule = [r for r in result.results if r.rule_name == "sl_valid" and r.verdict == ConstraintVerdict.REJECT]
        assert len(sl_rule) > 0

    def test_sell_sl_above_entry(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_sell_proposal(sl=1.1050), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.PASS

    def test_sell_sl_below_entry_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_sell_proposal(sl=1.0950), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT

    def test_no_sl_rejects(self):
        engine = TradingConstraintEngine()
        proposal = {"action": "BUY", "entry": 1.1000, "take_profit": 1.1100, "lots": 0.1}
        result = engine.evaluate(proposal, _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT

    def test_zero_sl_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(sl=0), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 2: TP Direction
# ---------------------------------------------------------------------------

class TestRuleTPDirection:
    def test_buy_tp_above_entry(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(tp=1.1100), _empty_context())
        # Should pass (TP direction correct)
        tp_rule = [r for r in result.results if r.rule_name == "tp_direction"][0]
        assert tp_rule.verdict == ConstraintVerdict.PASS

    def test_buy_tp_below_entry_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(tp=1.0900), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT

    def test_sell_tp_below_entry(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_sell_proposal(tp=1.0900), _empty_context())
        tp_rule = [r for r in result.results if r.rule_name == "tp_direction"][0]
        assert tp_rule.verdict == ConstraintVerdict.PASS


# ---------------------------------------------------------------------------
# Rule 3: Position Size
# ---------------------------------------------------------------------------

class TestRulePositionSize:
    def test_normal_size(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(lots=0.1), _empty_context())
        pos_rule = [r for r in result.results if r.rule_name == "position_size"][0]
        assert pos_rule.verdict == ConstraintVerdict.PASS

    def test_exceeds_max_forex(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(lots=1.5), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REDUCE

    def test_gold_lower_limit(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(lots=0.5, symbol="XAUUSD"), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REDUCE

    def test_zero_lots_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(lots=0), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 4: Risk Per Trade
# ---------------------------------------------------------------------------

class TestRuleRiskPerTrade:
    def test_normal_risk(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"risk_per_trade": 0.01})
        risk_rule = [r for r in result.results if r.rule_name == "risk_per_trade"][0]
        assert risk_rule.verdict == ConstraintVerdict.PASS

    def test_exceeds_max(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"risk_per_trade": 0.05})
        assert result.overall_verdict == ConstraintVerdict.REJECT

    def test_below_min(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"risk_per_trade": 0.0001})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 5: R:R Ratio
# ---------------------------------------------------------------------------

class TestRuleRRRatio:
    def test_good_rr(self):
        engine = TradingConstraintEngine()
        # Risk = 50 pips, Reward = 100 pips, R:R = 2.0
        result = engine.evaluate(_buy_proposal(entry=1.1000, sl=1.0950, tp=1.1100), _empty_context())
        rr_rule = [r for r in result.results if r.rule_name == "risk_reward_ratio"][0]
        assert rr_rule.verdict == ConstraintVerdict.PASS

    def test_bad_rr(self):
        engine = TradingConstraintEngine()
        # Risk = 100 pips, Reward = 50 pips, R:R = 0.5
        result = engine.evaluate(_buy_proposal(entry=1.1000, sl=1.0900, tp=1.1050), _empty_context())
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 6: Spread
# ---------------------------------------------------------------------------

class TestRuleSpread:
    def test_normal_spread(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"spread": 1.5})
        spread_rule = [r for r in result.results if r.rule_name == "spread"][0]
        assert spread_rule.verdict == ConstraintVerdict.PASS

    def test_wide_spread_warns(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"spread": 10.0})
        spread_rule = [r for r in result.results if r.rule_name == "spread"][0]
        assert spread_rule.verdict == ConstraintVerdict.WARN


# ---------------------------------------------------------------------------
# Rule 7: Price Range
# ---------------------------------------------------------------------------

class TestRulePriceRange:
    def test_normal_price(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(entry=1.1000), {"recent_close": 1.1005})
        price_rule = [r for r in result.results if r.rule_name == "price_range"][0]
        assert price_rule.verdict == ConstraintVerdict.PASS

    def test_fat_finger_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(entry=1.2000), {"recent_close": 1.1000})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 8: Cooldown
# ---------------------------------------------------------------------------

class TestRuleCooldown:
    def test_cooldown_elapsed(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"last_trade_time": time.time() - 600})
        cooldown_rule = [r for r in result.results if r.rule_name == "cooldown"][0]
        assert cooldown_rule.verdict == ConstraintVerdict.PASS

    def test_cooldown_active(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"last_trade_time": time.time() - 60})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 9: Max Active Trades
# ---------------------------------------------------------------------------

class TestRuleMaxActiveTrades:
    def test_under_limit(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"active_trade_count": 2})
        active_rule = [r for r in result.results if r.rule_name == "max_active_trades"][0]
        assert active_rule.verdict == ConstraintVerdict.PASS

    def test_at_limit(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"active_trade_count": 5})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 10: Daily Loss Limit
# ---------------------------------------------------------------------------

class TestRuleDailyLossLimit:
    def test_within_limit(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"daily_pnl_pct": -0.03})
        daily_rule = [r for r in result.results if r.rule_name == "daily_loss_limit"][0]
        assert daily_rule.verdict == ConstraintVerdict.PASS

    def test_kill_switch(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"daily_pnl_pct": -0.06})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 11: Consecutive Losses
# ---------------------------------------------------------------------------

class TestRuleConsecutiveLosses:
    def test_normal(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"consecutive_losses": 2})
        cons_rule = [r for r in result.results if r.rule_name == "consecutive_losses"][0]
        assert cons_rule.verdict == ConstraintVerdict.PASS

    def test_circuit_breaker(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"consecutive_losses": 5})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 12: Crash Detector
# ---------------------------------------------------------------------------

class TestRuleCrashDetector:
    def test_no_crash(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"crash_detector_hold_bias": False, "crash_detector_trend": "OSCILLATION"})
        crash_rule = [r for r in result.results if r.rule_name == "crash_detector"][0]
        assert crash_rule.verdict == ConstraintVerdict.PASS

    def test_rapid_descend_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"crash_detector_hold_bias": True, "crash_detector_trend": "RAPID_DESCEND"})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 13: Cross-Market
# ---------------------------------------------------------------------------

class TestRuleCrossMarket:
    def test_robust(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"cross_market_hold_bias": False})
        xm_rule = [r for r in result.results if r.rule_name == "cross_market"][0]
        assert xm_rule.verdict == ConstraintVerdict.PASS

    def test_fragile_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"cross_market_hold_bias": True})
        assert result.overall_verdict == ConstraintVerdict.REJECT


# ---------------------------------------------------------------------------
# Rule 14: CPCV Escalation
# ---------------------------------------------------------------------------

class TestRuleCPCVEscalation:
    def test_no_escalation(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"cpcv_position_scale": 1.0, "cpcv_degradation_streak": 0})
        cpcv_rule = [r for r in result.results if r.rule_name == "cpcv_escalation"][0]
        assert cpcv_rule.verdict == ConstraintVerdict.PASS

    def test_scale_zero_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"cpcv_position_scale": 0.0, "cpcv_degradation_streak": 5})
        assert result.overall_verdict == ConstraintVerdict.REJECT

    def test_scale_reduces(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"cpcv_position_scale": 0.5, "cpcv_degradation_streak": 3})
        assert result.overall_verdict == ConstraintVerdict.REDUCE


# ---------------------------------------------------------------------------
# Integration: Multiple Rules
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_perfect_proposal_passes_all(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(
            _buy_proposal(entry=1.1000, sl=1.0950, tp=1.1100, lots=0.1),
            {"risk_per_trade": 0.01, "spread": 1.0, "recent_close": 1.1000,
             "active_trade_count": 2, "consecutive_losses": 1,
             "crash_detector_hold_bias": False, "crash_detector_trend": "OSCILLATION"},
        )
        assert result.overall_verdict == ConstraintVerdict.PASS
        assert len(result.reject_reasons) == 0

    def test_multiple_rejects(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(
            _buy_proposal(sl=1.1050, lots=2.0),  # SL wrong + too large
            {"risk_per_trade": 0.05, "consecutive_losses": 5},  # Risk too high + circuit breaker
        )
        assert result.overall_verdict == ConstraintVerdict.REJECT
        assert len(result.reject_reasons) >= 3

    def test_evaluation_to_dict(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), _empty_context())
        d = result.to_dict()
        assert "overall_verdict" in d
        assert "rules_checked" in d
        assert d["rules_checked"] == 15

    def test_evaluation_summary(self):
        engine = TradingConstraintEngine()
        result = engine.evaluate(_buy_proposal(), {"risk_per_trade": 0.01, "spread": 1.0, "recent_close": 1.1000})
        d = result.to_dict()
        assert d["rules_passed"] >= 10
        assert d["rules_rejected"] == 0
