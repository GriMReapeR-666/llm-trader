"""Automated strategy-lifecycle evaluation over the durable journal.

Consumes the strict point-in-time research dataset plus the existing
setup-governance gates and produces lifecycle demotions with operator alerts.
The engine may only DEMOTE (DEPLOYED -> DECAYING -> FROZEN); every promotion
or retirement remains an explicit operator action. Nothing here grants order
authority.

``evaluate`` is also reachable with no scheduler at all via ``run_offline`` /
``python operations/strategy_evaluation.py --db <journal>``, which scores
CANDIDATE setups against whatever paper-reconciled history the journal has
accumulated instead of waiting for a trading loop that may not be running.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from research.research_validation import labelled_dataset
except ImportError:
    from research.research_validation import labelled_dataset

try:
    from research.setup_governance import MIN_RESEARCH_SAMPLE, retirement_gate
except ImportError:
    from research.setup_governance import MIN_RESEARCH_SAMPLE, retirement_gate

try:
    import operations.strategy_lifecycle as slc
except ImportError:
    from operations import strategy_lifecycle as slc
import logging
log = logging.getLogger(__name__)

# ── Gate Chaining Orchestrator (Blind Spot audit) ────────────────────────────
try:
    from research.gate_chaining_orchestrator import GateChainingOrchestrator as _GateOrchestrator
except ImportError:
    _GateOrchestrator = None

# ── Model Staleness Tracker (Blind Spot audit) ──────────────────────────────
try:
    from core.model_staleness_tracker import ModelStalenessTracker as _ModelStalenessTracker
except ImportError:
    _ModelStalenessTracker = None


MIN_MONTHLY_COMPARISON_SAMPLE = 30
MAX_DEGRADATION_FRACTION = 0.30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_key(closed_at: str) -> Optional[str]:
    try:
        parsed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _per_setup_stats(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    monthly: Dict[tuple, List[float]] = {}
    for row in rows:
        setup = str(row.get("setup_id") or "legacy_unclassified")
        month = _month_key(row.get("closed_at", ""))
        if month is None:
            continue
        try:
            r = float(row.get("r_multiple"))
        except (TypeError, ValueError, OverflowError):
            continue
        monthly.setdefault((setup, month), []).append(r)
    stats: Dict[str, Dict[str, Any]] = {}
    for setup in {key[0] for key in monthly}:
        months = sorted(key[1] for key in monthly if key[0] == setup)
        all_r = [r for key, values in monthly.items() if key[0] == setup for r in values]
        current_month = months[-1]
        prior_candidates = [m for m in months if m < current_month]
        entry: Dict[str, Any] = {
            "closed_samples": len(all_r),
            "expectancy_r": (sum(all_r) / len(all_r)) if all_r else None,
            "current_month": current_month,
            "prior_month": prior_candidates[-1] if prior_candidates else None,
        }
        if entry["prior_month"] is not None:
            current_values = monthly[(setup, current_month)]
            prior_values = monthly[(setup, entry["prior_month"])]
            entry["current_sample_size"] = len(current_values)
            entry["prior_sample_size"] = len(prior_values)
            enough = (len(current_values) >= MIN_MONTHLY_COMPARISON_SAMPLE
                      and len(prior_values) >= MIN_MONTHLY_COMPARISON_SAMPLE)
            if enough:
                current_mean = sum(current_values) / len(current_values)
                prior_mean = sum(prior_values) / len(prior_values)
                degradation = None
                if prior_mean > 0:
                    degradation = (prior_mean - current_mean) / abs(prior_mean)
                entry.update({
                    "comparison_status": "DESCRIPTIVE_COMPARISON",
                    "current_expectancy_r": current_mean,
                    "prior_expectancy_r": prior_mean,
                    "degradation_fraction": degradation,
                })
            else:
                entry["comparison_status"] = "INSUFFICIENT_DATA"
        else:
            entry["comparison_status"] = "NO_PRIOR_WINDOW"
        wins = [r for r in all_r if r > 0]
        losses = [r for r in all_r if r < 0]
        entry["win_rate"] = (len(wins) / len(all_r)) if all_r else 0.0
        gross_win = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        entry["profit_factor"] = (gross_win / gross_loss) if gross_loss > 0 else (2.0 if gross_win > 0 else 1.0)
        entry["closed_trades"] = len(all_r)
        stats[setup] = entry
    return stats


def evaluate(path: str, *, registry_specs: Optional[Dict[str, Any]] = None,
             registry_fingerprint: Optional[str] = None,
             minimum_samples: int = MIN_RESEARCH_SAMPLE,
             max_degradation: float = MAX_DEGRADATION_FRACTION) -> Dict[str, Any]:
    """Run one evaluation pass; demote decaying setups and alert the operator."""
    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < MIN_RESEARCH_SAMPLE):
        raise ValueError(f"minimum_samples policy floor is {MIN_RESEARCH_SAMPLE}")
    try:
        max_degradation = float(max_degradation)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("max_degradation must be finite") from None
    if not 0.0 <= max_degradation <= 1.0:
        raise ValueError("max_degradation must be within [0,1]")

    seed_result = {"seeded": [], "count": 0}
    if registry_specs is not None:
        seed_result = slc.seed_registry(path, registry_specs,
                                        fingerprint=registry_fingerprint or "")

    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    stats = _per_setup_stats(rows)

    transitions: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    evaluated: List[Dict[str, Any]] = []

    for setup_id, entry in sorted(stats.items()):
        record: Dict[str, Any] = {"setup_id": setup_id, **{
            key: entry.get(key) for key in
            ("closed_samples", "expectancy_r", "comparison_status",
             "degradation_fraction")}
        }
        state = slc.current_states(path).get(setup_id)
        record["state"] = state
        if state in ("FROZEN", "RETIRED"):
            evaluated.append(record)
            continue

        # Only judge setups with at least the mandatory evidence floor.
        if entry["closed_samples"] < minimum_samples:
            record["decision"] = "CONTINUE_MONITORING"
            record["reason"] = (
                f"insufficient closed samples ({entry['closed_samples']}<{minimum_samples})")
            evaluated.append(record)
            continue

        comparison_ready = entry.get("comparison_status") == "DESCRIPTIVE_COMPARISON"
        gate = retirement_gate(
            live_expectancy_r=entry.get("expectancy_r"),
            oos_expectancy_r=None,
            degradation_fraction=entry.get("degradation_fraction")
            if comparison_ready else None,
            max_degradation=max_degradation,
            minimum_samples=minimum_samples,
            closed_samples=minimum_samples,  # floor already verified above
        )
        record["gate_reasons"] = gate["reasons"]
        if not gate["retire_or_freeze"]:
            # Healthy evidence: recover a DECAYING row back to monitoring-pending
            # only via explicit operator action; do nothing here.
            record["decision"] = "CONTINUE_MONITORING"
            evaluated.append(record)
            continue

        reason_text = "; ".join(gate["reasons"])[:500]
        if state == "DEPLOYED":
            result = slc.transition(path, setup_id, "DECAYING",
                                    actor="evaluation", allow_auto=True,
                                    reason=reason_text,
                                    evidence={"stats": record})
            transitions.append(result)
            alerts.append({"code": "STRATEGY_DECAY_DETECTED", "severity": "WARNING",
                           "count": 1,
                           "details": {"setup_id": setup_id, "reason": reason_text}})
            record["decision"] = "DEMOTED_TO_DECAYING"
        elif state == "DECAYING":
            result = slc.transition(path, setup_id, "FROZEN",
                                    actor="evaluation", allow_auto=True,
                                    reason=reason_text,
                                    evidence={"stats": record})
            transitions.append(result)
            alerts.append({"code": "STRATEGY_FROZEN_OPERATOR_REVIEW",
                           "severity": "CRITICAL", "count": 1,
                           "details": {"setup_id": setup_id, "reason": reason_text}})
            record["decision"] = "FROZEN_PENDING_OPERATOR_REVIEW"
        else:
            record["decision"] = "NO_ACTION"
        evaluated.append(record)

    # ── Gate Chaining Orchestrator (Blind Spot audit) ────────────────────────
    if _GateOrchestrator is not None:
        try:
            _gate_orch = _GateOrchestrator()
            _gate_verdicts = []
            for setup_id, entry in stats.items():
                _gate_input = {
                    "strategy_id": setup_id,
                    "sample_size": entry.get("closed_samples", 0),
                    "expectancy_r": entry.get("expectancy_r", 0),
                    "win_rate": entry.get("win_rate", 0),
                    "profit_factor": entry.get("profit_factor", 0),
                    "sharpe_ratio": entry.get("sharpe_ratio", 0),
                    "max_drawdown": entry.get("max_drawdown", 0),
                    "walk_forward_efficiency": entry.get("wfe", 0),
                }
                _verdict = _gate_orch.run_pipeline(_gate_input)
                _gate_verdicts.append({
                    "setup_id": setup_id,
                    "verdict": _verdict.verdict.value,
                    "quality_score": _verdict.quality_score,
                    "passed_gates": [g.name for g in _verdict.gates_passed],
                    "failed_gates": [g.name for g in _verdict.gates_failed],
                })
                # If gate chaining rejects a setup that was passing, add alert
                if _verdict.verdict.value == "REJECT" and entry.get("expectancy_r", 0) > 0.1:
                    alerts.append({
                        "code": "GATE_CHAINING_REJECT",
                        "severity": "WARNING",
                        "count": 1,
                        "details": {
                            "setup_id": setup_id,
                            "reason": f"Quality score {_verdict.quality_score:.2f} failed gates: {[g.name for g in _verdict.gates_failed]}",
                        },
                    })
        except Exception as _gate_exc:
            log.debug("GateChainingOrchestrator skipped: %s", _gate_exc)

    # Registry drift warnings for tracked rows whose fingerprint no longer matches.
    drift_rows: List[Dict[str, Any]] = []
    if registry_fingerprint:
        board = slc.status_board(path, registry_specs=registry_specs,
                                 registry_fingerprint=registry_fingerprint)
        for row in board:
            if row.get("drift"):
                drift_rows.append({"setup_id": row["setup_id"], "drift": row["drift"]})
                alerts.append({"code": "STRATEGY_REGISTRY_DRIFT", "severity": "WARNING",
                               "count": 1,
                               "details": {"setup_id": row["setup_id"],
                                           "drift": row["drift"]}})

    # ── Model Staleness Tracker (Blind Spot audit) ──────────────────────────
    model_staleness_report = {}
    if _ModelStalenessTracker is not None:
        try:
            _staleness_tracker = _ModelStalenessTracker()
            _stale_models = _staleness_tracker.get_stale_models()
            model_staleness_report = {
                "stale_models": _stale_models,
                "stale_count": len(_stale_models),
                "has_critical": any(m.get("stage") == "CRITICAL" for m in _stale_models),
            }
            if _stale_models:
                alerts.append({
                    "code": "MODEL_STALENESS_DETECTED",
                    "severity": "CRITICAL" if model_staleness_report["has_critical"] else "WARNING",
                    "count": len(_stale_models),
                    "details": {"models": _stale_models[:5]},
                })
        except Exception as _stale_exc:
            log.debug("ModelStalenessTracker skipped: %s", _stale_exc)

    enqueued: Dict[str, Any] = {"inserted": [], "count": 0}
    if alerts:
        try:
            from operations.alert_outbox import enqueue as enqueue_alerts
        except ImportError:
            from operations.alert_outbox import enqueue as enqueue_alerts
        enqueued = enqueue_alerts(path, alerts, channel="operator")

    try:
        _metrics_path = os.path.join(_root, "artifacts", "strategy_metrics.json")
        os.makedirs(os.path.dirname(_metrics_path), exist_ok=True)
        try:
            from core.file_utils import safe_atomic_write_json
            safe_atomic_write_json(_metrics_path, stats)
        except Exception:
            with open(_metrics_path, "w", encoding="utf-8") as _mfh:
                json.dump(stats, _mfh, indent=2, default=str)
    except Exception as _dump_exc:
        log.debug("Failed to export strategy_metrics.json: %s", _dump_exc)

    return {
        "evaluated_at": _now(),
        "dataset_sample_size": dataset.get("sample_size", 0),
        "evidence": {
            # Why the pass scored nothing, when it scored nothing.  A silent
            # zero is how 100+ setups sat in CANDIDATE with nobody able to say
            # whether the blocker was evidence, code, or the scheduler.
            "source": "journal_broker_reconciled_labels",
            "cost_model": dataset.get("cost_model"),
            "excluded": dataset.get("excluded", {}),
            "point_in_time": bool(dataset.get("point_in_time", False)),
            "admitted_rows": len(rows),
            "status": "READY" if rows else "NO_ADMISSIBLE_EVIDENCE",
        },
        "seeded": seed_result,
        "setups_evaluated": evaluated,
        "transitions": transitions,
        "alerts_enqueued": enqueued,
        "registry_drift": drift_rows,
        "model_staleness": model_staleness_report,
        "execution_authority": False,
    }


def run_offline(path: str, *, minimum_samples: int = MIN_RESEARCH_SAMPLE,
                max_degradation: float = MAX_DEGRADATION_FRACTION,
                registry_specs: Optional[Dict[str, Any]] = None,
                registry_fingerprint: Optional[str] = None) -> Dict[str, Any]:
    """Run one lifecycle pass with no scheduler, broker, or LLM in the loop.

    Offline means offline, not privileged.  Registry seeding stays off unless
    the caller explicitly passes specs, because ``seed_registry`` inserts
    unregistered setups straight into DEPLOYED — an operator decision that must
    never be a side effect of running an evaluation.  ``evaluate`` itself only
    ever demotes; nothing on this path promotes.
    """
    return evaluate(path, registry_specs=registry_specs,
                    registry_fingerprint=registry_fingerprint,
                    minimum_samples=minimum_samples, max_degradation=max_degradation)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Offline strategy-lifecycle evaluation.")
    parser.add_argument("--db", default=os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db"))
    parser.add_argument("--min-samples", type=int, default=MIN_RESEARCH_SAMPLE,
                        help=f"closed-sample floor per setup (policy minimum {MIN_RESEARCH_SAMPLE})")
    args = parser.parse_args(argv)
    try:
        report = run_offline(args.db, minimum_samples=args.min_samples)
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
