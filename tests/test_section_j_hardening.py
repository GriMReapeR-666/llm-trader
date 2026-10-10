import pytest

from research import sample_gate
from operations.live_rollout_gate import status as live_status, approve as live_approve, CONFIRMATION
from core.trading_safety import TradingStore
from research.broker_replay import ReplaySpec, ReplayIntent, replay


def test_sample_gate_qualification_and_rejection():
    # 1. Insufficient sample rows (< 100)
    small_rows = [
        {"strategy": "trend_v1", "symbol": "EURUSD", "setup_id": "trend_pullback_v1",
         "features": {"regime": "TRENDING_UP"}, "closed_at": "2026-08-01T12:00:00Z"}
        for _ in range(30)
    ]
    res_small = sample_gate.evaluate(small_rows, minimum_samples=100, minimum_regimes=2)
    assert res_small["execution_authority"] is False
    assert res_small["status"] == "INSUFFICIENT_DATA"
    assert len(res_small["setup_classes"]) == 1
    assert res_small["setup_classes"][0]["eligible"] is False

    # 2. Sufficient sample rows (120 rows across 2 regimes)
    large_rows = [
        {"strategy": "trend_v1", "symbol": "EURUSD", "setup_id": "trend_pullback_v1",
         "features": {"regime": "TRENDING_UP" if i % 2 == 0 else "RANGING"}, "closed_at": "2026-08-01T12:00:00Z"}
        for i in range(120)
    ]
    res_large = sample_gate.evaluate(large_rows, minimum_samples=100, minimum_regimes=2)
    assert res_large["execution_authority"] is False
    assert res_large["status"] == "READY_FOR_RESEARCH"
    assert res_large["setup_classes"][0]["eligible"] is True


def test_live_rollout_gate_approval(tmp_path):
    db_file = str(tmp_path / "state.db")

    # Initially unapproved
    initial = live_status(db_file)
    assert initial["approved"] is False
    assert initial["active"] is False

    # Record valid operator tiny-live approval
    evidence_file = str(tmp_path / "evidence.txt")
    with open(evidence_file, "w") as f:
        f.write("Backtest and forward demo test evidence manifest\n")

    rec = live_approve(
        db_file,
        operator="operator_john",
        note="Tiny live rollout authorization",
        evidence=[evidence_file],
        risk_fraction_cap=0.001,
        expires_hours=24.0,
        confirmation=CONFIRMATION,
    )
    assert rec["approved"] is True
    assert rec["risk_fraction_cap"] == 0.001

    # Verify persistent status state
    verified = live_status(db_file)
    assert verified["approved"] is True
    assert verified["operator"] == "operator_john"
    assert verified["active"] is True


def test_broker_replay_engine_simulation():
    spec = ReplaySpec(
        point=0.00001,
        contract_size=100000.0,
        commission_per_volume=7.0,
        swap_per_volume_day=2.0,
        expected_slippage_price=0.00002,
        spread_price=0.00010,
    )
    bars = [
        {"low": 1.0800, "high": 1.0850, "close": 1.0820, "timestamp": "2026-08-01T12:00:00Z"},
        {"low": 1.0810, "high": 1.0860, "close": 1.0855, "timestamp": "2026-08-15T12:15:00Z"},
    ]
    intents = [
        ReplayIntent(
            ticket="intent_1",
            side="BUY",
            entry=1.0815,
            stop_loss=1.0780,
            take_profit=1.0850,
            volume=0.1,
            submit_index=0,
        )
    ]
    res = replay(intents, bars, spec)
    assert isinstance(res, dict)
    assert "closed_trades" in res
    assert res["execution_authority"] is False
