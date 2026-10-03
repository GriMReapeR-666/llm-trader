from research_influence_gate import MIN_SAMPLE, evidence_fingerprint, status, approve, revoke
from trading_safety import TradingStore


def test_research_influence_is_fail_closed_without_sample_or_approval(tmp_path):
    path = str(tmp_path / "research.db")
    report = status(path, model_kind="rl")
    assert report["allowed"] is False
    assert report["status"] == "BLOCKED"
    assert report["execution_authority"] is False
    assert evidence_fingerprint(path)


def test_approval_requires_oos_and_hash(tmp_path):
    path = str(tmp_path / "approval.db")
    try:
        approve(path, operator="operator", evidence_hash="0" * 64,
                oos_validated=False, minimum_sample=MIN_SAMPLE)
    except ValueError as exc:
        assert "oos_validated" in str(exc)
    else:
        raise AssertionError("approval without explicit OOS validation must fail")
    blocked = approve(path, operator="operator", evidence_hash="0" * 64,
                      oos_validated=True, minimum_sample=MIN_SAMPLE)
    assert blocked["status"] == "BLOCKED"


def test_malformed_persisted_approval_fails_closed(tmp_path):
    path = str(tmp_path / "malformed.db")
    store = TradingStore(path)
    store.set_state("research_influence_approval", {"status": "APPROVED", "minimum_sample": "bad"})
    report = status(path)
    assert report["allowed"] is False
    assert report["status"] == "BLOCKED"


def test_approval_minimum_sample_cannot_be_lowered_after_recording(tmp_path):
    path = str(tmp_path / "sample.db")
    store = TradingStore(path)
    store.set_state("research_influence_approval", {
        "status": "APPROVED", "model_kind": "all", "minimum_sample": 1000,
        "oos_validated": True, "evidence_hash": "0" * 64,
        "expires_at": "2999-01-01T00:00:00+00:00",
    })
    report = status(path)
    assert report["allowed"] is False
    assert any("approved minimum" in reason for reason in report["reasons"])


def test_revoke_is_explicit_and_non_authoritative(tmp_path):
    path = str(tmp_path / "revoke.db")
    result = revoke(path, operator="operator", note="test")
    assert result["status"] == "REVOKED"
    assert result["execution_authority"] is False
