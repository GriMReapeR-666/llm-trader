"""Tests for Task 3 (P0-1: Wire alpha decay -> ensemble knockout).

Acceptance criteria:
- Degrade an alpha's IC to -0.05.
- Confirm status becomes DEAD or REVERSED within consecutive_bars=5.
- Confirm weight -> 0 in ensemble without manual restart.
- Confirm knockout event logged to lessons.jsonl.
- Confirm knockout recorded in TradingMemory Level 2 (verified_memories with kind="alpha_decay_knockout").
- Confirm combine_alphas(alpha_columns=alive_only, min_ir=0.3) filters dead alphas.
"""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from core.alpha_decay_monitor import AlphaDecayMonitor
from core.alpha_ensemble import combine_alphas
from intelligence.trading_memory import TradingMemory


def _make_ohlcv_with_degrading_alpha(n: int = 120, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    df = pd.DataFrame({
        "open": close + rng.randn(n) * 0.1,
        "high": close + rng.uniform(0.2, 1.0, n),
        "low": close - rng.uniform(0.2, 1.0, n),
        "close": close,
        "volume": rng.uniform(1000, 5000, n),
    })
    fwd_ret = df["close"].pct_change().shift(-1).fillna(0.0)
    df["forward_return"] = fwd_ret

    # alpha_healthy: correlated with fwd_ret
    df["alpha_healthy"] = fwd_ret * 4.0 + rng.randn(n) * 0.001

    # alpha_dying: starts correlated, but last 15 bars are strongly negatively correlated (-0.05 IC or worse)
    alpha_dying = fwd_ret * 3.0 + rng.randn(n) * 0.001
    # Inverse signal in recent bars to crash rolling IC
    alpha_dying.iloc[-15:] = -fwd_ret.iloc[-15:] * 10.0 - rng.uniform(0.01, 0.05, 15)
    df["alpha_dying"] = alpha_dying

    return df


def test_alpha_decay_knockout_end_to_end(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trading_state.db")
    lessons_path = tmp_path / "lessons.jsonl"
    monkeypatch.chdir(tmp_path)

    monitor = AlphaDecayMonitor(
        ic_window=30,
        decay_threshold=0.02,
        reversal_threshold=-0.02,
        consecutive_bars=5,
        alert_db_path=db_path,
        orthogonalize=False,
    )

    df = _make_ohlcv_with_degrading_alpha()
    cols = ["alpha_healthy", "alpha_dying"]

    # 1. Update monitor across bars
    health = monitor.update(df, cols)
    alerts = monitor.check_decay()

    # Verify that alpha_dying was flagged as DEAD or REVERSED
    dying_health = health["alpha_dying"]
    assert dying_health.status in ("DEAD", "REVERSED")
    assert dying_health.ic_consecutive_low >= 5
    assert dying_health.ic_current < -0.02

    # Verify alert was generated
    dead_alerts = [a for a in alerts if a.alpha_name == "alpha_dying"]
    assert len(dead_alerts) >= 1
    assert dead_alerts[0].severity == "CRITICAL"

    # 2. Check lessons.jsonl
    assert lessons_path.exists(), "lessons.jsonl was not created"
    lessons_lines = lessons_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lessons_lines) >= 1
    lesson_records = [json.loads(line) for line in lessons_lines]
    dying_records = [r for r in lesson_records if r.get("alpha_name") == "alpha_dying"]
    assert len(dying_records) >= 1
    assert dying_records[0]["kind"] == "alpha_decay_knockout"
    assert dying_records[0]["status"] in ("DEAD", "REVERSED")

    # 3. Check TradingMemory Level 2
    tm = TradingMemory(db_path)
    memories = tm.list_verified_memories()
    decay_mems = [m for m in memories if m.get("kind") == "alpha_decay_knockout" and m["tags"].get("alpha") == "alpha_dying"]
    assert len(decay_mems) >= 1
    assert decay_mems[0]["tags"]["status"] in ("DEAD", "REVERSED")
    assert "knocked out" in decay_mems[0]["summary"].lower()

    # 4. Check combine_alphas integration with decay_monitor
    res = combine_alphas(
        df,
        cols,
        decay_monitor=monitor,
        alive_only=True,
        min_ir=0.3,
        ic_window=30,
    )

    # Dead alpha MUST receive weight 0.0
    assert res.alpha_weights.get("alpha_dying", 0.0) == 0.0
    assert res.alpha_weights.get("alpha_healthy", 0.0) == 1.0
