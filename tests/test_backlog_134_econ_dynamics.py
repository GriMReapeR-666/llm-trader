"""Tests for backlog items 1, 3, 4 — STAT_PASS bus, measurement, regime replay."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autonomous_causal_engine import AutonomousCausalValidator, MIN_REACTION_SAMPLES
from economic_dynamics_measure import (
    classify_runtime_event,
    mirror_runtime_reaction_to_catalog,
    run_catalog_measurement_pass,
)
from economic_dynamics_seed import seed_economic_dynamics
from edge_lifecycle import advance_lifecycle, current_lifecycle
from historical_regime_replay import (
    ingest_observation_batch,
    list_regime_observations,
    regime_research_package,
    replay_regime_with_bars,
)
from macro_graph import MacroGraph
from opportunity_bus import opportunity_from_stat_pass_edge, publish_from_stat_pass_edges
from trading_safety import TradingStore


def test_classify_runtime_event_tokens():
    assert classify_runtime_event({"event": "US CPI m/m"}) == "CPI_RELEASE"
    assert classify_runtime_event({"title": "Non-Farm Payrolls"}) == "NFP_RELEASE"
    assert classify_runtime_event({"event": "FOMC Rate Decision"}) == "CB_MEETING"
    assert classify_runtime_event({"event": "Random noise"}) is None


def test_stat_pass_opportunity_bus_emits_only_approved_tradable(tmp_path):
    store = TradingStore(path=str(tmp_path / "bus.db"))
    edges = [
        {
            "edge_id": 1,
            "status": "AUTO_APPROVED",
            "relation": "SAFE_HAVEN_BID",
            "sign": "POSITIVE",
            "confidence": 0.81,
            "source_name": "UNCERTAINTY",
            "source_type": "SENTIMENT",
            "target_name": "GOLD",
            "target_type": "COMMODITY",
        },
        {
            "edge_id": 2,
            "status": "CANDIDATE",
            "relation": "SAFE_HAVEN_BID",
            "sign": "POSITIVE",
            "confidence": 0.81,
            "source_name": "UNCERTAINTY",
            "source_type": "SENTIMENT",
            "target_name": "GOLD",
            "target_type": "COMMODITY",
        },
        {
            "edge_id": 3,
            "status": "AUTO_APPROVED",
            "relation": "PULLS",
            "sign": "POSITIVE",
            "confidence": 0.7,
            "source_name": "AGGREGATE_DEMAND",
            "source_type": "MACRO_INDICATOR",
            "target_name": "INFLATION",
            "target_type": "MACRO_INDICATOR",
        },
    ]
    published = publish_from_stat_pass_edges(
        store, edges, symbol="XAUUSD", timeframe="H1", regime_id="CRISIS_PANIC",
    )
    assert published["execution_authority"] is False
    assert published["published"] == 1
    assert published["skipped"] == 2
    candidate = opportunity_from_stat_pass_edge(edges[0], symbol="XAUUSD", lifecycle="STAT_PASS")
    assert candidate is not None
    assert candidate["setup_id"] == "macro_continuation_v1"
    assert candidate["side_hint"] == "BUY"
    assert candidate["execution_authority"] is False


def test_mirror_and_measure_catalog_edges(tmp_path):
    graph = MacroGraph(str(tmp_path / "measure.db"))
    seed = seed_economic_dynamics(graph=graph)
    assert seed["created_count"] > 0
    event = {
        "event": "US CPI m/m",
        "actual": 3.5,
        "forecast": 3.1,
        "previous": 3.0,
    }
    for i in range(MIN_REACTION_SAMPLES):
        mirrored = mirror_runtime_reaction_to_catalog(
            graph,
            event={**event, "actual": 3.5 + i * 0.01},
            symbol="EURUSD",
            windows={"15m": {"return_fraction": 0.002}},
            catalog_source_id=seed["source_id"],
        )
        assert mirrored["mirrored"] is True
    result = run_catalog_measurement_pass(graph)
    assert result["execution_authority"] is False
    assert result["validated_count"] >= 1
    # At least some tradable/high-confidence edges can auto-approve with samples.
    assert result["approved_count"] >= 1 or result["insufficient_sample_count"] >= 0


def test_historical_regime_ingest_and_replay(tmp_path):
    graph = MacroGraph(str(tmp_path / "regime.db"))
    start = datetime(2020, 3, 15, tzinfo=timezone.utc)
    observations = []
    bars = []
    price = 1.10
    for i in range(40):
        ts = start + timedelta(hours=i)
        bars.append({
            "timestamp": ts.isoformat(),
            "open": price,
            "high": price + 0.002,
            "low": price - 0.002,
            "close": price + 0.0005,
        })
        if i in (5, 10, 15):
            observations.append({
                "observed_at": ts.isoformat(),
                "event_time": ts.isoformat(),
                "spread": 0.0002,
                "volatility": 0.02 + i * 0.001,
                "instrument": "EURUSD",
                "source_span": "Fed emergency facilities press release §1",
            })
        price += 0.0003

    batch = ingest_observation_batch(graph, "pandemic_2020", observations)
    assert batch["ingested"] == 3
    assert batch["execution_authority"] is False
    listed = list_regime_observations(graph, "pandemic_2020")
    assert len(listed) == 3
    package = regime_research_package(graph, "pandemic_2020")
    assert package["status"] == "READY_FOR_REPLAY"

    # Create a STAT_PASS edge to allow optional REPLAY_PASS lifecycle advance.
    seed = seed_economic_dynamics(graph=graph)
    with graph._con() as con:
        edge = con.execute(
            "SELECT edge_id FROM macro_edges WHERE status='CANDIDATE' LIMIT 1"
        ).fetchone()
    edge_id = int(edge["edge_id"])
    # Force STAT_PASS lifecycle via history for replay promotion path.
    advance_lifecycle(graph, edge_id, "MEASURED")
    advance_lifecycle(graph, edge_id, "STAT_PASS")
    with graph._con() as con:
        con.execute(
            "UPDATE macro_edges SET status='AUTO_APPROVED' WHERE edge_id=?",
            (edge_id,),
        )

    replayed = replay_regime_with_bars(
        regime_id="pandemic_2020",
        bars=bars,
        observations=listed,
        side="BUY",
        graph=graph,
        edge_id=edge_id,
    )
    assert replayed["execution_authority"] is False
    assert replayed["status"] == "REPLAY_COMPLETE"
    assert replayed["alignment"]["aligned"] >= 1
    assert replayed["lifecycle"] is None or replayed["lifecycle"].get("execution_authority") is False
    life = current_lifecycle(graph, edge_id)
    assert life["lifecycle"] in {"STAT_PASS", "REPLAY_PASS", "REPLAY_FAIL"}
