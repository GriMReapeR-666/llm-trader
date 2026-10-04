from pathlib import Path
import math

import pytest

from intelligence.macro_graph import MacroGraph


def test_macro_edge_history_is_append_only_and_revision_requires_review(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = graph.ingest_source(
        title="Official release", payload={"actual": 1.0},
        source_url="https://www.federalreserve.gov/", publisher="Federal Reserve",
    )
    edge_id = graph.propose_edge(
        source_name="Real yields", source_type="indicator", target_name="Gold", target_type="asset",
        relation="inverse", sign="NEGATIVE", confidence=0.7, source_id=source_id,
        notes="Initial cited hypothesis",
    )
    assert graph.edge_history(edge_id)[0]["action"] == "PROPOSED"
    graph.review_edge(edge_id, approve=True, reviewer="operator")
    assert graph.approved_edges(["Gold"])
    graph.revise_edge(edge_id, reviewer="operator", confidence=0.4, notes="Amended after counter-evidence")
    assert graph.approved_edges(["Gold"]) == []
    history = graph.edge_history(edge_id)
    assert [item["action"] for item in history] == ["PROPOSED", "REVIEWED", "REVISED"]
    assert history[-1]["payload"]["before"]["confidence"] == 0.7
    with pytest.raises(ValueError):
        graph.review_edge(edge_id, approve=True, reviewer="")
    graph.review_edge(edge_id, approve=True, reviewer="operator-2")
    assert graph.approved_edges(["Gold"])


def test_unverified_sources_cannot_create_causal_edges(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = graph.ingest_source(
        title="Unverified blog", payload={"claim": "unknown"},
        source_url="https://example.invalid/article", publisher="unknown",
    )
    with pytest.raises(ValueError, match="source policy"):
        graph.propose_edge(
            source_name="Claim", source_type="text", target_name="EURUSD", target_type="asset",
            relation="moves", sign="CONDITIONAL", confidence=0.2, source_id=source_id,
        )


def test_event_retrieval_respects_point_in_time_cutoff(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro-events.db"))
    old_source = graph.ingest_source(title="Old release", payload={"actual": 1.0},
                                      source_url="https://www.federalreserve.gov/", publisher="Federal Reserve")
    future_source = graph.ingest_source(title="Future release", payload={"actual": 2.0},
                                         source_url="https://www.federalreserve.gov/", publisher="Federal Reserve")
    with graph._con() as con:
        con.execute(
            """INSERT INTO event_reactions(event_source_id,symbol,observed_at,actual,forecast,previous,surprise,windows,source_quality)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (old_source, "EURUSD", "2025-01-02T00:00:00+00:00", 1.0, 0.5, 0.4, 0.5, "{}", "verified"),
        )
        con.execute(
            """INSERT INTO event_reactions(event_source_id,symbol,observed_at,actual,forecast,previous,surprise,windows,source_quality)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (future_source, "EURUSD", "2027-01-02T00:00:00+00:00", 2.0, 0.5, 0.4, 1.5, "{}", "verified"),
        )

    recent = graph.recent_event_reactions("EURUSD", as_of="2026-01-01T00:00:00Z")
    similar = graph.similar_event_reactions("EURUSD", actual=1.0, forecast=0.5,
                                            as_of="2026-01-01T00:00:00Z")
    assert len(recent) == 1
    assert len(similar["results"]) == 1
    assert similar["retrieval_as_of"] == "2026-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="as_of"):
        graph.similar_event_reactions("EURUSD", as_of="bad-time")


def test_approved_edges_respect_validity_and_review_cutoff(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro-edges.db"))
    source_id = graph.ingest_source(
        title="Official release", payload={"claim": "old"},
        source_url="https://www.federalreserve.gov/", publisher="Federal Reserve",
    )
    edge_id = graph.propose_edge(
        source_name="Rates", source_type="indicator", target_name="EURUSD", target_type="asset",
        relation="affects", sign="NEGATIVE", confidence=0.8, source_id=source_id,
        valid_from="2025-01-01T00:00:00+00:00", valid_until="2026-12-31T00:00:00+00:00",
    )
    graph.review_edge(edge_id, approve=True, reviewer="operator")
    with graph._con() as con:
        con.execute("UPDATE macro_edges SET created_at=?, reviewed_at=? WHERE edge_id=?",
                    ("2025-01-02T00:00:00+00:00", "2025-01-03T00:00:00+00:00", edge_id))
    assert graph.approved_edges(["EURUSD"], as_of="2026-01-01T00:00:00Z")
    assert graph.approved_edges(["EURUSD"], as_of="2024-01-01T00:00:00Z") == []


def test_macro_edges_reject_nonfinite_confidence(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro-confidence.db"))
    source_id = graph.ingest_source(
        title="Official release", payload={"claim": "test"},
        source_url="https://www.federalreserve.gov/", publisher="Federal Reserve",
    )
    with pytest.raises(ValueError, match="confidence"):
        graph.propose_edge(
            source_name="Rates", source_type="indicator", target_name="EURUSD", target_type="asset",
            relation="affects", sign="NEGATIVE", confidence=math.inf, source_id=source_id,
        )


def test_macro_source_ingestion_rejects_non_object_payload(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro-source.db"))
    with pytest.raises(ValueError, match="payload"):
        graph.ingest_source(title="Release", payload=["not", "an", "object"])


def test_event_reaction_rejects_malformed_identity_and_keeps_nan_as_unknown(tmp_path: Path):
    graph = MacroGraph(str(tmp_path / "macro-reaction.db"))
    source_id = graph.ingest_source(
        title="Official release", payload={"actual": 1.0},
        source_url="https://www.federalreserve.gov/", publisher="Federal Reserve",
    )
    with pytest.raises(ValueError, match="symbol"):
        graph.record_event_reaction(source_id, "", actual=math.nan, forecast=1.0,
                                    previous=0.5, windows={}, source_quality="verified")
    reaction_id = graph.record_event_reaction(
        source_id, "EURUSD", actual=math.nan, forecast=1.0, previous=0.5,
        windows={}, source_quality="verified",
    )
    with graph._con() as con:
        row = con.execute("SELECT actual,surprise FROM event_reactions WHERE id=?", (reaction_id,)).fetchone()
    assert row["actual"] is None
    assert row["surprise"] is None


@pytest.mark.parametrize("windows", [
    {"1m": math.nan},
    {"1m": 0},
    {"": 60},
])
def test_event_reaction_measurement_windows_fail_closed(tmp_path: Path, windows):
    graph = MacroGraph(str(tmp_path / "macro-window-validation.db"))
    result = graph.capture_due_event_reactions("EURUSD", 1.1, windows=windows)
    assert result["scheduled"] == 0
    assert result["measured"] == 0
    assert result["reason"].startswith("invalid reaction window")
