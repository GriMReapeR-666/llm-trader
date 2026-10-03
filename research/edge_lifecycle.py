"""Unified macro-edge lifecycle transitions (research / governance only).

Lifecycle is recorded in ``macro_edge_history`` and never grants
``execution_authority``.  Existing journal statuses (CANDIDATE, AUTO_APPROVED,
…) remain compatible; lifecycle stages are a stricter promotion path.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

try:
    from macro_graph import MacroGraph, utcnow
except ImportError:
    from intelligence.macro_graph import MacroGraph, utcnow


LIFECYCLE_STAGES: FrozenSet[str] = frozenset({
    "CANDIDATE",
    "MEASURED",
    "STAT_PASS",
    "STAT_FAIL",
    "REPLAY_PASS",
    "REPLAY_FAIL",
    "DEMO_MATCH",
    "TINY_LIVE",
    "FULL",
    "RETIRED",
})

ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "CANDIDATE": frozenset({"MEASURED", "STAT_PASS", "STAT_FAIL", "RETIRED"}),
    "MEASURED": frozenset({"STAT_PASS", "STAT_FAIL", "RETIRED"}),
    "STAT_PASS": frozenset({"REPLAY_PASS", "REPLAY_FAIL", "RETIRED"}),
    "STAT_FAIL": frozenset({"CANDIDATE", "RETIRED"}),
    "REPLAY_PASS": frozenset({"DEMO_MATCH", "RETIRED"}),
    "REPLAY_FAIL": frozenset({"STAT_PASS", "RETIRED"}),
    "DEMO_MATCH": frozenset({"TINY_LIVE", "RETIRED"}),
    "TINY_LIVE": frozenset({"FULL", "RETIRED"}),
    "FULL": frozenset({"RETIRED"}),
    "RETIRED": frozenset(),
}

# Map legacy / validator statuses onto lifecycle stages.
STATUS_TO_LIFECYCLE = {
    "CANDIDATE": "CANDIDATE",
    "INSUFFICIENT_SAMPLE": "CANDIDATE",
    "AUTO_APPROVED": "STAT_PASS",
    "APPROVED": "STAT_PASS",
    "REJECTED_STATISTICAL": "STAT_FAIL",
    "REJECTED_SOURCE_POLICY": "STAT_FAIL",
    "RETIRED": "RETIRED",
}


def normalize_lifecycle(status_or_stage: str) -> str:
    key = str(status_or_stage or "").strip().upper()
    if key in LIFECYCLE_STAGES:
        return key
    return STATUS_TO_LIFECYCLE.get(key, "CANDIDATE")


def can_transition(current: str, target: str) -> bool:
    cur = normalize_lifecycle(current)
    nxt = normalize_lifecycle(target)
    if cur == nxt:
        return True
    return nxt in ALLOWED_TRANSITIONS.get(cur, frozenset())


def current_lifecycle(graph: MacroGraph, edge_id: int) -> Dict[str, Any]:
    with graph._con() as con:
        row = con.execute(
            "SELECT status FROM macro_edges WHERE edge_id=?",
            (int(edge_id),),
        ).fetchone()
        hist = con.execute(
            """SELECT status, payload FROM macro_edge_history
               WHERE edge_id=? ORDER BY history_id DESC LIMIT 20""",
            (int(edge_id),),
        ).fetchall()
    if not row:
        return {"edge_id": edge_id, "status": "MISSING", "lifecycle": None,
                "execution_authority": False}
    lifecycle = None
    for item in hist:
        payload = item["payload"]
        try:
            import json
            data = json.loads(payload or "{}")
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("lifecycle"):
            lifecycle = normalize_lifecycle(str(data["lifecycle"]))
            break
    if lifecycle is None:
        lifecycle = normalize_lifecycle(str(row["status"]))
    return {
        "edge_id": int(edge_id),
        "status": str(row["status"]),
        "lifecycle": lifecycle,
        "execution_authority": False,
    }


def advance_lifecycle(
    graph: MacroGraph,
    edge_id: int,
    target: str,
    *,
    reviewer: str = "EDGE_LIFECYCLE",
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append a lifecycle transition; refuses illegal jumps (fail closed)."""
    state = current_lifecycle(graph, edge_id)
    if state.get("lifecycle") is None:
        return {**state, "advanced": False, "reason": "edge not found",
                "execution_authority": False}
    target_stage = normalize_lifecycle(target)
    if not can_transition(str(state["lifecycle"]), target_stage):
        return {
            "edge_id": int(edge_id),
            "advanced": False,
            "from": state["lifecycle"],
            "to": target_stage,
            "reason": f"illegal lifecycle transition {state['lifecycle']}→{target_stage}",
            "execution_authority": False,
        }
    payload = {
        "lifecycle": target_stage,
        "from": state["lifecycle"],
        "evidence": evidence or {},
        "execution_authority": False,
    }
    now = utcnow()
    with graph._con() as con:
        con.execute(
            """INSERT INTO macro_edge_history(edge_id, changed_at, action, status, reviewer, payload)
               VALUES(?, ?, 'LIFECYCLE', ?, ?, ?)""",
            (int(edge_id), now, str(state["status"]), str(reviewer)[:200], graph._json(payload)),
        )
    return {
        "edge_id": int(edge_id),
        "advanced": True,
        "from": state["lifecycle"],
        "to": target_stage,
        "execution_authority": False,
    }
