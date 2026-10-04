from datetime import datetime, timedelta, timezone

import pytest

from operations.agent_operations import AgentOperations
from operations.health_alerts import evaluate


def test_health_alerts_detect_stale_workers_and_exhausted_retries(tmp_path):
    path = tmp_path / "health.db"
    ops = AgentOperations(str(path))
    run_id = ops.start_run("health-test")
    ops.claim_task("task:health", "health-test", "analyse", {}, run_id, lease_seconds=10, max_attempts=1)
    assert ops.create_order_intent("intent:health", {"symbol": "EURUSD"})[0]
    ops.flag_execution_review("intent:health", "broker history unavailable")
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    with ops._con() as con:
        con.execute("UPDATE agent_runs SET heartbeat_at=? WHERE run_id=?", (old, run_id))
        con.execute("UPDATE agent_tasks SET lease_expires_at=? WHERE idempotency_key=?", (old, "task:health"))
        con.execute("UPDATE agent_tasks SET status='ERROR',attempts=1 WHERE idempotency_key=?", ("task:health",))
        con.execute("UPDATE execution_reviews SET retry_count=5 WHERE intent_key=?", ("intent:health",))

    result = evaluate(str(path), stale_seconds=30)
    codes = {item["code"] for item in result["alerts"]}
    assert result["status"] == "CRITICAL"
    assert {"STALE_AGENT_HEARTBEATS", "EXPIRED_AGENT_TASK_LEASES",
            "EXHAUSTED_AGENT_TASK_RETRIES", "EXHAUSTED_EXECUTION_REVIEW_RETRIES"} <= codes
    assert result["execution_authority"] is False


def test_health_alerts_detect_latest_clock_drift(tmp_path):
    path = tmp_path / "clock-health.db"
    ops = AgentOperations(str(path))
    ops.record_clock_observation("broker", 1_700_000_000, max_offset_seconds=0.1)
    result = evaluate(str(path), stale_seconds=30)
    assert result["status"] == "CRITICAL"
    assert any(item["code"] == "CLOCK_DRIFT_OR_MALFORMED" for item in result["alerts"])


@pytest.mark.parametrize("kwargs", [
    {"stale_seconds": 0},
    {"stale_seconds": True},
    {"max_slippage_price": float("nan")},
])
def test_health_alert_parameters_fail_closed(kwargs, tmp_path):
    with pytest.raises(ValueError):
        evaluate(str(tmp_path / "missing.db"), **kwargs)
