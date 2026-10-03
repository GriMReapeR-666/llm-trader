import json

import pytest

from mt5_trader import AccountManager


def test_account_registration_stores_only_password_environment_reference(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"default": None, "accounts": {}}), encoding="utf-8")
    monkeypatch.setattr(AccountManager, "FILE_PATH", str(path))

    AccountManager.add_account("demo", 123, "MT5_PASSWORD", "MetaQuotes-Demo", "DEMO")

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["accounts"]["demo"]
    assert record["password_env"] == "MT5_PASSWORD"
    assert "password" not in record


def test_account_registration_rejects_non_environment_password_reference(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"default": None, "accounts": {}}), encoding="utf-8")
    monkeypatch.setattr(AccountManager, "FILE_PATH", str(path))

    with pytest.raises(ValueError):
        AccountManager.add_account("demo", 123, "plain-secret", "MetaQuotes-Demo", "DEMO")
