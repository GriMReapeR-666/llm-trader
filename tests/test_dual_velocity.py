from threading import Event
from time import sleep

from operations.dual_velocity import DualVelocityExecutor


def test_slow_jobs_are_deduplicated_and_polled_without_raising(tmp_path):
    executor = DualVelocityExecutor(max_pending_slow=2)
    gate = Event()
    executor.submit_slow("memory:1", lambda: (gate.wait(2), {"processed": 1})[1])
    assert executor.submit_slow("memory:1", lambda: {"processed": 2})["status"] == "DEDUPLICATED"
    gate.set()
    for _ in range(20):
        result = executor.poll()
        if result:
            break
        sleep(0.01)
    assert result[0]["status"] == "COMPLETED"
    assert result[0]["result"] == {"processed": 1}
    executor.shutdown()


def test_backpressure_is_explicit_and_errors_are_telemetry():
    executor = DualVelocityExecutor(max_pending_fast=1)
    gate = Event()
    executor.submit_fast("tick:1", lambda: gate.wait(2))
    pressured = executor.submit_fast("tick:2", lambda: None)
    assert pressured["status"] == "BACKPRESSURE"
    gate.set()
    for _ in range(20):
        if executor.poll():
            break
        sleep(0.01)
    executor.submit_slow("failure", lambda: (_ for _ in ()).throw(RuntimeError("worker failed")))
    for _ in range(20):
        result = executor.poll()
        if result:
            break
        sleep(0.01)
    assert result[0]["status"] == "FAILED"
    assert "worker failed" in result[0]["error"]
    assert result[0]["execution_authority"] is False
    executor.shutdown()


def test_shutdown_rejects_new_work_and_reports_lane_state():
    executor = DualVelocityExecutor()
    executor.shutdown()
    result = executor.submit_slow("after-close", lambda: None)
    assert result["status"] == "CLOSED"
    assert executor.status()["closed"] is True
