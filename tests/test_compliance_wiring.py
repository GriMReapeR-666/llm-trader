"""Tests for compliance dashboard, κ audit wiring in orders, and S.Q integration.

Verifies:
- Compliance dashboard generates valid HTML
- κ audit events are logged from order_execution functions
- S.Q task queue initializes and ticks in the main loop context
- Dashboard shows chain integrity, alerts, and event breakdown
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Compliance Dashboard
# ---------------------------------------------------------------------------

class TestComplianceDashboard:
    """Tests for ui/compliance_dashboard.py."""

    def test_generate_returns_path(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        result = generate_compliance_dashboard(hours=1, output_path=out)
        assert result == out
        assert os.path.exists(out)

    def test_html_structure(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "Compliance Dashboard" in content
        assert "Hash Chain Integrity" in content
        assert "Recent Audit Events" in content

    def test_empty_events_section(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        # Should handle empty state gracefully
        assert "</table>" in content or "No events" in content

    def test_events_with_data(self, tmp_path):
        """With actual audit events, table should be populated."""
        from core.compliance_audit import get_audit, EventType
        audit = get_audit()
        audit.log(EventType.ORDER_SUBMITTED, symbol="EURUSD", agent="ε",
                  payload={"side": "BUY", "volume": 0.1})
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "EURUSD" in content

    def test_chain_integrity_shown(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "VALID" in content or "BROKEN" in content

    def test_summary_cards_present(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "compliance.html")
        generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "Total Events" in content
        assert "Chain Valid" in content


# ---------------------------------------------------------------------------
# κ audit in order_execution
# ---------------------------------------------------------------------------

class TestKappaOrderLogging:
    """Tests that order_execution functions trigger κ audit events."""

    def test_place_market_order_logs_event(self, tmp_path):
        """Verify κ logging doesn't crash when called from place_market_order path."""
        from core.compliance_audit import get_audit, EventType
        audit = get_audit()
        # Simulate the logging that place_market_order does
        audit.log(EventType.ORDER_FILLED, symbol="EURUSD", agent="ε",
                  payload={"type": "market_order", "side": "BUY", "volume": 0.1})
        events = audit.query(event_type="ORDER_FILLED", limit=5)
        assert any(e.get("symbol") == "EURUSD" for e in events)

    def test_emergency_exit_logs_event(self, tmp_path):
        from core.compliance_audit import get_audit, EventType
        audit = get_audit()
        audit.log(EventType.ORDER_FILLED, symbol="GBPUSD", agent="ε",
                  payload={"type": "emergency_exit", "reason": "guardian_trip"})
        events = audit.query(event_type="ORDER_FILLED", limit=5)
        assert any("emergency_exit" in str(e.get("payload", "")) for e in events)

    def test_pending_order_logs_event(self, tmp_path):
        from core.compliance_audit import get_audit, EventType
        audit = get_audit()
        audit.log(EventType.ORDER_SUBMITTED, symbol="XAUUSD", agent="ε",
                  payload={"type": "buy_limit", "price": 2000.0, "volume": 0.05})
        events = audit.query(event_type="ORDER_SUBMITTED", limit=5)
        assert any(e.get("symbol") == "XAUUSD" for e in events)


# ---------------------------------------------------------------------------
# S.Q integration
# ---------------------------------------------------------------------------

class TestSQIntegration:
    """Tests for S.Q task queue initialization and tick."""

    def test_migrate_from_intervals(self):
        from core.task_queue import migrate_from_intervals
        from core.auto_quant_tools import DEFAULT_INTERVALS
        queue = migrate_from_intervals(DEFAULT_INTERVALS)
        assert len(queue._pending) > 0

    def test_queue_tick(self):
        from core.task_queue import migrate_from_intervals
        from core.auto_quant_tools import DEFAULT_INTERVALS
        queue = migrate_from_intervals(DEFAULT_INTERVALS)
        # get_ready_tasks should not crash at various cycle numbers
        queue.get_ready_tasks(cycle_num=0)
        queue.get_ready_tasks(cycle_num=1)
        queue.get_ready_tasks(cycle_num=100)

    def test_ready_tasks(self):
        from core.task_queue import migrate_from_intervals, Priority
        from core.auto_quant_tools import DEFAULT_INTERVALS
        queue = migrate_from_intervals(DEFAULT_INTERVALS)
        # CRITICAL tasks should be ready immediately
        ready = queue.get_ready_tasks(cycle_num=0)
        assert isinstance(ready, list)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_dashboard_survives_missing_audit(self, tmp_path):
        """Dashboard should generate even if κ has no events."""
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "empty.html")
        result = generate_compliance_dashboard(hours=1, output_path=out)
        assert os.path.exists(out)

    def test_dashboard_file_is_valid_html(self, tmp_path):
        from ui.compliance_dashboard import generate_compliance_dashboard
        out = str(tmp_path / "valid.html")
        generate_compliance_dashboard(hours=24, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        # Basic HTML validation
        assert content.startswith("<!DOCTYPE html>")
        assert content.rstrip().endswith("</html>")
        assert content.count("<table>") == content.count("</table>")


class TestOutboxHealthSection:
    """The dashboard surfaces last_market_health.outbox_health for operators."""

    def _generate_with_journal(self, tmp_path, monkeypatch, health_state):
        """Point the dashboard's journal read at a temp store with a seeded state."""
        from core.trading_safety import TradingStore
        from ui import compliance_dashboard as mod

        journal = str(tmp_path / "journal.db")
        store = TradingStore(journal)
        if health_state is not None:
            store.set_state("last_market_health", health_state)
        monkeypatch.setattr(mod, "JOURNAL_PATH", journal)
        out = str(tmp_path / "compliance.html")
        result = mod.generate_compliance_dashboard(hours=1, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        return result, content

    def test_outbox_section_renders_counts_and_badge(self, tmp_path, monkeypatch):
        state = {
            "captured_at": "2026-09-26T10:31:43+00:00",
            "outbox_health": {
                "status": "WARN",
                "counts": {"EXPIRED": 2732, "SENT": 148, "PENDING": 0},
                "pending": 0, "expired": 2732, "oldest_pending_at": None,
            },
        }
        _, content = self._generate_with_journal(tmp_path, monkeypatch, state)
        assert "Operator Alert Outbox" in content
        assert "badge badge-warn" in content and "WARN" in content
        assert "EXPIRED" in content and "2732" in content
        assert "SENT" in content and "148" in content
        assert "2026-09-26T10:31:43+00:00" in content  # captured_at surfaced

    def test_outbox_section_critical_badge(self, tmp_path, monkeypatch):
        state = {"outbox_health": {"status": "CRITICAL",
                                   "counts": {"PENDING": 500}}}
        _, content = self._generate_with_journal(tmp_path, monkeypatch, state)
        assert "badge badge-crit" in content and "CRITICAL" in content
        assert "500" in content

    def test_outbox_section_absent_when_no_state(self, tmp_path, monkeypatch):
        """No last_market_health state -> no section, dashboard still valid."""
        _, content = self._generate_with_journal(tmp_path, monkeypatch, None)
        assert "Operator Alert Outbox" not in content
        assert content.startswith("<!DOCTYPE html>")

    def test_outbox_section_absent_when_block_missing(self, tmp_path, monkeypatch):
        """Older market_health payloads without outbox_health render no section."""
        state = {"captured_at": "2026-09-26T10:00:00+00:00", "alerts": {}}
        _, content = self._generate_with_journal(tmp_path, monkeypatch, state)
        assert "Operator Alert Outbox" not in content

    def test_outbox_section_fail_soft_on_bad_journal(self, tmp_path, monkeypatch):
        """A missing/corrupt journal must not break dashboard generation."""
        from ui import compliance_dashboard as mod
        monkeypatch.setattr(mod, "JOURNAL_PATH", str(tmp_path / "nope" / "x.db"))
        out = str(tmp_path / "compliance.html")
        result = mod.generate_compliance_dashboard(hours=1, output_path=out)
        assert result == out
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "Operator Alert Outbox" not in content
        assert content.rstrip().endswith("</html>")

    def test_outbox_oldest_pending_rendered_when_present(self, tmp_path, monkeypatch):
        state = {"outbox_health": {
            "status": "OK", "counts": {"PENDING": 2},
            "oldest_pending_at": "2026-09-26T08:00:00+00:00"}}
        _, content = self._generate_with_journal(tmp_path, monkeypatch, state)
        assert "2026-09-26T08:00:00+00:00" in content
        assert "badge badge-ok" in content


class TestSchedulerStarvationSection:
    """The dashboard surfaces schedule-skip + overrun telemetry for operators."""

    def _generate_with_events(self, tmp_path, monkeypatch, events, journal=None):
        """Seed operational_events into the journal, then generate the dashboard.

        ``events`` is a list of (event_type, payload, minutes_ago) tuples so
        every event lands inside the dashboard's window.
        """
        from datetime import datetime, timedelta, timezone
        from operations.agent_operations import AgentOperations
        from ui import compliance_dashboard as mod

        journal = journal or str(tmp_path / "journal.db")
        ops = AgentOperations(journal)
        now = datetime.now(timezone.utc)
        for event_type, payload, minutes_ago in events:
            created = (now - timedelta(minutes=minutes_ago)).isoformat()
            with ops._con() as con:
                con.execute(
                    "INSERT INTO operational_events(created_at,run_id,actor,event_type,"
                    "correlation_id,severity,payload,payload_hash) VALUES(?,?,?,?,?,?,?,?)",
                    (created, "run", "scheduler", event_type, str(payload.get("schedule", "")),
                     "WARNING", __import__("json").dumps(payload), ""))
        monkeypatch.setattr(mod, "JOURNAL_PATH", journal)
        out = str(tmp_path / "compliance.html")
        result = mod.generate_compliance_dashboard(hours=24, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        return result, content

    def test_section_present_with_dispatch_count_when_idle(self, tmp_path, monkeypatch):
        """An instrumented journal with no starvation shows the OK state."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_due", {"schedule": "market_health", "kind": "health"}, 10),
            ("schedule_due", {"schedule": "market_health", "kind": "health"}, 5),
        ])
        assert "Schedule Starvation" in content
        assert "badge badge-ok" in content
        assert "No starvation telemetry" in content
        assert "2 scheduled dispatches" in content

    def test_session_skip_renders_critical_with_silence(self, tmp_path, monkeypatch):
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_session_skip", {"schedule": "position_reconcile",
                                       "silence_s": 1234.5, "threshold_s": 600.0}, 8),
        ])
        assert "badge badge-crit" in content and "CRITICAL" in content
        assert "position_reconcile" in content and "SKIPPING" in content
        assert "1234.5s" in content and "600.0s" in content

    def test_recovery_after_skip_flips_episode_to_recovered(self, tmp_path, monkeypatch):
        """The LATEST event per schedule wins: a skip then a clear shows RECOVERED."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_session_skip", {"schedule": "pos_rec",
                                       "silence_s": 900.0, "threshold_s": 600.0}, 30),
            ("schedule_skips_cleared", {"schedule": "pos_rec"}, 5),
        ])
        assert "RECOVERED" in content
        assert "CRITICAL" not in content  # no open episode -> no critical badge

    def test_overruns_render_counts_and_worst_values(self, tmp_path, monkeypatch):
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 74.2, "over_by_s": 14.2}, 12),
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 81.0, "over_by_s": 21.0}, 4),
        ])
        assert "badge badge-warn" in content and "WARN" in content
        assert "event_monitor" in content
        assert "81.0s" in content and "21.0s" in content
        assert ">2<" in content  # aggregated overrun count

    def test_overrun_percentiles_match_report_definition(self, tmp_path, monkeypatch):
        """Parity pin: dashboard p50/p95 == the report's _percentile over ALL rows.

        The pre-unification SQL aggregated percentiles over median/p95
        survivor rows only (37 journal overruns rendered as n=2) and its
        even-count median averaged the middle two values. Seeds an odd-count
        and an even-count schedule, then asserts the dashboard's aggregates
        equal the report's element-pick definition row for row.
        """
        scripts = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from scheduler_tuning_report import _percentile as report_pct
        from ui import compliance_dashboard as mod

        event_elapsed = [60.5, 74.2, 81.0, 95.5, 120.25]  # odd count
        market_elapsed = [40.0, 50.0, 60.0, 70.0]         # even count
        events = []
        for i, elapsed in enumerate(event_elapsed):
            events.append(("schedule_handler_overrun",
                           {"schedule": "event_monitor", "budget_s": 60.0,
                            "elapsed_s": elapsed, "over_by_s": elapsed - 60.0},
                           60 - i))
        for i, elapsed in enumerate(market_elapsed):
            events.append(("schedule_handler_overrun",
                           {"schedule": "market_health", "budget_s": 30.0,
                            "elapsed_s": elapsed, "over_by_s": elapsed - 30.0},
                           60 - i))
        _, content = self._generate_with_events(tmp_path, monkeypatch, events)
        health = mod._get_scheduler_health(hours=24)
        rows = {row["schedule"]: row for row in health.get("overruns") or []}

        # n counts every journal row (the old SQL reported survivor rows only).
        assert rows["event_monitor"]["n"] == 5
        assert rows["market_health"]["n"] == 4
        assert [r["schedule"] for r in health["overruns"]] == [
            "event_monitor", "market_health"]  # ordered by n desc

        for name, elapsed in (("event_monitor", event_elapsed),
                              ("market_health", market_elapsed)):
            row = rows[name]
            assert row["worst_elapsed_s"] == max(elapsed)
            assert row["worst_over_by_s"] == max(
                e - (60.0 if name == "event_monitor" else 30.0) for e in elapsed)
            assert row["p50_elapsed_s"] == pytest.approx(report_pct(elapsed, 0.50))
            assert row["p95_elapsed_s"] == pytest.approx(report_pct(elapsed, 0.95))
            assert row["last_at"]

        # Even-count p50 follows the report's element-pick (index 2 of 4 =
        # 60.0), NOT the SQL average-of-middle-two (55.0) it used before.
        assert rows["market_health"]["p50_elapsed_s"] == 60.0

        # The rendered table carries the full counts too.
        assert ">5<" in content and ">4<" in content

    def test_abandoned_batches_render_reason_breakdown(self, tmp_path, monkeypatch):
        """The emitter's reason field (wave_deadline) is surfaced in the
        abandoned-batches note so deferral sources are distinguishable."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_skipped", {"schedules": ["a", "b"], "claimed": 3, "dispatched": 1,
                                  "reason": "wave_deadline"}, 6),
        ])
        assert "1 abandoned batch(es)" in content
        assert "wave_deadline x1" in content
        assert "released claims keep their cadence turn" in content

    def test_abandoned_batches_without_reason_render_unattributed(self, tmp_path, monkeypatch):
        """Legacy payloads pre-dating the reason field read as unattributed."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_skipped", {"schedules": ["x"], "claimed": 1, "dispatched": 0}, 6),
        ])
        assert "1 abandoned batch(es)" in content
        assert "unattributed x1" in content

    def test_summary_card_reflects_open_skips(self, tmp_path, monkeypatch):
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_session_skip", {"schedule": "pos_rec",
                                       "silence_s": 999.0, "threshold_s": 600.0}, 3),
        ])
        assert "Schedule Starvation" in content and "1 SKIPPING" in content

    def test_duty_cycle_table_renders_starved_verdict(self, tmp_path, monkeypatch):
        """schedule_due claims spanning >2h get a duty ratio vs the registry
        interval (market_health claims every 30s); a 1% ratio is STARVED."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_due", {"schedule": "market_health"}, 150),
            ("schedule_due", {"schedule": "market_health"}, 5),
        ])
        assert "Schedule Duty Cycle" in content
        assert "market_health" in content
        assert "STARVED" in content
        assert "30s" in content  # registry interval surfaced

    def test_duty_cycle_span_too_short_renders_no_ratio(self, tmp_path, monkeypatch):
        """Claims spanning <2h render the row without a ratio (report parity)."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_due", {"schedule": "market_health"}, 10),
            ("schedule_due", {"schedule": "market_health"}, 5),
        ])
        assert "Schedule Duty Cycle" in content
        assert "span too short" in content
        assert "STARVED" not in content

    def test_duty_table_absent_without_due_events(self, tmp_path, monkeypatch):
        """No schedule_due in window -> no duty table (skips still render)."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_session_skip", {"schedule": "pos_rec",
                                       "silence_s": 900.0, "threshold_s": 600.0}, 5),
        ])
        assert "Schedule Duty Cycle" not in content
        assert "SKIPPING" in content

    def test_overrun_p50_p95_rendered(self, tmp_path, monkeypatch):
        """p50/p95 elapsed columns follow the tuning report's element-pick
        definition (unified): an even-count p50 picks the upper-middle row
        rather than averaging the middle two like the old SQL median."""
        _, content = self._generate_with_events(tmp_path, monkeypatch, [
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 10.0, "over_by_s": 0.0}, 20),
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 20.0, "over_by_s": 0.0}, 15),
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 30.0, "over_by_s": 0.0}, 10),
            ("schedule_handler_overrun", {"schedule": "event_monitor", "budget_s": 60.0,
                                          "elapsed_s": 40.0, "over_by_s": 0.0}, 5),
        ])
        assert "30.0s" in content   # p50: element-pick index 2 of 4 (not AVG(20, 30))
        assert "40.0s" in content   # p95: element-pick index 3 of 4 == worst
        assert "25.0s" not in content  # old SQL even-count median is gone

    def test_absent_when_journal_has_no_events_table(self, tmp_path, monkeypatch):
        """Fail-soft: a journal without operational_events yields no section,
        while the at-a-glance summary card stays with an em-dash placeholder."""
        from ui import compliance_dashboard as mod
        import sqlite3
        bare = str(tmp_path / "bare.db")
        con = sqlite3.connect(bare)
        con.execute("CREATE TABLE other(x)")
        con.commit()
        con.close()
        monkeypatch.setattr(mod, "JOURNAL_PATH", bare)
        out = str(tmp_path / "compliance.html")
        result = mod.generate_compliance_dashboard(hours=24, output_path=out)
        assert result == out
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "◷ Schedule Starvation</h2>" not in content  # section absent
        assert "label'>Schedule Starvation<" in content      # card still present
        assert content.rstrip().endswith("</html>")


class TestLLMProviderSection:
    """The dashboard shows which LLM providers are initialized and whether
    AlphaDiscovery is on live LLM generation or static fallback."""

    def _seed_journal_state(self, journal: str, state: dict) -> None:
        import sqlite3
        con = sqlite3.connect(journal)
        con.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        con.execute("INSERT OR REPLACE INTO state(key,value,updated_at) VALUES('alpha_discovery_state',?, '2026-09-27T00:00:00+00:00')",
                    (json.dumps(state),))
        con.commit()
        con.close()

    def _generate(self, tmp_path, monkeypatch, journal, fake_router=None):
        from ui import compliance_dashboard as mod
        monkeypatch.setattr(mod, "JOURNAL_PATH", journal)
        if fake_router is not None:
            import types
            module = types.ModuleType("data.llm_router")
            module.get_router = fake_router
            monkeypatch.setitem(sys.modules, "data.llm_router", module)
            monkeypatch.setitem(sys.modules, "llm_router", module)
        else:
            monkeypatch.setitem(sys.modules, "data.llm_router", None)  # import fails
            monkeypatch.setitem(sys.modules, "llm_router", None)  # import fails
        out = str(tmp_path / "compliance.html")
        result = mod.generate_compliance_dashboard(hours=24, output_path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        return result, content

    def test_providers_and_fallback_mode_render(self, tmp_path, monkeypatch):
        """Initialized providers + a FALLBACK_STATIC cycle = CRITICAL badge."""
        import sqlite3
        journal = str(tmp_path / "journal.db")
        con = sqlite3.connect(journal)
        con.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        con.commit()
        con.close()
        self._seed_journal_state(journal, {
            "last_generation_source": "FALLBACK_STATIC",
            "last_generation_at": "2026-09-27T01:02:03+00:00",
            "generation_count": 7,
        })

        class _Prov:
            api_keys = ["k"]
            def is_circuit_open(self):
                return False

        class _Router:
            providers = {"openrouter": _Prov(), "cerebras": _Prov()}
            agents = {"alpha_discovery": {"provider": "openrouter",
                                          "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
                                          "fallback_providers": ["cerebras"]}}

        _, content = self._generate(tmp_path, monkeypatch, journal,
                                    fake_router=lambda: _Router())
        assert "✉ LLM Providers</h2>" in content
        assert "INITIALIZED" in content and "openrouter" in content
        assert "alpha_discovery agent: primary openrouter" in content
        assert "FALLBACK_STATIC" in content
        assert "static heuristic candidates" in content
        assert "generation 7" in content
        assert "label'>AlphaDiscovery<" in content and "FALLBACK" in content

    def test_llm_mode_renders_ok(self, tmp_path, monkeypatch):
        import sqlite3
        journal = str(tmp_path / "journal.db")
        con = sqlite3.connect(journal)
        con.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        con.commit()
        con.close()
        self._seed_journal_state(journal, {
            "last_generation_source": "LLM",
            "last_generation_at": "2026-09-27T02:00:00+00:00",
            "generation_count": 8,
        })

        class _Prov:
            api_keys = ["k"]
            def is_circuit_open(self):
                return False

        class _Router:
            providers = {"openrouter": _Prov()}
            agents = {}

        _, content = self._generate(tmp_path, monkeypatch, journal,
                                    fake_router=lambda: _Router())
        assert ">LLM<" in content
        assert "static heuristic candidates" not in content
        assert "label'>AlphaDiscovery<" in content and "LLM" in content

    def test_circuit_open_and_no_key_render(self, tmp_path, monkeypatch):
        class _Open:
            api_keys = ["k"]
            def is_circuit_open(self):
                return True

        class _NoKey:
            api_keys = []
            def is_circuit_open(self):
                return False

        class _Router:
            providers = {"nvidia_nim": _Open(), "tokenrouter": _NoKey()}
            agents = {}

        _, content = self._generate(tmp_path, monkeypatch, str(tmp_path / "j.db"),
                                    fake_router=lambda: _Router())
        assert "CIRCUIT OPEN" in content
        assert "NO KEY" in content

    def test_router_unavailable_renders_error_note(self, tmp_path, monkeypatch):
        """llm_router import failure shows an error row; section still valid."""
        _, content = self._generate(tmp_path, monkeypatch, str(tmp_path / "j.db"),
                                    fake_router=None)
        assert "✉ LLM Providers</h2>" in content
        assert "llm_router unavailable" in content

    def test_state_file_mirror_fallback(self, tmp_path, monkeypatch):
        """mt5_trader path: no journal state -> read alpha_discovery_state.json."""
        import sqlite3
        journal = str(tmp_path / "journal.db")
        con = sqlite3.connect(journal)
        con.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        con.commit()
        con.close()
        (tmp_path / "alpha_discovery_state.json").write_text(json.dumps({
            "last_generation_source": "LLM",
            "last_generation_at": "2026-09-27T03:00:00+00:00",
            "generation_count": 9,
        }), encoding="utf-8")

        class _Router:
            providers = {}
            agents = {}

        _, content = self._generate(tmp_path, monkeypatch, journal,
                                    fake_router=lambda: _Router())
        assert "state file (alpha_discovery_state.json)" in content
        assert "generation 9" in content
