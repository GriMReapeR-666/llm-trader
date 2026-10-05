import json
from types import SimpleNamespace

from operations.mt5_auth_diagnostics import classify_error, configuration_shape, probe_terminal


def test_configuration_shape_never_reads_values_into_report(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"default": "demo", "accounts": {
        "demo": {"login": 1, "password": "secret", "server": "broker", "type": "DEMO"}
    }}), encoding="utf-8")
    result = configuration_shape(str(path))
    assert result["status"] == "READY_SHAPE"
    assert result["accounts"][0]["required_fields_present"] is True
    assert "secret" not in str(result)
    assert "broker" not in str(result)


def test_auth_error_is_classified_without_repairing_it():
    assert classify_error((-6, "Terminal: Authorization failed")) == "AUTHORIZATION_FAILED_CHECK_LOGIN_PASSWORD_SERVER_OR_TERMINAL_SESSION"


def test_configuration_shape_accepts_environment_backed_secret(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"default": "demo", "accounts": {
        "demo": {"login": 1, "password_env": "MT5_PASSWORD", "server": "broker", "type": "DEMO"}
    }}), encoding="utf-8")
    result = configuration_shape(str(path))
    assert result["status"] == "READY_SHAPE"
    assert result["accounts"][0]["required_fields_present"] is True


def test_probe_terminal_reports_auth_failure_without_order_calls():
    class FakeMT5:
        def initialize(self, **kwargs):
            return False

        def last_error(self):
            return (-6, "Terminal: Authorization failed")

        def shutdown(self):
            return True

    result = probe_terminal(FakeMT5(), [None])
    assert result["status"] == "AUTHORIZATION_FAILED"
    assert result["execution_authority"] is False
    assert result["attempts"][0]["timeout_ms"] == 5000


def test_probe_terminal_bounds_requested_timeout():
    seen = []
    class FakeMT5:
        def initialize(self, **kwargs):
            seen.append(kwargs.get("timeout"))
            return False

        def last_error(self):
            return (-10005, "timeout")

        def shutdown(self):
            return True

    result = probe_terminal(FakeMT5(), [None], timeout_ms=999999)
    assert seen == [30000]
    assert result["attempts"][0]["timeout_ms"] == 30000
    assert result["status"] == "TERMINAL_UNAVAILABLE"


def test_successful_probe_redacts_account_login():
    class FakeMT5:
        def initialize(self, **kwargs):
            return True

        def last_error(self):
            return (0, "")

        def terminal_info(self):
            return SimpleNamespace(name="terminal", company="broker", trade_allowed=True)

        def account_info(self):
            return SimpleNamespace(login=123456, server="Demo", currency="USD", leverage=100)

        def shutdown(self):
            return True

    result = probe_terminal(FakeMT5(), [None])
    assert result["status"] == "AUTHORIZED"
    assert result["attempts"][0]["account"]["login"] == "REDACTED"
    assert "123456" not in str(result)
