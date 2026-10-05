import os
from datetime import datetime, timedelta, timezone

from operations.journal_backup import prune_backups


def test_backup_retention_is_dry_run_by_default_and_preserves_minimum(tmp_path):
    backups = []
    ages = (10, 20, 45)
    for name, age in zip(("one.backup.db", "two.backup.db", "three.encrypted.backup"), ages):
        path = tmp_path / name
        path.write_bytes(b"backup")
        modified = (datetime.now(timezone.utc) - timedelta(days=age)).timestamp()
        os.utime(path, (modified, modified))
        backups.append(path)
        (tmp_path / f"{name}.manifest.json").write_text("{}", encoding="utf-8")

    plan = prune_backups(str(tmp_path), older_than_days=30, keep_minimum=2)
    assert plan["dry_run"] is True
    assert len(plan["planned"]) == 1
    assert all(path.exists() for path in backups)

    result = prune_backups(str(tmp_path), older_than_days=30, keep_minimum=2, apply=True)
    assert result["deleted"] == [str(backups[2])]
    assert not backups[2].exists()
    assert not (tmp_path / "three.encrypted.backup.manifest.json").exists()
    assert backups[0].exists() and backups[1].exists()


def test_backup_retention_rejects_filesystem_root():
    root = os.path.abspath(os.path.splitdrive(os.getcwd())[0] + os.sep)
    try:
        prune_backups(root)
    except ValueError as exc:
        assert "filesystem root" in str(exc)
    else:
        raise AssertionError("filesystem-root pruning must be rejected")
