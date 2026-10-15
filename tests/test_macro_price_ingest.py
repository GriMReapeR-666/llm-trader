"""Tests for macro price history ingestion and intermarket edge coverage unblocking."""
from __future__ import annotations

import sqlite3
import pandas as pd
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from intelligence.observation_provenance import audit_series, edge_evidence_coverage
from intelligence.macro_observations import ensure_schema
from intelligence.economic_dynamics_seed import seed_economic_dynamics
from intelligence.macro_graph import MacroGraph


def _setup_test_db(tmp_path) -> str:
    db_file = str(tmp_path / "test_macro_prices.db")
    ensure_schema(db_file)
    graph = MacroGraph(db_path=db_file)
    seed_economic_dynamics(graph)
    return db_file


def _mock_price_df(n_days: int = 120, base_price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(end="2026-10-10", periods=n_days, freq="D")
    prices = base_price + np.cumsum(np.random.normal(0, 1, size=n_days))
    df = pd.DataFrame(
        {
            "Open": prices * 0.99,
            "High": prices * 1.01,
            "Low": prices * 0.98,
            "Close": prices,
            "Volume": 100000,
        },
        index=dates,
    )
    return df


class TestMacroPriceIngest:
    """Verify macro price ingestion populates USABLE_AS_EVIDENCE and unblocks causal edges."""

    def test_ingest_macro_prices_populates_evidence_and_unblocks_edges(self, tmp_path):
        from operations.macro_price_ingest import ingest_macro_prices

        db_path = _setup_test_db(tmp_path)

        # Mock YahooFinanceProvider returning 100 days of distinct prices
        mock_yf = MagicMock()
        mock_yf.get_historical_data.side_effect = lambda symbol, **kwargs: _mock_price_df(
            n_days=100, base_price=105.0 if "DX" in symbol else (2000.0 if "GC" in symbol else 75.0)
        )

        with patch("operations.macro_price_ingest.YahooFinanceProvider", return_value=mock_yf):
            res = ingest_macro_prices(
                db_path=db_path,
                symbols_map={"DXY": "DX-Y.NYB", "GOLD": "GC=F", "WTI_OIL": "CL=F"},
                limit_per_series=100,
            )

        assert res["status"] == "SUCCESS"
        assert res["rows_inserted"] >= 300
        assert "DXY" in res["entities_ingested"]
        assert "GOLD" in res["entities_ingested"]
        assert "WTI_OIL" in res["entities_ingested"]

        # Check provenance in database
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            report = audit_series(con)
            usable_entities = set(report.usable_entities)
            assert "DXY" in usable_entities
            assert "GOLD" in usable_entities
            assert "WTI_OIL" in usable_entities

            coverage = edge_evidence_coverage(con)
            # Edge coverage must now have validatable observation edges (e.g. DXY -> GOLD, DXY -> WTI_OIL)
            assert coverage["observation_validatable"] >= 2
            assert coverage["verdict"] == "PARTIAL_COVERAGE"

    def test_ingest_is_idempotent(self, tmp_path):
        from operations.macro_price_ingest import ingest_macro_prices

        db_path = _setup_test_db(tmp_path)
        mock_yf = MagicMock()
        mock_yf.get_historical_data.return_value = _mock_price_df(n_days=80, base_price=50.0)

        with patch("operations.macro_price_ingest.YahooFinanceProvider", return_value=mock_yf):
            res1 = ingest_macro_prices(db_path=db_path, symbols_map={"DXY": "DX-Y.NYB"}, limit_per_series=80)
            rows1 = res1["rows_inserted"]

            # Second run with same data must not duplicate
            res2 = ingest_macro_prices(db_path=db_path, symbols_map={"DXY": "DX-Y.NYB"}, limit_per_series=80)
            rows2 = res2["rows_inserted"]

        assert rows1 == 80
        assert rows2 == 0
