import sqlite3

from journal_backup import (
    create_encrypted_backup,
    restore_encrypted_backup,
    verify_encrypted_backup,
)
from trading_safety import TradingStore


def test_encrypted_backup_verifies_and_restores_without_plaintext_copy(tmp_path):
    source = tmp_path / "source.db"
    encrypted = tmp_path / "journal.encrypted.backup"
    restored = tmp_path / "restored.db"
    TradingStore(str(source)).set_state("sentinel", "PLAINTEXT_MARKER")

    result = create_encrypted_backup(str(source), str(encrypted), passphrase="correct horse battery")
    assert result["encrypted"] is True
    assert b"PLAINTEXT_MARKER" not in encrypted.read_bytes()
    assert verify_encrypted_backup(str(encrypted), passphrase="wrong passphrase")["passed"] is False
    assert verify_encrypted_backup(str(encrypted), passphrase="correct horse battery")["passed"] is True

    restored_result = restore_encrypted_backup(
        str(encrypted), str(restored), passphrase="correct horse battery"
    )
    assert restored_result["passed"] is True
    with sqlite3.connect(restored) as con:
        assert con.execute("SELECT value FROM state WHERE key='sentinel'").fetchone()[0] == '"PLAINTEXT_MARKER"'
