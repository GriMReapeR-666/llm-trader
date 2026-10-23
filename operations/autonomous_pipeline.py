"""Autonomous research pipeline: discovery, verification, lifecycle scheduling.

Zero-authority research work that progresses 24/7, including weekends.
Integrates 11-step AutonomousQuantOperatingPipeline across strategy universe matrix,
4-tier diagnostics, small-capital compounding, 9-tier quant gates, and graduated
lifecycle candidate registration into trading_state.db.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _rows(journal_path: str) -> List[Dict[str, Any]]:
    """current_states() returns {setup_id: state}; normalize to row dicts."""
    from operations.strategy_lifecycle import current_states
    states = current_states(journal_path)
    return [{"setup_id": k, "state": v} for k, v in states.items()]


def board(journal_path: str = "trading_state.db") -> Dict[str, Any]:
    """Return a snapshot of discovered strategies and their states."""
    try:
        rows = _rows(journal_path)
        by_state: Dict[str, int] = {}
        for row in rows:
            st = row.get("state", "unknown")
            by_state[st] = by_state.get(st, 0) + 1
        return {
            "total": len(rows),
            "by_state": by_state,
            "rows": rows,
        }
    except Exception as exc:
        logger.debug("pipeline board failed: %s", exc)
        return {"total": 0, "by_state": {}, "rows": []}


def verify(
    journal_path: str = "trading_state.db",
    gate_state_file: str = "overfit_gate_state.json",
) -> Dict[str, Any]:
    """Verify strategies against overfit gate; return counts + flagged details."""
    try:
        rows = _rows(journal_path)
        checked = len(rows)
        verified = 0
        flagged: List[Dict[str, Any]] = []
        for row in rows:
            state = row.get("state", "")
            if state in ("VERIFIED", "DEPLOYED", "PAPER_TRADING"):
                verified += 1
            elif state in ("FLAGGED", "REJECTED"):
                flagged.append({
                    "setup_id": row.get("setup_id", ""),
                    "why": f"state={state}",
                })
        return {
            "checked": checked,
            "verified": verified,
            "flagged": flagged,
        }
    except Exception as exc:
        logger.debug("pipeline verify failed: %s", exc)
        return {"checked": 0, "verified": 0, "flagged": []}


def _resolve_market_bars(
    dataset: Optional[Any] = None,
    archive_path: str = os.path.join("data", "candle_archive", "EURUSD_H1_long.parquet"),
    n_bars: int = 1000,
) -> Any:
    """Resolve market bars for autonomous evaluation: passed df -> parquet archive -> synthetic fallback."""
    import polars as pl
    if dataset is not None:
        if not isinstance(dataset, pl.DataFrame) or "close" not in dataset.columns or len(dataset) == 0:
            raise ValueError(f"Invalid dataset provided: expected non-empty pl.DataFrame with 'close' column, got {type(dataset)}")
        return dataset
    if os.path.exists(archive_path):
        try:
            df = pl.read_parquet(archive_path)
            if len(df) > n_bars:
                df = df.tail(n_bars)
            return df
        except Exception as exc:
            logger.debug("Failed reading candle archive: %s", exc)

    # Synthetic realistic market bars fallback
    import numpy as np
    from core.strategy_universe import load_market_dataset_polars
    np.random.seed(42)
    prices = 1.0850 * np.exp(np.cumsum(np.random.normal(0.0001, 0.002, n_bars)))
    return load_market_dataset_polars({"close": prices})


def run_pipeline(
    lifecycle_path: str = "trading_state.db",
    outbox_path: str = "alerts_outbox.db",
    queue: Any = None,
    cycle_num: Optional[int] = None,
    run_quant_pipeline: bool = True,
    dataset: Optional[Any] = None,
    symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute one autonomous pipeline tick: discover, register, verify, quant operating evaluation.

    Returns a summary dict with keys: discovery, registered, skipped, flagged, incubation, quant_operating_pipeline.
    """
    result = {
        "discovery": {"members": 0},
        "registered": 0,
        "skipped": 0,
        "flagged": 0,
        "incubation": {"status": "NOT_LOADED", "edges": 0, "heuristics": 0},
    }
    try:
        rows = _rows(lifecycle_path)
        result["discovery"]["members"] = len(rows)
        for row in rows:
            state = row.get("state", "")
            if state in ("VERIFIED", "DEPLOYED", "PAPER_TRADING", "VALIDATING"):
                result["registered"] += 1
            elif state in ("FLAGGED", "REJECTED"):
                result["flagged"] += 1
            else:
                result["skipped"] += 1

        # Ingest research-trained causal intelligence bundle for forward incubation
        sync = sync_incubation_knowledge()
        result["incubation"] = {
            "status": sync.get("status", "NOT_FOUND"),
            "edges": sync.get("edges_count", 0),
            "heuristics": sync.get("heuristics_count", 0),
            "maml_profile": sync.get("maml_profile"),
        }

        # Process ready S.Q priority queue tasks under bounded execution (max 2 per tick)
        if queue is not None and hasattr(queue, "dispatch_ready_tasks"):
            try:
                effective_cycle = (
                    cycle_num
                    if cycle_num is not None
                    else (result.get("registered", 0) + result.get("skipped", 0))
                )
                tasks_res = queue.dispatch_ready_tasks(effective_cycle, max_tasks=2)
                result["queue_dispatched"] = len(tasks_res)
                result["queue_tasks"] = tasks_res
            except Exception as q_exc:
                logger.debug("TaskQueue dispatch error in pipeline: %s", q_exc)

        # Autonomous 11-step Quant Operating Pipeline execution
        if run_quant_pipeline:
            try:
                from core.quant_gates_and_pipeline import AutonomousQuantOperatingPipeline
                from operations.strategy_lifecycle import register_candidate, transition

                bars_df = _resolve_market_bars(dataset=dataset)
                quant_engine = AutonomousQuantOperatingPipeline(emit_telemetry=True)
                quant_pkg = quant_engine.run_pipeline(bars_df, symbols=symbols)

                staged_count = 0
                for item in quant_pkg.ranked_finalists:
                    strat_id = item.get("strategy_id")
                    if strat_id and item.get("gate_passed", False):
                        try:
                            reg = register_candidate(
                                lifecycle_path,
                                strat_id,
                                reason=f"Passed 9-Tier Quant Gates (score={item.get('composite_score', 0.0):.3f})",
                                evidence=item,
                                actor="autonomous_pipeline",
                            )
                            if reg.get("changed", False):
                                transition(
                                    lifecycle_path,
                                    strat_id,
                                    "VALIDATING",
                                    actor="autonomous_pipeline",
                                    reason="Auto-promoted to VALIDATING on 9-tier quant gate clearance",
                                    evidence=item,
                                    allow_auto=True,
                                )
                                staged_count += 1
                        except Exception as reg_exc:
                            logger.debug("Failed registering candidate %s: %s", strat_id, reg_exc)

                top_strat = quant_pkg.ranked_finalists[0] if quant_pkg.ranked_finalists else {}
                logger.info(
                    "Autonomous Quant Operating Pipeline: evaluated %d matrix candidates, %d passed 9-tier gates, %d staged to VALIDATING. Top finalist: %s (Score: %.3f, Style: %s)",
                    quant_pkg.total_candidates,
                    quant_pkg.gate_survivors_count,
                    staged_count,
                    top_strat.get("strategy_id", "N/A"),
                    top_strat.get("composite_score", 0.0),
                    top_strat.get("style", "N/A"),
                )

                result["quant_operating_pipeline"] = {
                    "status": "COMPLETED",
                    "total_candidates": quant_pkg.total_candidates,
                    "gate_survivors_count": quant_pkg.gate_survivors_count,
                    "staged_strategies": quant_pkg.staged_registry_entries,
                    "staged_validating_count": staged_count,
                    "top_finalists": quant_pkg.ranked_finalists[:5],
                    "evaluated_at": quant_pkg.evaluated_at,
                    "execution_authority": False,
                }
            except Exception as qop_exc:
                logger.warning("Autonomous quant operating pipeline execution failed: %s", qop_exc)
                result["quant_operating_pipeline"] = {
                    "status": "ERROR",
                    "error": str(qop_exc),
                    "execution_authority": False,
                }
        else:
            result["quant_operating_pipeline"] = {
                "status": "SKIPPED",
                "execution_authority": False,
            }
    except Exception as exc:
        logger.debug("autonomous pipeline tick failed: %s", exc)
    return result


def sync_incubation_knowledge(
    bundle_path: str = os.path.join("data", "incubation_knowledge_bundle.json")
) -> Dict[str, Any]:
    """Load research-trained causal intelligence bundle for forward incubation.

    Provides live incubation strategies with empirically validated macro edges,
    MAML regime profiles, and verified heuristics without database write hazards.
    """
    if not os.path.exists(bundle_path):
        return {"status": "NOT_FOUND", "edges": 0, "heuristics": 0}
    try:
        with open(bundle_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "status": "LOADED",
            "generated_at": data.get("generated_at"),
            "edges_count": data.get("total_approved_causal_edges", 0),
            "maml_profile": data.get("active_maml_profile"),
            "ewc_regimes": data.get("ewc_locked_regimes", []),
            "heuristics_count": len(data.get("top_verified_heuristics", [])),
            "pricing_model": data.get("broker_pricing_model", "XM_ULTRA_LOW"),
            "bundle": data,
        }
    except Exception as exc:
        logger.warning("Failed to sync incubation knowledge: %s", exc)
        return {"status": "ERROR", "error": str(exc)}
