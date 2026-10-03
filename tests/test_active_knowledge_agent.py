"""Unit tests for the Active Knowledge Agent, Multi-Tier Memory, Quant Ingestion, and Immutable Retrieval Logs."""
import os
import pytest
import sqlite3
import time

from intelligence.active_knowledge_agent import (
    ActiveKnowledgeAgent,
    robust_retry,
    ACTIVE_KNOWLEDGE_AGENT,
)


@pytest.fixture
def temp_agent(tmp_path):
    db_file = str(tmp_path / "test_active_memory.db")
    agent = ActiveKnowledgeAgent(db_path=db_file, max_working_memory_tokens=500)
    return agent


def test_multi_tier_retrieval_and_audit_logging(temp_agent):
    # Test multi-tier retrieval for XAUUSD
    res = temp_agent.retrieve(
        query_text="US Core PCE disinflation continuation setup",
        query_tags={"symbol": "xauusd", "strategy": "macro_continuation_v1", "disruption_domain": "MARITIME_CHOKEPOINT"},
        symbol="XAUUSD",
        timeframe="H1",
        trigger_event="UNIT_TEST_EVALUATION",
    )
    
    assert res["status"] == "SUCCESS"
    assert "retrieval_id" in res
    assert res["latency_ms"] >= 0.0
    assert res["injected_context_tokens"] > 0
    assert "knowledge" in res
    
    # Verify immutable audit trail persisted in SQLite
    trails = temp_agent.get_retrieval_audit_trail(limit=5, symbol="XAUUSD")
    assert len(trails) >= 1
    assert trails[0]["trigger_event"] == "UNIT_TEST_EVALUATION"
    assert trails[0]["symbol"] == "XAUUSD"
    assert trails[0]["retrieval_id"] == res["retrieval_id"]


def test_quant_snapshot_ingestion_and_reflection_loop(temp_agent):
    # 1. Ingest real-time quant pillars
    snap = temp_agent.ingest_quant_snapshot(
        symbol="XAUUSD",
        timeframe="H1",
        expected_value_r=1.25,
        balsara_ruin_probability=0.00005,
        quarter_kelly_fraction=0.16,
        run_theory_l_max=4,
        hurst_exponent=0.64,
        bocpd_regime_shift=False,
        kalman_dynamic_beta=1.05,
        ornstein_uhlenbeck_half_life=6.2,
        fractional_diff_stationary=True,
        var_95_dollar=48.0,
        portfolio_heat_r=1.2,
    )
    assert snap["status"] == "QUANT_INGESTED"
    assert snap["is_viable"] is True

    # 2. Retrieve latest quant state
    latest = temp_agent.get_latest_quant_snapshot(symbol="XAUUSD")
    assert latest is not None
    assert latest["expected_value_r"] == 1.25
    assert latest["hurst_exponent"] == 0.64

    # 3. Simulate trade execution and bind to retrieval ID
    ret = temp_agent.retrieve(query_text="pre_order", query_tags={"symbol": "xauusd"}, symbol="XAUUSD")
    ret_id = ret["retrieval_id"]
    temp_agent.ingest_trade_execution(
        ticket=9901,
        symbol="XAUUSD",
        setup_name="macro_continuation_v1",
        entry_price=2634.50,
        stop_loss=2628.90,
        take_profit=2648.00,
        volume=0.10,
        rationale="Strong EV + Causal edge",
        retrieval_id=ret_id,
    )

    # 4. Post-trade reflection loop
    refl = temp_agent.ingest_trade_reflection(
        ticket=9901,
        symbol="XAUUSD",
        setup_name="macro_continuation_v1",
        close_price=2648.00,
        r_multiple=2.41,
        pnl=135.00,
        exit_reason="TAKE_PROFIT_HIT",
        mae_r=0.20,
        mfe_r=2.45,
    )
    assert refl["status"] == "MEMORY_CONSOLIDATED"
    assert refl["r_multiple"] == 2.41

    # Verify audit trail updated with trade outcome
    trail = temp_agent.get_retrieval_audit_trail(limit=5, symbol="XAUUSD")
    matched = [t for t in trail if t["retrieval_id"] == ret_id]
    assert len(matched) == 1
    assert matched[0]["outcome_r"] == 2.41
    assert matched[0]["resulting_action"] == "TRADE_CLOSED_REFLECTED"


def test_working_memory_compaction_and_token_budget(temp_agent):
    # Add multiple items to exceed token budget of 500
    for i in range(25):
        temp_agent.add_working_item(
            session_key="session_EURUSD",
            kind="MARKET_TICK_FEATURE",
            importance=0.10 + (i * 0.02),  # Lower importance items get compacted
            payload={"bar_index": i, "price": 1.0850 + i * 0.0010, "data": "A" * 300},
        )
    
    sync_res = temp_agent.sync_and_compact_working_memory(session_key="session_EURUSD")
    assert sync_res["status"] == "SYNCHRONIZED"
    assert sync_res["estimated_token_usage"] <= temp_agent.max_working_memory_tokens

    # Verify that compacted rows exist in database
    with temp_agent._con() as con:
        compacted_db_count = con.execute("SELECT count(*) FROM working_context_items WHERE status = 'COMPACTED'").fetchone()[0]
        assert compacted_db_count > 0


def test_robust_retry_decorator_and_dead_letter(temp_agent):
    attempts = 0

    @robust_retry(max_retries=3, base_delay=0.01)
    def flaky_function():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("Database locked simulated")
        return "SUCCESS_AFTER_RETRY"

    res = flaky_function()
    assert res == "SUCCESS_AFTER_RETRY"
    assert attempts == 3

    # Test dead-letter logging
    temp_agent.log_dead_letter(
        channel="MT5_FEED",
        payload={"symbol": "XAUUSD", "raw": "MALFORMED_PACKET"},
        error_msg="ParserError",
    )
    with temp_agent._con() as con:
        row = con.execute("SELECT * FROM dead_letter_vault WHERE channel = 'MT5_FEED'").fetchone()
        assert row is not None
        assert "MALFORMED_PACKET" in row["payload"]
