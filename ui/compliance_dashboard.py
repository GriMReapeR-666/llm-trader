"""Compliance Dashboard — Real-time κ audit alerts + S.Q task status.

Generates an HTML dashboard showing:
  - κ compliance audit events (state transitions, LLM calls, orders, alerts)
  - S.Q priority queue status (pending, running, completed, failed tasks)
  - Wash-sale, pattern-day-trading, and spoofing alerts
  - Chain integrity verification

Usage:
    from ui.compliance_dashboard import generate_compliance_dashboard
    path = generate_compliance_dashboard()
"""

from __future__ import annotations

import html
import logging
import os
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

OUTPUT_PATH = "compliance_dashboard.html"

# Journal state KV holding the market_health report (overridable for tests).
JOURNAL_PATH = os.getenv("TRADING_JOURNAL_PATH", "trading_state.db")


def _get_audit_events(
    hours: int = 24,
    event_type: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Fetch recent audit events from κ."""
    try:
        from core.compliance_audit import get_audit
        from datetime import datetime, timezone, timedelta
        audit = get_audit()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = audit.query(event_type=event_type, limit=limit)
        # Filter by time (timestamp may be ISO string or epoch float)
        result = []
        for e in events:
            ts = e.get("timestamp", 0)
            try:
                if isinstance(ts, str):
                    ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                elif isinstance(ts, (int, float)):
                    ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    continue
                if ts_dt >= cutoff:
                    result.append(e)
            except (ValueError, TypeError):
                result.append(e)  # include if unparseable
        return result
    except Exception as exc:
        log.debug("κ audit query failed: %s", exc)
        return []


def _get_audit_report(hours: int = 24) -> Dict[str, Any]:
    """Get full audit report from κ."""
    try:
        from core.compliance_audit import get_audit
        audit = get_audit()
        return audit.generate_report(hours=hours)
    except Exception as exc:
        log.debug("κ audit report failed: %s", exc)
        return {"total_events": 0, "by_type": {}, "alerts": []}


def _get_chain_integrity() -> Dict[str, Any]:
    """Verify hash chain integrity."""
    try:
        from core.compliance_audit import get_audit
        audit = get_audit()
        return audit.verify_chain()
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


def _get_outbox_health() -> Dict[str, Any]:
    """Read the operator alert-outbox block from the last market_health report.

    The market_health schedule persists ``last_market_health`` into the
    journal's state KV; its ``outbox_health`` block carries the PENDING/
    EXPIRED counts and status badge operators need at a glance.  Fail-soft:
    a missing journal or state key simply yields an empty section.
    """
    try:
        try:
            from trading_safety import TradingStore
        except ImportError:  # package import parity
            from core.trading_safety import TradingStore
        store = TradingStore(JOURNAL_PATH)
        raw = store.get_state("last_market_health", None)
        if not isinstance(raw, dict):
            return {}
        block = raw.get("outbox_health")
        out = dict(block) if isinstance(block, dict) else {}
        out["captured_at"] = raw.get("captured_at")
        return out
    except Exception as exc:
        log.debug("outbox health read failed: %s", exc)
        return {}


def _load_schedule_registry_intervals() -> Dict[str, int]:
    """schedule_name -> interval_seconds from the agent scheduler registry.

    Same source scripts/scheduler_tuning_report.py uses; imported without
    booting the scheduler. Empty on any failure so duty ratios degrade to
    'span too short' rather than raising.
    """
    try:
        root = Path(__file__).resolve().parent.parent
        for sub in ("operations", ""):
            candidate = root / sub / "agent_scheduler.py"
            if candidate.exists():
                parent = str(candidate.parent)
                if parent not in sys.path:
                    sys.path.insert(0, parent)
                from agent_scheduler import SCHEDULES  # noqa: E402
                return {s["name"]: int(s["interval"]) for s in SCHEDULES}
    except Exception as exc:
        log.debug("schedule registry read failed: %s", exc)
    return {}


def _default_percentile(values: list, q: float) -> float:
    """Element-pick percentile, identical semantics to the report's.

    Fallback for standalone renders where scripts/scheduler_tuning_report.py
    cannot be imported, so the dashboard's percentile definition still matches
    the report (and the executor's local copy) by construction.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def _load_report_percentile():
    """``_percentile`` from scripts/scheduler_tuning_report.py (fail-soft).

    Same path-import pattern as _load_schedule_registry_intervals: the
    dashboard must render standalone (no package context). Unifies the
    dashboard's p50/p95 with the tuning report's element-pick definition;
    the local fallback copy keeps identical semantics if the import fails.
    """
    try:
        root = Path(__file__).resolve().parent.parent
        candidate = root / "scripts" / "scheduler_tuning_report.py"
        if candidate.exists():
            parent = str(candidate.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            from scheduler_tuning_report import _percentile  # noqa: E402
            return _percentile
    except Exception as exc:
        log.debug("scheduler_tuning_report import failed: %s", exc)
    return _default_percentile


def _aggregate_overruns(con: sqlite3.Connection, cutoff: str) -> List[Dict[str, Any]]:
    """Per-schedule overrun aggregates over the journal's FULL row set.

    The previous SQL picked median/p95 rows via window functions and then
    aggregated over those survivors only, so ``n`` undercounted (37 journal
    overruns rendered as 2) and p50/p95 followed SQL median semantics that
    diverge from the tuning report on even counts. Rows are now counted and
    bucketed wholesale; percentiles come from the report's _percentile, so
    n, worst values, p50/p95 and last_at all describe the full population.
    """
    percentile = _load_report_percentile()
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in con.execute(
            """SELECT json_extract(payload,'$.schedule') schedule,
                      json_extract(payload,'$.elapsed_s') elapsed_s,
                      json_extract(payload,'$.over_by_s') over_by_s,
                      created_at
               FROM operational_events
               WHERE event_type='schedule_handler_overrun' AND created_at >= ?
               ORDER BY created_at""", (cutoff,)):
        name = row["schedule"]
        if not name:
            continue
        bucket = buckets.setdefault(name, {
            "schedule": name, "n": 0, "elapsed_s": [], "over_by_s": [], "last_at": None})
        bucket["n"] += 1
        for key in ("elapsed_s", "over_by_s"):
            try:
                bucket[key].append(float(row[key]))
            except (TypeError, ValueError):
                pass
        bucket["last_at"] = row["created_at"]  # rows ordered by created_at
    overruns: List[Dict[str, Any]] = []
    for name in sorted(buckets, key=lambda n: (-buckets[n]["n"], n)):
        item = buckets[name]
        elapsed: list = item.pop("elapsed_s")
        over_by: list = item.pop("over_by_s")
        item["worst_elapsed_s"] = max(elapsed) if elapsed else None
        item["worst_over_by_s"] = max(over_by) if over_by else None
        item["p50_elapsed_s"] = percentile(elapsed, 0.50) if elapsed else 0.0
        item["p95_elapsed_s"] = percentile(elapsed, 0.95) if elapsed else 0.0
        overruns.append(item)
    return overruns


def _get_scheduler_health(hours: int = 24) -> Dict[str, Any]:
    """Aggregate scheduler starvation telemetry from the operations journal.

    Read-only (``mode=ro`` URI) and fail-soft: a missing journal simply yields
    an empty block, matching the outbox-health loader. Returns per-schedule
    overrun counts, the LATEST episode per schedule for session skips (an
    open SKIPPING alert or its RECOVERED event, whichever is newer),
    abandoned-batch counts, and total scheduled dispatches so the renderer
    can distinguish "healthy" from "trader down".
    """
    try:
        con = sqlite3.connect(f"file:{JOURNAL_PATH}?mode=ro", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))).isoformat()
            overruns = _aggregate_overruns(con, cutoff)
            # Duty cycle, computed exactly like scripts/scheduler_tuning_report.py:
            # per-schedule schedule_due claims vs (first..last claim span) x
            # (3600 / registry interval). Ratios only when the span clears
            # --min-span-hours' default of 2.0h (span too short => None);
            # sub-0.8 is STARVED, sub-0.95 low. All-time spans include
            # trader-down hours, so ratios lower-bound real duty.
            duty = {}
            registry = _load_schedule_registry_intervals()
            for row in con.execute(
                """SELECT json_extract(payload,'$.schedule') schedule,
                          COUNT(*) claims,
                          MIN(created_at) first_at, MAX(created_at) last_at
                   FROM operational_events
                   WHERE event_type='schedule_due' AND created_at >= ?
                   GROUP BY json_extract(payload,'$.schedule')""", (cutoff,)):
                name = row["schedule"]
                if not name:
                    continue
                interval = registry.get(name, 0)
                try:
                    span_hours = (datetime.fromisoformat(row["last_at"])
                                  - datetime.fromisoformat(row["first_at"])).total_seconds() / 3600
                except (TypeError, ValueError):
                    span_hours = 0.0
                expected = ((span_hours * 3600) / interval
                            if interval and span_hours >= 2.0 else None)
                duty[name] = {
                    "claims": int(row["claims"] or 0),
                    "span_hours": round(span_hours, 1),
                    "interval_s": interval or None,
                    "expected": round(expected, 1) if expected else None,
                    "ratio": round(row["claims"] / expected, 3) if expected else None,
                }
            skips = []
            for row in con.execute(
                """SELECT schedule, status, silence_s, threshold_s, last_at FROM (
                       SELECT json_extract(payload,'$.schedule') schedule,
                              CASE event_type WHEN 'schedule_session_skip' THEN 'SKIPPING'
                                              ELSE 'RECOVERED' END status,
                              json_extract(payload,'$.silence_s') silence_s,
                              json_extract(payload,'$.threshold_s') threshold_s,
                              created_at last_at,
                              ROW_NUMBER() OVER (
                                  PARTITION BY json_extract(payload,'$.schedule')
                                  ORDER BY created_at DESC) rn
                       FROM operational_events
                       WHERE event_type IN ('schedule_session_skip','schedule_skips_cleared')
                         AND created_at >= ?
                   ) WHERE rn = 1 ORDER BY last_at DESC""", (cutoff,)):
                skips.append(dict(row))
            batches = con.execute(
                """SELECT COUNT(*), MAX(created_at) FROM operational_events
                   WHERE event_type='schedule_skipped' AND created_at >= ?""",
                (cutoff,)).fetchone()
            # Deferral sources (2026-09-27): the emitter tags every
            # schedule_skipped with a reason (currently 'wave_deadline');
            # legacy payloads pre-dating the field read as 'unattributed'.
            skipped_reasons = {}
            for row in con.execute(
                """SELECT COALESCE(json_extract(payload,'$.reason'),'unattributed') reason,
                          COUNT(*) n
                   FROM operational_events
                   WHERE event_type='schedule_skipped' AND created_at >= ?
                   GROUP BY reason ORDER BY n DESC""", (cutoff,)):
                skipped_reasons[str(row["reason"])] = int(row["n"] or 0)
            due = con.execute(
                """SELECT COUNT(*) FROM operational_events
                   WHERE event_type='schedule_due' AND created_at >= ?""",
                (cutoff,)).fetchone()[0]
            return {
                "hours": max(1, int(hours)),
                "overruns": overruns,
                "skips": skips,
                "abandoned_batches": int(batches[0] or 0),
                "last_abandoned_at": batches[1],
                "skipped_reasons": skipped_reasons,
                "due_events": int(due or 0),
                "duty": duty,
            }
        finally:
            con.close()
    except Exception as exc:
        log.debug("scheduler health read failed: %s", exc)
        return {}


def _load_router_snapshot() -> Dict[str, Any]:
    """Snapshot llm_router providers + the alpha_discovery agent (no network).

    ``get_router()`` only constructs provider clients from env keys; it does
    not call out. Fail-soft: an import failure (standalone dashboard without
    subpackages on sys.path) yields an error note instead of a crash.
    """
    # Reflect the boot environment: every entry point (trader, gui_api) loads
    # .env before rendering; a standalone render should too, or providers
    # would falsely read as keyless.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    try:
        from llm_router import get_router
    except ImportError:
        # Standalone rendering context: the trader boot adds every subpackage
        # to sys.path, a bare interpreter does not. One retry, then fail soft.
        try:
            data_dir = Path(__file__).resolve().parent.parent / "data"
            if str(data_dir) not in sys.path:
                sys.path.insert(0, str(data_dir))
            from llm_router import get_router
        except Exception as exc:
            return {"providers": [], "agents_error": f"llm_router unavailable: {str(exc)[:120]}"}
    except Exception as exc:
        return {"providers": [], "agents_error": f"llm_router unavailable: {str(exc)[:120]}"}
    try:
        router = get_router()
        providers = []
        for name, prov in getattr(router, "providers", {}).items():
            keys = list(getattr(prov, "api_keys", []) or [])
            circuit_open = None
            try:
                circuit_open = bool(prov.is_circuit_open())
            except Exception:
                circuit_open = None
            providers.append({
                "name": str(name),
                "enabled": bool(keys),
                "keys": len(keys),
                "circuit_open": circuit_open,
            })
        agent = getattr(router, "agents", {}).get("alpha_discovery") or {}
        if isinstance(agent, dict):
            agent_cfg = {"primary": agent.get("provider"),
                         "model": agent.get("model"),
                         "fallbacks": list(agent.get("fallback_providers") or [])}
        else:
            agent_cfg = {"primary": getattr(agent, "provider", None),
                         "model": getattr(agent, "model", None),
                         "fallbacks": list(getattr(agent, "fallback_providers", []) or [])}
        return {"providers": providers, "agents_error": "", "alpha_discovery_agent": agent_cfg}
    except Exception as exc:
        return {"providers": [], "agents_error": f"router snapshot failed: {str(exc)[:120]}"}


def _load_alpha_generation_mode() -> Dict[str, Any]:
    """Where AlphaDiscovery's LAST cycle got its candidates from.

    Reads the persisted experience memory: journal ``state`` KV first
    (production auto_quant_tools attaches the TradingStore), then the
    ``alpha_discovery_state.json`` file mirror (mt5_trader path). Purely
    read-only via a ``mode=ro`` URI — never constructs a TradingStore, so a
    missing journal cannot be created by a dashboard render.
    """
    out: Dict[str, Any] = {"source": "unknown", "at": "", "generation_count": None,
                           "origin": "unavailable"}
    try:
        con = sqlite3.connect(f"file:{JOURNAL_PATH}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute(
                "SELECT value FROM state WHERE key='alpha_discovery_state'").fetchone()
            if row:
                data = json.loads(row[0] or "{}")
                if isinstance(data, dict) and data.get("last_generation_source"):
                    out = {"source": str(data.get("last_generation_source")),
                           "at": str(data.get("last_generation_at") or ""),
                           "generation_count": data.get("generation_count"),
                           "origin": "journal store"}
                    return out
        finally:
            con.close()
    except Exception:
        pass
    try:
        path = Path(JOURNAL_PATH).parent / "alpha_discovery_state.json"
        if not path.exists():
            path = Path("alpha_discovery_state.json")
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict) and data.get("last_generation_source"):
                out = {"source": str(data.get("last_generation_source")),
                       "at": str(data.get("last_generation_at") or ""),
                       "generation_count": data.get("generation_count"),
                       "origin": f"state file ({path.name})"}
    except Exception as exc:
        log.debug("alpha generation mode read failed: %s", exc)
    return out


def _get_llm_provider_status() -> Dict[str, Any]:
    """Aggregate LLM provider init state + AlphaDiscovery generation mode."""
    snapshot = _load_router_snapshot()
    mode = _load_alpha_generation_mode()
    snapshot["mode"] = mode
    return snapshot


def _outbox_section(outbox: Dict[str, Any]) -> str:
    """Render the outbox_health block (badge + PENDING/EXPIRED counts)."""
    if not outbox or not outbox.get("status"):
        return ""
    status = str(outbox.get("status", "OK")).upper()
    badge_cls = {"CRITICAL": "badge-crit", "ERROR": "badge-crit",
                 "WARN": "badge-warn"}.get(status, "badge-ok")
    counts = outbox.get("counts") if isinstance(outbox.get("counts"), dict) else {}
    oldest = outbox.get("oldest_pending_at")
    captured = str(outbox.get("captured_at") or "unknown")
    lines = [
        "<div class='section'>",
        "<h2>✉ Operator Alert Outbox</h2>",
        "<table>",
        "<tr><th>Metric</th><th>Value</th></tr>",
        f"<tr><td>Status</td><td><span class='badge {badge_cls}'>{html.escape(status)}</span></td></tr>",
        f"<tr><td>PENDING</td><td>{counts.get('PENDING', 0)}</td></tr>",
        f"<tr><td>CLAIMED</td><td>{counts.get('CLAIMED', 0)}</td></tr>",
        f"<tr><td>SENT</td><td>{counts.get('SENT', 0)}</td></tr>",
        f"<tr><td>EXPIRED</td><td>{counts.get('EXPIRED', 0)}</td></tr>",
        f"<tr><td>Oldest pending</td><td>{html.escape(str(oldest) if oldest else '—')}</td></tr>",
        f"<tr><td>Report captured at</td><td>{html.escape(captured)}</td></tr>",
        "</table>",
        "</div>",
    ]
    return "\n".join(lines)


def _scheduler_section(sched: Dict[str, Any]) -> str:
    """Render scheduler starvation telemetry (skips, overruns, abandoned batches).

    Empty block -> empty string (no journal / pre-instrumentation journal),
    matching the outbox section's absence contract. Zero activity renders as
    an explicit OK badge; any open SKIPPING episode renders CRITICAL.
    """
    if not sched:
        return ""
    hours = int(sched.get("hours") or 24)
    due_events = int(sched.get("due_events") or 0)
    status = "OK"
    badge_cls = "badge-ok"
    if any(s.get("status") == "SKIPPING" for s in sched.get("skips", [])):
        status, badge_cls = "CRITICAL", "badge-crit"
    elif sched.get("overruns") or sched.get("abandoned_batches"):
        status, badge_cls = "WARN", "badge-warn"
    lines = [
        "<div class='section'>",
        "<h2>◷ Schedule Starvation</h2>",
        f"<p>Status: <span class='badge {badge_cls}'>{status}</span> "
        f"<span class='muted'>| window {hours}h | {due_events} scheduled dispatches</span></p>",
        "<table>",
    ]
    skip_rows = sched.get("skips") or []
    if skip_rows:
        lines.append("<tr><th>Schedule</th><th>Session-skip episode</th>"
                     "<th>Silence</th><th>Threshold</th><th>Last event</th></tr>")
        for row in skip_rows:
            episode_cls = "crit" if row.get("status") == "SKIPPING" else "ok"
            lines.append(
                f"<tr><td>{html.escape(str(row.get('schedule') or ''))}</td>"
                f"<td class='{episode_cls}'>{html.escape(str(row.get('status') or ''))}</td>"
                f"<td>{html.escape(str(row.get('silence_s') if row.get('silence_s') is not None else '—'))}s</td>"
                f"<td>{html.escape(str(row.get('threshold_s') if row.get('threshold_s') is not None else '—'))}s</td>"
                f"<td>{html.escape(str(row.get('last_at') or '')[:19])}</td></tr>")
    overrun_rows = sched.get("overruns") or []
    if overrun_rows:
        lines.append("<tr><th>Schedule</th><th>Handler overruns</th>"
                     "<th>Worst elapsed</th><th>Worst over-by</th>"
                     "<th>p50</th><th>p95</th><th>Last overrun</th></tr>")
        for row in overrun_rows:
            lines.append(
                f"<tr><td>{html.escape(str(row.get('schedule') or ''))}</td>"
                f"<td>{html.escape(str(row.get('n') or 0))}</td>"
                f"<td>{float(row.get('worst_elapsed_s') or 0):.1f}s</td>"
                f"<td>{float(row.get('worst_over_by_s') or 0):.1f}s</td>"
                f"<td>{float(row.get('p50_elapsed_s') or 0):.1f}s</td>"
                f"<td>{float(row.get('p95_elapsed_s') or 0):.1f}s</td>"
                f"<td>{html.escape(str(row.get('last_at') or '')[:19])}</td></tr>")
    lines.append("</table>")
    # Duty cycle: claims vs cadence-expected over the claims' own span (the
    # tuning report's computation, verbatim semantics; 2.0h span floor and
    # the report's STARVED/low thresholds).
    duty_rows = sched.get("duty") or {}
    if duty_rows:
        lines.extend([
            "<h2>◷ Schedule Duty Cycle</h2>",
            "<table>",
            "<tr><th>Schedule</th><th>Claims</th><th>Span</th><th>Interval</th>"
            "<th>Expected</th><th>Duty</th><th>Verdict</th></tr>",
        ])
        for name in sorted(duty_rows,
                           key=lambda k: (duty_rows[k].get("ratio") is None,
                                          duty_rows[k].get("ratio") or 1.0)):
            item = duty_rows[name]
            ratio = item.get("ratio")
            if ratio is None:
                ratio_html, ratio_cls = "—", "muted"
                verdict_html, verdict_cls = "span too short", "muted"
            else:
                pct = ratio * 100
                if ratio < 0.8:
                    verdict_html, verdict_cls, ratio_cls = "STARVED", "crit", "crit"
                elif ratio < 0.95:
                    verdict_html, verdict_cls, ratio_cls = "low", "warn", "warn"
                else:
                    verdict_html, verdict_cls, ratio_cls = "healthy", "ok", "ok"
                ratio_html = f"{pct:.0f}%"
            interval = item.get("interval_s")
            expected = item.get("expected")
            lines.append(
                f"<tr><td>{html.escape(str(name))}</td>"
                f"<td>{item.get('claims', 0)}</td>"
                f"<td>{html.escape(str(item.get('span_hours') or 0))}h</td>"
                f"<td>{f'{interval}s' if interval else '—'}</td>"
                f"<td>{expected if expected is not None else '—'}</td>"
                f"<td class='{ratio_cls}'>{ratio_html}</td>"
                f"<td class='{verdict_cls}'>{verdict_html}</td></tr>")
        lines.append("</table>")
        lines.append(
            "<p class='muted'>Duty = claims vs cadence-expected over the "
            "claims' own span (scheduler tuning report computation); spans "
            "under 2h render without a ratio. All-time spans include "
            "trader-down hours, so ratios lower-bound real duty.</p>")
    abandoned = int(sched.get("abandoned_batches") or 0)
    if abandoned:
        reasons = sched.get("skipped_reasons") or {}
        reason_note = ", ".join(f"{r} x{n}" for r, n in
                                sorted(reasons.items(), key=lambda kv: -kv[1])) \
            or "reason unattributed"
        lines.append(
            f"<p class='warn'>{abandoned} abandoned batch(es) in window "
            f"(last {html.escape(str(sched.get('last_abandoned_at') or '')[:19])}) — "
            f"deferral reasons: {html.escape(reason_note)}; "
            "released claims keep their cadence turn (claims never advance it).</p>")
    elif not skip_rows and not overrun_rows:
        lines.append(f"<p class='ok'>No starvation telemetry in the last {hours}h "
                     f"({due_events} scheduled dispatches).</p>")
    lines.append("</div>")
    return "\n".join(lines)


def _llm_provider_section(status: Dict[str, Any]) -> str:
    """Render LLM provider init state + AlphaDiscovery generation mode.

    Renders whenever providers exist (initialized from env keys) OR an error
    note is available; stays absent on fully unavailable environments.
    Badge: FALLBACK_STATIC -> CRITICAL, unknown -> WARN, LLM/EXPLICIT_FN -> OK.
    """
    providers = status.get("providers") or []
    mode = status.get("mode") or {}
    agents_error = str(status.get("agents_error") or "")
    mode_known = str(mode.get("source") or "unknown") not in ("unknown", "")
    if not providers and not agents_error and not mode_known:
        return ""
    source = str(mode.get("source") or "unknown")
    badge_map = {"FALLBACK_STATIC": ("badge-crit", "crit"),
                 "unknown": ("badge-warn", "warn")}
    badge_cls, source_cls = badge_map.get(source, ("badge-ok", "ok"))
    lines = [
        "<div class='section'>",
        "<h2>✉ LLM Providers</h2>",
        "<table>",
        "<tr><th>Provider</th><th>Status</th><th>Keys</th><th>Circuit</th></tr>",
    ]
    if providers:
        for row in providers:
            circuit = row.get("circuit_open")
            if row.get("enabled"):
                state_html, state_cls = "INITIALIZED", "ok"
            else:
                state_html, state_cls = "NO KEY", "warn"
            if circuit is True:
                state_html, state_cls = "CIRCUIT OPEN", "crit"
            circuit_html = "open" if circuit is True else ("closed" if circuit is False else "—")
            lines.append(
                f"<tr><td>{html.escape(str(row.get('name') or ''))}</td>"
                f"<td class='{state_cls}'>{state_html}</td>"
                f"<td>{int(row.get('keys') or 0)}</td>"
                f"<td>{circuit_html}</td></tr>")
    else:
        lines.append(f"<tr><td colspan='4' class='warn'>{html.escape(agents_error)}</td></tr>")
    lines.append("</table>")
    agent = status.get("alpha_discovery_agent") or {}
    if agent.get("primary"):
        model = html.escape(str(agent.get("model") or ""))
        fallbacks = ", ".join(str(f) for f in (agent.get("fallbacks") or [])) or "none"
        lines.append(
            f"<p class='muted'>alpha_discovery agent: primary "
            f"{html.escape(str(agent['primary']))} ({model}); failover: "
            f"{html.escape(fallbacks)}</p>")
    at = str(mode.get("at") or "")
    origin = html.escape(str(mode.get("origin") or ""))
    gen_count = mode.get("generation_count")
    gen_note = f" | generation {gen_count}" if isinstance(gen_count, int) else ""
    lines.append(
        f"<p>AlphaDiscovery generation: <span class='badge {badge_cls}'>"
        f"{html.escape(source)}</span> <span class='{source_cls}'>"
        f"{'(static heuristic candidates — NOT LLM)' if source == 'FALLBACK_STATIC' else ''}"
        f"</span> <span class='muted'>| {(html.escape(at) or 'no cycle recorded')[:19]}"
        f"{gen_note} | source: {origin}</span></p>")
    lines.append("</div>")
    return "\n".join(lines)


def _get_task_queue_status() -> Dict[str, Any]:
    """Get S.Q priority queue status."""
    try:
        from core.task_queue import TaskQueue
        # Try to load from persisted state
        state_file = "task_queue_state.json"
        if os.path.exists(state_file):
            import json
            with open(state_file) as f:
                return json.load(f)
        return {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    except Exception as exc:
        log.debug("S.Q status query failed: %s", exc)
        return {}


def _severity_color(severity: str) -> str:
    """Map severity to CSS color."""
    return {
        "CRITICAL": "#dc3545",
        "WARNING": "#ffc107",
        "INFO": "#17a2b8",
        "SUCCESS": "#28a745",
    }.get(severity, "#6c757d")


def _event_type_icon(event_type: str) -> str:
    """Map event type to emoji."""
    return {
        "STATE_TRANSITION": "↻",
        "LLM_CALL": "",
        "LLM_DECISION": "",
        "ORDER_SUBMITTED": "",
        "ORDER_FILLED": "✔",
        "ORDER_CANCELLED": "",
        "ORDER_REJECTED": "✖",
        "POSITION_OPENED": "",
        "POSITION_CLOSED": "",
        "RISK_DECISION": "▣",
        "CIRCUIT_BREAKER": "⚠",
        "GUARDIAN_TRIP": "▣",
        "KILL_SWITCH": "●",
        "GATE_PASSED": "✔",
        "GATE_FAILED": "✖",
        "WASH_SALE_ALERT": "⚠",
        "PATTERN_DAY_ALERT": "",
        "SPOOFING_ALERT": "⚠",
    }.get(event_type, "≡")


def generate_compliance_dashboard(
    hours: int = 24,
    output_path: str = OUTPUT_PATH,
) -> str:
    """Generate the compliance dashboard HTML file.

    Returns the output path on success, empty string on failure.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report = _get_audit_report(hours)
    chain = _get_chain_integrity()
    events = _get_audit_events(hours=hours, limit=100)
    sq_status = _get_task_queue_status()
    outbox = _get_outbox_health()
    sched = _get_scheduler_health(hours)
    llm_status = _get_llm_provider_status()

    # AlphaDiscovery mode for the summary card: fallback is the alarming state.
    mode_source = str((llm_status.get("mode") or {}).get("source") or "unknown")
    if mode_source == "FALLBACK_STATIC":
        alpha_value, alpha_icon = "FALLBACK", "⚠"
    elif mode_source == "unknown":
        alpha_value, alpha_icon = "unknown", "≡"
    else:
        alpha_value, alpha_icon = "LLM", "✔"

    # Starvation summary card: open session-skip episodes dominate (CRITICAL),
    # then overruns, then abandoned batches; silence means a healthy or idle
    # scheduler (the section differentiates which).
    skip_open = sum(1 for s in sched.get("skips", []) if s.get("status") == "SKIPPING")
    overrun_total = sum(int(r.get("n") or 0) for r in sched.get("overruns", []))
    if skip_open:
        starv_value, starv_icon = f"{skip_open} SKIPPING", "⚠"
    elif overrun_total:
        starv_value, starv_icon = str(overrun_total), "⚠"
    elif sched:
        starv_value, starv_icon = "OK", "✔"
    else:
        starv_value, starv_icon = "—", "≡"

    # Build HTML
    lines = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Compliance Dashboard</title>",
        "<style>",
        _css(),
        "</style>",
        "</head>",
        "<body>",
        f"<h1>▣ Compliance Dashboard</h1>",
        f"<p class='subtitle'>Generated: {now} | Period: {hours}h</p>",
        "",
        # --- Summary Cards ---
        "<div class='cards'>",
        _summary_card("Total Events", str(report.get("total_events", 0)), "↻"),
        _summary_card("Chain Valid", "✔" if chain.get("valid") else "", "▣"),
        _summary_card(
            "Alerts",
            str(len(report.get("alerts", []))),
            "⚠",
        ),
        _summary_card(
            "S.Q Tasks",
            str(sq_status.get("pending", "?")),
            "≡",
        ),
        _summary_card("Schedule Starvation", starv_value, starv_icon),
        _summary_card("AlphaDiscovery", alpha_value, alpha_icon),
        "</div>",
        "",
        # --- Chain Integrity ---
        "<div class='section'>",
        "<h2>▣ Hash Chain Integrity</h2>",
        f"<p>Status: <span class='{'ok' if chain.get('valid') else 'crit'}'>"
        f"{'VALID' if chain.get('valid') else 'BROKEN'}</span></p>",
        f"<p>Entries verified: {chain.get('entries_verified', 0)}</p>",
        f"<p>Duration: {chain.get('duration_ms', 0):.1f}ms</p>",
        "</div>",
        "",
        # --- Alerts ---
        _alerts_section(report.get("alerts", [])),
        "",
        # --- Operator Alert Outbox (PENDING/EXPIRED + status badge) ---
        _outbox_section(outbox),
        "",
        # --- Scheduler Starvation (session skips, overruns, abandoned batches) ---
        _scheduler_section(sched),
        "",
        # --- LLM Providers (init state + AlphaDiscovery generation mode) ---
        _llm_provider_section(llm_status),
        "",
        # --- Event Breakdown ---
        _event_breakdown(report.get("by_type", {})),
        "",
        # --- S.Q Status ---
        _sq_status_section(sq_status),
        "",
        # --- Recent Events ---
        _events_table(events),
        "",
        "</body>",
        "</html>",
    ]

    html_content = "\n".join(lines)
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(html_content)
        log.info("Compliance dashboard generated: %s", output_path)
        return output_path
    except Exception as exc:
        log.warning("Failed to write compliance dashboard: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _css() -> str:
    return """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #0d1117; color: #c9d1d9; padding: 20px; }
    h1 { color: #58a6ff; margin-bottom: 5px; }
    h2 { color: #58a6ff; margin: 15px 0 8px; font-size: 1.1em; }
    .subtitle { color: #8b949e; font-size: 0.9em; margin-bottom: 15px; }
    .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
            padding: 15px 20px; min-width: 140px; text-align: center; }
    .card .num { font-size: 1.8em; font-weight: bold; color: #58a6ff; }
    .card .label { font-size: 0.8em; color: #8b949e; margin-top: 4px; }
    .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
               padding: 15px 20px; margin-bottom: 15px; }
    .ok { color: #28a745; font-weight: bold; }
    .warn { color: #ffc107; font-weight: bold; }
    .crit { color: #dc3545; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #21262d; }
    th { color: #8b949e; font-weight: 600; position: sticky; top: 0; background: #161b22; }
    tr:hover { background: #1c2128; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
             font-size: 0.75em; font-weight: 600; }
    .badge-crit { background: #dc354522; color: #dc3545; border: 1px solid #dc354544; }
    .badge-warn { background: #ffc10722; color: #ffc107; border: 1px solid #ffc10744; }
    .badge-info { background: #17a2b822; color: #17a2b8; border: 1px solid #17a2b844; }
    .badge-ok { background: #28a74522; color: #28a745; border: 1px solid #28a74544; }
    .truncate { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .muted { color: #8b949e; }
    """


def _summary_card(label: str, value: str, icon: str) -> str:
    return (
        f"<div class='card'>"
        f"<div class='num'>{icon} {html.escape(str(value))}</div>"
        f"<div class='label'>{html.escape(label)}</div>"
        f"</div>"
    )


def _alerts_section(alerts: List[Dict]) -> str:
    if not alerts:
        return ""
    lines = [
        "<div class='section'>",
        "<h2>⚠ Compliance Alerts</h2>",
        "<table>",
        "<tr><th>Severity</th><th>Type</th><th>Details</th><th>Time</th></tr>",
    ]
    for a in alerts[:50]:
        sev = a.get("severity", "INFO")
        badge_cls = {"CRITICAL": "badge-crit", "WARNING": "badge-warn"}.get(sev, "badge-info")
        ts = a.get("timestamp", "")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
        lines.append(
            f"<tr>"
            f"<td><span class='badge {badge_cls}'>{html.escape(sev)}</span></td>"
            f"<td>{html.escape(a.get('type', a.get('code', '')))}</td>"
            f"<td class='truncate'>{html.escape(str(a.get('details', a.get('message', '')))[:200])}</td>"
            f"<td>{html.escape(str(ts))}</td>"
            f"</tr>"
        )
    lines.extend(["</table>", "</div>"])
    return "\n".join(lines)


def _event_breakdown(by_type: Dict[str, int]) -> str:
    if not by_type:
        return ""
    lines = [
        "<div class='section'>",
        "<h2>▣ Event Breakdown</h2>",
        "<table>",
        "<tr><th>Event Type</th><th>Count</th><th>Share</th></tr>",
    ]
    total = sum(by_type.values()) or 1
    for etype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        icon = _event_type_icon(etype)
        pct = count / total * 100
        lines.append(
            f"<tr><td>{icon} {html.escape(etype)}</td>"
            f"<td>{count}</td><td>{pct:.1f}%</td></tr>"
        )
    lines.extend(["</table>", "</div>"])
    return "\n".join(lines)


def _sq_status_section(sq: Dict) -> str:
    if not sq:
        return ""
    lines = [
        "<div class='section'>",
        "<h2>≡ S.Q Priority Queue Status</h2>",
        "<table>",
        "<tr><th>Metric</th><th>Value</th></tr>",
    ]
    for key in ["pending", "running", "completed", "failed", "total_tasks"]:
        if key in sq:
            lines.append(f"<tr><td>{html.escape(key)}</td><td>{sq[key]}</td></tr>")
    lines.extend(["</table>", "</div>"])
    return "\n".join(lines)


def _events_table(events: List[Dict]) -> str:
    if not events:
        return (
            "<div class='section'>"
            "<h2>≡ Recent Audit Events</h2>"
            "<p>No events recorded in this period.</p>"
            "</div>"
        )
    lines = [
        "<div class='section'>",
        "<h2>≡ Recent Audit Events</h2>",
        "<table>",
        "<tr><th>Time</th><th>Type</th><th>Agent</th><th>Symbol</th><th>Details</th></tr>",
    ]
    for e in events[:100]:
        icon = _event_type_icon(e.get("event_type", ""))
        ts = e.get("timestamp", "")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
        payload = e.get("payload", {})
        if isinstance(payload, str):
            payload_str = payload[:150]
        else:
            import json as _json
            payload_str = _json.dumps(payload, default=str)[:150]
        lines.append(
            f"<tr>"
            f"<td>{html.escape(str(ts))}</td>"
            f"<td>{icon} {html.escape(e.get('event_type', ''))}</td>"
            f"<td>{html.escape(e.get('agent', ''))}</td>"
            f"<td>{html.escape(e.get('symbol', ''))}</td>"
            f"<td class='truncate'>{html.escape(payload_str)}</td>"
            f"</tr>"
        )
    lines.extend(["</table>", "</div>"])
    return "\n".join(lines)
