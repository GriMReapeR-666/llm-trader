"""Durable SQLite Checkpointer for LangGraph and Agentic Workflows.

Integrates state persistence directly into the authoritative SQLite store
(trading_state.db), supporting full thread persistence, time-travel,
crash-recovery, and non-blocking state inspection.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    RunnableConfig,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteCheckpointSaver(BaseCheckpointSaver):
    """Authoritative SQLite Checkpoint Saver for LangGraph trading agents."""

    def __init__(
        self,
        db_path: str = "trading_state.db",
        *,
        serde: Optional[SerializerProtocol] = None,
    ) -> None:
        super().__init__(serde=serde or JsonPlusSerializer())
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        con.row_factory = sqlite3.Row
        # Tolerate brief cross-thread lock contention instead of crashing.
        con.execute("PRAGMA busy_timeout=10000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS graph_checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT NOT NULL,
                checkpoint_blob BLOB NOT NULL,
                metadata_blob BLOB NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE INDEX IF NOT EXISTS idx_graph_checkpoints_lookup 
                ON graph_checkpoints(thread_id, checkpoint_ns, checkpoint_id DESC);

            CREATE TABLE IF NOT EXISTS graph_checkpoint_writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT NOT NULL,
                value_blob BLOB NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );
            CREATE INDEX IF NOT EXISTS idx_graph_writes_lookup
                ON graph_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id);
            """)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Fetch the latest (or specified) checkpoint tuple for a given thread."""
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return None
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")

        with self._con() as con:
            if checkpoint_id:
                row = con.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                           type, checkpoint_blob, metadata_blob
                    FROM graph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (str(thread_id), str(checkpoint_ns), str(checkpoint_id)),
                ).fetchone()
            else:
                row = con.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                           type, checkpoint_blob, metadata_blob
                    FROM graph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ?
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                    """,
                    (str(thread_id), str(checkpoint_ns)),
                ).fetchone()

            if not row:
                return None

            checkpoint_data = self.serde.loads_typed((row["type"], row["checkpoint_blob"]))
            metadata_data = self.serde.loads_typed((row["type"], row["metadata_blob"]))

            # Load any pending writes for this checkpoint
            write_rows = con.execute(
                """
                SELECT task_id, channel, type, value_blob
                FROM graph_checkpoint_writes
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                ORDER BY idx ASC
                """,
                (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
            ).fetchall()

            pending_writes = [
                (
                    w_row["task_id"],
                    w_row["channel"],
                    self.serde.loads_typed((w_row["type"], w_row["value_blob"])),
                )
                for w_row in write_rows
            ]

            parent_config: Optional[RunnableConfig] = None
            if row["parent_checkpoint_id"]:
                parent_config = {
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=checkpoint_data,
                metadata=metadata_data,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )

    def list(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoint history for time-travel analysis."""
        thread_id = config.get("configurable", {}).get("thread_id") if config else None
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "") if config else ""
        before_id = before.get("configurable", {}).get("checkpoint_id") if before else None

        query = "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint_blob, metadata_blob FROM graph_checkpoints WHERE 1=1"
        params: List[Any] = []

        if thread_id:
            query += " AND thread_id = ?"
            params.append(str(thread_id))
            query += " AND checkpoint_ns = ?"
            params.append(str(checkpoint_ns))
        if before_id:
            query += " AND checkpoint_id < ?"
            params.append(str(before_id))

        query += " ORDER BY checkpoint_id DESC"
        if limit and limit > 0:
            query += f" LIMIT {int(limit)}"

        with self._con() as con:
            rows = con.execute(query, params).fetchall()
            for row in rows:
                chk = self.serde.loads_typed((row["type"], row["checkpoint_blob"]))
                meta = self.serde.loads_typed((row["type"], row["metadata_blob"]))
                parent_cfg: Optional[RunnableConfig] = None
                if row["parent_checkpoint_id"]:
                    parent_cfg = {
                        "configurable": {
                            "thread_id": row["thread_id"],
                            "checkpoint_ns": row["checkpoint_ns"],
                            "checkpoint_id": row["parent_checkpoint_id"],
                        }
                    }
                yield CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": row["thread_id"],
                            "checkpoint_ns": row["checkpoint_ns"],
                            "checkpoint_id": row["checkpoint_id"],
                        }
                    },
                    checkpoint=chk,
                    metadata=meta,
                    parent_config=parent_cfg,
                )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist a new checkpoint snapshot."""
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint["id"])
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")

        type_chk, blob_chk = self.serde.dumps_typed(checkpoint)
        type_meta, blob_meta = self.serde.dumps_typed(metadata)

        now = utcnow_iso()
        with self._con() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO graph_checkpoints
                (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint_blob, metadata_blob, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    str(parent_checkpoint_id) if parent_checkpoint_id else None,
                    type_chk,
                    blob_chk,
                    blob_meta,
                    now,
                ),
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def prune_checkpoints(self, keep_per_thread: int = 200) -> int:
        """Delete oldest checkpoints beyond a per-thread retention bound.

        Time-travel history grows without limit during continuous runs; this
        keeps the most recent ``keep_per_thread`` checkpoints per thread.
        """
        keep = max(10, int(keep_per_thread))
        with self._con() as con:
            threads = [r["thread_id"] for r in con.execute(
                "SELECT DISTINCT thread_id FROM graph_checkpoints").fetchall()]
            removed = 0
            for thread_id in threads:
                cutoff_row = con.execute(
                    """SELECT checkpoint_id FROM graph_checkpoints
                       WHERE thread_id=? ORDER BY checkpoint_id DESC LIMIT 1 OFFSET ?""",
                    (thread_id, keep - 1),
                ).fetchone()
                if not cutoff_row:
                    continue
                cutoff_id = cutoff_row["checkpoint_id"]
                removed += con.execute(
                    "DELETE FROM graph_checkpoints WHERE thread_id=? AND checkpoint_id<=?",
                    (thread_id, cutoff_id),
                ).rowcount
                con.execute(
                    """DELETE FROM graph_checkpoint_writes
                       WHERE thread_id=? AND checkpoint_id NOT IN
                       (SELECT checkpoint_id FROM graph_checkpoints WHERE thread_id=?)""",
                    (thread_id, thread_id),
                )
            return removed

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate write records during graph steps."""
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = str(config["configurable"]["checkpoint_id"])

        now = utcnow_iso()
        with self._con() as con:
            for idx, (channel, value) in enumerate(writes):
                type_val, blob_val = self.serde.dumps_typed(value)
                con.execute(
                    """
                    INSERT OR REPLACE INTO graph_checkpoint_writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value_blob, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        str(task_id),
                        idx,
                        str(channel),
                        type_val,
                        blob_val,
                        now,
                    ),
                )
