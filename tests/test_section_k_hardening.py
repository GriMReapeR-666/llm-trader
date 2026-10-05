import os
import pytest

from operations.security_audit import audit as security_audit
from operations.journal_scope import ensure_scope
from operations.journal_backup import create_backup, create_encrypted_backup, verify_backup
from core.trading_safety import TradingStore
from operations import incident_drills


def test_security_audit_preflight(tmp_path):
    report = security_audit(str(tmp_path))
    assert isinstance(report, dict)
    assert "credential_findings" in report
    assert report["execution_authority"] is False


def test_journal_scope_isolation(tmp_path):
    db_file = str(tmp_path / "demo_journal.db")

    # Initialize DEMO scope
    scope1 = ensure_scope(db_file, "DEMO", "account_110677135")
    assert scope1["mode"] == "DEMO"
    assert scope1["enforced"] is True

    # Re-initialization with same mode & account -> Succeeds
    scope1_re = ensure_scope(db_file, "DEMO", "account_110677135")
    assert scope1_re["mode"] == "DEMO"

    # Attempting to re-open as LIVE scope -> Fails with RuntimeError
    with pytest.raises(RuntimeError, match="journal scope mismatch"):
        ensure_scope(db_file, "LIVE", "account_110677135")


def test_encrypted_backup_and_restore(tmp_path):
    source_db = str(tmp_path / "source.db")
    store = TradingStore(source_db)
    store.set_state("test_key", {"data": "sample_trade_journal_entry"})

    # 1. Unencrypted backup
    backup_db = str(tmp_path / "source.backup.db")
    bk_res = create_backup(source_db, backup_db)
    assert bk_res["integrity"]["passed"] is True
    assert os.path.isfile(backup_db)

    # 2. AES-256 Encrypted backup
    enc_bk_res = create_encrypted_backup(source_db, passphrase="SuperSecretBackupPassphrase123!")
    assert enc_bk_res["integrity"]["passed"] is True
    assert os.path.isfile(enc_bk_res["backup"])
    
    # 3. Verify backup integrity
    verify_res = verify_backup(backup_db)
    assert verify_res["passed"] is True


def test_incident_recovery_drills():
    report = incident_drills.run()
    assert isinstance(report, dict)
    assert report["passed"] is True
    assert "checks" in report
    assert report["execution_authority"] is False
