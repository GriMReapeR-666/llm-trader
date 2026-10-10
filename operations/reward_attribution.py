"""Offline causal / opportunity credit attribution (Phase C).

Uses journaled ``causal_edge_ids`` and ``opportunity_candidate_id`` to assign
shares of reconciled ``R_net``. Never grants ``execution_authority``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

try:
    from edge_lifecycle import normalize_lifecycle
except ImportError:
    from research.edge_lifecycle import normalize_lifecycle

try:
    from reward_ledger import CREDIT_SHARES, list_reward_events
except ImportError:
    from operations.reward_ledger import CREDIT_SHARES, list_reward_events

try:
    from research_validation import labelled_dataset
except ImportError:
    from research.research_validation import labelled_dataset

try:
    from reward_ledger import emit_terminal_trade_rewards
except ImportError:
    from operations.reward_ledger import emit_terminal_trade_rewards


# Lifecycle rank for ≥ REPLAY_PASS gate (formula: 1{lifecycle≥REPLAY_PASS}).
_LIFECYCLE_RANK = {
    "CANDIDATE": 0,
    "MEASURED": 1,
    "STAT_PASS": 2,
    "STAT_FAIL": -1,
    "REPLAY_PASS": 3,
    "REPLAY_FAIL": -1,
    "DEMO_MATCH": 4,
    "TINY_LIVE": 5,
    "FULL": 6,
    "RETIRED": -2,
    "MISSING": -3,
}

MIN_CAUSAL_CREDIT_LIFECYCLE = "REPLAY_PASS"
FALSE_POSITIVE_PENALTY = 0.05  # λ_fp for emitted∧rejected_for_quality (offline)


def lifecycle_rank(stage: Optional[str]) -> int:
    return _LIFECYCLE_RANK.get(normalize_lifecycle(str(stage or "")), -3)


def lifecycle_qualifies_for_causal_credit(stage: Optional[str]) -> bool:
    return lifecycle_rank(stage) >= lifecycle_rank(MIN_CAUSAL_CREDIT_LIFECYCLE)


def extract_attribution_ids(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pull edge/candidate ids from a durable trade payload or features blob."""
    if not isinstance(payload, dict):
        return {
            "causal_edge_ids": [],
            "candidate_id": None,
            "execution_authority": False,
        }
    features = payload.get("proposal_features") or payload.get("features") or {}
    if not isinstance(features, dict):
        features = {}
    raw_edges = (
        features.get("causal_edge_ids")
        or payload.get("causal_edge_ids")
        or []
    )
    edge_ids: List[int] = []
    if isinstance(raw_edges, list):
        for item in raw_edges:
            try:
                edge_ids.append(int(item))
            except (TypeError, ValueError):
                continue
    candidate = (
        features.get("opportunity_candidate_id")
        or features.get("candidate_id")
        or payload.get("opportunity_candidate_id")
        or payload.get("candidate_id")
    )
    return {
        "causal_edge_ids": edge_ids[:5],
        "candidate_id": str(candidate) if candidate else None,
        "execution_authority": False,
    }


def enrich_trade_result_attribution(
    trade_result: Dict[str, Any],
    store: Any = None,
) -> Dict[str, Any]:
    """Ensure close-path reward emit has edge/candidate linkage from the journal."""
    out = dict(trade_result or {})
    existing_edges = out.get("causal_edge_ids") or []
    existing_cand = out.get("candidate_id") or out.get("opportunity_candidate_id")
    if existing_edges and existing_cand:
        out["causal_edge_ids"] = list(existing_edges) if isinstance(existing_edges, list) else []
        out["candidate_id"] = str(existing_cand)
        return out

    payload: Dict[str, Any] = {}
    ticket = out.get("ticket")
    if store is not None and ticket is not None and hasattr(store, "trade_payload"):
        try:
            payload = store.trade_payload(int(ticket)) or {}
        except (TypeError, ValueError):
            payload = {}
    extracted = extract_attribution_ids(payload if isinstance(payload, dict) else {})
    if not existing_edges:
        out["causal_edge_ids"] = extracted["causal_edge_ids"]
    elif not isinstance(existing_edges, list):
        out["causal_edge_ids"] = extracted["causal_edge_ids"]
    if not existing_cand:
        out["candidate_id"] = extracted["candidate_id"]
    else:
        out["candidate_id"] = str(existing_cand)
    return out


def eligible_edge_ids(
    edge_ids: Sequence[Any],
    *,
    lifecycle_lookup: Optional[Callable[[int], Optional[str]]] = None,
) -> List[int]:
    """Filter edges that meet the REPLAY_PASS lifecycle floor for causal credit."""
    parsed: List[int] = []
    for raw in edge_ids or []:
        try:
            parsed.append(int(raw))
        except (TypeError, ValueError):
            continue
    if lifecycle_lookup is None:
        return parsed
    eligible: List[int] = []
    for edge_id in parsed:
        try:
            stage = lifecycle_lookup(edge_id)
        except Exception:
            stage = None
        if lifecycle_qualifies_for_causal_credit(stage):
            eligible.append(edge_id)
    return eligible


def causal_credit_shares(
    r_net: float,
    edge_ids: Sequence[int],
    *,
    agent_share: float = CREDIT_SHARES["CausalValidator"],
) -> List[Dict[str, Any]]:
    """Split CausalValidator weight evenly across eligible linked edges."""
    if not edge_ids:
        return []
    n = len(edge_ids)
    per = (float(agent_share) * float(r_net)) / n
    return [
        {
            "edge_id": int(edge_id),
            "value": per,
            "share": float(agent_share) / n,
            "r_net": float(r_net),
            "execution_authority": False,
        }
        for edge_id in edge_ids
    ]


def score_edges_from_ledger(
    path: str,
    *,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Aggregate offline edge scorer from L2 credit / terminal rows with edge_id."""
    events = list_reward_events(path, layer="L2", limit=limit)
    by_edge: Dict[int, Dict[str, Any]] = {}
    for event in events:
        edge_raw = event.get("edge_id")
        if edge_raw is None:
            continue
        try:
            edge_id = int(edge_raw)
        except (TypeError, ValueError):
            continue
        bucket = by_edge.setdefault(
            edge_id,
            {"edge_id": edge_id, "n": 0, "sum_r": 0.0, "sum_credit": 0.0, "tickets": set()},
        )
        value = float(event.get("value") or 0.0)
        action = str(event.get("action") or "")
        if action == "terminal_trade_close":
            bucket["sum_r"] += value
            bucket["n"] += 1
        elif action in {"terminal_credit_share", "causal_edge_credit"}:
            bucket["sum_credit"] += value
        ticket = event.get("ticket")
        if ticket is not None:
            bucket["tickets"].add(int(ticket))
    scored = []
    for edge_id, bucket in sorted(by_edge.items()):
        n = int(bucket["n"]) or len(bucket["tickets"])
        mean_r = (bucket["sum_r"] / n) if n else 0.0
        scored.append({
            "edge_id": edge_id,
            "sample_n": n,
            "ticket_n": len(bucket["tickets"]),
            "sum_terminal_r": float(bucket["sum_r"]),
            "mean_terminal_r": float(mean_r),
            "sum_credit": float(bucket["sum_credit"]),
            "positive_expectancy": mean_r > 0.0 and n >= 5,
            "execution_authority": False,
        })
    return {
        "status": "OK",
        "edges": scored,
        "edge_count": len(scored),
        "execution_authority": False,
        "kind": "offline_edge_scorer",
    }


def score_opportunities_from_ledger(
    path: str,
    *,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Aggregate opportunity precision scorer by candidate_id."""
    events = list_reward_events(path, layer="L2", limit=limit)
    by_cand: Dict[str, Dict[str, Any]] = {}
    for event in events:
        cand = event.get("candidate_id")
        if not cand:
            continue
        key = str(cand)
        bucket = by_cand.setdefault(
            key,
            {"candidate_id": key, "n": 0, "sum_r": 0.0, "sum_credit": 0.0},
        )
        value = float(event.get("value") or 0.0)
        action = str(event.get("action") or "")
        if action == "terminal_trade_close":
            bucket["sum_r"] += value
            bucket["n"] += 1
        elif action in {"terminal_credit_share", "opportunity_precision_credit"}:
            bucket["sum_credit"] += value
    scored = []
    for key, bucket in sorted(by_cand.items()):
        n = int(bucket["n"])
        mean_r = (bucket["sum_r"] / n) if n else 0.0
        scored.append({
            "candidate_id": key,
            "sample_n": n,
            "sum_terminal_r": float(bucket["sum_r"]),
            "mean_terminal_r": float(mean_r),
            "sum_credit": float(bucket["sum_credit"]),
            "precision_positive": mean_r > 0.0 and n >= 1,
            "execution_authority": False,
        })
    return {
        "status": "OK",
        "candidates": scored,
        "candidate_count": len(scored),
        "execution_authority": False,
        "kind": "offline_opportunity_scorer",
    }


def opportunity_precision_reward(
    *,
    approved: bool,
    r_net: float,
    emitted: bool,
    rejected_for_quality: bool,
    weight: float = CREDIT_SHARES["TechnicalNet"],
    lambda_fp: float = FALSE_POSITIVE_PENALTY,
) -> Dict[str, Any]:
    """Offline formula: r_opp = w_o · (1{approved}·R_net − λ_fp·1{emitted∧rejected})."""
    term = 0.0
    if approved:
        term += float(r_net)
    if emitted and rejected_for_quality:
        term -= float(lambda_fp)
    value = float(weight) * term
    return {
        "value": value,
        "weight": float(weight),
        "term": term,
        "approved": bool(approved),
        "emitted": bool(emitted),
        "rejected_for_quality": bool(rejected_for_quality),
        "execution_authority": False,
        "kind": "opportunity_precision",
    }


def match_open_candidate_id(
    open_candidates: Iterable[Dict[str, Any]],
    *,
    symbol: str,
    setup_id: Optional[str] = None,
    side: Optional[str] = None,
) -> Optional[str]:
    """Pick the best open bus candidate for a proposal (symbol + optional filters)."""
    symbol_u = str(symbol or "").upper()
    setup_u = str(setup_id or "").strip()
    side_u = str(side or "").upper()
    matches: List[Dict[str, Any]] = []
    for item in open_candidates or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol") or "").upper() != symbol_u:
            continue
        if setup_u and str(item.get("setup_id") or "") not in {"", setup_u}:
            continue
        side_hint = str(item.get("side_hint") or "NONE").upper()
        if side_u in {"BUY", "SELL"} and side_hint in {"BUY", "SELL"} and side_hint != side_u:
            continue
        matches.append(item)
    if not matches:
        return None
    # Prefer highest score then freshest emission.
    matches.sort(
        key=lambda m: (float(m.get("score") or 0.0), str(m.get("emitted_at") or "")),
        reverse=True,
    )
    return str(matches[0].get("candidate_id") or "") or None


# ── Cycle rotation ──────────────────────────────────────────────────────────
# The ledger's only emitter used to be the live close callback, so the last
# reward rows carried run_ids from runs that ended weeks ago and every PPO /
# GRPO / bandit consumer replayed the same dead cycle.  ``attribute_reconciled_history``
# re-derives the same terminal reward from *current* broker-reconciled journal
# history and stamps it with the caller's live run reference.  It is deliberately
# built on ``research_validation.labelled_dataset`` rather than a private reader:
# RL must not learn from a row the research gate would refuse to promote on.

TERMINAL_CLOSE_ACTION = "terminal_trade_close"


def ledger_freshness(path: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Age of the newest reward event, plus the run refs that went stale.

    This is the bottom-line metric from the engine audit (``reward_events``
    freshness < 24h) expressed as something a scheduler can assert on.
    """
    empty = {"freshness_hours": None, "oldest_run_ids": [], "event_count": 0,
             "stale_after_hours": 24.0, "fresh": False, "execution_authority": False}
    if not os.path.exists(path):
        return empty
    try:
        from pathlib import Path
        uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as con:
            newest, count = con.execute(
                "SELECT MAX(created_at), COUNT(*) FROM reward_events").fetchone()
            run_rows = con.execute(
                "SELECT run_id, MAX(created_at) FROM reward_events WHERE run_id IS NOT NULL "
                "GROUP BY run_id").fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return empty
    if not newest:
        return empty
    stamp = now or datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_h = (stamp.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0
    except (TypeError, ValueError, OverflowError):
        return empty
    stale = []
    for run_id, last_seen in run_rows:
        try:
            seen = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            run_age_h = (stamp.astimezone(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds() / 3600.0
        except (TypeError, ValueError, OverflowError):
            continue
        if run_age_h >= 24.0:
            stale.append({"run_id": str(run_id), "age_hours": round(run_age_h, 3)})
    stale.sort(key=lambda item: (-item["age_hours"], item["run_id"]))
    return {
        "freshness_hours": round(age_h, 3),
        "oldest_run_ids": stale[:20],
        "event_count": int(count or 0),
        "stale_after_hours": 24.0,
        "fresh": age_h < 24.0,
        "execution_authority": False,
    }


def attribute_reconciled_history(
    path: str,
    *,
    run_id: Optional[str] = None,
    since_iso: Optional[str] = None,
    limit: int = 1000,
    emit_credit_shares: bool = True,
    lifecycle_lookup: Optional[Callable[[int], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Emit L2 terminal rewards for reconciled closes this cycle has not seen.

    ``compute_terminal_reward`` refuses any close that is not marked
    broker-reconciled, and admission here goes through
    ``labelled_dataset``, so this can only ever re-emit a close the journal has
    already proven against a broker.  Tickets that already carry a terminal
    close are skipped, which keeps an hourly scheduler from multiplying rows.
    """
    dataset = labelled_dataset(path)
    rows = list(dataset.get("rows") or [])
    if since_iso:
        rows = [row for row in rows if str(row.get("closed_at") or "") >= str(since_iso)]
    rows = rows[-max(0, int(limit)):] if limit else rows

    emitted: List[Dict[str, Any]] = []
    skipped_existing = 0
    skipped_unlabelled = 0
    for row in rows:
        ticket = row.get("ticket")
        try:
            ticket_i = int(ticket)
        except (TypeError, ValueError):
            continue
        try:
            prior = list_reward_events(path, ticket=ticket_i, layer="L2", limit=50)
        except (sqlite3.Error, OSError, ValueError):
            continue
        if any(str(event.get("action") or "") == TERMINAL_CLOSE_ACTION for event in prior):
            skipped_existing += 1
            continue
        trade_result = {
            "ticket": ticket_i,
            "pnl": float(row.get("pnl") or 0.0),
            "risk": float(row.get("risk_amount") or 0.0),
            "r_multiple": row.get("r_multiple"),
            "broker_reconciled": True,
            "symbol": row.get("symbol"),
            "setup_id": row.get("setup_id"),
        }
        trade_result = enrich_trade_result_attribution(trade_result)
        try:
            recorded = emit_terminal_trade_rewards(
                path, trade_result, run_id=run_id, emit_credit_shares=emit_credit_shares,
                lifecycle_lookup=lifecycle_lookup,
            )
        except (sqlite3.Error, OSError, ValueError):
            skipped_unlabelled += 1
            continue
        if str(recorded.get("status") or "") == "RECORDED":
            emitted.append({"ticket": ticket_i,
                            "reward": recorded.get("reward"),
                            "r_net": (recorded.get("breakdown") or {}).get("r_net"),
                            "events": len(recorded.get("events") or [])})
        else:
            skipped_unlabelled += 1

    return {
        "status": "ATTRIBUTED" if emitted else "NO_NEW_RECONCILED_CLOSES",
        "run_id": run_id,
        "candidates": len(rows),
        "emitted": emitted,
        "emitted_count": len(emitted),
        "skipped_already_attributed": skipped_existing,
        "skipped_not_admissible": skipped_unlabelled,
        "dataset_sample_size": dataset.get("sample_size", 0),
        "dataset_excluded": dataset.get("excluded", {}),
        "freshness": ledger_freshness(path),
        "execution_authority": False,
    }
