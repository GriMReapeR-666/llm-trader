from pathlib import Path

import pytest

from intelligence.semantic_memory import search as semantic_search
from intelligence.trading_memory import TradingMemory


def _seed_approved_memory(path: Path) -> TradingMemory:
    memory = TradingMemory(str(path))
    with memory._con() as con:
        con.execute(
            """INSERT INTO narratives(created_at,tags,content,source_ids,status,reviewed_at)
               VALUES(?,?,?,?,?,?)""",
            ("2025-01-01T00:00:00+00:00", '{"symbol":"EURUSD"}', "old narrative", '["old"]', "APPROVED", "2025-01-02T00:00:00+00:00"),
        )
        con.execute(
            """INSERT INTO narratives(created_at,tags,content,source_ids,status,reviewed_at)
               VALUES(?,?,?,?,?,?)""",
            ("2027-01-01T00:00:00+00:00", '{"symbol":"EURUSD"}', "future narrative", '["future"]', "APPROVED", "2027-01-02T00:00:00+00:00"),
        )
        con.execute(
            """INSERT INTO scenario_documents(
                scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,
                rejected_count,unresolved_count,content,evidence,status,reviewed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("old-scenario", "2025-01-01T00:00:00+00:00", "2025-01-03T00:00:00+00:00",
             '{"symbol":"EURUSD"}', 2, 1, 1, 0, 0, '{"balanced":true}', '{"old":true}', "APPROVED", "2025-01-04T00:00:00+00:00"),
        )
        con.execute(
            """INSERT INTO scenario_documents(
                scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,
                rejected_count,unresolved_count,content,evidence,status,reviewed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("future-scenario", "2027-01-01T00:00:00+00:00", "2027-01-03T00:00:00+00:00",
             '{"symbol":"EURUSD"}', 2, 1, 1, 0, 0, '{"balanced":true}', '{"future":true}', "APPROVED", "2027-01-04T00:00:00+00:00"),
        )
    return memory


def test_structured_and_semantic_memory_respect_as_of(tmp_path: Path):
    path = tmp_path / "memory.db"
    memory = _seed_approved_memory(path)
    cutoff = "2026-01-01T00:00:00Z"

    narratives = memory.retrieve_narrative_records({"symbol": "EURUSD"}, as_of=cutoff)
    scenarios = memory.retrieve_scenarios({"symbol": "EURUSD"}, as_of=cutoff)
    semantic = semantic_search(str(path), {"symbol": "EURUSD"}, as_of=cutoff)

    assert [item["content"] for item in narratives] == ["old narrative"]
    assert [item["scenario_key"] for item in scenarios] == ["old-scenario"]
    assert {item["id"] for item in semantic} == {1, "old-scenario"}
    assert all("future" not in str(item) for item in semantic)


def test_proposal_retrieval_records_cutoff_and_rejects_bad_timestamp(tmp_path: Path):
    memory = _seed_approved_memory(tmp_path / "proposal.db")
    result = memory.retrieve_for_proposal(
        {"symbol": "EURUSD"}, core_namespaces=[], as_of="2026-01-01T00:00:00Z"
    )
    assert result["retrieval_as_of"] == "2026-01-01T00:00:00+00:00"
    with memory._con() as con:
        row = con.execute("SELECT DISTINCT as_of FROM memory_retrievals").fetchone()
    assert row["as_of"] == result["retrieval_as_of"]
    with pytest.raises(ValueError, match="as_of"):
        memory.retrieve_for_proposal({"symbol": "EURUSD"}, core_namespaces=[], as_of="not-a-time")

