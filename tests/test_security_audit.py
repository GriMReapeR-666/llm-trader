from pathlib import Path

from operations.security_audit import _git_history_risky_files, audit


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
    (tmp_path / "config.ini").write_text("password=Q22w#e4R5\n", encoding="utf-8")  # secret-scan: allow
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
    (tests / "test_fixture.py").write_text('payload = "password=synthetic-secret"\n', encoding="utf-8")  # secret-scan: allow
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

    monkeypatch.setattr("operations.security_audit.subprocess.run", fake_run)
    assert _git_history_risky_files(tmp_path) == [".env", "config.ini"]


def test_security_audit_ignores_explicit_redaction_markers(tmp_path: Path):
    (tmp_path / "redacted.py").write_text(
        'payload = {"password": "[REDACTED]"}\n', encoding="utf-8"
    )
    report = audit(tmp_path, include_untracked=True)
    assert report["credential_findings"] == []


def _git_repo(tmp_path: Path) -> Path:
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _track(repo: Path, name: str, text: str) -> None:
    import subprocess
    (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)


def test_tracked_only_audit_scans_tracked_files(tmp_path: Path):
    # Regression: include_untracked=False used to scan zero files.
    from operations import security_audit
    repo = _git_repo(tmp_path)
    _track(repo, "leak.py", 'KEY = "' + "rm-" + "a" * 32 + '"\n')
    scanned = list(security_audit._iter_files(repo.resolve(), include_untracked=False))
    assert [p.name for p in scanned] == ["leak.py"]
    report = audit(repo, include_untracked=False)
    assert any(item["path"] == "leak.py" for item in report["credential_findings"])


def test_allow_marker_suppresses_only_its_own_line(tmp_path: Path):
    from operations import security_audit
    fake = "sk-" + "b" * 30
    path = tmp_path / "fixture.py"
    path.write_text(
        f'ok = "{fake}"  # {security_audit.ALLOW_MARKER}\nleak = "{fake}"\n', encoding="utf-8"
    )
    assert security_audit._scan_file(path) == ["OPENAI_LIKE_KEY"]
    path.write_text(f'ok = "{fake}"  # {security_audit.ALLOW_MARKER}\n', encoding="utf-8")
    assert security_audit._scan_file(path) == []


def test_cli_fails_on_findings_and_never_prints_values(tmp_path: Path, capsys):
    from operations import security_audit
    fake = "rm-" + "c" * 32
    leak = tmp_path / "leak.txt"
    leak.write_text(f"ROUTESME_KEY={fake}\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing here\n", encoding="utf-8")
    assert security_audit.main(["--paths", str(clean), "--fail-on-findings"]) == 0
    assert security_audit.main(["--paths", str(leak), "--fail-on-findings"]) == 1
    out = capsys.readouterr()
    assert fake not in out.out + out.err
