from pathlib import Path

from ui.dashboard_report import build
from core.trading_safety import TradingStore


def test_dashboard_report_is_qualified_and_non_authoritative(tmp_path: Path):
    path = str(tmp_path / "dashboard.db")
    TradingStore(path)
    report = build(path, auth_fn=lambda: {"status": "AUTHORIZATION_FAILED", "execution_authority": False})

    assert report["status"] == "BLOCKED"
    assert report["execution_authority"] is False
    assert report["qualifiers"]["sample_size"] == 0
    assert report["qualifiers"]["out_of_sample_status"] in {"not validated", "unknown"}
    assert report["panels"]["research_quality"]["promotion_allowed"] is False
    assert report["readiness"]["execution_authority"] is False


def test_dashboard_missing_database_fails_closed_without_authority(tmp_path: Path):
    report = build(str(tmp_path / "missing.db"), auth_fn=lambda: {"status": "AUTHORIZATION_FAILED"})

    assert report["status"] == "BLOCKED"
    assert report["execution_authority"] is False
    assert report["alerts"]["status"] in {"CRITICAL", "ERROR"}
    assert report["qualifiers"]["promotion_allowed"] is False

