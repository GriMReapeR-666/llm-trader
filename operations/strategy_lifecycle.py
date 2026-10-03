"""Durable strategy-lifecycle state machine with append-only history.

One authoritative table answers "what is the status of every setup/strategy":
CANDIDATE -> VALIDATING -> PAPER_PROVEN -> DEMO_PROVEN -> DEPLOYED -> DECAYING
-> FROZEN -> RETIRED.

Design rules inherited from the wider system:

- The automated evaluation engine may only *demote* (DEPLOYED -> DECAYING ->
  FROZEN). Every promotion, unfreeze, or retirement is an explicit operator
  transition through the CLI; nothing here grants order permission.
- FROZEN/RETIRED setups are blocked from new deterministic validation and the
  block is loud: a CRITICAL alert is enqueued at freeze time.
- History is append-only (SQLite triggers reject UPDATE/DELETE), so lifecycle
  decisions remain replayable evidence.
- Registered code setups are seeded as DEPLOYED bound to a content fingerprint;
  when the code registry changes, the board reports REGISTRY_DRIFT until an
  operator re-acknowledges the row.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional


STATES = [
    "CANDIDATE", "VALIDATING", "PAPER_PROVEN", "DEMO_PROVEN", "READY_FOR_DEPLOYMENT",
    "DEPLOYED", "DECAYING", "FROZEN", "RETIRED",
]
BLOCKING_STATES = {"FROZEN", "RETIRED"}
TERMINAL_STATES = {"RETIRED"}

# Graduated authority. Automation may move a setup up to PAPER_PROVEN, where
# the evidence is machine-checkable, and may take only these demotions.
# DEMO_PROVEN -> READY_FOR_DEPLOYMENT -> DEPLOYED stays operator-only: deployment
# is a human call even when every gate is green.
AUTO_TRANSITIONS = {
    ("CANDIDATE", "VALIDATING"),
    ("VALIDATING", "PAPER_PROVEN"),
    ("DEPLOYED", "DECAYING"),
    ("DECAYING", "FROZEN"),
}

OPERATOR_TRANSITIONS = {
    ("CANDIDATE", "VALIDATING"),
    ("VALIDATING", "PAPER_PROVEN"),
    ("PAPER_PROVEN", "DEMO_PROVEN"),
    ("DEMO_PROVEN", "READY_FOR_DEPLOYMENT"),
    ("DEMO_PROVEN", "DEPLOYED"),
    ("READY_FOR_DEPLOYMENT", "DEPLOYED"),
    ("DEPLOYED", "DECAYING"),
    ("DEPLOYED", "RETIRED"),
    ("DECAYING", "FROZEN"),
    ("DECAYING", "DEPLOYED"),      # operator recovery after review
    ("FROZEN", "DEPLOYED"),        # explicit operator unfreeze
    ("FROZEN", "RETIRED"),
    # Operator rollback of a promotion that rested on bad evidence. Without
    # these edges the ladder could only move forward, so a mis-promoted row
    # could only be demoted as far as DECAYING/FROZEN/RETIRED and never
    # returned to VALIDATING for re-evaluation.
    ("PAPER_PROVEN", "VALIDATING"),
    ("DEMO_PROVEN", "VALIDATING"),
    ("READY_FOR_DEPLOYMENT", "VALIDATING"),
}

MAX_REASON_CHARS = 2000
MAX_EVIDENCE_CHARS = 8000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int) -> str:
    return str(value if value is not None else "")[:limit]


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS strategy_lifecycle(
        setup_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        version_fingerprint TEXT,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        reason TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS strategy_lifecycle_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setup_id TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT,
        evidence TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_strategy_lifecycle_history_setup
        ON strategy_lifecycle_history(setup_id, created_at)""")
    con.execute("""CREATE TRIGGER IF NOT EXISTS strategy_lifecycle_history_immutable_update
        BEFORE UPDATE ON strategy_lifecycle_history BEGIN
            SELECT RAISE(ABORT, 'strategy lifecycle history is append-only');
        END;""")
    con.execute("""CREATE TRIGGER IF NOT EXISTS strategy_lifecycle_history_immutable_delete
        BEFORE DELETE ON strategy_lifecycle_history BEGIN
            SELECT RAISE(ABORT, 'strategy lifecycle history is append-only');
        END;""")
    con.commit()
    return con


def _validate_state(state: Any) -> str:
    state = str(state or "").strip().upper()
    if state not in STATES:
        raise ValueError(f"unknown lifecycle state '{state}'; valid states: {STATES}")
    return state


def seed_registry(path: str, specs: Dict[str, Any], *,
                  fingerprint: str, actor: str = "system") -> Dict[str, Any]:
    """Insert any registered setup missing from the table as DEPLOYED.

    Existing rows are never overwritten here; registry content changes are
    surfaced as drift by :func:`status_board` instead of silent mutation.
    """
    fingerprint = _clean(fingerprint, 128)
    if not fingerprint:
        raise ValueError("seed_registry requires a non-empty registry fingerprint")
    inserted: List[str] = []
    con = _connect(path)
    try:
        for name in sorted(str(key) for key in specs):
            row = con.execute(
                "SELECT setup_id FROM strategy_lifecycle WHERE setup_id=?", (name,)
            ).fetchone()
            if row is not None:
                continue
            now = _now()
            con.execute("""INSERT INTO strategy_lifecycle
                (setup_id,state,version_fingerprint,updated_at,updated_by,reason)
                VALUES(?,?,?,?,?,?)""",
                        (name, "DEPLOYED", fingerprint, now, actor,
                         "seeded from registered code setup"))
            con.execute("""INSERT INTO strategy_lifecycle_history
                (setup_id,from_state,to_state,actor,reason,evidence,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                        (name, None, "DEPLOYED", actor,
                         "seeded from registered code setup",
                         json.dumps({"version_fingerprint": fingerprint}),
                         now))
            inserted.append(name)
        con.commit()
    finally:
        con.close()
    return {"seeded": inserted, "count": len(inserted), "execution_authority": False}


def current_states(path: str) -> Dict[str, str]:
    con = _connect(path)
    try:
        rows = con.execute("SELECT setup_id, state FROM strategy_lifecycle").fetchall()
        return {str(row["setup_id"]): str(row["state"]) for row in rows}
    finally:
        con.close()


def block_info(path: str, setup_id: Any) -> Optional[Dict[str, Any]]:
    """Return blocking evidence for a setup, or None when it may trade."""
    setup_id = str(setup_id or "").strip()
    if not setup_id:
        return None
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT state, updated_at, reason FROM strategy_lifecycle WHERE setup_id=?",
            (setup_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    state = str(row["state"])
    if state not in BLOCKING_STATES:
        return None
    return {
        "setup_id": setup_id,
        "state": state,
        "since": row["updated_at"],
        "reason": row["reason"],
    }


def register_candidate(path: str, setup_id: Any, *, reason: str = "",
                       evidence: Optional[Dict[str, Any]] = None,
                       actor: str = "autonomous_pipeline") -> Dict[str, Any]:
    """Insert a newly validated setup as CANDIDATE (insert-only, idempotent).

    Research artifacts (harness sweeps, pre-registered families) that PASS the
    gates enter the lifecycle at the bottom of the authority ladder: CANDIDATE
    carries zero execution authority and cannot trade. Only an operator may
    promote it (CANDIDATE -> VALIDATING -> ... -> DEPLOYED); automation may
    only add rows, never promote.
    """
    setup_id = _clean(setup_id, 200)
    if not setup_id:
        raise ValueError("setup_id is required")
    actor = _clean(actor, 120)
    if not actor:
        raise ValueError("actor is required")
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state FROM strategy_lifecycle WHERE setup_id=?", (setup_id,)
        ).fetchone()
        if row is not None:
            con.commit()
            return {"setup_id": setup_id, "changed": False, "state": str(row["state"]),
                    "execution_authority": False}
        now = _now()
        con.execute("""INSERT INTO strategy_lifecycle
            (setup_id,state,version_fingerprint,updated_at,updated_by,reason)
            VALUES(?,?,?,?,?,?)""",
                    (setup_id, "CANDIDATE", "autonomous_pipeline", now, actor,
                     _clean(reason, MAX_REASON_CHARS)))
        con.execute("""INSERT INTO strategy_lifecycle_history
            (setup_id,from_state,to_state,actor,reason,evidence,created_at)
            VALUES(?,?,?,?,?,?,?)""",
                    (setup_id, None, "CANDIDATE", actor,
                     _clean(reason, MAX_REASON_CHARS),
                     _clean(json.dumps(evidence or {}, sort_keys=True, default=str),
                            MAX_EVIDENCE_CHARS),
                     now))
        con.commit()
        return {"setup_id": setup_id, "changed": True, "state": "CANDIDATE",
                "execution_authority": False}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def transition(path: str, setup_id: Any, to_state: Any, *, actor: str,
               reason: str = "", evidence: Optional[Dict[str, Any]] = None,
               allow_auto: bool = False) -> Dict[str, Any]:
    """Apply one validated lifecycle transition and append history.

    ``actor='evaluation'`` (or any non-operator actor) may only perform the
    AUTO_TRANSITIONS demotions and must set ``allow_auto=True`` explicitly so
    accidental automation cannot promote authority.
    """
    setup_id = _clean(setup_id, 200)
    if not setup_id:
        raise ValueError("setup_id is required")
    to_state = _validate_state(to_state)
    actor = _clean(actor, 120)
    if not actor:
        raise ValueError("actor is required")
    is_operator = actor.startswith("operator")
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state FROM strategy_lifecycle WHERE setup_id=?", (setup_id,)
        ).fetchone()
        from_state = str(row["state"]) if row is not None else None
        if from_state == to_state:
            con.commit()
            return {"setup_id": setup_id, "changed": False, "state": to_state,
                    "execution_authority": False}
        if from_state is None:
            raise KeyError(
                f"unknown lifecycle row for '{setup_id}'; seed the registry first")
        allowed_by_table = ((from_state, to_state) in OPERATOR_TRANSITIONS)
        if not is_operator:
            if (from_state, to_state) not in AUTO_TRANSITIONS or not allow_auto:
                raise ValueError(
                    f"transition {from_state}->{to_state} requires an operator actor")
        elif not allowed_by_table:
            raise ValueError(
                f"operator transition {from_state}->{to_state} is not permitted")
        now = _now()
        payload_text = _clean(json.dumps(evidence or {}, sort_keys=True, default=str),
                              MAX_EVIDENCE_CHARS)
        con.execute("""UPDATE strategy_lifecycle SET state=?, updated_at=?, updated_by=?, reason=?
            WHERE setup_id=?""",
                    (to_state, now, actor, _clean(reason, MAX_REASON_CHARS), setup_id))
        con.execute("""INSERT INTO strategy_lifecycle_history
            (setup_id,from_state,to_state,actor,reason,evidence,created_at)
            VALUES(?,?,?,?,?,?,?)""",
                    (setup_id, from_state, to_state, actor,
                     _clean(reason, MAX_REASON_CHARS), payload_text, now))
        con.commit()
        return {"setup_id": setup_id, "changed": True, "from_state": from_state,
                "state": to_state, "actor": actor, "execution_authority": False}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def acknowledge_registry_fingerprint(path: str, setup_id: Any, *, actor: str,
                                     fingerprint: str) -> Dict[str, Any]:
    """Operator re-acknowledgement after a code-registry change (no state change)."""
    setup_id = _clean(setup_id, 200)
    fingerprint = _clean(fingerprint, 128)
    actor = _clean(actor, 120)
    if not setup_id or not fingerprint or not actor:
        raise ValueError("setup_id, actor, and fingerprint are required")
    if not actor.startswith("operator"):
        raise ValueError("registry acknowledgement requires an operator actor")
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state FROM strategy_lifecycle WHERE setup_id=?", (setup_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown lifecycle row for '{setup_id}'")
        now = _now()
        con.execute("""UPDATE strategy_lifecycle SET version_fingerprint=?, updated_at=?, updated_by=?
            WHERE setup_id=?""", (fingerprint, now, actor, setup_id))
        con.execute("""INSERT INTO strategy_lifecycle_history
            (setup_id,from_state,to_state,actor,reason,evidence,created_at)
            VALUES(?,?,?,?,?,?,?)""",
                    (setup_id, str(row["state"]), str(row["state"]), actor,
                     "registry change acknowledged",
                     json.dumps({"version_fingerprint": fingerprint}), now))
        con.commit()
        return {"setup_id": setup_id, "version_fingerprint": fingerprint,
                "execution_authority": False}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def audit_history(path: str) -> Dict[str, Any]:
    """Read-only audit of strategy_lifecycle_history.

    Nothing validated this table's contents: no check for self-transitions, no
    check that evidence was captured, no check that the actor was entitled to
    the move it performed. Report-only - it never writes.
    """
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT id, setup_id, from_state, to_state, actor, reason, evidence "
            "FROM strategy_lifecycle_history ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    self_transitions: List[Dict[str, Any]] = []
    empty_evidence: List[Dict[str, Any]] = []
    unauthorized: List[Dict[str, Any]] = []

    for r in rows:
        rid = r["id"]
        setup_id = r["setup_id"]
        frm, to = r["from_state"], r["to_state"]
        actor = str(r["actor"] or "")
        evidence = r["evidence"]

        if frm is not None and frm == to:
            self_transitions.append(
                {"id": rid, "setup_id": setup_id, "state": to, "actor": actor})

        try:
            parsed = json.loads(evidence or "{}")
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict) or not parsed:
            empty_evidence.append(
                {"id": rid, "setup_id": setup_id, "from_state": frm,
                 "to_state": to, "actor": actor})

        if frm is None or to is None:
            continue
        is_operator = actor.startswith("operator")
        if is_operator:
            if (frm, to) not in OPERATOR_TRANSITIONS:
                unauthorized.append(
                    {"id": rid, "setup_id": setup_id, "from_state": frm,
                     "to_state": to, "actor": actor,
                     "reason": "operator transition not in OPERATOR_TRANSITIONS"})
        elif (frm, to) not in AUTO_TRANSITIONS:
            unauthorized.append(
                {"id": rid, "setup_id": setup_id, "from_state": frm,
                 "to_state": to, "actor": actor,
                 "reason": "non-operator transition not in AUTO_TRANSITIONS"})

    return {
        "rows": len(rows),
        "self_transitions": len(self_transitions),
        "empty_evidence": len(empty_evidence),
        "unauthorized_transitions": len(unauthorized),
        "clean": not (self_transitions or empty_evidence or unauthorized),
        "self_transition_samples": self_transitions[:10],
        "empty_evidence_samples": empty_evidence[:10],
        "unauthorized_samples": unauthorized[:10],
        "execution_authority": False,
    }


def status_board(path: str, *, registry_specs: Optional[Dict[str, Any]] = None,
                 registry_fingerprint: Optional[str] = None) -> List[Dict[str, Any]]:
    """Full operator status board, including registry-drift detection."""
    con = _connect(path)
    try:
        rows = [dict(row) for row in con.execute(
            """SELECT setup_id, state, version_fingerprint, updated_at, updated_by, reason
               FROM strategy_lifecycle ORDER BY setup_id""")]
        for row in rows:
            counts = con.execute(
                """SELECT to_state, COUNT(*) AS n FROM strategy_lifecycle_history
                   WHERE setup_id=? GROUP BY to_state""", (row["setup_id"],)).fetchall()
            row["history_counts"] = {str(c["to_state"]): int(c["n"]) for c in counts}
            last = con.execute(
                """SELECT from_state, to_state, actor, created_at FROM strategy_lifecycle_history
                   WHERE setup_id=? ORDER BY id DESC LIMIT 1""", (row["setup_id"],)).fetchone()
            row["last_transition"] = dict(last) if last is not None else None
            row["blocked"] = row["state"] in BLOCKING_STATES
    finally:
        con.close()
    known = {str(key) for key in (registry_specs or {})}
    for row in rows:
        drift = None
        if registry_specs is not None and row["setup_id"] not in known:
            drift = "NOT_IN_CODE_REGISTRY"
        elif (registry_fingerprint and row.get("version_fingerprint")
              and row["version_fingerprint"] != registry_fingerprint):
            drift = "REGISTRY_DRIFT"
        row["drift"] = drift
    if registry_specs is not None:
        tracked = {row["setup_id"] for row in rows}
        for name in sorted(known - tracked):
            rows.append({"setup_id": name, "state": "UNSEEDED",
                         "blocked": True, "drift": "AWAITING_SEED"})
    return rows


def export_spec(spec: Any) -> Dict[str, Any]:
    """Best-effort canonical serialization of a setup spec for hashing."""
    if is_dataclass(spec):
        return asdict(spec)
    if isinstance(spec, dict):
        return spec
    return {"repr": repr(spec)}
