"""A read-only RiskManager must never rewrite the live risk state.

``RiskManager.__init__`` calls ``load_state()`` and then, on a day/week rollover,
``reset_daily_limits()`` -> ``save_state()``. Because ``state_file`` defaults to
the RELATIVE ``"risk_state.json"``, and ``core.state_persistence`` resolves any
relative path against ``data/state/``, a bare ``RiskManager(balance=...)`` both
READS and WRITES the live circuit-breaker anchors.

``core/trading_tools.py`` did exactly that inside an LLM-invocable tool: it built
``RiskManager(balance=float(equity or 10000), ...)`` to ask a read-only portfolio
heat question, and on rollover it stamped its $10k paper default over the real
account. The audit found the result on disk: ``start_equity=10000`` sitting next
to ``weekly_start_equity=100000``, which disables the weekly drawdown circuit.

These tests pin the fix in both directions - ephemeral reads nothing and writes
nothing, while an explicitly-pathed instance still persists normally.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.risk_manager import RiskManager  # noqa: E402

LIVE_STATE = os.path.join(ROOT, "data", "state", "risk_state.json")


class TestEphemeralIsolation:
    def test_ephemeral_instance_never_reaches_the_persistence_transport(self, monkeypatch):
        """The exact corruption path: rollover triggers an unconditional save.

        Asserted at the transport rather than by inspecting the directory,
        because the suite's own conftest sandbox fixtures legitimately create
        backup files in tmp_path and asserting "tmp_path is empty" races them.
        If save_state() never delegates, no path can leak.
        """
        calls = []
        monkeypatch.setattr(
            RiskManager, "_save_state_dict",
            lambda self, *a, **kw: calls.append(a) or None,
            raising=False)
        monkeypatch.setattr(
            RiskManager, "_load_state_dict",
            lambda self, *a, **kw: (calls.append(a), {})[1],
            raising=False)

        rm = RiskManager(balance=10000.0, state_file=RiskManager.EPHEMERAL_STATE)
        # Force both rollover paths that save on construction, then save again.
        rm.reset_daily_limits()
        rm.reset_weekly_limits()
        rm.save_state()

        assert calls == [], "ephemeral instance touched the persistence transport"
        assert not os.path.exists(":ephemeral:")
        assert not os.path.exists(os.path.join(ROOT, "data", "state", ":ephemeral:"))

    def test_ephemeral_instance_does_not_read_shared_state(self, tmp_path):
        """A read-only probe must not inherit a stranger's breaker state either."""
        poisoned = tmp_path / "risk_state.json"
        poisoned.write_text(json.dumps({
            "start_equity": 999.0, "weekly_start_equity": 999.0,
            "peak_equity": 999.0, "date": "2026-10-10", "week": "2026-W41",
            "permanent_halt": True, "initial_balance": 999.0,
            "flatten_reason": "peak_drawdown_limit:injected",
        }), encoding="utf-8")

        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            rm = RiskManager(balance=10000.0, state_file=RiskManager.EPHEMERAL_STATE)
        finally:
            os.chdir(cwd)

        assert rm.daily_start_equity == 10000.0     # its own balance, not 999
        assert rm.peak_equity == 10000.0
        assert rm.permanent_halt is False
        assert rm.pending_flatten_reason() is None

    def test_ephemeral_keeps_working_breakers(self, tmp_path):
        """Ephemeral must not mean inert: the safety checks still function."""
        rm = RiskManager(balance=10000.0, state_file=RiskManager.EPHEMERAL_STATE)
        # Polarity: True = allowed, False = tripped.
        assert rm.check_max_drawdown(current_equity=10000.0) is True
        assert rm.check_max_drawdown(current_equity=5000.0) is False
        assert rm.pending_flatten_reason() is not None


class TestExplicitPathsStillPersist:
    """The guard must not have broken ordinary persistence."""

    def test_named_state_file_round_trips(self, tmp_path):
        path = str(tmp_path / "risk.json")
        first = RiskManager(balance=10000.0, state_file=path)
        first.consecutive_losses = 3
        first.save_state()

        second = RiskManager(balance=10000.0, state_file=path)
        assert second.consecutive_losses == 3
        assert os.path.exists(path)

    def test_ephemeral_sentinel_is_not_a_plausible_filename(self):
        """Guards the sentinel against being 'simplified' into a real path."""
        assert RiskManager.EPHEMERAL_STATE.startswith(":")
        assert os.path.sep not in RiskManager.EPHEMERAL_STATE


class TestNoBareConstructionOnTheHeatProbe:
    """Tripwire on the original defect.

    The failure was silent - the write succeeded and looked correct - so a
    behavioural test on the tool would be the only thing to catch a regression.
    Asserting the unsafe construction is simply absent is cheaper and more
    durable than re-deriving the whole heat-gate result.
    """

    def test_heat_probe_does_not_take_the_default_state_file(self):
        source = open(os.path.join(ROOT, "core", "trading_tools.py"),
                      encoding="utf-8").read()
        assert "RiskManager(balance=float(equity or 10000), risk_per_trade=0.01)" not in source, (
            "the LLM heat probe is constructing a RiskManager with the default "
            "state_file, which resolves to the live data/state/risk_state.json"
        )
        assert "RiskManager.EPHEMERAL_STATE" in source

    @pytest.mark.skipif(not os.path.exists(LIVE_STATE),
                        reason="live risk state not present in this checkout")
    def test_live_state_is_not_touched_by_an_ephemeral_probe(self):
        """Constructing an ephemeral instance must leave the real file alone."""
        with open(LIVE_STATE, encoding="utf-8") as fh:
            before = fh.read()
        RiskManager(balance=1.0, state_file=RiskManager.EPHEMERAL_STATE).save_state()
        with open(LIVE_STATE, encoding="utf-8") as fh:
            assert fh.read() == before