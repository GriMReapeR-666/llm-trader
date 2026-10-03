"""Unit tests for standardized agent tools and skill registry."""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.trading_tools import (
    RESEARCH_TOOLS,
    evaluate_expectancy_balsara_ruin_kelly,
    evaluate_mathematical_pillars,
    calculate_fractional_differentiation,
    detect_bayesian_changepoints,
    estimate_transaction_costs_and_impact,
    run_kalman_statarb_tracking,
    test_cointegration_and_robustness,
)
from core.skill_registry import SKILL_REGISTRY


def test_research_tools_count():
    assert len(RESEARCH_TOOLS) >= 19
    tool_names = [t.name for t in RESEARCH_TOOLS]
    assert "evaluate_expectancy_balsara_ruin_kelly" in tool_names
    assert "calculate_fractional_differentiation" in tool_names
    assert "detect_bayesian_changepoints" in tool_names
    assert "estimate_transaction_costs_and_impact" in tool_names
    assert "run_kalman_statarb_tracking" in tool_names
    assert "test_cointegration_and_robustness" in tool_names


def test_tool_invocations():
    # 1. Math Expectancy & Balsara Ruin Tool
    res_math = evaluate_expectancy_balsara_ruin_kelly.invoke({"win_rate": 0.65, "win_loss_ratio": 1.8, "open_risk_r": 0.5, "total_trades": 100})
    assert res_math["is_mathematically_viable"] is True
    assert res_math["quarter_kelly_fraction"] > 0.0
    assert res_math["balsara_ruin_probability"] < 0.001

    # Test alias
    res_math_alias = evaluate_mathematical_pillars.invoke({"win_rate": 0.65, "win_loss_ratio": 1.8, "open_risk_r": 0.5, "total_trades": 100})
    assert res_math_alias["is_mathematically_viable"] is True

    # 2. FracDiff Tool
    prices = [100.0 + i * 0.2 for i in range(50)]
    res_fd = calculate_fractional_differentiation.invoke({"prices": prices, "d": 0.40})
    assert res_fd["d_order"] == 0.40
    assert res_fd["stationary_series_length"] > 0

    # 3. BOCPD Tool
    obs = [0.1, -0.2, 0.05, 0.15, -0.1, 4.5, 4.8, 5.1]
    res_bocpd = detect_bayesian_changepoints.invoke({"observations": obs, "hazard_lambda": 30.0})
    assert "detected_changepoints_count" in res_bocpd

    # 4. TCA Tool
    res_tca = estimate_transaction_costs_and_impact.invoke({"symbol": "XAUUSD", "side": "BUY", "shares": 5.0, "decision_price": 2630.0})
    assert res_tca["total_cost_dollar"] > 0.0

    # 5. Kalman StatArb Tool
    y_s = [100.0 + i * 0.1 for i in range(30)]
    x_s = [100.0 + i * 0.1 for i in range(30)]
    res_kalman = run_kalman_statarb_tracking.invoke({"y_series": y_s, "x_series": x_s})
    assert "dynamic_beta" in res_kalman

    # 6. Cointegration Robustness Tool
    res_coint = test_cointegration_and_robustness.invoke({"backtest_sharpe": 2.4, "backtest_cagr": 0.30, "backtest_max_dd": 0.10})
    assert res_coint["is_promotion_approved"] is True

    # 7. Monte Carlo Simulation Tool
    from core.trading_tools import (
        run_regime_monte_carlo,
        run_vectorized_backtest,
        query_alternative_data_registry,
        evaluate_portfolio_risk_gate,
        audit_journal_integrity,
    )
    res_mc = run_regime_monte_carlo.invoke({"r_multiples": [1.5, -1.0, 2.0, 1.0, -1.0, 0.5, 1.8], "n_simulations": 500})
    assert "median_max_drawdown" in res_mc
    assert res_mc["num_simulations"] == 500

    # 8. Alternative Data Registry Tool
    res_alt = query_alternative_data_registry.invoke({"domain": "ALL"})
    assert "proxies" in res_alt
    assert "AIS_VESSEL_FLOW" in res_alt["proxies"]

    # 9. Portfolio Risk Gate Tool
    res_risk = evaluate_portfolio_risk_gate.invoke({"symbol": "XAUUSD", "action": "BUY", "volume": 0.1, "entry_price": 2634.50, "stop_loss": 2628.90})
    assert res_risk["gate_status"] == "APPROVED"

    # 10. Journal Audit Tool
    res_audit = audit_journal_integrity.invoke({"db_path": "trading_state.db"})
    assert "total_checks" in res_audit

    # 11. Kronos Foundation Signals
    from core.trading_tools import (
        query_kronos_foundation_signals,
        simulate_causal_shock_propagation,
        match_historical_case_study_analogs,
        audit_edge_lifecycle_governance,
        query_economic_calendar_events,
        query_eia_energy_inventory,
        reconcile_broker_and_journal_orders,
        run_system_health_diagnostics,
    )
    res_kronos = query_kronos_foundation_signals.invoke({"symbol": "XAUUSD", "timeframe": "H1", "lookback_bars": 32})
    assert "predicted_close_mean" in res_kronos
    assert "direction_bias" in res_kronos

    # 12. Causal Shock Propagation
    res_shock = simulate_causal_shock_propagation.invoke({"shock_source": "FED_FUNDS_RATE", "target_asset": "XAUUSD", "shock_magnitude_bps": 50.0})
    assert "active_causal_paths_found" in res_shock
    assert res_shock["propagation_confidence"] > 0.5

    # 13. Case Study Analogs
    res_case = match_historical_case_study_analogs.invoke({"disruption_domain": "MARITIME_CHOKEPOINT"})
    assert res_case["total_case_studies_matched"] >= 1

    # 14. Edge Lifecycle Governance
    res_gov = audit_edge_lifecycle_governance.invoke({})
    assert "valid_stages" in res_gov

    # 15. Economic Calendar (mocked MT5 calendar feed: fail-closed offline)
    cal_df = pd.DataFrame([{
        "currency": "USD", "event_name": "Non-Farm Payrolls", "importance": "HIGH",
        "forecast_value": "170K", "previous_value": "150K",
        "timestamp": "2026-09-04T14:30:00+00:00",
    }])
    fake_cal = MagicMock()
    fake_cal.get_upcoming_events.return_value = cal_df
    with patch("data.mt5_calendar_enhanced.EnhancedMT5CalendarWrapper", return_value=fake_cal):
        res_cal = query_economic_calendar_events.invoke({"currency": "USD"})
    assert res_cal["events_count"] >= 1

    # 16. EIA Energy Inventory
    res_eia = query_eia_energy_inventory.invoke({})
    assert "oil_market_bias" in res_eia

    # 17. Reconcile Broker & Journal
    res_rec = reconcile_broker_and_journal_orders.invoke({"db_path": "trading_state.db"})
    assert res_rec["is_execution_synchronized"] is True

    # 18. System Health Diagnostics
    res_health = run_system_health_diagnostics.invoke({})
    assert "overall_health" in res_health

    # 19. Active Knowledge Retrieval & Audit
    from core.trading_tools import (
        recall_active_knowledge_context,
        ingest_quant_and_market_memory,
        query_retrieval_audit_trail,
        sync_working_memory_state,
    )
    res_mem = recall_active_knowledge_context.invoke({"symbol": "XAUUSD", "timeframe": "H1", "query_text": "disinflation_continuation"})
    assert res_mem["status"] == "KNOWLEDGE_RETRIEVED_AND_LOGGED"
    assert "retrieval_id" in res_mem

    res_ingest = ingest_quant_and_market_memory.invoke({"symbol": "XAUUSD", "expected_value_r": 1.04, "balsara_ruin_probability": 0.00008})
    assert res_ingest["status"] == "QUANT_INGESTED"

    res_trail = query_retrieval_audit_trail.invoke({"limit": 3})
    assert "audit_records" in res_trail

    res_sync = sync_working_memory_state.invoke({"session_key": "session_XAUUSD"})
    assert res_sync["status"] == "SYNCHRONIZED"


def test_skills_registry():
    skills = SKILL_REGISTRY.list_skills()
    assert len(skills) >= 11
    skill_names = [s["name"] for s in skills]
    assert "expectancy_balsara_ruin_audit" in skill_names
    assert "mathematical_pillars_audit" in skill_names
    assert "fractional_diff_stationarity" in skill_names
    assert "bocpd_changepoint_audit" in skill_names
    assert "transaction_cost_tca" in skill_names
    assert "kalman_statarb_tracker" in skill_names
    assert "cointegration_haircut_audit" in skill_names

    # Test invoking each skill
    res1 = SKILL_REGISTRY.invoke_skill("expectancy_balsara_ruin_audit")
    assert res1["status"] == "SUCCESS"
    assert res1["output"]["audit_pass"] is True

    res1_alias = SKILL_REGISTRY.invoke_skill("mathematical_pillars_audit")
    assert res1_alias["status"] == "SUCCESS"

    res2 = SKILL_REGISTRY.invoke_skill("fractional_diff_stationarity")
    assert res2["status"] == "SUCCESS"

    res3 = SKILL_REGISTRY.invoke_skill("transaction_cost_tca")
    assert res3["status"] == "SUCCESS"
