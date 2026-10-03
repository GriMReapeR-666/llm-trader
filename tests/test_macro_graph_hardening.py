import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

from intelligence.macro_graph import MacroGraph


@pytest.fixture
def temp_macro_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    import gc
    gc.collect()
    try:
        if os.path.exists(path):
            os.remove(path)
    except (PermissionError, OSError):
        pass


def test_prune_stale_and_low_confidence_edges(temp_macro_db):
    mg = MacroGraph(temp_macro_db)
    
    # Ingest a valid primary source
    source_id = mg.ingest_source(
        title="Fed Policy Statement",
        payload={"notes": "Benchmark rates unchanged"},
        source_url="https://federalreserve.gov/statement",
        publisher="federalreserve.gov",
    )
    
    # 1. Active edge: fresh (0 days old), high confidence (0.85) -> Should NOT be pruned
    edge_active = mg.propose_edge(
        source_name="FED_FUNDS_RATE",
        source_type="INTEREST_RATE",
        target_name="USD",
        target_type="CURRENCY",
        relation="INTEREST_RATE_DIFFERENTIAL",
        sign="POSITIVE",
        confidence=0.85,
        source_id=source_id,
    )
    mg.review_edge(edge_active, approve=True, reviewer="operator:test")
    
    # 2. Stale edge: 100 days old, high confidence (0.80) -> Should be pruned due to age
    edge_stale = mg.propose_edge(
        source_name="ECB_DEPOSIT_RATE",
        source_type="INTEREST_RATE",
        target_name="EUR",
        target_type="CURRENCY",
        relation="INTEREST_RATE_DIFFERENTIAL",
        sign="POSITIVE",
        confidence=0.80,
        source_id=source_id,
    )
    mg.review_edge(edge_stale, approve=True, reviewer="operator:test")
    
    # Artificially age edge_stale by 100 days in sqlite
    stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with mg._con() as con:
        con.execute("UPDATE macro_edges SET updated_at=? WHERE edge_id=?", (stale_date, edge_stale))
        
    # 3. Low confidence edge: fresh, but low confidence (0.10 < 0.20 threshold) -> Should be pruned
    edge_low_conf = mg.propose_edge(
        source_name="BOJ_POLICY_RATE",
        source_type="INTEREST_RATE",
        target_name="JPY",
        target_type="CURRENCY",
        relation="INTEREST_RATE_DIFFERENTIAL",
        sign="POSITIVE",
        confidence=0.10,
        source_id=source_id,
    )
    mg.review_edge(edge_low_conf, approve=True, reviewer="operator:test")

    # Run pruning
    report = mg.prune_stale_edges(max_age_days=90, min_confidence=0.20, actor="operator:test_pruner")
    
    assert report["scanned_count"] == 3
    assert report["pruned_count"] == 2
    assert edge_stale in report["pruned_edge_ids"]
    assert edge_low_conf in report["pruned_edge_ids"]
    assert edge_active not in report["pruned_edge_ids"]
    assert report["execution_authority"] is False
    
    # Verify approved_edges only returns the active edge
    approved = mg.approved_edges(["USD", "EUR", "JPY"])
    approved_ids = [row["edge_id"] for row in approved]
    assert edge_active in approved_ids
    assert edge_stale not in approved_ids
    assert edge_low_conf not in approved_ids

    # Verify audit history
    with mg._con() as con:
        history_rows = con.execute(
            "SELECT * FROM macro_edge_history WHERE action='PRUNED' ORDER BY edge_id"
        ).fetchall()
        assert len(history_rows) == 2
        for h in history_rows:
            assert h["status"] == "PRUNED"
            assert h["reviewer"] == "operator:test_pruner"
            payload = json.loads(h["payload"])
            assert "reason" in payload
            assert payload["previous_status"] == "APPROVED"


def test_ingest_live_release_surprise(temp_macro_db):
    mg = MacroGraph(temp_macro_db)
    
    # Case 1: Hawkish shock (Actual > Forecast)
    res_hawkish = mg.ingest_live_release_surprise(
        event_title="US CPI YoY",
        currency="USD",
        actual=3.5,
        forecast=3.2,
        previous=3.1,
        publisher="official calendar",
    )
    assert res_hawkish["surprise"] == 0.3
    assert res_hawkish["shock_category"] == "HAWKISH_SHOCK"
    assert res_hawkish["source_id"].startswith("macro:")
    assert res_hawkish["execution_authority"] is False

    # Case 2: Dovish shock (Actual < Forecast)
    res_dovish = mg.ingest_live_release_surprise(
        event_title="US Non-Farm Payrolls",
        currency="USD",
        actual=140.0,
        forecast=180.0,
        previous=210.0,
        publisher="official calendar",
    )
    assert res_dovish["surprise"] == -40.0
    assert res_dovish["shock_category"] == "DOVISH_SHOCK"

    # Case 3: In line (Actual == Forecast)
    res_inline = mg.ingest_live_release_surprise(
        event_title="EUR Harmonised CPI",
        currency="EUR",
        actual=2.4,
        forecast=2.4,
        previous=2.5,
        publisher="official calendar",
    )
    assert abs(res_inline["surprise"]) < 1e-5
    assert res_inline["shock_category"] == "IN_LINE"

    # Verify source ingested into macro_sources table
    with mg._con() as con:
        row = con.execute("SELECT * FROM macro_sources WHERE source_id=?", (res_hawkish["source_id"],)).fetchone()
        assert row is not None
        payload = json.loads(row["payload"])
        assert payload["shock_category"] == "HAWKISH_SHOCK"
        assert payload["actual"] == 3.5
        assert payload["forecast"] == 3.2


def test_macro_prune_cli(temp_macro_db):
    mg = MacroGraph(temp_macro_db)
    source_id = mg.ingest_source(
        title="Fed Policy Statement",
        payload={"notes": "Benchmark rates unchanged"},
        source_url="https://federalreserve.gov/statement",
        publisher="federalreserve.gov",
    )
    edge_id = mg.propose_edge(
        source_name="FED_FUNDS_RATE",
        source_type="INTEREST_RATE",
        target_name="USD",
        target_type="CURRENCY",
        relation="INTEREST_RATE_DIFFERENTIAL",
        sign="POSITIVE",
        confidence=0.15,  # Below 0.20
        source_id=source_id,
    )
    mg.review_edge(edge_id, approve=True, reviewer="operator:test")

    import gc
    del mg
    gc.collect()

    # Run CLI command
    cmd = [
        sys.executable,
        "mt5_trader.py",
        "journal",
        "macro-prune",
        "--journal-path",
        temp_macro_db,
        "--min-confidence",
        "0.20",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["pruned_count"] == 1
    assert edge_id in out["pruned_edge_ids"]
    assert out["execution_authority"] is False
