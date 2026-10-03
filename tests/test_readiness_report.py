from pathlib import Path

from readiness_report import build
from trading_safety import TradingStore


def test_readiness_report_is_fail_closed_and_read_only(tmp_path: Path):
    path = str(tmp_path / "readiness.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {"execution_authority": False, "status": "AUTHORIZATION_FAILED"})
    assert result["status"] == "BLOCKED"
    assert result["execution_authority"] is False
    assert result["blockers"]
    assert "MT5 terminal/account authorization is not verified" in result["blockers"]
    assert "live_rollout" in result["gates"]
    assert "tiny-live rollout approval is absent, expired, or evidence-invalid" in result["blockers"]
    assert "security" in result["gates"]


def test_authorized_diagnostic_does_not_grant_execution_authority(tmp_path: Path):
    path = str(tmp_path / "authorized.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {
        "execution_authority": False,
        "status": "AUTHORIZED",
        "probe": {"status": "AUTHORIZED"},
    })
    assert "MT5 terminal/account authorization is not verified" not in result["blockers"]
    assert result["execution_authority"] is False


def test_readiness_report_converts_auth_exception_to_blocked_evidence(tmp_path: Path):
    def broken_auth():
        raise RuntimeError("simulated terminal outage")

    result = build(str(tmp_path / "auth-error.db"), auth_fn=broken_auth)
    assert result["status"] == "BLOCKED"
    assert result["execution_authority"] is False
    assert result["gates"]["authorization"]["status"] == "ERROR"
    assert "authorization" in " ".join(result["blockers"])


def test_readiness_report_converts_malformed_preflight_to_blocked_gate(tmp_path: Path):
    path = str(tmp_path / "malformed-preflight.db")
    store = TradingStore(path)
    store.set_state("broker_preflight", {"report": "not-an-object"})
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    assert result["status"] == "BLOCKED"
    assert result["gates"]["broker_preflight"]["validation"]["valid"] is False
    assert "broker preflight" in " ".join(result["blockers"])


def test_readiness_redacts_broker_login_from_output(tmp_path: Path):
    path = str(tmp_path / "redacted-preflight.db")
    store = TradingStore(path)
    store.set_state("broker_preflight", {"report": {"account": {"login": 987654, "server": "Demo"}}})
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    rendered = str(result)
    assert "987654" not in rendered
    assert result["gates"]["broker_preflight"]["envelope"]["report"]["account"]["login"] == "REDACTED"


# ---------------------------------------------------------------------------
# S.Ξ trial-ledger chain-integrity gate
# ---------------------------------------------------------------------------
def _chain_db(tmp_path: Path, name: str = "chain.db") -> str:
    """Build a small intact ledger DB for gate tests."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    from trial_ledger import TrialLedger
    db = str(tmp_path / name)
    led = TrialLedger(db_path=db)
    led.record_trial(strategy_id="t", sharpe=0.5, n_observations=100)
    return db


def test_chain_gate_ok_when_both_ledgers_intact(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    from trial_ledger import verify_chain_readonly as main_v
    from strategy_trial_ledger import verify_chain_readonly as cpcv_v

    main_db = _chain_db(tmp_path, "main.db")
    cpcv_db = _chain_db(tmp_path, "cpcv.db")
    monkeypatch.setattr("readiness_report._verify_chain_main", lambda: main_v(main_db))
    monkeypatch.setattr("readiness_report._verify_chain_cpcv", lambda: cpcv_v(cpcv_db))

    path = str(tmp_path / "r.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    gate = result["gates"]["trial_ledger_chain"]
    assert gate["status"] == "OK"
    assert gate["valid"] is True
    assert gate["ledgers"] == {"trial_ledger": "OK", "strategy_trial_ledger": "OK"}
    # No chain blocker appended
    assert not any("hash chain" in b for b in result["blockers"])


def test_chain_gate_broken_ledger_is_a_blocker(tmp_path, monkeypatch):
    import sqlite3
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    from trial_ledger import verify_chain_readonly as main_v
    from strategy_trial_ledger import verify_chain_readonly as cpcv_v

    main_db = _chain_db(tmp_path, "broken.db")
    cpcv_db = _chain_db(tmp_path, "ok.db")
    # Tamper: rewrite a payload value in place (the alarm scenario)
    con = sqlite3.connect(main_db)
    con.execute("UPDATE trials SET sharpe = 99.0")
    con.commit()
    con.close()

    monkeypatch.setattr("readiness_report._verify_chain_main", lambda: main_v(main_db))
    monkeypatch.setattr("readiness_report._verify_chain_cpcv", lambda: cpcv_v(cpcv_db))

    path = str(tmp_path / "r2.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    gate = result["gates"]["trial_ledger_chain"]
    assert gate["status"] == "COMPROMISED"
    assert gate["valid"] is False
    assert any("hash chain" in b for b in result["blockers"])
    # Blocked-with-next-action, never execution authority
    assert result["status"] == "BLOCKED"
    assert result["execution_authority"] is False


def test_chain_gate_absent_ledger_is_not_a_blocker(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    from trial_ledger import verify_chain_readonly as main_v
    from strategy_trial_ledger import verify_chain_readonly as cpcv_v

    missing = str(tmp_path / "does_not_exist.db")
    monkeypatch.setattr("readiness_report._verify_chain_main", lambda: main_v(missing))
    monkeypatch.setattr("readiness_report._verify_chain_cpcv", lambda: cpcv_v(missing))

    path = str(tmp_path / "r3.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    gate = result["gates"]["trial_ledger_chain"]
    assert gate["status"] == "OK"  # ABSENT both sides → valid
    assert gate["ledgers"] == {"trial_ledger": "ABSENT", "strategy_trial_ledger": "ABSENT"}
    assert not any("hash chain" in b for b in result["blockers"])


def test_chain_gate_source_failure_fails_closed(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("disk failure")

    monkeypatch.setattr("readiness_report._verify_chain_main", broken)
    monkeypatch.setattr("readiness_report._verify_chain_cpcv", broken)

    path = str(tmp_path / "r4.db")
    TradingStore(path)
    result = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})
    gate = result["gates"]["trial_ledger_chain"]
    # _safe_source converts the crash into ERROR evidence, which is not valid
    assert gate.get("valid") is not True
    assert any("hash chain" in b for b in result["blockers"])


def test_verify_chain_readonly_does_not_mutate_db(tmp_path):
    """The verifier must not mutate the evidence file.

    SQLite needs the -shm/-wal coordination files to read a WAL-mode DB
    even through a read-only connection (and cannot delete them on close
    without write access), so ephemeral siblings may appear. The guarantee
    that matters for tamper-evidence: the DB file itself is byte-identical.
    """
    import hashlib
    import os
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    from trial_ledger import verify_chain_readonly

    db = _chain_db(tmp_path, "ro.db")
    with open(db, "rb") as f:
        before_hash = hashlib.sha256(f.read()).hexdigest()
    report = verify_chain_readonly(db)
    assert report["status"] == "OK"
    with open(db, "rb") as f:
        after_hash = hashlib.sha256(f.read()).hexdigest()
    assert after_hash == before_hash, "read-only verification mutated the ledger"
    # Any files appearing are only ephemeral coordination siblings (WAL
    # or the ledger's cross-process lockfile created during setup)
    extra = {p for p in os.listdir(tmp_path) if p.startswith("ro.db")} - {"ro.db"}
    assert extra <= {"ro.db-wal", "ro.db-shm", "ro.db.lock"}
