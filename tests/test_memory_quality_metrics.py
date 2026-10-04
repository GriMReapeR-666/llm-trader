from pathlib import Path
import sqlite3

from intelligence.trading_memory import TradingMemory


def test_memory_quality_reports_latency_balance_tokens_and_cutoff_errors(tmp_path: Path):
    path = str(tmp_path / "memory-quality.db")
    memory = TradingMemory(path)
    with sqlite3.connect(path) as con:
        con.executemany(
            """INSERT INTO memory_retrievals(
                retrieved_at,layer,query_tags,source_ids,result_count,
                latency_ms,prompt_char_estimate,as_of,outcome_label,outcome_ticket
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                ("2026-08-10T10:00:00+00:00", "scenario", "{}", "[]", 2,
                 10.0, 400, "2026-08-10T09:59:59+00:00", "WIN", 1),
                ("2026-08-10T10:00:01+00:00", "scenario", "{}", "[]", 0,
                 30.0, 800, None, None, None),
                ("2026-08-10T10:00:02+00:00", "semantic", "{}", "[]", 1,
                 20.0, 200, "2026-08-10T10:00:03+00:00", "LOSS", 2),
            ],
        )
    report = memory.memory_quality_metrics()
    assert report["retrieval_count"] == 3
    assert report["zero_result_rate"] == 1 / 3
    assert report["total_prompt_token_estimate"] == 350
    assert report["point_in_time_cutoff_errors"] == {
        "malformed_or_missing": 1,
        "future_cutoff": 1,
    }
    assert report["layers"]["scenario"]["latency_ms_p95"] == 30.0
    assert report["layers"]["scenario"]["outcome_associated_win_rate"] == 1.0
    assert report["layers"]["scenario"]["outcome_association_rate"] == 1 / 2

