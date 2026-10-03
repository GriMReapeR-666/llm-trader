"""Memory candidate review queue + journal-backed lessons."""
from trading_memory import TradingMemory


def test_candidate_review_queue_lists_all_kinds(tmp_path):
    mem = TradingMemory(str(tmp_path / "mem.db"))
    mem.save_narrative({"k": 1}, "candidate narrative text", status="CANDIDATE")
    mem.save_verified_memory(
        "trade:1", "lesson", {"symbol": "EURUSD"}, "summary lesson",
        "do not chase", {"ticket": 1}, status="CANDIDATE",
    )
    mem.propose_core_memory("STABLE_LESSON", "no_chase", "Never revenge trade", ["trade:1"])

    queue = mem.candidate_review_queue(limit=10)
    assert queue["execution_authority"] is False
    assert queue["status"] == "REVIEW_REQUIRED"
    assert queue["counts"]["narrative"] >= 1
    assert queue["counts"]["verified"] >= 1
    assert queue["counts"]["core"] >= 1
    kinds = {item["kind"] for item in queue["items"]}
    assert "narrative" in kinds
    assert "verified" in kinds
    assert "core" in kinds


def test_format_approved_lessons_prefers_journal_not_empty_noise(tmp_path):
    mem = TradingMemory(str(tmp_path / "lessons.db"))
    assert mem.format_approved_lessons() == "No approved journal lessons yet."

    vid = mem.save_verified_memory(
        "trade:9", "lesson", {"symbol": "EURUSD"}, "Avoid late NY entries",
        "spread expands", {"ticket": 9}, status="APPROVED",
    )
    text = mem.format_approved_lessons(limit=4)
    assert f"[verified:{vid}]" in text
    assert "Avoid late NY entries" in text
    assert "perf.history" not in text
