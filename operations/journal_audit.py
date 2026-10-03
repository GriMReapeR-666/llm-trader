"""Offline integrity and baseline metrics for the durable trading journal.

This module never connects to MT5 and never changes execution state. It is the
required gate before promoting any HMM/GARCH/Kelly/RL artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import hashlib
import random
import sqlite3
import os
import statistics
from typing import Any, Dict, List, Optional


def _con(path: str):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def audit(path: str = "trading_state.db") -> Dict[str, Any]:
    result: Dict[str, Any] = {"database": path, "exists": False, "passed": False, "checks": []}
    if not os.path.exists(path):
        result["checks"].append({"name": "database exists", "passed": False, "detail": "journal database has not been created"})
        return result
    try:
        con = _con(path)
        result["exists"] = True
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"proposals", "trades"}
        missing = sorted(required - tables)
        if missing:
            result["checks"].append({"name": "required tables", "passed": False, "detail": missing})
            return result
        checks: List[Dict[str, Any]] = []
        duplicate_tickets = con.execute("SELECT ticket,COUNT(*) n FROM trades GROUP BY ticket HAVING n > 1").fetchall()
        checks.append({"name": "unique tickets", "passed": not duplicate_tickets, "detail": [dict(r) for r in duplicate_tickets]})
        orphan_labels = []
        if "trade_labels" in tables:
            orphan_labels = con.execute("SELECT l.ticket FROM trade_labels l LEFT JOIN trades t ON t.ticket=l.ticket WHERE t.ticket IS NULL").fetchall()
        checks.append({"name": "labels reference trades", "passed": not orphan_labels, "detail": [r["ticket"] for r in orphan_labels]})
        if "setup_labels" in tables:
            malformed_setup_labels = con.execute("""SELECT subject_key FROM setup_labels
                WHERE subject_type NOT IN ('setup_instance','order_intent')
                   OR subject_key IS NULL OR subject_key=''
                   OR label IS NULL OR label=''
                   OR rationale IS NULL OR rationale=''
                   OR label_rule IS NULL OR label_rule=''""").fetchall()
            checks.append({"name": "non-trade setup labels are attributable", "passed": not malformed_setup_labels,
                           "detail": [r["subject_key"] for r in malformed_setup_labels]})
            bad_review_status = con.execute("SELECT subject_key FROM setup_labels WHERE review_status NOT IN ('AUTO','APPROVED','RETIRED')").fetchall()
            checks.append({"name": "setup-label review states are valid", "passed": not bad_review_status,
                           "detail": [r["subject_key"] for r in bad_review_status]})
        open_without_magic = con.execute("SELECT ticket FROM trades WHERE magic IS NULL OR magic=0").fetchall()
        checks.append({"name": "all trades tagged", "passed": not open_without_magic, "detail": [r["ticket"] for r in open_without_magic]})
        invalid_closes = con.execute("SELECT ticket FROM trades WHERE closed_at IS NOT NULL AND (pnl IS NULL OR exit_price IS NULL)").fetchall()
        checks.append({"name": "closed trades have outcome", "passed": not invalid_closes, "detail": [r["ticket"] for r in invalid_closes]})
        missing_payload = con.execute("SELECT ticket FROM trades WHERE payload IS NULL OR payload='' ").fetchall()
        checks.append({"name": "trades have feature payload", "passed": not missing_payload, "detail": [r["ticket"] for r in missing_payload]})
        missing_artifacts = []
        for row in con.execute("SELECT ticket,payload FROM trades WHERE payload IS NOT NULL AND payload<>''").fetchall():
            try:
                payload = json.loads(row["payload"] or "{}")
                features = payload.get("proposal_features") or payload.get("features") or {}
                versions = features.get("artifact_versions") or {}
                if features.get("feature_schema_version") and not all(versions.get(name) for name in ("policy_version", "strategy_version", "feature_version", "playbook_version", "prompt_version", "model_version")):
                    missing_artifacts.append(row["ticket"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        checks.append({"name": "new proposals carry versioned artifacts", "passed": not missing_artifacts, "detail": missing_artifacts})
        trade_columns = {r["name"] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
        if "setup_id" in trade_columns:
            missing_setup = con.execute("SELECT ticket FROM trades WHERE setup_id IS NULL AND opened_at >= '2026-08-05T00:00:00+00:00'").fetchall()
            checks.append({"name": "new trades have deterministic setup ID", "passed": not missing_setup, "detail": [r["ticket"] for r in missing_setup]})
        if "order_intents" in tables:
            duplicate_intents = con.execute("SELECT intent_key,COUNT(*) n FROM order_intents GROUP BY intent_key HAVING n > 1").fetchall()
            checks.append({"name": "unique order intents", "passed": not duplicate_intents, "detail": [dict(r) for r in duplicate_intents]})
            unresolved = con.execute("SELECT intent_key,status FROM order_intents WHERE status IN ('CREATED','ERROR')").fetchall()
            checks.append({"name": "order intents resolved or reviewable", "passed": True, "detail": [dict(r) for r in unresolved]})
        if "data_snapshots" in tables:
            invalid_snapshots = con.execute("SELECT snapshot_id FROM data_snapshots WHERE source_hash IS NULL OR payload IS NULL OR payload='' ").fetchall()
            checks.append({"name": "point-in-time snapshots have payload and hash", "passed": not invalid_snapshots, "detail": [r["snapshot_id"] for r in invalid_snapshots]})
        if "verified_memories" in tables:
            orphan_memory = con.execute("SELECT id FROM verified_memories WHERE source_id IS NULL OR source_id='' ").fetchall()
            checks.append({"name": "verified memories have source links", "passed": not orphan_memory, "detail": [r["id"] for r in orphan_memory]})
        if "trade_lifecycle_events" in tables:
            malformed_events = con.execute("SELECT id FROM trade_lifecycle_events WHERE event_type='' OR actor='' OR payload IS NULL OR payload='' ").fetchall()
            checks.append({"name": "lifecycle events are attributable", "passed": not malformed_events, "detail": [r["id"] for r in malformed_events]})
        if "broker_execution_reports" in tables:
            malformed_reports = con.execute("SELECT id FROM broker_execution_reports WHERE event_type='' OR payload IS NULL OR payload='' ").fetchall()
            checks.append({"name": "broker execution reports are attributable", "passed": not malformed_reports, "detail": [r["id"] for r in malformed_reports]})
        if "position_observations" in tables:
            invalid_marks = con.execute("SELECT id FROM position_observations WHERE price <= 0").fetchall()
            checks.append({"name": "position observations have valid marks", "passed": not invalid_marks, "detail": [r["id"] for r in invalid_marks]})
            mark_columns = {r["name"] for r in con.execute("PRAGMA table_info(position_observations)").fetchall()}
            if {"source", "mark_quality", "tick_age_ms"}.issubset(mark_columns):
                bad_quality = con.execute("""SELECT id FROM position_observations
                    WHERE observed_at >= '2026-08-05T00:00:00+00:00'
                    AND (source IS NULL OR source='' OR mark_quality NOT IN ('BROKER_FRESH','BROKER_EXECUTION','STALE'))""").fetchall()
                checks.append({"name": "new position marks have source and quality", "passed": not bad_quality, "detail": [r["id"] for r in bad_quality]})
        if "market_ticks" in tables:
            malformed_ticks = con.execute("""SELECT id FROM market_ticks
                WHERE quality NOT IN ('FRESH','STALE','MISSING_OR_INVALID','UNKNOWN')
                   OR (quality='FRESH' AND (bid IS NULL OR ask IS NULL OR bid <= 0 OR ask < bid))""").fetchall()
            checks.append({"name": "market tick observations preserve quality", "passed": not malformed_ticks,
                           "detail": [r["id"] for r in malformed_ticks]})
        if "daily_reconciliations" in tables:
            malformed_reconciliations = con.execute("SELECT period_start FROM daily_reconciliations WHERE status NOT IN ('CLEAN','ATTENTION') OR report IS NULL OR report='' ").fetchall()
            checks.append({"name": "daily reconciliations are attributable", "passed": not malformed_reconciliations, "detail": [r["period_start"] for r in malformed_reconciliations]})
        if "setup_instances" in tables:
            valid_states = ("CANDIDATE", "VALIDATED", "REJECTED", "ACCEPTED", "FILLED", "MISSED", "INVALIDATED", "CLOSED")
            malformed_setups = con.execute("SELECT id FROM setup_instances WHERE setup_key='' OR setup_id='' OR state NOT IN (%s)" % ",".join("?" for _ in valid_states), valid_states).fetchall()
            checks.append({"name": "setup instances have valid deterministic states", "passed": not malformed_setups, "detail": [r["id"] for r in malformed_setups]})
        if "manual_activity" in tables:
            malformed_manual = con.execute("SELECT id FROM manual_activity WHERE action='' OR payload IS NULL OR payload='' ").fetchall()
            checks.append({"name": "manual activity is separate and attributable", "passed": not malformed_manual, "detail": [r["id"] for r in malformed_manual]})
        if "model_registry" in tables:
            # The registry carries both research-model states (CANDIDATE/
            # APPROVED/REJECTED/RETIRED/ROLLED_BACK) and strategy-lifecycle
            # states written by operations/strategy_lifecycle.py and
            # research/auto_promotion.py (VALIDATING/PAPER_PROVEN/
            # DEMO_PROVEN/READY_FOR_DEPLOYMENT/DEPLOYED/DECAYING/FROZEN).
            # An allowlist missing either vocabulary flags the system's own
            # legitimate rows as malformed.
            model_registry_valid_statuses = (
                'CANDIDATE', 'APPROVED', 'REJECTED', 'RETIRED', 'ROLLED_BACK',
                'VALIDATING', 'PAPER_PROVEN', 'DEMO_PROVEN',
                'READY_FOR_DEPLOYMENT', 'DEPLOYED', 'DECAYING', 'FROZEN',
            )
            malformed_models = con.execute(f"""SELECT id FROM model_registry
                WHERE model_key='' OR version='' OR kind='' OR status NOT IN
                ({','.join("?" for _ in model_registry_valid_statuses)})
                OR metrics IS NULL OR metrics=''""",
                model_registry_valid_statuses).fetchall()
            checks.append({"name": "model registry records are reviewable", "passed": not malformed_models, "detail": [r["id"] for r in malformed_models]})
        if "research_validation_runs" in tables:
            malformed_runs = []
            for row in con.execute("SELECT id,run_id,dataset_hash,report_hash,configuration,report,execution_authority FROM research_validation_runs").fetchall():
                valid_json = True
                try:
                    json.loads(row["configuration"] or "{}")
                    json.loads(row["report"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    valid_json = False
                if (not row["run_id"] or len(str(row["dataset_hash"] or "")) != 64
                        or len(str(row["report_hash"] or "")) != 64
                        or int(row["execution_authority"] or 0) != 0 or not valid_json):
                    malformed_runs.append(row["id"])
            trigger_names = {r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'research_validation_runs_%'"
            ).fetchall()}
            triggers_present = {"research_validation_runs_no_update", "research_validation_runs_no_delete"}.issubset(trigger_names)
            checks.append({"name": "research validation ledger is append-only and well formed",
                           "passed": not malformed_runs and triggers_present,
                           "detail": {"malformed_rows": malformed_runs, "triggers_present": triggers_present}})
        result["checks"] = checks
        result["counts"] = {
            "proposals": con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0],
            "approved_proposals": con.execute("SELECT COUNT(*) FROM proposals WHERE approved=1").fetchone()[0],
            "trades": con.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "open_trades": con.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NULL").fetchone()[0],
            "closed_trades": con.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL").fetchone()[0],
        }
        if "operational_events" in tables:
            result["counts"]["operational_events"] = con.execute("SELECT COUNT(*) FROM operational_events").fetchone()[0]
            result["counts"]["pending_memory"] = con.execute("SELECT COUNT(*) FROM memory_outbox WHERE status='PENDING'").fetchone()[0]
        if "data_snapshots" in tables:
            result["counts"]["data_snapshots"] = con.execute("SELECT COUNT(*) FROM data_snapshots").fetchone()[0]
        if "verified_memories" in tables:
            result["counts"]["verified_memories"] = con.execute("SELECT COUNT(*) FROM verified_memories").fetchone()[0]
        if "trade_lifecycle_events" in tables:
            result["counts"]["trade_lifecycle_events"] = con.execute("SELECT COUNT(*) FROM trade_lifecycle_events").fetchone()[0]
        if "position_observations" in tables:
            result["counts"]["position_observations"] = con.execute("SELECT COUNT(*) FROM position_observations").fetchone()[0]
        if "market_ticks" in tables:
            result["counts"]["market_ticks"] = con.execute("SELECT COUNT(*) FROM market_ticks").fetchone()[0]
        if "broker_execution_reports" in tables:
            result["counts"]["broker_execution_reports"] = con.execute("SELECT COUNT(*) FROM broker_execution_reports").fetchone()[0]
        if "daily_reconciliations" in tables:
            result["counts"]["daily_reconciliations"] = con.execute("SELECT COUNT(*) FROM daily_reconciliations").fetchone()[0]
        if "setup_instances" in tables:
            result["counts"]["setup_instances"] = con.execute("SELECT COUNT(*) FROM setup_instances").fetchone()[0]
        if "setup_labels" in tables:
            result["counts"]["setup_labels"] = con.execute("SELECT COUNT(*) FROM setup_labels").fetchone()[0]
        if "manual_activity" in tables:
            result["counts"]["manual_activity"] = con.execute("SELECT COUNT(*) FROM manual_activity").fetchone()[0]
        if "model_registry" in tables:
            result["counts"]["model_registry"] = con.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        if "research_validation_runs" in tables:
            result["counts"]["research_validation_runs"] = con.execute("SELECT COUNT(*) FROM research_validation_runs").fetchone()[0]
        result["passed"] = all(c["passed"] for c in checks)
        con.close()
    except Exception as exc:
        result["checks"].append({"name": "database readable", "passed": False, "detail": str(exc)})
    return result


def baseline_metrics(path: str = "trading_state.db") -> Dict[str, Any]:
    if not __import__("os").path.exists(path):
        return {"closed_trades": 0, "win_rate": 0.0, "expectancy_currency": 0.0,
                "profit_factor": None, "max_drawdown_currency": 0.0,
                "promotion_ready_sample": False, "note": "No journal database yet."}
    con = _con(path)
    # Descriptive performance metrics over broker-evidenced rows only: a
    # self-declared journal row must never become a win_rate claim.
    # Import locally because research_validation imports ``audit`` from this
    # module; a module-level import would create a circular import.
    try:
        from research_validation import broker_reconciled_tickets
    except ImportError:
        from research.research_validation import broker_reconciled_tickets
    broker_tickets = sorted(broker_reconciled_tickets(path))
    if broker_tickets:
        marks = ",".join("?" for _ in broker_tickets)
        rows = con.execute(
            "SELECT pnl,risk_amount,strategy,symbol,side FROM trades WHERE closed_at IS NOT NULL "
            "AND pnl IS NOT NULL AND ticket IN (%s)" % marks,
            tuple(broker_tickets)).fetchall()
    else:
        rows = []
    pnls = [float(r["pnl"]) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0
    latest_reconciliation = None
    if "daily_reconciliations" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        row = con.execute("SELECT period_start,status,report FROM daily_reconciliations ORDER BY period_start DESC LIMIT 1").fetchone()
        if row:
            try:
                parsed = json.loads(row["report"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
            latest_reconciliation = {"period_start": row["period_start"], "status": row["status"], "discrepancies": parsed.get("discrepancies")}
    return {
        "closed_trades": len(pnls), "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "expectancy_currency": expectancy,
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "max_drawdown_currency": max_dd,
        "promotion_ready_sample": len(pnls) >= 100,
        "note": "Metrics are descriptive only; promotion requires walk-forward validation and realistic costs.",
    }


def qualified_metrics(path: str = "trading_state.db") -> Dict[str, Any]:
    """Reproducible journal metrics with explicit sample/cost/data qualifiers."""
    audit_result = audit(path)
    qualifier = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "data_mode": "journal",
        "cost_model": "broker-reconciled only where gross_pnl/commission/swap are populated",
        "in_sample_status": "unknown", "out_of_sample_status": "not validated",
    }
    if not os.path.exists(path):
        empty_research = {
            "status": "INSUFFICIENT_DATA", "sample_size": 0,
            "excluded": {"database_missing": 1}, "point_in_time": True,
            "cost_model": "journal broker-reconciled fields only",
            "date_range": None, "out_of_sample_status": "not validated",
            "promotion_ready_sample": False, "promotion_allowed": False,
        }
        return {**qualifier, "sample_size": 0, "descriptive_sample_size": 0,
                "date_range": None, "descriptive_date_range": None,
                "research_quality": empty_research,
                "proposal_quality": {"status": "INSUFFICIENT_DATA", "total_proposals": 0,
                                     "approved_proposals": 0, "rejected_proposals": 0,
                                     "raw_vs_validated_status": "INSUFFICIENT_DATA",
                                     "baseline_lift_status": "NOT_VALIDATED"},
                "setup_decay": {"status": "INSUFFICIENT_DATA", "minimum_comparison_sample": 30,
                                "comparisons": [], "execution_authority": False},
                "trade_quality": {}, "execution_quality": {}, "portfolio_quality": {}}
    con = _con(path)
    # Descriptive sections below count broker-evidenced rows only; the strict
    # research dataset is extracted separately via labelled_dataset().
    # Local import: research_validation imports ``audit`` from this module.
    try:
        from research_validation import broker_reconciled_tickets
    except ImportError:
        from research.research_validation import broker_reconciled_tickets
    broker_tickets = sorted(broker_reconciled_tickets(path))
    if broker_tickets:
        marks = ",".join("?" for _ in broker_tickets)
        rows = con.execute(
            "SELECT * FROM trades WHERE closed_at IS NOT NULL AND pnl IS NOT NULL "
            "AND ticket IN (%s) ORDER BY closed_at" % marks,
            tuple(broker_tickets)).fetchall()
    else:
        rows = []

    # Keep the operational/descriptive report below backward compatible, but
    # never let it be mistaken for a training or promotion dataset.  The
    # strict dataset is extracted through the same fail-closed path used by
    # walk-forward validation: deterministic close label, valid trade horizon,
    # current feature schema, finite point-in-time values and a matching hash.
    # Import locally because research_validation imports ``audit`` from this
    # module; a module-level import would create a circular import.
    try:
        from research_validation import labelled_dataset
    except ImportError:
        from research.research_validation import labelled_dataset
    research_dataset = labelled_dataset(path)
    research_rows = research_dataset.get("rows", [])
    research_pnls = [float(item["pnl"]) for item in research_rows]
    research_rs = [float(item["r_multiple"]) for item in research_rows]
    research_wins = [value for value in research_pnls if value > 0]
    research_losses = [value for value in research_pnls if value < 0]
    research_start = research_rows[0]["closed_at"] if research_rows else None
    research_end = research_rows[-1]["closed_at"] if research_rows else None
    research_label_counts: Dict[str, int] = {}
    research_setup_counts: Dict[str, int] = {}
    for item in research_rows:
        label = str(item.get("label") or "UNKNOWN")
        setup = str(item.get("setup_id") or "legacy_unclassified")
        research_label_counts[label] = research_label_counts.get(label, 0) + 1
        research_setup_counts[setup] = research_setup_counts.get(setup, 0) + 1
    research_equity = research_peak = research_max_dd = 0.0
    for value in research_pnls:
        research_equity += value
        research_peak = max(research_peak, research_equity)
        research_max_dd = max(research_max_dd, research_peak - research_equity)
    research_cost_complete = 0
    # ``labelled_dataset`` deliberately exposes only the outcome and the
    # proposal snapshot. Cost completeness is checked against the authoritative
    # trade row by ticket so the research report cannot silently claim costs
    # that were not broker-recorded.
    if research_rows:
        by_ticket = {int(row["ticket"]): row for row in rows}
        research_cost_complete = sum(
            1 for item in research_rows
            if (row := by_ticket.get(int(item["ticket"]))) is not None
            and row["gross_pnl"] is not None
            and row["commission"] is not None
            and row["swap"] is not None
        )
    research_quality = {
        "status": "RESEARCH_QUALIFIED" if research_rows else "INSUFFICIENT_DATA",
        "sample_size": len(research_rows),
        "excluded": research_dataset.get("excluded", {}),
        "date_range": {"start": research_start, "end": research_end} if research_rows else None,
        "point_in_time": bool(research_dataset.get("point_in_time", True)),
        "cost_model": research_dataset.get("cost_model", "journal broker-reconciled fields only"),
        "label_counts": research_label_counts,
        "setup_counts": research_setup_counts,
        "win_rate": len(research_wins) / len(research_pnls) if research_pnls else None,
        "expectancy_currency": statistics.fmean(research_pnls) if research_pnls else None,
        "expectancy_r": statistics.fmean(research_rs) if research_rs else None,
        "profit_factor": sum(research_wins) / abs(sum(research_losses)) if research_losses else None,
        "max_drawdown_currency": research_max_dd if research_pnls else None,
        "cost_complete_closed_trades": research_cost_complete,
        "out_of_sample_status": "not validated",
        "promotion_ready_sample": len(research_rows) >= 100,
        "promotion_allowed": False,
        "note": "Research metrics use only deterministic labels and point-in-time proposal features; promotion still requires purged walk-forward evidence and operator approval.",
    }

    # Proposal quality is kept separate from closed-trade performance. A
    # rejected proposal has no realized counterfactual outcome in the journal,
    # so it must not be scored as a loss or silently omitted from the proposal
    # count. Only deterministic confidence/label pairs from the strict dataset
    # are eligible for false-positive/negative rates.
    prediction_rows: List[tuple[float, bool]] = []
    for item in research_rows:
        features = item.get("features") if isinstance(item.get("features"), dict) else {}
        confidence = next((features.get(key) for key in ("confidence", "model_confidence", "signal_confidence")
                           if features.get(key) not in (None, "")), None)
        try:
            probability = float(confidence)
            if math.isfinite(probability) and 0.0 <= probability <= 1.0:
                prediction_rows.append((probability, str(item.get("label", "")).upper().startswith("WIN")))
        except (TypeError, ValueError, OverflowError):
            continue
    predicted_positive = [(probability, outcome) for probability, outcome in prediction_rows if probability >= 0.5]
    predicted_negative = [(probability, outcome) for probability, outcome in prediction_rows if probability < 0.5]
    false_positive_count = sum(not outcome for _, outcome in predicted_positive)
    false_negative_count = sum(outcome for _, outcome in predicted_negative)
    proposal_total = proposal_approved = proposal_rejected = 0
    linked_closed = 0
    if "proposals" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        proposal_counts = con.execute("SELECT COUNT(*) total, SUM(approved) approved FROM proposals").fetchone()
        proposal_total = int(proposal_counts["total"] or 0)
        proposal_approved = int(proposal_counts["approved"] or 0)
        proposal_rejected = proposal_total - proposal_approved
    strict_tickets = {int(item["ticket"]) for item in research_rows}
    if strict_tickets and "setup_instances" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        placeholders = ",".join("?" for _ in strict_tickets)
        linked_closed = int(con.execute(
            f"SELECT COUNT(DISTINCT ticket) FROM setup_instances WHERE ticket IN ({placeholders})",
            tuple(strict_tickets),
        ).fetchone()[0] or 0)
    proposal_quality = {
        "status": "DESCRIPTIVE_ONLY" if proposal_total else "INSUFFICIENT_DATA",
        "total_proposals": proposal_total,
        "approved_proposals": proposal_approved,
        "rejected_proposals": proposal_rejected,
        "approval_rate": proposal_approved / proposal_total if proposal_total else None,
        "strict_closed_trades_linked_to_setup_instance": linked_closed,
        "confidence_threshold": 0.5,
        "confidence_label_sample": len(prediction_rows),
        "false_positive_count": false_positive_count,
        "false_positive_rate": false_positive_count / len(predicted_positive) if predicted_positive else None,
        "false_negative_count": false_negative_count,
        "false_negative_rate": false_negative_count / len(predicted_negative) if predicted_negative else None,
        "validated_expectancy_r": research_quality["expectancy_r"],
        "raw_proposal_expectancy_r": None,
        "raw_vs_validated_lift_r": None,
        "raw_vs_validated_status": "INSUFFICIENT_DATA",
        "raw_vs_validated_reason": "Rejected proposals have no point-in-time counterfactual outcome; do not score them as losses without an explicit labelled replay.",
        "baseline_lift": None,
        "baseline_lift_status": "NOT_VALIDATED",
        "note": "False-positive/negative rates are descriptive strict-label diagnostics, not model promotion evidence.",
    }

    # Setup decay is a descriptive stability check over the strict research
    # sample. Compare each setup's latest month with the immediately prior
    # month in which it appeared; small samples never produce a degradation
    # percentage and no result mutates the setup registry.
    setup_monthly: Dict[tuple[str, str], List[float]] = {}
    for item in research_rows:
        try:
            closed = datetime.fromisoformat(str(item["closed_at"]).replace("Z", "+00:00"))
            setup = str(item.get("setup_id") or "legacy_unclassified")
            month = f"{closed.year:04d}-{closed.month:02d}"
            setup_monthly.setdefault((setup, month), []).append(float(item["r_multiple"]))
        except (TypeError, ValueError, OverflowError):
            continue
    setup_decay_minimum = 30
    setup_decay_comparisons: List[Dict[str, Any]] = []
    for (setup, month), current_values in sorted(setup_monthly.items()):
        prior_candidates = sorted((key, values) for key, values in setup_monthly.items()
                                  if key[0] == setup and key[1] < month)
        if not prior_candidates:
            continue
        prior_month, prior_values = prior_candidates[-1]
        current_expectancy = statistics.fmean(current_values) if current_values else None
        prior_expectancy = statistics.fmean(prior_values) if prior_values else None
        comparison = {
            "setup_id": setup, "month": month, "prior_month": prior_month,
            "sample_size": len(current_values), "prior_sample_size": len(prior_values),
            "current_expectancy_r": current_expectancy, "prior_expectancy_r": prior_expectancy,
            "out_of_sample_status": "not validated", "operator_review_required": False,
            "execution_authority": False,
        }
        if len(current_values) < setup_decay_minimum or len(prior_values) < setup_decay_minimum:
            comparison.update({"status": "INSUFFICIENT_DATA", "expectancy_change_r": None,
                               "degradation_fraction": None})
        else:
            change = current_expectancy - prior_expectancy
            degradation = ((prior_expectancy - current_expectancy) / abs(prior_expectancy)
                           if prior_expectancy is not None and prior_expectancy > 0 else None)
            comparison.update({"status": "DESCRIPTIVE_COMPARISON", "expectancy_change_r": change,
                               "degradation_fraction": degradation,
                               "operator_review_required": bool(degradation is not None and degradation > 0.30)})
        setup_decay_comparisons.append(comparison)
    setup_decay = {
        "status": "INSUFFICIENT_DATA" if not setup_decay_comparisons else "REVIEW_REQUIRED",
        "minimum_comparison_sample": setup_decay_minimum,
        "comparisons": setup_decay_comparisons,
        "note": "Setup decay is descriptive only; it never retires or changes a setup automatically.",
        "execution_authority": False,
    }
    pnls = [float(row["pnl"]) for row in rows]
    rs = [float(row["pnl"]) / float(row["risk_amount"]) for row in rows if float(row["risk_amount"] or 0) > 0]
    holding = []
    for row in rows:
        try:
            holding.append((datetime.fromisoformat(row["closed_at"]) - datetime.fromisoformat(row["opened_at"])).total_seconds())
        except (TypeError, ValueError):
            pass
    tickets = [int(row["ticket"]) for row in rows]
    excursions: Dict[int, Dict[str, Any]] = {}
    if tickets and "position_observations" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        marks = con.execute("""SELECT ticket,MAX(r_multiple) mfe_r,MIN(r_multiple) mae_r,COUNT(*) mark_count
            FROM position_observations
            WHERE mark_quality IN ('BROKER_FRESH','BROKER_EXECUTION')
            GROUP BY ticket""").fetchall()
        excursions = {int(row["ticket"]): dict(row) for row in marks}
    mfe = [float(excursions[ticket]["mfe_r"]) for ticket in tickets if ticket in excursions and excursions[ticket]["mfe_r"] is not None]
    mae = [float(excursions[ticket]["mae_r"]) for ticket in tickets if ticket in excursions and excursions[ticket]["mae_r"] is not None]
    wins, losses = [item for item in pnls if item > 0], [item for item in pnls if item < 0]
    gross_loss = abs(sum(losses))
    equity = peak = max_dd = 0.0
    for value in pnls:
        equity += value; peak = max(peak, equity); max_dd = max(max_dd, peak - equity)
    start, end = (rows[0]["closed_at"], rows[-1]["closed_at"]) if rows else (None, None)

    # Risk-adjusted metrics are deliberately descriptive and sample-gated.
    # They use reconciled per-trade R values, never unrealized marks or
    # hindsight-filled labels.  A missing/short sample returns an explicit
    # status instead of a misleading zero that could be mistaken for a result.
    risk_sample_min = 30
    # Risk statistics are research claims, so they use the strict labelled
    # dataset rather than every closed operational row.
    risk_values = list(research_rs)
    risk_status = "DESCRIPTIVE_ONLY" if len(risk_values) >= risk_sample_min else "INSUFFICIENT_DATA"
    mean_r = statistics.fmean(risk_values) if risk_values else None
    stdev_r = statistics.stdev(risk_values) if len(risk_values) >= 2 else None
    downside = [value for value in risk_values if value < 0]
    downside_dev = math.sqrt(statistics.fmean([value * value for value in downside])) if downside else None
    sharpe_r = (mean_r / stdev_r * math.sqrt(len(risk_values))) if mean_r is not None and stdev_r and stdev_r > 0 else None
    sortino_r = (mean_r / downside_dev * math.sqrt(len(risk_values))) if mean_r is not None and downside_dev and downside_dev > 0 else None
    cumulative_r = []
    running_r = 0.0
    for value in risk_values:
        running_r += value; cumulative_r.append(running_r)
    peak_r = max(cumulative_r) if cumulative_r else 0.0
    max_drawdown_r = max((peak_r - value for value in cumulative_r), default=0.0)
    cvar_min = 100
    cvar_r = None
    if len(risk_values) >= cvar_min:
        tail_count = max(1, math.ceil(len(risk_values) * 0.05))
        cvar_r = statistics.fmean(sorted(risk_values)[:tail_count])
    omega_r = None
    if risk_values:
        positive_total = sum(value for value in risk_values if value > 0)
        negative_total = abs(sum(value for value in risk_values if value < 0))
        omega_r = positive_total / negative_total if negative_total > 0 else None
    calmar_r = None
    calmar_status = "INSUFFICIENT_DATA"
    if risk_values and start and end:
        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            elapsed_years = max((end_dt - start_dt).total_seconds() / (365.25 * 86400), 0.0)
            if elapsed_years >= 1.0 and max_drawdown_r > 0:
                annualized_r = mean_r * len(risk_values) / elapsed_years
                calmar_r = annualized_r / max_drawdown_r
                calmar_status = "DESCRIPTIVE_ONLY"
        except (TypeError, ValueError):
            pass
    risk_of_ruin = None
    risk_of_ruin_status = "INSUFFICIENT_DATA"
    if len(risk_values) >= 100:
        # A reproducible research estimate under explicit assumptions, not a
        # forecast: fixed 1% account risk per trade, 10% equity-ruin boundary,
        # 1,000 future trades, and bootstrap resampling of observed R values.
        seed_material = json.dumps(risk_values, separators=(",", ":"), sort_keys=True).encode("utf-8")
        rng = random.Random(int(hashlib.sha256(seed_material).hexdigest()[:16], 16))
        ruined = 0
        for _ in range(2000):
            equity_path = 1.0
            for _step in range(1000):
                equity_path *= 1.0 + 0.01 * rng.choice(risk_values)
                if equity_path <= 0.90:
                    ruined += 1
                    break
        risk_of_ruin = ruined / 2000.0
        risk_of_ruin_status = "DESCRIPTIVE_ONLY"
    kelly_quarter = None
    risk_wins = [value for value in risk_values if value > 0]
    risk_losses = [value for value in risk_values if value < 0]
    if len(risk_values) >= 100 and risk_wins and risk_losses:
        win_rate = len(risk_wins) / len(risk_values)
        avg_win_r = statistics.fmean([value for value in risk_values if value > 0]) if any(value > 0 for value in risk_values) else 0.0
        avg_loss_r = abs(statistics.fmean([value for value in risk_values if value < 0])) if any(value < 0 for value in risk_values) else 0.0
        if avg_win_r > 0 and avg_loss_r > 0:
            payoff = avg_win_r / avg_loss_r
            kelly_quarter = max(0.0, 0.25 * (win_rate - (1.0 - win_rate) / payoff))
    risk_quality = {
        "status": risk_status, "sample_size": len(risk_values), "minimum_sample": risk_sample_min,
        "sharpe_r": sharpe_r, "sortino_r": sortino_r, "max_drawdown_r": max_drawdown_r if risk_values else None,
        "calmar_r": calmar_r, "calmar_status": calmar_status, "omega_r": omega_r,
        "cvar_5_r": cvar_r, "cvar_minimum_sample": cvar_min, "quarter_kelly_risk_fraction": kelly_quarter,
        "risk_of_ruin_10pct": risk_of_ruin, "risk_of_ruin_status": risk_of_ruin_status,
        "risk_of_ruin_assumptions": {"risk_fraction": 0.01, "ruin_boundary": 0.10,
                                      "horizon_trades": 1000, "bootstrap_paths": 2000},
        "note": "Risk metrics are descriptive only; Kelly/CVaR/ruin estimates and promotion require labelled point-in-time walk-forward evidence.",
    }
    intents = []
    if "order_intents" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        intents = con.execute("SELECT status,payload,result,created_at,updated_at FROM order_intents").fetchall()
    statuses = [str(row["status"]) for row in intents]
    status_counts = {status: statuses.count(status) for status in sorted(set(statuses))}

    def status_rate(status: str) -> Optional[float]:
        return status_counts.get(status, 0) / len(statuses) if statuses else None

    slippages = []
    for item in intents:
        try:
            payload, result = json.loads(item["payload"] or "{}"), json.loads(item["result"] or "{}")
            requested, accepted = payload.get("entry"), result.get("accepted_price", result.get("fill_price"))
            if requested is not None and accepted is not None:
                slippages.append(abs(float(accepted) - float(requested)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    execution_reports = []
    if "broker_execution_reports" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        execution_reports = con.execute("""SELECT event_type,execution_latency_ms,spread_at_submit,
            spread_at_fill,implementation_shortfall,requote_count,commission,swap
            FROM broker_execution_reports""").fetchall()
    latency_ms = [float(row["execution_latency_ms"]) for row in execution_reports if row["execution_latency_ms"] is not None]
    spread_submit = [float(row["spread_at_submit"]) for row in execution_reports if row["spread_at_submit"] is not None]
    spread_fill = [float(row["spread_at_fill"]) for row in execution_reports if row["spread_at_fill"] is not None]
    shortfalls = [float(row["implementation_shortfall"]) for row in execution_reports if row["implementation_shortfall"] is not None]

    def percentile(values: List[float], fraction: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
        return ordered[index]

    report_requotes = sum(int(row["requote_count"] or 0) for row in execution_reports)
    report_commission = sum(float(row["commission"] or 0.0) for row in execution_reports)
    report_swap = sum(float(row["swap"] or 0.0) for row in execution_reports)
    start, end = (rows[0]["closed_at"], rows[-1]["closed_at"]) if rows else (None, None)
    periods: Dict[str, List[float]] = {"day": [], "week": [], "month": []}
    period_dates: Dict[str, List[str]] = {"day": [], "week": [], "month": []}
    for row in rows:
        try:
            closed = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
            day_key = closed.date().isoformat()
            iso = closed.isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            month_key = f"{closed.year:04d}-{closed.month:02d}"
            for name, key in (("day", day_key), ("week", week_key), ("month", month_key)):
                periods[name].append(float(row["pnl"]))
                period_dates[name].append(key)
        except (TypeError, ValueError):
            continue
    period_quality: Dict[str, Dict[str, Any]] = {}
    for name in periods:
        grouped: Dict[str, List[float]] = {}
        for key, pnl in zip(period_dates[name], periods[name]):
            grouped.setdefault(key, []).append(pnl)
        period_quality[name] = {"periods": [{"period": key, "sample_size": len(values),
                                              "net_pnl": sum(values),
                                              "expectancy_currency": statistics.fmean(values) if values else None}
                                             for key, values in sorted(grouped.items())],
                                "cost_model": qualifier["cost_model"], "in_sample_status": qualifier["in_sample_status"],
                                "out_of_sample_status": qualifier["out_of_sample_status"]}

    # Segment every closed result by the durable trade columns plus the
    # point-in-time feature/provenance fields that were present at proposal
    # time. Missing dimensions are explicit UNKNOWN values; they are never
    # inferred from hindsight or silently dropped from a report.
    dimensions = ("setup_id", "symbol", "side", "strategy", "timeframe", "session",
                  "regime", "volatility_regime", "spread_bucket", "event_class",
                  "model_version", "prompt_version", "feature_schema_version")
    segmented: Dict[str, Dict[str, Dict[str, Any]]] = {dimension: {} for dimension in dimensions}
    monthly_regime: Dict[tuple[str, str], List[float]] = {}
    monthly_regime_stats: Dict[tuple[str, str], Dict[str, Any]] = {}
    alignment_total = 0
    alignment_matches = 0

    def payload_context(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        features = payload.get("proposal_features") or payload.get("features") or {}
        if not isinstance(features, dict):
            features = {}
        versions = features.get("artifact_versions") or payload.get("artifact_versions") or {}
        if not isinstance(versions, dict):
            versions = {}
        context = payload.get("context") or features.get("context") or {}
        if not isinstance(context, dict):
            context = {}
        return {**features, **context, **versions}

    def segment_value(row: sqlite3.Row, context: Dict[str, Any], dimension: str) -> str:
        if dimension == "setup_id":
            value = row["setup_id"] or context.get("setup_id")
        elif dimension in {"symbol", "side", "strategy"}:
            value = row[dimension]
        elif dimension == "spread_bucket":
            spread = context.get("spread_at_submit", context.get("spread"))
            try:
                spread_value = float(spread)
                value = "UNKNOWN" if spread_value < 0 else ("LOW" if spread_value <= 0.0003 else ("MEDIUM" if spread_value <= 0.001 else "HIGH"))
            except (TypeError, ValueError):
                value = "UNKNOWN"
        else:
            aliases = {"volatility_regime": ("volatility_regime", "volatility"),
                       "model_version": ("model_version", "model"),
                       "prompt_version": ("prompt_version", "prompt_hash"),
                       "feature_schema_version": ("feature_schema_version",),
                       "event_class": ("event_class", "event_state"),
                       "timeframe": ("timeframe", "primary_timeframe"),
                       "session": ("session", "session_name"),
                       "regime": ("regime", "market_regime")}
            value = next((context.get(key) for key in aliases.get(dimension, (dimension,)) if context.get(key) not in (None, "")), None)
        return str(value) if value not in (None, "") else "UNKNOWN"

    calibration_rows: List[tuple[float, int]] = []
    for row in rows:
        context = payload_context(row)
        confidence = next((context.get(key) for key in ("confidence", "model_confidence", "signal_confidence")
                           if context.get(key) not in (None, "")), None)
        try:
            probability = float(confidence)
            if 0.0 <= probability <= 1.0:
                calibration_rows.append((probability, int(float(row["pnl"] or 0.0) > 0.0)))
        except (TypeError, ValueError):
            continue
    calibration_bins = []
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        upper = lower + 0.2
        members = [(probability, outcome) for probability, outcome in calibration_rows
                   if lower <= probability < upper or (upper == 1.0 and probability <= upper)]
        calibration_bins.append({"lower": lower, "upper": upper, "sample_size": len(members),
                                 "mean_confidence": statistics.fmean([item[0] for item in members]) if members else None,
                                 "observed_win_rate": statistics.fmean([item[1] for item in members]) if members else None})
    brier = statistics.fmean([(probability - outcome) ** 2 for probability, outcome in calibration_rows]) if calibration_rows else None
    log_loss = None
    if calibration_rows:
        log_loss = statistics.fmean([-math.log(max(1e-15, probability if outcome else 1.0 - probability))
                                     for probability, outcome in calibration_rows])
    calibration = {"status": "DESCRIPTIVE_ONLY" if calibration_rows else "INSUFFICIENT_DATA",
                   "sample_size": len(calibration_rows), "brier_score": brier, "log_loss": log_loss,
                   "accuracy_at_0_5": (statistics.fmean([int((probability >= 0.5) == bool(outcome))
                                                           for probability, outcome in calibration_rows])
                                       if calibration_rows else None),
                   "bins": calibration_bins,
                   "note": "Calibration is descriptive and cannot promote a model without labelled walk-forward evidence."}

    for row in rows:
        context = payload_context(row)
        for dimension in dimensions:
            key = segment_value(row, context, dimension)
            bucket = segmented[dimension].setdefault(key, {"sample_size": 0, "wins": 0, "net_pnl": 0.0,
                                                            "r_values": [], "date_start": row["closed_at"],
                                                            "date_end": row["closed_at"]})
            pnl = float(row["pnl"] or 0.0)
            bucket["sample_size"] += 1; bucket["wins"] += int(pnl > 0); bucket["net_pnl"] += pnl
            risk = float(row["risk_amount"] or 0.0)
            if risk > 0:
                bucket["r_values"].append(pnl / risk)
            bucket["date_start"] = min(bucket["date_start"], row["closed_at"])
            bucket["date_end"] = max(bucket["date_end"], row["closed_at"])
        regime = segment_value(row, context, "regime")
        try:
            closed = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
            month = f"{closed.year:04d}-{closed.month:02d}"
            monthly_regime.setdefault((month, regime), []).append(float(row["pnl"] or 0.0))
            stats = monthly_regime_stats.setdefault(
                (month, regime), {"pnls": [], "mae_abs": [], "alignment_known": 0, "alignment_matches": 0}
            )
            pnl_value = float(row["pnl"] or 0.0)
            stats["pnls"].append(pnl_value)
            ticket = int(row["ticket"])
            if ticket in excursions and excursions[ticket].get("mae_r") is not None:
                stats["mae_abs"].append(abs(float(excursions[ticket]["mae_r"])))
            macro_bias = next((context.get(key) for key in
                               ("macro_bias", "macro_direction", "macro_thesis_direction")
                               if context.get(key) not in (None, "")), None)
            bias = str(macro_bias).upper().strip() if macro_bias is not None else ""
            side = str(row["side"] or "").upper().strip()
            bias_side = {"LONG": "BUY", "BULL": "BUY", "BUY": "BUY",
                         "SHORT": "SELL", "BEAR": "SELL", "SELL": "SELL"}.get(bias)
            if bias_side and side in {"BUY", "SELL"}:
                stats["alignment_known"] += 1
                stats["alignment_matches"] += int(side == bias_side)
                alignment_total += 1
                alignment_matches += int(side == bias_side)
        except (TypeError, ValueError):
            pass

    for dimension, buckets in segmented.items():
        for bucket in buckets.values():
            values = bucket.pop("r_values")
            bucket["win_rate"] = bucket["wins"] / bucket["sample_size"] if bucket["sample_size"] else None
            bucket["expectancy_r"] = statistics.fmean(values) if values else None
            bucket["cost_model"] = qualifier["cost_model"]
            bucket["out_of_sample_status"] = qualifier["out_of_sample_status"]
            bucket["sample_qualification"] = "DESCRIPTIVE_ONLY"

    # Month-over-month regime change is deliberately gated.  A small sample
    # is reported as INSUFFICIENT_DATA rather than being described as growth.
    min_comparison_sample = 30
    monthly_regime_growth: List[Dict[str, Any]] = []
    for (month, regime), values in sorted(monthly_regime.items()):
        prior_candidates = sorted((key, vals) for key, vals in monthly_regime.items() if key[1] == regime and key[0] < month)
        if not prior_candidates:
            continue
        prior_month, prior_values = prior_candidates[-1]
        item = {"regime": regime, "month": month, "prior_month": prior_month,
                "sample_size": len(values), "prior_sample_size": len(prior_values),
                "cost_model": qualifier["cost_model"], "out_of_sample_status": qualifier["out_of_sample_status"]}
        if len(values) < min_comparison_sample or len(prior_values) < min_comparison_sample:
            item.update({"status": "INSUFFICIENT_DATA", "expectancy_change_currency": None,
                         "growth_quotient": None, "drawdown_compression": None})
        else:
            current_stats = monthly_regime_stats.get((month, regime), {})
            prior_stats = monthly_regime_stats.get((prior_month, regime), {})
            current_win_rate = sum(1 for value in values if value > 0) / len(values)
            prior_win_rate = sum(1 for value in prior_values if value > 0) / len(prior_values)
            current_mae = current_stats.get("mae_abs", [])
            prior_mae = prior_stats.get("mae_abs", [])
            item.update({"status": "DESCRIPTIVE_COMPARISON",
                         "expectancy_change_currency": statistics.fmean(values) - statistics.fmean(prior_values),
                         "growth_quotient": current_win_rate - prior_win_rate,
                         "current_win_rate": current_win_rate,
                         "prior_win_rate": prior_win_rate,
                         "drawdown_compression": (
                             statistics.fmean(prior_mae) - statistics.fmean(current_mae)
                             if current_mae and prior_mae else None
                         ),
                         "drawdown_metric": "mean absolute MAE in R; positive means compression"})
        monthly_regime_growth.append(item)
    alignment_min_sample = 30
    alignment_metric = {
        "sample_size": alignment_total, "minimum_sample": alignment_min_sample,
        "aligned_count": alignment_matches, "status": "DESCRIPTIVE_ONLY" if alignment_total >= alignment_min_sample else "INSUFFICIENT_DATA",
        "efficiency": alignment_matches / alignment_total if alignment_total >= alignment_min_sample else None,
        "source": "explicit point-in-time macro bias versus trade side; unknown bias is excluded",
    }
    costs_complete = sum(1 for row in rows if row["gross_pnl"] is not None and row["commission"] is not None and row["swap"] is not None)
    latest_reconciliation = None
    if "daily_reconciliations" in {item["name"] for item in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        row = con.execute("SELECT period_start,status,report FROM daily_reconciliations ORDER BY period_start DESC LIMIT 1").fetchone()
        if row:
            try:
                parsed = json.loads(row["report"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
            latest_reconciliation = {"period_start": row["period_start"], "status": row["status"], "discrepancies": parsed.get("discrepancies")}
    return {
        **qualifier, "integrity_passed": audit_result["passed"],
        # ``sample_size`` is intentionally the strict research sample.  The
        # raw closed-row count remains available for operations diagnostics and
        # cannot be confused with evidence eligible for model promotion.
        "sample_size": len(research_rows), "descriptive_sample_size": len(rows),
        "date_range": research_quality["date_range"],
        "descriptive_date_range": {"start": start, "end": end},
        "research_quality": research_quality,
        "proposal_quality": proposal_quality,
        "setup_decay": setup_decay,
        "trade_quality": {
            "win_rate": len(wins) / len(pnls) if pnls else None, "expectancy_currency": statistics.fmean(pnls) if pnls else None,
            "expectancy_r": statistics.fmean(rs) if rs else None, "profit_factor": sum(wins) / gross_loss if gross_loss else None,
            "avg_win": statistics.fmean(wins) if wins else None, "avg_loss": statistics.fmean(losses) if losses else None,
            "mfe_r_mean": statistics.fmean(mfe) if mfe else None, "mae_r_mean": statistics.fmean(mae) if mae else None,
            "mfe_mae_qualified_trades": len(excursions),
            "holding_seconds_mean": statistics.fmean(holding) if holding else None,
            "cost_complete_closed_trades": costs_complete,
            "calibration": calibration,
        },
        "execution_quality": {
            "order_intents": len(intents), "status_counts": status_counts,
            "filled": statuses.count("FILLED"), "partial": statuses.count("PARTIAL"),
            "pending": statuses.count("PENDING"), "cancelled": statuses.count("CANCELLED"),
            "expired": statuses.count("EXPIRED"), "rejected": statuses.count("REJECTED"), "unresolved": statuses.count("UNRESOLVED"),
            "fill_rate": (statuses.count("FILLED") + statuses.count("PARTIAL")) / len(statuses) if statuses else None,
            "partial_rate": status_rate("PARTIAL"), "pending_rate": status_rate("PENDING"),
            "cancellation_rate": status_rate("CANCELLED"), "expiry_rate": status_rate("EXPIRED"),
            "rejection_rate": status_rate("REJECTED"), "unresolved_rate": status_rate("UNRESOLVED"),
            "slippage_mean_price": statistics.fmean(slippages) if slippages else None,
            "slippage_observation_count": len(slippages),
            "execution_report_count": len(execution_reports),
            "latency_ms_p50": percentile(latency_ms, 0.50), "latency_ms_p95": percentile(latency_ms, 0.95),
            "spread_at_submit_mean": statistics.fmean(spread_submit) if spread_submit else None,
            "spread_at_fill_mean": statistics.fmean(spread_fill) if spread_fill else None,
            "implementation_shortfall_mean": statistics.fmean(shortfalls) if shortfalls else None,
            "requote_count": report_requotes,
            "execution_report_commission": report_commission,
            "execution_report_swap": report_swap,
        },
        "portfolio_quality": {"closed_net_pnl": sum(pnls), "max_drawdown_currency": max_dd,
                              "open_trades": audit_result.get("counts", {}).get("open_trades", 0),
                              "latest_daily_reconciliation": latest_reconciliation,
                              "risk_quality": risk_quality},
        "period_quality": period_quality,
        "segment_quality": segmented,
        "monthly_regime_growth": {"minimum_comparison_sample": min_comparison_sample,
                                   "comparisons": monthly_regime_growth,
                                   "status": "INSUFFICIENT_DATA" if not monthly_regime_growth else "REVIEW_REQUIRED"},
        "adaptive_metrics": {
            "growth_quotient": {
                "definition": "current regime win rate minus prior comparable month win rate",
                "minimum_comparison_sample": min_comparison_sample,
                "comparisons": monthly_regime_growth,
            },
            "drawdown_compression": {
                "definition": "prior mean absolute MAE in R minus current mean absolute MAE in R",
                "minimum_comparison_sample": min_comparison_sample,
                "comparisons": monthly_regime_growth,
            },
            "regime_alignment_efficiency": alignment_metric,
        },
        "limitations": ["No calibration/Brier/Sharpe/CVaR claim until adequate correctly labelled point-in-time samples exist.",
                        "No result is out-of-sample or a promotion signal without recorded validation metadata."],
    }


def write_report(path: str = "trading_state.db", output: str = "journal_audit.json") -> Dict[str, Any]:
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "audit": audit(path), "baseline": baseline_metrics(path), "qualified_metrics": qualified_metrics(path)}
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def write_audit_export(path: str = "trading_state.db", output: str = "journal_audit.json",
                       *, operational_limit: int = 10000) -> Dict[str, Any]:
    """Write trading and operational evidence into one non-authoritative file."""
    from agent_operations import AgentOperations
    from research_runs import list_runs as list_validation_runs
    from trading_memory import TradingMemory

    report = write_report(path, output=output)
    report["operational"] = AgentOperations(path).audit_export(limit=operational_limit)
    report["research_validation_runs"] = list_validation_runs(path, limit=50)
    report["memory_quality"] = TradingMemory(path).memory_quality_metrics()
    report["execution_authority"] = False
    report["limitation"] = "Combined evidence export only; it never grants order permission."
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report
