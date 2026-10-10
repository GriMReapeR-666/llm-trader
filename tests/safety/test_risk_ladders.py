"""Pin RiskManager ladders and breakers.

Mutation scores before this file: drawdown multiplier 14 %, streak 16 %,
record_trade_outcome 18 %, weekly breaker 20 %, check_drawdown and
check_active_trade_limit 0 %.
"""
from datetime import datetime, timezone

import pytest

from core.risk_manager import RiskManager
from tests.safety._clock import freeze

NAN = float("nan")


@pytest.fixture
def rm(tmp_path):
    return RiskManager(balance=10_000, state_file=str(tmp_path / "risk_state.json"))


@pytest.mark.parametrize("equity,expected", [
    (10_000, 1.0), (9_501, 1.0), (9_500, 0.75), (9_001, 0.75), (9_000, 0.50),
    (8_501, 0.50), (8_500, 0.25), (8_001, 0.25), (8_000, 0.0), (5_000, 0.0),
], ids=lambda v: str(v))
def test_drawdown_multiplier_ladder(rm, equity, expected):
    rm.peak_equity = 10_000.0
    assert rm.get_drawdown_multiplier(equity) == expected


@pytest.mark.parametrize("equity", [NAN, 0.0, -100.0], ids=["nan", "zero", "negative"])
def test_drawdown_multiplier_fails_closed_on_invalid_equity(rm, equity):
    rm.peak_equity = 10_000.0
    assert rm.get_drawdown_multiplier(equity) == 0.0


@pytest.mark.parametrize("losses,wins,expected", [
    (0, 0, 1.0), (1, 0, 1.0), (2, 0, 0.5), (3, 0, 0.25), (4, 0, 0.125), (5, 0, 0.0), (9, 0, 0.0),
    (0, 1, 1.0), (0, 2, 1.2), (0, 3, 1.4), (0, 5, 1.8), (0, 9, 1.8),
], ids=lambda v: str(v))
def test_streak_multiplier_table(rm, losses, wins, expected):
    rm.consecutive_losses, rm.consecutive_wins = losses, wins
    assert rm.get_streak_multiplier() == pytest.approx(expected)


def test_permanent_halt_zeroes_streak_multiplier(rm):
    rm.permanent_halt = True
    assert rm.get_streak_multiplier() == 0.0


def test_record_trade_outcome_counters(rm):
    rm.record_trade_outcome(-50)
    rm.record_trade_outcome(-50)
    assert (rm.consecutive_losses, rm.consecutive_wins) == (2, 0)
    rm.record_trade_outcome(0.0)          # breakeven: loss streak kept
    assert (rm.consecutive_losses, rm.consecutive_wins) == (2, 0)
    rm.record_trade_outcome(100)
    assert (rm.consecutive_losses, rm.consecutive_wins) == (0, 1)
    rm.record_trade_outcome(100)
    assert rm.consecutive_wins == 2
    rm.record_trade_outcome(0.0)
    # Breakeven neither extends nor resets the win streak. (The comment in
    # record_trade_outcome says it "ends a winning run"; the code keeps it.)
    assert rm.consecutive_wins == 2
    for bad in (NAN, float("inf"), "x", None):
        before = (rm.consecutive_losses, rm.consecutive_wins)
        rm.record_trade_outcome(bad)
        assert (rm.consecutive_losses, rm.consecutive_wins) == before


@pytest.mark.parametrize("equity,ok", [(9_701, True), (9_700, False), (9_000, False)], ids=["below", "at", "above"])
def test_daily_loss_breaker_boundary(rm, equity, ok):
    rm.daily_start_equity = 10_000.0
    assert rm.check_daily_drawdown(equity) is ok
    assert (rm.pending_flatten_reason() is None) is ok


@pytest.mark.parametrize("start,equity", [(0.0, 9_000.0), (-1.0, 9_000.0), (10_000.0, NAN)],
                         ids=["zero_baseline", "negative_baseline", "nan_equity"])
def test_daily_loss_breaker_fails_closed_on_invalid_inputs(rm, start, equity):
    rm.daily_start_equity = start
    assert rm.check_daily_drawdown(equity) is False


@pytest.mark.parametrize("equity,step_down", [(9_401, False), (9_400, True)], ids=["below", "at"])
def test_weekly_breaker_halves_size(rm, equity, step_down):
    rm.weekly_start_equity = 10_000.0
    rm.check_weekly_circuit_breaker(equity)
    assert rm.weekly_circuit_step_down is step_down
    assert rm.get_circuit_breaker_size_multiplier() == (0.5 if step_down else 1.0)


def test_weekly_breaker_clears_after_two_r_recovered(rm):
    rm.weekly_start_equity = rm.daily_start_equity = 10_000.0
    rm.check_weekly_circuit_breaker(9_400)
    rm.record_trade_outcome(100)          # +1R (R = 1 % of 10,000)
    assert rm.weekly_circuit_step_down is True
    rm.record_trade_outcome(100)          # +2R total
    assert rm.weekly_circuit_step_down is False


@pytest.mark.parametrize("count,ok", [(0, True), (1, True), (2, False), (5, False)])
def test_active_trade_limit(rm, count, ok):
    assert rm.max_active_positions == 2
    assert rm.check_active_trade_limit(custom_count=count) is ok


@pytest.mark.parametrize("equity,ok", [(9_001, True), (9_000, False), (NAN, False), (0.0, False)],
                         ids=["below", "at", "nan", "zero"])
def test_max_drawdown_check(rm, equity, ok):
    rm.peak_equity = 10_000.0
    assert rm.check_drawdown(equity) is ok


# --------------------------------------------- P1-H1: restart resets the limit
UTC_EVENING = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)    # 01:30 IST on 8 Oct
UTC_NOON = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)       # 17:30 IST on 7 Oct


def _ist(utc):
    return (utc.replace(tzinfo=None) + (datetime(2000, 1, 1, 5, 30) - datetime(2000, 1, 1)))


def _restart_after_daily_breach(monkeypatch, tmp_path, utc_now):
    freeze(monkeypatch, "core/risk_manager.py", utc_now, _ist(utc_now))
    state = str(tmp_path / "risk_state.json")
    first = RiskManager(balance=10_000, state_file=state)
    first.daily_start_equity = 10_000.0
    assert first.check_daily_drawdown(9_600) is False      # 4 % down: breached
    first.save_state()
    restarted = RiskManager(balance=9_600, state_file=state)
    return restarted.check_daily_drawdown(9_600)


def test_restart_keeps_daily_breach_when_local_and_utc_dates_agree(monkeypatch, tmp_path):
    assert _restart_after_daily_breach(monkeypatch, tmp_path, UTC_NOON) is False


def test_restart_keeps_daily_breach_across_the_utc_local_date_gap(monkeypatch, tmp_path):
    """P1-H1 fixed: the daily breaker is anchored on the broker trading day,
    so a restart across the UTC/local midnight gap no longer resets it."""
    assert _restart_after_daily_breach(monkeypatch, tmp_path, UTC_EVENING) is False
