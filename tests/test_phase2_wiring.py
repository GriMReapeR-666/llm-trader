from macro_graph import MacroGraph
from autonomous_causal_engine import AutonomousCausalValidator, MIN_REACTION_SAMPLES


def _seed_reactions(graph: MacroGraph, source_id: str, *, count: int, symbol: str = "EURUSD") -> None:
    for i in range(count):
        graph.record_event_reaction(
            event_source_id=source_id,
            symbol=symbol,
            actual=3.5 + (i * 0.01),
            forecast=3.2,
            previous=3.0,
            windows={"15m": {"return_fraction": 0.0025}},
            source_quality="PRIMARY_RELEASE",
        )


def test_trade_outcome_feedback_revises_edge_and_can_revalidate(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = graph.ingest_source(
        title="Fed Policy Rate Decision",
        source_url="https://www.federalreserve.gov/releases/h15/",
        publisher="Federal Reserve Board",
        payload={"rate_change_bps": 25},
    )
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="POSITIVE",
        confidence=0.80,
        source_id=source_id,
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES)
    validator = AutonomousCausalValidator(graph)
    validated = validator.validate_candidate_edge(edge_id)
    assert validated["approved"] is True
    assert validated["status"] == "AUTO_APPROVED"
    # Gap fix: AUTO_APPROVED edges must appear in approved_edges retrieval.
    assert any(int(row["edge_id"]) == int(edge_id) for row in graph.approved_edges(["USD_INDEX_RALLY"]))
    pre_feedback_confidence = float(validated["adjusted_confidence"])

    feedback = graph.apply_trade_outcome_feedback(
        edge_id, r_multiple=2.0, ticket=42, label="WIN_TARGET",
    )
    assert feedback["old_confidence"] == pre_feedback_confidence
    assert feedback["new_confidence"] > pre_feedback_confidence
    assert feedback["status"] == "CANDIDATE"
    assert graph.approved_edges(["USD_INDEX_RALLY"]) == []

    reval = validator.validate_candidate_edge(edge_id)
    assert reval["approved"] is True
    assert reval["execution_authority"] is False
    assert any(int(row["edge_id"]) == int(edge_id) for row in graph.approved_edges(["USD_INDEX_RALLY"]))
