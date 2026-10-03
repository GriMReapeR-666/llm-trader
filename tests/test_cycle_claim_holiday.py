"""Regression: claimed candle tasks must settle on holiday / fetch failure."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_operations import AgentOperations
from mt5_trader import LLMOrchestrator
from trading_safety import TradingStore


def _orchestrator(tmp_path):
    risk = MagicMock()
    risk.risk_per_trade = 0.01
    risk.open_positions = []
    db = str(tmp_path / "cycle.db")
    with patch("mt5_trader.TradingStore", lambda **kwargs: TradingStore(path=db, **{k: v for k, v in kwargs.items() if k != "path"})):
        orch = LLMOrchestrator(
            MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
            journal_mode="PAPER",
        )
    # Ensure ops share the temp journal even if init rewired defaults.
    orch.store = TradingStore(path=db, mode="PAPER")
    orch.operations = AgentOperations(db)
    orch.run_id = orch.operations.start_run("test")
    orch.velocity = MagicMock()
    orch.velocity.submit_slow.return_value = {"status": "SUBMITTED"}
    orch._poll_velocity_results = MagicMock()
    orch.reconcile_pending_orders = MagicMock()
    orch.reconcile_open_positions = MagicMock()
    orch._monitor_owned_positions_tick = MagicMock()
    orch._process_due_schedules = MagicMock()
    orch._completed_candle_candidate = MagicMock(return_value="2026-08-12T12:00:00+00:00")
    orch.data_provider = MagicMock()
    orch.data_provider.api_manager = MagicMock()
    return orch


def test_holiday_completes_claimed_candle_task(tmp_path):
    orch = _orchestrator(tmp_path)
    orch.data_provider.api_manager.is_holiday.return_value = True

    decision, vol = orch.execute_cycle(
        "EURUSD", "M15", 1, 1, paper_mode=True, strategy="scalp",
    )
    assert decision["action"] == "HOLD"
    assert "Holiday" in decision["reasoning"]
    assert vol == 0.0
    rows = list(orch.operations._con().execute(
        "SELECT status FROM agent_tasks WHERE task_type='analyse_completed_candle'"
    ))
    # Holiday must SETTLE the claimed lease — never leave it stuck RUNNING.
    # Current doctrine: release the lease so the candle is re-claimed and
    # evaluated properly once the market re-opens.
    assert all(r["status"] != "RUNNING" for r in rows)
    released = list(orch.operations._con().execute(
        "SELECT event_type, correlation_id FROM operational_events WHERE event_type='task_released'"
    ))
    assert released


def test_fetch_data_failure_fails_claimed_task(tmp_path):
    orch = _orchestrator(tmp_path)
    orch.data_provider.api_manager.is_holiday.return_value = False
    orch.data_provider.fetch_data.side_effect = RuntimeError("broker bars unavailable")

    try:
        orch.execute_cycle(
            "EURUSD", "M15", 1, 1, paper_mode=True, strategy="scalp",
        )
        assert False, "expected fetch failure to raise"
    except RuntimeError as exc:
        assert "broker bars unavailable" in str(exc)

    rows = list(orch.operations._con().execute(
        "SELECT status, error FROM agent_tasks WHERE task_type='analyse_completed_candle'"
    ))
    assert rows
    assert rows[0]["status"] == "ERROR"
    assert "fetch_data failed" in (rows[0]["error"] or "")


def test_news_blackout_releases_claim_for_post_gate_analysis(tmp_path):
    """A news blackout must not consume the candle's analysis trigger.

    Session-8 wart: the news-gate block path settled the claimed candle task
    as COMPLETED, so a candle whose window overlapped a blackout was marked
    analyzed without any LLM attempt and never re-analyzed after the gate
    lifted. Doctrine (same as the holiday gate): a blackout is a transient
    market state — RELEASE the lease so the next cycle re-claims the candle
    and it reaches the LLM decision stage once the blackout ends.
    """
    orch = _orchestrator(tmp_path)
    orch.data_provider.api_manager.is_holiday.return_value = False
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    orch.data_provider.fetch_data.return_value = {"news": [], "ohlc": [], "indicators": {}}

    with patch("core.news_trading.NewsTradingGate") as _gate_cls:
        _gate_cls.return_value.evaluate.return_value = MagicMock(
            allowed=False, phase="pre_spike_blackout",
            block_reason="Pre-spike blackout: ISM Manufacturing PMI (test)")
        decision, vol = orch.execute_cycle(
            "EURUSD", "M15", 1, 1, paper_mode=True, strategy="scalp",
        )

    assert decision["action"] == "HOLD"
    assert "News trading gate" in decision["reasoning"]
    assert vol == 0.0

    # The claim must be settled (never stuck RUNNING). Release DELETES the
    # RUNNING row, so the table may be empty — the durable evidence of the
    # release is the task_released event, and the proof the trigger was not
    # consumed is the successful re-claim below.
    rows = list(orch.operations._con().execute(
        "SELECT status FROM agent_tasks WHERE task_type='analyse_completed_candle'"
    ))
    assert all(r["status"] != "RUNNING" for r in rows)
    released = list(orch.operations._con().execute(
        "SELECT event_type, correlation_id FROM operational_events "
        "WHERE event_type='task_released'"
    ))
    assert released

    # Post-blackout: the same candle is re-claimable for a real decision pass.
    candle = orch._completed_candle_candidate.return_value
    assert orch.operations.claim_task(
        f"scalp:EURUSD:M15:{candle}",
        "scalp", "analyse_completed_candle", {}, orch.run_id,
    ) is True
