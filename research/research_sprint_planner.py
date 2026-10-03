"""Research sprint planner for QuantitativeCoach offline work.

Emits immutable research tickets (hypothesis, required N, embargo, cost model,
acceptance metrics) into the append-only ``research_validation_runs`` ledger.
Never grants execution authority and never changes live risk/policy.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Optional

try:
    from research_runs import record as record_research_run
except ImportError:
    from research.research_runs import record as record_research_run

try:
    from research_validation import (
        MIN_PROMOTION_EXPECTANCY_R,
        MIN_PROMOTION_PROFIT_FACTOR,
        MIN_RESEARCH_SAMPLE,
        promotion_gate,
        setup_metrics,
    )
except ImportError:
    from research.research_validation import (
        MIN_PROMOTION_EXPECTANCY_R,
        MIN_PROMOTION_PROFIT_FACTOR,
        MIN_RESEARCH_SAMPLE,
        promotion_gate,
        setup_metrics,
    )


DEFAULT_EMBARGO_SECONDS = 3600
COST_MODEL = "journal broker-reconciled fields only"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticket_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def default_acceptance(*, required_n: int = MIN_RESEARCH_SAMPLE) -> Dict[str, Any]:
    return {
        "minimum_closed_trades": int(required_n),
        "min_expectancy_r": MIN_PROMOTION_EXPECTANCY_R,
        "min_profit_factor": MIN_PROMOTION_PROFIT_FACTOR,
        "require_purged_walk_forward": True,
        "require_integrity": True,
        "require_stress_floors": True,
        "allowed_to_influence_execution": False,
    }


def plan_research_sprints(
    path: str = "trading_state.db",
    *,
    required_n: int = MIN_RESEARCH_SAMPLE,
    embargo_seconds: int = DEFAULT_EMBARGO_SECONDS,
    limit: int = 20,
) -> Dict[str, Any]:
    """Build OPEN research tickets from promotion-floor gaps and thin samples."""
    if not isinstance(required_n, int) or isinstance(required_n, bool) or required_n < MIN_RESEARCH_SAMPLE:
        required_n = MIN_RESEARCH_SAMPLE
    if not isinstance(embargo_seconds, int) or isinstance(embargo_seconds, bool) or embargo_seconds < 0:
        embargo_seconds = DEFAULT_EMBARGO_SECONDS

    gate = promotion_gate(path, minimum_closed_per_setup=required_n)
    metrics = list(gate.get("setup_metrics") or setup_metrics(path))
    tickets: List[Dict[str, Any]] = []

    # Gap tickets for setups that exist but fail floors / sample.
    for metric in metrics:
        floor_reasons = list(metric.get("floor_reasons") or [])
        if not floor_reasons and metric.get("floors_passed") is not True:
            try:
                from research_validation import _setup_floor_reasons
            except ImportError:
                from research.research_validation import _setup_floor_reasons
            floor_reasons = _setup_floor_reasons(metric, minimum_closed=required_n)
        if metric.get("floors_passed") is True or (
            not floor_reasons
            and int(metric.get("closed_trades") or 0) >= required_n
            and int(metric.get("purged_walk_forward_folds") or 0) > 0
            and float(metric.get("expectancy_r") or 0.0) > MIN_PROMOTION_EXPECTANCY_R
        ):
            continue

        hypothesis = (
            f"Validate setup {metric.get('setup_id')} on {metric.get('symbol')} "
            f"{metric.get('side')} ({metric.get('strategy')}) against purged OOS floors."
        )
        ticket = {
            "ticket_id": _ticket_id(
                metric.get("strategy"), metric.get("setup_id"), metric.get("symbol"),
                metric.get("side"), "gap", _now()[:10],
            ),
            "kind": "SETUP_VALIDATION_GAP",
            "status": "OPEN",
            "hypothesis": hypothesis,
            "strategy": metric.get("strategy"),
            "setup_id": metric.get("setup_id"),
            "symbol": metric.get("symbol"),
            "side": metric.get("side"),
            "required_n": required_n,
            "current_n": int(metric.get("closed_trades") or 0),
            "embargo_seconds": embargo_seconds,
            "cost_model": COST_MODEL,
            "gap_reasons": floor_reasons or ["awaiting_promotion_floors"],
            "acceptance": default_acceptance(required_n=required_n),
            "created_at": _now(),
            "execution_authority": False,
        }
        tickets.append(ticket)
        if len(tickets) >= limit:
            break

    # If journal is empty, emit a bootstrap sprint so the coach still has work.
    if not tickets:
        tickets.append({
            "ticket_id": _ticket_id("bootstrap", path, _now()[:10]),
            "kind": "BOOTSTRAP_LABELLED_DATASET",
            "status": "OPEN",
            "hypothesis": (
                "Accumulate >= "
                f"{required_n} labelled, point-in-time, broker-reconciled closed trades "
                "before any promotion review."
            ),
            "strategy": None,
            "setup_id": None,
            "symbol": None,
            "side": None,
            "required_n": required_n,
            "current_n": int((gate.get("dataset") or {}).get("sample_size") or 0),
            "embargo_seconds": embargo_seconds,
            "cost_model": COST_MODEL,
            "gap_reasons": ["insufficient_labelled_sample"],
            "acceptance": default_acceptance(required_n=required_n),
            "created_at": _now(),
            "execution_authority": False,
        })

    report = {
        "status": "PLANNED",
        "kind": "research_sprint_plan",
        "created_at": _now(),
        "ticket_count": len(tickets),
        "tickets": tickets,
        "promotion_snapshot": {
            "integrity_passed": gate.get("integrity_passed"),
            "eligible_setups": len(gate.get("eligible_setups") or []),
            "dataset_sample_size": (gate.get("dataset") or {}).get("sample_size"),
            "floors": gate.get("floors"),
        },
        "configuration": {
            "required_n": required_n,
            "embargo_seconds": embargo_seconds,
            "cost_model": COST_MODEL,
        },
        "dataset": gate.get("dataset") or {},
        "execution_authority": False,
        "limitation": "Sprint tickets are offline research work orders only.",
    }
    return report


def persist_research_sprints(path: str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Plan (if needed) and append the sprint bundle to research_runs."""
    plan = plan or plan_research_sprints(path)
    ledger = record_research_run(path, "research_sprint", plan)
    return {
        **ledger,
        "ticket_count": plan.get("ticket_count", 0),
        "tickets": plan.get("tickets", []),
        "execution_authority": False,
    }


def plan_and_persist(path: str = "trading_state.db", **kwargs: Any) -> Dict[str, Any]:
    plan = plan_research_sprints(path, **kwargs)
    return persist_research_sprints(path, plan)
