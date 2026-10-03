"""Analytics rollup tests (P7)."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.analytics import compute_analytics  # noqa: E402


def _make_db(tmp_path, rows):
    db = str(tmp_path / "journal.db")
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE trades (ticket INTEGER PRIMARY KEY, pnl REAL,"
            " risk REAL, close_time TEXT)")
        for i, (pnl, risk, ct) in enumerate(rows, start=1):
            con.execute("INSERT INTO trades VALUES (?,?,?,?)", (i, pnl, risk, ct))
        # Submission evidence: the curve fallback only admits broker-evidenced
        # tickets.
        con.execute("CREATE TABLE order_intents (mt5_ticket INTEGER)")
        con.executemany(
            "INSERT INTO order_intents (mt5_ticket) VALUES (?)",
            [(i,) for i in range(1, len(rows) + 1)],
        )
    return db


def test_curve_r_and_monthly_from_trades_table(tmp_path):
    rows = [
        (+120.0, 100.0, "2026-07-03 14:00:00"),   # +1.2R July
        (-100.0, 100.0, "2026-07-05 15:00:00"),   # -1R   July
        (+260.0, 100.0, "2026-08-01 10:00:00"),   # +2.6R August
    ]
    db = _make_db(tmp_path, rows)
    out = compute_analytics(db)

    assert out["equity_curve"] == [120.0, 20.0, 280.0]
    assert out["r_multiples"] == [1.2, -1.0, 2.6]
    assert out["monthly_r"][6] == round(0.2, 3)     # July net +0.2R
    assert out["monthly_r"][7] == 2.6               # August
    t = out["totals"]
    assert t["trades"] == 3
    assert abs(t["win_rate"] - 66.67) < 0.01
    assert abs(t["profit_factor"] - 3.8) < 0.01     # 380 / 100
    assert t["net"] == 280.0


def test_reward_artifact_history_preferred(tmp_path):
    rewards = str(tmp_path / "trading_rewards.json")
    with open(rewards, "w", encoding="utf-8") as fh:
        import json
        json.dump({"history": [
            {"pnl": 50.0, "risk": 50.0, "ts": "2026-01-15T10:00:00"},
            {"pnl": -25.0, "risk": 50.0, "ts": "2026-01-16T10:00:00"},
        ]}, fh)
    empty_db = str(tmp_path / "unused.db")      # file not even created
    out = compute_analytics(empty_db, rewards_path=rewards)
    assert out["equity_curve"] == [50.0, 25.0]
    assert out["r_multiples"] == [1.0, -0.5]
    assert out["monthly_r"][0] == 0.5


def test_registry_maps_to_per_strategy(tmp_path):
    registry = [{
        "id": "m1", "name": "macro_continuation_v1", "stage": "FULL",
        "trades": 214, "winRate": 57.5, "profitFactor": 1.72,
        "sharpe": 1.42, "maxDD": 8.9,
    }]
    db = _make_db(tmp_path, [])
    out = compute_analytics(db, registry=registry)
    ps = out["per_strategy"]
    assert len(ps) == 1
    assert ps[0]["stage"] == "FULL" and ps[0]["profit_factor"] == 1.72


def test_empty_everything_is_safe(tmp_path):
    out = compute_analytics(str(tmp_path / "missing.db"))
    assert out["equity_curve"] == [] and out["totals"]["trades"] == 0
