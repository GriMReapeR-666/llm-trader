"""Local Agent Memory (offline SQLite document/KV store).

import logging

logger = logging.getLogger(__name__)
This is a fully
local SQLite store inspired by document + key-value layouts, used so the
trading agent can remember process events, journal snapshots, and
improvement-relevant details without any network or cloud credentials.

Local ``TradingMemory`` / ``AgentOperations`` remain authoritative for trading
truth.  This module never grants ``execution_authority`` and never blocks the
primary write path when disabled or on error (fail-closed helpers).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


_INLINE_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(?:MT5_PASSWORD|OPENROUTER_API_KEY|TOKENROUTER_KEY|TOKENROUTER_API_KEY|ROUTESME_KEY|ROUTESME_API_KEY|NEWSAPI_KEY|API_KEY|REMOTE_MIRROR_PASSWORD)\s*=\s*[^\s#,'\"]+"),
     "[REDACTED_ASSIGNMENT]"),
    (re.compile(r"(?i)\b(?:password|secret|api[_-]?key|token|authorization|cookie)\s*[:=]\s*['\"]?[^\s,'\"]+"),
     "[REDACTED_SECRET]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|rm-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,})\b"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\b(?:login|account(?:[_ -]?(?:id|number))?)\s*[:=]\s*\d{4,}\b"),
     "[REDACTED_ACCOUNT_ID]"),
)

_SENSITIVE_KEYS = (
    "password", "secret", "api_key", "token", "authorization", "cookie",
    "credential", "login", "account_id", "account_number",
)


def redact_payload(value: Any, key: str = "") -> Any:
    """Strip credentials/secrets while keeping operational structure useful."""
    if any(marker in str(key).lower() for marker in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact_payload(item_value, str(item_key))
                for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_payload(item, key) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in _INLINE_SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    return value


def _normalize_as_of(value: Any = None) -> str:
    """Point-in-time cutoff; rejects malformed timestamps (fail-closed)."""
    if value is None or value == "":
        return _utcnow()
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("local agent memory as_of must be a valid ISO-8601 timestamp with timezone") from exc


class LocalAgentMemoryConfig:
    """Environment/config-driven local agent-memory settings (no cloud)."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        db_path: str = "local_agent_memory.db",
        max_active_docs: int = 5000,
        retention_days: int = 90,
        compact_keep_per_collection: int = 200,
    ):
        self.enabled = bool(enabled)
        self.db_path = str(db_path or "local_agent_memory.db").strip() or "local_agent_memory.db"
        self.max_active_docs = max(100, int(max_active_docs))
        self.retention_days = max(1, int(retention_days))
        self.compact_keep_per_collection = max(10, int(compact_keep_per_collection))

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "LocalAgentMemoryConfig":
        source = env if env is not None else os.environ
        def _get(key, default=""):
            if isinstance(source, dict):
                return source.get(key, default)
            return os.getenv(key, default)
        def _int(name, default: int) -> int:
            raw = str(_get(name, str(default)) or default)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        return cls(
            enabled=_truthy(_get("LOCAL_MEMORY_ENABLED", "")),
            db_path=str(_get("LOCAL_MEMORY_PATH", "local_agent_memory.db") or "local_agent_memory.db"),
            max_active_docs=_int("LOCAL_MEMORY_MAX_ACTIVE", 5000),
            retention_days=_int("LOCAL_MEMORY_RETENTION_DAYS", 90),
            compact_keep_per_collection=_int("LOCAL_MEMORY_COMPACT_KEEP", 200),
        )


class LocalAgentMemory:
    """Offline document/KV agent memory inspired by document/KV layouts.

    Collections map roughly to document groups; KV rows hold compact indexes.
    All public write/read APIs return ``execution_authority: False``.
    """

    DOC_TYPES = frozenset({
        "event", "journal_snapshot", "trade_cycle", "proposal", "reject",
        "fill", "risk_decision", "agent_task", "heartbeat", "order_intent",
        "macro", "causal", "news", "working_memory", "memory_link", "other",
    })

    def __init__(self, config: Optional[LocalAgentMemoryConfig] = None):
        self.config = config or LocalAgentMemoryConfig.from_env()
        self._schema_ready = False
        if self.config.enabled:
            self._ensure_schema()

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "local_agent_memory",
            "inspired_by": "document/KV layout (local only; no cloud)",
            "enabled": self.config.enabled,
            "db_path": self.config.db_path,
            "ready": bool(self.config.enabled),
            "primary_store": "local_sqlite_trading_state",
            "authoritative": False,
            "cloud": False,
            "execution_authority": False,
            "network_required": False,
            "mode": "ACTIVE" if self.config.enabled else "DISABLED",
            "reason": (
                "local non-authoritative agent memory sidecar enabled"
                if self.config.enabled
                else "LOCAL_MEMORY_ENABLED is not true; TradingMemory/AgentOperations remain authoritative"
            ),
        }

    # ── public write APIs ────────────────────────────────────────────────────

    def remember_event(
        self,
        *,
        actor: str,
        event_type: str,
        payload: Any = None,
        run_id: str = "",
        correlation_id: str = "",
        severity: str = "INFO",
        collection: str = "operational_events",
        tags: Optional[Dict[str, Any]] = None,
        source_ids: Optional[Iterable[str]] = None,
        memory_links: Optional[Dict[str, Any]] = None,
        observed_at: str = "",
    ) -> Dict[str, Any]:
        """Persist a trading-process / agent operational event."""
        gate = self.status()
        if not gate.get("ready"):
            return {"stored": False, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False}
        doc_type = self._infer_event_doc_type(event_type, actor)
        safe = redact_payload(payload if isinstance(payload, (dict, list)) else {"value": payload})
        body = {
            "actor": str(actor or "")[:120],
            "event_type": str(event_type or "")[:160],
            "severity": str(severity or "INFO")[:32],
            "run_id": str(run_id or "")[:80],
            "correlation_id": str(correlation_id or "")[:200],
            "payload": safe,
        }
        merged_tags = {
            "actor": body["actor"],
            "event_type": body["event_type"],
            "severity": body["severity"],
            **(tags or {}),
        }
        return self._insert_document(
            doc_type=doc_type,
            collection=collection or "operational_events",
            doc_key=f"{body['actor']}:{body['event_type']}:{correlation_id or hashlib.sha256(_stable_json(body).encode()).hexdigest()[:16]}",
            payload=body,
            tags=merged_tags,
            source_ids=source_ids,
            memory_links=memory_links,
            observed_at=observed_at,
        )

    def remember_journal_snapshot(
        self,
        *,
        kind: str,
        payload: Any,
        snapshot_id: str = "",
        run_id: str = "",
        symbol: str = "",
        timeframe: str = "",
        candle_id: str = "",
        tags: Optional[Dict[str, Any]] = None,
        source_ids: Optional[Iterable[str]] = None,
        memory_links: Optional[Dict[str, Any]] = None,
        observed_at: str = "",
    ) -> Dict[str, Any]:
        """Persist a point-in-time journal / decision input snapshot."""
        gate = self.status()
        if not gate.get("ready"):
            return {"stored": False, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False}
        safe = redact_payload(payload if isinstance(payload, (dict, list)) else {"value": payload})
        body = {
            "kind": str(kind or "")[:120],
            "snapshot_id": str(snapshot_id or "")[:240],
            "run_id": str(run_id or "")[:80],
            "symbol": str(symbol or "")[:64],
            "timeframe": str(timeframe or "")[:32],
            "candle_id": str(candle_id or "")[:120],
            "payload": safe,
        }
        key = body["snapshot_id"] or f"{body['kind']}:{hashlib.sha256(_stable_json(safe).encode()).hexdigest()[:24]}"
        merged_tags = {
            "kind": body["kind"],
            "symbol": body["symbol"],
            "timeframe": body["timeframe"],
            **(tags or {}),
        }
        return self._insert_document(
            doc_type="journal_snapshot",
            collection="journal_snapshots",
            doc_key=key[:200],
            payload=body,
            tags=merged_tags,
            source_ids=source_ids or ([body["snapshot_id"]] if body["snapshot_id"] else None),
            memory_links=memory_links,
            observed_at=observed_at,
        )

    def remember_trade_cycle(
        self,
        *,
        cycle_key: str,
        stage: str,
        payload: Any = None,
        ticket: Any = None,
        symbol: str = "",
        side: str = "",
        run_id: str = "",
        tags: Optional[Dict[str, Any]] = None,
        source_ids: Optional[Iterable[str]] = None,
        memory_links: Optional[Dict[str, Any]] = None,
        observed_at: str = "",
    ) -> Dict[str, Any]:
        """Persist one stage of a proposal→risk→intent→fill→close cycle."""
        gate = self.status()
        if not gate.get("ready"):
            return {"stored": False, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False}
        stage_norm = str(stage or "unknown").strip().lower()[:80] or "unknown"
        doc_type = {
            "proposal": "proposal",
            "reject": "reject",
            "rejected": "reject",
            "fill": "fill",
            "filled": "fill",
            "risk": "risk_decision",
            "risk_decision": "risk_decision",
            "close": "trade_cycle",
            "closed": "trade_cycle",
            "trade_closed": "trade_cycle",
        }.get(stage_norm, "trade_cycle")
        safe = redact_payload(payload if isinstance(payload, (dict, list)) else {"value": payload})
        body = {
            "cycle_key": str(cycle_key or "")[:200],
            "stage": stage_norm,
            "ticket": ticket,
            "symbol": str(symbol or "")[:64],
            "side": str(side or "")[:16],
            "run_id": str(run_id or "")[:80],
            "payload": safe,
        }
        key = f"{body['cycle_key']}:{stage_norm}:{hashlib.sha256(_stable_json(body).encode()).hexdigest()[:12]}"
        merged_tags = {
            "stage": stage_norm,
            "symbol": body["symbol"],
            "side": body["side"],
            "ticket": str(ticket) if ticket is not None else "",
            **(tags or {}),
        }
        result = self._insert_document(
            doc_type=doc_type,
            collection="trade_cycles",
            doc_key=key[:200],
            payload=body,
            tags=merged_tags,
            source_ids=source_ids or ([str(ticket)] if ticket is not None else [str(cycle_key)]),
            memory_links=memory_links,
            observed_at=observed_at,
        )
        # Compact KV index of latest stage per cycle for fast lookup.
        if result.get("stored"):
            self._upsert_kv(
                namespace="trade_cycle_index",
                key=str(cycle_key or key)[:200],
                value={
                    "latest_stage": stage_norm,
                    "ticket": ticket,
                    "symbol": body["symbol"],
                    "doc_id": result.get("doc_id"),
                    "observed_at": result.get("observed_at"),
                },
            )
        return result

    # ── query / export / maintenance ─────────────────────────────────────────

    def query_memory(
        self,
        *,
        collection: str = "",
        doc_type: str = "",
        tags: Optional[Dict[str, Any]] = None,
        text_contains: str = "",
        as_of: Any = None,
        limit: int = 50,
        include_compacted: bool = False,
    ) -> Dict[str, Any]:
        """Point-in-time safe query: only documents with observed_at <= as_of."""
        gate = self.status()
        if not gate.get("ready"):
            return {"items": [], "count": 0, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False, "as_of": None}
        try:
            cutoff = _normalize_as_of(as_of)
        except ValueError as exc:
            return {"items": [], "count": 0, "error": str(exc), "mode": "INVALID_AS_OF",
                    "execution_authority": False, "as_of": None}
        limit_n = max(1, min(500, int(limit)))
        clauses = ["observed_at <= ?"]
        params: List[Any] = [cutoff]
        if not include_compacted:
            clauses.append("status = 'ACTIVE'")
        if collection:
            clauses.append("collection = ?")
            params.append(str(collection)[:120])
        if doc_type:
            clauses.append("doc_type = ?")
            params.append(str(doc_type)[:80])
        where = " AND ".join(clauses)
        with self._con() as con:
            rows = con.execute(
                f"""SELECT id, doc_type, collection, doc_key, observed_at, status,
                           tags_json, payload_json, payload_hash, source_ids_json,
                           memory_links_json, created_at
                    FROM memory_documents
                    WHERE {where}
                    ORDER BY observed_at DESC, id DESC
                    LIMIT ?""",
                (*params, limit_n * 4),  # over-fetch before tag/text filters
            ).fetchall()
        items = []
        tag_filters = {str(k): str(v).strip().upper() for k, v in (tags or {}).items()
                       if v not in (None, "", "UNKNOWN")}
        needle = str(text_contains or "").strip().lower()
        for row in rows:
            item = self._row_to_item(row)
            if tag_filters:
                item_tags = item.get("tags") or {}
                if not isinstance(item_tags, dict):
                    continue
                ok = True
                for key, expected in tag_filters.items():
                    actual = str(item_tags.get(key, "")).strip().upper()
                    if actual != expected:
                        ok = False
                        break
                if not ok:
                    continue
            if needle:
                blob = _stable_json({"tags": item.get("tags"), "payload": item.get("payload")}).lower()
                if needle not in blob:
                    continue
            items.append(item)
            if len(items) >= limit_n:
                break
        return {
            "items": items,
            "count": len(items),
            "as_of": cutoff,
            "execution_authority": False,
        }

    def export_for_improvement(
        self,
        *,
        as_of: Any = None,
        limit: int = 200,
        collections: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Bounded redacted export for offline improvement / distillation."""
        gate = self.status()
        if not gate.get("ready"):
            return {"records": [], "count": 0, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False}
        try:
            cutoff = _normalize_as_of(as_of)
        except ValueError as exc:
            return {"records": [], "count": 0, "error": str(exc), "mode": "INVALID_AS_OF",
                    "execution_authority": False}
        wanted = [str(c).strip() for c in (collections or []) if str(c).strip()]
        limit_n = max(1, min(2000, int(limit)))
        records: List[Dict[str, Any]] = []
        with self._con() as con:
            if wanted:
                placeholders = ",".join("?" for _ in wanted)
                rows = con.execute(
                    f"""SELECT id, doc_type, collection, doc_key, observed_at, tags_json,
                               payload_json, source_ids_json, memory_links_json, payload_hash
                        FROM memory_documents
                        WHERE status='ACTIVE' AND observed_at<=? AND collection IN ({placeholders})
                        ORDER BY observed_at DESC, id DESC LIMIT ?""",
                    (cutoff, *wanted, limit_n),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT id, doc_type, collection, doc_key, observed_at, tags_json,
                              payload_json, source_ids_json, memory_links_json, payload_hash
                       FROM memory_documents
                       WHERE status='ACTIVE' AND observed_at<=?
                       ORDER BY observed_at DESC, id DESC LIMIT ?""",
                    (cutoff, limit_n),
                ).fetchall()
        for row in rows:
            item = self._row_to_item(row)
            records.append({
                "doc_id": item["doc_id"],
                "doc_type": item["doc_type"],
                "collection": item["collection"],
                "doc_key": item["doc_key"],
                "observed_at": item["observed_at"],
                "tags": item["tags"],
                "source_ids": item["source_ids"],
                "memory_links": item["memory_links"],
                "payload_hash": item["payload_hash"],
                # Payload already redacted at write time; re-redact for safety.
                "payload": redact_payload(item["payload"]),
                "improvement_hint": self._improvement_hint(item),
            })
        return {
            "records": records,
            "count": len(records),
            "as_of": cutoff,
            "exported_at": _utcnow(),
            "execution_authority": False,
            "note": "Advisory export only; never authorizes trading actions.",
        }

    def compact_memory(self, *, dry_run: bool = False) -> Dict[str, Any]:
        """Mark excess ACTIVE docs COMPACTED per collection; keep newest."""
        gate = self.status()
        if not gate.get("ready"):
            return {"compacted": 0, "reason": gate.get("reason"), "mode": gate.get("mode"),
                    "execution_authority": False}
        keep = self.config.compact_keep_per_collection
        now = _utcnow()
        compacted = 0
        with self._con() as con:
            collections = [r[0] for r in con.execute(
                "SELECT DISTINCT collection FROM memory_documents WHERE status='ACTIVE'"
            ).fetchall()]
            for collection in collections:
                rows = con.execute(
                    """SELECT id FROM memory_documents
                       WHERE collection=? AND status='ACTIVE'
                       ORDER BY observed_at DESC, id DESC""",
                    (collection,),
                ).fetchall()
                excess = [int(r[0]) for r in rows[keep:]]
                if not excess:
                    continue
                if dry_run:
                    compacted += len(excess)
                    continue
                con.executemany(
                    "UPDATE memory_documents SET status='COMPACTED', updated_at=? WHERE id=? AND status='ACTIVE'",
                    [(now, doc_id) for doc_id in excess],
                )
                compacted += len(excess)
            if not dry_run and compacted:
                con.execute(
                    """INSERT INTO memory_maintenance(created_at, action, detail_json)
                       VALUES(?,?,?)""",
                    (now, "compact", _stable_json({"compacted": compacted, "keep_per_collection": keep})),
                )
        return {"compacted": compacted, "keep_per_collection": keep, "dry_run": bool(dry_run),
                "execution_authority": False}

    def run_maintenance(self, *, dry_run: bool = False) -> Dict[str, Any]:
        """Retention archive + compaction. Does not delete evidence by default."""
        gate = self.status()
        if not gate.get("ready"):
            return {"archived": 0, "compacted": 0, "reason": gate.get("reason"),
                    "mode": gate.get("mode"), "execution_authority": False}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)).isoformat()
        now = _utcnow()
        archived = 0
        with self._con() as con:
            rows = con.execute(
                """SELECT id FROM memory_documents
                   WHERE status='ACTIVE' AND observed_at < ?""",
                (cutoff,),
            ).fetchall()
            ids = [int(r[0]) for r in rows]
            if not dry_run and ids:
                con.executemany(
                    "UPDATE memory_documents SET status='ARCHIVED', updated_at=? WHERE id=? AND status='ACTIVE'",
                    [(now, doc_id) for doc_id in ids],
                )
                con.execute(
                    """INSERT INTO memory_maintenance(created_at, action, detail_json)
                       VALUES(?,?,?)""",
                    (now, "archive", _stable_json({
                        "archived": len(ids),
                        "retention_days": self.config.retention_days,
                        "cutoff": cutoff,
                    })),
                )
            archived = len(ids)
        compact = self.compact_memory(dry_run=dry_run)
        # Soft enforce max_active_docs after archive/compact.
        with self._con() as con:
            active = con.execute(
                "SELECT COUNT(*) FROM memory_documents WHERE status='ACTIVE'"
            ).fetchone()[0]
        return {
            "archived": archived,
            "compacted": compact.get("compacted", 0),
            "active_count": int(active),
            "max_active_docs": self.config.max_active_docs,
            "retention_days": self.config.retention_days,
            "cutoff": cutoff,
            "dry_run": bool(dry_run),
            "execution_authority": False,
        }

    def get_kv(self, namespace: str, key: str) -> Dict[str, Any]:
        gate = self.status()
        if not gate.get("ready"):
            return {"found": False, "reason": gate.get("reason"), "execution_authority": False}
        with self._con() as con:
            row = con.execute(
                "SELECT namespace, kv_key, revision, updated_at, value_json FROM memory_kv WHERE namespace=? AND kv_key=?",
                (str(namespace)[:120], str(key)[:200]),
            ).fetchone()
        if not row:
            return {"found": False, "execution_authority": False}
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            value = row["value_json"]
        return {
            "found": True,
            "namespace": row["namespace"],
            "key": row["kv_key"],
            "revision": row["revision"],
            "updated_at": row["updated_at"],
            "value": value,
            "execution_authority": False,
        }

    # ── internals ────────────────────────────────────────────────────────────

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.config.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        parent = os.path.dirname(os.path.abspath(self.config.db_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS memory_documents (
              id INTEGER PRIMARY KEY,
              doc_type TEXT NOT NULL,
              collection TEXT NOT NULL,
              doc_key TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              source_ids_json TEXT NOT NULL,
              memory_links_json TEXT NOT NULL,
              UNIQUE(collection, doc_key)
            );
            CREATE INDEX IF NOT EXISTS idx_local_mem_obs
              ON memory_documents(status, observed_at DESC, collection);
            CREATE INDEX IF NOT EXISTS idx_local_mem_type
              ON memory_documents(doc_type, observed_at DESC);
            CREATE TABLE IF NOT EXISTS memory_kv (
              namespace TEXT NOT NULL,
              kv_key TEXT NOT NULL,
              revision INTEGER NOT NULL,
              updated_at TEXT NOT NULL,
              value_json TEXT NOT NULL,
              PRIMARY KEY(namespace, kv_key)
            );
            CREATE TABLE IF NOT EXISTS memory_maintenance (
              id INTEGER PRIMARY KEY,
              created_at TEXT NOT NULL,
              action TEXT NOT NULL,
              detail_json TEXT NOT NULL
            );
            """)
        self._schema_ready = True

    def _insert_document(
        self,
        *,
        doc_type: str,
        collection: str,
        doc_key: str,
        payload: Any,
        tags: Optional[Dict[str, Any]] = None,
        source_ids: Optional[Iterable[str]] = None,
        memory_links: Optional[Dict[str, Any]] = None,
        observed_at: str = "",
    ) -> Dict[str, Any]:
        self._ensure_schema()
        now = _utcnow()
        try:
            observed = _normalize_as_of(observed_at) if observed_at else now
        except ValueError:
            observed = now
        safe_payload = redact_payload(payload)
        safe_tags = redact_payload(tags or {})
        safe_links = self._normalize_memory_links(memory_links)
        sources = []
        for source in source_ids or []:
            text = str(source or "").strip()
            if text:
                sources.append(text[:200])
        encoded = _stable_json(safe_payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        dtype = str(doc_type or "other")[:80]
        if dtype not in self.DOC_TYPES:
            dtype = "other"
        coll = str(collection or "misc")[:120]
        key = str(doc_key or digest)[:200]
        with self._con() as con:
            # Upsert keeps latest revision of same collection/key without losing history
            # of distinct keys; status returns to ACTIVE if previously compacted.
            existing = con.execute(
                "SELECT id, payload_hash FROM memory_documents WHERE collection=? AND doc_key=?",
                (coll, key),
            ).fetchone()
            if existing:
                if existing["payload_hash"] == digest:
                    # Content is identical, skip redundant write to reduce disk I/O and churn
                    return {
                        "stored": True,
                        "doc_id": int(existing["id"]),
                        "doc_type": dtype,
                        "collection": coll,
                        "doc_key": key,
                        "payload_hash": digest,
                        "observed_at": observed,
                        "memory_links": safe_links,
                        "deduplicated": True,
                        "execution_authority": False,
                    }
                con.execute(
                    """UPDATE memory_documents
                       SET doc_type=?, observed_at=?, updated_at=?, status='ACTIVE',
                           tags_json=?, payload_json=?, payload_hash=?,
                           source_ids_json=?, memory_links_json=?
                       WHERE id=?""",
                    (dtype, observed, now, _stable_json(safe_tags), encoded, digest,
                     _stable_json(sources), _stable_json(safe_links), int(existing["id"])),
                )
                doc_id = int(existing["id"])
            else:
                # Check for identical active document in the same collection
                recent_duplicate = con.execute(
                    "SELECT id, doc_key FROM memory_documents WHERE collection=? AND doc_type=? AND payload_hash=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1",
                    (coll, dtype, digest),
                ).fetchone()
                if recent_duplicate:
                    return {
                        "stored": True,
                        "doc_id": int(recent_duplicate["id"]),
                        "doc_type": dtype,
                        "collection": coll,
                        "doc_key": recent_duplicate["doc_key"],
                        "payload_hash": digest,
                        "observed_at": observed,
                        "memory_links": safe_links,
                        "deduplicated": True,
                        "execution_authority": False,
                    }
                cur = con.execute(
                    """INSERT INTO memory_documents(
                         doc_type, collection, doc_key, observed_at, created_at, updated_at,
                         status, tags_json, payload_json, payload_hash, source_ids_json,
                         memory_links_json)
                       VALUES(?,?,?,?,?,?,'ACTIVE',?,?,?,?,?)""",
                    (dtype, coll, key, observed, now, now, _stable_json(safe_tags),
                     encoded, digest, _stable_json(sources), _stable_json(safe_links)),
                )
                doc_id = int(cur.lastrowid)
            active = con.execute(
                "SELECT COUNT(*) FROM memory_documents WHERE status='ACTIVE'"
            ).fetchone()[0]
        overflow = int(active) > self.config.max_active_docs
        if overflow:
            # Best-effort soft compact; never raise to callers.
            try:
                self.compact_memory()
            except Exception as exc:
                logger.debug("memory operation failed: %s", exc)
        return {
            "stored": True,
            "doc_id": doc_id,
            "doc_type": dtype,
            "collection": coll,
            "doc_key": key,
            "payload_hash": digest,
            "observed_at": observed,
            "memory_links": safe_links,
            "execution_authority": False,
        }

    def _upsert_kv(self, *, namespace: str, key: str, value: Any) -> None:
        self._ensure_schema()
        now = _utcnow()
        encoded = _stable_json(redact_payload(value))
        ns = str(namespace)[:120]
        kv = str(key)[:200]
        with self._con() as con:
            row = con.execute(
                "SELECT revision FROM memory_kv WHERE namespace=? AND kv_key=?",
                (ns, kv),
            ).fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            if row:
                con.execute(
                    "UPDATE memory_kv SET revision=?, updated_at=?, value_json=? WHERE namespace=? AND kv_key=?",
                    (revision, now, encoded, ns, kv),
                )
            else:
                con.execute(
                    "INSERT INTO memory_kv(namespace, kv_key, revision, updated_at, value_json) VALUES(?,?,?,?,?)",
                    (ns, kv, revision, now, encoded),
                )

    @staticmethod
    def _normalize_memory_links(links: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Accept L1/L2/L3 trading-memory link ids without granting authority."""
        if not isinstance(links, dict):
            return {}
        out: Dict[str, Any] = {}
        for layer in ("L0", "L1", "L2", "L3", "l0", "l1", "l2", "l3",
                      "raw_trace", "atomic_fact", "scenario", "core_memory",
                      "verified_memory", "narrative", "working_memory"):
            if layer in links and links[layer] not in (None, "", [], {}):
                out[layer.upper() if len(layer) == 2 else layer] = links[layer]
        # Also keep explicit keys if present.
        for key in ("fact_ids", "scenario_keys", "core_memory_ids", "narrative_ids",
                    "verified_memory_ids", "retrieval_ids"):
            if key in links and links[key] not in (None, "", [], {}):
                out[key] = links[key]
        return out

    @staticmethod
    def _infer_event_doc_type(event_type: str, actor: str) -> str:
        text = f"{actor} {event_type}".lower()
        if "heartbeat" in text:
            return "heartbeat"
        if "order_intent" in text or "intent" in text:
            return "order_intent"
        if "risk" in text or "validation_decision" in text:
            return "risk_decision"
        if "task_" in text or "task" in text:
            return "agent_task"
        if "macro" in text or "calendar" in text:
            return "macro"
        if "causal" in text:
            return "causal"
        if "news" in text:
            return "news"
        if "reject" in text:
            return "reject"
        if "fill" in text or "filled" in text:
            return "fill"
        if "proposal" in text:
            return "proposal"
        if "working_memory" in text:
            return "working_memory"
        return "event"

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
        def decode(raw: Any) -> Any:
            if not isinstance(raw, str):
                return raw
            try:
                return json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return raw

        keys = set(row.keys())
        return {
            "doc_id": row["id"],
            "doc_type": row["doc_type"],
            "collection": row["collection"],
            "doc_key": row["doc_key"],
            "observed_at": row["observed_at"],
            "status": row["status"] if "status" in keys else "ACTIVE",
            "created_at": row["created_at"] if "created_at" in keys else None,
            "tags": decode(row["tags_json"]),
            "payload": decode(row["payload_json"]),
            "payload_hash": row["payload_hash"],
            "source_ids": decode(row["source_ids_json"]),
            "memory_links": decode(row["memory_links_json"]),
            "execution_authority": False,
        }

    @staticmethod
    def _improvement_hint(item: Dict[str, Any]) -> str:
        dtype = str(item.get("doc_type") or "")
        tags = item.get("tags") or {}
        if dtype in {"reject", "risk_decision"}:
            return "Review reject/risk rationale for recurring filter friction."
        if dtype in {"fill", "trade_cycle"}:
            return "Compare cycle outcome labels with proposal confidence."
        if dtype in {"macro", "news", "causal"}:
            return "Check whether event reactions improved or hurt expectancy."
        if dtype == "working_memory":
            return "Working context only; do not treat as verified trade evidence."
        stage = str(tags.get("stage") or "")
        if stage:
            return f"Trade-cycle stage '{stage}' available for process review."
        return "Operational memory for process improvement; not order authority."


# ── safe entry points (never raise, never authorize) ─────────────────────────

def default_local_memory(env: Optional[Dict[str, str]] = None) -> LocalAgentMemory:
    return LocalAgentMemory(LocalAgentMemoryConfig.from_env(env))


def maybe_remember_event(
    *,
    actor: str,
    event_type: str,
    payload: Any = None,
    run_id: str = "",
    correlation_id: str = "",
    severity: str = "INFO",
    memory_links: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        return default_local_memory(env).remember_event(
            actor=actor,
            event_type=event_type,
            payload=payload,
            run_id=run_id,
            correlation_id=correlation_id,
            severity=severity,
            memory_links=memory_links,
        )
    except Exception as exc:
        return {"stored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}


def maybe_remember_journal_snapshot(
    *,
    kind: str,
    payload: Any,
    snapshot_id: str = "",
    run_id: str = "",
    symbol: str = "",
    timeframe: str = "",
    candle_id: str = "",
    memory_links: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        return default_local_memory(env).remember_journal_snapshot(
            kind=kind,
            payload=payload,
            snapshot_id=snapshot_id,
            run_id=run_id,
            symbol=symbol,
            timeframe=timeframe,
            candle_id=candle_id,
            memory_links=memory_links,
        )
    except Exception as exc:
        return {"stored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}


def maybe_remember_trade_cycle(
    *,
    cycle_key: str,
    stage: str,
    payload: Any = None,
    ticket: Any = None,
    symbol: str = "",
    side: str = "",
    run_id: str = "",
    memory_links: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        return default_local_memory(env).remember_trade_cycle(
            cycle_key=cycle_key,
            stage=stage,
            payload=payload,
            ticket=ticket,
            symbol=symbol,
            side=side,
            run_id=run_id,
            memory_links=memory_links,
        )
    except Exception as exc:
        return {"stored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}


def maybe_remember_working_memory(entry: Dict[str, Any], *, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Mirror a working-memory write into the local document store (advisory)."""
    try:
        memory = default_local_memory(env)
        if not memory.config.enabled:
            return {"stored": False, "reason": "LOCAL_MEMORY_ENABLED is not true",
                    "execution_authority": False}
        return memory._insert_document(
            doc_type="working_memory",
            collection="working_memory",
            doc_key=f"{entry.get('namespace', '')}:{entry.get('memory_key', '')}"[:200],
            payload={
                "namespace": entry.get("namespace"),
                "memory_key": entry.get("memory_key"),
                "kind": entry.get("kind"),
                "priority": entry.get("priority"),
                "revision": entry.get("revision"),
                "payload": entry.get("payload"),
                "payload_hash": entry.get("payload_hash"),
            },
            tags={
                "kind": entry.get("kind"),
                "namespace": entry.get("namespace"),
            },
            source_ids=entry.get("source_ids") or [],
            memory_links={"working_memory": entry.get("memory_key")},
            observed_at=str(entry.get("updated_at") or ""),
        )
    except Exception as exc:
        return {"stored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}

