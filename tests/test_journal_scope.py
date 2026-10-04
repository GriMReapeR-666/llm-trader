import sqlite3

import pytest

from operations.journal_scope import ensure_scope, status
from core.trading_safety import TradingStore


def test_demo_and_live_scopes_require_account_identity(tmp_path):
    with pytest.raises(ValueError, match="account fingerprint"):
        ensure_scope(str(tmp_path / "demo.db"), "DEMO")
    with pytest.raises(ValueError, match="account fingerprint"):
        ensure_scope(str(tmp_path / "live.db"), "LIVE", "")


def test_existing_scope_cannot_be_reopened_without_matching_account(tmp_path):
    path = str(tmp_path / "demo.db")
    created = ensure_scope(path, "DEMO", "demo-account-1")
    assert created["enforced"] is True
    with pytest.raises(RuntimeError, match="scope mismatch"):
        ensure_scope(path, "DEMO", "demo-account-2")
    with pytest.raises(ValueError, match="account fingerprint"):
        ensure_scope(path, "DEMO", "")
    assert status(path)["status"] == "SCOPED"


def test_incomplete_existing_live_scope_is_invalid(tmp_path):
    path = str(tmp_path / "legacy-live.db")
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE journal_scope(
            id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
            account_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        con.execute("INSERT INTO journal_scope VALUES(1,'LIVE','','now','now')")
    assert status(path)["status"] == "INVALID"


def test_invalid_execution_scope_does_not_initialize_operational_tables(tmp_path, monkeypatch):
    # Hermetic: TradingStore falls back to os.getenv("MT5_LOGIN") when no
    # fingerprint is passed; a loaded .env would otherwise satisfy the DEMO
    # identity requirement and mask the fail-closed behavior under test.
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    path = tmp_path / "blocked-demo.db"
    with pytest.raises(ValueError, match="account fingerprint"):
        TradingStore(str(path), mode="DEMO")
    assert not path.exists()
