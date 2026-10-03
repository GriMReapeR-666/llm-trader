"""Regression tests for the LiveHealthMonitor degradation path in
core/trading_safety.DeterministicValidator.validate().

The guard around the live-health check ended in

    except Exception as _health_exc:
        log.debug("LiveHealthMonitor skipped: %s", _health_exc)

but the module never imported `logging` and never defined `log`, so any
exception raised inside that guard raised NameError *from the handler* and
propagated out of validate(). A health check that was supposed to degrade
gracefully instead failed the whole safety validation -- and no test exercised
the path, so nothing caught it.
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "data", "core", "intelligence", "operations", "research", "ui"):
    _p = os.path.join(_ROOT, _sub) if _sub else _ROOT
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import core.trading_safety as ts


def _market():
    return {
        "confluence_score": 10,
        "changepoint_probability": 0.1,
        "spread_pips": 1.0,
        "slippage_pct": 0.05,
        "adx": 25.0,
        "equity": 10000.0,
        "balance": 10000.0,
        "daily_pnl": 0.0,
        "open_positions": [],
        "recent_executions": [],
        "equity_history": [],
        "backtest_metrics": {},
        "slippage_history": [],
        "price_velocity": 0.0,
        "correlation_heat": 0.0,
        "tail_risk_heat": 0.0,
    }


def _proposal():
    return SimpleNamespace(
        side="buy",
        entry=1.0850,
        stop_loss=1.0830,
        take_profit=1.0900,
        reward_risk=2.0,
        setup_key=lambda: "test_setup",
        strategy="causal_trend",
        features={},
    )


def _validator():
    store = MagicMock()
    store.is_kill_switch_active.return_value = False
    store.setup_seen_recently.return_value = False
    return ts.DeterministicValidator(store=store, min_rr=2.0)


def test_module_defines_a_usable_logger():
    """The handler at the end of the live-health guard calls log.debug()."""
    assert "logging" in vars(ts), "core.trading_safety must import logging"
    assert "log" in vars(ts), "core.trading_safety must define a module-level `log`"
    assert isinstance(ts.log, logging.Logger)


def test_live_health_failure_degrades_instead_of_raising(monkeypatch):
    """A raising LiveHealthMonitor must not abort the whole validator.

    This is the exact regression: the guard's own handler used to raise
    NameError, which propagated out of validate().
    """
    saved = ts._LiveHealthMonitor

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated LiveHealthMonitor failure")

    monkeypatch.setattr(ts, "_LiveHealthMonitor", _Boom)
    try:
        result = _validator().validate(_proposal(), _market())
    finally:
        monkeypatch.setattr(ts, "_LiveHealthMonitor", saved)

    assert result is not None
    assert hasattr(result, "approved")


def test_live_health_failure_never_surfaces_as_nameerror(monkeypatch):
    """Explicitly assert the failure mode that used to occur."""
    saved = ts._LiveHealthMonitor

    class _BoomRun:
        def __init__(self, *a, **k):
            pass

        def run_full_check(self):
            raise ValueError("health backend exploded")

    monkeypatch.setattr(ts, "_LiveHealthMonitor", _BoomRun)
    try:
        _validator().validate(_proposal(), _market())
    except NameError as exc:  # pragma: no cover - the regression
        pytest.fail(f"live-health handler raised NameError again: {exc}")
    finally:
        monkeypatch.setattr(ts, "_LiveHealthMonitor", saved)
