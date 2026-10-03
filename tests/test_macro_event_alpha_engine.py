"""Test Suite for Macroeconomic Event-Driven Alpha & Surprise Transmission Engine."""
import pytest
import time
import numpy as np

from core.harness import TradingContext
from core.harness.plugins import (
    RiskManagementPlugin,
    TradingMemoryPlugin,
    BrokerPlugin,
    CausalIntelligencePlugin,
    DataHubPlugin,
    AnalyticalFrameworksPlugin,
    KronosIntelligencePlugin,
    ExecutionSafetyPlugin,
    CointegrationGovernancePlugin,
    MacroEventAlphaPlugin,
    QuantWorkflowsPlugin,
)
from core.harness.workflows import QuantWorkflowEngine, MacroEventAlphaWorkflow
from core.macro_event_alpha_engine import (
    MacroLeadingIndicatorScorecard,
    RollingEconomicSurpriseIndex,
    StandardizedSurpriseDecomposer,
    MacroRegimePositioningArbitrator,
    ThreePhaseEventStateMachine,
    MacroEventAlphaEngine,
)


@pytest.fixture
def macro_event_context(tmp_path):
    db_path = str(tmp_path / "macro_event_test.db")
    ctx = TradingContext(name="MacroEventTestCtx")
    ctx.plugin(TradingMemoryPlugin, db_path=db_path)
    ctx.plugin(RiskManagementPlugin, db_path=db_path, balance=10000.0, max_risk_pct=0.01)
    ctx.plugin(BrokerPlugin, paper_mode=True, db_path=db_path)
    ctx.plugin(CausalIntelligencePlugin, db_path=db_path)
    ctx.plugin(DataHubPlugin)
    ctx.plugin(AnalyticalFrameworksPlugin)
    ctx.plugin(KronosIntelligencePlugin)
    ctx.plugin(ExecutionSafetyPlugin)
    ctx.plugin(CointegrationGovernancePlugin)
    ctx.plugin(MacroEventAlphaPlugin)
    ctx.plugin(QuantWorkflowsPlugin)
    yield ctx
    ctx.dispose()


def test_nfp_leading_indicator_scorecard():
    scorecard = MacroLeadingIndicatorScorecard.calculate_nfp_scorecard(
        adp_change_k=240.0,
        adp_consensus_k=170.0,
        jobless_claims_4w_ma_k=205.0,
        ism_employment_pmi=53.5,
        jolts_openings_millions=8.8,
        esi_trend_score=65.0,
    )
    assert scorecard.event_name == "Non-Farm Payrolls (NFP)"
    assert scorecard.composite_score > 5.0
    assert scorecard.directional_bias in ("MODERATE_BEAT", "STRONG_BEAT")
    assert scorecard.confidence >= 0.60


def test_cpi_leading_indicator_scorecard():
    scorecard = MacroLeadingIndicatorScorecard.calculate_cpi_scorecard(
        ppi_mom_pct=0.5,
        ppi_consensus_pct=0.1,
        crude_oil_30d_change_pct=6.5,
        agriculture_basket_change_pct=2.0,
        metals_basket_change_pct=3.0,
        ism_prices_paid_pmi=62.0,
        wage_growth_yoy_pct=4.4,
    )
    assert scorecard.event_name == "Consumer Price Index (CPI)"
    assert scorecard.composite_score > 8.0
    assert scorecard.directional_bias in ("MODERATE_BEAT", "STRONG_BEAT")


def test_central_bank_scorecard():
    scorecard = MacroLeadingIndicatorScorecard.calculate_central_bank_scorecard(
        cme_fedwatch_hike_prob_pct=85.0,
        cme_fedwatch_cut_prob_pct=5.0,
        yield_curve_2y10y_bps=15.0,
        esi_score=75.0,
        inflation_momentum_3m_pct=0.4,
        speaker_tone_score=3.5,
    )
    assert scorecard.event_name == "Central Bank Interest Rate Decision"
    assert scorecard.composite_score > 10.0
    assert "HAWKISH" in scorecard.directional_bias


def test_rolling_economic_surprise_index():
    releases = [
        {"event": "NFP", "actual": 220, "consensus": 180},
        {"event": "CPI", "actual": 0.4, "consensus": 0.2},
        {"event": "GDP", "actual": 2.8, "consensus": 2.2},
        {"event": "RETAIL", "actual": 0.6, "consensus": 0.3},
        {"event": "PMI", "actual": 51.5, "consensus": 49.5},
    ]
    esi = RollingEconomicSurpriseIndex.calculate_esi(releases)
    assert esi["esi_score"] == 100.0
    assert esi["momentum"] == "PERSISTENT_EXPANSION_HAWKISH"
    assert esi["bullish_currency_bias"] is True


def test_standardized_surprise_decomposition():
    decomp = StandardizedSurpriseDecomposer.decompose_surprise(
        event_name="NFP",
        actual=270.0,
        consensus=180.0,
    )
    assert decomp["delta"] == 90.0
    assert decomp["z_delta"] == 2.0  # 90 / 45
    assert decomp["impact_level"] == "REGIME_SHIFT_STRUCTURAL_SHOCK"
    assert "MODEL_A_POST_NEWS_PULLBACK" in decomp["permitted_models"]


def test_regime_and_positioning_arbitration():
    # 1. Multiplier cascade on deterioration
    miss_res = MacroRegimePositioningArbitrator.evaluate_regime_and_crowding(
        z_delta=-1.8,
        macro_regime="RECESSION",
        cot_speculative_percentile=50.0,
    )
    assert miss_res["conviction_multiplier"] >= 1.50
    assert miss_res["reaction_type"] == "MULTIPLIER_DETERIORATION_CASCADE"

    # 2. Diminishing returns on over-crowded longs
    crowd_res = MacroRegimePositioningArbitrator.evaluate_regime_and_crowding(
        z_delta=1.5,
        macro_regime="EXPANSION",
        cot_speculative_percentile=92.0,
    )
    assert crowd_res["is_exhaustion_trap"] is True
    assert crowd_res["conviction_multiplier"] < 1.0


def test_three_phase_event_state_machine():
    cur_t = time.time()
    
    # Phase 2: Toxic Spike Blackout (T = 0s)
    p2 = ThreePhaseEventStateMachine.evaluate_phase(
        event_time_unix=cur_t,
        current_time_unix=cur_t,
        quote_spread=0.0001,
        baseline_spread=0.0001,
        spike_high=1.0900,
        spike_low=1.0850,
        current_price=1.0880,
    )
    assert p2["phase"] == "PHASE_2_TOXIC_SPIKE_BLACKOUT"
    assert p2["can_execute_market"] is False

    # Phase 3: Post-News Real Trend & Fib Retracement (T = +20m)
    p3 = ThreePhaseEventStateMachine.evaluate_phase(
        event_time_unix=cur_t - 1200,
        current_time_unix=cur_t,
        quote_spread=0.0001,
        baseline_spread=0.0001,
        spike_high=1.0900,
        spike_low=1.0800,
        current_price=1.0860,
        direction="BUY",
    )
    assert p3["phase"] == "PHASE_3_POST_NEWS_TREND"
    assert p3["can_execute_market"] is True
    assert p3["fib_levels"]["fib_500"] == 1.0850
    assert p3["fib_levels"]["optimal_entry"] == 1.0850


def test_macro_event_alpha_workflow_execution(macro_event_context):
    engine: QuantWorkflowEngine = macro_event_context.get("quant_workflow_engine")
    cur_t = time.time()
    snapshot = {
        "symbol": "EURUSD",
        "event_name": "NFP",
        "actual": 260.0,
        "consensus": 180.0,
        "previous": 175.0,
        "event_timestamp_unix": cur_t - 1200,  # 20m post release
        "price": 1.0850,
        "spike_high": 1.0880,
        "spike_low": 1.0840,
        "atr": 0.0015,
        "quote_spread": 0.0001,
        "baseline_spread": 0.0001,
        "macro_regime": "EXPANSION",
        "cot_percentile": 50.0,
    }
    res = engine.execute_workflow("macro_event_alpha", ctx=macro_event_context, symbol="EURUSD", market_snapshot=snapshot)
    assert res.workflow_name == "macro_event_alpha"
    assert res.decision == "EXECUTE"
    assert res.proposal is not None
    assert res.proposal["strategy"] == "macro_event_alpha_pullback"
    assert res.execution_ticket == 9606
