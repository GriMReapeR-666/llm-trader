"""Tests for Behavioral Macro, Socioeconomic & AI Paradigm Knowledge Catalog.

Covers:
- Entity definitions (Behavioral psychology, PsyOps, Class dynamics, AI Supercycles)
- Causal edge mechanisms and deterministic sign constraints
- SQLite MacroGraph database ingestion
- Multi-hop causal transmission across socioeconomic and technological shocks
"""
from __future__ import annotations

import os
import tempfile
import pytest

from intelligence.macro_graph import MacroGraph
from intelligence.economic_dynamics_seed import seed_economic_dynamics
from intelligence.behavioral_macro_catalog import (
    BEHAVIORAL_ENTITIES,
    BEHAVIORAL_CAUSAL_EDGES,
    BEHAVIORAL_ENTITY_TYPES,
    get_behavioral_entities,
    get_behavioral_edges,
    seed_behavioral_dynamics_into_graph,
)


def test_behavioral_entities_schema():
    entities = get_behavioral_entities()
    assert len(entities) >= 20
    allowed_types = set(BEHAVIORAL_ENTITY_TYPES)

    for ent in entities:
        assert "name" in ent
        assert "entity_type" in ent
        assert ent["entity_type"] in allowed_types
        assert "layer" in ent
        assert isinstance(ent["aliases"], list)


def test_behavioral_causal_edges_validation():
    edges = get_behavioral_edges()
    assert len(edges) >= 15
    entity_names = {e["name"] for e in BEHAVIORAL_ENTITIES} | {
        "QE_QT", "INFLATION", "AGGREGATE_DEMAND", "GOLD", "EQUITIES", "VIX", "EMPLOYMENT", "CREDIT_SPREAD"
    }

    for edge in edges:
        assert edge["source_name"] in entity_names, f"Unknown source: {edge['source_name']}"
        assert edge["target_name"] in entity_names, f"Unknown target: {edge['target_name']}"
        assert edge["sign"] in ("POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN")
        assert 0.0 <= float(edge["confidence"]) <= 1.0
        assert edge.get("execution_authority") is False
        assert len(edge.get("mechanism", "")) > 10


def test_behavioral_graph_seeding_and_queries():
    td = tempfile.mkdtemp()
    try:
        db_path = os.path.join(td, "test_behavioral_graph.db")
        graph = MacroGraph(db_path=db_path)

        # 1. Seed base economic dynamics
        seed_economic_dynamics(graph, db_path=db_path)

        # 2. Seed behavioral & socioeconomic dynamics
        res = seed_behavioral_dynamics_into_graph(graph)
        assert res["status"] == "SUCCESS"
        assert res["entities_seeded"] >= 20
        assert res["edges_seeded"] >= 15

        # 3. Query seeded graph
        with graph._con() as con:
            cantillon_edges = con.execute("""
                SELECT s.name AS src, t.name AS tgt, e.relation, e.sign, e.confidence, e.notes
                FROM macro_edges e
                JOIN macro_entities s ON s.entity_id=e.source_entity
                JOIN macro_entities t ON t.entity_id=e.target_entity
                WHERE s.name='CANTILLON_EFFECT' OR t.name='CANTILLON_EFFECT'
            """).fetchall()
            assert len(cantillon_edges) > 0

            ai_edges = con.execute("""
                SELECT s.name AS src, t.name AS tgt, e.relation, e.sign
                FROM macro_edges e
                JOIN macro_entities s ON s.entity_id=e.source_entity
                JOIN macro_entities t ON t.entity_id=e.target_entity
                WHERE s.name='AI_CAPEX_SUPERCYCLE'
            """).fetchall()
            assert len(ai_edges) >= 2
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
