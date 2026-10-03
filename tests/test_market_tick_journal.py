from pathlib import Path

import pytest

from journal_audit import audit
from trading_safety import TradingStore


def test_market_tick_journal_keeps_fresh_quote_and_spread(tmp_path: Path):
    store = TradingStore(str(tmp_path / "state.db"))

    key = store.record_market_tick(
        "EURUSD", bid=1.1000, ask=1.1002, quote_time_msc=123,
        tick_age_ms=25, source="test", quality="FRESH", payload={"session": "london"}
    )

    assert key.startswith("market_tick:")
    with store._connect() as con:
        row = con.execute("SELECT * FROM market_ticks WHERE id=?", (int(key.split(":")[1]),)).fetchone()
    assert row["mid"] == 1.1001
    assert row["spread"] == pytest.approx(0.0002)
    assert row["quality"] == "FRESH"


def test_market_tick_journal_keeps_missing_and_stale_evidence(tmp_path: Path):
    store = TradingStore(str(tmp_path / "state.db"))
    store.record_market_tick("EURUSD", source="test", quality="MISSING_OR_INVALID")
    store.record_market_tick(
        "EURUSD", bid=1.1, ask=1.1003, tick_age_ms=9000,
        source="test", quality="STALE"
    )

    with store._connect() as con:
        rows = con.execute("SELECT quality,bid,ask FROM market_ticks ORDER BY id").fetchall()
    assert [row["quality"] for row in rows] == ["MISSING_OR_INVALID", "STALE"]
    assert rows[0]["bid"] is None
    assert rows[1]["ask"] == 1.1003
    report = audit(str(tmp_path / "state.db"))
    assert report["passed"]
    assert report["counts"]["market_ticks"] == 2
