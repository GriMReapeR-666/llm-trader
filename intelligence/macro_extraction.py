"""Strict, candidate-only macro fact extraction using the configured LLM.

The extractor converts a cited source into bounded JSON candidates.  It never
approves a causal edge, changes a policy, or reaches the order path.  Every
candidate must retain a source span so a reviewer can compare it with the
original source before promotion.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph

try:
    from intelligence.macro_source_policy import assess_source
except ImportError:
    from intelligence.macro_source_policy import assess_source

try:
    from intelligence.trading_memory import TradingMemory
except ImportError:
    from intelligence.trading_memory import TradingMemory


SCHEMA_VERSION = "macro-extraction-v1"
MAX_SOURCE_CHARS = 16000
MAX_ITEMS = 30


def _parse_json(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("macro extraction must be a JSON object")
    return parsed


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def extract_candidates(llm: Any, *, source_id: str, source_title: str,
                       source_text: str, source_url: str = "",
                       publisher: str = "", rights_note: str = "",
                       model: str = "") -> Dict[str, Any]:
    """Ask the configured provider for reviewable, source-span-bound facts."""
    if not source_id.strip():
        raise ValueError("source_id is required")
    source_policy = assess_source(source_url, publisher, rights_note)
    if source_policy.get("allowed") is not True:
        raise ValueError("source policy rejects macro extraction; use an allow-listed primary or runtime source")
    text = _bounded_text(source_text, MAX_SOURCE_CHARS)
    if not text:
        raise ValueError("source_text is required")
    prompt = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "source_title": source_title[:500],
        "source_url": source_url[:2000],
        "publisher": publisher[:500],
        "rights_note": rights_note[:1000] or source_policy.get("retention", ""),
        "source_policy": source_policy,
        "source_text": text,
        "instructions": [
            "Return JSON only; do not use markdown.",
            "Extract facts, not trading advice or orders.",
            "Every principle, relationship, precedent, and uncertainty must include an exact short source_span.",
            "Mark causal relationships as hypotheses; never claim certainty.",
            "Do not invent missing values, dates, entities, or market reactions.",
        ],
        "output": {
            "principles": [{"text": "", "confidence": 0.0, "source_span": ""}],
            "causal_relationships": [{
                "source_entity": "", "source_type": "economic_variable",
                "target_entity": "", "target_type": "asset_or_variable",
                "relation": "", "sign": "CONDITIONAL", "confidence": 0.0,
                "source_span": "", "uncertainty": ""
            }],
            "historical_precedents": [{"label": "", "date_or_period": "", "outcome": "", "source_span": ""}],
            "uncertainties": [{"text": "", "source_span": ""}],
        },
    }
    raw = llm.generate([
        {"role": "system", "content": "Extract cited macro facts. Output strict JSON only."},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ], model=model or None)
    parsed = _parse_json(raw)

    def list_items(key: str) -> List[Dict[str, Any]]:
        value = parsed.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        return [item for item in value[:MAX_ITEMS] if isinstance(item, dict)]

    principles, relations = list_items("principles"), list_items("causal_relationships")
    precedents, uncertainties = list_items("historical_precedents"), list_items("uncertainties")
    for collection_name, items in (("principles", principles), ("causal_relationships", relations),
                                   ("historical_precedents", precedents), ("uncertainties", uncertainties)):
        for item in items:
            if not _bounded_text(item.get("source_span"), 800):
                raise ValueError(f"{collection_name} item missing source_span")
    for relation in relations:
        if not _bounded_text(relation.get("source_entity"), 200) or not _bounded_text(relation.get("target_entity"), 200):
            raise ValueError("causal relationship requires source_entity and target_entity")
        if str(relation.get("sign", "CONDITIONAL")).upper() not in {"POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN"}:
            raise ValueError("causal relationship has invalid sign")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "source_title": source_title[:500],
        "source_url": source_url[:2000],
        "publisher": publisher[:500],
        "rights_note": rights_note[:1000] or source_policy.get("retention", ""),
        "source_policy": source_policy,
        "principles": principles,
        "causal_relationships": relations,
        "historical_precedents": precedents,
        "uncertainties": uncertainties,
        "execution_authority": False,
        "status": "CANDIDATE_REVIEW_REQUIRED",
    }


def persist_candidates(graph: MacroGraph, memory: TradingMemory,
                       candidates: Dict[str, Any], *, reviewer: str = "") -> Dict[str, Any]:
    """Persist candidates as L1 facts and candidate graph edges only."""
    source_id = str(candidates.get("source_id", ""))
    if not source_id:
        raise ValueError("candidate payload requires source_id")
    source_policy = candidates.get("source_policy") or {}
    if source_policy.get("allowed") is not True:
        raise ValueError("candidate source is not approved by source policy")
    edge_ids: List[int] = []
    fact_ids: List[int] = []
    provenance = {
        "source_id": source_id,
        "source_title": candidates.get("source_title", ""),
        "source_url": candidates.get("source_url", ""),
        "publisher": candidates.get("publisher", ""),
        "rights_note": candidates.get("rights_note", ""),
        "source_policy": source_policy,
        "extractor": SCHEMA_VERSION,
    }
    for item in candidates.get("principles", [])[:MAX_ITEMS]:
        fact_ids.append(memory.record_fact(
            source_id, "macro.principle", {**provenance, "text": item.get("text", ""),
                                            "source_span": item.get("source_span", "")},
            fact_status="PROPOSED", source="llm_candidate", confidence=float(item.get("confidence", 0.0) or 0.0),
        ))
    for item in candidates.get("historical_precedents", [])[:MAX_ITEMS]:
        fact_ids.append(memory.record_fact(
            source_id, "macro.historical_precedent", {**provenance,
                "label": item.get("label", ""), "date_or_period": item.get("date_or_period", ""),
                "outcome": item.get("outcome", ""), "source_span": item.get("source_span", ""),
            }, fact_status="PROPOSED", source="llm_candidate", confidence=0.0,
        ))
    for item in candidates.get("uncertainties", [])[:MAX_ITEMS]:
        fact_ids.append(memory.record_fact(
            source_id, "macro.uncertainty", {**provenance, "text": item.get("text", ""),
                                              "source_span": item.get("source_span", "")},
            fact_status="PROPOSED", source="llm_candidate", confidence=0.0,
        ))
    for item in candidates.get("causal_relationships", [])[:MAX_ITEMS]:
        notes = json.dumps({**provenance, "source_span": item.get("source_span", ""),
                            "uncertainty": item.get("uncertainty", ""),
                            "reviewer": reviewer}, default=str)
        edge_ids.append(graph.propose_edge(
            source_name=_bounded_text(item.get("source_entity"), 200),
            source_type=_bounded_text(item.get("source_type"), 100) or "economic_variable",
            target_name=_bounded_text(item.get("target_entity"), 200),
            target_type=_bounded_text(item.get("target_type"), 100) or "asset_or_variable",
            relation=_bounded_text(item.get("relation"), 200),
            sign=str(item.get("sign", "CONDITIONAL")).upper(),
            confidence=max(0.0, min(float(item.get("confidence", 0.0) or 0.0), 1.0)),
            source_id=source_id, notes=notes,
        ))
    return {"source_id": source_id, "fact_ids": fact_ids, "edge_ids": edge_ids,
            "status": "CANDIDATE_REVIEW_REQUIRED", "execution_authority": False}


def status(graph: MacroGraph) -> Dict[str, Any]:
    """Return candidate extraction counts; approved execution influence is impossible."""
    with graph._con() as con:
        source_count = con.execute(
            "SELECT COUNT(*) FROM macro_sources WHERE payload LIKE '%historical_regime_observation%' OR payload LIKE '%macro-extraction-v1%'"
        ).fetchone()[0]
        edge_counts = {str(row[0]): int(row[1]) for row in con.execute(
            "SELECT status,COUNT(*) FROM macro_edges GROUP BY status"
        ).fetchall()}
    return {"source_records": int(source_count), "edge_counts": edge_counts,
            "schema_version": SCHEMA_VERSION, "execution_authority": False,
            "status": "CANDIDATE_REVIEW_REQUIRED" if source_count else "NO_CANDIDATES"}
