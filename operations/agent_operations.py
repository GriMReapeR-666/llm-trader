"""Durable, local-first operating state for the continuous trading agents.

This module never connects to MT5 or an LLM.  It owns event audit records,
idempotency keys, task recovery, LLM budgets, and a reliable memory outbox.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from datetime import datetime, timedelta, timezone
import json
import hashlib
import math
import os
import re
import sqlite3
import uuid
from typing import Any, Dict, Iterable, Optional, Tuple

try:
    from operations.agent_contracts import (
        require as require_agent_action,
        require_order_authority,
        require_write as require_agent_write,
    )
    from operations.setup_labeling import persist_setup_label
except ImportError:
    from operations.agent_contracts import (
        require as require_agent_action,
        require_order_authority,
        require_write as require_agent_write,
    )
    from research.setup_labeling import persist_setup_label


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentOperations:
    SAFETY_KINDS = frozenset({'error', 'risk_decision', 'position_event', 'kill_switch', 'reconciliation', 'incident'})
    # A RESERVED request is a local lease.  A crashed worker cannot call
    # finish_llm(), so the lease must eventually become reusable while the
    # request still counts toward hourly/daily attempt budgets.
    LLM_RESERVATION_MAX_AGE_SECONDS = 900
    ORDER_INTENT_TRANSITIONS = {
        "CREATED": frozenset({"CREATED", "PENDING", "REJECTED", "CANCELLED", "ERROR", "UNRESOLVED"}),
        "PENDING": frozenset({"PENDING", "PARTIAL", "FILLED", "REJECTED", "CANCELLED", "EXPIRED", "ERROR", "UNRESOLVED"}),
        "PARTIAL": frozenset({"PARTIAL", "FILLED", "CANCELLED", "EXPIRED", "ERROR", "UNRESOLVED"}),
        "UNRESOLVED": frozenset({"UNRESOLVED", "PARTIAL", "FILLED", "REJECTED", "CANCELLED", "EXPIRED", "ERROR"}),
        "ERROR": frozenset({"ERROR", "UNRESOLVED", "REJECTED", "CANCELLED"}),
        "FILLED": frozenset({"FILLED"}),
        "REJECTED": frozenset({"REJECTED"}),
        "CANCELLED": frozenset({"CANCELLED"}),
        "EXPIRED": frozenset({"EXPIRED"}),
    }

    _INLINE_SECRET_PATTERNS = (
        (re.compile(r"(?i)\b(?:MT5_PASSWORD|OPENROUTER_API_KEY|TOKENROUTER_KEY|TOKENROUTER_API_KEY|ROUTESME_KEY|ROUTESME_API_KEY|NEWSAPI_KEY|API_KEY)\s*=\s*[^\s#,'\"]+"),
         "[REDACTED_ASSIGNMENT]"),
        (re.compile(r"(?i)\b(?:password|secret|api[_-]?key|token|authorization|cookie)\s*[:=]\s*['\"]?[^\s,'\"]+"),
         "[REDACTED_SECRET]"),
        (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED_TOKEN]"),
        (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|rm-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,})\b"), "[REDACTED_KEY]"),
        (re.compile(r"(?i)\b(?:login|account(?:[_ -]?(?:id|number))?)\s*[:=]\s*\d{4,}\b"),
         "[REDACTED_ACCOUNT_ID]"),
    )

    @staticmethod
    def _finite_nonnegative(value: Any, name: str, *, allow_none: bool = False) -> float:
        if value is None and allow_none:
            return 0.0
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be finite and non-negative") from None
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return parsed

    @staticmethod
    def _nonnegative_integer(value: Any, name: str, *, allow_none: bool = False) -> int:
        if value is None and allow_none:
            return 0
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be a non-negative integer") from None
        if not math.isfinite(parsed) or parsed < 0 or not parsed.is_integer():
            raise ValueError(f"{name} must be a non-negative integer")
        return int(parsed)

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self._compacting = False
        self._init_db()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        # Wait for locks instead of raising "database is locked" while the
        # main loop, reflection lane, and TUI share the same store.
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_db(self) -> None:
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS agent_runs (
              run_id TEXT PRIMARY KEY, agent TEXT NOT NULL, started_at TEXT NOT NULL,
              heartbeat_at TEXT NOT NULL, status TEXT NOT NULL, config_hash TEXT, code_version TEXT,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operational_events (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT, actor TEXT NOT NULL,
              event_type TEXT NOT NULL, correlation_id TEXT, severity TEXT NOT NULL,
              payload TEXT NOT NULL, payload_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_operations_events ON operational_events(event_type, created_at);
            CREATE TRIGGER IF NOT EXISTS operational_events_immutable_update
              BEFORE UPDATE ON operational_events BEGIN SELECT RAISE(ABORT, 'operational events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS operational_events_immutable_delete
              BEFORE DELETE ON operational_events BEGIN SELECT RAISE(ABORT, 'operational events are immutable'); END;
            CREATE TABLE IF NOT EXISTS agent_tasks (
              idempotency_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              run_id TEXT, agent TEXT NOT NULL, task_type TEXT NOT NULL, status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 1, max_attempts INTEGER NOT NULL DEFAULT 3,
              lease_expires_at TEXT, payload TEXT NOT NULL, result TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_task_edges (
              parent_key TEXT NOT NULL, child_key TEXT NOT NULL, relation TEXT NOT NULL,
              created_at TEXT NOT NULL, PRIMARY KEY(parent_key, child_key, relation),
              FOREIGN KEY(parent_key) REFERENCES agent_tasks(idempotency_key),
              FOREIGN KEY(child_key) REFERENCES agent_tasks(idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_task_edges_child ON agent_task_edges(child_key);
            CREATE TABLE IF NOT EXISTS llm_requests (
              request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, completed_at TEXT, run_id TEXT,
              strategy TEXT, provider TEXT, model TEXT, trigger_key TEXT, status TEXT NOT NULL,
              prompt_hash TEXT, response_hash TEXT, latency_ms REAL, tokens INTEGER, cost REAL,
              attempt_count INTEGER NOT NULL DEFAULT 0, parse_status TEXT, response_chars INTEGER NOT NULL DEFAULT 0,
              estimated_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost REAL NOT NULL DEFAULT 0,
              error TEXT, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_llm_requests_time ON llm_requests(created_at, status);
            CREATE TABLE IF NOT EXISTS order_intents (
              intent_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              run_id TEXT, proposal_id TEXT, status TEXT NOT NULL, mt5_ticket INTEGER,
              payload TEXT NOT NULL, result TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS setup_labels (
              subject_type TEXT NOT NULL, subject_key TEXT PRIMARY KEY, label TEXT NOT NULL,
              rationale TEXT NOT NULL, label_rule TEXT NOT NULL, created_at TEXT NOT NULL,
              review_status TEXT NOT NULL DEFAULT 'AUTO'
            );
            CREATE INDEX IF NOT EXISTS idx_setup_labels_label ON setup_labels(label, created_at);
            CREATE TABLE IF NOT EXISTS execution_reviews (
              intent_key TEXT PRIMARY KEY, first_detected_at TEXT NOT NULL,
              last_detected_at TEXT NOT NULL, status TEXT NOT NULL,
              severity TEXT NOT NULL, detections INTEGER NOT NULL DEFAULT 1,
              reason TEXT NOT NULL, operator TEXT, resolution_note TEXT,
              resolved_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT, last_retry_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_execution_reviews_status
              ON execution_reviews(status, last_detected_at);
            CREATE TABLE IF NOT EXISTS memory_outbox (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
              source_id TEXT NOT NULL, payload TEXT NOT NULL, sent_at TEXT, error TEXT,
              UNIQUE(kind, source_id)
            );
            CREATE TABLE IF NOT EXISTS data_snapshots (
              snapshot_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, run_id TEXT,
              kind TEXT NOT NULL, symbol TEXT, timeframe TEXT, candle_id TEXT,
              source_hash TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_data_snapshots_lookup ON data_snapshots(symbol, timeframe, captured_at);
            CREATE TRIGGER IF NOT EXISTS data_snapshots_immutable_update
              BEFORE UPDATE ON data_snapshots BEGIN SELECT RAISE(ABORT, 'data snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS data_snapshots_immutable_delete
              BEFORE DELETE ON data_snapshots BEGIN SELECT RAISE(ABORT, 'data snapshots are immutable'); END;
            CREATE TABLE IF NOT EXISTS configuration_versions (
              version_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
              content_hash TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(kind, content_hash)
            );
            CREATE TRIGGER IF NOT EXISTS configuration_versions_immutable_update
              BEFORE UPDATE ON configuration_versions BEGIN SELECT RAISE(ABORT, 'configuration versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS configuration_versions_immutable_delete
              BEFORE DELETE ON configuration_versions BEGIN SELECT RAISE(ABORT, 'configuration versions are immutable'); END;
            CREATE TABLE IF NOT EXISTS raw_traces (
              trace_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT,
              trace_type TEXT NOT NULL, correlation_id TEXT, source_hash TEXT NOT NULL,
              redacted_hash TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_raw_traces_lookup ON raw_traces(trace_type, created_at);
            CREATE TRIGGER IF NOT EXISTS raw_traces_immutable_update
              BEFORE UPDATE ON raw_traces BEGIN SELECT RAISE(ABORT, 'raw traces are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS raw_traces_immutable_delete
              BEFORE DELETE ON raw_traces BEGIN SELECT RAISE(ABORT, 'raw traces are immutable'); END;
            CREATE TABLE IF NOT EXISTS working_memory (
              id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
              memory_key TEXT NOT NULL, kind TEXT NOT NULL, priority INTEGER NOT NULL,
              revision INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, expires_at TEXT, source_ids TEXT NOT NULL,
              payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
              UNIQUE(namespace, memory_key)
            );
            CREATE INDEX IF NOT EXISTS idx_working_memory_context
              ON working_memory(namespace, status, priority DESC, updated_at DESC);
            CREATE TABLE IF NOT EXISTS agent_schedules (
              schedule_name TEXT PRIMARY KEY, agent TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
              next_run_at TEXT NOT NULL, last_run_at TEXT, enabled INTEGER NOT NULL DEFAULT 1,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS clock_observations (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL,
              remote_time TEXT, offset_seconds REAL, max_offset_seconds REAL NOT NULL,
              status TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_clock_observations_time ON clock_observations(source, observed_at);
            CREATE TRIGGER IF NOT EXISTS clock_observations_immutable_update
              BEFORE UPDATE ON clock_observations BEGIN SELECT RAISE(ABORT, 'clock observations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS clock_observations_immutable_delete
              BEFORE DELETE ON clock_observations BEGIN SELECT RAISE(ABORT, 'clock observations are immutable'); END;
            """)
            task_columns = {row["name"] for row in con.execute("PRAGMA table_info(agent_tasks)").fetchall()}
            if "max_attempts" not in task_columns:
                con.execute("ALTER TABLE agent_tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")
            if "lease_expires_at" not in task_columns:
                con.execute("ALTER TABLE agent_tasks ADD COLUMN lease_expires_at TEXT")
            # Schedule leases (2026-09-27): the in-flight claim marker. Claim
            # no longer advances last_run_at/next_run_at - advancement moved
            # to dispatch completion (complete_schedule_dispatch) so a claimed
            # but abandoned schedule never loses its cadence turn.
            schedule_columns = {row["name"] for row in con.execute("PRAGMA table_info(agent_schedules)").fetchall()}
            if "claimed_at" not in schedule_columns:
                con.execute("ALTER TABLE agent_schedules ADD COLUMN claimed_at TEXT")
            if "claim_expires_at" not in schedule_columns:
                con.execute("ALTER TABLE agent_schedules ADD COLUMN claim_expires_at TEXT")
            llm_columns = {row["name"] for row in con.execute("PRAGMA table_info(llm_requests)").fetchall()}
            operational_columns = {row["name"] for row in con.execute("PRAGMA table_info(operational_events)").fetchall()}
            if "payload_hash" not in operational_columns:
                con.execute("ALTER TABLE operational_events ADD COLUMN payload_hash TEXT NOT NULL DEFAULT ''")
            review_columns = {row["name"] for row in con.execute("PRAGMA table_info(execution_reviews)").fetchall()}
            if "retry_count" not in review_columns:
                con.execute("ALTER TABLE execution_reviews ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
            if "next_retry_at" not in review_columns:
                con.execute("ALTER TABLE execution_reviews ADD COLUMN next_retry_at TEXT")
            if "last_retry_at" not in review_columns:
                con.execute("ALTER TABLE execution_reviews ADD COLUMN last_retry_at TEXT")
            if "estimated_tokens" not in llm_columns:
                con.execute("ALTER TABLE llm_requests ADD COLUMN estimated_tokens INTEGER NOT NULL DEFAULT 0")
            if "estimated_cost" not in llm_columns:
                con.execute("ALTER TABLE llm_requests ADD COLUMN estimated_cost REAL NOT NULL DEFAULT 0")
            if "attempt_count" not in llm_columns:
                con.execute("ALTER TABLE llm_requests ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
            if "parse_status" not in llm_columns:
                con.execute("ALTER TABLE llm_requests ADD COLUMN parse_status TEXT")
            if "response_chars" not in llm_columns:
                con.execute("ALTER TABLE llm_requests ADD COLUMN response_chars INTEGER NOT NULL DEFAULT 0")
            working_columns = {row["name"] for row in con.execute("PRAGMA table_info(working_memory)").fetchall()}
            if "revision" not in working_columns:
                con.execute("ALTER TABLE working_memory ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")

    def start_run(self, agent: str, config_hash: str = "", code_version: str = "", payload: Optional[Dict[str, Any]] = None) -> str:
        self.recover_stale_runs()
        run_id = str(uuid.uuid4())
        now = utcnow()
        with self._con() as con:
            con.execute("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?)", (run_id, agent, now, now, "RUNNING", config_hash, code_version, json.dumps(payload or {})))
        self.event("agent", "run_started", {"agent": agent}, run_id=run_id)
        return run_id

    def finish_run(self, run_id: str, status: str = "COMPLETED",
                   payload: Optional[Dict[str, Any]] = None) -> bool:
        """Close a worker run without leaving a false RUNNING record."""
        status = str(status).upper()
        if status not in {"COMPLETED", "FAILED", "STOPPED", "STALE_RECOVERED"}:
            raise ValueError(f"invalid run status: {status}")
        with self._con() as con:
            row = con.execute("SELECT status,payload FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return False
            prior = json.loads(row["payload"] or "{}")
            if payload:
                prior.update(payload)
            changed = con.execute(
                "UPDATE agent_runs SET heartbeat_at=?,status=?,payload=? WHERE run_id=? AND status='RUNNING'",
                (utcnow(), status, json.dumps(prior, default=str), run_id),
            ).rowcount
        if changed:
            self.event("agent", "run_finished", {"status": status}, run_id=run_id,
                       severity="ERROR" if status == "FAILED" else "INFO")
        return bool(changed)

    def recover_stale_runs(self, max_age_seconds: int = 300) -> int:
        """Mark abandoned workers stale before a new worker starts.

        A process crash cannot execute a finally block.  Recovery is therefore
        based on the durable heartbeat, and never treats a stale run as active.
        """
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        cutoff_iso = cutoff.isoformat()
        with self._con() as con:
            rows = con.execute(
                "SELECT run_id FROM agent_runs WHERE status='RUNNING' AND heartbeat_at < ?",
                (cutoff_iso,),
            ).fetchall()
            if rows:
                con.execute(
                    "UPDATE agent_runs SET status='STALE_RECOVERED',heartbeat_at=? "
                    "WHERE status='RUNNING' AND heartbeat_at < ?",
                    (utcnow(), cutoff_iso),
                )
        for row in rows:
            self.event("agent", "run_stale_recovered", {"reason": "heartbeat_timeout"},
                       run_id=row["run_id"], severity="WARNING")
        return len(rows)

    def heartbeat(self, run_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        with self._con() as con:
            con.execute("UPDATE agent_runs SET heartbeat_at=?, payload=? WHERE run_id=?", (utcnow(), json.dumps(payload or {}), run_id))

    def event(self, actor: str, event_type: str, payload: Dict[str, Any], *, run_id: str = "", correlation_id: str = "", severity: str = "INFO") -> None:
        safe_payload = self._redact(payload)
        encoded = self._stable_json(safe_payload)
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._con() as con:
            con.execute("INSERT INTO operational_events(created_at,run_id,actor,event_type,correlation_id,severity,payload,payload_hash) VALUES(?,?,?,?,?,?,?,?)", (utcnow(), run_id, actor, event_type, correlation_id, severity, encoded, payload_hash))
        # Optional local local document/KV agent memory (offline SQLite). Never blocks, never authorizes.
        try:
            from intelligence.local_agent_memory import maybe_remember_event
            maybe_remember_event(
                actor=actor,
                event_type=event_type,
                payload=safe_payload,
                run_id=run_id,
                correlation_id=correlation_id,
                severity=severity,
            )
        except Exception as exc:
            logger.debug("event emission failed: %s", exc)

    def agent_event(self, agent: str, action: str, artifact: str, event_type: str,
                    payload: Dict[str, Any], *, run_id: str = "", correlation_id: str = "",
                    severity: str = "INFO") -> None:
        """Journal a contract-checked agent action.

        Generic infrastructure events continue to use :meth:`event`; new
        strategy-agent writes should use this gateway so a proposer cannot be
        accidentally promoted into a risk or execution principal.
        """
        require_agent_action(agent, action)
        require_agent_write(agent, artifact)
        self.event(agent, event_type, payload, run_id=run_id, correlation_id=correlation_id, severity=severity)

    @staticmethod
    def _memory_timestamp(value: Any, name: str, *, allow_none: bool = True) -> Optional[str]:
        """Normalize a working-memory timestamp without inventing time."""
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be an ISO-8601 timestamp")
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from None
        if parsed.tzinfo is None:
            raise ValueError(f"{name} must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat()

    def put_working_memory(self, namespace: str, memory_key: str, payload: Any, *,
                           kind: str = "observation", priority: int = 50,
                           source_ids: Optional[list[str]] = None,
                           expires_at: Optional[str] = None,
                           run_id: str = "") -> Dict[str, Any]:
        """Persist restart-safe short-term context with bounded provenance.

        Working memory is operational context, not an order-permission store.
        It is redacted at write time, versioned in place, and can be compacted
        without deleting durable journal evidence.
        """
        for value, name in ((namespace, "namespace"), (memory_key, "memory_key"), (kind, "kind")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"working-memory {name} is required")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("working-memory priority must be an integer from 0 to 100")
        safe_sources = []
        for source in source_ids or []:
            if not isinstance(source, str) or not source.strip():
                raise ValueError("working-memory source IDs must be non-empty strings")
            safe_sources.append(source.strip()[:200])
        if len(safe_sources) > 100:
            raise ValueError("working-memory source IDs are limited to 100")
        normalized_expiry = self._memory_timestamp(expires_at, "expires_at")
        now = utcnow()
        safe_payload = self._redact(payload)
        encoded = self._stable_json(safe_payload)
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._con() as con:
            row = con.execute(
                "SELECT revision FROM working_memory WHERE namespace=? AND memory_key=?",
                (namespace.strip(), memory_key.strip()),
            ).fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            if row:
                con.execute(
                    """UPDATE working_memory SET kind=?,priority=?,revision=?,status='ACTIVE',
                       updated_at=?,expires_at=?,source_ids=?,payload=?,payload_hash=?
                       WHERE namespace=? AND memory_key=?""",
                    (kind.strip()[:80], priority, revision, now, normalized_expiry,
                     self._stable_json(safe_sources), encoded, payload_hash,
                     namespace.strip()[:120], memory_key.strip()[:200]),
                )
            else:
                con.execute(
                    """INSERT INTO working_memory(namespace,memory_key,kind,priority,revision,
                       status,created_at,updated_at,expires_at,source_ids,payload,payload_hash)
                       VALUES(?,?,?,?,?,'ACTIVE',?,?,?,?,?,?)""",
                    (namespace.strip()[:120], memory_key.strip()[:200], kind.strip()[:80], priority,
                     revision, now, now, normalized_expiry, self._stable_json(safe_sources),
                     encoded, payload_hash),
                )
        self.event("memory", "working_memory_updated", {
            "namespace": namespace.strip()[:120], "memory_key": memory_key.strip()[:200],
            "kind": kind.strip()[:80], "priority": priority, "revision": revision,
            "source_ids": safe_sources, "payload_hash": payload_hash,
        }, run_id=run_id)
        # Optional remote mirror: never blocks local authority, never authorizes.
        remote_mirror = {"mirrored": False, "reason": "not_attempted", "execution_authority": False}
        try:
            from intelligence.remote_memory_mirror import maybe_mirror_working_memory
            remote_mirror = maybe_mirror_working_memory({
                "namespace": namespace.strip()[:120],
                "memory_key": memory_key.strip()[:200],
                "kind": kind.strip()[:80],
                "priority": priority,
                "revision": revision,
                "payload": safe_payload,
                "source_ids": safe_sources,
                "payload_hash": payload_hash,
                "updated_at": now,
            })
        except Exception as exc:
            remote_mirror = {"mirrored": False, "mode": "ERROR", "error": str(exc)[:500],
                              "execution_authority": False}
        # Offline local local document/KV document store (no cloud). Fail-closed.
        local_memory = {"stored": False, "reason": "not_attempted", "execution_authority": False}
        try:
            from intelligence.local_agent_memory import maybe_remember_working_memory
            local_memory = maybe_remember_working_memory({
                "namespace": namespace.strip()[:120],
                "memory_key": memory_key.strip()[:200],
                "kind": kind.strip()[:80],
                "priority": priority,
                "revision": revision,
                "payload": safe_payload,
                "source_ids": safe_sources,
                "payload_hash": payload_hash,
                "updated_at": now,
            })
        except Exception as exc:
            local_memory = {"stored": False, "mode": "ERROR", "error": str(exc)[:500],
                            "execution_authority": False}
        # GAP-19: auto-compact after write so AgentOperations cannot grow unbounded.
        compaction = {"compacted": False, "reason": "under_limit"}
        try:
            max_active = int(os.environ.get("TRADING_WORKING_MEMORY_MAX_ACTIVE", "50") or 50)
            max_chars = int(os.environ.get("TRADING_WORKING_MEMORY_MAX_CHARS", "20000") or 20000)
            if max_active > 0:
                with self._con() as con:
                    active_count = int(con.execute(
                        "SELECT COUNT(*) AS n FROM working_memory WHERE namespace=? AND status='ACTIVE'",
                        (namespace.strip()[:120],),
                    ).fetchone()["n"])
                if active_count > max_active and not self._compacting:
                    self._compacting = True
                    try:
                        compaction = self.compact_working_memory(
                            namespace.strip()[:120],
                            max_entries=max_active,
                            max_total_chars=max(1000, max_chars),
                            run_id=run_id,
                        )
                        compaction = {**compaction, "auto": True}
                    finally:
                        self._compacting = False
                else:
                    compaction = {"compacted": False, "auto": True, "reason": "under_limit",
                                  "active_count": active_count, "max_active": max_active}
        except Exception as exc:
            compaction = {"compacted": False, "auto": True, "error": str(exc)[:300]}
        return {"namespace": namespace.strip()[:120], "memory_key": memory_key.strip()[:200],
                "kind": kind.strip()[:80], "priority": priority, "revision": revision,
                "payload_hash": payload_hash, "remote_mirror": remote_mirror,
                "local_memory": local_memory, "compaction": compaction,
                "execution_authority": False}

    def working_memory_context(self, namespace: str, *, as_of: Optional[str] = None,
                               limit: int = 40, max_chars: int = 12000,
                               include_namespaces: Optional[list[str]] = None) -> Dict[str, Any]:
        """Return bounded active context with explicit point-in-time filtering."""
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("working-memory namespace is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("working-memory limit must be a positive integer")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("working-memory max_chars must be a positive integer")
        namespaces = [namespace.strip()]
        for extra in include_namespaces or []:
            if not isinstance(extra, str) or not extra.strip():
                raise ValueError("working-memory included namespaces must be non-empty strings")
            if extra.strip() not in namespaces:
                namespaces.append(extra.strip())
        cutoff = self._memory_timestamp(as_of, "as_of") or utcnow()
        placeholders = ",".join("?" for _ in namespaces)
        with self._con() as con:
            rows = con.execute(
                """SELECT * FROM working_memory
                   WHERE namespace IN (""" + placeholders + """) AND status='ACTIVE' AND updated_at<=?
                     AND (expires_at IS NULL OR expires_at>?)
                   ORDER BY priority DESC,updated_at DESC,id DESC LIMIT ?""",
                (*namespaces, cutoff, cutoff, limit),
            ).fetchall()
        items = []
        used = 0
        for row in rows:
            payload = self._decode_json(row["payload"])
            source_ids = self._decode_json(row["source_ids"])
            item = {"namespace": row["namespace"], "memory_key": row["memory_key"], "kind": row["kind"],
                    "priority": int(row["priority"]), "revision": int(row["revision"]),
                    "updated_at": row["updated_at"], "expires_at": row["expires_at"],
                    "source_ids": source_ids if isinstance(source_ids, list) else [],
                    "payload": payload, "payload_hash": row["payload_hash"]}
            encoded = self._stable_json(item)
            if items and used + len(encoded) > max_chars:
                continue
            if not items and len(encoded) > max_chars:
                item["payload"] = {"truncated": True, "payload_hash": row["payload_hash"]}
                encoded = self._stable_json(item)
            used += len(encoded)
            items.append(item)
        return {"namespace": namespace.strip(), "included_namespaces": namespaces, "as_of": cutoff, "items": items,
                "count": len(items), "prompt_chars": used, "execution_authority": False}

    def compact_working_memory(self, namespace: str, max_entries: int = 50, max_total_chars: int = 20000, *, max_active: Optional[int] = None, run_id: str = "") -> Dict[str, Any]:
        """Compact low-value short-term entries while retaining safety state."""
        if max_active is not None:
            max_entries = max_active
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("working-memory namespace is required")
        namespace = namespace.strip()
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM working_memory WHERE namespace=? AND status='ACTIVE' ORDER BY priority DESC,updated_at DESC,id DESC",
                (namespace,),
            ).fetchall()

        safety_retained = 0
        non_safety_rows = []
        selected_ids = set()

        for row in rows:
            if str(row["kind"]).lower() in self.SAFETY_KINDS:
                selected_ids.add(int(row["id"]))
                safety_retained += 1
            else:
                non_safety_rows.append(row)

        retained_count = safety_retained
        total_chars = sum(len(row["payload"]) for row in rows if int(row["id"]) in selected_ids)
        
        for row in non_safety_rows:
            row_chars = len(row["payload"])
            if retained_count < max_entries and (total_chars + row_chars <= max_total_chars):
                selected_ids.add(int(row["id"]))
                retained_count += 1
                total_chars += row_chars

        compacted = [row for row in rows if int(row["id"]) not in selected_ids]
        compacted_count = len(compacted)

        if compacted:
            now = utcnow()
            with self._con() as con:
                con.executemany("UPDATE working_memory SET status='COMPACTED',updated_at=? WHERE id=?",
                                [(now, int(row["id"])) for row in compacted])
            self.put_working_memory(
                namespace,
                f"compaction:{uuid.uuid4().hex[:8]}",
                {"compacted_count": compacted_count, "retained_count": retained_count, "safety_retained": safety_retained},
                kind="COMPACTION",
                priority=50,
                run_id=run_id,
            )

        return {
            "compacted_count": compacted_count,
            "retained_count": retained_count,
            "safety_retained": safety_retained,
            "namespace": namespace,
            "active": len(rows),
            "compacted": compacted_count,
            "retained": retained_count,
            "execution_authority": False
        }

    def working_memory_prompt(self, namespace: str, max_chars: int = 15000) -> Dict[str, Any]:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("working-memory namespace is required")
        namespace = namespace.strip()
        
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM working_memory WHERE namespace=? AND status='ACTIVE' ORDER BY priority DESC,updated_at DESC,id DESC",
                (namespace,),
            ).fetchall()
            
        entries = []
        total_chars = 0
        truncated_count = 0
        
        for row in rows:
            entry = {
                "key": row["memory_key"],
                "kind": row["kind"],
                "priority": int(row["priority"]),
                "payload": row["payload"],
                "source_ids": row["source_ids"]
            }
            
            entry_chars = len(self._stable_json(entry))
            
            if total_chars + entry_chars > max_chars:
                truncated_count += 1
            else:
                entries.append(entry)
                total_chars += entry_chars
                
        return {
            "entries": entries,
            "total_chars": total_chars,
            "truncated": truncated_count > 0,
            "truncated_count": truncated_count
        }

    def compaction_summary(self, namespace: str) -> Dict[str, Any]:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("working-memory namespace is required")
        namespace = namespace.strip()
        
        with self._con() as con:
            rows = con.execute(
                "SELECT status, kind, updated_at FROM working_memory WHERE namespace=?",
                (namespace,)
            ).fetchall()
            
        active = 0
        compacted = 0
        safety_retained = 0
        active_times = []
        
        for row in rows:
            if row["status"] == "ACTIVE":
                active += 1
                active_times.append(row["updated_at"])
                if str(row["kind"]).lower() in self.SAFETY_KINDS:
                    safety_retained += 1
            elif row["status"] == "COMPACTED":
                compacted += 1
                
        active_times.sort()
        oldest_active = active_times[0] if active_times else None
        newest_active = active_times[-1] if active_times else None
        
        return {
            "active": active,
            "compacted": compacted,
            "safety_retained": safety_retained,
            "oldest_active": oldest_active,
            "newest_active": newest_active
        }

    def working_memory_status(self, namespace: str = "") -> Dict[str, Any]:
        params = (namespace.strip(),) if namespace else ()
        where = " WHERE namespace=?" if namespace else ""
        with self._con() as con:
            rows = con.execute(
                f"SELECT namespace,status,COUNT(*) AS count FROM working_memory{where} GROUP BY namespace,status",
                params,
            ).fetchall()
        return {"namespace": namespace.strip() if namespace else None,
                "counts": [dict(row) for row in rows], "execution_authority": False}

    @staticmethod
    def _stable_json(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _redact(cls, value: Any, key: str = "") -> Any:
        """Keep L0 evidence useful without ever persisting credentials/IDs."""
        sensitive = ("password", "secret", "api_key", "token", "authorization", "cookie", "credential", "login", "account_id", "account_number")
        if any(marker in key.lower() for marker in sensitive):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(item_key): cls._redact(item_value, str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [cls._redact(item, key) for item in value]
        if isinstance(value, str):
            redacted = value
            for pattern, replacement in cls._INLINE_SECRET_PATTERNS:
                redacted = pattern.sub(replacement, redacted)
            return redacted
        return value

    def record_raw_trace(self, trace_type: str, payload: Dict[str, Any], *, run_id: str = "",
                         correlation_id: str = "") -> str:
        """Append an immutable, redacted L0 trace and return its evidence ID."""
        source_encoded = self._stable_json(payload)
        redacted = self._redact(payload)
        redacted_encoded = self._stable_json(redacted)
        source_hash = hashlib.sha256(source_encoded.encode("utf-8")).hexdigest()
        redacted_hash = hashlib.sha256(redacted_encoded.encode("utf-8")).hexdigest()
        trace_id = f"{trace_type}:{source_hash[:24]}"
        with self._con() as con:
            con.execute(
                "INSERT OR IGNORE INTO raw_traces(trace_id,created_at,run_id,trace_type,correlation_id,source_hash,redacted_hash,payload) VALUES(?,?,?,?,?,?,?,?)",
                (trace_id, utcnow(), run_id, trace_type, correlation_id, source_hash, redacted_hash, redacted_encoded),
            )
        return trace_id

    def store_snapshot(self, kind: str, payload: Dict[str, Any], *, run_id: str = "", symbol: str = "", timeframe: str = "", candle_id: str = "") -> str:
        """Persist the exact point-in-time input used by a decision."""
        safe_payload = self._redact(payload)
        encoded = self._stable_json(safe_payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        snapshot_id = f"{kind}:{digest}"
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO data_snapshots(snapshot_id,captured_at,run_id,kind,symbol,timeframe,candle_id,source_hash,payload) VALUES(?,?,?,?,?,?,?,?,?)", (snapshot_id, utcnow(), run_id, kind, symbol, timeframe, candle_id, digest, encoded))
        try:
            from intelligence.local_agent_memory import maybe_remember_journal_snapshot
            maybe_remember_journal_snapshot(
                kind=kind,
                payload=safe_payload,
                snapshot_id=snapshot_id,
                run_id=run_id,
                symbol=symbol,
                timeframe=timeframe,
                candle_id=candle_id,
            )
        except Exception as exc:
            logger.debug("memory operation failed: %s", exc)
        return snapshot_id

    def record_clock_observation(self, source: str, remote_epoch_seconds: Any, *, max_offset_seconds: float = 5.0,
                                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Persist local-versus-provider time evidence; no remote time is invented."""
        if not isinstance(source, str) or not source.strip():
            raise ValueError("clock observation source is required")
        try:
            max_offset_seconds = float(max_offset_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("max_offset_seconds must be finite and non-negative") from None
        if not math.isfinite(max_offset_seconds) or max_offset_seconds < 0:
            raise ValueError("max_offset_seconds must be finite and non-negative")
        now = datetime.now(timezone.utc)
        try:
            remote = datetime.fromtimestamp(float(remote_epoch_seconds), tz=timezone.utc)
            offset = (now - remote).total_seconds()
            status = "OK" if abs(offset) <= max_offset_seconds else "STALE_OR_SKEWED"
            remote_text = remote.isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            offset, status, remote_text = None, "MISSING_OR_MALFORMED", None
        result = {"source": source, "observed_at": now.isoformat(), "remote_time": remote_text,
                  "offset_seconds": offset, "max_offset_seconds": max_offset_seconds, "status": status,
                  "valid": status == "OK"}
        with self._con() as con:
            con.execute("INSERT INTO clock_observations(observed_at,source,remote_time,offset_seconds,max_offset_seconds,status,payload) VALUES(?,?,?,?,?,?,?)",
                        (now.isoformat(), source[:100], remote_text, offset, max_offset_seconds, status,
                         json.dumps(payload or {}, default=str)))
        return result

    def latest_clock_observation(self, source: str = "") -> Optional[Dict[str, Any]]:
        with self._con() as con:
            row = con.execute("SELECT * FROM clock_observations WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone() if source else con.execute("SELECT * FROM clock_observations ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def version_configuration(self, kind: str, payload: Dict[str, Any], status: str = "ACTIVE") -> str:
        encoded = self._stable_json(payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        version_id = f"{kind}:{digest[:16]}"
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO configuration_versions(version_id,created_at,kind,content_hash,status,payload) VALUES(?,?,?,?,?,?)", (version_id, utcnow(), kind, digest, status, encoded))
        return version_id

    def claim_task(self, key: str, agent: str, task_type: str, payload: Dict[str, Any], run_id: str = "",
                   *, lease_seconds: int = 900, max_attempts: int = 3) -> bool:
        """Claim once, or safely reclaim an expired/failed task within its retry cap."""
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        now_dt = datetime.now(timezone.utc)
        now, lease = now_dt.isoformat(), (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        claimed, event_type = False, "task_deduplicated"
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status,attempts,max_attempts,lease_expires_at FROM agent_tasks WHERE idempotency_key=?", (key,)).fetchone()
            if row is None:
                con.execute("INSERT INTO agent_tasks(idempotency_key,created_at,updated_at,run_id,agent,task_type,status,attempts,max_attempts,lease_expires_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (key, now, now, run_id, agent, task_type, "RUNNING", 1, max_attempts, lease, json.dumps(payload, default=str)))
                claimed, event_type = True, "task_claimed"
            else:
                expired = not row["lease_expires_at"] or str(row["lease_expires_at"]) <= now
                retryable = row["status"] == "ERROR" or (row["status"] == "RUNNING" and expired)
                if retryable and int(row["attempts"]) < int(row["max_attempts"]):
                    con.execute("UPDATE agent_tasks SET updated_at=?,run_id=?,agent=?,task_type=?,status='RUNNING',attempts=attempts+1,lease_expires_at=?,payload=?,error=NULL WHERE idempotency_key=?",
                                (now, run_id, agent, task_type, lease, json.dumps(payload, default=str), key))
                    claimed, event_type = True, "task_reclaimed"
                else:
                    con.execute("UPDATE agent_tasks SET updated_at=? WHERE idempotency_key=?", (now, key))
        self.event(agent, event_type, {"task_type": task_type, "lease_seconds": lease_seconds, "max_attempts": max_attempts}, run_id=run_id, correlation_id=key,
                   severity="WARNING" if event_type == "task_reclaimed" else "INFO")
        return claimed

    def heartbeat_task(self, key: str, *, lease_seconds: int = 900) -> bool:
        """Extend the lease only for a task still owned by a running worker."""
        now = datetime.now(timezone.utc)
        with self._con() as con:
            changed = con.execute("UPDATE agent_tasks SET updated_at=?,lease_expires_at=? WHERE idempotency_key=? AND status='RUNNING'",
                                  (now.isoformat(), (now + timedelta(seconds=lease_seconds)).isoformat(), key)).rowcount
        return bool(changed)

    def register_schedule(self, schedule_name: str, agent: str, interval_seconds: int,
                          payload: Optional[Dict[str, Any]] = None) -> None:
        if not isinstance(schedule_name, str) or not schedule_name.strip():
            raise ValueError("schedule_name is required")
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent is required")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO agent_schedules(schedule_name,agent,interval_seconds,next_run_at,payload) VALUES(?,?,?,?,?)",
                        (schedule_name.strip()[:160], agent.strip()[:120], interval_seconds, utcnow(), json.dumps(payload or {}, default=str)))

    def list_schedules(self) -> list[Dict[str, Any]]:
        """Read-only schedule registry snapshot (name, interval, enabled).

        Used by the orchestrator's session-skip watchdog to derive per-schedule
        cadence thresholds without a second source of truth.
        """
        with self._con() as con:
            rows = con.execute(
                "SELECT schedule_name, interval_seconds, enabled FROM agent_schedules"
            ).fetchall()
        return [{"schedule_name": str(r["schedule_name"]),
                 "interval_seconds": int(r["interval_seconds"] or 0),
                 "enabled": int(r["enabled"] or 0)} for r in rows]

    def claim_due_schedules(self, limit: int = 20,
                            lease_seconds: int = 900) -> list[Dict[str, Any]]:
        """Atomically mark due schedules as claimed WITHOUT advancing cadence.

        The claim stamps ``claimed_at``/``claim_expires_at`` (a lease) and
        leaves ``last_run_at``/``next_run_at`` untouched: a schedule claimed
        but never dispatched keeps its cadence turn. Advancement happens in
        ``complete_schedule_dispatch`` on the dispatch thread. Exclusivity
        comes from the lease: a row with an unexpired claim is not claimable,
        and a claim whose lease expired (crashed dispatch) is re-claimable.

        Malformed timestamps are the one claim-time cadence write: a corrupt
        due point cannot drive completion math, so the row is moved to a
        known future point at recovery and exposed via schedule_state for
        operator review.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("schedule claim limit must be a positive integer")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("schedule claim lease must be a positive integer")
        now = datetime.now(timezone.utc)
        due: list[Dict[str, Any]] = []
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """SELECT * FROM agent_schedules
                   WHERE enabled=1 AND next_run_at<=?
                     AND (claim_expires_at IS NULL OR claim_expires_at<=?)
                   ORDER BY next_run_at LIMIT ?""",
                (now.isoformat(), now.isoformat(), limit)).fetchall()
            for row in rows:
                interval = int(row["interval_seconds"])
                try:
                    scheduled_at = datetime.fromisoformat(str(row["next_run_at"]).replace("Z", "+00:00"))
                    if scheduled_at.tzinfo is None:
                        raise ValueError("schedule timestamp has no timezone")
                    scheduled_at = scheduled_at.astimezone(timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    # A corrupt schedule must not crash the pulse loop.  Move
                    # it to a known future point and expose the recovery in
                    # the returned item for operator review. This is the one
                    # claim-time cadence write (see docstring).
                    scheduled_at = now
                    malformed = True
                else:
                    malformed = False
                elapsed = max(0.0, (now - scheduled_at).total_seconds())
                missed_runs = int(elapsed // interval) if interval else 0
                next_run = scheduled_at + timedelta(seconds=(missed_runs + 1) * interval)
                if malformed:
                    con.execute(
                        "UPDATE agent_schedules SET next_run_at=?,claimed_at=?,claim_expires_at=? WHERE schedule_name=?",
                        (next_run.isoformat(), now.isoformat(),
                         (now + timedelta(seconds=lease_seconds)).isoformat(),
                         row["schedule_name"]))
                else:
                    con.execute(
                        "UPDATE agent_schedules SET claimed_at=?,claim_expires_at=? WHERE schedule_name=?",
                        (now.isoformat(), (now + timedelta(seconds=lease_seconds)).isoformat(),
                         row["schedule_name"]))
                item = dict(row)
                item["payload"] = json.loads(item["payload"] or "{}")
                item["scheduled_for"] = scheduled_at.isoformat()
                item["claimed_at"] = now.isoformat()
                item["claim_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
                item["missed_runs"] = missed_runs
                item["schedule_state"] = "MALFORMED_RECOVERED" if malformed else "ON_CADENCE"
                due.append(item)
        return due

    def complete_schedule_dispatch(self, schedule_name: str, claimed_at: str,
                                   status: str = "COMPLETED") -> bool:
        """Advance cadence for one dispatched schedule (or release its claim).

        ``status='COMPLETED'`` re-stamps ``last_run_at`` to the completion
        time and advances ``next_run_at`` from the row's stored due point
        (the same catch-up math the claim used to do: skipped intervals
        collapse into the next due point). ``status='SKIPPED'`` only clears
        the claim, so the dispatch's cadence turn survives an abandoned
        dispatch. Both are guarded by ``claimed_at`` (the claim token), so a
        completion from a superseded claim can never touch the row.
        """
        if status not in ("COMPLETED", "SKIPPED"):
            raise ValueError("schedule dispatch status must be COMPLETED or SKIPPED")
        name = str(schedule_name or "").strip()
        token = str(claimed_at or "").strip()
        if not name or not token:
            return False
        now = datetime.now(timezone.utc)
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT interval_seconds, next_run_at FROM agent_schedules WHERE schedule_name=? AND claimed_at=?",
                (name, token)).fetchone()
            if row is None:
                return False
            if status == "SKIPPED":
                con.execute(
                    "UPDATE agent_schedules SET claimed_at=NULL,claim_expires_at=NULL WHERE schedule_name=? AND claimed_at=?",
                    (name, token))
                return True
            interval = int(row["interval_seconds"])
            try:
                due_at = datetime.fromisoformat(str(row["next_run_at"]).replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    raise ValueError("schedule timestamp has no timezone")
                due_at = due_at.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                # Due point corrupted between claim and completion: fall
                # back to one interval from now so the cadence stays sane.
                next_run = now + timedelta(seconds=interval)
            else:
                elapsed = max(0.0, (now - due_at).total_seconds())
                missed_runs = int(elapsed // interval) if interval else 0
                next_run = due_at + timedelta(seconds=(missed_runs + 1) * interval)
            con.execute(
                "UPDATE agent_schedules SET last_run_at=?,next_run_at=?,claimed_at=NULL,claim_expires_at=NULL WHERE schedule_name=? AND claimed_at=?",
                (now.isoformat(), next_run.isoformat(), name, token))
        return True

    def release_schedule_claims(self, items: Iterable[Dict[str, Any]]) -> int:
        """Clear claims for claimed-but-never-dispatched schedules.

        Cadence was never consumed (claims no longer advance next_run_at),
        so releasing just clears the lease: the next wave re-claims the row
        at its original due point. Guarded by ``claimed_at`` per item, so a
        stale release cannot clear a newer claim.
        """
        released = 0
        with self._con() as con:
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("schedule_name") or "").strip()
                token = str(item.get("claimed_at") or "").strip()
                if not name or not token:
                    continue
                cursor = con.execute(
                    "UPDATE agent_schedules SET claimed_at=NULL,claim_expires_at=NULL WHERE schedule_name=? AND claimed_at=?",
                    (name, token))
                released += int(cursor.rowcount)
        return released

    def complete_task(self, key: str, result: Optional[Dict[str, Any]] = None) -> None:
        with self._con() as con:
            con.execute("UPDATE agent_tasks SET status='COMPLETED',updated_at=?,lease_expires_at=NULL,result=?,error=NULL WHERE idempotency_key=?", (utcnow(), json.dumps(result or {}, default=str), key))

    def link_tasks(self, parent_key: str, child_key: str, relation: str = "DEPENDS_ON") -> None:
        """Persist a restart-visible execution dependency between two task keys."""
        if not parent_key or not child_key or parent_key == child_key:
            raise ValueError("task edge requires two distinct task keys")
        with self._con() as con:
            parents = con.execute("SELECT idempotency_key FROM agent_tasks WHERE idempotency_key IN (?,?)", (parent_key, child_key)).fetchall()
            if len(parents) != 2:
                raise ValueError("both linked tasks must be claimed before linking")
            con.execute("INSERT OR IGNORE INTO agent_task_edges(parent_key,child_key,relation,created_at) VALUES(?,?,?,?)",
                        (parent_key, child_key, relation[:80], utcnow()))

    def task_graph(self, key: str) -> Dict[str, Any]:
        """Return a local, replayable view of a task and its durable edges."""
        with self._con() as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE idempotency_key=?", (key,)).fetchone()
            if not task:
                raise ValueError("unknown task")
            edges = con.execute("SELECT * FROM agent_task_edges WHERE parent_key=? OR child_key=? ORDER BY created_at", (key, key)).fetchall()
        item = dict(task)
        item["payload"] = json.loads(item["payload"] or "{}")
        item["result"] = json.loads(item["result"] or "{}")
        item["edges"] = [dict(edge) for edge in edges]
        return item

    def fail_task(self, key: str, error: str) -> None:
        with self._con() as con:
            con.execute("UPDATE agent_tasks SET status='ERROR',updated_at=?,lease_expires_at=NULL,error=? WHERE idempotency_key=?", (utcnow(), error[:2000], key))

    def release_task(self, key: str, reason: str = "released") -> None:
        """Release a claimed task lease so it can be re-evaluated cleanly when eligible.

        Removes the running task row, allowing a subsequent cycle (e.g. after a
        holiday or transient data gap) to claim and analyze the candle freshly.
        """
        with self._con() as con:
            con.execute("DELETE FROM agent_tasks WHERE idempotency_key=? AND status='RUNNING'", (key,))
        self.event("orchestrator", "task_released", {"reason": reason}, correlation_id=key, severity="INFO")

    def recover_stale_tasks(self, older_than_seconds: int = 900) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        with self._con() as con:
            rows = con.execute("SELECT idempotency_key FROM agent_tasks WHERE status='RUNNING' AND (lease_expires_at < ? OR (lease_expires_at IS NULL AND updated_at < ?))", (utcnow(), cutoff)).fetchall()
            con.execute("UPDATE agent_tasks SET status='ERROR',updated_at=?,lease_expires_at=NULL,error='task lease expired; retry or operator review required' WHERE status='RUNNING' AND (lease_expires_at < ? OR (lease_expires_at IS NULL AND updated_at < ?))", (utcnow(), utcnow(), cutoff))
        for row in rows:
            self.event("recovery", "task_marked_stale", {}, correlation_id=row["idempotency_key"], severity="WARNING")
        return len(rows)

    def queue_health(self) -> Dict[str, int]:
        now = utcnow()
        with self._con() as con:
            return {
                "running": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='RUNNING'").fetchone()[0],
                "lease_expired": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='RUNNING' AND lease_expires_at < ?", (now,)).fetchone()[0],
                "retryable_errors": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='ERROR' AND attempts < max_attempts").fetchone()[0],
                "exhausted_errors": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='ERROR' AND attempts >= max_attempts").fetchone()[0],
            }

    def backpressure_status(self, *, max_running: int = 100, max_retryable_errors: int = 100) -> Dict[str, Any]:
        """Expose deterministic queue thresholds for an operator/worker gate.

        This is deliberately advisory telemetry: callers may choose to stop
        claiming new work, while existing leases and reconciliation remain
        visible.  Thresholds are returned with the observation so alerts and
        postmortems are reproducible rather than relying on process-local
        configuration.
        """
        if max_running <= 0 or max_retryable_errors <= 0:
            raise ValueError("backpressure thresholds must be positive")
        counts = self.queue_health()
        running = int(counts.get("running", 0))
        retryable = int(counts.get("retryable_errors", 0))
        return {
            **counts,
            "thresholds": {"max_running": int(max_running), "max_retryable_errors": int(max_retryable_errors)},
            "running_pressure": running >= int(max_running),
            "retry_pressure": retryable >= int(max_retryable_errors),
            "backpressure": running >= int(max_running) or retryable >= int(max_retryable_errors),
            "status": "BACKPRESSURE" if running >= int(max_running) or retryable >= int(max_retryable_errors) else "HEALTHY",
            "execution_authority": False,
        }

    def recover_stale_llm_reservations(self, max_age_seconds: int = LLM_RESERVATION_MAX_AGE_SECONDS,
                                       *, now: Optional[datetime] = None) -> int:
        """Expire abandoned LLM leases without erasing usage evidence.

        ``RESERVED`` is counted as an attempted request for rate/cost
        accounting, but it must not consume a concurrency slot forever after a
        process crash.  Only old reservations are changed; successful or
        explicitly failed requests are immutable from this recovery path.
        """
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be a positive integer")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must include a timezone")
        cutoff = (current.astimezone(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        completed_at = current.astimezone(timezone.utc).isoformat()
        with self._con() as con:
            rows = con.execute(
                "SELECT request_id FROM llm_requests WHERE status='RESERVED' AND created_at < ?",
                (cutoff,),
            ).fetchall()
            if rows:
                con.execute(
                    """UPDATE llm_requests
                       SET completed_at=?, status='EXPIRED', error=?
                       WHERE status='RESERVED' AND created_at < ?""",
                    (completed_at, "reservation lease expired after worker interruption", cutoff),
                )
        if rows:
            self.event("llm", "reservations_expired", {
                "count": len(rows), "max_age_seconds": max_age_seconds,
                "request_ids": [str(row["request_id"]) for row in rows[:100]],
            }, severity="WARNING")
        return len(rows)

    def reserve_llm(self, *, strategy: str, provider: str, model: str, trigger_key: str, run_id: str = "",
                    max_per_hour: int = 30, max_per_day: int = 200, max_concurrent: int = 1,
                    max_tokens_per_hour: int = 120_000, max_cost_per_day: float = 10.0,
                    estimated_tokens: int = 0, estimated_cost: float = 0.0) -> Tuple[bool, str]:
        """Reserve against global and provider/model-specific local budgets."""
        if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                   for value in (max_per_hour, max_per_day, max_concurrent, max_tokens_per_hour)):
            raise ValueError("LLM budget limits must be positive (cost may be zero)")
        max_cost_per_day = self._finite_nonnegative(max_cost_per_day, "max_cost_per_day")
        estimated_tokens = self._nonnegative_integer(estimated_tokens, "estimated_tokens")
        estimated_cost = self._finite_nonnegative(estimated_cost, "estimated_cost")
        for name, value in (("strategy", strategy), ("provider", provider), ("model", model), ("trigger_key", trigger_key)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"LLM {name} is required")
        # Recover interrupted leases before enforcing concurrency.  This is a
        # local state transition only; it never retries or sends a provider
        # request.
        self.recover_stale_llm_reservations()
        now = datetime.now(timezone.utc)
        hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._con() as con:
            if con.execute("SELECT 1 FROM llm_requests WHERE trigger_key=? AND status IN ('RESERVED','SUCCESS')", (trigger_key,)).fetchone():
                return False, "duplicate trigger"
            per_hour = con.execute("SELECT COUNT(*) FROM llm_requests WHERE created_at >= ?", (hour,)).fetchone()[0]
            per_day = con.execute("SELECT COUNT(*) FROM llm_requests WHERE created_at >= ?", (day,)).fetchone()[0]
            provider_hour = con.execute("SELECT COUNT(*) FROM llm_requests WHERE provider=? AND model=? AND created_at >= ?", (provider, model, hour)).fetchone()[0]
            concurrent = con.execute("SELECT COUNT(*) FROM llm_requests WHERE provider=? AND model=? AND status='RESERVED'", (provider, model)).fetchone()[0]
            token_used = con.execute("SELECT COALESCE(SUM(CASE WHEN status='SUCCESS' THEN COALESCE(tokens,0) ELSE COALESCE(estimated_tokens,0) END),0) FROM llm_requests WHERE provider=? AND model=? AND created_at >= ?", (provider, model, hour)).fetchone()[0]
            cost_used = con.execute("SELECT COALESCE(SUM(CASE WHEN status='SUCCESS' THEN COALESCE(cost,0) ELSE COALESCE(estimated_cost,0) END),0) FROM llm_requests WHERE provider=? AND model=? AND created_at >= ?", (provider, model, day)).fetchone()[0]
            if per_hour >= max_per_hour:
                return False, "hourly LLM budget exhausted"
            if per_day >= max_per_day:
                return False, "daily LLM budget exhausted"
            if provider_hour >= max_per_hour:
                return False, "provider/model hourly LLM budget exhausted"
            if concurrent >= max_concurrent:
                return False, "provider/model concurrency budget exhausted"
            if int(token_used) + estimated_tokens > max_tokens_per_hour:
                return False, "provider/model hourly token budget exhausted"
            if float(cost_used) + estimated_cost > max_cost_per_day:
                return False, "provider/model daily cost budget exhausted"
            request_id = str(uuid.uuid4())
            con.execute("INSERT INTO llm_requests(request_id,created_at,run_id,strategy,provider,model,trigger_key,status,estimated_tokens,estimated_cost,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (request_id, utcnow(), run_id, strategy, provider, model, trigger_key, "RESERVED", estimated_tokens, estimated_cost, self._stable_json({"budget": {"max_concurrent": max_concurrent, "max_tokens_per_hour": max_tokens_per_hour, "max_cost_per_day": max_cost_per_day}})))
        self.event("llm", "request_reserved", {"strategy": strategy, "provider": provider, "model": model, "estimated_tokens": estimated_tokens, "estimated_cost": estimated_cost}, run_id=run_id, correlation_id=request_id)
        return True, request_id

    def finish_llm(self, request_id: str, *, success: bool, latency_ms: float = 0,
                   prompt_hash: str = "", response_hash: str = "", tokens: int = 0,
                   cost: float = 0, error: str = "", attempt_count: int = 0,
                   parse_status: str = "", response_chars: int = 0) -> None:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("LLM request ID is required")
        if not isinstance(success, bool):
            raise ValueError("LLM success status must be boolean")
        latency_ms = self._finite_nonnegative(latency_ms, "latency_ms", allow_none=True)
        tokens = self._nonnegative_integer(tokens, "tokens", allow_none=True)
        cost = self._finite_nonnegative(cost, "cost", allow_none=True)
        attempt_count = self._nonnegative_integer(attempt_count, "attempt_count", allow_none=True)
        response_chars = self._nonnegative_integer(response_chars, "response_chars", allow_none=True)
        status = "SUCCESS" if success else "ERROR"
        with self._con() as con:
            con.execute("""UPDATE llm_requests
                SET completed_at=?,status=?,latency_ms=?,prompt_hash=?,response_hash=?,
                    tokens=?,cost=?,error=?,attempt_count=?,parse_status=?,response_chars=?
                WHERE request_id=?""", (utcnow(), status, latency_ms, prompt_hash,
                response_hash, tokens, cost, str(error or "")[:2000], attempt_count,
                str(parse_status or "")[:100] or None, response_chars, request_id))

    def llm_budget_usage(self, provider: str, model: str) -> Dict[str, Any]:
        self.recover_stale_llm_reservations()
        now = datetime.now(timezone.utc)
        hour, day = now.replace(minute=0, second=0, microsecond=0).isoformat(), now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._con() as con:
            row = con.execute("""SELECT COUNT(*) hourly_requests,
                SUM(CASE WHEN status='RESERVED' THEN 1 ELSE 0 END) concurrent_reserved,
                COALESCE(SUM(CASE WHEN status='SUCCESS' THEN COALESCE(tokens,0) ELSE COALESCE(estimated_tokens,0) END),0) hourly_tokens
                FROM llm_requests WHERE provider=? AND model=? AND created_at >= ?""", (provider, model, hour)).fetchone()
            daily_cost = con.execute("SELECT COALESCE(SUM(CASE WHEN status='SUCCESS' THEN COALESCE(cost,0) ELSE COALESCE(estimated_cost,0) END),0) FROM llm_requests WHERE provider=? AND model=? AND created_at >= ?", (provider, model, day)).fetchone()[0]
        return {"provider": provider, "model": model, **dict(row), "daily_cost": daily_cost}

    def create_order_intent(self, intent_key: str, payload: Dict[str, Any], run_id: str = "",
                            proposal_id: str = "", actor: str = "RiskController") -> Tuple[bool, Dict[str, Any]]:
        """Create an execution intent only for the deterministic risk principal."""
        require_order_authority(actor)
        require_agent_action(actor, "submit_validated_intent")
        require_agent_write(actor, "order_intent")
        now = utcnow()
        with self._con() as con:
            inserted = con.execute("INSERT OR IGNORE INTO order_intents(intent_key,created_at,updated_at,run_id,proposal_id,status,payload) VALUES(?,?,?,?,?,?,?)", (intent_key, now, now, run_id, proposal_id, "CREATED", json.dumps(payload, default=str))).rowcount
            row = con.execute("SELECT * FROM order_intents WHERE intent_key=?", (intent_key,)).fetchone()
        self.event(actor, "order_intent_created" if inserted else "order_intent_deduplicated", payload, run_id=run_id, correlation_id=intent_key)
        return bool(inserted), dict(row)

    def resolve_order_intent(self, intent_key: str, status: str, *, ticket: Optional[int] = None,
                             result: Optional[Dict[str, Any]] = None, error: str = "") -> bool:
        """Apply one documented order-intent transition, never a free-form overwrite.

        Repeating the current state is idempotent. Terminal states cannot be
        changed later, while ``UNRESOLVED`` remains recoverable only into a
        broker-confirmed terminal/partial outcome. This prevents a stale or
        malformed callback from rewriting execution truth.
        """
        key = str(intent_key or "").strip()
        next_status = str(status or "").upper().strip()
        if not key:
            raise ValueError("intent_key is required")
        if next_status not in self.ORDER_INTENT_TRANSITIONS:
            raise ValueError(f"invalid order intent status: {next_status}")
        transition_event = None
        with self._con() as con:
            row = con.execute("SELECT status,mt5_ticket FROM order_intents WHERE intent_key=?", (key,)).fetchone()
            if not row:
                raise KeyError(f"unknown order intent: {key}")
            current = str(row["status"] or "").upper()
            allowed = self.ORDER_INTENT_TRANSITIONS.get(current)
            if not allowed or next_status not in allowed:
                raise ValueError(f"invalid order intent transition: {current} -> {next_status}")
            if next_status == current:
                return False
            now = utcnow()
            con.execute("UPDATE order_intents SET status=?,updated_at=?,mt5_ticket=COALESCE(?,mt5_ticket),result=?,error=? WHERE intent_key=?",
                        (next_status, now, ticket, json.dumps(result or {}, default=str), error[:2000], key))
            if next_status in {"REJECTED", "CANCELLED", "EXPIRED", "UNRESOLVED", "ERROR"}:
                label_state = "UNRESOLVED" if next_status in {"UNRESOLVED", "ERROR"} else next_status
                persist_setup_label(con, "order_intent", f"order_intent:{key}", label_state, error or next_status)
            if next_status in {"FILLED", "PARTIAL", "CANCELLED", "REJECTED", "EXPIRED"}:
                con.execute("UPDATE execution_reviews SET status='AUTO_RESOLVED',resolved_at=?,resolution_note=? WHERE intent_key=? AND status IN ('OPEN','ACKNOWLEDGED')",
                            (now, f"broker terminal outcome: {next_status}", key))
            transition_event = {"intent_key": key, "from": current, "to": next_status,
                                "ticket": ticket, "error": error[:500]}
        self.event("reconciler", "order_intent_transition", transition_event,
                   correlation_id=key, severity="WARNING" if next_status in {"ERROR", "UNRESOLVED"} else "INFO")
        return True

    def flag_execution_review(self, intent_key: str, reason: str, *, severity: str = "WARNING") -> None:
        """Open/update durable operator review for an unresolved execution."""
        now = utcnow()
        first_retry = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        with self._con() as con:
            con.execute("""INSERT INTO execution_reviews(intent_key,first_detected_at,last_detected_at,status,severity,detections,reason,next_retry_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(intent_key) DO UPDATE SET last_detected_at=excluded.last_detected_at,
                severity=excluded.severity,detections=execution_reviews.detections+1,reason=excluded.reason,
                next_retry_at=COALESCE(execution_reviews.next_retry_at, excluded.next_retry_at),
                status=CASE WHEN execution_reviews.status IN ('RESOLVED','QUARANTINED','AUTO_RESOLVED') THEN execution_reviews.status ELSE 'OPEN' END""",
                (intent_key, now, now, "OPEN", severity[:30], 1, reason[:2000], first_retry))
        self.event("reconciler", "execution_review_flagged", {"reason": reason}, correlation_id=intent_key, severity=severity)

    def execution_review_retry_state(self, intent_key: str, *, now: Optional[datetime] = None,
                                     max_attempts: int = 5) -> str:
        """Return the durable retry state for one unresolved execution."""
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed = observed.astimezone(timezone.utc)
        with self._con() as con:
            row = con.execute("SELECT status,retry_count,next_retry_at FROM execution_reviews WHERE intent_key=?", (intent_key,)).fetchone()
        if not row:
            return "NO_REVIEW"
        if str(row["status"]).upper() not in {"OPEN", "ACKNOWLEDGED"}:
            return "CLOSED"
        if int(row["retry_count"] or 0) >= max_attempts:
            return "EXHAUSTED"
        try:
            if row["next_retry_at"] in (None, ""):
                return "DUE"
            due = datetime.fromisoformat(str(row["next_retry_at"]).replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return "DUE" if observed >= due.astimezone(timezone.utc) else "WAIT"
        except (TypeError, ValueError, OverflowError):
            return "DUE"

    def claim_due_execution_review_retries(self, *, now: Optional[datetime] = None,
                                            max_attempts: int = 5,
                                            base_seconds: int = 30,
                                            max_seconds: int = 900) -> list[Dict[str, Any]]:
        """Claim due broker-history retries with bounded exponential backoff.

        Claiming only schedules a read-only reconciliation retry. It never
        resubmits an order and never changes execution authority.
        """
        if max_attempts <= 0 or base_seconds <= 0 or max_seconds < base_seconds:
            raise ValueError("retry policy thresholds are invalid")
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed = observed.astimezone(timezone.utc)
        observed_text = observed.isoformat()
        claimed: list[Dict[str, Any]] = []
        with self._con() as con:
            rows = con.execute("""SELECT * FROM execution_reviews
                WHERE status IN ('OPEN','ACKNOWLEDGED') AND retry_count < ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY last_detected_at LIMIT 100""", (max_attempts, observed_text)).fetchall()
            for row in rows:
                new_count = int(row["retry_count"] or 0) + 1
                delay = min(max_seconds, base_seconds * (2 ** max(0, new_count - 1)))
                next_retry = (observed + timedelta(seconds=delay)).isoformat()
                con.execute("""UPDATE execution_reviews SET retry_count=?,last_retry_at=?,next_retry_at=?
                    WHERE intent_key=? AND status IN ('OPEN','ACKNOWLEDGED') AND retry_count < ?""",
                            (new_count, observed_text, next_retry, row["intent_key"], max_attempts))
                claimed.append({"intent_key": row["intent_key"], "retry_count": new_count,
                                "next_retry_at": next_retry, "reason": row["reason"]})
        for item in claimed:
            self.event("reconciler", "execution_review_retry_claimed", item,
                       correlation_id=item["intent_key"], severity="WARNING")
        return claimed

    def execution_reviews(self, include_resolved: bool = False) -> list[Dict[str, Any]]:
        with self._con() as con:
            sql = "SELECT r.*,i.status AS intent_status,i.mt5_ticket,i.created_at AS intent_created_at,i.error AS intent_error FROM execution_reviews r LEFT JOIN order_intents i ON i.intent_key=r.intent_key"
            if not include_resolved:
                sql += " WHERE r.status IN ('OPEN','ACKNOWLEDGED')"
            rows = con.execute(sql + " ORDER BY r.last_detected_at DESC").fetchall()
        return [dict(row) for row in rows]

    def resolve_execution_review(self, intent_key: str, *, disposition: str,
                                 operator: str, note: str = "") -> None:
        """Require an explicit human disposition for unresolved broker evidence."""
        disposition = disposition.upper()
        if disposition not in {"ACKNOWLEDGED", "RESOLVED", "QUARANTINED"}:
            raise ValueError("disposition must be ACKNOWLEDGED, RESOLVED, or QUARANTINED")
        with self._con() as con:
            updated = con.execute("UPDATE execution_reviews SET status=?,operator=?,resolution_note=?,resolved_at=? WHERE intent_key=?",
                                  (disposition, operator[:200], note[:2000], utcnow() if disposition in {"RESOLVED", "QUARANTINED"} else None, intent_key)).rowcount
        if not updated:
            raise ValueError("unknown execution review")
        self.event("operator", "execution_review_" + disposition.lower(), {"note": note}, correlation_id=intent_key,
                   severity="WARNING" if disposition == "QUARANTINED" else "INFO")

    def escalate_execution_reviews(self, *, warning_after_seconds: int = 300,
                                   critical_after_seconds: int = 1800,
                                   critical_detections: int = 3,
                                   now: Optional[datetime] = None) -> Dict[str, Any]:
        """Escalate unresolved broker reviews using durable age/count evidence.

        This changes only review severity; it never resolves an intent or grants
        execution authority.  The thresholds and observation time are returned
        so an alert/postmortem can be reproduced after restart.
        """
        if warning_after_seconds <= 0 or critical_after_seconds < warning_after_seconds or critical_detections <= 0:
            raise ValueError("review escalation thresholds are invalid")
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed = observed.astimezone(timezone.utc)
        with self._con() as con:
            rows = con.execute(
                "SELECT intent_key,first_detected_at,last_detected_at,status,severity,detections,reason "
                "FROM execution_reviews WHERE status IN ('OPEN','ACKNOWLEDGED')"
            ).fetchall()
            escalated: list[Dict[str, Any]] = []
            current_critical = 0
            for row in rows:
                try:
                    first = datetime.fromisoformat(str(row["first_detected_at"]).replace("Z", "+00:00"))
                    if first.tzinfo is None:
                        first = first.replace(tzinfo=timezone.utc)
                    age_seconds = max(0.0, (observed - first.astimezone(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    age_seconds = float("inf")
                desired = "CRITICAL" if age_seconds >= critical_after_seconds or int(row["detections"] or 0) >= critical_detections else (
                    "WARNING" if age_seconds >= warning_after_seconds else str(row["severity"] or "WARNING")
                )
                current = str(row["severity"] or "WARNING").upper()
                if desired == "CRITICAL":
                    current_critical += 1
                rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
                if rank.get(desired, 1) > rank.get(current, 1):
                    note = f"age_seconds={age_seconds:.1f}; detections={int(row['detections'] or 0)}"
                    con.execute(
                        "UPDATE execution_reviews SET severity=?,reason=? WHERE intent_key=?",
                        (desired, f"{str(row['reason'] or '')[:1700]}; escalation {note}", row["intent_key"]),
                    )
                    escalated.append({"intent_key": row["intent_key"], "from": current,
                                      "to": desired, "age_seconds": age_seconds,
                                      "detections": int(row["detections"] or 0)})
        for item in escalated:
            self.event("reconciler", "execution_review_escalated", item,
                       correlation_id=item["intent_key"], severity=item["to"])
        return {
            "observed_at": observed.isoformat(),
            "thresholds": {"warning_after_seconds": warning_after_seconds,
                           "critical_after_seconds": critical_after_seconds,
                           "critical_detections": critical_detections},
            "open_reviews": len(rows), "current_critical": current_critical,
            "escalated": escalated, "execution_authority": False,
        }

    def order_intents(self, statuses: Tuple[str, ...] = ("PENDING",)) -> list[Dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._con() as con:
            rows = con.execute(f"SELECT * FROM order_intents WHERE status IN ({placeholders}) ORDER BY created_at", statuses).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"] or "{}")
            item["result"] = json.loads(item["result"] or "{}")
            result.append(item)
        return result

    def enqueue_memory(self, kind: str, source_id: str, payload: Dict[str, Any]) -> None:
        safe_payload = self._redact(payload)
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO memory_outbox(created_at,kind,status,next_attempt_at,source_id,payload) VALUES(?,?, 'PENDING',?,?,?)", (utcnow(), kind, utcnow(), source_id, json.dumps(safe_payload, default=str)))
        # Also remember closed-trade / event-reaction cycles in local agent memory.
        try:
            from intelligence.local_agent_memory import maybe_remember_trade_cycle
            stage = "closed" if str(kind) == "closed_trade" else str(kind or "memory_outbox")
            maybe_remember_trade_cycle(
                cycle_key=str(source_id),
                stage=stage,
                payload={"kind": kind, "source_id": source_id, **(safe_payload if isinstance(safe_payload, dict) else {"value": safe_payload})},
                ticket=safe_payload.get("ticket") if isinstance(safe_payload, dict) else source_id,
                symbol=str((safe_payload or {}).get("symbol", "")) if isinstance(safe_payload, dict) else "",
                side=str((safe_payload or {}).get("side", "")) if isinstance(safe_payload, dict) else "",
                memory_links={"L1": source_id, "verified_memory": source_id},
            )
        except Exception as exc:
            logger.debug("event emission failed: %s", exc)

    def pending_memory(self, limit: int = 20):
        """Due PENDING rows, oldest-attempt first — not INSERT-id first.

        Pure ``ORDER BY id`` starved high-id rows forever: three low-id rows
        that permanently fail during provider outages retry every 5 minutes
        and always occupy the lowest-id batch slots (2026-09-29 → 10-01:
        ids 17-20 sat at attempts=0 for 36h while every batch burned its
        slots on ids 13/14/16). Oldest-attempt first drains every row.
        """
        with self._con() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM memory_outbox WHERE status='PENDING' AND next_attempt_at <= ? "
                "ORDER BY attempts ASC, id ASC LIMIT ?", (utcnow(), limit)).fetchall()]

    def complete_memory(self, message_id: int, error: str = "") -> str:
        """Record a distillation attempt outcome; returns the row's new status
        (``SENT``/``PENDING``/``FAILED``) so callers can distinguish a retry
        from permanent parking without re-querying."""
        with self._con() as con:
            if not error:
                con.execute("UPDATE memory_outbox SET status='SENT',sent_at=?,error=NULL WHERE id=?", (utcnow(), message_id))
                return "SENT"
            # Bounded retry with backoff; a permanently failing record is
            # parked as FAILED instead of retrying forever every 5 minutes.
            retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            con.execute(
                """UPDATE memory_outbox SET attempts=attempts+1,
                   status=CASE WHEN attempts+1>=8 THEN 'FAILED' ELSE 'PENDING' END,
                   next_attempt_at=?,error=? WHERE id=?""",
                (retry, error[:2000], message_id),
            )
            row = con.execute(
                "SELECT status FROM memory_outbox WHERE id=?", (message_id,)).fetchone()
            return str(row["status"]) if row is not None else "PENDING"

    def block_memory(self, message_id: int, reason: str) -> None:
        """Quarantine an unverified memory source; it must never retry into context."""
        with self._con() as con:
            con.execute("UPDATE memory_outbox SET status='BLOCKED',attempts=attempts+1,error=? WHERE id=?",
                        (str(reason)[:2000], int(message_id)))

    @staticmethod
    def _decode_json(value: Any) -> Any:
        try:
            return json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            return value

    def audit_export(self, *, limit: int = 10000) -> Dict[str, Any]:
        """Return a bounded, redacted operational evidence bundle.

        This is an evidence export only.  It does not grant execution
        authority and never mutates the journal.  Raw traces and operational
        events are already redacted at write time; configuration/snapshot
        payloads are redacted again at the export boundary as defense in
        depth.
        """
        limit = max(1, min(int(limit), 100000))

        def rows(con: sqlite3.Connection, query: str, hash_field: str = "") -> list[Dict[str, Any]]:
            result = []
            for row in con.execute(query, (limit,)).fetchall():
                item = dict(row)
                if "payload" in item:
                    raw_payload = item["payload"]
                    item["payload"] = self._redact(self._decode_json(raw_payload))
                    if hash_field:
                        expected = str(item.get(hash_field) or "")
                        item["payload_hash_valid"] = bool(expected) and hashlib.sha256(
                            str(raw_payload).encode("utf-8")
                        ).hexdigest() == expected
                result.append(item)
            return result

        with self._con() as con:
            events = rows(con, "SELECT * FROM operational_events ORDER BY id LIMIT ?", "payload_hash")
            traces = rows(con, "SELECT * FROM raw_traces ORDER BY created_at,trace_id LIMIT ?", "redacted_hash")
            snapshots = rows(con, "SELECT * FROM data_snapshots ORDER BY captured_at,snapshot_id LIMIT ?", "source_hash")
            configs = rows(con, "SELECT * FROM configuration_versions ORDER BY created_at,version_id LIMIT ?", "content_hash")
            clocks = rows(con, "SELECT * FROM clock_observations ORDER BY id LIMIT ?")
        hashed_sets = {
            "operational_events": events, "raw_traces": traces,
            "data_snapshots": snapshots, "configuration_versions": configs,
        }
        integrity = {
            name: {"checked": len(items),
                   "valid": sum(1 for item in items if item.get("payload_hash_valid")),
                   "passed": all(item.get("payload_hash_valid") is True for item in items)}
            for name, items in hashed_sets.items()
        }
        return {
            "generated_at": utcnow(),
            "execution_authority": False,
            "limit": limit,
            "counts": {
                "operational_events": len(events), "raw_traces": len(traces),
                "data_snapshots": len(snapshots), "configuration_versions": len(configs),
                "clock_observations": len(clocks),
            },
            "integrity": integrity,
            "operational_events": events,
            "raw_traces": traces,
            "data_snapshots": snapshots,
            "configuration_versions": configs,
            "clock_observations": clocks,
            "limitation": "Evidence export only; it never grants order permission.",
        }

    def retention_status(self, *, older_than_days: int = 365) -> Dict[str, Any]:
        """Report evidence outside retention without deleting append-only data."""
        if int(older_than_days) < 1:
            raise ValueError("older_than_days must be at least 1")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(older_than_days))).isoformat()
        tables = {
            "operational_events": "created_at", "raw_traces": "created_at",
            "data_snapshots": "captured_at", "configuration_versions": "created_at",
            "clock_observations": "observed_at", "llm_requests": "created_at",
            # Volatile telemetry tables (reported for visibility; pruned via
            # prune_volatile_tables, never by this status command).
            "harness_events": "timestamp", "macro_observations": "captured_at",
            "macro_edge_history": "changed_at", "market_ticks": "observed_at",
            "correlation_returns": "captured_at", "metrics_equity": "recorded_at",
        }
        report = {}
        with self._con() as con:
            for table, column in tables.items():
                try:
                    total = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    eligible = int(con.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} < ?", (cutoff,)
                    ).fetchone()[0])
                except sqlite3.Error as exc:
                    # Fail-safe by contract: some tables in the report
                    # (harness_events and other harness/trading-owned
                    # telemetry) are created by their owning components, not
                    # by this store, so a fresh journal legitimately lacks
                    # them. Degrade the per-table entry visibly instead of
                    # crashing the whole status command (same pattern as
                    # prune_volatile_tables).
                    report[table] = {"status": "UNAVAILABLE", "error": str(exc)[:200]}
                    continue
                report[table] = {"total": total, "outside_retention": eligible,
                                 "status": "REVIEW_REQUIRED" if eligible else "WITHIN_RETENTION"}
        return {
            "generated_at": utcnow(), "older_than_days": int(older_than_days),
            "cutoff": cutoff, "tables": report, "execution_authority": False,
            "deletion": "DISABLED_BY_DEFAULT",
            "limitation": "Append-only evidence is never deleted by this status command; export/archive and operator policy review are required.",
        }

    def summary(self) -> Dict[str, Any]:
        # Status is also an operator recovery boundary.  A process can crash
        # before its next worker starts, so an old RUNNING row must not remain
        # visible as active indefinitely.  Active workers heartbeat well below
        # this threshold and are unaffected.
        stale_runs_recovered = self.recover_stale_runs()
        with self._con() as con:
            return {
                "runs": con.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
                "stale_runs_recovered": stale_runs_recovered,
                "running_runs": con.execute("SELECT COUNT(*) FROM agent_runs WHERE status='RUNNING'").fetchone()[0],
                "events": con.execute("SELECT COUNT(*) FROM operational_events").fetchone()[0],
                "tasks_running": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='RUNNING'").fetchone()[0],
                "tasks_error": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='ERROR'").fetchone()[0],
                "expired_task_leases": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='RUNNING' AND lease_expires_at < ?", (utcnow(),)).fetchone()[0],
                "retryable_task_errors": con.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='ERROR' AND attempts < max_attempts").fetchone()[0],
                "queue_backpressure": self.backpressure_status(),
                "task_edges": con.execute("SELECT COUNT(*) FROM agent_task_edges").fetchone()[0],
                "llm_today": con.execute("SELECT COUNT(*) FROM llm_requests WHERE created_at >= ?", (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),)).fetchone()[0],
                "pending_memory": con.execute("SELECT COUNT(*) FROM memory_outbox WHERE status='PENDING'").fetchone()[0],
                "blocked_memory": con.execute("SELECT COUNT(*) FROM memory_outbox WHERE status='BLOCKED'").fetchone()[0],
                "unresolved_order_intents": con.execute("SELECT COUNT(*) FROM order_intents WHERE status IN ('CREATED','PENDING','UNRESOLVED','ERROR')").fetchone()[0],
                "open_execution_reviews": con.execute("SELECT COUNT(*) FROM execution_reviews WHERE status IN ('OPEN','ACKNOWLEDGED')").fetchone()[0],
                "snapshots": con.execute("SELECT COUNT(*) FROM data_snapshots").fetchone()[0],
                "configuration_versions": con.execute("SELECT COUNT(*) FROM configuration_versions").fetchone()[0],
                "raw_traces": con.execute("SELECT COUNT(*) FROM raw_traces").fetchone()[0],
                "enabled_schedules": con.execute("SELECT COUNT(*) FROM agent_schedules WHERE enabled=1").fetchone()[0],
                "execution_authority": False,
            }

    # Non-evidence telemetry: safe to prune on an age cutoff + row cap.
    # Evidence tables (order_intents, trade_lifecycle_events, data_snapshots,
    # raw_traces, operational_events, llm_requests, ...) are NEVER here —
    # the journal-is-source-of-truth doctrine keeps them append-only.
    VOLATILE_TABLES: Dict[str, Dict[str, Any]] = {
        "harness_events":       {"ts": "timestamp",    "days": 30,  "cap": 200_000},
        "market_ticks":         {"ts": "observed_at",  "days": 7,   "cap": 50_000},
        "correlation_returns":  {"ts": "captured_at",  "days": 30,  "cap": 100_000},
        "metrics_equity":       {"ts": "recorded_at",  "days": 180, "cap": 50_000},
    }

    def prune_volatile_tables(self, *, older_than_days: int = 30,
                              apply: bool = False) -> Dict[str, Any]:
        """Prune non-evidence telemetry by age and row cap.

        Report-only unless ``apply=True``. Age cutoff defaults to 30 days;
        the row cap additionally bounds growth for hot tables regardless of
        age. Volatile tables only — evidence tables are never touched.
        """
        if int(older_than_days) < 1:
            raise ValueError("older_than_days must be at least 1")
        now = datetime.now(timezone.utc)
        report: Dict[str, Any] = {}
        with self._con() as con:
            for table, cfg in self.VOLATILE_TABLES.items():
                try:
                    total = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    cutoff = (now - timedelta(days=max(int(older_than_days), int(cfg["days"])))).isoformat()
                    stale = int(con.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {cfg['ts']} < ?", (cutoff,)
                    ).fetchone()[0])
                    over_cap = max(0, total - int(cfg["cap"]))
                    deleted = 0
                    if apply and (stale or over_cap):
                        if stale:
                            cur = con.execute(
                                f"DELETE FROM {table} WHERE {cfg['ts']} < ?", (cutoff,))
                            deleted += max(0, cur.rowcount)
                        if over_cap:
                            cur = con.execute(
                                f"DELETE FROM {table} WHERE rowid IN ("
                                f"SELECT rowid FROM {table} ORDER BY {cfg['ts']} ASC LIMIT ?)",
                                (over_cap,))
                            deleted += max(0, cur.rowcount)
                        con.commit()
                    remaining = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    report[table] = {
                        "total_before": total, "stale": stale, "over_cap": over_cap,
                        "deleted": deleted, "remaining": remaining,
                        "retention_days": cfg["days"], "row_cap": cfg["cap"],
                    }
                except sqlite3.Error as exc:
                    report[table] = {"error": str(exc)[:200]}
        result = {
            "generated_at": now.isoformat(), "applied": bool(apply),
            "older_than_days": int(older_than_days), "tables": report,
            "note": "Run VACUUM offline (perform_database_maintenance CLI) to reclaim file size; live VACUUM is intentionally avoided.",
            "execution_authority": False,
        }
        self.event("operations", "volatile_tables_pruned" if apply else "volatile_tables_prune_report",
                   {k: v for k, v in result.items() if k != "tables"} if not apply else {"deleted": {t: r.get("deleted", 0) for t, r in report.items()}},
                   severity="INFO")
        return result

    def checkpoint_and_verify(self) -> Dict[str, Any]:
        """Cheap live maintenance: truncate the WAL and verify integrity.

        Unlike perform_database_maintenance this never copies the database
        file, so it is safe to run on the live journal (a multi-GB backup
        remains an explicit operator action via the CLI).
        """
        with self._con() as con:
            wal = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            integrity = [str(r[0]) for r in con.execute("PRAGMA integrity_check").fetchall()]
        ok = len(integrity) == 1 and integrity[0].lower() == "ok"
        result = {
            "wal_checkpoint": {"busy": int(wal[0]) if wal else -1,
                               "log_pages": int(wal[1]) if wal else -1,
                               "checkpointed": int(wal[2]) if wal else -1},
            "integrity_ok": ok, "integrity_messages": integrity[:5],
            "execution_authority": False,
        }
        self.event("operations", "journal_checkpoint", result,
                   severity="INFO" if ok else "CRITICAL")
        return result

    def perform_database_maintenance(self, backup_dir: str = "backups") -> Dict[str, Any]:
        """Perform institutional database hygiene: WAL checkpoint truncate, PRAGMA integrity_check, and online backup.

        1. Runs `PRAGMA wal_checkpoint(TRUNCATE)` to flush and truncate write-ahead logs.
        2. Runs `PRAGMA integrity_check` to detect any page corruption or indexing faults.
        3. Creates an atomic online backup snapshot using `sqlite3.Connection.backup` into `backup_dir`.
        4. Computes SHA-256 digest of the backup file for cold-storage auditability.
        Strictly read-only / administrative: execution_authority is False.
        """
        os.makedirs(backup_dir, exist_ok=True)
        now_utc = datetime.now(timezone.utc)
        ts_str = now_utc.strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(os.path.basename(self.db_path))[0]
        backup_filename = f"{base_name}_{ts_str}.db.bak"
        backup_path = os.path.join(backup_dir, backup_filename)

        with self._con() as con:
            # 1. Truncate WAL
            wal_res = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            wal_checkpoint_busy = int(wal_res[0]) if wal_res else 0
            wal_checkpoint_log = int(wal_res[1]) if wal_res else 0
            wal_checkpoint_checkpointed = int(wal_res[2]) if wal_res else 0

            # 2. Check integrity
            integrity_rows = con.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(r[0]) for r in integrity_rows]
            integrity_ok = len(integrity_messages) == 1 and integrity_messages[0].lower() == "ok"

            # 3. Online atomic backup
            bck_con = sqlite3.connect(backup_path)
            try:
                con.backup(bck_con)
            finally:
                bck_con.close()

        # 4. Checksum of backup
        with open(backup_path, "rb") as bf:
            digest = hashlib.sha256(bf.read()).hexdigest()

        backup_size = os.path.getsize(backup_path)

        result = {
            "timestamp": now_utc.isoformat(),
            "database_path": self.db_path,
            "wal_checkpoint": {
                "status": "SUCCESS" if wal_checkpoint_busy == 0 else "BUSY",
                "busy": wal_checkpoint_busy,
                "log_pages": wal_checkpoint_log,
                "checkpointed_pages": wal_checkpoint_checkpointed,
            },
            "integrity_check": {
                "passed": integrity_ok,
                "messages": integrity_messages,
            },
            "backup": {
                "created": True,
                "path": os.path.abspath(backup_path),
                "filename": backup_filename,
                "size_bytes": backup_size,
                "sha256": digest,
            },
            "execution_authority": False,
        }
        self.event("operations", "database_maintenance", result, severity="INFO" if integrity_ok else "CRITICAL")
        return result
