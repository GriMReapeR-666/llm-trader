from research.edge_lifecycle import advance_lifecycle, can_transition, current_lifecycle, normalize_lifecycle
from intelligence.macro_graph import MacroGraph


def test_normalize_and_transitions():
    assert normalize_lifecycle("AUTO_APPROVED") == "STAT_PASS"
    assert can_transition("CANDIDATE", "STAT_PASS") is True
    assert can_transition("CANDIDATE", "FULL") is False
    assert can_transition("STAT_PASS", "REPLAY_PASS") is True


def test_advance_lifecycle_records_history(tmp_path):
    graph = MacroGraph(str(tmp_path / "life.db"))
    source_id = graph.ingest_source(
        title="Fed",
        source_url="https://www.federalreserve.gov/releases/h15/",
        publisher="Federal Reserve Board",
        payload={"x": 1},
    )
    edge_id = graph.propose_edge(
        source_name="A",
        source_type="POLICY",
        target_name="B",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.8,
        source_id=source_id,
    )
    first = advance_lifecycle(graph, edge_id, "STAT_PASS", evidence={"n": 30})
    assert first["advanced"] is True
    assert first["to"] == "STAT_PASS"
    assert first["execution_authority"] is False
    state = current_lifecycle(graph, edge_id)
    assert state["lifecycle"] == "STAT_PASS"

    illegal = advance_lifecycle(graph, edge_id, "FULL")
    assert illegal["advanced"] is False
    assert "illegal" in illegal["reason"]
