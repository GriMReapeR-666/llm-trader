"""Unit tests for SQLite checkpointer in LangGraph."""
import pytest
import sqlite3
from graph_checkpointer import SqliteCheckpointSaver
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata


def test_sqlite_checkpoint_saver_put_and_get(tmp_path):
    db_file = str(tmp_path / "test_state.db")
    saver = SqliteCheckpointSaver(db_path=db_file)

    config = {"configurable": {"thread_id": "test_thread_1", "checkpoint_ns": ""}}
    checkpoint: Checkpoint = {
        "v": 1,
        "ts": "2026-08-18T00:00:00Z",
        "id": "cp_001",
        "channel_values": {"status": "INITIALIZED", "symbol": "EURUSD"},
        "channel_versions": {"status": 1, "symbol": 1},
        "versions_seen": {},
        "pending_sends": [],
    }
    metadata: CheckpointMetadata = {
        "source": "input",
        "step": 0,
        "writes": {},
        "parents": {},
    }

    # Save checkpoint
    saved_config = saver.put(config, checkpoint, metadata, {"status": 1})
    assert saved_config["configurable"]["checkpoint_id"] == "cp_001"

    # Fetch checkpoint
    retrieved = saver.get_tuple(config)
    assert retrieved is not None
    assert retrieved.checkpoint["id"] == "cp_001"
    assert retrieved.checkpoint["channel_values"]["symbol"] == "EURUSD"
    assert retrieved.metadata["step"] == 0


def test_sqlite_checkpoint_saver_list(tmp_path):
    db_file = str(tmp_path / "test_state_list.db")
    saver = SqliteCheckpointSaver(db_path=db_file)

    config = {"configurable": {"thread_id": "test_thread_2", "checkpoint_ns": ""}}

    for i in range(3):
        cp: Checkpoint = {
            "v": 1,
            "ts": f"2026-08-18T00:0{i}:00Z",
            "id": f"cp_00{i}",
            "channel_values": {"step_num": i},
            "channel_versions": {"step_num": i + 1},
            "versions_seen": {},
            "pending_sends": [],
        }
        meta: CheckpointMetadata = {"source": "loop", "step": i, "writes": {}, "parents": {}}
        saver.put(config, cp, meta, {"step_num": i + 1})

    history = list(saver.list(config))
    assert len(history) == 3
    # Check ordered descending by checkpoint_id
    assert history[0].checkpoint["id"] == "cp_002"
    assert history[2].checkpoint["id"] == "cp_000"
