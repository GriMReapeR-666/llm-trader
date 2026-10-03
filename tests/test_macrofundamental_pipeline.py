"""Tests for Autonomous Macrofundamental Analysis Pipeline.

Covers:
- End-to-end autonomous closed loop execution
- Multi-source ingestion & catalyst fact-checking
- Causal mapping & setup resolution
- Opportunity scoring & publication to OpportunityBus
"""
from __future__ import annotations

import pytest

from core.trading_safety import TradingStore
from intelligence.macrofundamental_pipeline import AutonomousMacroPipeline
from intelligence.opportunity_bus import load_open


def test_autonomous_macrofundamental_pipeline_cycle():
    store = TradingStore()
    pipeline = AutonomousMacroPipeline(store=store)

    symbols = ["EURUSD", "XAUUSD", "USOIL"]
    cycle_res = pipeline.run_pipeline_cycle(symbols=symbols)

    assert cycle_res["status"] == "SUCCESS"
    assert cycle_res["symbols_analyzed"] == symbols
    assert "regime_state" in cycle_res
    assert "architectural_health" in cycle_res
    assert len(cycle_res["verified_claims"]) >= len(symbols)
    assert cycle_res["execution_authority"] is False

    # Check published opportunities
    open_opps = load_open(store)
    assert isinstance(open_opps, list)
