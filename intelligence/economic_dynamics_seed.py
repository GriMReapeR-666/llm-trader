"""Seed Economic Dynamics catalog into MacroGraph as CANDIDATE edges.

Fail-closed:
- Uses PRIMARY citation URL only (Fed FOMC page).
- Never auto-approves edges.
- Never sets execution_authority.
- Idempotent: skips duplicate (source,target,relation,sign) candidates.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from intelligence.economic_dynamics_catalog import (
        CATALOG_VERSION,
        CAUSAL_EDGES,
        CITATION_PUBLISHER,
        CITATION_URL,
        SOURCE_DOCUMENT,
        catalog_summary,
        entity_index,
        validate_catalog,
    )
except ImportError:
    from intelligence.economic_dynamics_catalog import (
        CATALOG_VERSION,
        CAUSAL_EDGES,
        CITATION_PUBLISHER,
        CITATION_URL,
        SOURCE_DOCUMENT,
        catalog_summary,
        entity_index,
        validate_catalog,
    )

try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph


def _edge_key(edge: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(edge["source_name"]).upper(),
        str(edge["target_name"]).upper(),
        str(edge["relation"]).upper(),
        str(edge["sign"]).upper(),
    )


def _existing_keys(graph: MacroGraph) -> Set[Tuple[str, str, str, str]]:
    with graph._con() as con:
        rows = con.execute(
            """SELECT e.relation, e.sign, s.name AS src, t.name AS tgt
               FROM macro_edges e
               JOIN macro_entities s ON s.entity_id = e.source_entity
               JOIN macro_entities t ON t.entity_id = e.target_entity
               WHERE e.status IN ('CANDIDATE','AUTO_APPROVED','APPROVED','MEASURED','STAT_PASS')"""
        ).fetchall()
    return {
        (str(r["src"]).upper(), str(r["tgt"]).upper(), str(r["relation"]).upper(), str(r["sign"]).upper())
        for r in rows
    }


def seed_economic_dynamics(
    graph: Optional[MacroGraph] = None,
    *,
    db_path: str = "trading_state.db",
    include_lag_metadata: bool = True,
) -> Dict[str, Any]:
    problems = validate_catalog()
    if problems:
        raise ValueError("catalog integrity failed: " + "; ".join(problems[:8]))

    graph = graph or MacroGraph(db_path)
    index = entity_index()
    source_id = graph.ingest_source(
        title=f"Economic Dynamics Catalog {CATALOG_VERSION}",
        source_url=CITATION_URL,
        publisher=CITATION_PUBLISHER,
        published_at="",
        rights_note="store factual payload/hash and citation; catalog is research hypothesis seed",
        version=CATALOG_VERSION,
        payload={
            "kind": "economic_dynamics_catalog_seed",
            "catalog_version": CATALOG_VERSION,
            "source_document": SOURCE_DOCUMENT,
            "summary": catalog_summary(),
            "execution_authority": False,
        },
    )

    existing = _existing_keys(graph)
    created: List[int] = []
    skipped = 0
    for raw in CAUSAL_EDGES:
        src_meta = index[raw["source_name"]]
        tgt_meta = index[raw["target_name"]]
        edge = {
            **raw,
            "source_type": src_meta["entity_type"],
            "target_type": tgt_meta["entity_type"],
        }
        key = _edge_key(edge)
        if key in existing:
            skipped += 1
            continue
        notes_payload = {
            "catalog_version": CATALOG_VERSION,
            "channel": edge.get("channel"),
            "active_regimes": edge.get("active_regimes"),
            "contradiction_group": edge.get("contradiction_group"),
            "user_notes": edge.get("notes"),
            "execution_authority": False,
        }
        if include_lag_metadata:
            notes_payload.update({
                "lag_aware": True,
                "min_lag_seconds": edge.get("min_lag_seconds"),
                "max_lag_seconds": edge.get("max_lag_seconds"),
                "decay_half_life_seconds": edge.get("decay_half_life_seconds"),
            })
        edge_id = graph.propose_edge(
            source_name=edge["source_name"],
            source_type=edge["source_type"],
            target_name=edge["target_name"],
            target_type=edge["target_type"],
            relation=edge["relation"],
            sign=edge["sign"],
            confidence=float(edge["confidence"]),
            source_id=source_id,
            notes=json.dumps(notes_payload, sort_keys=True),
        )
        created.append(int(edge_id))
        existing.add(key)

    return {
        "source_id": source_id,
        "catalog_version": CATALOG_VERSION,
        "created_edge_ids": created,
        "created_count": len(created),
        "skipped_duplicates": skipped,
        "execution_authority": False,
        "status": "CANDIDATE_SEED_COMPLETE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed economic dynamics catalog into MacroGraph")
    parser.add_argument("--db", default="trading_state.db")
    parser.add_argument("--no-lag", action="store_true")
    args = parser.parse_args()
    result = seed_economic_dynamics(db_path=args.db, include_lag_metadata=not args.no_lag)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
