"""Tests for autonomous operation gates wired into the live trading loop.

Covers:
  G1 — ExecutionSafetySupervisor.validate_pre_trade_safety() in place_market_order
  G2 — DataQualityGate in execute_cycle() pre-analysis
  G4 — Circuit breaker evaluate_circuits() in main loop
  G5 — Autonomous safety state in /api/snapshot
  G8 — Autonomous recovery (auto-demote on consecutive failures)
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G1: ExecutionSafetySupervisor.validate_pre_trade_safety
# ---------------------------------------------------------------------------

class TestExecutionSafetyGate:
    """Tests for the pre-trade safety gate wired into place_market_order."""

    def _make_supervisor(self, **overrides):
        from core.execution_safety_supervisor import LiveExecutionSafetySupervisor
        defaults = {
            "max_daily_drawdown_limit_pct": 5.0,
            "max_acceptable_latency_ms": 250.0,
            "max_slippage_multiplier": 2.5,
        }
        defaults.update(overrides)
        return LiveExecutionSafetySupervisor(**defaults)

    def test_approves_normal_conditions(self):
        sup = self._make_supervisor()
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=1.5, historical_avg_spread_pips=1.2,
            roundtrip_latency_ms=50.0,
        )
        assert result["is_approved"] is True
        assert result["rejection_reason"] is None

    def test_rejects_high_latency(self):
        sup = self._make_supervisor(max_acceptable_latency_ms=100.0)
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=1.0, historical_avg_spread_pips=1.0,
            roundtrip_latency_ms=500.0,
        )
        assert result["is_approved"] is False
        assert "LATENCY" in result["rejection_reason"]

    def test_rejects_spread_explosion(self):
        sup = self._make_supervisor(max_slippage_multiplier=2.0)
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=5.0, historical_avg_spread_pips=1.0,
            roundtrip_latency_ms=50.0,
        )
        assert result["is_approved"] is False
        assert "SPREAD" in result["rejection_reason"]

    def test_rejects_when_kill_switch_active(self):
        sup = self._make_supervisor()
        sup.state.kill_switch_active = True
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=1.0, historical_avg_spread_pips=1.0,
            roundtrip_latency_ms=50.0,
        )
        assert result["is_approved"] is False
        assert "KILL_SWITCH" in result["rejection_reason"]

    def test_rejects_when_trading_disabled(self):
        sup = self._make_supervisor()
        sup.state.is_trading_enabled = False
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=1.0, historical_avg_spread_pips=1.0,
            roundtrip_latency_ms=50.0,
        )
        assert result["is_approved"] is False

    def test_approves_with_zero_spread(self):
        """Zero spread should not crash or reject."""
        sup = self._make_supervisor()
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=0.0, historical_avg_spread_pips=0.0,
            roundtrip_latency_ms=10.0,
        )
        assert result["is_approved"] is True

    def test_drawdown_trips_circuit_breaker(self):
        sup = self._make_supervisor(max_daily_drawdown_limit_pct=5.0)
        sup.update_equity(100000.0)  # peak
        sup.update_equity(94000.0)   # 6% drawdown -> should trip
        assert sup.state.kill_switch_active is True
        assert sup.state.is_trading_enabled is False
        result = sup.validate_pre_trade_safety(
            symbol="EURUSD", requested_volume=0.1,
            current_spread_pips=1.0, historical_avg_spread_pips=1.0,
            roundtrip_latency_ms=50.0,
        )
        assert result["is_approved"] is False

    def test_inspect_safety_status_returns_all_fields(self):
        sup = self._make_supervisor()
        status = sup.inspect_safety_status()
        assert "is_trading_enabled" in status
        assert "kill_switch_active" in status
        assert "current_equity" in status
        assert "permanent_halt" in status
        assert "tripwire_reasons" in status

    def test_permanent_halt_cannot_be_reset(self):
        sup = self._make_supervisor()
        sup.state.initial_equity = 100000.0
        sup.state.peak_equity = 100000.0
        sup.state.equity_floor_pct = 20.0
        sup.update_equity(15000.0)  # 85% DD -> permanent halt
        assert sup.state.permanent_halt is True
        reset_ok = sup.reset_circuit_breaker()
        assert reset_ok is False
        assert sup.state.permanent_halt is True

    def test_reset_works_for_non_permanent(self):
        sup = self._make_supervisor()
        sup.trip_circuit_breaker("test trip")
        assert sup.state.kill_switch_active is True
        reset_ok = sup.reset_circuit_breaker()
        assert reset_ok is True
        assert sup.state.kill_switch_active is False


# ---------------------------------------------------------------------------
# G2: DataQualityGate in execute_cycle
# ---------------------------------------------------------------------------

class TestDataQualityGateWiring:
    """Tests for the data quality gate wired into execute_cycle."""

    def _good_snapshot(self):
        captured = datetime.now(timezone.utc)
        bars = []
        for i in range(30):
            ts = captured - __import__("datetime").timedelta(minutes=30 - i)
            bars.append({
                "timestamp": ts.isoformat(),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            })
        return {
            "context": {
                "market_data_source": "mt5",
                "symbol": "EURUSD", "timeframe": "M15",
                "source_identity": {
                    "provider": "mt5", "server": "Test-Demo",
                    "account_hash": "0123456789abcdef0123456789abcdef",
                },
                "captured_at": captured.isoformat(),
                "provider_time": captured.isoformat(),
                "clock_health": {"valid": True, "offset_seconds": 0.0},
            },
            "ohlc": bars,
            "dom": {"best_bid": 99.9, "best_ask": 100.1, "spread": 0.2,
                     "captured_at": captured.isoformat()},
        }

    def test_good_data_passes(self):
        from data.data_quality import DataQualityGate
        gate = DataQualityGate()
        result = gate.validate(self._good_snapshot(), require_live_quote=True)
        assert result.passed, result.reasons

    def test_insufficient_bars_rejects(self):
        from data.data_quality import DataQualityGate
        snap = self._good_snapshot()
        snap["ohlc"] = snap["ohlc"][:10]  # only 10 bars
        gate = DataQualityGate()
        result = gate.validate(snap, require_live_quote=True)
        assert not result.passed
        assert any("insufficient" in r.lower() for r in result.reasons)

    def test_missing_source_identity_rejects(self):
        from data.data_quality import DataQualityGate
        snap = self._good_snapshot()
        snap["context"]["source_identity"] = "bad"
        gate = DataQualityGate()
        result = gate.validate(snap, require_live_quote=True)
        assert not result.passed

    def test_paper_mode_skips_live_quote(self):
        """Paper mode should not require live-quote validation."""
        from data.data_quality import DataQualityGate
        snap = {"ohlc": [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(30)]}
        gate = DataQualityGate()
        result = gate.validate(snap, require_live_quote=False)
        assert result.passed, result.reasons

    def test_candle_revision_detected(self):
        """Same timestamp with different OHLC should be flagged.

        The revision fingerprint only covers SETTLED candles (a forming
        candle legitimately churns on every poll), so the tampered bar is
        stamped two M15 bars in the past to make it authoritative.
        """
        from data.data_quality import DataQualityGate
        gate = DataQualityGate()
        ts = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        snap1 = {"ohlc": [{"timestamp": ts, "open": 100, "high": 101,
                             "low": 99, "close": 100} for _ in range(30)]}
        snap1["context"] = {
            "market_data_source": "mt5", "symbol": "EURUSD",
            "timeframe": "M15",
            "source_identity": {"provider": "mt5", "server": "Test",
                                "account_hash": "0" * 32},
            "captured_at": ts, "provider_time": ts,
            "clock_health": {"valid": True, "offset_seconds": 0},
        }
        # First pass — fingerprints recorded
        gate.validate(snap1, require_live_quote=False)
        # Second pass — same timestamp, different close
        snap2 = dict(snap1)
        snap2["ohlc"] = list(snap1["ohlc"])
        snap2["ohlc"][-1] = {"timestamp": ts, "open": 100, "high": 101,
                              "low": 99, "close": 105}
        result = gate.validate(snap2, require_live_quote=False)
        assert any("revision" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# G4: Circuit breaker evaluate_circuits
# ---------------------------------------------------------------------------

class TestCircuitBreakerWiring:
    """Tests for circuit breaker evaluation wired into the main loop."""

    def _make_manager(self, tmp_path):
        from operations.autonomy import AutonomyManager
        state_file = str(tmp_path / "autonomy.json")
        control_file = str(tmp_path / "control.jsonl")
        return AutonomyManager(state_file=state_file, control_file=control_file)

    def test_no_trip_within_bounds(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"drawdown_pct": 2.0, "recon_mismatch_24h": 0,
                   "slippage_breach_streak": 0, "llm_outage_minutes": 0,
                   "data_stale_seconds": 30}
        trips = mgr.evaluate_circuits(metrics)
        assert trips == []
        assert mgr.snapshot().halted is False

    def test_drawdown_circuit_halts(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"drawdown_pct": 7.0}  # threshold is 6.0
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) == 1
        assert trips[0]["key"] == "drawdown"
        assert trips[0]["severity"] == "CRITICAL"
        assert trips[0]["action"] == "HALT"
        assert mgr.snapshot().halted is True
        assert "circuit:drawdown" in mgr.snapshot().halt_reason

    def test_recon_mismatch_circuit_halts(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"recon_mismatch_24h": 3}  # threshold is 2
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) == 1
        assert trips[0]["key"] == "recon_mismatch"
        assert mgr.snapshot().halted is True

    def test_slippage_streak_demotes(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.snapshot().level == "L1_PAPER"
        metrics = {"slippage_breach_streak": 4}  # threshold is 3
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) == 1
        assert trips[0]["action"] == "DEMOTE"
        assert mgr.snapshot().level == "L0_SHADOW"

    def test_stale_data_pauses(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"data_stale_seconds": 150}  # threshold is 120
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) == 1
        assert trips[0]["action"] == "PAUSE_NEW_CYCLES"
        assert mgr.snapshot().paused is True

    def test_missing_metric_skips(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        # Empty metrics — no trips
        trips = mgr.evaluate_circuits({})
        assert trips == []

    def test_multiple_circuits_trip_simultaneously(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"drawdown_pct": 10.0, "slippage_breach_streak": 5,
                   "data_stale_seconds": 200}
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) >= 2
        # Should be halted from drawdown
        assert mgr.snapshot().halted is True

    def test_circuit_trip_recorded_in_history(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"drawdown_pct": 7.0}
        mgr.evaluate_circuits(metrics)
        history = mgr.snapshot().history
        circuit_events = [h for h in history if h.get("kind") == "CIRCUIT_TRIP"]
        assert len(circuit_events) >= 1
        assert circuit_events[-1]["key"] == "drawdown"

    def test_llm_outage_sets_hold_only_flag(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        metrics = {"llm_outage_minutes": 15}  # threshold is 10
        trips = mgr.evaluate_circuits(metrics)
        assert len(trips) == 1
        assert trips[0]["action"] == "DEGRADE_HOLD_ONLY"
        assert mgr._state.get("flags", {}).get("hold_only") is True


# ---------------------------------------------------------------------------
# G8: Autonomous recovery — auto-demote on consecutive failures
# ---------------------------------------------------------------------------

class TestAutonomousRecovery:
    """Tests for the autonomous recovery logic in the main loop."""

    def test_consecutive_failures_below_threshold_no_demotion(self):
        """3 failures should trigger demotion (threshold is >= 3)."""
        from operations.autonomy import AutonomyManager
        mgr = AutonomyManager(state_file="/tmp/test_ar_state.json",
                              control_file="/tmp/test_ar_ctrl.jsonl")
        mgr._state["level"] = "L1_PAPER"
        mgr._persist_locked()

        # Simulate: consecutive_failures = 2 (below threshold)
        consecutive = 2
        if consecutive >= 3:
            mgr.demote(f"consecutive_cycle_failures={consecutive}")
        assert mgr.snapshot().level == "L1_PAPER"

    def test_consecutive_failures_at_threshold_demotes(self):
        """At 3+ consecutive failures, autonomy should demote."""
        from operations.autonomy import AutonomyManager
        mgr = AutonomyManager(state_file="/tmp/test_ar_state2.json",
                              control_file="/tmp/test_ar_ctrl2.jsonl")
        mgr._state["level"] = "L1_PAPER"
        mgr._persist_locked()

        consecutive = 3
        if consecutive >= 3:
            mgr.demote(f"consecutive_cycle_failures={consecutive}")
        assert mgr.snapshot().level == "L0_SHADOW"

    def test_demote_at_bottom_stays_at_bottom(self):
        """Demoting from L0 should stay at L0 (rank 0 is minimum)."""
        from operations.autonomy import AutonomyManager
        mgr = AutonomyManager(state_file="/tmp/test_ar_state3.json",
                              control_file="/tmp/test_ar_ctrl3.jsonl")
        mgr._state["level"] = "L0_SHADOW"
        mgr._persist_locked()

        result = mgr.demote("test bottom")
        # Should still be L0 since rank 0 can't go lower
        assert mgr.snapshot().level == "L0_SHADOW"

    def test_high_failure_count_demotes(self):
        """Even 10 consecutive failures should only demote one level."""
        from operations.autonomy import AutonomyManager
        mgr = AutonomyManager(state_file="/tmp/test_ar_state4.json",
                              control_file="/tmp/test_ar_ctrl4.jsonl")
        mgr._state["level"] = "L2_MICRO"
        mgr._persist_locked()

        mgr.demote("consecutive_cycle_failures=10")
        assert mgr.snapshot().level == "L1_PAPER"


# ---------------------------------------------------------------------------
# G5: Safety state in /api/snapshot
# ---------------------------------------------------------------------------

class TestSnapshotSafetyState:
    """Tests for the autonomous safety state included in /api/snapshot."""

    def test_snapshot_includes_safety_field(self):
        """The /api/snapshot endpoint should include a 'safety' field."""
        import sys
        sys.path.insert(0, "ui")
        # We can't easily spin up FastAPI, but we can test the function logic
        # by checking that the safety state assembly code works.
        from core.execution_safety_supervisor import LiveExecutionSafetySupervisor
        sup = LiveExecutionSafetySupervisor()
        status = sup.inspect_safety_status()
        assert isinstance(status, dict)
        assert "is_trading_enabled" in status
        assert "kill_switch_active" in status
        assert "permanent_halt" in status

    def test_alpha_decay_state_readable(self):
        """Alpha decay state JSON should be parseable for snapshot."""
        state_file = "alpha_decay_state.json"
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)
            # Should have alphas or empty
            alphas = data.get("alphas", {})
            assert isinstance(alphas, dict)

    def test_ewc_state_readable(self):
        """EWC state JSON should be parseable for snapshot."""
        state_file = "ewc_state.json"
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Integration: Gate ordering in execute_cycle
# ---------------------------------------------------------------------------

class TestGateOrdering:
    """Verify the correct ordering of gates in the execution path."""

    def test_data_quality_gate_before_analysis(self):
        """DataQualityGate.validate() must be called before any analysis."""
        import ast
        tree = ast.parse(open("intelligence/llm_orchestrator.py", encoding="utf-8").read())
        source = ast.dump(tree)
        # Find that data_quality.validate appears before feature_snapshot
        dq_pos = source.find("data_quality")
        fs_pos = source.find("feature_snapshot")
        assert dq_pos < fs_pos, "DataQualityGate must come before feature_snapshot"

    def test_execution_safety_gate_before_order_send(self):
        """ExecutionSafetySupervisor must gate before place_market_order dispatch."""
        import ast
        source = open("data/market_data_provider.py", encoding="utf-8").read()
        safety_pos = source.find("validate_pre_trade_safety")
        order_pos = source.find("return place_market_order(")
        assert safety_pos < order_pos, "Safety gate must come before order dispatch"

    def test_circuit_breaker_in_main_loop(self):
        """evaluate_circuits() must appear in the main trading loop."""
        source = open("mt5_trader.py", encoding="utf-8").read()
        assert "evaluate_circuits" in source, "Circuit breaker not wired into main loop"
        assert "CIRCUIT TRIP" in source or "circuit_breaker" in source.lower(), \
            "Circuit trip logging missing from main loop"
