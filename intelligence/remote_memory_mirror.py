"""Optional Remote Mirror for agent working / semantic memory.

Local SQLite (`TradingMemory` / `AgentOperations.working_memory`) remains the
authoritative store.  This module never grants execution authority and never
blocks the local write path.

Design:
- Disabled by default (`REMOTE_MIRROR_ENABLED` unset/false).
- When enabled, requires host + credentials; otherwise reports
  ``MISSING_CREDENTIALS`` and no-ops.
- Uses ``pymysql`` only if installed (MySQL protocol). Missing driver
  yields ``MISSING_DRIVER`` without pretending a cloud write succeeded.
- Connection objects can be injected for tests; live network calls are not
  fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional


ConnectionFactory = Callable[[], Any]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _redact_dsn(host: str, user: str, database: str) -> Dict[str, str]:
    return {
        "host": host or "",
        "user": user or "",
        "database": database or "",
        "password": "[REDACTED]" if os.getenv("REMOTE_MIRROR_PASSWORD") or os.getenv("REMOTE_MIRROR_PASSWORD") else "",
    }


class RemoteMirrorConfig:
    """Environment-driven optional RemoteMirror mirror settings."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        host: str = "",
        port: int = 3306,
        user: str = "",
        password: str = "",
        database: str = "",
        table_prefix: str = "llm_trader_",
        mirror_on_write: bool = False,
        connect_timeout: float = 3.0,
    ):
        self.enabled = bool(enabled)
        self.host = str(host or "").strip()
        self.port = int(port)
        self.user = str(user or "").strip()
        self.password = str(password or "")
        self.database = str(database or "").strip()
        self.table_prefix = re.sub(r"[^a-zA-Z0-9_]", "", str(table_prefix or "llm_trader_")) or "llm_trader_"
        self.mirror_on_write = bool(mirror_on_write)
        self.connect_timeout = float(connect_timeout)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "RemoteMirrorConfig":
        source = env if env is not None else os.environ
        def _get(key, default=""):
            if isinstance(source, dict):
                return source.get(key, default)
            return os.getenv(key, default)
        port_raw = str(_get("REMOTE_MIRROR_PORT", "3306") or "3306")
        timeout_raw = str(_get("REMOTE_MIRROR_CONNECT_TIMEOUT", "3") or "3")
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = 3306
        try:
            timeout = float(timeout_raw)
        except (TypeError, ValueError):
            timeout = 3.0
        return cls(
            enabled=_truthy(_get("REMOTE_MIRROR_ENABLED", "")),
            host=str(_get("REMOTE_MIRROR_HOST", "") or ""),
            port=port,
            user=str(_get("REMOTE_MIRROR_USER", "") or ""),
            password=str(_get("REMOTE_MIRROR_PASSWORD", "") or ""),
            database=str(_get("REMOTE_MIRROR_DATABASE", "") or ""),
            table_prefix=str(_get("REMOTE_MIRROR_TABLE_PREFIX", "llm_trader_") or "llm_trader_"),
            mirror_on_write=_truthy(_get("REMOTE_MIRROR_ON_WRITE", "")),
            connect_timeout=timeout,
        )

    def credentials_complete(self) -> bool:
        return bool(self.host and self.user and self.password and self.database and 1 <= self.port <= 65535)


class RemoteMemoryMirror:
    """Fail-closed optional remote mirror. Local SQLite stays primary."""

    WORKING_TABLE = "agent_working_memory"
    SEMANTIC_TABLE = "agent_semantic_vectors"
    SYNC_LOG_TABLE = "agent_memory_sync_log"

    def __init__(
        self,
        config: Optional[RemoteMirrorConfig] = None,
        *,
        connection_factory: Optional[ConnectionFactory] = None,
    ):
        self.config = config or RemoteMirrorConfig.from_env()
        self._connection_factory = connection_factory
        self._driver_error = ""
        self._schema_ready = False

    def status(self) -> Dict[str, Any]:
        """Operator-visible readiness; never implies order permission."""
        base = {
            "backend": "remote_mirror",
            "enabled": self.config.enabled,
            "mirror_on_write": self.config.mirror_on_write,
            "primary_store": "local_sqlite",
            "authoritative": False,
            "cloud_mirror_only": True,
            "execution_authority": False,
            "dsn": _redact_dsn(self.config.host, self.config.user, self.config.database),
            "table_prefix": self.config.table_prefix,
        }
        if not self.config.enabled:
            return {**base, "mode": "DISABLED", "ready": False,
                    "reason": "REMOTE_MIRROR_ENABLED is not true; local SQLite remains authoritative."}
        if not self.config.credentials_complete():
            return {**base, "mode": "MISSING_CREDENTIALS", "ready": False,
                    "reason": "enabled but host/user/password/database incomplete"}
        driver = self._resolve_driver()
        if driver is None and self._connection_factory is None:
            return {**base, "mode": "MISSING_DRIVER", "ready": False,
                    "reason": self._driver_error or "pymysql is not installed"}
        if self._connection_factory is None:
            # Honest: credentials present and driver importable, but we do not
            # open a live socket during status() unless explicitly probed.
            return {**base, "mode": "CONFIGURED", "ready": True,
                    "reason": "credentials and driver present; call probe() to verify connectivity"}
        return {**base, "mode": "CONFIGURED", "ready": True,
                "reason": "test/injected connection factory available"}

    def probe(self) -> Dict[str, Any]:
        """Optional live connectivity check; failures stay fail-closed."""
        status = self.status()
        if not status.get("ready"):
            return {**status, "probe": "SKIPPED", "execution_authority": False}
        try:
            with self._connect() as con:
                self._ensure_schema(con)
                cursor = con.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return {**status, "probe": "OK", "schema_ready": True, "execution_authority": False}
        except Exception as exc:
            return {**status, "probe": "FAILED", "ready": False, "mode": "ERROR",
                    "error": str(exc)[:500], "execution_authority": False}

    def upsert_working_memory(
        self,
        *,
        namespace: str,
        memory_key: str,
        kind: str,
        priority: int,
        revision: int,
        payload: Any,
        source_ids: Optional[Iterable[str]] = None,
        payload_hash: str = "",
        updated_at: str = "",
    ) -> Dict[str, Any]:
        """Mirror one working-memory row. No-ops when not ready."""
        gate = self.status()
        if not gate.get("ready"):
            return {"mirrored": False, "reason": gate.get("reason") or gate.get("mode"),
                    "mode": gate.get("mode"), "execution_authority": False}
        encoded_payload = json.dumps(payload, default=str, sort_keys=True)
        encoded_sources = json.dumps(list(source_ids or []), default=str)
        digest = payload_hash or hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()
        now = updated_at or _utcnow()
        try:
            with self._connect() as con:
                self._ensure_schema(con)
                table = self._table(self.WORKING_TABLE)
                cursor = con.cursor()
                cursor.execute(
                    f"""INSERT INTO {table}
                        (namespace, memory_key, kind, priority, revision, status,
                         updated_at, source_ids, payload, payload_hash)
                        VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          kind=VALUES(kind), priority=VALUES(priority),
                          revision=VALUES(revision), status='ACTIVE',
                          updated_at=VALUES(updated_at), source_ids=VALUES(source_ids),
                          payload=VALUES(payload), payload_hash=VALUES(payload_hash)
                    """,
                    (namespace[:120], memory_key[:200], kind[:80], int(priority),
                     int(revision), now, encoded_sources, encoded_payload, digest),
                )
                self._log_sync(con, "working_memory", f"{namespace}:{memory_key}", "OK", "")
                con.commit()
            return {"mirrored": True, "namespace": namespace, "memory_key": memory_key,
                    "payload_hash": digest, "execution_authority": False}
        except Exception as exc:
            return {"mirrored": False, "mode": "ERROR", "error": str(exc)[:500],
                    "execution_authority": False}

    def fetch_working_memory(self, namespace: str, *, limit: int = 40) -> Dict[str, Any]:
        """Advisory remote read; callers must not treat this as order permission."""
        gate = self.status()
        if not gate.get("ready"):
            return {"items": [], "count": 0, "reason": gate.get("reason") or gate.get("mode"),
                    "mode": gate.get("mode"), "execution_authority": False}
        try:
            with self._connect() as con:
                self._ensure_schema(con)
                table = self._table(self.WORKING_TABLE)
                cursor = con.cursor()
                cursor.execute(
                    f"""SELECT namespace, memory_key, kind, priority, revision, status,
                               updated_at, source_ids, payload, payload_hash
                        FROM {table}
                        WHERE namespace=%s AND status='ACTIVE'
                        ORDER BY priority DESC, updated_at DESC
                        LIMIT %s""",
                    (namespace[:120], max(1, int(limit))),
                )
                rows = cursor.fetchall()
            items = []
            for row in rows:
                if isinstance(row, dict):
                    item = dict(row)
                else:
                    item = {
                        "namespace": row[0], "memory_key": row[1], "kind": row[2],
                        "priority": row[3], "revision": row[4], "status": row[5],
                        "updated_at": row[6], "source_ids": row[7], "payload": row[8],
                        "payload_hash": row[9],
                    }
                for field in ("source_ids", "payload"):
                    try:
                        item[field] = json.loads(item[field]) if isinstance(item[field], str) else item[field]
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                items.append(item)
            return {"items": items, "count": len(items), "namespace": namespace,
                    "execution_authority": False}
        except Exception as exc:
            return {"items": [], "count": 0, "mode": "ERROR", "error": str(exc)[:500],
                    "execution_authority": False}

    def upsert_semantic_vector(
        self,
        *,
        record_id: str,
        layer: str,
        tags: Any,
        content: Any,
        source_ids: Optional[Iterable[str]] = None,
        vector: Optional[Dict[int, float]] = None,
    ) -> Dict[str, Any]:
        """Store a deterministic hashed-token vector blob (not a managed VectorDB)."""
        gate = self.status()
        if not gate.get("ready"):
            return {"mirrored": False, "reason": gate.get("reason") or gate.get("mode"),
                    "mode": gate.get("mode"), "execution_authority": False}
        vec = vector if isinstance(vector, dict) else _hash_vector({"tags": tags, "content": content})
        payload = {
            "tags": tags,
            "content": content,
            "source_ids": list(source_ids or []),
            "vector": {str(k): float(v) for k, v in vec.items()},
            "note": "Sparse hashed-token vector mirrored to remote store; not a managed VectorDB index.",
        }
        now = _utcnow()
        try:
            with self._connect() as con:
                self._ensure_schema(con)
                table = self._table(self.SEMANTIC_TABLE)
                cursor = con.cursor()
                cursor.execute(
                    f"""INSERT INTO {table}
                        (record_id, layer, updated_at, payload_json, payload_hash)
                        VALUES (%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          layer=VALUES(layer), updated_at=VALUES(updated_at),
                          payload_json=VALUES(payload_json), payload_hash=VALUES(payload_hash)
                    """,
                    (
                        str(record_id)[:200],
                        str(layer)[:80],
                        now,
                        json.dumps(payload, default=str, sort_keys=True),
                        hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode("utf-8")).hexdigest(),
                    ),
                )
                self._log_sync(con, "semantic_vector", str(record_id), "OK", "")
                con.commit()
            return {"mirrored": True, "record_id": record_id, "layer": layer,
                    "execution_authority": False}
        except Exception as exc:
            return {"mirrored": False, "mode": "ERROR", "error": str(exc)[:500],
                    "execution_authority": False}

    def search_semantic(self, query: Any, *, limit: int = 5, candidate_limit: int = 200) -> Dict[str, Any]:
        """Client-side cosine over mirrored sparse vectors; advisory only."""
        gate = self.status()
        if not gate.get("ready"):
            return {"results": [], "reason": gate.get("reason") or gate.get("mode"),
                    "mode": gate.get("mode"), "execution_authority": False}
        query_vec = _hash_vector(query or {})
        try:
            with self._connect() as con:
                self._ensure_schema(con)
                table = self._table(self.SEMANTIC_TABLE)
                cursor = con.cursor()
                cursor.execute(
                    f"""SELECT record_id, layer, payload_json FROM {table}
                        ORDER BY updated_at DESC LIMIT %s""",
                    (max(1, int(candidate_limit)),),
                )
                rows = cursor.fetchall()
            ranked = []
            for row in rows:
                if isinstance(row, dict):
                    record_id, layer, payload_json = row["record_id"], row["layer"], row["payload_json"]
                else:
                    record_id, layer, payload_json = row[0], row[1], row[2]
                try:
                    payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                raw_vec = payload.get("vector") or {}
                cand = {int(k): float(v) for k, v in raw_vec.items()}
                score = _cosine(query_vec, cand)
                if score > 0:
                    ranked.append({
                        "record_id": record_id,
                        "layer": layer,
                        "similarity": round(score, 6),
                        "tags": payload.get("tags"),
                        "content": payload.get("content"),
                        "source_ids": payload.get("source_ids") or [],
                    })
            ranked.sort(key=lambda item: item["similarity"], reverse=True)
            return {"results": ranked[:max(0, int(limit))], "count": min(len(ranked), max(0, int(limit))),
                    "execution_authority": False}
        except Exception as exc:
            return {"results": [], "mode": "ERROR", "error": str(exc)[:500],
                    "execution_authority": False}

    def maybe_mirror_working_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Best-effort mirror used by local writers when MIRROR is enabled."""
        if not self.config.enabled or not self.config.mirror_on_write:
            return {"mirrored": False, "reason": "mirror_on_write disabled",
                    "execution_authority": False}
        return self.upsert_working_memory(
            namespace=str(entry.get("namespace", "")),
            memory_key=str(entry.get("memory_key", "")),
            kind=str(entry.get("kind", "observation")),
            priority=int(entry.get("priority", 50) or 50),
            revision=int(entry.get("revision", 1) or 1),
            payload=entry.get("payload"),
            source_ids=entry.get("source_ids") or [],
            payload_hash=str(entry.get("payload_hash", "")),
            updated_at=str(entry.get("updated_at", "")),
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _table(self, name: str) -> str:
        return f"{self.config.table_prefix}{name}"

    def _resolve_driver(self):
        if self._connection_factory is not None:
            return self._connection_factory
        try:
            import pymysql  # type: ignore
            return pymysql
        except Exception as exc:
            self._driver_error = f"pymysql import failed: {exc}"
            return None

    def _connect(self):
        if self._connection_factory is not None:
            return _ConnectionGuard(self._connection_factory())
        driver = self._resolve_driver()
        if driver is None:
            raise RuntimeError(self._driver_error or "pymysql is not installed")
        if not self.config.credentials_complete():
            raise RuntimeError("RemoteMirror credentials incomplete")
        return _ConnectionGuard(driver.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            connect_timeout=self.config.connect_timeout,
            charset="utf8mb4",
            autocommit=False,
        ))

    def _ensure_schema(self, con) -> None:
        if self._schema_ready:
            return
        cursor = con.cursor()
        working = self._table(self.WORKING_TABLE)
        semantic = self._table(self.SEMANTIC_TABLE)
        sync_log = self._table(self.SYNC_LOG_TABLE)
        cursor.execute(
            f"""CREATE TABLE IF NOT EXISTS {working} (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  namespace VARCHAR(120) NOT NULL,
                  memory_key VARCHAR(200) NOT NULL,
                  kind VARCHAR(80) NOT NULL,
                  priority INT NOT NULL,
                  revision INT NOT NULL,
                  status VARCHAR(32) NOT NULL,
                  updated_at VARCHAR(64) NOT NULL,
                  source_ids MEDIUMTEXT NOT NULL,
                  payload MEDIUMTEXT NOT NULL,
                  payload_hash CHAR(64) NOT NULL,
                  UNIQUE KEY uq_ns_key (namespace, memory_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        cursor.execute(
            f"""CREATE TABLE IF NOT EXISTS {semantic} (
                  record_id VARCHAR(200) PRIMARY KEY,
                  layer VARCHAR(80) NOT NULL,
                  updated_at VARCHAR(64) NOT NULL,
                  payload_json MEDIUMTEXT NOT NULL,
                  payload_hash CHAR(64) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        cursor.execute(
            f"""CREATE TABLE IF NOT EXISTS {sync_log} (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  created_at VARCHAR(64) NOT NULL,
                  kind VARCHAR(80) NOT NULL,
                  source_key VARCHAR(240) NOT NULL,
                  status VARCHAR(32) NOT NULL,
                  detail VARCHAR(500) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        con.commit()
        self._schema_ready = True

    def _log_sync(self, con, kind: str, source_key: str, status: str, detail: str) -> None:
        cursor = con.cursor()
        cursor.execute(
            f"""INSERT INTO {self._table(self.SYNC_LOG_TABLE)}
                (created_at, kind, source_key, status, detail) VALUES (%s,%s,%s,%s,%s)""",
            (_utcnow(), kind[:80], source_key[:240], status[:32], detail[:500]),
        )


class _ConnectionGuard:
    """Context manager that works for both real pymysql and test fakes."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        close = getattr(self.connection, "close", None)
        if callable(close):
            close()
        return False


def _tokens(value: Any) -> List[str]:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    return [token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 2]


def _hash_vector(value: Any, dimensions: int = 256) -> Dict[int, float]:
    vector: Dict[int, float] = {}
    for token in _tokens(value):
        index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dimensions
        vector[index] = vector.get(index, 0.0) + 1.0
    return vector


def _cosine(left: Dict[int, float], right: Dict[int, float]) -> float:
    dot = sum(value * right.get(index, 0.0) for index, value in left.items())
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def default_memory(env: Optional[Dict[str, str]] = None) -> RemoteMemoryMirror:
    return RemoteMemoryMirror(RemoteMirrorConfig.from_env(env))


def maybe_mirror_working_memory(entry: Dict[str, Any], *, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Safe entry-point for local writers: never raises, never authorizes."""
    try:
        return default_memory(env).maybe_mirror_working_entry(entry)
    except Exception as exc:
        return {"mirrored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}


def maybe_mirror_semantic_record(
    *,
    record_id: str,
    layer: str,
    tags: Any,
    content: Any,
    source_ids: Optional[Iterable[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Safe semantic mirror entry-point: never raises, never authorizes."""
    try:
        memory = default_memory(env)
        if not memory.config.enabled or not memory.config.mirror_on_write:
            return {"mirrored": False, "reason": "mirror_on_write disabled",
                    "execution_authority": False}
        return memory.upsert_semantic_vector(
            record_id=record_id,
            layer=layer,
            tags=tags,
            content=content,
            source_ids=source_ids,
        )
    except Exception as exc:
        return {"mirrored": False, "mode": "ERROR", "error": str(exc)[:500],
                "execution_authority": False}

