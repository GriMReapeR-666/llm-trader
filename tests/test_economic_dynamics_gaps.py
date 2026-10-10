"""Gap-closure tests for Economic Dynamics integration."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from intelligence.causal_setup_map import is_research_transmission, resolve_causal_setup
from intelligence.economic_dynamics_catalog import tradable_setup_gaps, validate_catalog
from intelligence.intermarket_regime import filter_edges_for_live_regime
from intelligence.macro_graph import MacroGraph
from mt5_trader import LLMOrchestrator
from operations.agent_scheduler import SCHEDULES


def test_approved_edges_include_auto_approved(tmp_path):
    graph = MacroGraph(str(tmp_path / "auto.db"))
    source_id = graph.ingest_source(
        title="Fed release",
        payload={"rate": 1},
        source_url="https://www.federalreserve.gov/",
        publisher="Federal Reserve",
    )
    edge_id = graph.propose_edge(
        source_name="UNCERTAINTY",
        source_type="SENTIMENT",
        target_name="GOLD",
        target_type="COMMODITY",
        relation="SAFE_HAVEN_BID",
        sign="POSITIVE",
        confidence=0.8,
        source_id=source_id,
        notes='{"active_regimes":["CRISIS_PANIC","ALL"]}',
    )
    # Simulate autonomous validator promotion.
    with graph._con() as con:
        con.execute(
            "UPDATE macro_edges SET status='AUTO_APPROVED', reviewed_at=?, review_by=? WHERE edge_id=?",
            (datetime.now(timezone.utc).isoformat(), "AUTONOMOUS_VALIDATOR", edge_id),
        )
    rows = graph.approved_edges(["GOLD"])
    assert len(rows) == 1
    assert rows[0]["status"] == "AUTO_APPROVED"
    assert rows[0]["relation"] == "SAFE_HAVEN_BID"


def test_contradiction_group_prefers_regime_specific_edge():
    edges = [
        {
            "edge_id": 1,
            "relation": "INVERSE_PRICES",
            "confidence": 0.9,
            "notes": '{"active_regimes":["NORMAL","ALL"],"contradiction_group":"gold_dxy_safehaven"}',
        },
        {
            "edge_id": 2,
            "relation": "SAFE_HAVEN_BID",
            "confidence": 0.7,
            "notes": '{"active_regimes":["CRISIS_PANIC"],"contradiction_group":"gold_dxy_safehaven"}',
        },
    ]
    crisis = filter_edges_for_live_regime(edges, "CRISIS_PANIC")
    assert len(crisis) == 1
    assert crisis[0]["relation"] == "SAFE_HAVEN_BID"
    assert crisis[0]["contradiction_resolved"] is True

    normal = filter_edges_for_live_regime(edges, "NORMAL")
    assert len(normal) == 1
    assert normal[0]["relation"] == "INVERSE_PRICES"


def test_unknown_regime_keeps_only_all_tagged_edges():
    edges = [
        {"edge_id": 1, "relation": "A", "notes": '{"active_regimes":["ALL"]}'},
        {"edge_id": 2, "relation": "B", "notes": '{"active_regimes":["CRISIS_PANIC"]}'},
    ]
    filtered = filter_edges_for_live_regime(edges, "UNKNOWN")
    assert [e["relation"] for e in filtered] == ["A"]


def test_tradable_setup_coverage_complete():
    assert validate_catalog() == []
    assert tradable_setup_gaps() == []


def test_transmission_nodes_are_not_tradable_setups():
    mapped = resolve_causal_setup(
        relation="PULLS",
        source_type="MACRO_INDICATOR",
        target_type="MACRO_INDICATOR",
        source_name="AGGREGATE_DEMAND",
        target_name="INFLATION",
    )
    assert mapped["eligible"] is False
    assert mapped["research_transmission"] is True
    assert is_research_transmission("CREDIT") is True


def test_scheduler_includes_causal_validate_and_seed():
    names = {item["name"] for item in SCHEDULES}
    assert "causal_edge_validate" in names
    assert "economic_dynamics_seed" in names


def test_schedule_causal_validate_handler(tmp_path):
    class _Ops:
        def __init__(self):
            self.events = []

        def claim_due_schedules(self):
            return [{"schedule_name": "causal_edge_validate", "payload": {"kind": "causal_validate"}}]

        def event(self, *args, **kwargs):
            self.events.append(("event", args, kwargs))

        def agent_event(self, *args, **kwargs):
            self.events.append(("agent_event", args, kwargs))

    class _Store:
        def __init__(self):
            self.states = {}

        def set_state(self, key, value):
            self.states[key] = value

    class _Validator:
        def auto_validate_all_candidates(self):
            return [{"approved": True, "edge_id": 1}, {"approved": False, "edge_id": 2}]

    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Ops()
    instance.store = _Store()
    instance.causal_validator = _Validator()
    instance.run_id = "run"
    instance._process_due_schedules("EURUSD", True)
    payload = instance.store.states["last_causal_edge_validate"]
    assert payload["validated_count"] == 2
    assert payload["approved_count"] == 1
    assert payload["execution_authority"] is False


def test_schedule_economic_dynamics_seed_handler(tmp_path):
    class _Ops:
        def claim_due_schedules(self):
            return [{"schedule_name": "economic_dynamics_seed", "payload": {"kind": "economic_dynamics_seed"}}]

        def event(self, *args, **kwargs):
            pass

        def agent_event(self, *args, **kwargs):
            pass

    class _Store:
        def __init__(self):
            self.states = {}

        def set_state(self, key, value):
            self.states[key] = value

    graph = MacroGraph(str(tmp_path / "seed_sched.db"))
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance.operations = _Ops()
    instance.store = _Store()
    instance.macro_graph = graph
    instance.run_id = "run"
    instance._process_due_schedules("EURUSD", True)
    payload = instance.store.states["economic_dynamics_seed"]
    assert payload["created_count"] > 0
    assert payload["execution_authority"] is False
    assert payload["status"] == "CANDIDATE_SEED_COMPLETE"
