"""Autonomy doctrine: levels, circuits and the operator control plane.

The system may operate autonomously up to L2 (micro-size live). Escalation to
L3 (full live size) always requires an operator-signed rollout manifest id.
Every escalation, demotion or circuit trip is persisted and journalled so the
command deck can render a complete governance trail.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autonomy_state.json")
#: Legacy JSONL queue.  Still read once at startup so controls issued by an
#: older build are not lost, then archived; the durable queue is CONTROL_DB.
CONTROL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "control_plane.jsonl")
#: Durable control queue (K5).  SQLite gives the API process and the engine
#: process one atomic queue with compare-and-set consumption, instead of two
#: processes racing on an append-and-rewrite JSONL file.
CONTROL_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "control_plane.db")

CONTROL_ACTIONS = {"pause", "resume", "flatten", "killswitch"}


@dataclass(frozen=True)
class Level:
    key: str
    rank: int
    label: str
    max_risk_pct_per_trade: float
    allows_live: bool


LEVELS: Dict[str, Level] = {
    "L0_SHADOW": Level("L0_SHADOW", 0, "SHADOW", 0.0, False),
    "L1_PAPER": Level("L1_PAPER", 1, "PAPER", 1.0, False),
    "L2_MICRO": Level("L2_MICRO", 2, "MICRO LIVE", 0.25, True),
    "L3_FULL": Level("L3_FULL", 3, "FULL LIVE", 1.0, True),
}


@dataclass
class AutonomySnapshot:
    level: str
    halted: bool
    halt_reason: str
    paused: bool
    pause_reason: str
    history: List[Dict[str, Any]] = field(default_factory=list)


class AutonomyManager(StoreBackedState):
    """Owns the autonomy level, halt/pause flags, circuits and control plane."""

    store_key = "autonomy_state"

    def __init__(self, state_file: str = STATE_FILE, control_file: str = CONTROL_FILE,
                  signature_verifier: Optional[Callable[[str], bool]] = None,
                  store: Optional[Any] = None):
        self._lock = threading.RLock()
        self.state_file = state_file
        self.control_file = control_file
        # Queue DB derived from the control-file path: same directory, .db
        # extension.  Tests that pass tmp/control_plane.jsonl get a private
        # tmp/control_plane.db automatically.
        self.control_db = os.path.splitext(control_file)[0] + ".db"
        self._control_schema_ready = False
        self.store = store
        self._signature_verifier = signature_verifier
        self._state: Dict[str, Any] = {
            "level": "L1_PAPER",
            "halted": False,
            "halt_reason": "",
            "paused": False,
            "pause_reason": "",
            "history": [],
        }
        self._load()
        if store is not None:
            self.attach_store(store)
        # Migrate any legacy JSONL controls queued by older builds (done under
        # the lock so two starting processes cannot double-insert).
        with self._lock:
            self._migrate_legacy_jsonl_locked()

    # ---------- control queue (K5: journal-backed, compare-and-set) ----------
    def _connect_locked(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.control_db, timeout=10.0)
        con.execute("PRAGMA busy_timeout=10000")
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        return con

    def _ensure_schema_locked(self) -> None:
        if self._control_schema_ready:
            return
        os.makedirs(os.path.dirname(self.control_db) or ".", exist_ok=True)
        con = self._connect_locked()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS control_queue (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'operator',
                    source TEXT NOT NULL DEFAULT 'api',
                    consumed INTEGER NOT NULL DEFAULT 0,
                    consumed_by TEXT,
                    consumed_at REAL,
                    UNIQUE (ts, action, actor, source)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_control_queue_unconsumed "
                "ON control_queue (consumed, seq)"
            )
            con.commit()
            self._control_schema_ready = True
        finally:
            con.close()

    def _migrate_legacy_jsonl_locked(self) -> None:
        """One-shot import of unconsumed records from the legacy JSONL queue."""
        if not os.path.exists(self.control_file):
            return
        records: List[Dict[str, Any]] = []
        try:
            with open(self.control_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict) and not rec.get("consumed"):
                        records.append(rec)
        except OSError as exc:
            logger.warning("legacy control queue read failed: %s", exc)
            return
        if records:
            self._ensure_schema_locked()
            con = self._connect_locked()
            try:
                for rec in records:
                    con.execute(
                        "INSERT OR IGNORE INTO control_queue (ts, action, actor, source, consumed) "
                        "VALUES (?, ?, ?, ?, 0)",
                        (
                            float(rec.get("ts") or time.time()),
                            str(rec.get("action") or ""),
                            str(rec.get("actor") or "operator"),
                            str(rec.get("source") or "api"),
                        ),
                    )
                con.commit()
                logger.info(
                    "migrated %d unconsumed control(s) from legacy %s",
                    len(records), self.control_file,
                )
            finally:
                con.close()
        # Archive (not delete) so the migration is auditable and idempotent:
        # a second process finds no control_plane.jsonl and skips.
        try:
            os.replace(self.control_file, self.control_file + ".migrated")
        except OSError as exc:
            logger.warning("legacy control queue archive failed: %s", exc)

    def attach_store(self, store: Any) -> Dict[str, Any]:
        report = super().attach_store(store)
        self._retire_control_plane_file()
        return report

    def _retire_control_plane_file(self) -> None:
        if not getattr(self, "control_file", None) or not os.path.exists(self.control_file):
            return
        if getattr(self, "store", None) is None:
            return
        try:
            with open(self.control_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not rec.get("consumed"):
                        cid = self.store.create_operator_control(
                            action=rec.get("action", "unknown"),
                            actor=rec.get("actor", "operator"),
                            source=rec.get("source", "retired_control_plane"),
                        )
                        self.store.complete_operator_control(
                            cid,
                            "EXPIRED",
                            {"error": "imported from retired control_plane.jsonl as EXPIRED"},
                        )
            retired_name = self.control_file + ".retired"
            os.replace(self.control_file, retired_name)
        except Exception as exc:
            logger.warning("Retiring control_plane.jsonl failed: %s", exc)

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            loaded = self._load_state_dict()
            if isinstance(loaded, dict) and loaded.get("level") in LEVELS:
                self._state = {
                    "level": loaded.get("level", "L1_PAPER"),
                    "halted": bool(loaded.get("halted", False)),
                    "halt_reason": loaded.get("halt_reason", ""),
                    "paused": bool(loaded.get("paused", False)),
                    "pause_reason": loaded.get("pause_reason", ""),
                    "history": list(loaded.get("history", []))[-100:],
                }
        except Exception as exc:
            logger.warning("autonomy state load failed (%s); starting defaults", exc)

    def _persist_locked(self) -> None:
        self._save_state_dict(self._state)

    def _record_locked(self, kind: str, detail: Dict[str, Any]) -> None:
        self._state.setdefault("history", []).append({
            "ts": time.time(), "kind": kind, **detail,
        })
        self._state["history"] = self._state["history"][-100:]

    # ---------- levels ----------
    def snapshot(self) -> AutonomySnapshot:
        with self._lock:
            self._load()
            return AutonomySnapshot(
                level=self._state["level"],
                halted=self._state["halted"],
                halt_reason=self._state["halt_reason"],
                paused=self._state["paused"],
                pause_reason=self._state["pause_reason"],
                history=list(self._state["history"]),
            )

    def level(self) -> Level:
        with self._lock:
            return LEVELS[self._state["level"]]

    def escalate(self, target_key: str, reason: str, manifest_signature: str = "") -> Dict[str, Any]:
        target = LEVELS.get(target_key)
        if target is None:
            return {"granted": False, "reason": f"unknown level {target_key}"}
        with self._lock:
            current = LEVELS[self._state["level"]]
            if target.rank <= current.rank:
                return {"granted": False, "reason": "target not above current level"}
            if target.key == "L3_FULL":
                verifier = self._signature_verifier or (lambda sig: bool(sig))
                if not verifier(manifest_signature):
                    self._record_locked("ESCALATION_DENIED", {
                        "target": target_key, "reason": "missing/invalid operator signature",
                    })
                    self._persist_locked()
                    return {"granted": False, "reason": "L3 requires an operator-signed rollout manifest"}
            self._state["level"] = target.key
            self._record_locked("ESCALATED", {"from": current.key, "to": target.key, "why": reason})
            self._persist_locked()
            return {"granted": True, "level": target.key}

    def demote(self, reason: str) -> str:
        with self._lock:
            current = LEVELS[self._state["level"]]
            lowered = max(0, current.rank - 1)
            target = next(lv for lv in LEVELS.values() if lv.rank == lowered)
            self._state["level"] = target.key
            self._record_locked("DEMOTED", {"from": current.key, "to": target.key, "why": reason})
            self._persist_locked()
            return target.key

    def force_level(self, target_key: str, actor: str) -> Dict[str, Any]:
        if target_key not in LEVELS:
            return {"ok": False, "reason": f"unknown level {target_key}"}
        with self._lock:
            previous = self._state["level"]
            self._state["level"] = target_key
            self._record_locked("FORCE_LEVEL", {"from": previous, "to": target_key, "actor": actor})
            self._persist_locked()
            return {"ok": True, "level": target_key}

    # ---------- halts ----------
    def halt(self, reason: str) -> None:
        with self._lock:
            self._state["halted"] = True
            self._state["halt_reason"] = reason
            self._record_locked("HALT", {"reason": reason})
            self._persist_locked()

    def resume(self, actor: str) -> None:
        with self._lock:
            self._state["halted"] = False
            self._state["halt_reason"] = ""
            self._state["paused"] = False
            self._state["pause_reason"] = ""
            self._record_locked("RESUME", {"actor": actor})
            if getattr(self, "store", None) is not None:
                try:
                    self.store.set_autonomy_paused(False, actor=actor)
                except Exception:
                    pass
            self._persist_locked()

    def pause(self, reason: str = "") -> None:
        with self._lock:
            self._state["paused"] = True
            self._state["pause_reason"] = reason or "paused"
            self._record_locked("PAUSE", {"reason": self._state["pause_reason"]})
            if getattr(self, "store", None) is not None and hasattr(self.store, "set_autonomy_paused"):
                try:
                    self.store.set_autonomy_paused(True, actor="pipeline_runner", reason=self._state["pause_reason"])
                except Exception:
                    pass
            self._persist_locked()

    def is_hold_only(self) -> bool:
        with self._lock:
            return bool(self._state.get("flags", {}).get("hold_only", False))

    def set_hold_only(self, hold_only: bool = True, why: str = "") -> None:
        with self._lock:
            self._state.setdefault("flags", {})
            self._state["flags"]["hold_only"] = hold_only
            if why:
                self._state["flags"]["hold_only_why"] = why
            self._persist_locked()

    def trading_allowed(self) -> tuple[bool, str]:
        with self._lock:
            self._load()
            if self._state.get("halted"):
                return False, self._state.get("halt_reason") or "kill switch engaged"
            if self._state.get("paused"):
                return False, self._state.get("pause_reason") or "operator pause active"
            return True, ""

    # ---------- circuits ----------
    CIRCUITS: List[Dict[str, Any]] = [
        {"key": "drawdown", "metric": "drawdown_pct", "op": ">=", "threshold": 6.0,
         "severity": "CRITICAL", "action": "HALT"},
        {"key": "recon_mismatch", "metric": "recon_mismatch_24h", "op": ">=", "threshold": 2,
         "severity": "CRITICAL", "action": "HALT"},
        {"key": "slippage_streak", "metric": "slippage_breach_streak", "op": ">=", "threshold": 3,
         "severity": "WARN", "action": "DEMOTE"},
        {"key": "llm_outage_min", "metric": "llm_outage_minutes", "op": ">=", "threshold": 10,
         "severity": "WARN", "action": "DEGRADE_HOLD_ONLY"},
        {"key": "stale_data_s", "metric": "data_stale_seconds", "op": ">=", "threshold": 120,
         "severity": "WARN", "action": "PAUSE_NEW_CYCLES"},
    ]

    def evaluate_circuits(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        trips: List[Dict[str, Any]] = []
        for circuit in self.CIRCUITS:
            value = metrics.get(circuit["metric"])
            if value is None:
                continue
            fired = value >= circuit["threshold"] if circuit["op"] == ">=" else value <= circuit["threshold"]
            if not fired:
                continue
            trip = {**circuit, "observed": value}
            trips.append(trip)
            with self._lock:
                self._record_locked("CIRCUIT_TRIP", {k: trip[k] for k in ("key", "severity", "action", "observed")})
                if circuit["action"] == "HALT":
                    self._state["halted"] = True
                    self._state["halt_reason"] = f"circuit:{circuit['key']}"
                elif circuit["action"] == "PAUSE_NEW_CYCLES":
                    self._state["paused"] = True
                    self._state["pause_reason"] = f"circuit:{circuit['key']}"
                elif circuit["action"] == "DEMOTE" and LEVELS[self._state["level"]].rank > 0:
                    lowered = LEVELS[self._state["level"]].rank - 1
                    self._state["level"] = next(lv.key for lv in LEVELS.values() if lv.rank == lowered)
                else:
                    self._state.setdefault("flags", {})
                    self._state["flags"]["hold_only"] = True
                self._persist_locked()
        return trips

    # ---------- operator control plane ----------
    def issue_control(
        self,
        action: str,
        actor: str = "operator",
        source: str = "api",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        action = action.lower().strip()
        if action not in CONTROL_ACTIONS:
            return {"ok": False, "reason": f"unsupported action {action}"}

        # Journal store first (origin/main): it supports idempotency_key and returns
        # the control_id the code below and get_control_status() consume.
        # Falls back to the local control_queue table (HEAD) when no journal
        # store is attached - a manager built from a state file alone still has
        # to be able to queue a control, and refusing there would mean a
        # killswitch issued without a store was silently dropped. Only when
        # BOTH are unavailable do we fail closed: reporting ok with nowhere to
        # put the control would be a lie (B1).
        _has_store = self.store is not None and hasattr(self.store, "create_operator_control")
        if not _has_store:
            try:
                self._ensure_schema_locked()
                con = self._connect_locked()
                try:
                    con.execute(
                        "INSERT INTO control_queue (ts, action, actor, source, consumed) "
                        "VALUES (?, ?, ?, ?, 0)",
                        (time.time(), action, str(actor), str(source)),
                    )
                    con.commit()
                finally:
                    con.close()
                self._record_locked("CONTROL_ISSUED", {"action": action, "actor": actor, "source": source})
                control_id = None
            except Exception as exc:
                logger.error("issue_control(%s) refused: no journal store and control_queue unavailable: %s", action, exc)
                return {"ok": False, "reason": f"engine journal unavailable: control not issued ({exc})"}
        else:
            with self._lock:
                try:
                    control_id = self.store.create_operator_control(
                        action=action,
                        actor=actor,
                        source=source,
                        idempotency_key=idempotency_key,
                    )
                except Exception as exc:
                    logger.warning("Failed to insert into operator_controls: %s", exc)
                    return {"ok": False, "reason": f"control journal write failed: {exc}"}
                if control_id is None:
                    return {"ok": False, "reason": "control journal write failed: no id returned"}

                self._record_locked("CONTROL_ISSUED", {"action": action, "actor": actor, "source": source, "control_id": control_id})
                if action == "pause":
                    # Apply locally so this process's snapshot is immediately
                    # honest; the consuming engine applies its own copy on drain.
                    self._state["paused"] = True
                    self._state["pause_reason"] = f"operator:{actor}"
                    try:
                        self.store.set_autonomy_paused(True, actor=actor, reason=f"operator:{actor}")
                    except Exception as exc:
                        logger.warning("set_autonomy_paused failed: %s", exc)
                        return {"ok": False, "control_id": control_id, "reason": f"pause not persisted: {exc}"}
                    self._persist_locked()
                    try:
                        self.store.complete_operator_control(control_id, "APPLIED", {"action": "pause", "paused": True})
                    except Exception as exc:
                        return {"ok": False, "control_id": control_id, "reason": f"pause applied but not recorded: {exc}"}
                elif action == "resume":
                    self._state["paused"] = False
                    self._state["pause_reason"] = ""
                    self._state["halted"] = False
                    self._state["halt_reason"] = ""
                    try:
                        self.store.set_autonomy_paused(False, actor=actor)
                    except Exception as exc:
                        logger.warning("set_autonomy_paused failed: %s", exc)
                        return {"ok": False, "control_id": control_id, "reason": f"resume not persisted: {exc}"}
                    self._persist_locked()
                    try:
                        self.store.complete_operator_control(control_id, "APPLIED", {"action": "resume", "paused": False})
                    except Exception as exc:
                        return {"ok": False, "control_id": control_id, "reason": f"resume applied but not recorded: {exc}"}
                elif action == "killswitch":
                    try:
                        self.store.set_kill_switch(True, f"operator:{actor}")
                    except Exception as exc:
                        logger.warning("set_kill_switch failed: %s", exc)
                        return {"ok": False, "control_id": control_id, "reason": f"kill switch not set: {exc}"}
                    self._record_locked("CONTROL_ACK_QUEUED", {"action": action, "control_id": control_id})
                elif action == "killswitch_off":
                    try:
                        self.store.set_kill_switch(False, f"operator:{actor}")
                    except Exception as exc:
                        logger.warning("set_kill_switch failed: %s", exc)
                        return {"ok": False, "control_id": control_id, "reason": f"kill switch not cleared: {exc}"}
                    try:
                        self.store.complete_operator_control(control_id, "APPLIED", {"action": "killswitch_off"})
                    except Exception as exc:
                        return {"ok": False, "control_id": control_id, "reason": f"killswitch_off applied but not recorded: {exc}"}
                elif action == "flatten":
                    self._record_locked("CONTROL_ACK_QUEUED", {"action": action, "control_id": control_id})

                res = {"ok": True, "action": action}
                if control_id is not None:
                    res["control_id"] = control_id
                return res

    def drain_controls(self) -> List[Dict[str, Any]]:
        """Claim unissued controls exactly once.

        Journal store first (origin/main), then the local control_queue table
        (HEAD), then the jsonl control file. The queue branch is the counterpart
        of issue_control's fallback: controls written there are only visible if
        something can drain them, and each row is claimed by exactly one drainer
        via the ``UPDATE ... WHERE consumed = 0`` compare-and-set, so the API
        process and the engine can never apply the same control twice (K5).
        """
        with self._lock:
            if self.store is not None and hasattr(self.store, "claim_pending_operator_controls"):
                return self.store.claim_pending_operator_controls()

            try:
                self._ensure_schema_locked()
                con = self._connect_locked()
                try:
                    rows = con.execute(
                        "SELECT seq, ts, action, actor, source FROM control_queue "
                        "WHERE consumed = 0 ORDER BY seq"
                    ).fetchall()
                    claimed: List[Dict[str, Any]] = []
                    for seq, ts, action, actor, source in rows:
                        cur = con.execute(
                            "UPDATE control_queue SET consumed = 1, consumed_by = ?, consumed_at = ? "
                            "WHERE seq = ? AND consumed = 0",
                            (f"pid:{os.getpid()}", time.time(), seq),
                        )
                        if cur.rowcount == 1:
                            claimed.append({"ts": ts, "action": action, "actor": actor,
                                            "source": source, "consumed": True})
                    # Retention: keep the last 500 consumed records.
                    con.execute(
                        "DELETE FROM control_queue WHERE consumed = 1 AND seq NOT IN "
                        "(SELECT seq FROM control_queue WHERE consumed = 1 ORDER BY seq DESC LIMIT 500)"
                    )
                    con.commit()
                finally:
                    con.close()
                if claimed:
                    # pause/resume mutate local state, so apply them in the
                    # consuming process rather than only at issue time.
                    for rec in claimed:
                        act = rec.get("action")
                        who = str(rec.get("actor", "operator"))
                        if act == "resume":
                            self.resume(who)
                        elif act == "pause":
                            self._state["paused"] = True
                            self._state["pause_reason"] = f"operator:{who}"
                            self._record_locked("CONTROL_APPLIED", {"action": "pause", "actor": who})
                            self._persist_locked()
                    return claimed
            except Exception as exc:
                logger.error("control_queue drain failed: %s", exc)

            if self.control_file and os.path.exists(self.control_file):
                pending: List[Dict[str, Any]] = []
                kept: List[Dict[str, Any]] = []
                try:
                    with open(self.control_file, "r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if rec.get("consumed"):
                                kept.append(rec)
                                continue
                            rec["consumed"] = True
                            kept.append(rec)
                            pending.append(rec)
                    safe_atomic_write_lines(self.control_file, [json.dumps(rec) for rec in kept[-500:]])
                except Exception as exc:
                    logger.error("drain_controls failed: %s", exc)
                for rec in pending:
                    if rec.get("action") == "resume":
                        self.resume(str(rec.get("actor", "operator")))
                return pending
            return []


def _cancel_pending_intents(operations: Any, paper_broker: Any) -> Dict[str, Any]:
    """Cancel engine-owned pending orders at the broker (killswitch support)."""
    result = {"cancelled": 0, "failed": 0, "skipped": 0, "errors": []}
    if operations is None:
        result["errors"].append("no operations handle")
        return result
    try:
        from core.order_execution import cancel_pending_order
        intents = operations.order_intents(statuses=("PENDING",))
    except Exception as exc:
        result["errors"].append(f"pending intent lookup failed: {exc}")
        return result
    for intent in intents:
        try:
            ticket = int(intent.get("mt5_ticket") or 0)
            payload = json.loads(intent.get("payload") or "{}")
            magic = int(payload.get("magic") or 0)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            result["errors"].append(f"bad intent {intent.get('intent_key')}: {exc}")
            continue
        if ticket <= 0:
            result["skipped"] += 1
            continue
        try:
            ok = cancel_pending_order(ticket, paper_broker, magic)
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append(f"cancel #{ticket} raised: {exc}")
            continue
        if ok:
            result["cancelled"] += 1
            try:
                operations.resolve_order_intent(
                    str(intent.get("intent_key") or ""), "CANCELLED",
                    ticket=ticket, result={"reason": "operator killswitch"},
                )
            except Exception as exc:
                result["errors"].append(f"intent resolve #{ticket} failed: {exc}")
        else:
            result["failed"] += 1
    return result


def apply_operator_control(
    control: Dict[str, Any],
    *,
    store: Any,
    autonomy: "AutonomyManager",
    risk: Any = None,
    trade_manager: Any = None,
    operations: Any = None,
    paper_broker: Any = None,
    latch: Any = None,
) -> Dict[str, Any]:
    """Apply a drained emergency control for real (C4).

    Returns ``{"status": "applied" | "partial" | "queued" | "failed", ...}``.
    Callers must emit a success event only for ``applied``/``partial``; a
    ``failed`` outcome has to surface as an error, never as "executed" (D1).
    """
    action = str(control.get("action") or "")
    actor = str(control.get("actor") or "operator")

    if action in ("pause", "resume"):
        # Applied by AutonomyManager.drain_controls() itself.
        return {"action": action, "status": "applied", "applied_by": "drain"}

    if action == "killswitch":
        outcome: Dict[str, Any] = {"action": action, "status": "applied", "errors": []}
        try:
            store.set_kill_switch(True, reason=f"operator:{actor}")
            outcome["store_kill_switch"] = True
        except Exception as exc:
            outcome["status"] = "failed"
            outcome["errors"].append(f"store kill switch failed: {exc}")
            return outcome
        try:
            autonomy.halt(f"killswitch:operator:{actor}")
            outcome["autonomy_halted"] = True
        except Exception as exc:
            outcome["errors"].append(f"autonomy halt failed: {exc}")
        if latch is not None:
            try:
                latch.apply_market_data(halt_flag=True)
                outcome["latch_engaged"] = True
            except Exception as exc:
                outcome["errors"].append(f"latch engage failed: {exc}")
        cancel = _cancel_pending_intents(operations, paper_broker)
        outcome["pending_cancelled"] = cancel["cancelled"]
        outcome["pending_cancel_failed"] = cancel["failed"]
        if cancel["errors"]:
            outcome["errors"].extend(cancel["errors"])
        if outcome["errors"] or cancel["failed"]:
            outcome["status"] = "partial"
        return outcome

    if action == "flatten":
        outcome = {"action": action, "status": "applied", "errors": []}
        if risk is None:
            outcome["status"] = "failed"
            outcome["errors"].append("no risk manager handle")
            return outcome
        try:
            risk.trigger_emergency_flatten(f"operator:{actor}")
        except Exception as exc:
            outcome["status"] = "failed"
            outcome["errors"].append(f"flatten flag failed: {exc}")
            return outcome
        if trade_manager is None:
            # The flag is durable; TradeManager applies it on its next tick.
            outcome["status"] = "queued"
            outcome["note"] = "flatten flagged; no trade manager attached yet"
            return outcome
        try:
            trade_manager.enforce_protective_flatten()
        except Exception as exc:
            outcome["status"] = "partial"
            outcome["errors"].append(f"flatten raised: {exc}")
        pending = None
        try:
            if hasattr(risk, "pending_flatten_reason"):
                pending = risk.pending_flatten_reason()
        except Exception:
            pending = None
        if pending:
            outcome["status"] = "partial"
            outcome["reason"] = str(pending)
        return outcome

    return {"action": action, "status": "failed", "errors": [f"unsupported action {action}"]}


_default_manager: Optional[AutonomyManager] = None
_default_manager_lock = threading.Lock()


def get_autonomy() -> AutonomyManager:
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = AutonomyManager()
    return _default_manager
