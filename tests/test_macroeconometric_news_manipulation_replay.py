"""Tests for Macroeconometric SVAR Impulse Response, Liquidity Manipulation Detection, and All-Day News Replay."""
from __future__ import annotations

import pytest
import numpy as np
from typing import Dict, List, Any

from research.macroeconometric_var_engine import (
    MacroeconometricVAREngine,
    SVARImpulseResponse,
    estimate_macro_target_price,
)
from intelligence.news_liquidity_manipulation_detector import (
    NewsLiquidityManipulationDetector,
    LiquiditySweepVerdict,
    ManipulationSetup,
)


def test_macroeconometric_var_irf_calculation():
    """Verify SVAR Impulse Response function converges and estimates directional macro target."""
    engine = MacroeconometricVAREngine(lags=2)
    
    # Positive CPI inflation shock (+2.5 sigma) on EURUSD (bearish FX response)
    irf = engine.compute_impulse_response(
        shock_variable="CPI_YOY",
        shock_magnitude=2.50,
        target_asset="EURUSD",
        horizon=20
    )
    
    assert isinstance(irf, SVARImpulseResponse)
    assert irf.shock_variable == "CPI_YOY"
    assert irf.target_asset == "EURUSD"
    assert irf.directional_bias == "SELL"
    assert len(irf.trajectory) == 20
    assert irf.cumulative_impact < 0.0  # EURUSD declines on hawkish US CPI
    assert irf.half_life_bars > 0
    assert irf.peak_impact_bar >= 1

    # Macro target price calculation
    entry_price = 1.1180
    target_price = estimate_macro_target_price(entry_price, irf)
    assert target_price < entry_price  # Target is lower for SELL


def test_liquidity_manipulation_sweep_bullish_trap():
    """Verify detection of a bullish liquidity trap (Judas swing above EQH before a macro SELL)."""
    detector = NewsLiquidityManipulationDetector(sweep_tolerance_pips=1.5)

    # 10 bars of pre-event price action forming an Equal High (EQH) at 1.1200
    opens  = [1.1180, 1.1185, 1.1190, 1.1195, 1.1198, 1.1190, 1.1185, 1.1192, 1.1199, 1.1190]
    highs  = [1.1190, 1.1195, 1.1200, 1.1200, 1.1200, 1.1198, 1.1195, 1.1200, 1.1200, 1.1195]
    lows   = [1.1175, 1.1180, 1.1185, 1.1188, 1.1185, 1.1180, 1.1180, 1.1185, 1.1185, 1.1180]
    closes = [1.1185, 1.1190, 1.1195, 1.1198, 1.1192, 1.1185, 1.1192, 1.1198, 1.1190, 1.1185]

    # Bar 10: News release bar spikes up to 1.1215 (sweeping 1.1200 EQH) but closes at 1.1170
    opens.append(1.1185)
    highs.append(1.1215)   # Judas swing wick sweeping BSL
    lows.append(1.1165)
    closes.append(1.1170)  # Strong bearish displacement

    verdict = detector.analyze_news_bar(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        macro_directional_bias="SELL",
        news_bar_index=10,
        pip_size=0.0001
    )

    assert isinstance(verdict, LiquiditySweepVerdict)
    assert verdict.is_manipulation_detected is True
    assert verdict.sweep_type == "BUY_SIDE_LIQUIDITY_SWEPT"
    assert verdict.swept_level == pytest.approx(1.1200, abs=0.0002)
    assert verdict.manipulation_extreme == 1.1215
    assert verdict.market_structure_shift is True
    assert verdict.setup is not None
    assert verdict.setup.suggested_action == "SELL"
    assert verdict.setup.stop_loss > verdict.manipulation_extreme or verdict.setup.stop_loss == verdict.manipulation_extreme


def test_liquidity_manipulation_sweep_bearish_trap():
    """Verify detection of a bearish liquidity trap (Judas swing below EQL before a macro BUY)."""
    detector = NewsLiquidityManipulationDetector(sweep_tolerance_pips=1.5)

    # 10 bars of pre-event price action forming an Equal Low (EQL) at 1.3000
    opens  = [1.3020, 1.3015, 1.3008, 1.3005, 1.3012, 1.3018, 1.3010, 1.3005, 1.3008, 1.3015]
    highs  = [1.3030, 1.3025, 1.3018, 1.3015, 1.3022, 1.3025, 1.3018, 1.3015, 1.3018, 1.3025]
    lows   = [1.3010, 1.3002, 1.3000, 1.3000, 1.3001, 1.3008, 1.3002, 1.3000, 1.3000, 1.3005]
    closes = [1.3015, 1.3008, 1.3005, 1.3010, 1.3018, 1.3012, 1.3005, 1.3008, 1.3015, 1.3020]

    # Bar 10: News release bar dumps down to 1.2982 (sweeping 1.3000 EQL) but closes at 1.3035
    opens.append(1.3020)
    highs.append(1.3040)
    lows.append(1.2982)    # Judas swing wick sweeping SSL
    closes.append(1.3035)  # Strong bullish displacement

    verdict = detector.analyze_news_bar(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        macro_directional_bias="BUY",
        news_bar_index=10,
        pip_size=0.0001
    )

    assert isinstance(verdict, LiquiditySweepVerdict)
    assert verdict.is_manipulation_detected is True
    assert verdict.sweep_type == "SELL_SIDE_LIQUIDITY_SWEPT"
    assert verdict.swept_level == pytest.approx(1.3000, abs=0.0002)
    assert verdict.manipulation_extreme == 1.2982
    assert verdict.market_structure_shift is True
    assert verdict.setup is not None
    assert verdict.setup.suggested_action == "BUY"
    assert verdict.setup.stop_loss <= verdict.manipulation_extreme


def test_mitigation_entry_and_asymmetric_rr():
    """Verify mitigation zone calculation provides high asymmetric R:R to macro target."""
    detector = NewsLiquidityManipulationDetector()
    
    setup = detector.build_manipulation_setup(
        side="SELL",
        sweep_extreme=1.1215,
        displacement_close=1.1170,
        macro_target_price=1.1090,
        pip_size=0.0001,
        spread_pips=0.6
    )

    assert isinstance(setup, ManipulationSetup)
    assert setup.suggested_action == "SELL"
    # Entry at mitigation retracement (e.g. around 1.1185 - 1.1195)
    assert 1.1170 <= setup.limit_entry_price <= 1.1215
    # Stop loss placed beyond sweep extreme with buffer
    assert setup.stop_loss >= 1.1215
    # Target is the macro target
    assert setup.take_profit == 1.1090
    # Reward to risk must be >= 1.5
    risk = setup.stop_loss - setup.limit_entry_price
    reward = setup.limit_entry_price - setup.take_profit
    assert reward / max(risk, 1e-6) >= 1.5


def test_all_day_news_replay_and_svar_manipulation_integration(tmp_path):
    """Verify full end-to-end all-day news replay with SVAR and Judas swing capture."""
    from research.deterministic_historical_scenario_trainer import (
        DeterministicHistoricalScenarioTrainer,
        HISTORICAL_DAY_SCENARIOS,
    )

    # 1. Verify all 5 days have full-day news coverage (>= 8 dispatches each)
    for scen in HISTORICAL_DAY_SCENARIOS:
        assert len(scen["news_dispatches"]) >= 8, f"Day {scen['day']} has insufficient news dispatches"
        # Check sessions represented: Asian (offset <= 300), London (400-800), US/NY (>= 900)
        offsets = [d["time_offset_min"] for d in scen["news_dispatches"]]
        assert any(o <= 300 for o in offsets), f"Day {scen['day']} missing Asian session news"
        assert any(400 <= o <= 800 for o in offsets), f"Day {scen['day']} missing London session news"
        assert any(o >= 900 for o in offsets), f"Day {scen['day']} missing US session news"

    # 2. Run isolated replay
    test_db = str(tmp_path / "test_research_memory.db")
    trainer = DeterministicHistoricalScenarioTrainer(seed=42, db_path=test_db)
    res = trainer.run_full_historical_training()

    assert res["status"] == "COMPLETED"
    assert res["cumulative_pnl"] > 0.0
    assert len(res["scorecards"]) == 5

    # Verify scorecards have macro target, sweep type, and positive returns
    for score in res["scorecards"]:
        assert "macro_target" in score
        assert "sweep_type" in score
        assert score["pnl"] > 0.0

