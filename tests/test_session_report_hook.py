"""Tests for the post-session scheduler-report hook (scripts/session_report_hook.py)
and its wiring into the trader's graceful-shutdown path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import session_report_hook as hook


@pytest.fixture()
def env_report_on(monkeypatch):
    """Force the hook on inside pytest (the default here is inert)."""
    monkeypatch.setenv("LLM_TRADER_POST_SESSION_REPORT", "1")


def _fake_report_script(tmp_path: Path, monkeypatch, payload: dict,
                        exit_code: int = 0) -> Path:
    """A stub report 'script' that writes the payload as its JSON output.

    The destination is passed through an env var, NOT ``sys.argv[1]``:
    ``run_and_archive`` invokes the script as
    ``[..., "-X", "utf8", script, "--db", db_path]`` with ``cwd=ROOT``, so
    argv[1] is the literal string ``"--db"`` and writing there dropped a file
    named ``--db`` in the repo root on every run.
    """
    out = tmp_path / "fake_report_out.json"
    fake = tmp_path / "fake_tuning_report.py"
    fake.write_text(
        "import json, os\n"
        f"json.dump({payload!r}, open(os.environ['FAKE_REPORT_OUT'], 'w'))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_REPORT_OUT", str(out))
    monkeypatch.setattr(hook, "REPORT_SCRIPT", fake)
    return fake


def test_hook_archives_report_with_timestamp(env_report_on, tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "scheduler_tuning_report.json").write_text(
        json.dumps({"live_session": True, "duty_cycle_last_session": {}}),
        encoding="utf-8")
    _fake_report_script(tmp_path, monkeypatch, {"ok": True})

    dest = hook.run_and_archive(artifacts_dir=artifacts)
    assert dest is not None and dest.exists() and dest.parent == artifacts
    assert dest.name.startswith("scheduler_tuning_report_2") \
        and dest.name.endswith("Z.json")
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data == {"live_session": True, "duty_cycle_last_session": {}}


def test_hook_noop_when_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_TRADER_POST_SESSION_REPORT", "0")
    _fake_report_script(tmp_path, monkeypatch, {"ok": True})
    assert hook.run_and_archive(artifacts_dir=tmp_path) is None


def test_hook_is_inert_under_pytest_even_with_a_foreign_argv0(monkeypatch,
                                                               tmp_path):
    """Regression: the test-inert guard must not depend on ``sys.argv[0]``.

    The original check was ``"pytest" in Path(sys.argv[0]).name``.  That is
    False for ``python -m pytest`` (argv[0] is ``pytest/__main__.py``) and
    for xdist workers (argv[0] is ``-c``), so the hook stayed armed, spawned
    its report subprocess and rewrote the shared
    artifacts/scheduler_tuning_report.json on every test-process shutdown.
    Both argv shapes are simulated here.
    """
    monkeypatch.delenv("LLM_TRADER_POST_SESSION_REPORT", raising=False)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "scheduler_tuning_report.json").write_text(
        json.dumps({"live_session": True}), encoding="utf-8")
    _fake_report_script(tmp_path, monkeypatch, {"ok": True})

    for argv0 in ("__main__.py", "-c", "pytest.exe"):
        monkeypatch.setattr(sys, "argv", [argv0])
        assert hook.run_and_archive(artifacts_dir=artifacts) is None, (
            "hook ran under pytest with sys.argv[0]=%r" % argv0)
        assert list(artifacts.glob("scheduler_tuning_report_2*.json")) == [], (
            "hook archived into %s with sys.argv[0]=%r" % (artifacts, argv0))

    # The env var is still the documented opt-in and must override the inert
    # default.
    monkeypatch.setenv("LLM_TRADER_POST_SESSION_REPORT", "1")
    monkeypatch.setattr(sys, "argv", ["__main__.py"])
    dest = hook.run_and_archive(artifacts_dir=artifacts)
    assert dest is not None and dest.exists()


def test_hook_fail_soft_when_report_fails(env_report_on, tmp_path, monkeypatch):
    _fake_report_script(tmp_path, monkeypatch, {}, exit_code=3)
    assert hook.run_and_archive(artifacts_dir=tmp_path) is None


def test_hook_fail_soft_when_report_missing_output(env_report_on, tmp_path, monkeypatch):
    _fake_report_script(tmp_path, monkeypatch, {"ok": True})
    # The stub writes argv[1]... but we point the archive at a dir where the
    # report JSON never lands, exercising the missing-artifact guard.
    (tmp_path / "scheduler_tuning_report.json").write_text("{}", encoding="utf-8")
    dest = hook.run_and_archive(artifacts_dir=tmp_path / "elsewhere")
    assert dest is None


def test_hook_prunes_old_archives(env_report_on, tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "scheduler_tuning_report.json").write_text("{}", encoding="utf-8")
    _fake_report_script(tmp_path, monkeypatch, {"ok": True})
    for i in range(hook.KEEP_ARCHIVES + 8):
        (artifacts / f"scheduler_tuning_report_20260101T0000{i:04d}Z.json").write_text(
            "{}", encoding="utf-8")
    dest = hook.run_and_archive(artifacts_dir=artifacts)
    assert dest is not None
    remaining = list(artifacts.glob("scheduler_tuning_report_2*.json"))
    assert len(remaining) == hook.KEEP_ARCHIVES
    # The newest (this run's archive) survives; the oldest were pruned.
    assert dest in remaining


def test_shutdown_path_invokes_hook(monkeypatch, tmp_path, caplog):
    """The graceful-shutdown path calls the hook and logs the archive."""
    import logging
    from core.crash_recovery import CrashRecoveryManager
    calls = []

    class _HookStub:
        @staticmethod
        def run_and_archive(*a, **k):
            calls.append((a, k))
            return tmp_path / "scheduler_tuning_report_20260927T120000Z.json"

    monkeypatch.setitem(sys.modules, "scripts.session_report_hook", _HookStub)
    mgr = CrashRecoveryManager.__new__(CrashRecoveryManager)
    mgr._shutdown_in_progress = False
    mgr._shutdown_snapshot_enabled = False
    mgr._current_cycle = 0
    mgr._shutdown_event = __import__("threading").Event()
    with caplog.at_level(logging.INFO, logger="CrashRecovery"):
        mgr.shutdown()
    assert len(calls) == 1
    assert mgr._shutdown_event.is_set()
    assert any("Post-session scheduler report archived" in r.message
               for r in caplog.records)
    # Idempotent: a second call is a no-op.
    mgr.shutdown()
    assert len(calls) == 1


def test_shutdown_hook_failure_does_not_block_shutdown(monkeypatch):
    """An exploding hook must not prevent shutdown from completing."""
    from core.crash_recovery import CrashRecoveryManager

    class _Boom:
        @staticmethod
        def run_and_archive(*a, **k):
            raise RuntimeError("hook exploded")

    monkeypatch.setitem(sys.modules, "scripts.session_report_hook", _Boom)
    mgr = CrashRecoveryManager.__new__(CrashRecoveryManager)
    mgr._shutdown_in_progress = False
    mgr._shutdown_snapshot_enabled = False
    mgr._current_cycle = 0
    mgr._shutdown_event = __import__("threading").Event()
    mgr.shutdown()          # must not raise
    assert mgr._shutdown_event.is_set()
