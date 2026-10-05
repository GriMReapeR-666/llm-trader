"""Regression tests: security_audit disk cache + readiness async prefetch.

The security scan walks the whole checkout and can take minutes on large or
OneDrive-backed roots, which dominated boot VALIDATE. Requirements pinned
here:

- A scanned result is persisted next to the audited root and reused by a
  fresh process (disk hit), with the age disclosed in ``report["cache"]``.
- Tampered or expired disk payloads are ignored (re-scan), and a cache hit
  without ``values_redacted`` is never trusted.
- ``readiness_report.build()`` overlaps the scan with the DB sources and
  reports ``PENDING_BACKGROUND`` (an explicit blocker) when the scan is not
  finished within the bounded wait — it must never look like a pass.
- A fast scan resolves normally with no pending blocker.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "operations")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from operations import security_audit as sa
from operations import readiness_report


@pytest.fixture(autouse=True)
def _restore_l1_cache():
    """Keep the module-level L1 cache from leaking between tests."""
    saved = (sa._audit_cache, sa._audit_cache_ts, sa._audit_cache_key)
    yield
    sa._audit_cache, sa._audit_cache_ts, sa._audit_cache_key = saved


def _seed_vault(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("MT5_PASSWORD=hunter2hunter2\n", encoding="utf-8")
    (tmp_path / "normal.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_disk_cache_written_then_reused_across_processes(tmp_path, monkeypatch):
    _seed_vault(tmp_path)
    r1 = sa.audit(tmp_path)
    assert r1["status"] == "REVIEW_REQUIRED"
    assert r1["cache"]["hit"] is False

    cache_file = tmp_path / sa.DISK_CACHE_NAME
    assert cache_file.exists()
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["report"]["status"] == "REVIEW_REQUIRED"

    # Simulate a fresh process: L1 empty, only the disk payload remains.
    monkeypatch.setattr(sa, "_audit_cache", None)
    r2 = sa.audit(tmp_path)
    assert r2["cache"]["hit"] is True
    assert r2["cache"]["disk"] is True
    assert r2["cache"]["age_seconds"] is not None
    assert r2["credential_findings"] == r1["credential_findings"]
    # L1 is warmed by the disk hit too (same-process repeat is instant).
    assert sa._audit_cache is r2


def test_disk_cache_rejects_tampered_or_expired_payloads(tmp_path, monkeypatch):
    _seed_vault(tmp_path)
    sa.audit(tmp_path)
    cache_file = tmp_path / sa.DISK_CACHE_NAME
    monkeypatch.setattr(sa, "_audit_cache", None)

    # Tampered: values_redacted stripped → must not be trusted.
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["report"].pop("values_redacted")
    payload["ts"] = time.time()
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    r_tampered = sa.audit(tmp_path)
    assert r_tampered["cache"]["hit"] is False

    # Expired: TTL forced to 0 → disk payload ignored even though fresh.
    monkeypatch.setattr(sa, "_audit_cache", None)
    monkeypatch.setenv("SECURITY_AUDIT_DISK_TTL_SECONDS", "0")
    r_expired = sa.audit(tmp_path)
    assert r_expired["cache"]["hit"] is False


_AUTH_OK = lambda: {"status": "AUTHORIZED", "probe": {"status": "AUTHORIZED"}}


def test_readiness_security_pending_is_fail_closed(tmp_path, monkeypatch):
    release = __import__("threading").Event()

    def hanging_audit(root, include_untracked=True):
        release.wait(timeout=30)
        return {"status": "CLEAR_FOR_OPERATOR_REVIEW", "values_redacted": True}

    monkeypatch.setattr(sa, "audit", hanging_audit)
    monkeypatch.setattr(sa, "_audit_cache", None)
    monkeypatch.setattr(readiness_report, "_SECURITY_WAIT_SECONDS", 0.5)

    result = readiness_report.build(str(tmp_path / "r.db"), auth_fn=_AUTH_OK)
    security = result["gates"]["security"]
    assert security["status"] == "PENDING_BACKGROUND"
    assert security["execution_authority"] is False
    assert result["status"] == "BLOCKED"
    assert any("PENDING_BACKGROUND" in b for b in result["blockers"])
    assert any("background" in a for a in result["next_actions"])
    release.set()


def test_readiness_security_resolves_when_scan_is_fast(tmp_path, monkeypatch):
    def instant_audit(root, include_untracked=True):
        time.sleep(0.05)
        return {
            "status": "CLEAR_FOR_OPERATOR_REVIEW",
            "values_redacted": True,
            "credential_findings": [],
        }

    monkeypatch.setattr(sa, "audit", instant_audit)
    monkeypatch.setattr(sa, "_audit_cache", None)
    monkeypatch.setattr(readiness_report, "_SECURITY_WAIT_SECONDS", 5.0)

    result = readiness_report.build(str(tmp_path / "r.db"), auth_fn=_AUTH_OK)
    security = result["gates"]["security"]
    assert security["status"] == "CLEAR_FOR_OPERATOR_REVIEW"
    assert not any("PENDING_BACKGROUND" in b for b in result["blockers"])


def test_readiness_pending_next_action_mentions_rerun(tmp_path, monkeypatch):
    release = __import__("threading").Event()

    def hanging_audit(root, include_untracked=True):
        release.wait(timeout=30)
        return {"status": "CLEAR_FOR_OPERATOR_REVIEW", "values_redacted": True}

    monkeypatch.setattr(sa, "audit", hanging_audit)
    monkeypatch.setattr(sa, "_audit_cache", None)
    monkeypatch.setattr(readiness_report, "_SECURITY_WAIT_SECONDS", 0.2)

    result = readiness_report.build(str(tmp_path / "r.db"), auth_fn=_AUTH_OK)
    actions = " | ".join(result["next_actions"])
    assert "rerun readiness" in actions.lower()
    release.set()
