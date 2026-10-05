"""Durable, operator-reviewed registry for offline model artifacts.

Registry state is evidence and governance only.  No registry status is read
by the execution gate; promotion must still be wired explicitly and remains
forbidden until the research gates pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from core.trading_safety import TradingStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def register(path: str = "trading_state.db", *, model_key: str, version: str,
             kind: str, feature_schema_version: str, training_data_hash: str,
             metrics: Dict[str, Any], artifact_uri: str = "") -> Dict[str, Any]:
    if not all(str(value).strip() for value in (model_key, version, kind, feature_schema_version, training_data_hash)):
        raise ValueError("model key, version, kind, feature schema, and training data hash are required")
    TradingStore(path)  # creates/migrates the authoritative registry table
    with _con(path) as con:
        cur = con.execute("""INSERT INTO model_registry(
            model_key,version,kind,status,feature_schema_version,training_data_hash,
            metrics,artifact_uri,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""", (
            model_key[:200], version[:100], kind[:100], "CANDIDATE", feature_schema_version[:100],
            training_data_hash[:128], json.dumps(metrics or {}, sort_keys=True, default=str), artifact_uri[:1000], _now()))
        return dict(con.execute("SELECT * FROM model_registry WHERE id=?", (cur.lastrowid,)).fetchone())


def list_models(path: str = "trading_state.db", model_key: Optional[str] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    TradingStore(path)
    with _con(path) as con:
        rows = con.execute("SELECT * FROM model_registry WHERE (? IS NULL OR model_key=?) ORDER BY created_at DESC", (model_key, model_key)).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.get("metrics") or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {"malformed": True}
        output.append(item)
    return output


def review(path: str = "trading_state.db", *, model_id: int, status: str,
           reviewer: str, note: str = "") -> Dict[str, Any]:
    status = str(status).upper()
    if status not in {"APPROVED", "REJECTED", "RETIRED", "ROLLED_BACK"}:
        raise ValueError("model status must be APPROVED, REJECTED, RETIRED, or ROLLED_BACK")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    TradingStore(path)
    with _con(path) as con:
        row = con.execute("SELECT * FROM model_registry WHERE id=?", (int(model_id),)).fetchone()
        if not row:
            raise KeyError(f"unknown model registry id {model_id}")
        # Approval is deliberately explicit and cannot be inferred from a
        # metric.  The metrics remain attached as immutable evidence.
        if status == "APPROVED":
            try:
                metrics = json.loads(row["metrics"] or "{}")
            except json.JSONDecodeError:
                metrics = {}
            required_flags = (
                "walk_forward_passed", "purged_validation_passed",
                "point_in_time", "sample_gate_passed", "cost_model_validated",
            )
            missing = [name for name in required_flags if metrics.get(name) is not True]
            if missing:
                raise ValueError(
                    "model cannot be approved without explicit evidence flags: "
                    + ", ".join(missing)
                )
            if not str(row["artifact_uri"] or "").strip():
                raise ValueError("model cannot be approved without an artifact_uri")

            # P08 OpenQuant benchmark dominance check (Reid et al. 2026 Eq. 6)
            oq_keys = {"jensen_alpha", "excess_return", "sharpe", "profit_factor", "max_drawdown"}
            if any(k in metrics for k in oq_keys):
                try:
                    from research.openquant_eval import check_admissibility
                    verdict = check_admissibility(metrics)
                    if not verdict.admissible:
                        raise ValueError(
                            f"model cannot be approved: failed OpenQuant benchmark dominance gates: {', '.join(verdict.failed)}"
                        )
                except ImportError:
                    pass

        con.execute("UPDATE model_registry SET status=?,reviewed_at=?,reviewed_by=?,review_note=? WHERE id=?",
                    (status, _now(), reviewer[:200], note[:2000], int(model_id)))
        return dict(con.execute("SELECT * FROM model_registry WHERE id=?", (int(model_id),)).fetchone())


def drift_report(path: str = "trading_state.db") -> Dict[str, Any]:
    records = list_models(path)
    current_schema = None
    if os.path.exists(path):
        with _con(path) as con:
            row = con.execute("SELECT payload FROM proposals ORDER BY created_at DESC LIMIT 1").fetchone()
            if row:
                try:
                    payload = json.loads(row["payload"] or "{}")
                    current_schema = (payload.get("features") or {}).get("feature_schema_version")
                except json.JSONDecodeError:
                    pass
    observations = []
    for item in records:
        observations.append({
            "id": item["id"], "model_key": item["model_key"], "version": item["version"],
            "status": item["status"], "training_schema": item.get("feature_schema_version"),
            "current_proposal_schema": current_schema,
            "schema_mismatch": bool(current_schema and item.get("feature_schema_version") and current_schema != item.get("feature_schema_version")),
        })
    return {"models": observations, "execution_authority": False,
            "limitation": "Schema comparison is an alert only; distribution drift requires a labelled live sample and remains offline research."}


REQUIRED_EVIDENCE_FLAGS = (
    "walk_forward_passed", "purged_validation_passed",
    "point_in_time", "sample_gate_passed", "cost_model_validated",
)
_FAILED_RUN_STATUSES = {"INSUFFICIENT_DATA", "FAILED", "ERROR", "REJECTED", "BLOCKED"}


def activation_status(path: str = "trading_state.db", *, model_id: int) -> Dict[str, Any]:
    """Verify that an APPROVED registry row is genuinely deployable evidence-wise.

    Closes the self-attestation gap: approval flags must be corroborated by at
    least one append-only ``research_validation_runs`` row whose ``dataset_hash``
    matches the model's ``training_data_hash`` and whose recorded run did not
    fail its gates.  The referenced artifact must also exist on disk.  The
    verdict is advisory metadata for a future gated loader; it never grants
    execution authority by itself.
    """
    if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id <= 0:
        raise ValueError("model_id must be a positive integer")
    TradingStore(path)
    checks: Dict[str, Any] = {}
    reasons: List[str] = []
    with _con(path) as con:
        row = con.execute("SELECT * FROM model_registry WHERE id=?", (int(model_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown model registry id {model_id}")
        model = dict(row)
        try:
            metrics = json.loads(model.get("metrics") or "{}")
        except json.JSONDecodeError:
            metrics = {}

        checks["registry_status"] = str(model.get("status"))
        if str(model.get("status")) != "APPROVED":
            reasons.append(f"registry status is {model.get('status')}, not APPROVED")

        missing_flags = [name for name in REQUIRED_EVIDENCE_FLAGS if metrics.get(name) is not True]
        checks["evidence_flags_missing"] = missing_flags
        if missing_flags:
            reasons.append("evidence flags missing/false: " + ", ".join(missing_flags))

        artifact_uri = str(model.get("artifact_uri") or "").strip()
        checks["artifact_uri"] = artifact_uri
        checks["artifact_exists"] = bool(artifact_uri) and os.path.exists(artifact_uri)
        if not artifact_uri:
            reasons.append("artifact_uri is empty")
        elif not checks["artifact_exists"]:
            reasons.append(f"artifact_uri does not exist on disk: {artifact_uri}")

        training_hash = str(model.get("training_data_hash") or "").strip().lower()
        from research.research_runs import list_runs as list_validation_runs
        runs = list_validation_runs(path, limit=500)
        corroborating = []
        for run in runs:
            if str(run.get("dataset_hash") or "").lower() != training_hash:
                continue
            report = run.get("report") if isinstance(run.get("report"), dict) else {}
            status_text = str(run.get("status") or "").upper()
            failed_status = status_text in _FAILED_RUN_STATUSES
            oos_marker = bool(
                report.get("out_of_sample")
                or report.get("oos_validated")
                or (isinstance(report.get("out_of_sample_status"), str)
                    and report["out_of_sample_status"].upper() in {"PASS", "PASSED", "VALIDATED", "OK"})
            )
            if not failed_status and oos_marker:
                corroborating.append({
                    "run_id": run.get("run_id"), "run_kind": run.get("run_kind"),
                    "created_at": run.get("created_at"),
                })
        checks["ledger_runs_matching_dataset_hash"] = sum(
            1 for run in runs
            if str(run.get("dataset_hash") or "").lower() == training_hash)
        checks["corroborating_runs"] = corroborating[:5]
        if not corroborating:
            reasons.append(
                "no append-only validation run corroborates training_data_hash "
                "with a passing out-of-sample status")

        # Current-schema alignment (advisory warning only, mirrors drift_report).
        row_latest = con.execute(
            "SELECT payload FROM proposals ORDER BY created_at DESC LIMIT 1").fetchone()
        current_schema = None
        if row_latest:
            try:
                payload = json.loads(row_latest["payload"] or "{}")
                current_schema = (payload.get("features") or {}).get("feature_schema_version")
            except json.JSONDecodeError:
                pass
        checks["current_proposal_feature_schema"] = current_schema
        checks["schema_mismatch_with_current"] = bool(
            current_schema and model.get("feature_schema_version")
            and current_schema != model.get("feature_schema_version"))
        if checks["schema_mismatch_with_current"]:
            reasons.append("feature schema differs from current proposal schema")

    return {
        "model_id": int(model_id),
        "model_key": model.get("model_key"),
        "version": model.get("version"),
        "verdict": "ACTIVATION_ELIGIBLE" if not reasons else "BLOCKED",
        "reasons": reasons,
        "checks": checks,
        "execution_authority": False,
        "limitation": ("Eligibility metadata only; a runtime loader must still be "
                       "explicitly implemented and operator-approved before any "
                       "artifact influences sizing."),
    }


def load_approved_model(
    path: str = "trading_state.db",
    *,
    model_key: Optional[str] = None,
    kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load the most recent APPROVED model from the registry.

    Returns the model metadata including artifact_uri for loading the actual
    model weights. Returns None if no approved model is found.

    This function is advisory - it loads metadata only. The caller must
    implement the actual model loading and execution logic.
    """
    TradingStore(path)
    with _con(path) as con:
        query = "SELECT * FROM model_registry WHERE status='APPROVED'"
        params: list = []
        if model_key:
            query += " AND model_key=?"
            params.append(model_key)
        if kind:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY created_at DESC LIMIT 1"
        row = con.execute(query, params).fetchone()
        if not row:
            return None
        model = dict(row)
        try:
            model["metrics"] = json.loads(model.get("metrics") or "{}")
        except json.JSONDecodeError:
            model["metrics"] = {}
        return model


def list_approved_models(
    path: str = "trading_state.db",
    *,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List all APPROVED models from the registry."""
    TradingStore(path)
    with _con(path) as con:
        query = "SELECT * FROM model_registry WHERE status='APPROVED'"
        params: list = []
        if kind:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY created_at DESC"
        rows = con.execute(query, params).fetchall()
    models = []
    for row in rows:
        model = dict(row)
        try:
            model["metrics"] = json.loads(model.get("metrics") or "{}")
        except json.JSONDecodeError:
            model["metrics"] = {}
        models.append(model)
    return models
