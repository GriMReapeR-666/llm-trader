import pytest

from macro_graph import MacroGraph
from autonomous_causal_engine import (
    MIN_REACTION_SAMPLES,
    AutonomousCausalValidator,
    CausalChainSetupAnalyzer,
    CausalEdgeBacktestOptimizer,
)


def _primary_source(graph: MacroGraph, title: str = "Fed Policy Rate Decision") -> str:
    return graph.ingest_source(
        title=title,
        source_url="https://www.federalreserve.gov/releases/h15/",
        publisher="Federal Reserve Board",
        payload={"rate_change_bps": 50},
    )


def _seed_reactions(
    graph: MacroGraph,
    source_id: str,
    *,
    count: int,
    return_fraction: float = 0.0025,
    actual: float = 3.5,
    forecast: float = 3.2,
    symbol: str = "EURUSD",
) -> None:
    """Insert measured surprise reactions (valid_samples == count)."""
    for i in range(count):
        hour = 10 + (i % 10)
        day = 1 + (i // 10)
        # record_event_reaction stamps observed_at with utcnow(); override after insert
        reaction_id = graph.record_event_reaction(
            event_source_id=source_id,
            symbol=symbol,
            actual=actual,
            forecast=forecast,
            previous=3.0,
            windows={
                "15m": {"return_fraction": return_fraction},
                "1h": {"return_fraction": return_fraction * 2},
            },
            source_quality="PRIMARY_RELEASE",
        )
        # Force deterministic observed_at for event-aligned backtests.
        stamp = f"2026-08-{day:02d}T{hour:02d}:00:00+00:00"
        with graph._con() as con:
            con.execute(
                "UPDATE event_reactions SET observed_at=? WHERE id=?",
                (stamp, reaction_id),
            )


def test_insufficient_sample_keeps_candidate_and_no_default_hit_rate(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="POSITIVE",
        confidence=0.90,
        source_id=source_id,
        notes="should not auto-approve without sample floor",
    )
    # One matching reaction — previously defaulted hit_rate to 0.80 and approved.
    _seed_reactions(graph, source_id, count=1, return_fraction=0.0025)

    validator = AutonomousCausalValidator(graph)  # default MIN_REACTION_SAMPLES
    result = validator.validate_candidate_edge(edge_id)

    assert result["approved"] is False
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["empirical_win_rate"] is None
    assert result["valid_samples"] == 1
    assert result["min_reaction_samples"] == MIN_REACTION_SAMPLES
    assert result["execution_authority"] is False

    with graph._con() as con:
        status = con.execute("SELECT status FROM macro_edges WHERE edge_id=?", (edge_id,)).fetchone()[0]
    assert status == "CANDIDATE"


def test_autonomous_causal_validator_approves_with_min_samples(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="POSITIVE",
        confidence=0.80,
        source_id=source_id,
        notes="Rate hikes strengthen local currency",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES, return_fraction=0.0025)

    validator = AutonomousCausalValidator(graph)
    val_res = validator.validate_candidate_edge(edge_id)

    assert val_res["approved"] is True
    assert val_res["status"] == "AUTO_APPROVED"
    assert val_res["valid_samples"] == MIN_REACTION_SAMPLES
    assert val_res["empirical_win_rate"] == pytest.approx(1.0)
    assert val_res["adjusted_confidence"] >= 0.80
    assert val_res["execution_authority"] is False


def test_autonomous_causal_validator_rejects_weak_empirical_rate(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="POSITIVE",
        confidence=0.80,
        source_id=source_id,
        notes="weak reaction",
    )
    # Positive surprise but negative return → directional miss for POSITIVE edge.
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES, return_fraction=-0.0025)

    validator = AutonomousCausalValidator(graph)
    val_res = validator.validate_candidate_edge(edge_id)

    assert val_res["approved"] is False
    assert val_res["status"] == "REJECTED_STATISTICAL"
    assert val_res["empirical_win_rate"] == pytest.approx(0.0)
    assert val_res["execution_authority"] is False


def test_causal_chain_setup_analyzer(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = graph.ingest_source(
        title="CPI Release",
        source_url="https://fred.stlouisfed.org/series/CPIAUCSL",
        publisher="FRED / BLS",
        payload={"cpi": 3.5},
    )
    edge_id = graph.propose_edge(
        source_name="US_CPI_SURPRISE",
        source_type="MACRO_INDICATOR",
        target_name="VOLATILITY_BREAKOUT",
        target_type="MARKET_STRUCTURE",
        relation="SPURS_VOLATILITY",
        sign="POSITIVE",
        confidence=0.85,
        source_id=source_id,
        notes="High CPI drives volatility",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES, return_fraction=0.001)

    validator = AutonomousCausalValidator(graph)
    assert validator.validate_candidate_edge(edge_id)["approved"] is True

    analyzer = CausalChainSetupAnalyzer(graph)
    proposals = analyzer.discover_emerging_setups(
        symbol="EURUSD",
        active_entities=["US_CPI_SURPRISE", "VOLATILITY_BREAKOUT"],
    )

    assert len(proposals) >= 1
    assert proposals[0]["setup_class"] == "range_reversal_v1"
    assert proposals[0]["suggested_side"] == "BUY"
    assert proposals[0]["execution_authority"] is False


def test_causal_edge_backtest_refuses_without_event_anchors(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph, title="Fed Statement")
    edge_id = graph.propose_edge(
        source_name="FED_HAWKISH",
        source_type="POLICY_RATE",
        target_name="USD_STRENGTH",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.90,
        source_id=source_id,
        notes="no reactions yet",
    )

    bars = [
        {"low": 1.0800, "high": 1.0850, "close": 1.0820, "timestamp": f"2026-08-01T{10 + i:02d}:00:00+00:00"}
        for i in range(15)
    ]
    optimizer = CausalEdgeBacktestOptimizer(graph)
    bt_res = optimizer.backtest_causal_edge_strategy(edge_id, bars)

    assert bt_res["status"] == "INSUFFICIENT_EVENT_ANCHORS"
    assert bt_res["simulated_intents"] == 0
    assert bt_res["execution_authority"] is False


def test_causal_edge_backtest_event_aligned(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph, title="Fed Statement")
    edge_id = graph.propose_edge(
        source_name="FED_HAWKISH",
        source_type="POLICY_RATE",
        target_name="USD_STRENGTH",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.90,
        source_id=source_id,
        notes="Hawkish stance strengthens USD",
    )
    # Two event anchors on hours 10 and 12 within the bar grid.
    _seed_reactions(graph, source_id, count=2, return_fraction=0.0025)
    with graph._con() as con:
        rows = con.execute("SELECT id FROM event_reactions ORDER BY id").fetchall()
        con.execute("UPDATE event_reactions SET observed_at=? WHERE id=?", ("2026-08-01T10:00:00+00:00", rows[0][0]))
        con.execute("UPDATE event_reactions SET observed_at=? WHERE id=?", ("2026-08-01T12:00:00+00:00", rows[1][0]))

    bars = [
        {"low": 1.0800, "high": 1.0850, "close": 1.0820, "timestamp": f"2026-08-01T{10 + i:02d}:00:00+00:00"}
        for i in range(15)
    ]

    optimizer = CausalEdgeBacktestOptimizer(graph)
    bt_res = optimizer.backtest_causal_edge_strategy(edge_id, bars)

    assert bt_res["status"] == "BACKTEST_COMPLETED"
    assert bt_res["simulated_intents"] == 2
    assert bt_res["event_indices"] == [0, 2]
    assert bt_res["alignment"]["aligned"] == 2
    assert bt_res["execution_authority"] is False
