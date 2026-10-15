"""Tests for observation time-series causal edge validation and PREVIOUS_DELTA surprise."""
from __future__ import annotations

import sqlite3
import pytest
import numpy as np
import pandas as pd

from intelligence.macro_graph import MacroGraph
from intelligence.macro_observations import ensure_schema
from intelligence.autonomous_causal_engine import AutonomousCausalValidator, MIN_REACTION_SAMPLES


def _setup_db_with_observations(tmp_path) -> tuple[MacroGraph, str]:
    db_file = str(tmp_path / "obs_causal.db")
    ensure_schema(db_file)
    graph = MacroGraph(db_path=db_file)
    return graph, db_file


def _seed_observation_series(
    con: sqlite3.Connection,
    entity: str,
    dates: list[str],
    values: list[float],
) -> None:
    for dt, val in zip(dates, values):
        con.execute(
            """INSERT INTO macro_observations(
                captured_at, entity, series, observed_at, value, provider, data_provenance
            ) VALUES (?, ?, ?, ?, ?, 'test_prov', 'USABLE_AS_EVIDENCE')""",
            (f"{dt}T00:00:00Z", entity, f"{entity}_SERIES", f"{dt}T00:00:00Z", float(val)),
        )
    con.commit()


class TestObservationCausalValidation:
    """Verify AutonomousCausalValidator can validate macro-state edges via macro_observations."""

    def test_approves_negative_edge_with_sufficient_concordance(self, tmp_path):
        graph, db_path = _setup_db_with_observations(tmp_path)
        source_id = graph.ingest_source(
            title="Fed Macro Series",
            source_url="https://www.federalreserve.gov/data.htm",
            publisher="Federal Reserve Board",
            payload={"kind": "macro_test"},
        )
        edge_id = graph.propose_edge(
            source_name="DXY",
            source_type="CURRENCY_INDEX",
            target_name="GOLD",
            target_type="COMMODITY",
            relation="INVERSE_PRICES",
            sign="NEGATIVE",
            confidence=0.80,
            source_id=source_id,
            notes="DXY inversely drives GOLD",
        )

        # Generate 40 days of negatively correlated price returns
        dates = pd.date_range("2026-01-01", periods=40, freq="D").strftime("%Y-%m-%d").tolist()
        # Synthetic DXY goes up on even days, down on odd days
        # GOLD moves inversely (down on even days, up on odd days) -> 100% win rate
        dxy_vals = [100.0]
        gold_vals = [2000.0]
        for i in range(1, 40):
            dxy_delta = 1.0 if (i % 2 == 0) else -1.0
            gold_delta = -10.0 if (i % 2 == 0) else 10.0
            dxy_vals.append(dxy_vals[-1] + dxy_delta)
            gold_vals.append(gold_vals[-1] + gold_delta)

        with sqlite3.connect(db_path) as con:
            _seed_observation_series(con, "DXY", dates, dxy_vals)
            _seed_observation_series(con, "GOLD", dates, gold_vals)

        validator = AutonomousCausalValidator(graph, min_reaction_samples=30)
        res = validator.validate_candidate_edge(edge_id)

        assert res["approved"] is True
        assert res["status"] == "AUTO_APPROVED"
        assert res["evidence_type"] == "OBSERVATION_TIME_SERIES"
        assert res["valid_samples"] >= 30
        assert res["empirical_win_rate"] >= 0.90
        assert res["correlation"] < -0.5
        assert res["adjusted_confidence"] >= 0.80
        assert res["lifecycle"]["to"] == "STAT_PASS"

    def test_rejects_edge_when_correlation_violates_expected_sign(self, tmp_path):
        graph, db_path = _setup_db_with_observations(tmp_path)
        source_id = graph.ingest_source(
            title="Fed Macro Series",
            source_url="https://www.federalreserve.gov/data.htm",
            publisher="Federal Reserve Board",
            payload={"kind": "macro_test"},
        )
        # Expect NEGATIVE, but series move in same direction
        edge_id = graph.propose_edge(
            source_name="DXY",
            source_type="CURRENCY_INDEX",
            target_name="WTI_OIL",
            target_type="COMMODITY",
            relation="INVERSE_PRICES",
            sign="NEGATIVE",
            confidence=0.80,
            source_id=source_id,
            notes="Should fail when positively correlated",
        )

        dates = pd.date_range("2026-01-01", periods=40, freq="D").strftime("%Y-%m-%d").tolist()
        dxy_vals = [100.0 + i * 0.5 for i in range(40)]
        oil_vals = [70.0 + i * 0.5 for i in range(40)]

        with sqlite3.connect(db_path) as con:
            _seed_observation_series(con, "DXY", dates, dxy_vals)
            _seed_observation_series(con, "WTI_OIL", dates, oil_vals)

        validator = AutonomousCausalValidator(graph, min_reaction_samples=30)
        res = validator.validate_candidate_edge(edge_id)

        assert res["approved"] is False
        assert res["status"] == "REJECTED_STATISTICAL"
        assert res["evidence_type"] == "OBSERVATION_TIME_SERIES"
        assert res["empirical_win_rate"] == 0.0  # Zero directional agreement for NEGATIVE
        assert res["correlation"] > 0.9
        assert res["lifecycle"]["to"] == "STAT_FAIL"

    def test_momentum_surprise_from_previous_when_forecast_is_none(self, tmp_path):
        graph, _ = _setup_db_with_observations(tmp_path)
        source_id = graph.ingest_source(
            title="ALFRED CPI Release",
            source_url="https://api.stlouisfed.org/fred/series/observations",
            publisher="ALFRED",
            payload={"kind": "pit_release"},
        )
        # Record an event reaction with actual and previous, forecast=None
        rx_id = graph.record_event_reaction(
            event_source_id=source_id,
            symbol="EURUSD",
            actual=3.4,
            forecast=None,
            previous=3.1,
            windows={"15m": {"return_fraction": 0.002}},
            source_quality="ALFRED_PIT_RELEASE",
        )
        assert rx_id > 0

        with graph._con() as con:
            row = con.execute("SELECT * FROM event_reactions WHERE id = ?", (rx_id,)).fetchone()
            assert row["surprise"] == pytest.approx(0.3)
            assert row["actual"] == pytest.approx(3.4)
            assert row["previous"] == pytest.approx(3.1)
            assert row["forecast"] is None

    def test_rejects_edge_when_correlation_magnitude_below_hurdle(self, tmp_path):
        """Win rate may be ok on paper, but near-zero correlation (-0.0015) must be rejected."""
        graph, db_path = _setup_db_with_observations(tmp_path)
        source_id = graph.ingest_source(
            title="Fed Macro Series",
            source_url="https://www.federalreserve.gov/data.htm",
            publisher="Federal Reserve Board",
            payload={"kind": "macro_test"},
        )
        edge_id = graph.propose_edge(
            source_name="DXY",
            source_type="CURRENCY_INDEX",
            target_name="GOLD",
            target_type="COMMODITY",
            relation="INVERSE_PRICES",
            sign="NEGATIVE",
            confidence=0.80,
            source_id=source_id,
        )
        dates = pd.date_range("2026-01-01", periods=40, freq="D").strftime("%Y-%m-%d").tolist()
        # Random independent series -> correlation near 0
        np.random.seed(42)
        dxy_vals = [100.0]
        gold_vals = [2000.0]
        for _ in range(1, 40):
            dxy_vals.append(dxy_vals[-1] + np.random.normal(0, 1))
            gold_vals.append(gold_vals[-1] + np.random.normal(0, 10))

        with sqlite3.connect(db_path) as con:
            _seed_observation_series(con, "DXY", dates, dxy_vals)
            _seed_observation_series(con, "GOLD", dates, gold_vals)

        validator = AutonomousCausalValidator(graph, min_reaction_samples=30)
        res = validator.validate_candidate_edge(edge_id)
        # Even if sign happened to match by chance, magnitude is near 0 so it must be rejected
        assert res["approved"] is False
        assert res["status"] == "REJECTED_STATISTICAL"
        assert res["lifecycle"]["to"] == "STAT_FAIL"

    def test_ignores_quarantined_scale_corrupted_series(self, tmp_path):
        """Verify that SCALE_CORRUPTED series is ignored and only USABLE_AS_EVIDENCE series is used."""
        graph, db_path = _setup_db_with_observations(tmp_path)
        source_id = graph.ingest_source(
            title="Fed Macro Series",
            source_url="https://www.federalreserve.gov/data.htm",
            publisher="Federal Reserve Board",
            payload={"kind": "macro_test"},
        )
        edge_id = graph.propose_edge(
            source_name="DXY",
            source_type="CURRENCY_INDEX",
            target_name="GOLD",
            target_type="COMMODITY",
            relation="INVERSE_PRICES",
            sign="NEGATIVE",
            confidence=0.80,
            source_id=source_id,
        )
        dates = pd.date_range("2026-01-01", periods=40, freq="D").strftime("%Y-%m-%d").tolist()
        dxy_vals = [100.0]
        gold_vals = [2000.0]
        for i in range(1, 40):
            dxy_delta = 1.0 if (i % 2 == 0) else -1.0
            gold_delta = -10.0 if (i % 2 == 0) else 10.0
            dxy_vals.append(dxy_vals[-1] + dxy_delta)
            gold_vals.append(gold_vals[-1] + gold_delta)

        with sqlite3.connect(db_path) as con:
            # Seed valid series
            _seed_observation_series(con, "DXY", dates, dxy_vals)
            _seed_observation_series(con, "GOLD", dates, gold_vals)
            # Also seed a corrupted series with SCALE_CORRUPTED provenance
            for dt in dates:
                con.execute(
                    """INSERT INTO macro_observations(
                        captured_at, entity, series, observed_at, value, provider, data_provenance
                    ) VALUES (?, 'DXY', 'realtime_fx', ?, 50.0, 'test_prov', 'SCALE_CORRUPTED')""",
                    (f"{dt}T00:00:00Z", f"{dt}T00:00:00Z"),
                )
            con.commit()

        validator = AutonomousCausalValidator(graph, min_reaction_samples=30)
        res = validator.validate_candidate_edge(edge_id)
        assert res["approved"] is True
        assert res["status"] == "AUTO_APPROVED"
        assert res["correlation"] < -0.5

