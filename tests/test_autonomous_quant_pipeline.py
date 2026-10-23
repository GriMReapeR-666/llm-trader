"""Functional and integration tests for Autonomous Quant Operating Pipeline wiring.

Verifies:
1. Market bar resolution (DataFrame -> Parquet archive -> Synthetic fallback).
2. Autonomous pipeline execution of 11-step Quant Operating Pipeline.
3. Candidate registration & graduated authority promotion into strategy_lifecycle.
4. Fail-closed error handling (exceptions in research never crash primary tick).
5. Zero-authority invariant (execution_authority == False across all outputs).
6. Agent event telemetry emission during autonomous tick.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock
import numpy as np
import polars as pl
import pytest

from operations.autonomous_pipeline import (
    board,
    run_pipeline,
    _resolve_market_bars,
)
from operations.strategy_lifecycle import _connect as connect_lifecycle


def test_resolve_market_bars_with_dataframe():
    """Explicitly passed DataFrame is returned directly."""
    df = pl.DataFrame({"close": [1.0, 1.1, 1.2]})
    res = _resolve_market_bars(dataset=df)
    assert isinstance(res, pl.DataFrame)
    assert len(res) == 3


def test_resolve_market_bars_fallback():
    """When archive does not exist, returns realistic synthetic Polars DataFrame."""
    res = _resolve_market_bars(dataset=None, archive_path="non_existent_archive.parquet", n_bars=150)
    assert isinstance(res, pl.DataFrame)
    assert len(res) == 150
    assert "close" in res.columns


def test_resolve_market_bars_with_real_archive():
    """If candle archive exists, loads recent slice."""
    real_path = os.path.join("data", "candle_archive", "EURUSD_H1_long.parquet")
    if os.path.exists(real_path):
        res = _resolve_market_bars(dataset=None, archive_path=real_path, n_bars=200)
        assert isinstance(res, pl.DataFrame)
        assert len(res) == 200
        assert "close" in res.columns


def test_run_pipeline_autonomous_quant_execution(tmp_path):
    """Verify that run_pipeline autonomously executes the 11-step quant operating framework."""
    db_path = str(tmp_path / "trading_state.db")
    np.random.seed(42)
    prices = [100.0 + i * 0.05 for i in range(250)]
    df = pl.DataFrame({"close": prices})

    res = run_pipeline(
        lifecycle_path=db_path,
        dataset=df,
        symbols=["EURUSD"],
        run_quant_pipeline=True,
    )

    assert "quant_operating_pipeline" in res
    qop = res["quant_operating_pipeline"]
    assert qop["status"] == "COMPLETED"
    assert qop["total_candidates"] >= 15
    assert qop["execution_authority"] is False
    assert "gate_survivors_count" in qop
    assert "staged_strategies" in qop
    assert "top_finalists" in qop
    assert isinstance(qop["top_finalists"], list)

    # Lifecycle database inspection: staged survivors must exist as VALIDATING
    b = board(db_path)
    if qop["gate_survivors_count"] > 0:
        assert b["total"] > 0
        validating_count = b["by_state"].get("VALIDATING", 0)
        assert validating_count > 0


def test_run_pipeline_quant_disabled(tmp_path):
    """When run_quant_pipeline is False, quant operating step is skipped."""
    db_path = str(tmp_path / "trading_state.db")
    res = run_pipeline(lifecycle_path=db_path, run_quant_pipeline=False)
    assert "quant_operating_pipeline" not in res or res["quant_operating_pipeline"]["status"] == "SKIPPED"


def test_run_pipeline_quant_resilience(tmp_path):
    """Corrupt or invalid dataset does not crash run_pipeline (fail-closed)."""
    db_path = str(tmp_path / "trading_state.db")
    # Passing an invalid dataset object that raises on pipeline run
    res = run_pipeline(
        lifecycle_path=db_path,
        dataset="INVALID_DATASET_OBJECT",
        symbols=["EURUSD"],
        run_quant_pipeline=True,
    )
    assert "quant_operating_pipeline" in res
    # Should catch gracefully and record ERROR
    assert res["quant_operating_pipeline"]["status"] == "ERROR"
    assert res["quant_operating_pipeline"]["execution_authority"] is False


def test_run_pipeline_emits_telemetry_event(tmp_path):
    """Pipeline execution emits DEPLOYMENT_PIPELINE_COMPLETED event to agent bus."""
    import core.agent_event_bus as aeb

    db_path = str(tmp_path / "trading_state.db")
    mock_emit = MagicMock()
    orig_emit = getattr(aeb, "emit_agent_event", None)
    try:
        aeb.emit_agent_event = mock_emit
        np.random.seed(42)
        df = pl.DataFrame({"close": [100.0 + i * 0.05 for i in range(200)]})
        res = run_pipeline(
            lifecycle_path=db_path,
            dataset=df,
            symbols=["EURUSD"],
            run_quant_pipeline=True,
        )
        assert res["quant_operating_pipeline"]["status"] == "COMPLETED"
        assert mock_emit.call_count >= 1
        event_types = [call.kwargs.get("event_type") for call in mock_emit.call_args_list]
        assert "DEPLOYMENT_PIPELINE_COMPLETED" in event_types
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")
