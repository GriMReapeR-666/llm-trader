from pathlib import Path

from research_sprint_planner import default_acceptance, plan_and_persist, plan_research_sprints
from research_runs import list_runs
from opportunity_bus import (
    handoff_for_validation,
    narrate_candidate,
    opportunity_from_features,
    opportunity_from_scan_row,
    publish,
    publish_from_scan,
)
from agent_contracts import can, can_write
import pytest


def test_technicalnet_may_emit_opportunity():
    assert can("TechnicalNet", "emit_opportunity")
    assert can_write("TechnicalNet", "opportunity_candidate")
    assert can("MacroAnalyst", "emit_opportunity")
    assert can_write("MacroAnalyst", "opportunity_candidate")
    assert can("MarketModel", "emit_opportunity") is False


def test_plan_research_sprints_bootstrap_on_empty_journal(tmp_path: Path):
    path = str(tmp_path / "sprint.db")
    plan = plan_research_sprints(path)
    assert plan["execution_authority"] is False
    assert plan["ticket_count"] >= 1
    assert plan["tickets"][0]["kind"] == "BOOTSTRAP_LABELLED_DATASET"
    assert plan["tickets"][0]["acceptance"]["min_profit_factor"] == 1.2
    assert default_acceptance()["allowed_to_influence_execution"] is False

    persisted = plan_and_persist(path)
    assert persisted["execution_authority"] is False
    assert persisted["ticket_count"] >= 1
    runs = list_runs(path, run_kind="research_sprint")
    assert len(runs) >= 1
    assert runs[0]["execution_authority"] is False or runs[0].get("execution_authority") in (0, False)


def test_opportunity_bus_publish_and_handoff(tmp_path: Path):
    class Store:
        def __init__(self):
            self.state = {}

        def set_state(self, key, value):
            self.state[key] = value

        def get_state(self, key, default=None):
            return self.state.get(key, default)

    store = Store()
    row = {"symbol": "EURUSD", "score": 55, "setup_id": "trend_pullback_v1", "side": "BUY"}
    published = publish_from_scan(store, [row], timeframe="H1")
    assert published["status"] == "PUBLISHED"
    assert published["execution_authority"] is False
    assert published["published"] == 1

    candidate_id = published["candidate_ids"][0]
    handed = handoff_for_validation(store, candidate_id)
    assert handed["status"] == "PENDING_VALIDATION"
    assert handed["execution_authority"] is False
    assert handed["candidate"]["status"] == "PENDING_VALIDATION"

    missing = handoff_for_validation(store, "no-such-id")
    assert missing["status"] == "NOT_FOUND"


def test_opportunity_from_features_and_narrative():
    features = {
        "adx": 25,
        "ema_alignment": 1,
        "mtf_alignment": 1,
        "near_key_level": True,
        "confirmed_candle": True,
        "volume_confirmed": True,
        "liquidity_sweep": False,
        "asian_range_breakout": False,
        "completed_bar_time": "2026-08-11T10:00:00+00:00",
    }
    candidate = opportunity_from_features(features, symbol="EURUSD", timeframe="H1", session="LONDON")
    assert candidate is not None
    assert candidate["setup_id"] == "trend_pullback_v1"
    assert candidate["execution_authority"] is False

    narrated = narrate_candidate(candidate, "Trend pullback looks clean.", agent="MarketModel")
    assert narrated["status"] == "NARRATED"
    assert narrated["execution_authority"] is False


def test_market_model_cannot_publish_opportunities():
    class Store:
        def __init__(self):
            self.state = {}

        def set_state(self, key, value):
            self.state[key] = value

        def get_state(self, key, default=None):
            return self.state.get(key, default)

    with pytest.raises(PermissionError):
        publish(Store(), [opportunity_from_scan_row({"symbol": "XAUUSD", "score": 60})], agent="MarketModel")
