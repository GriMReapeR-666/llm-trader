"""Equity must be live, not a frozen constructor snapshot.

``RiskManager.balance`` is a plain attribute written in exactly two places: the
constructor and ``update_account``. Its only production caller is
``data/market_data_provider.py``, and that call is a no-op when the terminal is
absent - both source lines fall back to ``risk.balance`` and then write it back.
So on a paper session the seed balance was frozen for the whole process: sizing
never compounded as the simulated account won or lost, and the drawdown anchor
never moved.

``current_equity()`` fixes the precedence in one place -
MT5 (live/demo) -> injected venue provider (paper) -> last known snapshot -
and the previously duplicated ``_account_equity() or self.balance`` pairs now
route through it.

Hermetic: MT5 is stubbed, no terminal or broker is required.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.risk_manager as rm_mod  # noqa: E402
from core.risk_manager import RiskManager  # noqa: E402

SEED = 10000.0


@pytest.fixture
def no_mt5(monkeypatch):
    """Simulate a disconnected/unauthenticated terminal (the paper case)."""
    monkeypatch.setattr(rm_mod, "mt5", None, raising=False)


@pytest.fixture
def fake_mt5(monkeypatch):
    """A connected terminal reporting a fixed equity."""
    class _Acc:
        def __init__(self, equity):
            self.equity = equity
            self.balance = equity

    class _MT5:
        equity = 25000.0

        @staticmethod
        def account_info():
            return _Acc(_MT5.equity)

    monkeypatch.setattr(rm_mod, "mt5", _MT5, raising=False)
    return _MT5


def _rm(tmp_path, **kw):
    return RiskManager(balance=SEED, state_file=str(tmp_path / "r.json"), **kw)


class TestPrecedence:
    def test_mt5_wins_over_everything(self, tmp_path, fake_mt5):
        rm = _rm(tmp_path)
        rm.set_equity_provider(lambda: 999.0)
        assert rm.current_equity() == pytest.approx(25000.0)

    def test_paper_provider_used_when_terminal_absent(self, tmp_path, no_mt5):
        rm = _rm(tmp_path)
        rm.set_equity_provider(lambda: 12345.0)
        assert rm.current_equity() == pytest.approx(12345.0)

    def test_snapshot_is_the_last_resort(self, tmp_path, no_mt5):
        rm = _rm(tmp_path)
        assert rm.current_equity() == pytest.approx(SEED)


class TestFailClosed:
    def test_no_provider_and_no_terminal_returns_seed_not_none(self, tmp_path, no_mt5):
        rm = _rm(tmp_path)
        assert rm.current_equity() == pytest.approx(SEED)

    def test_provider_returning_none_falls_through(self, tmp_path, no_mt5):
        rm = _rm(tmp_path)
        rm.set_equity_provider(lambda: None)
        assert rm.current_equity() == pytest.approx(SEED)

    @pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
    def test_nonsense_provider_values_are_rejected(self, tmp_path, no_mt5, bad):
        rm = _rm(tmp_path)
        rm.set_equity_provider(lambda: bad)
        assert rm.current_equity() == pytest.approx(SEED)

    def test_provider_raising_does_not_propagate(self, tmp_path, no_mt5):
        rm = _rm(tmp_path)

        def _boom():
            raise RuntimeError("venue exploded")

        rm.set_equity_provider(_boom)
        assert rm.current_equity() == pytest.approx(SEED)


class TestSizingActuallyFollowsEquity:
    def test_r_amount_tracks_the_paper_venue_not_the_seed(self, tmp_path, no_mt5):
        """The point of the fix: compounding, not a frozen seed."""
        rm = _rm(tmp_path)
        venue_equity = {"value": SEED}
        rm.set_equity_provider(lambda: venue_equity["value"])

        first = rm.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=1.0)
        assert first and first > 0

        venue_equity["value"] = SEED * 2
        second = rm.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=1.0)

        assert second and second > first, (
            "position size did not follow the venue's equity: "
            f"{first} -> {second} while the paper equity doubled"
        )


class TestBreakersUnaffected:
    def test_drawdown_breaker_still_uses_live_equity(self, tmp_path, fake_mt5):
        rm = _rm(tmp_path)
        # Terminal reports 25000 while the seed peak was 10000.
        assert rm.check_max_drawdown() is True
        assert rm.peak_equity == pytest.approx(25000.0)

    def test_real_drawdown_from_venue_equity_still_trips(self, tmp_path, monkeypatch):
        class _Acc:
            equity = 8000.0
            balance = 8000.0

        class _MT5:
            @staticmethod
            def account_info():
                return _Acc()

        monkeypatch.setattr(rm_mod, "mt5", _MT5, raising=False)
        rm = _rm(tmp_path)
        rm.peak_equity = 10000.0
        assert rm.check_max_drawdown() is False
        assert rm.pending_flatten_reason() is not None


class TestSingleSourceOfPrecedence:
    def test_no_duplicated_live_or_snapshot_pairs_remain(self):
        """The precedence rule must exist in exactly one place.

        A reintroduced ``_account_equity() or self.balance`` would silently opt
        that call site out of the paper provider.
        """
        source = open(os.path.join(ROOT, "core", "risk_manager.py"),
                      encoding="utf-8").read()
        assert "_account_equity() or self.balance" not in source
        # _account_equity stays as the MT5 primitive; current_equity is the policy.
        assert source.count("def current_equity(") == 1
        assert source.count("def _account_equity(") == 1