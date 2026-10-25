"""Unified Memory Orchestrator

Orchestrates all memory subsystems into a coherent hierarchy:
- L0: Working Memory (active context, current trade state)
- L1: Episodic Memory (recent trades, scenarios, lessons)
- L2: Semantic Memory (patterns, narratives, verified knowledge)
- L3: Core Memory (permanent axioms, domain knowledge)

Provides:
1. Automatic consolidation from L0 → L1 → L2 → L3
2. Memory decay with importance-weighted forgetting
3. Cross-memory retrieval with relevance ranking
4. Memory health monitoring and compaction

Usage:
    orchestrator = MemoryOrchestrator(db_path="trading_state.db")
    # Store a new memory
    orchestrator.store("episodic", {"trade": "EURUSD", "outcome": "win", "lesson": "..."})
    # Retrieve relevant memories
    memories = orchestrator.retrieve("regime transition strategies", limit=10)
    # Consolidate short-term to long-term
    orchestrator.consolidate()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("MemoryOrchestrator")


class MemoryOrchestrator:
    """Unified orchestrator for all memory subsystems."""

    LAYERS = {
        "working": {"max_items": 100, "ttl_hours": 24, "decay_rate": 0.1},
        "episodic": {"max_items": 1000, "ttl_hours": 720, "decay_rate": 0.05},
        "semantic": {"max_items": 5000, "ttl_hours": 8760, "decay_rate": 0.01},
        "core": {"max_items": 500, "ttl_hours": None, "decay_rate": 0.0},
    }

    def __init__(self, db_path: str = ""):
        self.base_dir = str(Path(__file__).parent.parent)
        self.db_path = db_path or os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "memory_orchestrator_state.json")
        self.state = self._load_state()
        self._init_db()

    def _load_state(self) -> Dict[str, Any]:
        """Load orchestrator state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_consolidation": None,
            "last_decay": None,
            "total_stored": 0,
            "total_retrieved": 0,
            "total_consolidated": 0,
            "total_forgotten": 0,
        }

    def _save_state(self) -> None:
        """Save orchestrator state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save state: %s", exc)

    def _init_db(self) -> None:
        """Initialize unified memory table."""
        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.execute("""
                CREATE TABLE IF NOT EXISTS unified_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    layer TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding_hash TEXT,
                    importance REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TEXT,
                    source TEXT,
                    tags TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT,
                    consolidated_to INTEGER
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_unified_memory_layer
                ON unified_memory(layer)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_unified_memory_type
                ON unified_memory(memory_type)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_unified_memory_importance
                ON unified_memory(importance DESC)
            """)
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to init DB: %s", exc)

    def store(
        self,
        layer: str,
        content: Dict[str, Any],
        memory_type: str = "general",
        importance: float = 0.5,
        source: str = "",
        tags: Optional[List[str]] = None,
        ttl_hours: Optional[int] = None,
    ) -> int:
        """Store a memory in the specified layer."""
        if layer not in self.LAYERS:
            raise ValueError(f"Invalid layer: {layer}. Must be one of: {list(self.LAYERS.keys())}")

        config = self.LAYERS[layer]
        expires_at = None
        if ttl_hours or config["ttl_hours"]:
            expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours or config["ttl_hours"])
            expires_at = expires.isoformat()

        content_str = json.dumps(content, default=str)
        tags_str = json.dumps(tags or [])

        # Generate embedding hash for deduplication
        import hashlib
        embedding_hash = hashlib.md5(content_str.encode()).hexdigest()[:16]

        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            cursor = con.execute(
                "INSERT INTO unified_memory "
                "(layer, memory_type, content, embedding_hash, importance, source, tags, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (layer, memory_type, content_str, embedding_hash, importance, source, tags_str, expires_at)
            )
            memory_id = cursor.lastrowid
            con.commit()
            con.close()

            self.state["total_stored"] += 1
            self._save_state()

            log.debug("Stored %s memory [%s]: id=%d, importance=%.2f", layer, memory_type, memory_id, importance)
            return memory_id

        except Exception as exc:
            log.debug("Failed to store memory: %s", exc)
            return -1

    def retrieve(
        self,
        query: str,
        layers: Optional[List[str]] = None,
        memory_type: Optional[str] = None,
        limit: int = 10,
        min_importance: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Retrieve memories matching query across specified layers."""
        if layers is None:
            layers = ["working", "episodic", "semantic", "core"]

        results = []
        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.row_factory = sqlite3.Row

            for layer in layers:
                layer_config = self.LAYERS.get(layer, {})
                max_items = layer_config.get("max_items", 1000)

                # Build query
                where_clauses = ["layer = ?", "importance >= ?"]
                params = [layer, min_importance]

                if memory_type:
                    where_clauses.append("memory_type = ?")
                    params.append(memory_type)

                # Exclude expired memories
                where_clauses.append("(expires_at IS NULL OR expires_at > ?)")
                params.append(datetime.now(timezone.utc).isoformat())

                where_sql = " AND ".join(where_clauses)

                # Simple text matching (would use FTS5 or vector search in production)
                # For now, use LIKE for basic keyword matching
                if query:
                    keywords = query.lower().split()
                    keyword_clauses = []
                    for kw in keywords[:3]:  # Limit to 3 keywords
                        keyword_clauses.append("LOWER(content) LIKE ?")
                        params.append(f"%{kw}%")
                    if keyword_clauses:
                        where_clauses.append("(" + " OR ".join(keyword_clauses) + ")")
                        where_sql = " AND ".join(where_clauses)

                rows = con.execute(
                    f"SELECT * FROM unified_memory "
                    f"WHERE {where_sql} "
                    f"ORDER BY importance DESC, created_at DESC "
                    f"LIMIT ?",
                    params + [min(limit, max_items)]
                ).fetchall()

                for row in rows:
                    result = {
                        "id": row["id"],
                        "layer": row["layer"],
                        "memory_type": row["memory_type"],
                        "content": json.loads(row["content"]),
                        "importance": row["importance"],
                        "access_count": row["access_count"],
                        "source": row["source"],
                        "tags": json.loads(row["tags"]) if row["tags"] else [],
                        "created_at": row["created_at"],
                    }
                    results.append(result)

                    # Update access count
                    con.execute(
                        "UPDATE unified_memory SET access_count = access_count + 1, "
                        "last_accessed = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), row["id"])
                    )

            con.commit()
            con.close()

            # Sort by relevance (importance * recency)
            results.sort(key=lambda x: x["importance"], reverse=True)
            results = results[:limit]

            self.state["total_retrieved"] += len(results)
            self._save_state()

        except Exception as exc:
            log.debug("Failed to retrieve memories: %s", exc)

        return results

    def consolidate(self) -> Dict[str, int]:
        """Consolidate memories from shorter-term to longer-term layers."""
        consolidated = {"working_to_episodic": 0, "episodic_to_semantic": 0, "semantic_to_core": 0}

        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            now = datetime.now(timezone.utc).isoformat()

            # Working → Episodic: memories older than 24h with high importance
            rows = con.execute(
                "SELECT id, content, importance, memory_type, source, tags "
                "FROM unified_memory "
                "WHERE layer = 'working' AND importance >= 0.6 "
                "AND created_at < datetime(?, '-1 day') "
                "ORDER BY importance DESC LIMIT 50",
                (now,)
            ).fetchall()

            for row in rows:
                con.execute(
                    "UPDATE unified_memory SET layer = 'episodic', "
                    "expires_at = datetime(?, '+30 days') WHERE id = ?",
                    (now, row["id"])
                )
                consolidated["working_to_episodic"] += 1

            # Episodic → Semantic: memories accessed multiple times with high importance
            rows = con.execute(
                "SELECT id, content, importance, memory_type, source, tags "
                "FROM unified_memory "
                "WHERE layer = 'episodic' AND importance >= 0.7 AND access_count >= 3 "
                "AND created_at < datetime(?, '-7 days') "
                "ORDER BY importance DESC LIMIT 20",
                (now,)
            ).fetchall()

            for row in rows:
                con.execute(
                    "UPDATE unified_memory SET layer = 'semantic', "
                    "expires_at = datetime(?, '+1 year') WHERE id = ?",
                    (now, row["id"])
                )
                consolidated["episodic_to_semantic"] += 1

            # Semantic → Core: extremely important, frequently accessed memories
            rows = con.execute(
                "SELECT id, content, importance, memory_type, source, tags "
                "FROM unified_memory "
                "WHERE layer = 'semantic' AND importance >= 0.9 AND access_count >= 10 "
                "AND created_at < datetime(?, '-30 days') "
                "ORDER BY importance DESC LIMIT 10",
                (now,)
            ).fetchall()

            for row in rows:
                con.execute(
                    "UPDATE unified_memory SET layer = 'core', "
                    "expires_at = NULL WHERE id = ?",
                    (row["id"],)
                )
                consolidated["semantic_to_core"] += 1

            con.commit()
            con.close()

            total = sum(consolidated.values())
            self.state["last_consolidation"] = now
            self.state["total_consolidated"] += total
            self._save_state()

            if total > 0:
                log.info("Consolidated %d memories: %s", total, consolidated)

        except Exception as exc:
            log.debug("Consolidation failed: %s", exc)

        return consolidated

    def decay(self) -> Dict[str, int]:
        """Apply decay to memories based on age and access patterns."""
        forgotten = {"working": 0, "episodic": 0, "semantic": 0, "core": 0}

        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            now = datetime.now(timezone.utc).isoformat()

            for layer, config in self.LAYERS.items():
                if config["decay_rate"] == 0:
                    continue

                # Find expired memories
                rows = con.execute(
                    "SELECT id, importance FROM unified_memory "
                    "WHERE layer = ? AND expires_at IS NOT NULL AND expires_at < ? "
                    "LIMIT 100",
                    (layer, now)
                ).fetchall()

                for row in rows:
                    # Decay importance
                    new_importance = row["importance"] * (1 - config["decay_rate"])
                    if new_importance < 0.1:
                        # Forget this memory
                        con.execute("DELETE FROM unified_memory WHERE id = ?", (row["id"],))
                        forgotten[layer] += 1
                    else:
                        con.execute(
                            "UPDATE unified_memory SET importance = ? WHERE id = ?",
                            (new_importance, row["id"])
                        )

            con.commit()
            con.close()

            total = sum(forgotten.values())
            self.state["last_decay"] = now
            self.state["total_forgotten"] += total
            self._save_state()

            if total > 0:
                log.info("Decayed %d memories: %s", total, forgotten)

        except Exception as exc:
            log.debug("Decay failed: %s", exc)

        return forgotten

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics across all layers."""
        stats = {}
        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.row_factory = sqlite3.Row

            for layer in self.LAYERS:
                row = con.execute(
                    "SELECT COUNT(*) as count, AVG(importance) as avg_importance, "
                    "MAX(importance) as max_importance, AVG(access_count) as avg_access "
                    "FROM unified_memory WHERE layer = ?",
                    (layer,)
                ).fetchone()

                stats[layer] = {
                    "count": row["count"] or 0,
                    "avg_importance": round(row["avg_importance"] or 0, 4),
                    "max_importance": round(row["max_importance"] or 0, 4),
                    "avg_access": round(row["avg_access"] or 0, 2),
                    "max_items": self.LAYERS[layer]["max_items"],
                }

            # Total
            total_row = con.execute("SELECT COUNT(*) as count FROM unified_memory").fetchone()
            stats["total"] = total_row["count"] or 0

            con.close()

        except Exception as exc:
            log.debug("Failed to get stats: %s", exc)

        stats["orchestrator"] = {
            "total_stored": self.state.get("total_stored", 0),
            "total_retrieved": self.state.get("total_retrieved", 0),
            "total_consolidated": self.state.get("total_consolidated", 0),
            "total_forgotten": self.state.get("total_forgotten", 0),
            "last_consolidation": self.state.get("last_consolidation"),
            "last_decay": self.state.get("last_decay"),
        }

        return stats

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search across all memory layers with relevance ranking."""
        return self.retrieve(query, limit=limit)

    def promote(self, memory_id: int, target_layer: str) -> bool:
        """Manually promote a memory to a higher layer."""
        if target_layer not in self.LAYERS:
            return False

        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.execute(
                "UPDATE unified_memory SET layer = ?, "
                "expires_at = ? WHERE id = ?",
                (target_layer,
                 None if target_layer == "core" else
                 (datetime.now(timezone.utc) + timedelta(hours=self.LAYERS[target_layer]["ttl_hours"])).isoformat(),
                 memory_id)
            )
            con.commit()
            con.close()
            return True
        except Exception:
            return False

    def get_layer(self, layer: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all memories in a specific layer."""
        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT * FROM unified_memory WHERE layer = ? "
                "ORDER BY importance DESC LIMIT ?",
                (layer, limit)
            ).fetchall()

            con.close()

            return [
                {
                    "id": row["id"],
                    "memory_type": row["memory_type"],
                    "content": json.loads(row["content"]),
                    "importance": row["importance"],
                    "access_count": row["access_count"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

        except Exception:
            return []


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Memory Orchestrator")
    parser.add_argument("--stats", action="store_true", help="Show memory statistics")
    parser.add_argument("--consolidate", action="store_true", help="Run consolidation")
    parser.add_argument("--decay", action="store_true", help="Run decay")
    parser.add_argument("--search", type=str, help="Search memories")
    parser.add_argument("--layer", type=str, help="Show specific layer")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    orch = MemoryOrchestrator()

    if args.stats:
        print(json.dumps(orch.get_stats(), indent=2, default=str))
    elif args.consolidate:
        result = orch.consolidate()
        print(json.dumps(result, indent=2))
    elif args.decay:
        result = orch.decay()
        print(json.dumps(result, indent=2))
    elif args.search:
        results = orch.search(args.search)
        print(json.dumps(results, indent=2, default=str))
    elif args.layer:
        results = orch.get_layer(args.layer)
        print(json.dumps(results, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
