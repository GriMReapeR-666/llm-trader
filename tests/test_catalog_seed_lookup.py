"""Regression: the catalog seed must stay findable as macro_sources grows.

`find_catalog_seed_source_id` used to scan only the 200 most-recently-captured
sources and JSON-parse each. The seed is written once per catalog version while
macro_sources accrues a row per news article per cycle, so the seed eventually
ranked ~76k down, the scan returned None, and every mirroring attempt bailed at
the "seed missing" guard. That guard returns a normal result rather than
raising, so the whole causal-evidence path failed silently: zero mirrored
reactions, zero approved edges, an empty GraphRAG traversal.
"""
from __future__ import annotations

import json

from intelligence.economic_dynamics_measure import (
    find_catalog_seed_source_id,
    mirror_recent_reactions,
)
from intelligence.economic_dynamics_seed import seed_economic_dynamics
from intelligence.macro_graph import MacroGraph


def _seeded_graph(tmp_path):
    graph = MacroGraph(str(tmp_path / "seed_lookup.db"))
    seed = seed_economic_dynamics(graph=graph)
    assert seed["created_count"] > 0
    return graph, seed["source_id"]


def _windows():
    return {"15m": {"return_fraction": 0.002}}


def test_seed_found_immediately_after_seeding(tmp_path):
    graph, seed_id = _seeded_graph(tmp_path)
    assert find_catalog_seed_source_id(graph) == seed_id


def test_seed_found_despite_more_than_200_newer_sources(tmp_path):
    """The exact regression: 250 newer rows used to push the seed out of range."""
    graph, seed_id = _seeded_graph(tmp_path)
    graph.ingest_sources_bulk([
        {
            "title": f"Filler news article {i}",
            "publisher": "Filler Wire",
            "source_url": f"https://example.invalid/{i}",
            "payload": {"kind": "news_article", "seq": i},
        }
        for i in range(250)
    ])

    assert find_catalog_seed_source_id(graph) == seed_id


def test_mirroring_is_not_blocked_by_older_seed_rows(tmp_path):
    """A stale duplicate seed must not shadow the newest one."""
    graph, seed_id = _seeded_graph(tmp_path)
    # Older copy of the same catalog version, as repeated seeding produced.
    graph.ingest_source(
        title="Economic Dynamics Catalog 1.0.0",
        source_url="https://example.invalid/catalog",
        publisher="Example",
        payload={"kind": "economic_dynamics_catalog_seed", "stale": True},
    )
    graph.ingest_sources_bulk([
        {
            "title": f"Filler news article {i}",
            "publisher": "Filler Wire",
            "source_url": f"https://example.invalid/{i}",
            "payload": {"kind": "news_article", "seq": i},
        }
        for i in range(250)
    ])

    # Still resolves, and to a real seed.
    assert find_catalog_seed_source_id(graph) is not None


def test_seed_run_consolidates_edges_onto_one_canonical_source(tmp_path):
    """Edges must not stay split across superseded catalog snapshots.

    Mirroring writes every reaction to the newest seed, and the validator
    resolves samples by edge.source_id -- so an edge citing an older snapshot
    matches no reaction forever.
    """
    graph = MacroGraph(str(tmp_path / "consolidate.db"))
    first = seed_economic_dynamics(graph=graph)
    with graph._con() as con:
        con.execute(
            "UPDATE macro_edges SET source_id=? WHERE source_id=?",
            ("macro:deadbeefdeadbeefdeadbeef", first["source_id"]),
        )
        # Plant the stale id as a real catalog-seed row, as history holds.
        con.execute(
            "INSERT INTO macro_sources(source_id,captured_at,title,content_hash,version,payload)"
            " VALUES(?,?,?,?,?,?)",
            (
                "macro:deadbeefdeadbeefdeadbeef",
                "2020-01-01T00:00:00+00:00",
                "Economic Dynamics Catalog 1.0.0",
                "deadbeef",
                "1.0.0",
                json.dumps({"kind": "economic_dynamics_catalog_seed"}),
            ),
        )

    second = seed_economic_dynamics(graph=graph)

    assert second["repointed_from_stale_seed"] > 0
    with graph._con() as con:
        distinct = {r[0] for r in con.execute("SELECT DISTINCT source_id FROM macro_edges")}
        history = con.execute(
            "SELECT COUNT(*) FROM macro_edge_history WHERE action='SEED_CONSOLIDATED'"
        ).fetchone()[0]
    assert distinct == {second["source_id"]}
    assert history > 0


def test_consolidation_preserves_edge_status_and_confidence(tmp_path):
    graph = MacroGraph(str(tmp_path / "preserve.db"))
    seed = seed_economic_dynamics(graph=graph)
    with graph._con() as con:
        con.execute(
            "UPDATE macro_edges SET status='APPROVED', confidence=0.91 WHERE edge_id="
            "(SELECT MIN(edge_id) FROM macro_edges)"
        )

    seed_economic_dynamics(graph=graph)

    with graph._con() as con:
        row = con.execute(
            "SELECT status,confidence FROM macro_edges WHERE status='APPROVED'"
        ).fetchone()
    assert row is not None
    assert round(row["confidence"], 3) == 0.91


def test_non_catalog_edges_are_never_repointed(tmp_path):
    graph = MacroGraph(str(tmp_path / "foreign.db"))
    seed = seed_economic_dynamics(graph=graph)
    other = graph.ingest_source(
        title="Some research note",
        publisher="Example",
        source_url="https://example.invalid/n",
        payload={"kind": "research_note"},
    )
    with graph._con() as con:
        con.execute(
            "UPDATE macro_edges SET source_id=? WHERE edge_id=(SELECT MIN(edge_id) FROM macro_edges)",
            (other,),
        )

    seed_economic_dynamics(graph=graph)

    with graph._con() as con:
        still = con.execute(
            "SELECT COUNT(*) FROM macro_edges WHERE source_id=?", (other,)
        ).fetchone()[0]
    assert still == 1


def test_reaction_without_own_title_uses_source_title(tmp_path):
    """A reaction row carries no headline; its source row supplies one.

    Without this fallback `classify_runtime_event` received an empty string and
    every reaction skipped as unmapped regardless of the seed lookup.
    """
    graph, seed_id = _seeded_graph(tmp_path)
    source_id = graph.ingest_source(
        title="US Non-Farm Payrolls beat expectations",
        publisher="Example Wire",
        source_url="https://example.invalid/nfp",
        payload={"kind": "calendar_event", "name": "Non-Farm Payrolls"},
    )
    graph.record_event_reaction(
        event_source_id=source_id,
        symbol="EURUSD",
        actual=250,
        forecast=180,
        previous=200,
        windows=_windows(),
        source_quality="TEST_CALENDAR",
    )
    with graph._con() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM event_reactions")]

    result = mirror_recent_reactions(
        graph, symbol="EURUSD", news_events=[], recent_reactions=rows
    )

    assert result["catalog_source_id"] == seed_id
    assert result["mirrored_count"] == 1
    assert result["skip_reasons"] == {}


def test_unclassifiable_reaction_reports_reason_not_silent_skip(tmp_path):
    """Non-event sources (e.g. FRED yield-curve level releases) must stay
    rejected -- but the rejection is attributed, not silently swallowed."""
    graph, _ = _seeded_graph(tmp_path)
    source_id = graph.ingest_source(
        title="FRED level release: T10Y2Y 2026-09-24",
        publisher="FRED",
        source_url="https://example.invalid/fred",
        payload={"kind": "fred_level_release"},
    )
    graph.record_event_reaction(
        event_source_id=source_id,
        symbol="EURUSD",
        actual=-0.4,
        forecast=-0.3,
        previous=-0.35,
        windows=_windows(),
        source_quality="TEST_FRED",
    )
    with graph._con() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM event_reactions")]

    result = mirror_recent_reactions(
        graph, symbol="EURUSD", news_events=[], recent_reactions=rows
    )

    assert result["mirrored_count"] == 0
    assert result["skip_reasons"] == {"event not mapped to catalog EVENT_CLASS": 1}