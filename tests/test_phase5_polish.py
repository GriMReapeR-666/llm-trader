"""Phase 5 polish coverage: margin live query, memory, e2e, latency, smoke."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations.agent_operations import AgentOperations
from intelligence.lag_delay_engine import ConfidenceDecayEngine, SmithPredictorEngine, TimestampedEvent, utcnow_iso
from core.margin_safety import MarginSafetyGate
from core.rate_limiter import RateLimiter
from research.rl_action_bridge import bridge_rl_action
from research.rl_trainer import RLTrainer
from intelligence.semantic_memory import embed, embedding_backend_name, status as semantic_status
from research.setup_labeling import classify_setup_outcome
from intelligence.local_agent_memory import LocalAgentMemory, LocalAgentMemoryConfig
from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore


def test_assess_from_mt5_queries_account_and_margin():
    account = SimpleNamespace(
        equity=10_000.0, margin=1_000.0, margin_free=9_000.0,
        margin_level=1000.0, margin_so_mode=0, margin_so_call=100.0, margin_so_so=50.0,
    )
    mt5 = MagicMock()
    mt5.account_info.return_value = account
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.order_calc_margin.return_value = 500.0

    result = MarginSafetyGate().assess_from_mt5(
        mt5_module=mt5,
        symbol="EURUSD",
        side="BUY",
        volume=0.1,
        price=1.1,
        new_risk=100.0,
        driver_buckets=["USD"],
        position_margin_estimates=[],
    )
    assert result.approved is True
    mt5.account_info.assert_called_once()
    mt5.order_calc_margin.assert_called_once()


def test_assess_from_mt5_fails_closed_without_module():
    result = MarginSafetyGate().assess_from_mt5(
        mt5_module=None, symbol="EURUSD", side="BUY", volume=0.1, price=1.1,
        new_risk=50.0, driver_buckets=[],
    )
    assert result.approved is False
    assert "unavailable" in result.reasons[0].lower() or result.mode == "unavailable"


def test_local_memory_status_is_non_authoritative():
    mem = LocalAgentMemory(LocalAgentMemoryConfig(enabled=False, db_path=":memory:"))
    status = mem.status()
    assert status["authoritative"] is False
    assert status["execution_authority"] is False
    assert status["cloud"] is False


def test_semantic_default_backend_is_hash(monkeypatch):
    monkeypatch.delenv("TRADING_SEMANTIC_EMBEDDINGS", raising=False)
    assert embedding_backend_name() == "hash"
    backend, vector = embed({"symbol": "EURUSD", "lesson": "fade news spikes"})
    assert backend == "hash"
    assert isinstance(vector, dict)
    st = semantic_status()
    assert st["active_backend"] == "hash"
    assert st["execution_authority"] is False


def test_put_working_memory_auto_compacts(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_WORKING_MEMORY_MAX_ACTIVE", "3")
    monkeypatch.setenv("TRADING_WORKING_MEMORY_MAX_CHARS", "20000")
    ops = AgentOperations(db_path=str(tmp_path / "ops.db"))
    ns = "auto_compact"
    for i in range(6):
        result = ops.put_working_memory(ns, f"k{i}", {"i": i}, kind="observation", priority=40)
        assert "compaction" in result
        assert result.get("execution_authority") is False
    with ops._con() as con:
        active = con.execute(
            "SELECT COUNT(*) AS n FROM working_memory WHERE namespace=? AND status='ACTIVE'",
            (ns,),
        ).fetchone()["n"]
    # safety kinds none; max_active=3 non-safety retained
    assert active <= 4  # compact summary row may remain active


def test_setup_labeling_and_rate_limiter_smoke():
    labeled = classify_setup_outcome("REJECTED", "validator blocked")
    assert labeled["label"] == "REJECTED_VALIDATOR"
    limiter = RateLimiter(max_requests=2, time_window=60)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False


def test_end_to_end_trade_lifecycle_without_mt5(tmp_path):
    """tick → validate → open → close → journal → memory (mock broker)."""
    store = TradingStore(str(tmp_path / "lifecycle.db"))
    ops = AgentOperations(db_path=str(tmp_path / "lifecycle.db"))
    validator = DeterministicValidator(store)

    # 1) Tick / lag envelope
    now = utcnow_iso()
    event = TimestampedEvent(
        event_id="tick-1", source="MT5", t_event=now, t_ingest=now, t_proc=now,
        data_type="price_tick",
    )
    lag_confidence = ConfidenceDecayEngine.calculate_confidence(event.staleness_ms, "price_tick")
    assert lag_confidence > 0.5
    smith = SmithPredictorEngine.predict_state("EURUSD", 1.1000, event.staleness_ms, current_atr=0.001)

    # 2) Signal / RL bridge
    bridged = bridge_rl_action(
        "BUY",
        toon={"dom": {"best_bid": 1.0998, "best_ask": 1.1000}, "indicators": {"atr_14": 0.001}},
        atr_sl_mult=1.5,
        atr_tp_mult=3.5,
    )
    assert bridged.action == "BUY"
    assert bridged.execution_authority is False
    assert bridged.entry and bridged.stop_loss and bridged.take_profit

    proposal = TradeProposal(
        strategy="scalp", symbol="EURUSD", side="BUY",
        entry=float(bridged.entry),
        stop_loss=float(bridged.stop_loss),
        take_profit=float(bridged.take_profit),
        confidence=0.8,
        features={
            "ema_alignment": 1, "mtf_alignment": 1, "adx": 25,
            "near_key_level": True, "confirmed_candle": True, "volume_confirmed": True,
            "setup_id": "e2e_test",
        },
    )
    assert proposal.reward_risk >= 2.0

    market = {
        "market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
        "symbol_tradable": True, "spread": 0.0001, "max_spread": 0.001,
        "clock_ok": True, "session_allowed": True,
        "liquidity_ok": True, "microstructure_ok": True,
        "required_data_ok": True,
        "completed_candle": True, "breakout_event": False,
        "stop_distance": abs(proposal.entry - proposal.stop_loss),
        "min_stop_distance": 0.0001,
        "high_impact_news": False, "setup_cooldown_seconds": 0,
        "execution_price": proposal.entry, "entry_tolerance": 0.001,
        "lag_confidence": lag_confidence,
        "predicted_slippage": smith.predicted_slippage,
    }

    # 3) Risk validation
    validation = validator.validate(proposal, market)
    assert validation.approved, validation.reasons
    store.log_proposal(proposal, validation)
    store.transition_latest_setup(proposal.setup_key(), "ACCEPTED", reason="e2e broker accept")

    # 4) Order routing / execution (journaled fill)
    ticket = 424242
    store.record_open_trade(ticket, proposal, volume=0.1, risk_amount=50.0, payload={"source": "e2e"}, provenance="mt5_broker")
    store.record_execution_report(
        event_type="FILLED", order_ticket=ticket, position_ticket=ticket,
        requested_price=proposal.entry, filled_price=proposal.entry + 0.00005,
        requested_volume=0.1, filled_volume=0.1,
        implementation_shortfall=0.00005,
        payload={"predicted_slippage": smith.predicted_slippage},
    )

    # 5) Position tracking → close
    store.record_close(ticket, exit_price=proposal.take_profit, pnl=100.0, reason="TARGET")

    # 6) Memory / RL update
    ops.put_working_memory("e2e", f"trade:{ticket}", {"pnl": 100.0, "label": "WIN"}, kind="observation")
    trainer = RLTrainer(model_path=str(tmp_path / "e2e_rl.json"))
    state = {
        "rsi_14": 55, "macd_histogram": 0.1, "ma_alignment": 1, "confluence_score": 6,
        "near_sr": True, "htf_trend": 1, "news_sentiment": 0.1,
        "volatility_regime": "medium", "trend_strength": 0.5, "session": "london",
    }
    reward = trainer.calculate_reward(
        {"pnl": 100.0, "risk": 50.0, "confluence_score": 6, "broker_reconciled": True}
    )
    before = trainer.total_updates
    trainer.update(state, "BUY", reward, {"position": "FLAT"}, done=True)
    assert trainer.total_updates == before + 1

    opens = store.open_trade_rows()
    assert all(int(row["ticket"]) != ticket for row in opens)


def test_latency_bounds_for_core_paths(tmp_path):
    """Lightweight timing assertions (no pytest-benchmark dependency)."""
    t0 = time.perf_counter()
    for _ in range(200):
        ConfidenceDecayEngine.calculate_confidence(25.0, "price_tick")
        SmithPredictorEngine.predict_state("EURUSD", 1.1, 25.0, current_atr=0.001)
    lag_ms = (time.perf_counter() - t0) * 1000.0
    assert lag_ms < 500.0, f"lag/smith path too slow: {lag_ms:.1f}ms"

    t1 = time.perf_counter()
    for _ in range(50):
        embed({"symbol": "EURUSD", "text": "risk reduced after news fade"})
    embed_ms = (time.perf_counter() - t1) * 1000.0
    assert embed_ms < 1000.0, f"hash embed path too slow: {embed_ms:.1f}ms"

    trainer = RLTrainer(model_path=str(tmp_path / "latency_rl.json"))
    state = {
        "rsi_14": 40, "macd_histogram": -0.1, "ma_alignment": -1, "confluence_score": 4,
        "near_sr": False, "htf_trend": -1, "news_sentiment": -0.2,
        "volatility_regime": "high", "trend_strength": 0.4, "session": "ny",
    }
    t2 = time.perf_counter()
    for _ in range(100):
        trainer.select_action(state, explore=False)
    rl_ms = (time.perf_counter() - t2) * 1000.0
    assert rl_ms < 500.0, f"RL select_action too slow: {rl_ms:.1f}ms"
