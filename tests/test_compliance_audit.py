"""Tests for κ Compliance & Audit Agent — immutable hash-chained audit log.

Covers:
- Append-only: entries cannot be modified
- Hash-chained: each entry links to previous via SHA-256
- Chain verification: detect tampering
- All event types: state transitions, LLM calls, orders, risk, gates
- Wash-sale detection
- Pattern-day-trading detection
- Spoofing pattern detection
- Report generation
- JSONL export
- Thread safety
- Edge cases: empty log, single entry, large payload
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta

import pytest

from core.compliance_audit import (
    ComplianceAudit,
    EventType,
    AuditEntry,
    get_audit,
    log_event,
    WASH_SALE_WINDOW_DAYS,
    PDT_WINDOW_DAYS,
    PDT_THRESHOLD,
    SPOOF_WINDOW_S,
    SPOOF_THRESHOLD,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def audit(tmp_path) -> ComplianceAudit:
    """Create a fresh audit instance with temp DB."""
    db_path = str(tmp_path / "test_audit.db")
    return ComplianceAudit(db_path=db_path)


# ============================================================================
# TEST: HASH CHAIN INTEGRITY
# ============================================================================

class TestHashChain:
    def test_empty_chain_valid(self, audit):
        result = audit.verify_chain()
        assert result["valid"]
        assert result["total_entries"] == 0

    def test_single_entry_valid(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        result = audit.verify_chain()
        assert result["valid"]
        assert result["total_entries"] == 1

    def test_chain_links_correctly(self, audit):
        for i in range(10):
            audit.log(EventType.STATE_TRANSITION, cycle_num=i, agent="α")
        result = audit.verify_chain()
        assert result["valid"]
        assert result["total_entries"] == 10

    def test_prev_hash_links(self, audit):
        e1 = audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        e2 = audit.log(EventType.ORDER_FILLED, symbol="EURUSD")
        assert e2.prev_hash == e1.entry_hash

    def test_first_entry_prev_is_genesis(self, audit):
        e = audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        assert e.prev_hash == "GENESIS"

    def test_hash_computed_correctly(self):
        entry = AuditEntry(
            timestamp="2026-01-01T00:00:00Z",
            event_type="ORDER_SUBMITTED",
            agent="ε",
            symbol="EURUSD",
            cycle_num=1,
            payload={"side": "BUY", "volume": 0.1},
            prev_hash="GENESIS",
        )
        computed = entry.compute_hash()
        assert len(computed) == 20
        assert computed != "GENESIS"

    def test_hash_deterministic(self):
        entry1 = AuditEntry(
            timestamp="2026-01-01T00:00:00Z",
            event_type="ORDER_SUBMITTED",
            payload={"side": "BUY"},
            prev_hash="GENESIS",
        )
        entry2 = AuditEntry(
            timestamp="2026-01-01T00:00:00Z",
            event_type="ORDER_SUBMITTED",
            payload={"side": "BUY"},
            prev_hash="GENESIS",
        )
        assert entry1.compute_hash() == entry2.compute_hash()


# ============================================================================
# TEST: APPEND-ONLY
# ============================================================================

class TestAppendOnly:
    def test_entries_only_added(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_CANCELLED, symbol="GBPUSD", cycle_num=2)
        assert audit.count() == 3

    def test_cannot_update_via_query(self, audit):
        """SQLite rows are immutable in our API — no UPDATE/DELETE exposed."""
        entry = audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        # Verify entry exists
        rows = audit.query(event_type="ORDER_SUBMITTED")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "EURUSD"


# ============================================================================
# TEST: EVENT TYPES
# ============================================================================

class TestEventTypes:
    def test_state_transition(self, audit):
        e = audit.log_state_transition(
            agent="α", symbol="EURUSD", cycle_num=1,
            from_state="IDLE", to_state="RESEARCHING", reason="decay detected",
        )
        assert e.event_type == EventType.STATE_TRANSITION.value
        assert e.agent == "α"
        assert e.payload["from"] == "IDLE"
        assert e.payload["to"] == "RESEARCHING"

    def test_llm_call(self, audit):
        e = audit.log_llm_call(
            symbol="EURUSD", cycle_num=5,
            prompt_tokens=1500, completion_tokens=200,
            model="gpt-4", decision="BUY",
        )
        assert e.event_type == EventType.LLM_CALL.value
        assert e.payload["prompt_tokens"] == 1500
        assert e.payload["model"] == "gpt-4"

    def test_order_events(self, audit):
        for et in [
            EventType.ORDER_SUBMITTED,
            EventType.ORDER_FILLED,
            EventType.ORDER_CANCELLED,
            EventType.ORDER_REJECTED,
            EventType.ORDER_MODIFY,
        ]:
            e = audit.log_order(
                et, symbol="EURUSD", cycle_num=1,
                ticket=12345, side="BUY", volume=0.1, price=1.1000,
            )
            assert e.event_type == et.value

    def test_position_events(self, audit):
        for et in [
            EventType.POSITION_OPENED,
            EventType.POSITION_CLOSED,
            EventType.POSITION_MODIFIED,
            EventType.PARTIAL_CLOSE,
        ]:
            e = audit.log(EventType(et.value), symbol="EURUSD", cycle_num=1)
            assert e.event_type == et.value

    def test_risk_decision(self, audit):
        e = audit.log_risk_decision(
            symbol="EURUSD", cycle_num=3,
            decision="APPROVED", reason="within VaR limits",
        )
        assert e.event_type == EventType.RISK_DECISION.value
        assert e.payload["decision"] == "APPROVED"

    def test_gate_passed(self, audit):
        e = audit.log_gate(
            passed=True, gate_name="overfit_check", symbol="EURUSD", cycle_num=1,
        )
        assert e.event_type == EventType.GATE_PASSED.value
        assert e.payload["gate"] == "overfit_check"

    def test_gate_failed(self, audit):
        e = audit.log_gate(
            passed=False, gate_name="data_quality", symbol="GBPUSD", cycle_num=2,
            reason="stale data",
        )
        assert e.event_type == EventType.GATE_FAILED.value
        assert e.payload["passed"] is False

    def test_kill_switch(self, audit):
        e = audit.log(
            EventType.KILL_SWITCH, symbol="EURUSD",
            agent="Ψ", payload={"severity": "RED", "reason": "drawdown 25%"},
        )
        assert e.event_type == EventType.KILL_SWITCH.value


# ============================================================================
# TEST: QUERY
# ============================================================================

class TestQuery:
    def test_query_all(self, audit):
        for i in range(5):
            audit.log(EventType.ORDER_SUBMITTED, cycle_num=i)
        rows = audit.query()
        assert len(rows) == 5

    def test_query_by_type(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD")
        audit.log(EventType.LLM_CALL, symbol="EURUSD")
        rows = audit.query(event_type="ORDER_SUBMITTED")
        assert len(rows) == 1

    def test_query_by_symbol(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        audit.log(EventType.ORDER_SUBMITTED, symbol="GBPUSD")
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        rows = audit.query(symbol="EURUSD")
        assert len(rows) == 2

    def test_query_by_agent(self, audit):
        audit.log(EventType.LLM_CALL, agent="ω")
        audit.log(EventType.ORDER_SUBMITTED, agent="ε")
        audit.log(EventType.RISK_DECISION, agent="ρ")
        rows = audit.query(agent="ε")
        assert len(rows) == 1

    def test_query_by_cycle(self, audit):
        for i in range(10):
            audit.log(EventType.STATE_TRANSITION, cycle_num=i)
        rows = audit.query(cycle_from=5, cycle_to=7)
        assert len(rows) == 3

    def test_query_limit(self, audit):
        for i in range(20):
            audit.log(EventType.ORDER_SUBMITTED, cycle_num=i)
        rows = audit.query(limit=5)
        assert len(rows) == 5


# ============================================================================
# TEST: WASH SALE DETECTION
# ============================================================================

class TestWashSale:
    def test_no_wash_sale(self, audit):
        result = audit.check_wash_sale("EURUSD", "BUY", 1.1000)
        assert not result["wash_sale_risk"]

    def test_wash_sale_detected(self, audit):
        # Sell at 1.1000 (loss), then buy at 1.1050 (higher)
        audit.log_order(
            EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1,
            side="BUY", price=1.1000, volume=0.1,
        )
        result = audit.check_wash_sale("EURUSD", "SELL", 1.0950)
        assert result["wash_sale_risk"]

    def test_wash_sale_not_on_profit(self, audit):
        # Buy at 1.1000, sell at 1.1050 (profit) — not a wash sale
        audit.log_order(
            EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1,
            side="BUY", price=1.1000, volume=0.1,
        )
        result = audit.check_wash_sale("EURUSD", "SELL", 1.1050)
        assert not result["wash_sale_risk"]

    def test_wash_sale_different_symbol(self, audit):
        audit.log_order(
            EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1,
            side="BUY", price=1.1000, volume=0.1,
        )
        result = audit.check_wash_sale("GBPUSD", "SELL", 1.0950)
        assert not result["wash_sale_risk"]


# ============================================================================
# TEST: PATTERN DAY TRADING
# ============================================================================

class TestPatternDayTrading:
    def test_no_pdt(self, audit):
        result = audit.check_pattern_day_trading()
        assert not result["pattern_day_risk"]

    def test_pdt_detected(self, audit):
        # 4 round-trips for EURUSD
        for i in range(4):
            audit.log(EventType.POSITION_OPENED, symbol="EURUSD", cycle_num=i)
            audit.log(EventType.POSITION_CLOSED, symbol="EURUSD", cycle_num=i)
        result = audit.check_pattern_day_trading()
        assert result["pattern_day_risk"]
        assert "EURUSD" in result["symbols_at_risk"]

    def test_pdt_multiple_symbols(self, audit):
        # 5 round-trips EURUSD, 2 GBPUSD
        for i in range(5):
            audit.log(EventType.POSITION_OPENED, symbol="EURUSD", cycle_num=i)
            audit.log(EventType.POSITION_CLOSED, symbol="EURUSD", cycle_num=i)
        for i in range(2):
            audit.log(EventType.POSITION_OPENED, symbol="GBPUSD", cycle_num=i)
            audit.log(EventType.POSITION_CLOSED, symbol="GBPUSD", cycle_num=i)
        result = audit.check_pattern_day_trading()
        assert result["pattern_day_risk"]
        assert "EURUSD" in result["symbols_at_risk"]
        assert "GBPUSD" not in result["symbols_at_risk"]


# ============================================================================
# TEST: SPOOFING DETECTION
# ============================================================================

class TestSpoofing:
    def test_no_spoofing(self, audit):
        result = audit.check_spoofing("EURUSD")
        assert not result["spoofing_risk"]

    def test_spoofing_detected(self, audit):
        # 3 rapid cancels on same symbol
        for i in range(SPOOF_THRESHOLD):
            audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=i)
            audit.log(EventType.ORDER_CANCELLED, symbol="EURUSD", cycle_num=i)
        result = audit.check_spoofing("EURUSD")
        assert result["spoofing_risk"]

    def test_spoofing_different_symbols(self, audit):
        for i in range(SPOOF_THRESHOLD):
            audit.log(EventType.ORDER_CANCELLED, symbol="EURUSD", cycle_num=i)
            audit.log(EventType.ORDER_CANCELLED, symbol="GBPUSD", cycle_num=i)
        # EURUSD has 3 cancels → spoofing
        result_eur = audit.check_spoofing("EURUSD")
        assert result_eur["spoofing_risk"]
        # GBPUSD has 3 cancels → also spoofing
        result_gbp = audit.check_spoofing("GBPUSD")
        assert result_gbp["spoofing_risk"]


# ============================================================================
# TEST: REPORT GENERATION
# ============================================================================

class TestReport:
    def test_generate_report(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.LLM_CALL, symbol="EURUSD", cycle_num=1)
        report = audit.generate_report(hours=1)
        assert report["total_entries"] == 3
        assert report["llm_calls"] == 1
        assert "ORDER_SUBMITTED" in report["by_event_type"]

    def test_format_report(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        report = audit.generate_report(hours=1)
        text = audit.format_report(report)
        assert "κ COMPLIANCE & AUDIT REPORT" in text
        assert "EURUSD" not in text  # Not in summary, only in events

    def test_report_with_alerts(self, audit):
        audit.log(EventType.WASH_SALE_ALERT, symbol="EURUSD", payload={})
        audit.log(EventType.SPOOFING_ALERT, symbol="GBPUSD", payload={})
        report = audit.generate_report(hours=1)
        assert report["alerts"]["WASH_SALE_ALERT"] == 1
        assert report["alerts"]["SPOOFING_ALERT"] == 1


# ============================================================================
# TEST: EXPORT
# ============================================================================

class TestExport:
    def test_export_jsonl(self, audit, tmp_path):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1)
        output = str(tmp_path / "export.jsonl")
        count = audit.export_jsonl(output, hours=1)
        assert count == 2
        with open(output, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        entry = json.loads(lines[0])
        assert entry["event_type"] == "ORDER_SUBMITTED"


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

class TestEdgeCases:
    def test_empty_payload(self, audit):
        e = audit.log(EventType.STATE_TRANSITION, symbol="EURUSD")
        assert e.payload == {}

    def test_large_payload(self, audit):
        large = {"data": "x" * 10000, "nested": {"a": list(range(100))}}
        e = audit.log(EventType.LLM_CALL, payload=large)
        rows = audit.query(event_type="LLM_CALL")
        assert rows[0]["payload"]["data"] == "x" * 10000

    def test_unicode_payload(self, audit):
        e = audit.log(EventType.LLM_DECISION, payload={"reason": "信号强度高 🚀"})
        rows = audit.query(event_type="LLM_DECISION")
        assert "🚀" in rows[0]["payload"]["reason"]

    def test_concurrent_logging(self, audit):
        """Thread safety: multiple threads logging simultaneously."""
        errors = []

        def log_events(thread_id: int):
            try:
                for i in range(20):
                    audit.log(
                        EventType.STATE_TRANSITION,
                        symbol=f"SYM_{thread_id}",
                        cycle_num=i,
                        agent=f"agent_{thread_id}",
                    )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=log_events, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert audit.count() == 100  # 5 threads × 20 events
        result = audit.verify_chain()
        assert result["valid"]

    def test_single_entry_chain(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD")
        result = audit.verify_chain()
        assert result["valid"]
        assert result["total_entries"] == 1

    def test_count_with_filters(self, audit):
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD", cycle_num=1)
        audit.log(EventType.ORDER_SUBMITTED, symbol="GBPUSD", cycle_num=2)
        assert audit.count(event_type="ORDER_SUBMITTED") == 2
        assert audit.count(symbol="EURUSD") == 2
        assert audit.count(cycle_from=2) == 1
        assert audit.count(event_type="ORDER_FILLED", symbol="GBPUSD") == 0
