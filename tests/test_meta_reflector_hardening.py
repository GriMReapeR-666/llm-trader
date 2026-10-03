import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import pytest

from intelligence.meta_reflector import SystemMetaReflector
from core.account_sizing import AccountSizingEngine


@pytest.fixture
def temp_journal_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    gc.collect()
    try:
        if os.path.exists(path):
            os.remove(path)
    except (PermissionError, OSError):
        pass


def test_audit_from_journal_empty_fallback(temp_journal_db):
    reflector = SystemMetaReflector()
    # When journal is empty, defaults to nominal baseline
    report = reflector.audit_from_journal(temp_journal_db)
    assert report.overall_status == "HEALTHY"
    assert report.risk_multiplier == 1.0
    assert report.rolling_30_expectancy == 0.008
    assert report.execution_authority is False


def test_audit_from_journal_alpha_decay(temp_journal_db):
    # Seed journal with 20 losing trades (R = -1.0)
    con = sqlite3.connect(temp_journal_db)
    con.execute("""
        CREATE TABLE trade_labels (
            ticket INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            rationale TEXT NOT NULL,
            r_multiple REAL,
            created_at TEXT NOT NULL
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(20):
        con.execute(
            "INSERT INTO trade_labels(ticket, label, rationale, r_multiple, created_at) VALUES(?,?,?,?,?)",
            (1000 + i, "LOSS_STOP", "protective stop", -1.0, now),
        )
    con.commit()
    con.close()

    reflector = SystemMetaReflector()
    report = reflector.audit_from_journal(temp_journal_db)
    assert report.overall_status == "DEGRADED"
    assert report.rolling_30_expectancy < 0.0
    assert report.risk_multiplier <= 0.50
    assert any("Alpha Decay Alert" in action for action in report.remediation_actions)


def test_audit_from_journal_healthy_gains(temp_journal_db):
    con = sqlite3.connect(temp_journal_db)
    con.execute("""
        CREATE TABLE trade_labels (
            ticket INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            rationale TEXT NOT NULL,
            r_multiple REAL,
            created_at TEXT NOT NULL
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(20):
        con.execute(
            "INSERT INTO trade_labels(ticket, label, rationale, r_multiple, created_at) VALUES(?,?,?,?,?)",
            (2000 + i, "WIN_TARGET", "target hit", 1.5, now),
        )
    con.commit()
    con.close()

    reflector = SystemMetaReflector()
    report = reflector.audit_from_journal(temp_journal_db)
    assert report.overall_status == "HEALTHY"
    assert report.rolling_30_expectancy == 1.5
    assert report.risk_multiplier == 1.0


def test_account_sizing_meta_health_throttling():
    engine = AccountSizingEngine(
        risk_per_trade=0.01,
        min_lot_size=0.01,
        tick_size=0.00001,
        tick_value=1.0,
    )

    # 1. Nominal health multiplier (1.0)
    res_nominal = engine.calculate_position_size(
        account_equity=10000.0,
        entry_price=1.1000,
        stop_loss_price=1.0950,  # 50 pips stop distance
        meta_health_multiplier=1.0,
    )
    # Stop distance = 0.0050 = 500 ticks. Loss per lot = 500 * $1 = $500.
    # 1R = $10,000 * 0.01 = $100.
    # Nominal raw_lots = 100 / 500 = 0.20 lots.
    assert res_nominal.position_size_lots == 0.20
    assert res_nominal.meta_health_multiplier == 1.0

    # 2. Degraded health multiplier (0.50)
    res_degraded = engine.calculate_position_size(
        account_equity=10000.0,
        entry_price=1.1000,
        stop_loss_price=1.0950,
        meta_health_multiplier=0.50,
    )
    # Effective raw_lots = 0.20 * 0.50 = 0.10 lots.
    assert res_degraded.position_size_lots == 0.10
    assert res_degraded.meta_health_multiplier == 0.50

    # 3. Clamping of extreme values
    res_clamped = engine.calculate_position_size(
        account_equity=10000.0,
        entry_price=1.1000,
        stop_loss_price=1.0950,
        meta_health_multiplier=0.01,  # below minimum 0.10
    )
    assert res_clamped.meta_health_multiplier == 0.10


def test_cli_meta_audit(temp_journal_db):
    con = sqlite3.connect(temp_journal_db)
    con.execute("""
        CREATE TABLE trade_labels (
            ticket INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            rationale TEXT NOT NULL,
            r_multiple REAL,
            created_at TEXT NOT NULL
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(15):
        con.execute(
            "INSERT INTO trade_labels(ticket, label, rationale, r_multiple, created_at) VALUES(?,?,?,?,?)",
            (3000 + i, "WIN_TARGET", "target", 0.5, now),
        )
    con.commit()
    con.close()

    cmd = [
        sys.executable,
        "mt5_trader.py",
        "meta-audit",
        "--journal-path",
        temp_journal_db,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert "overall_status" in out
    assert out["rolling_30_expectancy"] == 0.5
    assert out["execution_authority"] is False
