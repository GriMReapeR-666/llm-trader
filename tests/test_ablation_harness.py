"""Regression: ablation harness (PU-0156 §2).

Env-gated component removal (PU_ABLATION = no_llm / no_news / no_macro) for
marginal-contribution measurement. Contract pinned here:
  1. Modes parse from env at boot; unknown tokens are dropped.
  2. no_llm settles the candle task as HOLD *before* any LLM reservation —
     zero llm_requests rows for the cycle, durable boot marker emitted.
  3. no_news bypasses the news gate (gate.evaluate never called) and emits a
     durable bypass event.
  4. no_macro strips macro context keys from the decision payload (helper).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from operations.agent_operations import AgentOperations
from mt5_trader import LLMOrchestrator
from core.trading_safety import TradingStore


def _orchestrator(tmp_path, ablation_env=None):
    risk = MagicMock()
    risk.risk_per_trade = 0.01
    risk.open_positions = []
    db = str(tmp_path / "abl.db")
    env = {"PU_ABLATION": ablation_env} if ablation_env else {}
    with patch("mt5_trader.TradingStore",
               lambda **kwargs: TradingStore(
                   path=db, **{k: v for k, v in kwargs.items() if k != "path"})):
        if env:
            with patch.dict("os.environ", env):
                orch = LLMOrchestrator(
                    MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
                    journal_mode="PAPER")
        else:
            orch = LLMOrchestrator(
                MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
                journal_mode="PAPER")
    orch.store = TradingStore(path=db, mode="PAPER")
    orch.operations = AgentOperations(db)
    orch.run_id = orch.operations.start_run("ablation-test")
    orch.velocity = MagicMock()
    orch.velocity.submit_slow.return_value = {"status": "SUBMITTED"}
    orch._poll_velocity_results = MagicMock()
    orch.reconcile_pending_orders = MagicMock()
    orch.reconcile_open_positions = MagicMock()
    orch._monitor_owned_positions_tick = MagicMock()
    orch._run_maintenance_prefix = MagicMock()
    orch._run_schedules_bounded = MagicMock()
    orch._completed_candle_candidate = MagicMock(
        return_value="2026-10-02T01:00:00+00:00")
    orch.data_provider = MagicMock()
    orch.data_provider.api_manager = MagicMock()
    orch.data_provider.api_manager.is_holiday.return_value = False
    # The cycle's performance panel formats these BEFORE the LLM reservation
    # point; stub them so full-flow ablation tests survive the tail.
    orch.perf.history = []
    orch.perf.calc_rolling_sharpe = lambda window=20: 0.0
    orch.perf.calc_rolling_sortino = lambda window=20: 0.0
    return orch


def _events(orch, event_type):
    return list(orch.operations._con().execute(
        "SELECT payload FROM operational_events WHERE event_type=?",
        (event_type,)))


def test_modes_parse_from_env_drop_unknown(tmp_path):
    orch = _orchestrator(tmp_path, "NO_LLM,bogus_mode,no_news")
    assert orch.ablation_modes == ("no_llm", "no_news")

    plain = _orchestrator(tmp_path)
    assert plain.ablation_modes == ()


def test_no_llm_holds_before_reservation(tmp_path):
    orch = _orchestrator(tmp_path, "no_llm")
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    orch.data_provider.fetch_data.return_value = {
        "news": [], "ohlc": [], "indicators": {}, "macro_data": {}}

    decision, vol = orch.execute_cycle(
        "EURUSD", "M15", 1, 1, paper_mode=True, strategy="scalp")

    assert decision["action"] == "HOLD"
    assert "ablation no_llm" in decision["reasoning"]
    assert vol == 0.0

    # The candle task was settled by the ablation branch itself.
    tasks = list(orch.operations._con().execute(
        "SELECT status, result FROM agent_tasks WHERE task_type='analyse_completed_candle'"))
    assert tasks and tasks[0]["status"] == "COMPLETED"
    assert "ablation no_llm" in (tasks[0]["result"] or "")

    # No provider interaction: zero llm_requests rows in the window.
    llm_rows = list(orch.operations._con().execute("SELECT * FROM llm_requests"))
    assert llm_rows == []
    assert _events(orch, "ablation_news_gate_bypassed") == []

    # Durable boot marker exists for the report engine.
    assert _events(orch, "ablation_session_modes")


def test_no_news_bypasses_gate_evaluation(tmp_path):
    orch = _orchestrator(tmp_path, "no_news")
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    orch.data_provider.fetch_data.return_value = {
        "news": [], "ohlc": [], "indicators": {}}
    # Even a gate that WOULD block must never be consulted in no_news mode.
    # Downstream of the news stage the cycle may end however it ends on the
    # stub payload — only the bypass contract is under test here.
    with patch("core.news_trading.NewsTradingGate") as gate_cls:
        gate_cls.return_value.evaluate.return_value = MagicMock(
            allowed=False, phase="pre_spike_blackout", block_reason="would block")
        try:
            orch.execute_cycle("EURUSD", "M15", 1, 1, paper_mode=True,
                               strategy="scalp")
        except Exception:
            pass

    # The orchestrator's gate path must show the bypass event and NEVER its
    # own block event. (The patched class is also constructed internally by
    # the news-context builder, so class-level call counts overreach.)
    assert _events(orch, "ablation_news_gate_bypassed")
    blocked = list(orch.operations._con().execute(
        "SELECT payload FROM operational_events "
        "WHERE event_type='blocked' AND actor='news_gate'"))
    assert blocked == []


def test_no_macro_strips_macro_context(tmp_path):
    orch = _orchestrator(tmp_path, "no_macro")
    toon = {"macro_data": {"dxy": 103.2}, "zero_lag_macro": {"nowcast": 0.4},
            "ohlc": [], "indicators": {}}
    orch._apply_ablation_context_strips(toon)
    assert "macro_data" not in toon and "zero_lag_macro" not in toon
    stripped = _events(orch, "ablation_macro_context_stripped")
    assert stripped and "macro_data" in stripped[0]["payload"]

    plain = _orchestrator(tmp_path)
    toon2 = {"macro_data": {"dxy": 103.2}, "ohlc": []}
    plain._apply_ablation_context_strips(toon2)
    assert toon2["macro_data"] == {"dxy": 103.2}
