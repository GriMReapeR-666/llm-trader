"""Tests for the 9 extracted module facades (Modules 01-09) mounted by ExtractedModulesPlugin.

Covers: mounting, facade provision, calculation correctness, graceful degradation,
and the master ExtractedModulesPlugin orchestrator.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_ctx():
    """Create a fresh TradingContext with required base plugins."""
    from core.harness.kernel import TradingContext
    from core.harness.plugins import RiskManagementPlugin

    ctx = TradingContext(name="TestExtractedModules")
    ctx.plugin(RiskManagementPlugin, balance=10000.0, max_risk_pct=0.01)
    return ctx


def _make_full_ctx():
    """Create a TradingContext with RiskManagementPlugin + ExtractedModulesPlugin mounted."""
    from core.harness.extracted_module_plugins import ExtractedModulesPlugin

    ctx = _make_ctx()
    ctx.plugin(ExtractedModulesPlugin)
    return ctx


def _make_df(n=100):
    """Create a minimal OHLCV DataFrame."""
    np.random.seed(42)
    closes = 1.1000 + np.cumsum(np.random.randn(n) * 0.001)
    return pd.DataFrame({
        "open": closes - 0.0005,
        "high": closes + 0.001,
        "low": closes - 0.001,
        "close": closes,
        "tick_volume": np.random.randint(100, 1000, n),
    })


# ===========================================================================
# Module 01 — Core Trading Metrics
# ===========================================================================
class TestExtractedMetricsPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_metrics")

    def test_facade_has_all_functions(self):
        ctx = _make_full_ctx()
        m = ctx.get("extracted_metrics")
        for fn_name in ["ev", "ror", "r_multiple", "sharpe", "sortino", "dsr",
                         "kelly", "quarter_kelly", "hurst", "wfe", "max_streak",
                         "sqn", "streak_prob", "calmar"]:
            assert fn_name in m, f"Missing: {fn_name}"
            assert callable(m[fn_name])

    def test_ev(self):
        ctx = _make_full_ctx()
        ev = ctx.get("extracted_metrics")["ev"]
        # 3 wins (+2, +3, +1) and 2 losses (-1, -2)
        assert ev([2, 3, 1, -1, -2]) == pytest.approx((3 / 5) * 2.0 - (2 / 5) * 1.5)

    def test_r_multiple(self):
        ctx = _make_full_ctx()
        rm = ctx.get("extracted_metrics")["r_multiple"]
        # entry=100, stop=98, exit=106 → risk=2, R=(106-100)/2 = 3.0
        assert rm(100.0, 106.0, 98.0) == pytest.approx(3.0)
        # entry=100, stop=102, exit=96 → risk=2, R=(96-100)/2 = -2.0
        assert rm(100.0, 96.0, 102.0) == pytest.approx(-2.0)

    def test_quarter_kelly(self):
        ctx = _make_full_ctx()
        qk = ctx.get("extracted_metrics")["quarter_kelly"]
        # win_rate=0.6, avg_win_r=2.0 → Kelly = (0.6*2 - 0.4)/2 = 0.4
        assert qk(0.6, 2.0) == pytest.approx(0.1)  # quarter Kelly = 0.4/4

    def test_ror_zero_edge(self):
        ctx = _make_full_ctx()
        ror = ctx.get("extracted_metrics")["ror"]
        assert ror(0.0, 100) == 1.0
        assert ror(-0.05, 100) == 1.0

    def test_sqn(self):
        ctx = _make_full_ctx()
        sqn = ctx.get("extracted_metrics")["sqn"]
        assert sqn(100, 1.5, 2.0) == pytest.approx(7.5)

    def test_streak_prob(self):
        ctx = _make_full_ctx()
        sp = ctx.get("extracted_metrics")["streak_prob"]
        assert sp(0.5, 3) == pytest.approx(0.125)

    def test_calmar(self):
        ctx = _make_full_ctx()
        calmar = ctx.get("extracted_metrics")["calmar"]
        assert calmar(0.15, -0.10) == pytest.approx(1.5)  # abs(max_dd) in denominator


# ===========================================================================
# Module 02 — Validation Pipeline
# ===========================================================================
class TestExtractedValidationPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_validation")

    def test_facade_has_all(self):
        ctx = _make_full_ctx()
        v = ctx.get("extracted_validation")
        for key in ["cpcv", "overfit", "trial_ledger", "monte_carlo_shuffle",
                     "stress_scenarios", "cost_stress", "parameter_jitter"]:
            assert key in v

    def test_monte_carlo_shuffle(self):
        ctx = _make_full_ctx()
        mc = ctx.get("extracted_validation")["monte_carlo_shuffle"]
        # All positive returns → should pass
        returns = np.array([0.5, 0.3, 0.8, 0.2, 0.6, 0.4, 0.7, 0.1, 0.9, 0.35])
        result = mc(returns, n_sims=200)
        assert result["prob_profitability"] > 0.9
        assert result["passed"] is True

    def test_monte_carlo_shuffle_short(self):
        ctx = _make_full_ctx()
        mc = ctx.get("extracted_validation")["monte_carlo_shuffle"]
        result = mc(np.array([0.5]), n_sims=100)
        assert result["passed"] is False

    def test_stress_scenarios(self):
        ctx = _make_full_ctx()
        scenarios = ctx.get("extracted_validation")["stress_scenarios"]()
        assert len(scenarios) >= 4
        assert any("2008" in s["name"] for s in scenarios)

    def test_cost_stress(self):
        ctx = _make_full_ctx()
        cs = ctx.get("extracted_validation")["cost_stress"]
        returns = np.array([0.01, 0.005, 0.008, 0.003, 0.006, 0.004, 0.007])
        result = cs(returns, cost_multiplier=2.0)
        assert "stressed_sharpe" in result
        assert result["cost_multiplier"] == 2.0


# ===========================================================================
# Module 03 — Alpha Sources
# ===========================================================================
class TestExtractedAlphaSourcePlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_alpha_source")

    def test_detect_structural(self):
        ctx = _make_full_ctx()
        detect = ctx.get("extracted_alpha_source")["detect_structural"]
        # Normal volume → no opportunities
        df = _make_df()
        assert detect({"df": df}) == []
        # Volume spike → opportunity detected
        df_spike = df.copy()
        df_spike.loc[df_spike.index[-1], "tick_volume"] = 5000
        result = detect({"df": df_spike})
        assert len(result) >= 1
        assert result[0]["type"] == "volume_spike"

    def test_order_flow_imbalance(self):
        ctx = _make_full_ctx()
        ofi = ctx.get("extracted_alpha_source")["order_flow_imbalance"]
        df = _make_df()
        df["bid"] = df["close"] - 0.0002
        df["ask"] = df["close"] + 0.0002
        result = ofi(df)
        assert -1.0 <= result <= 1.0

    def test_gamma_regime_stub(self):
        ctx = _make_full_ctx()
        gr = ctx.get("extracted_alpha_source")["gamma_regime"]
        result = gr()
        # No options data → the committed market_structure_edges fallback
        # reports NEUTRAL (not the older "unknown" placeholder).
        assert result["regime"] == "NEUTRAL"


# ===========================================================================
# Module 04 — Regime Detection
# ===========================================================================
class TestExtractedRegimePlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_regime")

    def test_allocate_capital(self):
        ctx = _make_full_ctx()
        alloc = ctx.get("extracted_regime")["allocate_capital"]
        # Crisis → 100% cash
        crisis = alloc("HIGH_VOL_CRISIS")
        assert crisis["cash"] == 1.0
        assert crisis["momentum"] == 0.0
        # Trend → 80% momentum
        trend = alloc("LOW_VOL_TREND")
        assert trend["momentum"] == 0.8
        # Unknown → 100% cash fallback
        unknown = alloc("UNKNOWN_REGIME")
        assert unknown["cash"] == 1.0

    def test_regime_conditioned_ev(self):
        ctx = _make_full_ctx()
        rcev = ctx.get("extracted_regime")["regime_conditioned_ev"]
        # Trend regime, momentum has 1.0 EV → 0.8 * 1.0 = 0.8
        ev = rcev("LOW_VOL_TREND", {"momentum": 1.0, "mean_reversion": 0.5})
        assert ev == pytest.approx(0.8)

    def test_allocation_matrix_complete(self):
        ctx = _make_full_ctx()
        matrix = ctx.get("extracted_regime")["allocation_matrix"]
        for regime, alloc in matrix.items():
            total = sum(alloc.values())
            assert abs(total - 1.0) < 0.01, f"{regime} weights sum to {total}"


# ===========================================================================
# Module 05 — News Trading
# ===========================================================================
class TestExtractedNewsPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_news")

    def test_calculate_delta(self):
        ctx = _make_full_ctx()
        delta = ctx.get("extracted_news")["calculate_delta"]
        # Strong positive surprise
        result = delta(actual=300.0, forecast=200.0, historical_std=50.0)
        assert result["z_score"] == pytest.approx(2.0)
        assert result["severity"] == "critical"
        assert result["direction"] == "hawkish"
        # Minimal surprise
        result2 = delta(actual=201.0, forecast=200.0, historical_std=50.0)
        assert result2["severity"] == "low"
        # Negative surprise
        result3 = delta(actual=100.0, forecast=200.0, historical_std=50.0)
        assert result3["direction"] == "dovish"

    def test_detect_news_phase(self):
        ctx = _make_full_ctx()
        phase = ctx.get("extracted_news")["detect_news_phase"]
        r1 = phase("2026-09-01T14:30:00", "2026-09-01T14:20:00")
        assert r1["phase"] == "pre_news_imminent"
        r2 = phase("2026-09-01T14:30:00", "2026-09-01T14:32:00")
        assert r2["phase"] == "spike"
        r3 = phase("2026-09-01T14:30:00", "2026-09-01T15:00:00")
        assert r3["phase"] == "trend_continuation"

    def test_nfp_prediction(self):
        ctx = _make_full_ctx()
        predict = ctx.get("extracted_news")["predict_nfp"]
        result = predict(adp_change_k=250, adp_consensus_k=180)
        assert "directional_bias" in result
        assert "composite_score" in result

    def test_cpi_prediction(self):
        ctx = _make_full_ctx()
        predict = ctx.get("extracted_news")["predict_cpi"]
        result = predict(ppi_mom_pct=0.5, ppi_consensus_pct=0.2)
        assert "directional_bias" in result

    def test_tier_events_defined(self):
        ctx = _make_full_ctx()
        news = ctx.get("extracted_news")
        assert "tier_1_events" in news
        assert "tier_2_events" in news


# ===========================================================================
# Module 06 — Risk Management
# ===========================================================================
class TestExtractedRiskPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_risk")

    def test_position_size_1r(self):
        ctx = _make_full_ctx()
        ps = ctx.get("extracted_risk")["position_size_1r"]
        # equity=10000, risk=1%, entry=1.1000, stop=1.0980 → risk=$100, pip=0.0020
        size = ps(10000.0, 0.01, 1.1000, 1.0980)
        assert size == pytest.approx(50000.0)

    def test_vol_target_size(self):
        ctx = _make_full_ctx()
        vts = ctx.get("extracted_risk")["vol_target_size"]
        size = vts(10000.0, 0.01, 0.0050, k=2.0)
        assert size == pytest.approx(10000.0)

    def test_portfolio_heat(self):
        ctx = _make_full_ctx()
        ph = ctx.get("extracted_risk")["portfolio_heat"]
        positions = [{"risk_amount": 100}, {"risk_amount": 200}, {"risk_amount": 50}]
        assert ph(positions) == 350

    def test_correlation_gate(self):
        ctx = _make_full_ctx()
        cg = ctx.get("extracted_risk")["correlation_gate"]
        # Low correlation → not exceeded
        positions = [
            {"symbol": "EURUSD", "correlation": 0.3},
            {"symbol": "GBPUSD", "correlation": 0.3},
        ]
        result = cg(positions, threshold=0.6)
        assert result["exceeded"] is False
        # High correlation → exceeded
        positions_high = [
            {"symbol": "EURUSD", "correlation": 0.8},
            {"symbol": "GBPUSD", "correlation": 0.8},
        ]
        result2 = cg(positions_high, threshold=0.6)
        assert result2["exceeded"] is True
        assert len(result2["pairs"]) == 1

    def test_layer_architecture(self):
        ctx = _make_full_ctx()
        layers = ctx.get("extracted_risk")["layer_architecture"]
        assert "layer_1_strategy" in layers
        assert "layer_2_portfolio" in layers
        assert "layer_3_system" in layers


# ===========================================================================
# Module 07 — Autonomous Agent
# ===========================================================================
class TestExtractedAutonomousPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_autonomous")

    def test_constitutional_critique_approved(self):
        ctx = _make_full_ctx()
        critique = ctx.get("extracted_autonomous")["constitutional_critique"]
        strategy = {"max_drawdown": -0.05, "leverage": 1.5, "concentration": 0.10}
        result = critique(strategy)
        assert result["approved"] is True
        assert result["violations"] == []

    def test_constitutional_critique_violations(self):
        ctx = _make_full_ctx()
        critique = ctx.get("extracted_autonomous")["constitutional_critique"]
        strategy = {"max_drawdown": -0.20, "leverage": 3.0, "concentration": 0.30}
        result = critique(strategy)
        assert result["approved"] is False
        assert len(result["violations"]) >= 2

    def test_evaluate_promotion(self):
        ctx = _make_full_ctx()
        promote = ctx.get("extracted_autonomous")["evaluate_promotion"]
        # Not enough days → no promotion
        result = promote(0, {"positive_sharpe_days": 10, "kill_switch_free_days": 30})
        assert result["can_promote"] is False
        # Enough days → promotion
        result2 = promote(0, {"positive_sharpe_days": 30, "kill_switch_free_days": 60})
        assert result2["can_promote"] is True
        assert result2["next_level"] == 1
        # Already max level → no promotion
        result3 = promote(4, {"positive_sharpe_days": 100, "kill_switch_free_days": 200})
        assert result3["can_promote"] is False

    def test_trust_levels_defined(self):
        ctx = _make_full_ctx()
        tl = ctx.get("extracted_autonomous")["trust_levels"]
        assert len(tl) == 5
        assert tl[0]["name"] == "Observe"
        assert tl[4]["name"] == "Self-Managing"

    def test_seasons_defined(self):
        ctx = _make_full_ctx()
        seasons = ctx.get("extracted_autonomous")["seasons"]
        assert "exploration" in seasons
        assert "exploitation" in seasons
        assert "validation" in seasons
        assert "review" in seasons


# ===========================================================================
# Module 08 — Alternative Data
# ===========================================================================
class TestExtractedAltDataPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_alt_data")

    def test_gold_fair_value(self):
        ctx = _make_full_ctx()
        gfv = ctx.get("extracted_alt_data")["gold_fair_value"]
        result = gfv(tips_yield=1.5, dxy=103.0)
        assert "fair_value" in result
        assert result["fair_value"] > 0

    def test_detect_vol_regime(self):
        ctx = _make_full_ctx()
        dvr = ctx.get("extracted_alt_data")["detect_vol_regime"]
        # Contango: VIX < V3M
        result = dvr(vix=15.0, v3m=20.0)
        assert result["regime"] == "contango"
        # Backwardation: VIX >= V3M
        result2 = dvr(vix=25.0, v3m=20.0)
        assert result2["regime"] == "backwardation"

    def test_carry_signal(self):
        ctx = _make_full_ctx()
        cs = ctx.get("extracted_alt_data")["carry_signal"]
        result = cs(base_rate=0.05, quote_rate=0.02)
        assert result["carry"] == pytest.approx(0.03)
        assert result["direction"] == "long_base"
        assert result["annualized_pct"] == pytest.approx(3.0)

    def test_synthetic_index(self):
        ctx = _make_full_ctx()
        si = ctx.get("extracted_alt_data")["synthetic_index"]
        assert si({"A": 100, "B": 200, "C": 300}, divisor=3.0) == pytest.approx(200.0)
        assert si({}, divisor=1.0) == 0.0


# ===========================================================================
# Module 09 — Research Frameworks
# ===========================================================================
class TestExtractedResearchPlugin:
    def test_mounts(self):
        ctx = _make_full_ctx()
        assert ctx.has("extracted_research")

    def test_facade_has_configs(self):
        ctx = _make_full_ctx()
        r = ctx.get("extracted_research")
        for key in ["factorminer_config", "aqua_config", "mars_config", "challenge_map"]:
            assert key in r

    def test_factorminer_config(self):
        ctx = _make_full_ctx()
        cfg = ctx.get("extracted_research")["factorminer_config"]()
        assert cfg["experience_memory_size"] == 1000
        assert cfg["novelty_threshold"] == 0.3

    def test_aqua_config(self):
        ctx = _make_full_ctx()
        cfg = ctx.get("extracted_research")["aqua_config"]()
        assert cfg["sealed_sandbox"] is True
        assert cfg["shadow_deploy_before_apply"] is True

    def test_challenge_map(self):
        ctx = _make_full_ctx()
        cm = ctx.get("extracted_research")["challenge_map"]()
        assert "alpha_decay" in cm
        assert "overfitting" in cm
        assert "hallucination" in cm


# ===========================================================================
# Master Plugin — ExtractedModulesPlugin
# ===========================================================================
class TestExtractedModulesPlugin:
    def test_mounts_all(self):
        from core.harness.extracted_module_plugins import ExtractedModulesPlugin
        ctx = _make_ctx()
        ctx.plugin(ExtractedModulesPlugin)
        manifest = ctx.get("extracted_modules_manifest")
        assert manifest["total"] == 9
        assert len(manifest["mounted"]) >= 7  # at least most should mount

    def test_provides_all_facades(self):
        from core.harness.extracted_module_plugins import ExtractedModulesPlugin
        ctx = _make_ctx()
        ctx.plugin(ExtractedModulesPlugin)
        expected = [
            "extracted_metrics", "extracted_validation", "extracted_alpha_source",
            "extracted_regime", "extracted_news", "extracted_risk",
            "extracted_autonomous", "extracted_alt_data", "extracted_research",
        ]
        for svc in expected:
            assert ctx.has(svc), f"Service '{svc}' not provided by ExtractedModulesPlugin"

    def test_graceful_degradation_missing_deps(self):
        """If risk_manager is missing, risk facade should be skipped gracefully."""
        from core.harness.kernel import TradingContext
        from core.harness.extracted_module_plugins import ExtractedModulesPlugin

        ctx = TradingContext(name="NoDepsContext")
        # Mount WITHOUT RiskManagementPlugin — risk facade should be skipped
        ctx.plugin(ExtractedModulesPlugin)
        manifest = ctx.get("extracted_modules_manifest")
        assert manifest["failed"] <= 1  # at most 1 failure (the risk facade)
