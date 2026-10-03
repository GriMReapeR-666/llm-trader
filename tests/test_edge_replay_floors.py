"""Event-aligned backtest floors for STAT_PASS → REPLAY_PASS."""
from macro_graph import MacroGraph
from autonomous_causal_engine import (
    MIN_REACTION_SAMPLES,
    AutonomousCausalValidator,
    CausalEdgeBacktestOptimizer,
)
from edge_lifecycle import current_lifecycle


def _primary_source(graph: MacroGraph, title: str = "Fed Policy Rate Decision") -> str:
    return graph.ingest_source(
        title=title,
        source_url="https://www.federalreserve.gov/releases/h15/",
        publisher="Federal Reserve Board",
        payload={"rate_change_bps": 50},
    )


def _seed_reactions(graph: MacroGraph, source_id: str, *, count: int, return_fraction: float = 0.0025) -> None:
    for i in range(count):
        hour = 10 + (i % 10)
        day = 1 + (i // 10)
        reaction_id = graph.record_event_reaction(
            event_source_id=source_id,
            symbol="EURUSD",
            actual=3.5,
            forecast=3.2,
            previous=3.0,
            windows={
                "15m": {"return_fraction": return_fraction},
                "1h": {"return_fraction": return_fraction * 2},
            },
            source_quality="PRIMARY_RELEASE",
        )
        stamp = f"2026-08-{day:02d}T{hour:02d}:00:00+00:00"
        with graph._con() as con:
            con.execute("UPDATE event_reactions SET observed_at=? WHERE id=?", (stamp, reaction_id))


def test_apply_replay_floors_advances_stat_pass(tmp_path):
    graph = MacroGraph(str(tmp_path / "floors.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_HAWKISH",
        source_type="POLICY_RATE",
        target_name="USD_STRENGTH",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.90,
        source_id=source_id,
        notes="floor test",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES, return_fraction=0.0025)
    validator = AutonomousCausalValidator(graph)
    assert validator.validate_candidate_edge(edge_id)["approved"] is True
    assert current_lifecycle(graph, edge_id)["lifecycle"] == "STAT_PASS"

    with graph._con() as con:
        rows = con.execute("SELECT id FROM event_reactions ORDER BY id LIMIT 2").fetchall()
        con.execute(
            "UPDATE event_reactions SET observed_at=? WHERE id=?",
            ("2026-08-01T10:00:00+00:00", rows[0][0]),
        )
        con.execute(
            "UPDATE event_reactions SET observed_at=? WHERE id=?",
            ("2026-08-01T12:00:00+00:00", rows[1][0]),
        )

    bars = [
        {
            "low": 1.0800, "high": 1.0950, "close": 1.0820 + (0.0005 * i),
            "timestamp": f"2026-08-01T{10 + i:02d}:00:00+00:00",
        }
        for i in range(20)
    ]
    optimizer = CausalEdgeBacktestOptimizer(graph)
    results = optimizer.apply_replay_floors(bars, min_expectancy_r=-10.0, min_closed_trades=1)
    assert results
    row = next(item for item in results if item["edge_id"] == edge_id)
    assert row["execution_authority"] is False
    assert row["advanced"] is True
    assert row["to"] in {"REPLAY_PASS", "REPLAY_FAIL"}


def test_apply_replay_floors_skips_without_bars(tmp_path):
    graph = MacroGraph(str(tmp_path / "nobars.db"))
    optimizer = CausalEdgeBacktestOptimizer(graph)
    results = optimizer.apply_replay_floors([])
    assert results[0]["status"] == "SKIPPED_NO_BARS"
    assert results[0]["execution_authority"] is False


def test_replay_fail_when_no_event_anchors(tmp_path):
    graph = MacroGraph(str(tmp_path / "fail.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="CPI_SURPRISE",
        source_type="INFLATION",
        target_name="USD_STRENGTH",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.95,
        source_id=source_id,
        notes="no anchors",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES, return_fraction=0.001)
    AutonomousCausalValidator(graph).validate_candidate_edge(edge_id)
    assert current_lifecycle(graph, edge_id)["lifecycle"] == "STAT_PASS"
    bars = [
        {
            "low": 1.08, "high": 1.09, "close": 1.085,
            "timestamp": f"2025-01-01T{10 + i:02d}:00:00+00:00",
        }
        for i in range(10)
    ]
    results = CausalEdgeBacktestOptimizer(graph).apply_replay_floors(bars)
    row = next(item for item in results if item["edge_id"] == edge_id)
    assert row["passed"] is False
    assert row["to"] == "REPLAY_FAIL" or current_lifecycle(graph, edge_id)["lifecycle"] == "REPLAY_FAIL"
