import pytest

from mt5_trader import authenticated_account_fingerprint


def test_paper_and_research_do_not_require_broker_identity():
    assert authenticated_account_fingerprint("PAPER", {}) == ""
    assert authenticated_account_fingerprint("RESEARCH", {}) == ""


@pytest.mark.parametrize("mode", ["DEMO", "LIVE"])
def test_execution_scope_requires_identity_returned_by_mt5(mode):
    with pytest.raises(RuntimeError, match="account identity"):
        authenticated_account_fingerprint(mode, {})
    with pytest.raises(RuntimeError, match="account identity"):
        authenticated_account_fingerprint(mode, {"login": 99023225})
    assert authenticated_account_fingerprint(mode, {"login": 99023225, "server": "Broker-Demo"}) == "99023225|Broker-Demo"


def test_unknown_scope_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        authenticated_account_fingerprint("UNKNOWN", {})
