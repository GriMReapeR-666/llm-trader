import pytest

from live_rollout_gate import CONFIRMATION, approve, require, revoke, status, verify_evidence


def test_live_rollout_is_explicit_expiring_and_risk_capped(tmp_path):
    path = str(tmp_path / "state.db")
    demo_evidence = tmp_path / "demo-report.txt"
    kill_evidence = tmp_path / "kill-switch-test.txt"
    demo_evidence.write_text("demo evidence", encoding="utf-8")
    kill_evidence.write_text("kill-switch evidence", encoding="utf-8")
    assert status(path)["active"] is False
    with pytest.raises(RuntimeError):
        require(path, risk_fraction=0.0005)
    with pytest.raises(ValueError):
        approve(path, operator="op", note="review", evidence=["demo-report"], confirmation="wrong")

    record = approve(path, operator="op", note="reviewed evidence", evidence=[str(demo_evidence), str(kill_evidence)],
                     risk_fraction_cap=0.001, expires_hours=1, confirmation=CONFIRMATION)
    assert record["active"] is True
    assert record["execution_authority"] is False
    assert status(path)["execution_authority"] is False
    assert require(path, risk_fraction=0.0005)["active"] is True
    with pytest.raises(RuntimeError):
        require(path, risk_fraction=0.002)

    revoked = revoke(path, operator="op", note="revoke for test")
    assert revoked["active"] is False
    assert status(path)["active"] is False


def test_live_approval_rejects_reference_only_evidence(tmp_path):
    with pytest.raises(ValueError, match="existing file evidence"):
        approve(str(tmp_path / "state.db"), operator="op", note="reviewed",
                evidence=["external-report-id"], expires_hours=1,
                confirmation=CONFIRMATION)


def test_live_status_fails_closed_on_malformed_persisted_record(tmp_path):
    path = str(tmp_path / "malformed-state.db")
    from trading_safety import TradingStore
    TradingStore(path).set_state("live_rollout_approval", "not-an-object")
    result = status(path)
    assert result["active"] is False
    assert result["approved"] is False
    assert result["execution_authority"] is False
    assert "malformed" in result["invalidation_reason"]


def test_live_approval_file_evidence_detects_drift(tmp_path):
    path = str(tmp_path / "state.db")
    evidence = tmp_path / "demo-report.json"
    evidence.write_text('{"sample": 100}', encoding="utf-8")
    approve(path, operator="op", note="reviewed evidence", evidence=[str(evidence)],
            expires_hours=1, confirmation=CONFIRMATION)
    checked = verify_evidence(path)
    assert checked["evidence_valid"] is True
    evidence.write_text('{"sample": 99}', encoding="utf-8")
    assert status(path)["active"] is False
    with pytest.raises(RuntimeError):
        require(path, risk_fraction=0.0005)
    drifted = verify_evidence(path)
    assert drifted["evidence_valid"] is False
    assert drifted["checks"][0]["status"] == "DRIFT"
    evidence.write_text('{"sample": 100}', encoding="utf-8")
    assert status(path)["active"] is False
    assert verify_evidence(path)["evidence_valid"] is True
