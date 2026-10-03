"""Unified Knowledge Retrieval API

Single entry point for all knowledge retrieval across the system.
Orchestrates: macro causal graph, case studies, domain knowledge,
active knowledge agent, and semantic memory.

Provides:
1. Multi-source retrieval with relevance ranking
2. Cross-domain reasoning (macro → micro implications)
3. Confidence-weighted knowledge fusion
4. Knowledge graph traversal for related concepts
5. Temporal relevance scoring

Usage:
    kb = KnowledgeRetrieval()
    # Simple retrieval
    results = kb.query("OPEC supply shock impact on EURUSD")
    # Cross-domain reasoning
    implications = kb.reason(" What if Hormuz strait closes?")
    # Graph traversal
    related = kb.traverse("OPEC", depth=2)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("KnowledgeRetrieval")


class KnowledgeRetrieval:
    """Unified knowledge retrieval across all subsystems."""

    def __init__(self, db_path: str = ""):
        self.base_dir = str(Path(__file__).parent.parent)
        self.db_path = db_path or os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "knowledge_retrieval_state.json")
        self.state = self._load_state()

        # Lazy-load subsystems
        self._macro_graph = None
        self._case_matcher = None
        self._case_library = None
        self._active_kb = None
        self._semantic_memory = None

    def _load_state(self) -> Dict[str, Any]:
        """Load retrieval state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "total_queries": 0,
            "last_query": None,
            "source_stats": {},
        }

    def _save_state(self) -> None:
        """Save retrieval state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save state: %s", exc)

    def _get_macro_graph(self):
        """Lazy-load macro causal graph."""
        if self._macro_graph is None:
            try:
                from macro_graph import MacroGraph
                self._macro_graph = MacroGraph(self.db_path)
            except Exception as exc:
                log.debug("Macro graph unavailable: %s", exc)
        return self._macro_graph

    def _get_case_matcher(self):
        """Lazy-load case study matcher."""
        if self._case_matcher is None:
            try:
                from case_study_matcher import get_case_study_matcher
                self._case_matcher = get_case_study_matcher()
            except Exception as exc:
                log.debug("Case matcher unavailable: %s", exc)
        return self._case_matcher

    def _get_case_library(self):
        """Lazy-load case study library."""
        if self._case_library is None:
            try:
                from case_study_library import get_case_study_library
                self._case_library = get_case_study_library()
            except Exception as exc:
                log.debug("Case library unavailable: %s", exc)
        return self._case_library

    def _get_active_kb(self):
        """Lazy-load active knowledge agent."""
        if self._active_kb is None:
            try:
                from active_knowledge_agent import ACTIVE_KNOWLEDGE_AGENT
                self._active_kb = ACTIVE_KNOWLEDGE_AGENT
            except Exception as exc:
                log.debug("Active knowledge agent unavailable: %s", exc)
        return self._active_kb

    def _get_semantic_memory(self):
        """Lazy-load semantic memory."""
        if self._semantic_memory is None:
            try:
                import semantic_memory
                self._semantic_memory = semantic_memory
            except Exception as exc:
                log.debug("Semantic memory unavailable: %s", exc)
        return self._semantic_memory

    def query(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        limit: int = 10,
        min_confidence: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Query all knowledge sources and return fused results.

        Args:
            query: Natural language query
            sources: List of sources to query (None = all)
            limit: Maximum results per source
            min_confidence: Minimum confidence threshold

        Returns:
            Fused results with relevance ranking
        """
        if sources is None:
            sources = ["macro_graph", "case_studies", "domain_knowledge", "semantic_memory"]

        all_results = []
        source_timing = {}

        # Query each source
        for source in sources:
            t0 = time.time()
            try:
                results = self._query_source(source, query, limit)
                source_timing[source] = time.time() - t0

                for result in results:
                    result["source"] = source
                    result["query_time"] = source_timing[source]
                    all_results.append(result)

            except Exception as exc:
                log.debug("Source %s query failed: %s", source, exc)
                source_timing[source] = time.time() - t0

        # Rank and fuse results
        fused = self._fuse_results(all_results, min_confidence)
        fused = fused[:limit]

        # Update state
        self.state["total_queries"] += 1
        self.state["last_query"] = {
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results_count": len(fused),
            "sources": list(source_timing.keys()),
        }
        for source, timing in source_timing.items():
            if source not in self.state["source_stats"]:
                self.state["source_stats"][source] = {"count": 0, "total_time": 0}
            self.state["source_stats"][source]["count"] += 1
            self.state["source_stats"][source]["total_time"] += timing
        self._save_state()

        return {
            "query": query,
            "results": fused,
            "total_results": len(fused),
            "sources_queried": list(source_timing.keys()),
            "query_time_ms": sum(source_timing.values()) * 1000,
        }

    def _query_source(self, source: str, query: str, limit: int) -> List[Dict[str, Any]]:
        """Query a specific knowledge source."""
        if source == "macro_graph":
            return self._query_macro_graph(query, limit)
        elif source == "case_studies":
            return self._query_case_studies(query, limit)
        elif source == "domain_knowledge":
            return self._query_domain_knowledge(query, limit)
        elif source == "semantic_memory":
            return self._query_semantic_memory(query, limit)
        return []

    def _query_macro_graph(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Query the macro causal graph."""
        graph = self._get_macro_graph()
        if not graph:
            return []

        results = []
        try:
            # Extract entities from query
            entities = self._extract_entities(query)

            for entity in entities[:3]:
                edges = graph.query_entity(entity)
                for edge in edges[:limit]:
                    results.append({
                        "type": "macro_edge",
                        "entity": entity,
                        "target": edge.get("target", ""),
                        "sign": edge.get("sign", 0),
                        "confidence": edge.get("confidence", 0.5),
                        "source": edge.get("source", ""),
                        "content": edge,
                    })
        except Exception as exc:
            log.debug("Macro graph query failed: %s", exc)

        return results

    def _query_case_studies(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Query case study library."""
        matcher = self._get_case_matcher()
        library = self._get_case_library()

        if not matcher or not library:
            return []

        results = []
        try:
            # Extract domain from query
            domain = self._extract_domain(query)

            matches = matcher.match_event(
                event_domain=domain,
                affected_entities=self._extract_entities(query),
                top_k=limit,
            )

            for match in matches:
                case = library.get_case(match.matched_case_id)
                if case:
                    results.append({
                        "type": "case_study",
                        "case_id": case.case_id,
                        "label": case.label,
                        "similarity_score": match.similarity_score,
                        "tau_horizon": match.tau_horizon,
                        "projected_trajectory": match.projected_trajectory,
                        "content": {
                            "domain": case.primary_domain,
                            "entities": list(case.primary_entities),
                            "chokepoints": list(case.chokepoints_affected),
                            "lead_time_days": case.lead_time_days,
                            "duration_days": case.duration_days,
                        },
                    })
        except Exception as exc:
            log.debug("Case study query failed: %s", exc)

        return results

    def _query_domain_knowledge(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Query domain knowledge pillars."""
        results = []
        try:
            from multi_disciplinary_domain_knowledge import (
                evaluate_thucydides_trap,
                evaluate_regime_stability,
                evaluate_security_dilemma,
                evaluate_military_force_ratio,
                evaluate_dutch_disease,
                evaluate_geostrategy,
                evaluate_haber_bosch,
                evaluate_opec_spare_capacity,
                evaluate_semiconductor_chokepoints,
                evaluate_cross_currency_basis,
                evaluate_demographics,
                evaluate_multi_domain_synthesis,
            )

            # Evaluate all pillars
            pillars = {
                "thucydides_trap": evaluate_thucydides_trap,
                "regime_stability": evaluate_regime_stability,
                "security_dilemma": evaluate_security_dilemma,
                "military_force_ratio": evaluate_military_force_ratio,
                "dutch_disease": evaluate_dutch_disease,
                "geostrategy": evaluate_geostrategy,
                "haber_bosch": evaluate_haber_bosch,
                "opec_spare_capacity": evaluate_opec_spare_capacity,
                "semiconductor_chokepoints": evaluate_semiconductor_chokepoints,
                "cross_currency_basis": evaluate_cross_currency_basis,
                "demographics": evaluate_demographics,
                "multi_domain_synthesis": evaluate_multi_domain_synthesis,
            }

            keywords = set(query.lower().split())

            for name, eval_fn in pillars.items():
                try:
                    result = eval_fn()
                    # Check relevance
                    result_str = json.dumps(result, default=str).lower()
                    relevance = sum(1 for kw in keywords if kw in result_str) / max(len(keywords), 1)

                    if relevance > 0.1:
                        results.append({
                            "type": "domain_knowledge",
                            "pillar": name,
                            "relevance": relevance,
                            "content": result,
                        })
                except Exception:
                    pass

            results.sort(key=lambda x: x["relevance"], reverse=True)
            results = results[:limit]

        except Exception as exc:
            log.debug("Domain knowledge query failed: %s", exc)

        return results

    def _query_semantic_memory(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Query semantic memory."""
        sem = self._get_semantic_memory()
        if not sem:
            return []

        results = []
        try:
            search_results = sem.search(self.db_path, query=query, limit=limit)
            for item in search_results:
                results.append({
                    "type": "semantic_memory",
                    "score": item.get("score", 0),
                    "content": item.get("content", {}),
                    "source": item.get("source", ""),
                })
        except Exception as exc:
            log.debug("Semantic memory query failed: %s", exc)

        return results

    def _fuse_results(self, results: List[Dict[str, Any]], min_confidence: float) -> List[Dict[str, Any]]:
        """Fuse and rank results from multiple sources."""
        # Score each result
        for result in results:
            base_score = 0.0

            # Source-specific scoring
            source = result.get("source", "")
            if source == "macro_graph":
                base_score = result.get("confidence", 0.5) * 0.8
            elif source == "case_studies":
                base_score = result.get("similarity_score", 0.5) * 0.9
            elif source == "domain_knowledge":
                base_score = result.get("relevance", 0.5) * 0.7
            elif source == "semantic_memory":
                base_score = result.get("score", 0.5) * 0.6

            result["relevance_score"] = base_score

        # Sort by relevance
        results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        # Filter by minimum confidence
        results = [r for r in results if r.get("relevance_score", 0) >= min_confidence]

        return results

    def _extract_entities(self, query: str) -> List[str]:
        """Extract named entities from query."""
        # Simple entity extraction (would use NER in production)
        entities = []
        keywords = query.upper().split()

        # Common trading/macro entities
        known_entities = [
            "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USOIL", "WTI",
            "OPEC", "FED", "ECB", "BOJ", "NFP", "CPI", "GDP",
            "HORMUZ", "SUEZ", "PANAMA", "CHINA", "RUSSIA", "IRAN",
            "SEMICONDUCTOR", "CHIP", "OIL", "GOLD", "BOND",
        ]

        for kw in keywords:
            if kw in known_entities:
                entities.append(kw)

        return entities[:5]

    def _extract_domain(self, query: str) -> str:
        """Extract domain from query."""
        query_lower = query.lower()

        if any(kw in query_lower for kw in ["oil", "opec", "supply", "production"]):
            return "ENERGY"
        elif any(kw in query_lower for kw in ["chip", "semiconductor", "tech"]):
            return "TECHNOLOGY"
        elif any(kw in query_lower for kw in ["war", "military", "conflict", "sanction"]):
            return "GEOPOLITICS"
        elif any(kw in query_lower for kw in ["currency", "forex", "fx", "dollar"]):
            return "FX"
        elif any(kw in query_lower for kw in ["bond", "yield", "rate", "fed"]):
            return "FIXED_INCOME"

        return "GENERAL"

    def traverse(
        self,
        entity: str,
        depth: int = 2,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Traverse the knowledge graph from an entity."""
        graph = self._get_macro_graph()
        if not graph:
            return {"entity": entity, "connections": [], "error": "macro_graph_unavailable"}

        connections = []
        visited = set()

        def _traverse(current: str, current_depth: int):
            if current_depth > depth or current in visited:
                return
            visited.add(current)

            try:
                edges = graph.query_entity(current)
                for edge in edges[:limit]:
                    target = edge.get("target", "")
                    connections.append({
                        "source": current,
                        "target": target,
                        "sign": edge.get("sign", 0),
                        "confidence": edge.get("confidence", 0.5),
                        "depth": current_depth,
                    })
                    if current_depth < depth:
                        _traverse(target, current_depth + 1)
            except Exception:
                pass

        _traverse(entity, 1)

        return {
            "entity": entity,
            "depth": depth,
            "connections": connections,
            "total_connections": len(connections),
        }

    def reason(self, scenario: str) -> Dict[str, Any]:
        """
        Cross-domain reasoning for a scenario.

        Combines macro graph implications, historical case studies,
        and domain knowledge to produce a multi-faceted analysis.
        """
        # Query all sources
        query_result = self.query(scenario, limit=5)

        # Extract implications
        implications = []
        for result in query_result["results"]:
            source = result.get("source", "")
            content = result.get("content", {})

            if source == "macro_graph":
                implications.append({
                    "type": "causal_implication",
                    "entity": result.get("entity", ""),
                    "target": result.get("target", ""),
                    "sign": result.get("sign", 0),
                    "confidence": result.get("confidence", 0.5),
                })
            elif source == "case_studies":
                implications.append({
                    "type": "historical_parallel",
                    "case_id": result.get("case_id", ""),
                    "label": result.get("label", ""),
                    "similarity": result.get("similarity_score", 0),
                    "projected_trajectory": result.get("projected_trajectory", ""),
                })
            elif source == "domain_knowledge":
                implications.append({
                    "type": "domain_insight",
                    "pillar": result.get("pillar", ""),
                    "relevance": result.get("relevance", 0),
                })

        # Synthesize
        synthesis = {
            "scenario": scenario,
            "implications": implications,
            "total_implications": len(implications),
            "confidence": sum(i.get("confidence", i.get("similarity", i.get("relevance", 0.5)))
                             for i in implications) / max(len(implications), 1),
            "sources": query_result.get("sources_queried", []),
        }

        return synthesis

    def get_stats(self) -> Dict[str, Any]:
        """Get retrieval statistics."""
        return {
            "total_queries": self.state.get("total_queries", 0),
            "last_query": self.state.get("last_query"),
            "source_stats": self.state.get("source_stats", {}),
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Knowledge Retrieval")
    parser.add_argument("--query", type=str, help="Query to search")
    parser.add_argument("--traverse", type=str, help="Traverse from entity")
    parser.add_argument("--reason", type=str, help="Reason about scenario")
    parser.add_argument("--depth", type=int, default=2, help="Traversal depth")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    kb = KnowledgeRetrieval()

    if args.query:
        result = kb.query(args.query)
        print(json.dumps(result, indent=2, default=str))
    elif args.traverse:
        result = kb.traverse(args.traverse, depth=args.depth)
        print(json.dumps(result, indent=2, default=str))
    elif args.reason:
        result = kb.reason(args.reason)
        print(json.dumps(result, indent=2, default=str))
    elif args.stats:
        print(json.dumps(kb.get_stats(), indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
