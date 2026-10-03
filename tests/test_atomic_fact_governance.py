from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_memory import TradingMemory


def test_fact_retrieval_excludes_proposed_expired_and_future_facts(tmp_path: Path):
    memory = TradingMemory(str(tmp_path / "memory.db"))
    now = datetime.now(timezone.utc)
    memory.record_fact("source-observed", "usd.regime", "risk_on", fact_status="OBSERVED")
    memory.record_fact("source-inferred", "usd.regime", "trend", fact_status="INFERRED")
    memory.record_fact("source-proposed", "usd.regime", "guess", fact_status="PROPOSED")
    memory.record_fact("source-expired", "usd.regime", "old", fact_status="OBSERVED",
                       valid_until=(now - timedelta(minutes=1)).isoformat())
    memory.record_fact("source-future", "usd.regime", "future", fact_status="INFERRED",
                       valid_from=(now + timedelta(minutes=1)).isoformat())

    facts = memory.retrieve_facts(["usd.regime"])
    assert [item["source_id"] for item in facts] == ["source-inferred", "source-observed"]
