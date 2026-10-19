"""MT5 lazy attach must survive a cold terminal (bounded retry with backoff).

The continuous backtest pipeline reaches the terminal through
``_ensure_mt5_attached`` (data plane, via fetch_ohlc) and through
``fetch_broker_specs`` (broker specs, step 1 of
``run_full_autonomous_backtest``).  A terminal that is still booting when the
pipeline starts refuses the FIRST bare ``initialize()`` and accepts it a
second later; both call sites used to be single-shot, so one transient miss
failed the whole validation run closed (or silently degraded the friction
model to DEFAULT_CONFIG).  These tests pin the retry contract: transient
False/None outcomes are retried on the ``_ATTACH_BACKOFF_S`` ladder, raising
call sites and initialize-less fakes still fail fast, and the attempt count
is bounded so a genuinely absent terminal cannot stall boot.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data.market_data_provider as mdp  # noqa: E402


class _FakeTerminal:
    """Terminal that comes up after ``boot_attempts`` failed initialize()s."""

    def __init__(self, boot_attempts: int = 0, raise_on_init: bool = False):
        self.boot_attempts = boot_attempts
        self.raise_on_init = raise_on_init
        self.init_calls = 0
        self._terminal = None

    def terminal_info(self):
        return self._terminal

    def initialize(self):
        self.init_calls += 1
        if self.raise_on_init:
            raise RuntimeError("initialize exploded")
        if self.init_calls > self.boot_attempts:
            self._terminal = SimpleNamespace()
            return True
        return False


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff sleeps instead of paying them."""
    slept = []
    monkeypatch.setattr(mdp, "_ATTACH_SLEEP", slept.append)
    return slept


def test_attach_retries_until_terminal_boots(no_sleep):
    """Two transient refusals then success: 3 init calls, ladder sleeps."""
    term = _FakeTerminal(boot_attempts=2)
    assert mdp._ensure_mt5_attached(term) is True
    assert term.init_calls == 3
    assert no_sleep == [0.5, 1.0]


def test_attach_succeeds_first_try_without_sleeping(no_sleep):
    term = _FakeTerminal(boot_attempts=0)
    assert mdp._ensure_mt5_attached(term) is True
    assert term.init_calls == 1
    assert no_sleep == []


def test_persistent_failure_is_bounded_and_fails_closed(no_sleep):
    """A genuinely absent terminal fails closed after exactly 3 attempts."""
    term = _FakeTerminal(boot_attempts=99)  # never comes up
    assert mdp._ensure_mt5_attached(term) is False
    assert term.init_calls == mdp._ATTACH_MAX_ATTEMPTS
    # No sleep after the final attempt: the ladder bounds the wait, not the try.
    assert no_sleep == [0.5, 1.0]


def test_no_initialize_attribute_keeps_fast_fail(no_sleep):
    """Test fakes / exotic shims without initialize(): old behavior exactly."""
    term = SimpleNamespace(terminal_info=lambda: None)
    assert mdp._ensure_mt5_attached(term) is False
    assert no_sleep == []


def test_raising_initialize_fails_fast_without_retry(no_sleep):
    """A raising initialize() is a broken shim, not a booting terminal."""
    term = _FakeTerminal(raise_on_init=True)
    assert mdp._ensure_mt5_attached(term) is False
    assert term.init_calls == 1
    assert no_sleep == []


def test_already_attached_never_calls_initialize(no_sleep):
    term = SimpleNamespace(terminal_info=lambda: SimpleNamespace())
    assert mdp._ensure_mt5_attached(term) is True
    assert no_sleep == []


def test_engine_broker_specs_uses_retried_attach(monkeypatch, no_sleep):
    """fetch_broker_specs must go through the retried attach, not a one-shot.

    The fake terminal refuses initialize() once (cold boot) and comes up on
    the retry — with the old single-shot call the specs would have silently
    degraded to DEFAULT_CONFIG.
    """
    from research.mt5_backtest_engine import MT5BrokerBacktestEngine

    term = _FakeTerminal(boot_attempts=1)
    fake_mt5 = SimpleNamespace(
        terminal_info=lambda: term.terminal_info(),
        initialize=term.initialize,
        symbol_info=lambda s: SimpleNamespace(
            point=0.00001, digits=5, trade_contract_size=100000.0,
            spread=12, trade_tick_size=0.00001, trade_tick_value=1.0,
            swap_long=-0.7, swap_short=0.2, swap_rollover3days=3,
            margin_initial=0.0, volume_min=0.01, volume_max=100.0,
            volume_step=0.01),
        account_info=lambda: SimpleNamespace(
            balance=5000.0, equity=5100.0, leverage=200, currency="USD"),
    )
    monkeypatch.setattr(mdp, "_get_mt5", lambda: fake_mt5)

    engine = MT5BrokerBacktestEngine()
    specs = engine.fetch_broker_specs("EURUSD")

    assert specs["connected"] is True
    assert specs["source"] == "MT5_LIVE_TERMINAL"
    assert specs["balance"] == 5000.0
    assert term.init_calls == 2  # refused once, retried, attached
