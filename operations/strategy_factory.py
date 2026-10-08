"""Strategy lifecycle factory: gates, promotions and the approvals round-trip.

Owns the declarative CANDIDATE -> EVALUATION -> FULL -> RETIRED/QUARANTINE
state machine for every strategy, evaluates promotion/demotion gates from
labelled evidence, and bridges operator governance through a file-based
approvals channel shared with ui/gui_api.py and the command deck:

    approvals_outbox.jsonl     engine -> deck   (requests awaiting decision)
    approval_decisions.jsonl   deck   -> engine (operator decisions)

Decisions are applied by process_decisions(), which both the engine pulse and
the API server call, so governance works regardless of which side is up.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.file_utils import (
    EVIDENCE_MARKER_KEY,
    heal_strategy_registry_marker,
    safe_atomic_write,
    safe_atomic_write_json,
    safe_atomic_write_lines,
)

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_FILE = os.path.join(ROOT, "strategy_registry.json")
OUTBOX_FILE = os.path.join(ROOT, "approvals_outbox.jsonl")
DECISIONS_FILE = os.path.join(ROOT, "approval_decisions.jsonl")

STAGES = ("CANDIDATE", "EVALUATION", "FULL", "RETIRED", "QUARANTINE")

# Gate table: requirements to LEAVE the current stage upwards.
PROMOTION_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "CANDIDATE": {"trades": 30, "profit_factor": 1.05, "win_rate": 48.0},
    "EVALUATION": {"trades": 150, "profit_factor": 1.30, "win_rate": 50.0,
                    "sharpe": 0.90, "max_dd": 15.0},
}

# Auto-quarantine triggers (any hit demotes straight to QUARANTINE).
QUARANTINE_RULES: List[Dict[str, Any]] = [
    {"metric": "profit_factor", "op": "<", "threshold": 0.90, "min_trades": 20},
    {"metric": "max_dd", "op": ">", "threshold": 20.0, "min_trades": 0},
]


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _append_line(path: str, record: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


class StrategyFactory:
    def __init__(self, registry_file: str = REGISTRY_FILE,
                 outbox_file: str = OUTBOX_FILE,
                 decisions_file: str = DECISIONS_FILE,
                 autonomy_manager: Any = None):
        self._lock = threading.RLock()
        self.registry_file = registry_file
        self.outbox_file = outbox_file
        self.decisions_file = decisions_file
        self._autonomy = autonomy_manager
        self._reg: Dict[str, Any] = self._load()

    def _autonomy_mgr(self) -> Any:
        if self._autonomy is not None:
            return self._autonomy
        from operations.autonomy import get_autonomy
        return get_autonomy()

    # ---------- registry ----------
    def _load(self) -> Dict[str, Any]:
        reg = _read_json(self.registry_file, {"strategies": []})
        if not isinstance(reg.get("strategies"), list):
            # Keep the invalidation marker even when the strategies list is
            # unusable: dropping it would strip the CRITICAL #10 disclosure
            # from a file live code still reads.
            reg = {"strategies": [], EVIDENCE_MARKER_KEY: reg[EVIDENCE_MARKER_KEY]} \
                if isinstance(reg.get(EVIDENCE_MARKER_KEY), dict) else {"strategies": []}
        return reg

    def _persist_locked(self) -> None:
        # Keep _EVIDENCE_INVALID's counts describing the rows written beside
        # them (CRITICAL #10); a frozen count fails the honesty guard.
        heal_strategy_registry_marker(self._reg)
        safe_atomic_write_json(self.registry_file, self._reg)

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._reg["strategies"])

    def get(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return next((s for s in self._reg["strategies"] if s["id"] == strategy_id), None)

    def upsert(self, record: Dict[str, Any]) -> None:
        with self._lock:
            rec = dict(record)
            rec.setdefault("id", str(uuid.uuid4())[:8])
            rec.setdefault("stage", "CANDIDATE")
            rec.setdefault("gate_progress", 0.0)
            rec.setdefault("notes", "")
            strategies = [s for s in self._reg["strategies"] if s["id"] != rec["id"]]
            strategies.append(rec)
            self._reg["strategies"] = strategies
            self._persist_locked()

    def set_stage(self, strategy_id: str, stage: str, reason: str = "") -> Dict[str, Any]:
        """Operator or autonomous trigger to transition a strategy's lifecycle stage."""
        with self._lock:
            s = self.get(strategy_id)
            if s is None:
                s = {
                    "id": strategy_id,
                    "name": strategy_id,
                    "stage": stage if stage in STAGES else "CANDIDATE",
                    "gate_progress": 0.0,
                    "notes": reason,
                    "trades": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "sharpe": 0.0,
                    "max_dd": 0.0,
                }
                self.upsert(s)
                return {"ok": True, "strategy_id": strategy_id, "previous": None, "stage": s["stage"]}
            if stage not in STAGES:
                return {"ok": False, "reason": f"invalid stage {stage}, must be one of {STAGES}"}
            previous = s.get("stage")
            s["stage"] = stage
            if reason:
                s["notes"] = f"{reason} [{previous} -> {stage}]"
            self._persist_locked()
            return {"ok": True, "strategy_id": strategy_id, "previous": previous, "stage": stage}

    # ---------- gate evaluation ----------
    def evaluate(self, strategy_id: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Feed labelled evidence; advance gate progress, fire transitions."""
        with self._lock:
            s = self.get(strategy_id)
            if s is None:
                return {"ok": False, "reason": f"unknown strategy {strategy_id}"}
            s.update({k: metrics[k] for k in metrics if k in (
                "trades", "win_rate", "profit_factor", "sharpe", "max_dd",
                "expectancy_r", "recovery_factor")})
            s["last_eval_ts"] = time.time()

            for rule in QUARANTINE_RULES:
                value = s.get(rule["metric"])
                if value is None or s.get("trades", 0) < rule["min_trades"]:
                    continue
                fired = value < rule["threshold"] if rule["op"] == "<" else value > rule["threshold"]
                if fired and s["stage"] not in ("QUARANTINE", "RETIRED"):
                    previous = s["stage"]
                    s["stage"] = "QUARANTINE"
                    s["gate_progress"] = 0.0
                    s["notes"] = f"auto-quarantine: {rule['metric']}={value} [{previous}]"
                    self._persist_locked()
                    return {"ok": True, "transition": "QUARANTINE", "reason": s["notes"]}

            req = PROMOTION_REQUIREMENTS.get(s["stage"])
            if req:
                def _meets(metric: str, value: Any, threshold: Any) -> bool:
                    if value is None:
                        return False
                    return value <= threshold if metric == "max_dd" else value >= threshold
                passed = sum(1 for m, th in req.items() if _meets(m, s.get(m), th))
                s["gate_progress"] = round(passed / max(1, len(req)), 3)
                eligible = s["gate_progress"] >= 0.999
                if eligible and s.get("promotion_pending") != "PENDING":
                    self.request_approval({
                        "kind": "PROMOTION",
                        "title": f"{s.get('name', s['id'])} → {'EVALUATION' if s['stage']=='CANDIDATE' else 'FULL'}",
                        "detail_lines": [
                            f"trades={s.get('trades')} win={s.get('win_rate')}% "
                            f"PF={s.get('profit_factor')} sharpe={s.get('sharpe')}",
                            f"max_dd={s.get('max_dd')}% · gate {s['stage']}: all criteria met",
                        ],
                        "signature_required": False,
                        "strategy_id": s["id"],
                    })
                    s["promotion_pending"] = "PENDING"
            self._persist_locked()
            return {"ok": True, "stage": s["stage"], "gate_progress": s["gate_progress"]}

    # ---------- approvals ----------
    def request_approval(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = {
                "id": f"ap-{uuid.uuid4().hex[:6]}",
                "kind": spec.get("kind", "CONFIG_CHANGE"),
                "title": spec.get("title", ""),
                "detailLines": spec.get("detail_lines", []),
                "signatureRequired": bool(spec.get("signature_required")),
                "requestedTs": int(time.time() * 1000),
                "status": "PENDING",
                "strategyId": spec.get("strategy_id"),
                "manifestId": spec.get("manifest_id"),
            }
            _append_line(self.outbox_file, record)
            return record

    def request_l3_rollout(self, book: str, manifest_id: str) -> Dict[str, Any]:
        return self.request_approval({
            "kind": "ROLLOUT_L3",
            "title": f"live_rollout manifest {manifest_id}",
            "detail_lines": [
                f"escalate autonomy L2_MICRO → L3_FULL on {book}",
                "requires operator-signed manifest (live_rollout_gate)",
                f"type SIGN {manifest_id} in stream to pre-register, then approve",
            ],
            "signature_required": True,
            "manifest_id": manifest_id,
        })

    def pending_approvals(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        try:
            with open(self.outbox_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        latest: Dict[str, Dict[str, Any]] = {}
        for r in records:
            latest[r["id"]] = r
        return [latest[k] for k in sorted(latest, key=lambda k: latest[k].get("requestedTs", 0))]

    def write_decision(self, approval_id: str, decision: str, actor: str = "operator") -> Dict[str, Any]:
        if decision not in ("APPROVED", "REJECTED"):
            return {"ok": False, "reason": "decision must be APPROVED|REJECTED"}
        _append_line(self.decisions_file, {
            "approval_id": approval_id, "decision": decision,
            "actor": actor, "ts": time.time(),
        })
        return self.process_decisions()

    def process_decisions(self) -> List[Dict[str, Any]]:
        """Apply unseen operator decisions to registry/autonomy; mark outbox."""
        applied: List[Dict[str, Any]] = []
        try:
            with open(self.decisions_file, "r", encoding="utf-8") as fh:
                decisions = [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return applied

        processed_marker = self.decisions_file + ".offset"
        offset = _read_json(processed_marker, {"n": 0}).get("n", 0)
        fresh = decisions[offset:]
        if not fresh:
            return applied

        outbox = self.pending_approvals()
        by_id = {a["id"]: a for a in outbox}

        for dec in fresh:
            approval = by_id.get(dec.get("approval_id"))
            if not approval or approval.get("status") != "PENDING":
                continue
            approved = dec.get("decision") == "APPROVED"
            approval["status"] = dec.get("decision")
            approval["decidedBy"] = dec.get("actor", "operator")

            kind = approval.get("kind")
            if approved and kind == "PROMOTION" and approval.get("strategyId"):
                strat_id = approval["strategyId"]
                actor_name = approval.get("decidedBy", "operator")
                if not actor_name.startswith("operator"):
                    actor_name = f"operator_{actor_name}"
                with self._lock:
                    s = self.get(strat_id)
                    if s is not None:
                        s["stage"] = "EVALUATION" if s["stage"] == "CANDIDATE" else "FULL"
                        s["promotion_pending"] = "DONE"
                        s["gate_progress"] = 0.0 if s["stage"] == "EVALUATION" else s["gate_progress"]
                        s["notes"] = f"promoted via {approval['id']}"
                        self._persist_locked()
                # Durable transition in strategy_lifecycle & model_registry
                try:
                    from operations.strategy_lifecycle import transition as _lc_trans
                    _lc_trans("trading_state.db", strat_id, "DEPLOYED",
                              actor=actor_name, reason=f"operator approved via {approval['id']}")
                except Exception as _lc_err:
                    logger.debug("Lifecycle transition on approval: %s", _lc_err)
                try:
                    import sqlite3
                    con = sqlite3.connect("trading_state.db", timeout=2)
                    con.execute("UPDATE model_registry SET status='DEPLOYED' WHERE model_key=?", (strat_id,))
                    con.commit()
                    con.close()
                except Exception:
                    pass
            elif approved and kind == "ROLLOUT_L3":
                try:
                    manifest = approval.get("manifestId") or approval["id"]
                    res = self._autonomy_mgr().escalate(
                        "L3_FULL", f"rollout {approval['id']}", manifest_signature=manifest)
                    approval["applied"] = res
                except Exception as exc:
                    logger.error("L3 escalation failed: %s", exc)
            elif approved and kind == "CONFIG_CHANGE":
                with self._lock:
                    self._reg.setdefault("config_versions", []).append({
                        "ts": time.time(), "approval": approval["id"],
                        "summary": approval.get("title"),
                    })
                    self._persist_locked()

            applied.append({"approval_id": approval["id"], "decision": approval["status"]})

        # rewrite outbox with updated statuses (use mutated in-memory objects)
        if applied:
            with self._lock:
                safe_atomic_write_lines(self.outbox_file, [json.dumps(a) for a in by_id.values()])

        _write_json(processed_marker, {"n": offset + len(fresh)})
        return applied


def _write_json(path: str, data: Dict[str, Any]) -> None:
    safe_atomic_write_json(path, data)


_factory: Optional[StrategyFactory] = None


def get_factory() -> StrategyFactory:
    global _factory
    if _factory is None:
        _factory = StrategyFactory()
    return _factory
