import json

from journal_audit import audit
from journal_migration import migrate


def test_legacy_migration_is_idempotent(tmp_path):
    source = tmp_path / "legacy.json"
    db = tmp_path / "state.db"
    source.write_text(json.dumps([{
        "ticket": 7, "symbol": "EURUSD", "action": "buy", "volume": 0.1,
        "open_price": 1.1, "open_time": "2025-01-01T00:00:00",
        "decision": {"confidence": 0.7, "stop_loss": 1.09, "take_profit": 1.12},
        "status": "OPEN", "pnl": 0
    }]), encoding="utf-8")
    first = migrate(str(source), str(db))
    second = migrate(str(source), str(db))
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["skipped"] == 1
    assert audit(str(db))["passed"]
