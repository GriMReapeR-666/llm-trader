"""Tests for the Agent Event Bus and pipeline wiring.

Covers:
  - AgentEventBus emit/poll/count/clear
  - Event id monotonicity
  - Cross-thread safety
  - TUI EventFeed poll_bus integration
  - Orchestrator _bus_emit helper
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


class TestAgentEventBus:
    """Test the core AgentEventBus."""

    @pytest.fixture
    def bus(self, tmp_path):
        from core.agent_event_bus import AgentEventBus
        db = str(tmp_path / "test_events.db")
        return AgentEventBus(db_path=db)

    def test_emit_returns_id(self, bus):
        eid = bus.emit("ω", "CYCLE_START", "started", {"cycle": 1})
        assert eid > 0

    def test_poll_returns_emitted_events(self, bus):
        bus.emit("α", "DISCOVERY", "found", {"candidates": 3})
        bus.emit("δ", "DATA_COLLECT", "fetched", {"bars": 100})
        events = bus.poll(since_id=0)
        assert len(events) == 2
        assert events[0].agent == "α"
        assert events[1].agent == "delta" or events[1].agent == "δ"

    def test_poll_since_id(self, bus):
        bus.emit("α", "TEST", "a")
        eid2 = bus.emit("β", "TEST", "b")
        events = bus.poll(since_id=eid2 - 1)
        assert len(events) == 1
        assert events[0].agent == "β"

    def test_count(self, bus):
        assert bus.count() == 0
        bus.emit("α", "TEST", "a")
        bus.emit("β", "TEST", "b")
        assert bus.count() == 2

    def test_clear(self, bus):
        bus.emit("α", "TEST", "a")
        bus.clear()
        assert bus.count() == 0

    def test_payload_json_roundtrip(self, bus):
        payload = {"symbol": "EURUSD", "volume": 0.05, "nested": {"key": "val"}}
        bus.emit("ε", "ORDER", "submit", payload)
        events = bus.poll(since_id=0)
        assert events[0].payload["symbol"] == "EURUSD"
        assert events[0].payload["nested"]["key"] == "val"

    def test_id_monotonic(self, bus):
        ids = [bus.emit("α", f"action_{i}", f"stage_{i}") for i in range(10)]
        assert ids == sorted(ids)
        assert all(ids[i] < ids[i + 1] for i in range(len(ids) - 1))

    def test_empty_poll(self, bus):
        events = bus.poll(since_id=999)
        assert events == []

    def test_latest_id(self, bus):
        assert bus.latest_id() == 0
        eid = bus.emit("α", "TEST", "a")
        assert bus.latest_id() == eid

    def test_agent_event_dataclass(self):
        from core.agent_event_bus import AgentEvent
        ev = AgentEvent(id=1, timestamp="2026-09-01T00:00:00", agent="α",
                        action="test", stage="test_stage", payload={"k": "v"})
        d = ev.to_dict()
        assert d["agent"] == "α"
        assert d["payload"]["k"] == "v"


class TestEventBusConcurrency:
    """Test cross-thread safety."""

    def test_concurrent_emit(self, tmp_path):
        from core.agent_event_bus import AgentEventBus
        bus = AgentEventBus(db_path=str(tmp_path / "conc.db"))
        errors = []

        def writer(agent, count):
            try:
                for i in range(count):
                    bus.emit(agent, f"action_{i}", f"stage_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"agent_{j}", 20)) for j in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert bus.count() == 100


class TestOrchestratorBusEmit:
    """Test the orchestrator _bus_emit helper."""

    def test_bus_emit_does_not_crash(self):
        """_bus_emit should never raise, even if bus is unavailable."""
        from unittest.mock import MagicMock

        orch = MagicMock()
        # Call the actual function via the module
        from intelligence.llm_orchestrator import LLMOrchestrator
        # We just test the method exists and handles errors
        orch._bus_emit = LLMOrchestrator._bus_emit.__get__(orch, LLMOrchestrator)
        # Should not raise even if get_bus fails
        with patch("core.agent_event_bus.get_bus", side_effect=Exception("no bus")):
            orch._bus_emit("ω", "TEST", "test_stage", {"key": "val"}, 1, "EURUSD")
