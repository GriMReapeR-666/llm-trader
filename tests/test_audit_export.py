import json

from agent_operations import AgentOperations
from journal_audit import write_audit_export
from research_runs import record
from trading_safety import TradingStore


def test_combined_audit_export_contains_operational_evidence(tmp_path):
    db = tmp_path / "journal.db"
    TradingStore(str(db))
    ops = AgentOperations(str(db))
    ops.event("test", "export_event", {"safe": True})
    ops.store_snapshot("candle", {"close": 1.2}, symbol="EURUSD", timeframe="M15")
    record(str(db), "baseline", {"status": "INSUFFICIENT_DATA", "dataset": {"sample_size": 0}})
    output = tmp_path / "audit.json"

    report = write_audit_export(str(db), str(output))
    assert report["execution_authority"] is False
    assert report["operational"]["counts"]["operational_events"] >= 1
    assert report["operational"]["counts"]["data_snapshots"] >= 1
    assert report["operational"]["integrity"]["operational_events"]["passed"] is True
    assert report["operational"]["integrity"]["data_snapshots"]["passed"] is True
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["operational"]["execution_authority"] is False
    assert saved["research_validation_runs"][0]["execution_authority"] is False
    assert "memory_quality" in saved
    assert saved["memory_quality"]["retrieval_count"] == 0
    assert saved["limitation"]


def test_audit_checks_research_ledger_integrity(tmp_path):
    db = tmp_path / "ledger-audit.db"
    TradingStore(str(db))
    record(str(db), "baseline", {"status": "INSUFFICIENT_DATA", "dataset": {"sample_size": 0}})
    result = write_audit_export(str(db), str(tmp_path / "ledger-audit.json"))
    check = next(item for item in result["audit"]["checks"] if item["name"].startswith("research validation ledger"))
    assert check["passed"] is True
