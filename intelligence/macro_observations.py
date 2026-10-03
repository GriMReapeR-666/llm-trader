"""Point-in-time macro observations bound to catalog entities.

Ingests FRED/calendar snapshots into a local SQLite ledger shared with the
trading journal path. Observations are research evidence only and never grant
execution authority.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from economic_dynamics_catalog import FREE_DATA_BINDINGS


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def ensure_schema(path: str) -> None:
    with _con(path) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS macro_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                entity TEXT NOT NULL,
                provider TEXT NOT NULL,
                series TEXT NOT NULL,
                observed_at TEXT,
                value REAL,
                previous REAL,
                surprise REAL,
                source_id TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                execution_authority INTEGER NOT NULL DEFAULT 0
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_macro_obs_entity ON macro_observations(entity, captured_at)"
        )


def _series_to_entity() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for binding in FREE_DATA_BINDINGS:
        if str(binding.get("provider") or "").upper() != "FRED":
            continue
        entity = str(binding.get("entity") or "")
        for series in binding.get("series") or []:
            mapping[str(series).upper()] = entity
    # Common aliases used by the live fetch path
    mapping.setdefault("GDP", "GROWTH")
    mapping.setdefault("CPIAUCSL", "INFLATION")
    mapping.setdefault("UNRATE", "EMPLOYMENT")
    mapping.setdefault("DFF", "POLICY_RATE")
    mapping.setdefault("VIXCLS", "VIX")
    return mapping


def _extract_value(payload: Any) -> Dict[str, Optional[float]]:
    value = previous = surprise = None
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        value = float(payload) if math.isfinite(float(payload)) else None
        return {"value": value, "previous": None, "surprise": None}
    if isinstance(payload, dict):
        for key in ("value", "latest", "observation", "actual"):
            if payload.get(key) is None:
                continue
            try:
                number = float(payload[key])
                if math.isfinite(number):
                    value = number
                    break
            except (TypeError, ValueError, OverflowError):
                continue
        for key in ("previous", "prior"):
            if payload.get(key) is None:
                continue
            try:
                number = float(payload[key])
                if math.isfinite(number):
                    previous = number
                    break
            except (TypeError, ValueError, OverflowError):
                continue
        if payload.get("surprise") is not None:
            try:
                number = float(payload["surprise"])
                if math.isfinite(number):
                    surprise = number
            except (TypeError, ValueError, OverflowError):
                surprise = None
        elif value is not None and previous is not None:
            surprise = value - previous
        observed_at = payload.get("date") or payload.get("observed_at") or payload.get("realtime_start")
        return {
            "value": value,
            "previous": previous,
            "surprise": surprise,
            "observed_at": observed_at,
        }
    return {"value": None, "previous": None, "surprise": None, "observed_at": None}


def ingest_fred_snapshot(
    path: str,
    macro_data: Dict[str, Any],
    *,
    source_id: str = "",
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist FRED series values as entity-bound macro observations."""
    if not isinstance(macro_data, dict) or not macro_data:
        return {
            "status": "SKIPPED",
            "inserted": 0,
            "reason": "empty macro_data",
            "execution_authority": False,
        }
    ensure_schema(path)
    mapping = _series_to_entity()
    stamp = captured_at or utcnow()
    inserted = 0
    rows: List[Dict[str, Any]] = []
    with _con(path) as con:
        for series, payload in macro_data.items():
            series_key = str(series).upper()
            entity = mapping.get(series_key, series_key)
            extracted = _extract_value(payload)
            con.execute(
                """INSERT INTO macro_observations(
                    captured_at, entity, provider, series, observed_at, value, previous, surprise,
                    source_id, payload, execution_authority
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    stamp,
                    entity[:120],
                    "FRED",
                    series_key[:120],
                    str(extracted.get("observed_at") or "")[:80] or None,
                    extracted.get("value"),
                    extracted.get("previous"),
                    extracted.get("surprise"),
                    str(source_id or "")[:200],
                    json.dumps(payload if isinstance(payload, (dict, list)) else {"value": payload}, default=str),
                ),
            )
            inserted += 1
            rows.append({
                "entity": entity,
                "series": series_key,
                "value": extracted.get("value"),
                "surprise": extracted.get("surprise"),
            })
    return {
        "status": "INGESTED",
        "inserted": inserted,
        "observations": rows,
        "execution_authority": False,
    }


def ingest_calendar_events(
    path: str,
    events: Iterable[Dict[str, Any]],
    *,
    source_id: str = "",
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist high-level calendar surprises as EVENT_CLASS observations."""
    ensure_schema(path)
    stamp = captured_at or utcnow()
    inserted = 0
    with _con(path) as con:
        for event in events or []:
            if not isinstance(event, dict):
                continue
            currency = str(event.get("currency") or event.get("country") or "MACRO").upper()
            title = str(event.get("event") or event.get("title") or "calendar_event")
            extracted = _extract_value(event)
            entity = f"EVENT:{currency}:{title[:40]}"
            con.execute(
                """INSERT INTO macro_observations(
                    captured_at, entity, provider, series, observed_at, value, previous, surprise,
                    source_id, payload, execution_authority
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    stamp,
                    entity[:120],
                    "CALENDAR",
                    title[:120],
                    str(event.get("time") or event.get("date") or "")[:80] or None,
                    extracted.get("value"),
                    extracted.get("previous"),
                    extracted.get("surprise"),
                    str(source_id or "")[:200],
                    json.dumps(event, default=str)[:8000],
                ),
            )
            inserted += 1
    return {
        "status": "INGESTED" if inserted else "SKIPPED",
        "inserted": inserted,
        "execution_authority": False,
    }


def ingest_alternative_snapshot(
    path: str,
    observations: Iterable[Dict[str, Any]],
    *,
    captured_at: str = "",
) -> Dict[str, Any]:
    """Persist alternative nowcast rows into macro_observations ledger."""
    ensure_schema(path)
    stamp = captured_at or utcnow()
    inserted = 0
    with _con(path) as con:
        for row in observations or []:
            if not isinstance(row, dict):
                continue
            entity = str(row.get("entity") or "UNKNOWN")[:120]
            provider = str(row.get("provider") or "alternative")[:80]
            source_id = str(row.get("source_id") or "")[:120]
            value = row.get("value")
            try:
                numeric = float(value) if value is not None else None
                if numeric is not None and not math.isfinite(numeric):
                    numeric = None
            except (TypeError, ValueError):
                numeric = None
            con.execute(
                """INSERT INTO macro_observations
                   (captured_at, entity, provider, series, observed_at, value,
                    previous, surprise, source_id, payload, execution_authority)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    stamp,
                    entity,
                    provider,
                    source_id,
                    str(row.get("observed_at") or stamp),
                    numeric,
                    None,
                    None,
                    source_id,
                    json.dumps(row, default=str)[:8000],
                ),
            )
            inserted += 1
    return {
        "status": "INGESTED" if inserted else "SKIPPED",
        "inserted": inserted,
        "execution_authority": False,
    }


def recent(
    path: str,
    *,
    entity: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    ensure_schema(path)
    with _con(path) as con:
        if entity:
            rows = con.execute(
                """SELECT * FROM macro_observations WHERE entity=?
                   ORDER BY observation_id DESC LIMIT ?""",
                (str(entity)[:120], int(limit)),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM macro_observations ORDER BY observation_id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    return {
        "status": "OK",
        "count": len(rows),
        "items": [dict(row) for row in rows],
        "execution_authority": False,
    }
