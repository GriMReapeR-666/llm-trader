"""P0-2: risk weekly/daily/peak anchor usurp repair tests.

Covers the audited case (weekly $100k on a $10k account) plus the safety
direction that matters more: the repair must never quietly re-baseline a real
drawdown away. Every RiskManager here is pointed at a tmp state file so no
repo runtime state is read or written.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _make_rm(balance: float, tmp_path, state_payload: dict | None = None,
             is_paper: bool | None = None):
    """Build a RiskManager whose state file lives in tmp_path."""
    from core.risk_manager import RiskManager

    state_file = tmp_path / "risk_state.json"
    if state_payload is not None:
        state_file.write_text(json.dumps(state_payload), encoding="utf-8")
    return RiskManager(balance=balance, state_file=str(state_file),
                       is_paper=is_paper), state_file


def _now():
    return datetime.now(timezone.utc)


def _state(**overrides) -> dict:
    """A well-formed, same-scale state file for a 10k account."""
    now = _now()
    base = {
        "start_equity": 10000.0,
        "weekly_start_equity": 10000.0,
        "weekly_recovered_r": 0.0,
        "weekly_circuit_step_down": False,
        "date": now.date().isoformat(),
        "week": f"{now.isocalendar()[0]}-W{now.isocalendar()[1]}",
        "trade_timestamps": [],
        "last_trade_time": {},
        "peak_equity": 10000.0,
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "trade_r_history": [],
        "permanent_halt": False,
        "initial_balance": 10000.0,
        "flatten_reason": None,
    }
    base.update(overrides)
    return base


def _records(caplog, level: str = "") -> list:
    return [r.getMessage() for r in caplog.records
            if not level or r.levelname == level]


class TestWeeklyAnchorRepair:
    def test_corrupt_100k_weekly_anchor_on_10k_account_is_re_anchored(self, tmp_path, caplog):
        """The audited corruption: weekly anchor $100k while equity is $10k.

        That is the state that silently disabled the weekly circuit breaker.
        """
        rm, state_file = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0))

        assert rm.weekly_start_equity == 10000.0
        assert rm.peak_equity == 10000.0
        assert any("Re-anchoring weekly_start_equity" in m for m in _records(caplog))

    def test_repair_is_written_back_to_disk(self, tmp_path):
        """The audit's bottom-line metric is about the FILE, not the object.

        An in-memory-only repair re-corrupts every later restart.
        """
        rm, state_file = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0))

        on_disk = json.loads(state_file.read_text(encoding="utf-8"))
        assert on_disk["weekly_start_equity"] == pytest.approx(rm.balance, rel=0.002)
        assert on_disk["peak_equity"] == pytest.approx(rm.balance, rel=0.002)

    def test_repaired_weekly_breaker_can_actually_trip(self, tmp_path):
        """-6% from the re-anchored baseline must engage the step-down."""
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0))

        rm.check_weekly_circuit_breaker(current_equity=9400.0)
        assert rm.weekly_circuit_step_down is True
        assert rm.get_circuit_breaker_size_multiplier() == 0.50

    def test_peak_repair_stops_the_false_level3_liquidation(self, tmp_path):
        """peak_equity=100k on a 10k account re-arms level 3 every cycle.

        check_max_drawdown() does peak = max(peak, equity) then compares, so a
        foreign-scale peak is a permanent emergency-cash-liquidation trigger.
        """
        corrupt, _ = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0))
        assert corrupt.check_max_drawdown(current_equity=10000.0) is True
        assert corrupt._flatten_reason is None

        healthy, _ = _make_rm(10000.0, tmp_path, _state())
        assert healthy.check_max_drawdown(current_equity=10000.0) is True


class TestRepairNeverMasksARealDrawdown:
    def test_engaged_step_down_blocks_the_weekly_re_anchor(self, tmp_path):
        """State that already shows a tripped breaker keeps its denominator.

        Re-anchoring there would reset the measured drawdown to zero and trade
        at full size straight through a real loss.
        """
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0,
            weekly_circuit_step_down=True))

        assert rm.weekly_start_equity == 100000.0
        assert rm.get_circuit_breaker_size_multiplier() == 0.50

    def test_peak_flatten_on_record_keeps_the_peak(self, tmp_path):
        """A peak-drawdown flatten proves the peak is ours, so level 3 stays armed."""
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            weekly_start_equity=100000.0, peak_equity=100000.0,
            flatten_reason="peak_drawdown_limit:88.00%>=10.00%"))

        assert rm.peak_equity == 100000.0
        assert rm.check_max_drawdown(current_equity=12000.0) is False

    def test_re_anchor_engages_step_down_when_a_real_drop_is_implied(self, tmp_path):
        """Repairing the number must not cost the protection.

        Equity well below the discarded anchor may be a genuine drawdown, so
        the size step-down is engaged rather than the loss being re-baselined.
        """
        rm, _ = _make_rm(12000.0, tmp_path, _state(
            start_equity=12000.0, weekly_start_equity=30000.0, peak_equity=30000.0))

        assert rm.weekly_start_equity == 12000.0
        assert rm.weekly_circuit_step_down is True
        assert rm.get_circuit_breaker_size_multiplier() == 0.50


class TestGuardIsNotSilent:
    def test_malformed_anchor_logs_error_and_still_loads(self, tmp_path, caplog):
        """A safety guard must not degrade to a silent no-op (audit 4.11)."""
        rm, _ = _make_rm(10000.0, tmp_path, _state(weekly_start_equity="not-a-number"))

        assert any("malformed" in m for m in _records(caplog, "ERROR"))
        # Load survives and the breakers have a usable number to work with.
        assert isinstance(rm.weekly_start_equity, float)

    def test_malformed_anchor_does_not_raise_into_the_caller(self, tmp_path):
        rm, _ = _make_rm(10000.0, tmp_path, _state(weekly_start_equity=None))
        assert rm.weekly_start_equity == 10000.0


class TestLegitimateStateIsLeftAlone:
    def test_same_scale_restart_continuity_preserved(self, tmp_path):
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            start_equity=10250.0, weekly_start_equity=10100.0, peak_equity=10300.0,
            weekly_recovered_r=1.5, consecutive_losses=2, trade_r_history=[-1.0, -1.0]))

        assert rm.weekly_start_equity == 10100.0
        assert rm.peak_equity == 10300.0
        assert rm.weekly_recovered_r == 1.5
        assert rm.consecutive_losses == 2
        assert rm.trade_r_history == [-1.0, -1.0]
        assert rm.get_circuit_breaker_size_multiplier() == 1.0

    def test_stale_period_is_handled_by_the_week_rollover(self, tmp_path):
        """A previous-week foreign anchor is reset by reset_weekly_limits()."""
        last_week = _now() - timedelta(days=7)
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            date=last_week.date().isoformat(),
            week=f"{last_week.isocalendar()[0]}-W{last_week.isocalendar()[1]}",
            weekly_start_equity=100000.0, peak_equity=100000.0))

        assert rm.weekly_start_equity <= 2.0 * rm.initial_balance

    def test_daily_anchor_repair_only_for_the_live_day(self, tmp_path, caplog):
        rm, _ = _make_rm(10000.0, tmp_path, _state(start_equity=100000.0))

        assert rm.daily_start_equity == 10000.0
        assert any("Re-anchoring daily_start_equity" in m for m in _records(caplog))
        assert rm.check_daily_drawdown(current_equity=9400.0) is False

    def test_no_state_file_defaults_to_balance(self, tmp_path):
        rm, _ = _make_rm(10000.0, tmp_path, None)
        assert rm.weekly_start_equity == 10000.0
        assert rm.daily_start_equity == 10000.0
        assert rm.peak_equity == 10000.0


class TestPaperModeScale:
    def test_demo_scale_anchors_are_repaired_against_the_paper_book(self, tmp_path, monkeypatch):
        """A data-only demo terminal must not veto paper anchor repair.

        Regression: is_paper was False in the paper profile, so _account_equity()
        returned the demo equity (~100k) while the paper seed was 10k. The repair
        guard treated the demo scale as ground truth, left the demo-scale daily
        and peak anchors in place, and the paper account stayed malformed.
        """
        import core.risk_manager as rm_mod

        class _Acc:
            equity = 100035.06
            balance = 10000.0

        class _MT5:
            @staticmethod
            def account_info():
                return _Acc()

        monkeypatch.setattr(rm_mod, "mt5", _MT5, raising=False)
        rm, _ = _make_rm(10000.0, tmp_path, _state(
            start_equity=100035.4, weekly_start_equity=10000.0,
            peak_equity=100039.85, initial_balance=100035.06), is_paper=True)

        assert rm.initial_balance == pytest.approx(10000.0)
        assert rm.daily_start_equity == pytest.approx(10000.0)
        assert rm.weekly_start_equity == pytest.approx(10000.0)
        assert rm.peak_equity == pytest.approx(10000.0)
        assert rm.weekly_circuit_step_down is False
        assert rm.current_equity() == pytest.approx(10000.0)
