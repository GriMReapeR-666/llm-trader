import json
import subprocess
import sys
from pathlib import Path

from core.trading_safety import TradingStore
from research.model_registry import register


def test_reconciliation_cli_uses_explicit_journal_path(tmp_path: Path):
    journal = tmp_path / "selected.db"
    store = TradingStore(str(journal))
    store.record_daily_reconciliation({
        "period_start": "2026-08-01T00:00:00+00:00",
        "period_end": "2026-08-02T00:00:00+00:00",
        "status": "CLEAN",
        "discrepancies": {},
    })
    result = subprocess.run(
        [sys.executable, "mt5_trader.py", "journal", "reconciliation", "--journal-path", str(journal)],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    assert report["status"] == "CLEAN"
    assert report["period_start"] == "2026-08-01T00:00:00+00:00"


def test_model_status_cli_uses_explicit_journal_path(tmp_path: Path):
    journal = tmp_path / "models.db"
    record = register(
        str(journal), model_key="selected-model", version="v1", kind="supervised",
        feature_schema_version="feature_snapshot_v1", training_data_hash="b" * 64,
        metrics={}, artifact_uri="artifact.json",
    )
    result = subprocess.run(
        [sys.executable, "mt5_trader.py", "journal", "model-status", "--journal-path", str(journal)],
        check=True, capture_output=True, text=True,
    )
    models = json.loads(result.stdout)["models"]
    assert models and models[0]["id"] == record["id"]
