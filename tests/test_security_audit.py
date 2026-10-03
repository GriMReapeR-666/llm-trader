from pathlib import Path

from security_audit import _git_history_risky_files, audit


def test_security_audit_redacts_values(tmp_path: Path):
    fake_key = "sk-" + "abcdefghijklmnopqrstuvwxyz"
    (tmp_path / ".env").write_text(
        "MT5_PASSWORD=super-secret-value\nOPENROUTER_API_KEY=" + fake_key + "\n",
        encoding="utf-8",
    )
    report = audit(tmp_path, include_untracked=True)
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["values_redacted"] is True
    assert report["rotation_completed"] is False
    assert any(item["path"] == ".env" for item in report["credential_findings"])
    rendered = str(report)
    assert "super-secret-value" not in rendered
    assert fake_key not in rendered


def test_security_audit_detects_hash_password_and_ignores_code_references(tmp_path: Path):
    (tmp_path / "config.ini").write_text("password=Q22w#e4R5\n", encoding="utf-8")
    (tmp_path / "runtime.py").write_text(
        'password = os.environ.get("MT5_PASSWORD", "")\n'
        'payload = {"password": password}\n',
        encoding="utf-8",
    )
    report = audit(tmp_path, include_untracked=True)
    assert any(item["path"] == "config.ini" for item in report["credential_findings"])
    assert not any(item["path"] == "runtime.py" for item in report["credential_findings"])


def test_security_audit_separates_fixture_findings(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text('payload = "password=synthetic-secret"\n', encoding="utf-8")
    report = audit(tmp_path, include_untracked=True)
    assert report["credential_findings"] == []
    assert any(Path(item["path"]).as_posix() == "tests/test_fixture.py" for item in report["non_production_findings"])
    assert report["status"] == "CLEAR_FOR_OPERATOR_REVIEW"


def test_security_audit_requires_trace_review(tmp_path: Path):
    (tmp_path / "l0_traces").mkdir()
    report = audit(tmp_path, include_untracked=True)
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["l0_trace_directories"] == ["l0_traces"]


def test_security_audit_reports_historical_risky_paths(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    class Result:
        stdout = b".env\nnotes.txt\nconfig.ini\n"

    def fake_run(args, **kwargs):
        assert args[0:4] == ["git", "-C", str(tmp_path), "log"]
        return Result()

    monkeypatch.setattr("security_audit.subprocess.run", fake_run)
    assert _git_history_risky_files(tmp_path) == [".env", "config.ini"]


def test_security_audit_ignores_explicit_redaction_markers(tmp_path: Path):
    (tmp_path / "redacted.py").write_text(
        'payload = {"password": "[REDACTED]"}\n', encoding="utf-8"
    )
    report = audit(tmp_path, include_untracked=True)
    assert report["credential_findings"] == []
