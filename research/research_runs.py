"""Append-only ledger for offline validation reports.

Research reports are evidence, never order permission.  The ledger stores the
exact report/configuration hashes and dataset metadata so a later promotion
review can prove which run produced a metric without rerunning mutable code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dataset_hash(report: Dict[str, Any], dataset: Dict[str, Any]) -> str:
    """Return a canonical SHA-256 even when a producer supplied a label.

    Older research producers sometimes called a short dataset identifier a
    ``training_data_hash``.  Preserve that identifier as input, but never put
    a non-cryptographic value in the ledger's hash column.
    """
    supplied = str(report.get("training_data_hash") or dataset.get("training_data_hash") or "").strip()
    if len(supplied) == 64:
        try:
            int(supplied, 16)
            return supplied.lower()
        except ValueError:
            pass
    return _hash({"supplied_identifier": supplied, "dataset": dataset})


def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def ensure(path: str = "trading_state.db") -> None:
    with _con(path) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS research_validation_runs (
          id INTEGER PRIMARY KEY,
          run_id TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          run_kind TEXT NOT NULL,
          status TEXT NOT NULL,
          dataset_hash TEXT NOT NULL,
          report_hash TEXT NOT NULL,
          configuration TEXT NOT NULL,
          report TEXT NOT NULL,
          execution_authority INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_research_validation_runs_kind
          ON research_validation_runs(run_kind, created_at);
        CREATE TRIGGER IF NOT EXISTS research_validation_runs_no_update
        BEFORE UPDATE ON research_validation_runs
        BEGIN
          SELECT RAISE(ABORT, 'research validation runs are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS research_validation_runs_no_delete
        BEFORE DELETE ON research_validation_runs
        BEGIN
          SELECT RAISE(ABORT, 'research validation runs are append-only');
        END;
        """)


def record(path: str, run_kind: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one immutable research report and return its audit identity."""
    if not isinstance(report, dict):
        raise ValueError("research report must be an object")
    kind = str(run_kind or "").strip().lower()
    if not kind:
        raise ValueError("research run kind is required")
    created_at = _now()
    dataset = report.get("dataset") if isinstance(report.get("dataset"), dict) else {}
    dataset_hash = _dataset_hash(report, dataset)
    report_hash = _hash(report)
    run_id = hashlib.sha256(f"{kind}|{created_at}|{report_hash}".encode("utf-8")).hexdigest()[:32]
    ensure(path)
    with _con(path) as con:
        con.execute("""INSERT INTO research_validation_runs(
            run_id,created_at,run_kind,status,dataset_hash,report_hash,
            configuration,report,execution_authority
        ) VALUES(?,?,?,?,?,?,?,?,0)""", (
            run_id, created_at, kind, str(report.get("status", "UNKNOWN"))[:100],
            dataset_hash, report_hash,
            _canonical(report.get("configuration", {})), _canonical(report),
        ))
    return {
        "run_id": run_id, "created_at": created_at, "run_kind": kind,
        "status": str(report.get("status", "UNKNOWN")),
        "dataset_hash": dataset_hash, "report_hash": report_hash,
        "execution_authority": False,
    }


def list_runs(path: str = "trading_state.db", *, run_kind: Optional[str] = None,
              limit: int = 20) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    ensure(path)
    with _con(path) as con:
        rows = con.execute(
            "SELECT * FROM research_validation_runs "
            "WHERE (? IS NULL OR run_kind=?) ORDER BY created_at DESC,id DESC LIMIT ?",
            (run_kind, run_kind, max(0, int(limit))),
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        for field in ("configuration", "report"):
            try:
                item[field] = json.loads(item[field] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                item[field] = {"malformed": True}
        item["execution_authority"] = False
        output.append(item)
    return output
