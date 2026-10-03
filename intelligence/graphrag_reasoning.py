"""Multi-Hop Causal GraphRAG Traversal and Cited Prompt Synthesis.

Explores 2-to-4 hop causal transmission pathways from macro shocks (CPI, NFP, Fed)
to tradable target assets (EURUSD, XAUUSD, USOIL), computing compound signs,
damped path confidences, lag delays, and cited LLM prompt context.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from intelligence.macro_graph import MacroGraph


@dataclass
class CausalStep:
    """A single hop along a causal pathway."""
    source: str
    target: str
    relation: str
    sign: str  # "POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN"
    confidence: float
    lag_hours: float = 0.0
    source_url: str = ""
    notes: str = ""


@dataclass
class CausalPath:
    """A multi-hop causal transmission chain connecting a shock to a tradable asset."""
    source: str
    target: str
    hops: int
    steps: List[CausalStep]
    nodes: List[str]
    net_sign: str  # "POSITIVE", "NEGATIVE", "CONDITIONAL"
    composite_confidence: float
    total_lag_hours: float


class CausalGraphRAGEngine:
    """Multi-Hop Causal GraphRAG reasoner built over institutional SQLite MacroGraph."""

    def __init__(self, macro_graph: MacroGraph, damping_factor: float = 0.88):
        self.macro_graph = macro_graph
        self.damping_factor = damping_factor

    def _load_adjacency(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load approved causal edges into an in-memory adjacency list for fast path finding."""
        sql = """
            SELECT e.*, a.name AS source_name, b.name AS target_name, s.source_url, s.title
            FROM macro_edges e
            JOIN macro_entities a ON a.entity_id = e.source_entity
            JOIN macro_entities b ON b.entity_id = e.target_entity
            JOIN macro_sources s ON s.source_id = e.source_id
            WHERE e.status IN ('APPROVED', 'AUTO_APPROVED')
              AND (e.valid_until IS NULL OR e.valid_until = '' OR e.valid_until >= datetime('now'))
        """
        adjacency: Dict[str, List[Dict[str, Any]]] = {}
        with self.macro_graph._con() as con:
            rows = con.execute(sql).fetchall()
            for r in rows:
                s_name = r["source_name"]
                if s_name not in adjacency:
                    adjacency[s_name] = []
                adjacency[s_name].append(dict(r))
        return adjacency

    def find_causal_paths(
        self,
        source_entities: List[str],
        target_entities: List[str],
        max_hops: int = 4,
    ) -> List[CausalPath]:
        """Find all multi-hop causal paths from any source entity to any target entity."""
        adjacency = self._load_adjacency()
        target_set = {t.upper() for t in target_entities}
        discovered_paths: List[CausalPath] = []

        for start_ent in source_entities:
            start_name = start_ent.upper()
            # Queue stores: (current_node, list_of_steps, visited_nodes_set)
            queue: deque = deque([(start_name, [], [start_name])])

            while queue:
                current_node, steps, visited = queue.popleft()

                if len(steps) >= max_hops:
                    continue

                for edge in adjacency.get(current_node, []):
                    nxt = edge["target_name"]
                    if nxt in visited:
                        continue  # Avoid cycles

                    step = CausalStep(
                        source=current_node,
                        target=nxt,
                        relation=edge.get("relation", "INFLUENCES"),
                        sign=edge.get("sign", "POSITIVE"),
                        confidence=float(edge.get("confidence", 0.8)),
                        lag_hours=float(edge.get("lag_hours", 0.0) or 0.0),
                        source_url=edge.get("source_url", ""),
                        notes=edge.get("notes", ""),
                    )
                    new_steps = steps + [step]
                    new_visited = visited + [nxt]

                    if nxt in target_set:
                        # Found a path to one of the targets!
                        path_obj = self._compile_path(new_steps, new_visited)
                        discovered_paths.append(path_obj)
                    else:
                        queue.append((nxt, new_steps, new_visited))

        # Sort paths by composite confidence descending (most confident path first)
        discovered_paths.sort(key=lambda p: p.composite_confidence, reverse=True)
        return discovered_paths

    def _compile_path(self, steps: List[CausalStep], nodes: List[str]) -> CausalPath:
        """Calculate compound sign, damped composite confidence, and total lag hours."""
        net_sign_multiplier = 1
        has_conditional = False
        comp_confidence = 1.0
        total_lag = 0.0

        for step in steps:
            if step.sign.upper() in ("NEGATIVE", "-1", "INVERSE"):
                net_sign_multiplier *= -1
            elif step.sign.upper() in ("CONDITIONAL", "UNKNOWN"):
                has_conditional = True

            comp_confidence *= step.confidence
            total_lag += step.lag_hours

        # Apply multi-hop damping: gamma^(hops - 1)
        hops = len(steps)
        damped_confidence = comp_confidence * (self.damping_factor ** max(0, hops - 1))

        if has_conditional:
            net_sign = "CONDITIONAL"
            damped_confidence *= 0.8
        else:
            net_sign = "POSITIVE" if net_sign_multiplier > 0 else "NEGATIVE"

        return CausalPath(
            source=nodes[0],
            target=nodes[-1],
            hops=hops,
            steps=steps,
            nodes=nodes,
            net_sign=net_sign,
            composite_confidence=round(damped_confidence, 3),
            total_lag_hours=round(total_lag, 2),
        )

    def format_cited_prompt_context(self, paths: List[CausalPath], max_paths: int = 5) -> str:
        """Format discovered causal chains into a cited Markdown block for LLM prompt injection."""
        if not paths:
            return "No verified causal transmission paths discovered in Macro Knowledge Graph."

        lines = [
            "### Institutional Causal Transmission Paths (MacroGraph Verified)",
            "The following verified multi-hop causal transmission mechanisms link current macro conditions to tradable assets:",
            "",
        ]

        for i, path in enumerate(paths[:max_paths], 1):
            chain_str = " ➔ ".join(path.nodes)
            lines.append(f"**Path {i}: {chain_str}**")
            lines.append(f"- **Net Impact**: `{path.net_sign}` on `{path.target}` (Source: `{path.source}`)")
            lines.append(f"- **Confidence**: `{path.composite_confidence:.1%}` | **Cumulative Lag**: `~{path.total_lag_hours:.1f}h` | **Hops**: `{path.hops}`")
            
            # Show individual step citations
            step_details = []
            for s in path.steps:
                sign_sym = "(+)" if s.sign == "POSITIVE" else ("(-)" if s.sign == "NEGATIVE" else "(~)")
                note_str = f" [{s.notes}]" if s.notes else ""
                url_str = f" [ref: {s.source_url}]" if s.source_url else ""
                step_details.append(f"  - `{s.source}` {sign_sym}➔ `{s.target}` ({s.relation}, conf: {s.confidence:.2f}){note_str}{url_str}")
            
            lines.extend(step_details)
            lines.append("")

        return "\n".join(lines)
