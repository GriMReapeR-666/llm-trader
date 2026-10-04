import pytest
import sqlite3
import json
import os
import tempfile
from operations.agent_operations import AgentOperations

import gc

@pytest.fixture
def ops():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    agent_ops = AgentOperations(db_path=path)
    yield agent_ops
    gc.collect()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

def test_safety_kind_never_compacted(ops):
    namespace = "test_ns"
    # Insert safety kinds
    for i, kind in enumerate(AgentOperations.SAFETY_KINDS):
        ops.put_working_memory(namespace, f"safe_{i}", {"data": i}, kind=kind, priority=10)
    
    # Insert non-safety kinds
    for i in range(10):
        ops.put_working_memory(namespace, f"nonsafe_{i}", {"data": i}, kind="observation", priority=10)
        
    summary = ops.compact_working_memory(namespace, max_entries=5)
    
    assert summary["safety_retained"] == len(AgentOperations.SAFETY_KINDS)
    # The non-safety ones: we can only keep max_entries - safety_retained (but max_entries doesn't subtract safety, wait:
    # "Retain non-safety entries up to max_entries... wait, my code says: `retained_count < max_entries` where `retained_count` starts at `safety_retained`.)
    
    # So if max_entries=5, and safety=6, we keep 0 non-safety entries!
    assert summary["retained_count"] == len(AgentOperations.SAFETY_KINDS)
    assert summary["compacted_count"] == 10

def test_non_safety_compacted_oldest_first(ops):
    namespace = "ns2"
    # Insert with same priority, meaning older ones are compacted first.
    # The order of insertion gives updated_at (increasing).
    import time
    for i in range(5):
        ops.put_working_memory(namespace, f"key_{i}", {"v": i}, kind="observation", priority=50)
        time.sleep(0.01) # to ensure updated_at is different
        
    summary = ops.compact_working_memory(namespace, max_entries=3)
    assert summary["retained_count"] == 3
    assert summary["compacted_count"] == 2
    
    # Check that older ones were compacted. The retained ones should be key_2, key_3, key_4 (the newest).
    # Prompt gets only active ones.
    prompt = ops.working_memory_prompt(namespace)
    active_keys = {entry["key"] for entry in prompt["entries"] if not entry["key"].startswith("compaction:")}
    assert active_keys == {"key_2", "key_3", "key_4"}

def test_compacted_entries_have_status_compacted(ops):
    namespace = "ns3"
    ops.put_working_memory(namespace, "k1", {}, kind="obs")
    ops.put_working_memory(namespace, "k2", {}, kind="obs")
    ops.compact_working_memory(namespace, max_entries=1)
    
    # Check status directly in db
    with ops._con() as con:
        rows = con.execute("SELECT memory_key, status FROM working_memory WHERE namespace=?", (namespace,)).fetchall()
        statuses = {row["memory_key"]: row["status"] for row in rows}
    
    assert statuses["k1"] == "COMPACTED"
    assert statuses["k2"] == "ACTIVE"

def test_prompt_builder_respects_max_chars(ops):
    namespace = "ns4"
    # Create large entries
    large_payload = {"data": "x" * 1000}
    for i in range(5):
        ops.put_working_memory(namespace, f"k_{i}", large_payload, kind="obs", priority=50)
        
    # Each entry is ~1100 chars serialized
    prompt = ops.working_memory_prompt(namespace, max_chars=2500)
    assert len(prompt["entries"]) == 2
    assert prompt["truncated"] is True
    assert prompt["truncated_count"] == 3
    assert prompt["total_chars"] <= 2500

def test_prompt_builder_priority_order(ops):
    namespace = "ns5"
    ops.put_working_memory(namespace, "low", {}, kind="obs", priority=10)
    ops.put_working_memory(namespace, "high", {}, kind="obs", priority=90)
    ops.put_working_memory(namespace, "med", {}, kind="obs", priority=50)
    
    prompt = ops.working_memory_prompt(namespace)
    keys = [entry["key"] for entry in prompt["entries"]]
    assert keys == ["high", "med", "low"]

def test_compaction_summary_counts(ops):
    namespace = "ns6"
    ops.put_working_memory(namespace, "k1", {}, kind="error") # safety
    ops.put_working_memory(namespace, "k2", {}, kind="obs")
    ops.put_working_memory(namespace, "k3", {}, kind="obs")
    
    ops.compact_working_memory(namespace, max_entries=2)
    
    summary = ops.compaction_summary(namespace)
    assert summary["active"] == 3 # k1 + k3 + compaction record
    assert summary["compacted"] == 1
    assert summary["safety_retained"] == 1 # k1 (error)
    assert summary["oldest_active"] is not None
    assert summary["newest_active"] is not None

def test_edge_cases(ops):
    namespace = "ns7"
    # Empty namespace
    sum1 = ops.compact_working_memory(namespace)
    assert sum1["compacted_count"] == 0
    assert sum1["retained_count"] == 0
    
    # All safety entries
    ops.put_working_memory(namespace, "s1", {}, kind="error")
    ops.put_working_memory(namespace, "s2", {}, kind="risk_decision")
    sum2 = ops.compact_working_memory(namespace, max_entries=1)
    assert sum2["compacted_count"] == 0
    assert sum2["retained_count"] == 2
    
    # Single entry
    ops.put_working_memory("ns8", "k1", {}, kind="obs")
    sum3 = ops.compact_working_memory("ns8", max_entries=0)
    assert sum3["compacted_count"] == 1
    assert sum3["retained_count"] == 0
