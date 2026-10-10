"""The Level-3 drawdown breaker must stay honest in both directions.

Two failures this pins:

1. A foreign-scale ``peak_equity`` (audit finding: $100k recorded against a $10k
   account) made peak-to-trough compute as 90%, tripping EMERGENCY CASH
   LIQUIDATION on every single cycle. The repair at load re-anchors it; this
   asserts the phantom trip is actually gone.

2. The repair must not have been "fixed" by loosening anything. A breaker that
   stops firing on real drawdowns is worse than the bug, so each genuine
   drawdown below is asserted to still trip it.

Note the polarity, which is the opposite of what the method name suggests:
``check_max_drawdown`` returns True when trading is ALLOWED and False when the
breaker has tripped and positions must be flattened.

Hermetic: every case writes its own tmp state file, so the live
``data/state/risk_state.json`` is never touched.
"""
from __future__ import annotations

import json
import os

import pytest

from core.risk_manager import RiskManager

INITIAL = 10000.0


def _state(tmp_path, **overrides) -> str:
    state = {
        "start_equity": INITIAL, "weekly_start_equity": INITIAL,
        "weekly_recovered_r": 0.0, "weekly_circuit_step_down": False,
        "date": "2026-10-10", "week": "2026-W41", "trade_timestamps": [],
        "last_trade_time": {}, "peak_equity": INITIAL, "consecutive_losses": 0,
        "consecutive_wins": 0, "trade_r_history": [], "permanent_halt": False,
        "initial_balance": INITIAL, "flatten_reason": None,
    }
    state.update(overrides)
    path = tmp_path / "risk_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return str(path)


def test_configured_threshold_is_ten_percent_not_fifteen():
    """The docstring claimed 15%; the constructor default is 10%.

    Anyone reasoning about how much drawdown the account can absorb reads this
    number, so it is asserted rather than trusted.
    """
    assert RiskManager(balance=INITIAL, state_file=None).max_drawdown == pytest.approx(0.10)


class TestPhantomLiquidationIsDefused:
    def test_foreign_scale_peak_no_longer_trips_level3(self, tmp_path):
        """The exact audited artifact: $100k peak on a $10k account."""
        rm = RiskManager(balance=INITIAL, state_file=_state(
            tmp_path, peak_equity=100000.0, weekly_start_equity=100000.0,
            start_equity=INITIAL, initial_balance=INITIAL))

        # Repair happened at load.
        assert rm.peak_equity == pytest.approx(INITIAL)

        allowed = rm.check_max_drawdown(current_equity=INITIAL)
        assert allowed is True, "phantom emergency liquidation still fires"
        assert rm.pending_flatten_reason() is None

    def test_repair_does_not_fabricate_a_flatten(self, tmp_path):
        """``pending_flatten_reason`` really closes positions, so the repair
        must never invent one - it would liquidate a healthy book."""
        rm = RiskManager(balance=INITIAL, state_file=_state(
            tmp_path, peak_equity=100000.0, weekly_start_equity=100000.0,
            initial_balance=INITIAL))
        assert rm.pending_flatten_reason() is None
        assert rm._flatten_reason is None


class TestBreakerNotWeakened:
    @pytest.mark.parametrize("equity", [9000.0, 8000.0, 5000.0])
    def test_real_drawdowns_still_trip(self, tmp_path, equity):
        rm = RiskManager(balance=INITIAL, state_file=_state(tmp_path, peak_equity=INITIAL))
        allowed = rm.check_max_drawdown(current_equity=equity)
        assert allowed is False, "a real drawdown no longer trips the breaker"
        assert rm.pending_flatten_reason() is not None

    def test_drawdown_below_threshold_does_not_trip(self, tmp_path):
        """Guards the opposite failure: an over-eager breaker."""
        rm = RiskManager(balance=INITIAL, state_file=_state(tmp_path, peak_equity=INITIAL))
        assert rm.check_max_drawdown(current_equity=9500.0) is True
        assert rm.pending_flatten_reason() is None

    def test_legitimately_earned_peak_is_preserved_and_armed(self, tmp_path):
        """An account that genuinely grew must keep its high-water mark, and a
        real drawdown from it must still liquidate."""
        rm = RiskManager(
            balance=30000.0,
            state_file=_state(tmp_path, start_equity=30000.0,
                              weekly_start_equity=30000.0, peak_equity=30000.0,
                              initial_balance=30000.0),
        )
        assert rm.peak_equity == pytest.approx(30000.0)

        allowed = rm.check_max_drawdown(current_equity=12000.0)   # -60%
        assert allowed is False
        assert rm.pending_flatten_reason() is not None

    def test_repaired_peak_is_monotonic_as_equity_rises(self, tmp_path):
        """peak_equity tracks new highs; it must not stay pinned at the repair."""
        rm = RiskManager(balance=INITIAL, state_file=_state(
            tmp_path, peak_equity=100000.0, weekly_start_equity=100000.0,
            initial_balance=INITIAL))
        assert rm.peak_equity == pytest.approx(INITIAL)

        rm.check_max_drawdown(current_equity=15000.0)
        assert rm.peak_equity == pytest.approx(15000.0)
        assert rm.check_max_drawdown(current_equity=14500.0) is True  # only -3.3%