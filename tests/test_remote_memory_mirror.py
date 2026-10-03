"""Unit tests for optional remote memory mirror (no live cloud)."""
from __future__ import annotations

import os
import tempfile
import gc

from remote_memory_mirror import (
    RemoteMemoryMirror,
    RemoteMirrorConfig,
    default_memory,
    maybe_mirror_semantic_record,
    maybe_mirror_working_memory,
)


class _FakeCursor:
    def __init__(self, store: "_FakeStore"):
        self.store = store
        self._last_rows = []

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        params = params or ()
        upper = text.upper()
        if upper.startswith("CREATE TABLE"):
            return
        if upper.startswith("SELECT 1"):
            self._last_rows = [(1,)]
            return
        if "INSERT INTO" in upper and "WORKING_MEMORY" in upper.replace("`", ""):
            # Parse table via store key
            ns, key, kind, priority, revision, updated_at, source_ids, payload, payload_hash = params
            row = {
                "namespace": ns, "memory_key": key, "kind": kind, "priority": priority,
                "revision": revision, "status": "ACTIVE", "updated_at": updated_at,
                "source_ids": source_ids, "payload": payload, "payload_hash": payload_hash,
            }
            self.store.working[(ns, key)] = row
            self.store.sync_log.append(("working_memory", f"{ns}:{key}", "OK"))
            return
        if "INSERT INTO" in upper and "SEMANTIC_VECTORS" in upper.replace("`", ""):
            record_id, layer, updated_at, payload_json, payload_hash = params
            self.store.semantic[record_id] = {
                "record_id": record_id, "layer": layer, "updated_at": updated_at,
                "payload_json": payload_json, "payload_hash": payload_hash,
            }
            self.store.sync_log.append(("semantic_vector", record_id, "OK"))
            return
        if "INSERT INTO" in upper and "SYNC_LOG" in upper.replace("`", ""):
            return
        if "FROM" in upper and "WORKING_MEMORY" in upper.replace("`", ""):
            namespace = params[0]
            limit = int(params[1])
            rows = [row for (ns, _), row in self.store.working.items() if ns == namespace]
            rows.sort(key=lambda item: (item["priority"], item["updated_at"]), reverse=True)
            self._last_rows = [
                (r["namespace"], r["memory_key"], r["kind"], r["priority"], r["revision"],
                 r["status"], r["updated_at"], r["source_ids"], r["payload"], r["payload_hash"])
                for r in rows[:limit]
            ]
            return
        if "FROM" in upper and "SEMANTIC_VECTORS" in upper.replace("`", ""):
            limit = int(params[0])
            rows = list(self.store.semantic.values())
            rows.sort(key=lambda item: item["updated_at"], reverse=True)
            self._last_rows = [(r["record_id"], r["layer"], r["payload_json"]) for r in rows[:limit]]
            return
        raise AssertionError(f"unexpected SQL in fake cursor: {text}")

    def fetchone(self):
        return self._last_rows[0] if self._last_rows else None

    def fetchall(self):
        return list(self._last_rows)


class _FakeConnection:
    def __init__(self, store: "_FakeStore"):
        self.store = store
        self.closed = False
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class _FakeStore:
    def __init__(self):
        self.working = {}
        self.semantic = {}
        self.sync_log = []


def _enabled_env(**extra):
    base = {
        "REMOTE_MIRROR_ENABLED": "true",
        "REMOTE_MIRROR_ON_WRITE": "true",
        "REMOTE_MIRROR_HOST": "remote.example.com",
        "REMOTE_MIRROR_PORT": "3306",
        "REMOTE_MIRROR_USER": "trader",
        "REMOTE_MIRROR_PASSWORD": "secret",
        "REMOTE_MIRROR_DATABASE": "agent_memory",
        "REMOTE_MIRROR_TABLE_PREFIX": "llm_trader_",
    }
    base.update(extra)
    return base


def test_disabled_by_default_status_is_honest():
    memory = default_memory({})
    status = memory.status()
    assert status["mode"] == "DISABLED"
    assert status["ready"] is False
    assert status["primary_store"] == "local_sqlite"
    assert status["execution_authority"] is False


def test_enabled_without_credentials_reports_missing():
    memory = default_memory({
        "REMOTE_MIRROR_ENABLED": "true",
        "REMOTE_MIRROR_HOST": "only-host",
    })
    status = memory.status()
    assert status["mode"] == "MISSING_CREDENTIALS"
    assert status["ready"] is False
    assert memory.upsert_working_memory(
        namespace="ns", memory_key="k", kind="observation", priority=1, revision=1, payload={"a": 1},
    )["mirrored"] is False


def test_missing_driver_without_factory_is_fail_closed(monkeypatch):
    cfg = RemoteMirrorConfig.from_env(_enabled_env())
    memory = RemoteMemoryMirror(cfg)
    memory._connection_factory = None
    memory._driver_error = "pymysql is not installed"
    monkeypatch.setattr(memory, "_resolve_driver", lambda: None)
    status = memory.status()
    assert status["mode"] == "MISSING_DRIVER"
    assert status["ready"] is False
    denied = memory.upsert_working_memory(
        namespace="ns", memory_key="k", kind="obs", priority=1, revision=1, payload={"x": 1},
    )
    assert denied["mirrored"] is False
    assert denied["execution_authority"] is False


def test_upsert_and_fetch_working_memory_with_injected_connection():
    store = _FakeStore()
    memory = RemoteMemoryMirror(
        RemoteMirrorConfig.from_env(_enabled_env()),
        connection_factory=lambda: _FakeConnection(store),
    )
    status = memory.status()
    assert status["ready"] is True
    assert status["execution_authority"] is False

    result = memory.upsert_working_memory(
        namespace="trading:cycle",
        memory_key="position",
        kind="position_event",
        priority=90,
        revision=2,
        payload={"symbol": "XAUUSD", "side": "BUY"},
        source_ids=["evt-1"],
    )
    assert result["mirrored"] is True
    assert result["execution_authority"] is False
    assert ("trading:cycle", "position") in store.working

    fetched = memory.fetch_working_memory("trading:cycle")
    assert fetched["count"] == 1
    assert fetched["items"][0]["memory_key"] == "position"
    assert fetched["items"][0]["payload"]["symbol"] == "XAUUSD"
    assert fetched["execution_authority"] is False


def test_semantic_upsert_and_search_with_injected_connection():
    store = _FakeStore()
    memory = RemoteMemoryMirror(
        RemoteMirrorConfig.from_env(_enabled_env()),
        connection_factory=lambda: _FakeConnection(store),
    )
    mirrored = memory.upsert_semantic_vector(
        record_id="narrative:1",
        layer="narrative",
        tags={"symbol": "EURUSD", "session": "LONDON"},
        content="fade london open spike",
        source_ids=["src-9"],
    )
    assert mirrored["mirrored"] is True
    hits = memory.search_semantic({"symbol": "EURUSD", "session": "LONDON", "content": "london open"})
    assert hits["count"] >= 1
    assert hits["results"][0]["record_id"] == "narrative:1"
    assert hits["execution_authority"] is False


def test_maybe_mirror_respects_mirror_flag():
    enabled_no_mirror = _enabled_env(REMOTE_MIRROR_ON_WRITE="false")
    result = maybe_mirror_working_memory(
        {"namespace": "ns", "memory_key": "k", "kind": "obs", "priority": 1,
         "revision": 1, "payload": {"x": 1}},
        env=enabled_no_mirror,
    )
    assert result["mirrored"] is False
    assert "mirror_on_write" in result["reason"]

    semantic = maybe_mirror_semantic_record(
        record_id="verified:1", layer="verified", tags={"a": 1}, content="lesson",
        env=enabled_no_mirror,
    )
    assert semantic["mirrored"] is False


def test_probe_ok_with_fake_connection():
    store = _FakeStore()
    memory = RemoteMemoryMirror(
        RemoteMirrorConfig.from_env(_enabled_env()),
        connection_factory=lambda: _FakeConnection(store),
    )
    probed = memory.probe()
    assert probed["probe"] == "OK"
    assert probed["schema_ready"] is True
    assert probed["execution_authority"] is False


def test_agent_operations_local_write_succeeds_when_remote_disabled():
    from agent_operations import AgentOperations

    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        ops = AgentOperations(db_path=path)
        result = ops.put_working_memory("ns", "k1", {"hello": "world"}, kind="observation", priority=40)
        assert result["execution_authority"] is False
        assert result["remote_mirror"]["mirrored"] is False
        ctx = ops.working_memory_context("ns")
        assert ctx["count"] == 1
    finally:
        gc.collect()
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_agent_operations_mirrors_when_configured(monkeypatch):
    from agent_operations import AgentOperations

    store = _FakeStore()
    memory = RemoteMemoryMirror(
        RemoteMirrorConfig.from_env(_enabled_env()),
        connection_factory=lambda: _FakeConnection(store),
    )

    def _fake_maybe(entry, *, env=None):
        return memory.maybe_mirror_working_entry(entry)

    monkeypatch.setattr("remote_memory_mirror.maybe_mirror_working_memory", _fake_maybe)

    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        ops = AgentOperations(db_path=path)
        result = ops.put_working_memory("ns", "mirrored_key", {"v": 3}, kind="observation", priority=55)
        assert result["remote_mirror"]["mirrored"] is True
        assert ("ns", "mirrored_key") in store.working
        # Local authority still present regardless of mirror.
        assert result["execution_authority"] is False
        assert ops.working_memory_context("ns")["count"] == 1
    finally:
        gc.collect()
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_config_accepts_memory_aliases():
    cfg = RemoteMirrorConfig.from_env({
        "REMOTE_MIRROR_ENABLED": "1",
        "REMOTE_MIRROR_HOST": "h",
        "REMOTE_MIRROR_USER": "u",
        "REMOTE_MIRROR_PASSWORD": "p",
        "REMOTE_MIRROR_DATABASE": "d",
        "REMOTE_MIRROR_PORT": "3307",
        "REMOTE_MIRROR_ON_WRITE": "yes",
    })
    assert cfg.enabled is True
    assert cfg.host == "h"
    assert cfg.port == 3307
    assert cfg.mirror_on_write is True
    assert cfg.credentials_complete() is True
