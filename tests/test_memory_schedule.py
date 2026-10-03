from pathlib import Path

import pytest

from trading_memory import TradingMemory
from trading_safety import TradeProposal, TradingStore


def test_weekly_narrative_and_core_consolidation_are_restart_safe(tmp_path: Path):
    path = str(tmp_path / "memory-schedule.db")
    store = TradingStore(path)
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "weekly-test"})
    store.record_open_trade(1, proposal, 1.0, 1.0, {"proposal_features": {}})
    # Use an explicit broker close timestamp so this test remains deterministic
    # when the calendar advances beyond the originally documented week.
    store.record_close(1, 1.2, 1.0, "target", closed_at="2026-08-04T12:00:00+00:00")
    memory = TradingMemory(path)

    narrative = memory.generate_weekly_narrative("2026-08-03")
    assert narrative["closed_trades"] == 1
    assert narrative["week_end"] == "2026-08-10"

    first = memory.consolidate_weekly_core("2026-08-03")
    second = memory.consolidate_weekly_core("2026-08-03")
    assert first["status"] == "CANDIDATE"
    assert second["status"] == "EXISTING_CANDIDATE"
    assert first["candidate_id"] == second["candidate_id"]
    assert first["execution_authority"] is False


def test_weekly_narrative_rejects_bad_date(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "memory-schedule-invalid.db"))
    with pytest.raises(ValueError, match="week_start"):
        memory.generate_weekly_narrative("not-a-date")
    with pytest.raises(ValueError, match="Monday"):
        memory.generate_weekly_narrative("2026-08-04")
    with pytest.raises(ValueError, match="day"):
        memory.generate_daily_narrative("not-a-date")


def test_weekly_consolidation_does_not_create_empty_lesson(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "memory-empty-week.db"))
    result = memory.consolidate_weekly_core("2026-08-03")
    assert result["status"] == "NO_DATA"
    assert result["candidate_id"] is None


def test_monthly_maintenance_does_not_auto_retire_approved_memory(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "memory-maintenance.db"))
    memory.save_narrative({"kind": "test"}, "approved", status="APPROVED")
    result = memory.monthly_memory_maintenance(older_than_days=1)
    assert result["approved_memories_review_required"] >= 0
    assert result["execution_authority"] is False
    assert "operator review" in result["policy"]
