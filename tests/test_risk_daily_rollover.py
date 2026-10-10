"""Daily-anchor rollover must be stamped in the same clock it is compared in.

`reset_daily_limits()` used `datetime.now()` (host local) while `__init__`
compared against `datetime.now(timezone.utc)`. On a UTC+5:30 host a restart
during the UTC evening stamped TOMORROW's date, so the next UTC midnight found
`state_date == current_date` and skipped the reset - leaving the 3% daily-loss
breaker anchored ~24h stale.

Every case below pins a UTC instant deliberately chosen so that its LOCAL
calendar date is already the next day, which is what makes the two clocks
disagree. Without that the bug is invisible (and only shows up for ~5.5h a day).
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

import core.risk_manager as rm_mod  # noqa: E402
from core.risk_manager import RiskManager  # noqa: E402

# 20:00 UTC is already 01:30 next-day on this host (UTC+5:30).
UTC_EVENING = datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc)
NEXT_UTC_EVENING = UTC_EVENING + timedelta(days=1)


@pytest.fixture
def frozen_clock(monkeypatch):
    """Pin core.risk_manager's datetime to a chosen instant (UTC + local views)."""
    def _freeze(instant: datetime):
        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return instant.astimezone().replace(tzinfo=None)
                return instant.astimezone(tz)

        monkeypatch.setattr(rm_mod, "datetime", _Frozen)
    return _freeze


def _state(date: str, **overrides) -> dict:
    state = {
        "start_equity": 10000.0, "weekly_start_equity": 10000.0,
        "weekly_recovered_r": 0.0, "weekly_circuit_step_down": False,
        "date": date, "week": "2026-W24", "trade_timestamps": [],
        "last_trade_time": {}, "peak_equity": 10000.0, "consecutive_losses": 0,
        "consecutive_wins": 0, "trade_r_history": [], "permanent_halt": False,
        "initial_balance": 10000.0, "flatten_reason": None,
    }
    state.update(overrides)
    return state


def _write(tmp_path, payload: dict) -> str:
    path = tmp_path / "risk_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_frozen_instant_really_disagrees_between_clocks():
    """Guard the guard: if local and UTC agreed, every test below is vacuous."""
    assert UTC_EVENING.astimezone().date() != UTC_EVENING.date()
    assert UTC_EVENING.astimezone().date() == (UTC_EVENING + timedelta(days=1)).date()


class TestResetStampsUtc:
    def test_reset_daily_limits_stamps_the_utc_date(self, tmp_path, frozen_clock):
        frozen_clock(UTC_EVENING)
        rm = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-14")))
        rm.reset_daily_limits()
        assert rm.state_date == "2026-06-14"          # UTC, not local "2026-06-15"
        assert rm.state_date != UTC_EVENING.astimezone().date().isoformat()

    def test_loaded_and_compared_dates_are_the_same_clock(self, tmp_path, frozen_clock):
        """The invariant: after construction state_date == current_date."""
        frozen_clock(UTC_EVENING)
        rm = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-13")))
        assert rm.state_date == rm.current_date == "2026-06-14"


class TestNoStaleDailyAnchor:
    def test_utc_evening_restart_does_not_swallow_the_next_utc_rollover(
            self, tmp_path, frozen_clock):
        """The ~24h stale-anchor bug, end to end.

        Restart #1 stamps the UTC day; restart #2 a full UTC day later must
        still roll the daily anchor. Under the local stamp, restart #1 wrote
        tomorrow's date and restart #2 saw an equal date and skipped the reset.
        """
        frozen_clock(UTC_EVENING)
        first = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-13")))
        assert first.state_date == "2026-06-14"

        frozen_clock(NEXT_UTC_EVENING)
        second = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state(first.state_date, start_equity=5555.0)))
        assert second.state_date == "2026-06-15"
        # The stale anchor was replaced, not carried forward.
        assert second.daily_start_equity == 10000.0
        assert second.daily_start_equity != 5555.0

    def test_same_utc_day_restart_keeps_the_anchor(self, tmp_path, frozen_clock):
        """A restart later in the same UTC day must NOT re-anchor.

        Re-anchoring mid-day would silently forgive the day's losses and reset
        the daily-loss breaker's denominator.
        """
        frozen_clock(NEXT_UTC_EVENING)
        rm = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-15", start_equity=9000.0)))
        assert rm.daily_start_equity == 9000.0
        assert rm.state_date == "2026-06-15"


class TestSetStoreUsesTheSameClock:
    def test_set_store_compares_in_utc(self, tmp_path, frozen_clock):
        """set_store() had its own local-time compare site.

        A local compare here would fire a spurious mid-day reset (forgiving the
        day's losses) or skip a real rollover.
        """
        frozen_clock(UTC_EVENING)                      # UTC day is 2026-06-14
        rm = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-14", start_equity=9000.0)))
        assert rm.daily_start_equity == 9000.0
        rm.set_store(store=object())
        # Same UTC day -> no reset, anchor preserved.
        assert rm.daily_start_equity == 9000.0
        assert rm.state_date == "2026-06-14"

    def test_set_store_rolls_over_on_a_new_utc_day(self, tmp_path, frozen_clock):
        frozen_clock(NEXT_UTC_EVENING)
        rm = RiskManager(balance=10000.0, state_file=_write(
            tmp_path, _state("2026-06-14", start_equity=9000.0)))
        rm.state_date = "2026-06-14"
        rm.set_store(store=object())
        assert rm.state_date == "2026-06-15"
        assert rm.daily_start_equity == 10000.0
