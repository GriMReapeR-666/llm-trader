"""Approved-edge reinforcement from measured event reactions.

Reinforcement folds new reaction evidence into edges that ALREADY cleared
statistical validation (APPROVED/AUTO_APPROVED). CANDIDATE edges stay with
``validate_candidate_edge``; STAT_FAIL/RETIRED edges are never resurrected.
"""
from intelligence.macro_graph import MacroGraph
from intelligence.autonomous_causal_engine import (
    MIN_REACTION_SAMPLES,
    REINFORCEMENT_LIFECYCLE_SET,
    REINFORCEMENT_MAX_DELTA_PER_PASS,
    REINFORCEMENT_STEP,
    AutonomousCausalValidator,
)
from research.edge_lifecycle import current_lifecycle


def _primary_source(graph: MacroGraph, title: str = "Fed Policy Rate Decision") -> str:
    return graph.ingest_source(
        title=title,
        source_url="https://www.federalreserve.gov/releases/h15/",
        publisher="Federal Reserve Board",
        payload={"rate_change_bps": 50},
    )


def _secondary_source(graph: MacroGraph) -> str:
    return graph.ingest_source(
        title="Other Fed Release",
        source_url="https://www.federalreserve.gov/releases/other/",
        publisher="Federal Reserve Board",
        payload={"note": "unrelated source"},
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
        stamp = f"2026-08-{day:02d}T{hour:02d}:00:00+00:00"
        with graph._con() as con:
            con.execute("UPDATE event_reactions SET observed_at=? WHERE id=?", (stamp, reaction_id))


def _approve_edge(graph: MacroGraph, *, source_id: str, confidence: float = 0.79) -> int:
    """Approve a POSITIVE edge through the normal statistical validation path.

    Default 0.79 keeps the validator's adjusted confidence (0.79 * 1.2 = 0.948)

    below the 1.0 ceiling so reinforcement deltas have headroom to move it.
    """
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="POSITIVE",
        confidence=confidence,
        source_id=source_id,
        notes="reinforcement test",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES)
    validator = AutonomousCausalValidator(graph)
    result = validator.validate_candidate_edge(edge_id)
    assert result["approved"] is True, result
    return edge_id


def _edge_confidence(graph: MacroGraph, edge_id: int) -> float:
    with graph._con() as con:
        return float(con.execute(
            "SELECT confidence FROM macro_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()[0])


def _edge_status(graph: MacroGraph, edge_id: int) -> str:
    with graph._con() as con:
        return str(con.execute(
            "SELECT status FROM macro_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()[0])


def _reinforced_markers(graph: MacroGraph, edge_id: int) -> list:
    with graph._con() as con:
        rows = con.execute(
            """SELECT payload FROM macro_edge_history
               WHERE edge_id=? AND action='REACTION_REINFORCED'
               ORDER BY history_id ASC""",
            (edge_id,),
        ).fetchall()
    import json
    return [json.loads(row["payload"] or "{}") for row in rows]


def test_confirmation_reactions_nudge_confidence_up(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)
    before = _edge_confidence(graph, edge_id)

    _seed_reactions(graph, source_id, count=3)  # same positive surprise as approval
    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["reinforced_count"] == 1
    assert result["confirmations"] == 3
    assert result["misses"] == 0
    assert result["execution_authority"] is False
    row = result["reinforced"][0]
    assert row["edge_id"] == edge_id
    assert row["direction"] == "CONFIRM"
    assert abs(row["confidence_delta"] - 3 * REINFORCEMENT_STEP) < 1e-9
    assert _edge_confidence(graph, edge_id) > before
    assert _edge_status(graph, edge_id) in ("APPROVED", "AUTO_APPROVED")


def test_miss_reactions_nudge_confidence_down_without_demotion(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)
    before = _edge_confidence(graph, edge_id)

    # Positive surprise (hawkish) but negative 15m return -> miss for POSITIVE sign.
    _seed_reactions(graph, source_id, count=2, return_fraction=-0.002)
    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["reinforced_count"] == 1
    assert result["confirmations"] == 0
    assert result["misses"] == 2
    row = result["reinforced"][0]
    assert row["direction"] == "MISS"
    assert row["confidence_delta"] < 0
    assert _edge_confidence(graph, edge_id) < before
    # Governance-only: status and lifecycle unchanged.
    assert _edge_status(graph, edge_id) in ("APPROVED", "AUTO_APPROVED")
    assert current_lifecycle(graph, edge_id)["lifecycle"] == "STAT_PASS"


def test_pass_delta_capped_and_confidence_clamped(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)  # adjusted confidence 0.948
    before = _edge_confidence(graph, edge_id)

    _seed_reactions(graph, source_id, count=20)  # would be +0.20 uncapped
    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    row = result["reinforced"][0]
    assert row["confidence_delta"] == REINFORCEMENT_MAX_DELTA_PER_PASS
    assert _edge_confidence(graph, edge_id) == round(before + 0.05, 4)  # 0.998, under ceiling
    assert _edge_confidence(graph, edge_id) > before


def test_reactions_reinforce_each_edge_exactly_once(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)
    after_approval = _edge_confidence(graph, edge_id)

    _seed_reactions(graph, source_id, count=3)
    validator = AutonomousCausalValidator(graph)
    first = validator.reinforce_approved_edges_from_reactions()
    assert first["reinforced_count"] == 1
    after_first = _edge_confidence(graph, edge_id)

    # No new reactions: nothing to consume, nothing changes.
    second = validator.reinforce_approved_edges_from_reactions()
    assert second["reinforced_count"] == 0
    assert _edge_confidence(graph, edge_id) == after_first

    # Even a full historical replay must not double-count: marker union.
    markers = _reinforced_markers(graph, edge_id)
    assert len(markers) == 1
    assert len(markers[0]["reaction_ids"]) == 3
    assert after_first > after_approval


def test_auto_validated_reaction_ids_are_not_reinforced_again(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)

    import json
    with graph._con() as con:
        auto_ids = None
        for row in con.execute(
            "SELECT payload FROM macro_edge_history WHERE edge_id=? AND action='AUTO_VALIDATED'",
            (edge_id,),
        ).fetchall():
            payload = json.loads(row["payload"] or "{}")
            if payload.get("reaction_ids"):
                auto_ids = payload["reaction_ids"]
    assert auto_ids and len(auto_ids) == MIN_REACTION_SAMPLES

    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()
    assert result["reinforced_count"] == 0
    assert _edge_confidence(graph, edge_id) == round(min(1.0, 0.79 * (0.8 + 0.4 * 1.0)), 4)  # validator's formula


def test_per_pass_cap_limits_edges_examined(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    for n in range(30):
        source_id = _primary_source(graph, title=f"Fed Release {n}")
        _approve_edge(graph, source_id=source_id)

    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["edges_examined"] == 25  # REINFORCEMENT_MAX_EDGES_PER_PASS
    with graph._con() as con:
        total = con.execute(
            "SELECT COUNT(*) FROM macro_edges WHERE status IN ('APPROVED','AUTO_APPROVED')"
        ).fetchone()[0]
    assert total == 30


def test_candidate_and_failed_edges_are_untouched(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    candidate_source = _secondary_source(graph)
    candidate = graph.propose_edge(
        source_name="CPI_HOT",
        source_type="INFLATION",
        target_name="USD_STRENGTH",
        target_type="ASSET_REGIME",
        relation="STRENGTHENS",
        sign="POSITIVE",
        confidence=0.9,
        source_id=candidate_source,
    )
    _seed_reactions(graph, candidate_source, count=5)
    assert _edge_status(graph, candidate) == "CANDIDATE"
    assert current_lifecycle(graph, candidate)["lifecycle"] == "CANDIDATE"

    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["edges_examined"] == 0
    assert result["reinforced_count"] == 0
    assert _edge_confidence(graph, candidate) == 0.9
    assert _edge_status(graph, candidate) == "CANDIDATE"
    assert _reinforced_markers(graph, candidate) == []
    assert REINFORCEMENT_LIFECYCLE_SET.isdisjoint({"CANDIDATE", "STAT_FAIL", "RETIRED"})


def test_neutral_batch_consumes_reactions_without_churning_edge_row(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=source_id)
    with graph._con() as con:
        before_updated = con.execute(
            "SELECT updated_at FROM macro_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()[0]

    # One confirm + one miss -> zero net delta, but reactions must be consumed.
    _seed_reactions(graph, source_id, count=1, return_fraction=0.0025)
    _seed_reactions(graph, source_id, count=1, return_fraction=-0.002)
    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["reinforced_count"] == 1
    row = result["reinforced"][0]
    assert row["direction"] == "NEUTRAL"
    assert row["confidence_delta"] == 0
    with graph._con() as con:
        after_updated = con.execute(
            "SELECT updated_at FROM macro_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()[0]
    assert after_updated == before_updated  # no pointless row churn
    markers = _reinforced_markers(graph, edge_id)
    assert len(markers) == 1 and len(markers[0]["reaction_ids"]) == 2


def test_source_partitioning_and_correlation_id_isolation(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    edge_source = _primary_source(graph)
    edge_id = _approve_edge(graph, source_id=edge_source)

    # Reactions from an unrelated source must not touch this edge.
    other_source = _secondary_source(graph)
    _seed_reactions(graph, other_source, count=4, symbol="GBPUSD")
    # Matching reactions for the edge's own source do.
    _seed_reactions(graph, edge_source, count=2)

    result = AutonomousCausalValidator(graph).reinforce_approved_edges_from_reactions()

    assert result["reinforced_count"] == 1
    row = result["reinforced"][0]
    assert row["edge_id"] == edge_id
    assert row["confirms"] == 2  # only the edge's own source reactions counted
    assert len(row["reaction_ids"]) == 2
