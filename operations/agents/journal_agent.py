"""Journal maintenance executor (MaintenanceAgent).

Closes the live-loop gap where ``perform_database_maintenance`` existed only
behind a CLI flag: the 1.3 GB journal never got WAL checkpoints, integrity
verification, or telemetry pruning while trading. This executor runs cheap
maintenance on every cadence slice and the full prune on a daily cadence:

- ``checkpoint`` payload  -> WAL truncate + integrity check (seconds, safe
  on the live journal; no file copy).
- ``prune`` payload       -> prune_volatile_tables(apply=True): deletes
  stale/capped rows from NON-EVIDENCE telemetry tables only
  (harness_events, market_ticks, correlation_returns, metrics_equity).
  Evidence tables (order_intents, lifecycle events, snapshots, traces,
  operational_events, llm_requests) are never touched — journal doctrine.
- ``archive_outbox`` payload -> move terminal alert_outbox rows (SENT/EXPIRED)
  to alerts_archive.db (bounded batch; evidence preserved, live journal stays
  a hot queue).
- default payload         -> report-only prune preview (zero deletions).

Watchdog: every checkpoint also folds in ``report_consumer_incident`` — when
``outbox_health`` reports CRITICAL it raises the outbox's own
``ALERT_CONSUMER_STALLED`` incident (dampened), so a fully-dead alert
dispatcher still rings the bell even though it cannot self-report.

A multi-GB VACUUM/backup stays an explicit operator action
(``python mt5_trader.py db-maintenance``) — never run live.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("MaintenanceAgent")


def handle_journal_maintenance(payload: Dict[str, Any],
                               context: Any = None) -> Dict[str, Any]:
    """Dispatch journal maintenance according to the schedule payload."""
    kind = str((payload or {}).get("kind") or "report")
    active_journal = (
        (payload or {}).get("journal_path")
        or getattr(getattr(context, "operations", None), "db_path", None)
        or getattr(getattr(context, "store", None), "path", None)
        or getattr(context, "journal_path", None)
        or "trading_state.db"
    )
    operations = getattr(context, "operations", None)
    if operations is None:
        from operations.agent_operations import AgentOperations
        operations = AgentOperations(active_journal)

    if kind == "checkpoint":
        result = operations.checkpoint_and_verify()
        # Watchdog: raise/resolve the outbox's own stalled-consumer incident.
        # Dampened inside report_consumer_incident, so the hourly cadence
        # never spams; covers a dead dispatcher that cannot self-report.
        try:
            from operations import alert_outbox
            journal = active_journal
            result["outbox_incident"] = alert_outbox.report_consumer_incident(journal)
        except Exception as exc:
            result["outbox_incident"] = {"error": str(exc)[:200]}
        # Fold the volatile-telemetry prune into the hourly cadence as a
        # once-per-day action (day marker in the store, the journal's own
        # key/value state table).
        store = getattr(context, "store", None)
        today = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
        last = ""
        try:
            last = str(store.get_state("last_volatile_prune_date", "") or "") if store else ""
        except Exception:
            last = ""
        if store is not None and last != today:
            prune = operations.prune_volatile_tables(apply=True)
            try:
                store.set_state("last_volatile_prune_date", today)
            except Exception:
                pass
            result["daily_prune"] = {
                t: r.get("deleted", 0) for t, r in prune.get("tables", {}).items()
            }
    elif kind == "prune":
        result = operations.prune_volatile_tables(apply=True)
    elif kind == "archive_outbox":
        from operations import alert_outbox

        journal = active_journal
        result = alert_outbox.archive_terminal(
            journal,
            batch_limit=int((payload or {}).get("limit") or 2000),
        )
    else:
        result = operations.prune_volatile_tables(apply=False)

    logger.info("journal maintenance (%s): %s",
                kind, {k: v for k, v in result.items() if k != "tables"})
    return result


def handle_sizing_route_refresh(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Refresh the evidence snapshot that decides the sizing route.

    Scheduled by the MaintenanceAgent. It measures the contract's Phase 2
    exit criteria (broker-reconciled demo fills, an honest evidence spine, a
    granted rollout approval) and writes ``artifacts/sizing_route.json``.

    It never trades, never changes the route itself, and never writes to the
    journal's evidence rows: the measurement is SELECT-only. The route is
    decided from the snapshot, fail-closed.
    """
    journal_path = (
        (payload or {}).get("journal_path")
        or getattr(getattr(context, "operations", None), "db_path", None)
        or getattr(getattr(context, "store", None), "path", None)
        or getattr(context, "journal_path", None)
        or "trading_state.db"
    )
    artifacts_root = str((payload or {}).get("artifacts_root") or "artifacts")

    from core.sizing_route import refresh_route_snapshot, reset_cache, select_route

    try:
        snapshot = refresh_route_snapshot(journal_path, artifacts_root=artifacts_root)
        decision = select_route(snapshot)
        reset_cache()
    except Exception as exc:
        logger.warning("sizing route refresh failed: %s", exc)
        return {"route": "legacy", "error": str(exc)[:200]}

    result = {
        "route": decision.route,
        "reasons": list(decision.reasons),
        "evidence": decision.evidence,
        "errors": list(snapshot.get("errors") or []),
    }
    try:
        from operations import event_bridge

        event_bridge.emit("SIZING_ROUTE_REFRESHED", {
            "route": decision.route,
            "reasons": list(decision.reasons)[:5],
        }, actor="MaintenanceAgent")
    except Exception as exc:
        logger.debug("sizing route event emission failed: %s", exc)
    logger.info("sizing route refresh: %s (%s)", decision.route,
                "; ".join(decision.reasons[:3]) or "evidence complete")
    return result
