"""FRED/calendar macro observations bound to catalog entities."""
from intelligence.macro_observations import ingest_fred_snapshot, ingest_calendar_events, recent


def test_ingest_fred_binds_entities(tmp_path):
    path = str(tmp_path / "obs.db")
    result = ingest_fred_snapshot(path, {
        "CPIAUCSL": {"value": 3.2, "previous": 3.0, "date": "2026-08-01"},
        "UNRATE": 4.1,
    })
    assert result["status"] == "INGESTED"
    assert result["inserted"] == 2
    assert result["execution_authority"] is False
    entities = {row["entity"] for row in result["observations"]}
    assert "INFLATION" in entities
    assert "EMPLOYMENT" in entities
    rows = recent(path, limit=10)
    assert rows["count"] >= 2
    assert rows["execution_authority"] is False


def test_ingest_calendar_events(tmp_path):
    path = str(tmp_path / "cal.db")
    result = ingest_calendar_events(path, [
        {"currency": "USD", "event": "CPI", "actual": 3.1, "forecast": 3.0, "previous": 2.9},
    ])
    assert result["inserted"] >= 1
    assert result["execution_authority"] is False
