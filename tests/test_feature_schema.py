import math
from datetime import datetime, timezone

from feature_schema import REQUIRED_VALUES, schema_hash, validate_snapshot, values_hash


def _snapshot(**overrides):
    values = {name: (False if name.startswith("liquidity_sweep") else 1.0)
              for name in REQUIRED_VALUES}
    base = {
        "version": "feature_snapshot_v1",
        "schema_hash": schema_hash(),
        "ready": True,
        "source": "test",
        "captured_at": "2026-08-06T10:00:00+00:00",
        "completed_bar_time": "2026-08-06T09:59:00+00:00",
        "completed_bar_index": 10,
        "values": values,
    }
    base["values_hash"] = values_hash(values)
    base.update(overrides)
    return base


def test_feature_schema_accepts_complete_point_in_time_snapshot():
    result = validate_snapshot(_snapshot())
    assert result["valid"] is True
    assert result["reasons"] == []


def test_feature_schema_rejects_malformed_container_and_nonfinite_values():
    malformed = validate_snapshot({"version": "feature_snapshot_v1", "values": []})
    assert malformed["valid"] is False
    assert any("container" in reason for reason in malformed["reasons"])

    values = _snapshot()["values"]
    values["close"] = math.nan
    result = validate_snapshot(_snapshot(values=values, values_hash=values_hash(values)))
    assert result["valid"] is False
    assert any("finite" in reason for reason in result["reasons"])


def test_feature_schema_rejects_future_completed_bar_and_bad_timestamps():
    future = validate_snapshot(_snapshot(completed_bar_time="2026-08-06T10:01:00+00:00"))
    assert future["valid"] is False
    assert any("later than capture" in reason for reason in future["reasons"])

    malformed = validate_snapshot(_snapshot(captured_at="not-a-time"))
    assert malformed["valid"] is False
    assert any("capture timestamp" in reason for reason in malformed["reasons"])
