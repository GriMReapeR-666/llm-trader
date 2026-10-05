"""Tests for Economic Dynamics catalog, seed, and regime classifier."""
from __future__ import annotations

from intelligence.causal_setup_map import resolve_causal_setup
from intelligence.economic_dynamics_catalog import (
    CAUSAL_EDGES,
    CORRELATION_REGIMES,
    catalog_summary,
    edges_for_regime,
    validate_catalog,
)
from intelligence.economic_dynamics_seed import seed_economic_dynamics
from research.historical_regimes import get_template, template_registry
from intelligence.intermarket_regime import (
    build_intermarket_regime_snapshot,
    classify_intermarket_regime,
    filter_edges_for_live_regime,
    resolve_entity_proxies,
)
from intelligence.macro_graph import MacroGraph
from intelligence.correlation_risk import CorrelationRisk
from datetime import datetime, timezone, timedelta


def test_catalog_integrity():
    assert validate_catalog() == []
    summary = catalog_summary()
    assert summary["entity_count"] >= 40
    assert summary["edge_count"] >= 40
    assert summary["execution_authority"] is False
    assert summary["regime_count"] == len(CORRELATION_REGIMES)


def test_crisis_regime_includes_safe_haven_edges():
    crisis = edges_for_regime("CRISIS_PANIC")
    relations = {(e["source_name"], e["target_name"], e["relation"]) for e in crisis}
    assert ("UNCERTAINTY", "GOLD", "SAFE_HAVEN_BID") in relations
    # Normal inverse Gold↔DXY is offline in crisis (correlation break).
    assert ("DXY", "GOLD", "INVERSE_PRICES") not in relations
    normal = edges_for_regime("NORMAL")
    normal_rels = {(e["source_name"], e["target_name"], e["relation"]) for e in normal}
    assert ("DXY", "GOLD", "INVERSE_PRICES") in normal_rels


def test_seed_idempotent_candidate_only(tmp_path):
    db = str(tmp_path / "econ.db")
    first = seed_economic_dynamics(db_path=db)
    assert first["created_count"] == len(CAUSAL_EDGES)
    assert first["execution_authority"] is False
    second = seed_economic_dynamics(db_path=db)
    assert second["created_count"] == 0
    assert second["skipped_duplicates"] == len(CAUSAL_EDGES)

    graph = MacroGraph(db)
    with graph._con() as con:
        statuses = {row["status"] for row in con.execute("SELECT status FROM macro_edges")}
        count = con.execute("SELECT COUNT(*) c FROM macro_edges").fetchone()["c"]
    assert statuses == {"CANDIDATE"}
    assert count == len(CAUSAL_EDGES)


def test_intermarket_regime_crisis_vs_normal():
    crisis = classify_intermarket_regime({
        "DXY|GOLD": 0.55,
        "GOLD|EQUITIES": -0.70,
        "VIX|EQUITIES": -0.80,
    })
    assert crisis["execution_authority"] is False
    assert crisis["regime_id"] == "CRISIS_PANIC"
    assert crisis["confidence"] > 0.5

    normal = classify_intermarket_regime({
        "DXY|GOLD": -0.60,
        "DXY|WTI_OIL": -0.45,
        "GOLD|EQUITIES": -0.40,
    })
    assert normal["regime_id"] in {"NORMAL_RISK_OFF", "TIGHTENING", "EASY_MONEY"}


def test_causal_setup_map_economic_relations():
    gold = resolve_causal_setup(
        relation="SAFE_HAVEN_BID",
        source_type="SENTIMENT",
        target_type="COMMODITY",
        source_name="UNCERTAINTY",
        target_name="GOLD",
    )
    assert gold["eligible"] is True
    assert gold["setup_id"] == "macro_continuation_v1"
    assert gold["execution_authority"] is False

    fx = resolve_causal_setup(
        relation="DRIVES_CURRENCY_STRENGTH",
        source_type="EVENT_CLASS",
        target_type="CURRENCY",
        source_name="CPI_RELEASE",
        target_name="DXY",
    )
    assert fx["eligible"] is True
    assert fx["setup_id"] == "macro_continuation_v1"


def test_decade_shock_templates_merged():
    ids = {row["regime_id"] for row in template_registry()}
    assert "brexit_2016" in ids
    assert "covid_crash_2020" in ids
    tpl = get_template("oil_negative_2020")
    assert tpl["execution_authority"] is False
    assert "date_window" in tpl


def _bars(count: int, start: datetime, base: float = 100.0, trend: float = 0.01):
    bars = []
    price = base
    for i in range(count):
        dt = start + timedelta(hours=i)
        bars.append({"timestamp": int(dt.timestamp()), "close": price})
        price *= (1 + (trend if i % 2 == 0 else -trend * 0.5))
    return bars


def test_build_intermarket_regime_snapshot_from_correlation_risk(tmp_path):
    db = str(tmp_path / "regime_corr.db")
    risk = CorrelationRisk(db, minimum_samples=10, lookback_bars=40)
    start = datetime.now(timezone.utc) - timedelta(hours=60)
    # Inverse-ish gold/dxy: opposite trends
    risk.record_completed_bars("DXY", "H1", _bars(50, start, base=100, trend=0.012))
    risk.record_completed_bars("XAUUSD", "H1", _bars(50, start, base=2000, trend=-0.011))
    risk.record_completed_bars("US500", "H1", _bars(50, start, base=4000, trend=-0.008))

    approved = [
        {
            "edge_id": 1,
            "relation": "INVERSE_PRICES",
            "notes": json_notes_normal(),
            "execution_authority": False,
        },
        {
            "edge_id": 2,
            "relation": "SAFE_HAVEN_BID",
            "notes": '{"active_regimes":["CRISIS_PANIC"],"execution_authority":false}',
            "execution_authority": False,
        },
    ]
    snap = build_intermarket_regime_snapshot(risk, "H1", approved_edges=approved)
    assert snap["execution_authority"] is False
    assert snap["status"] in {"CLASSIFIED", "INSUFFICIENT_PAIRS"}
    assert "DXY" in snap.get("resolved_proxies", {})
    assert "GOLD" in snap.get("resolved_proxies", {})
    if snap["status"] == "CLASSIFIED":
        filtered = filter_edges_for_live_regime(approved, snap["regime_id"])
        assert all(e.get("execution_authority") is False for e in filtered)
        assert snap["macro_edges_total"] == 2


def json_notes_normal() -> str:
    return '{"active_regimes":["NORMAL","RISK_ON","TIGHTENING","EASY_MONEY","STAGFLATION"],"execution_authority":false}'


def test_resolve_entity_proxies_case_insensitive():
    resolved = resolve_entity_proxies(["xauusd", "usoil", "spy"])
    assert resolved["GOLD"].upper() == "XAUUSD"
    assert resolved["WTI_OIL"].upper() == "USOIL"
    assert resolved["EQUITIES"].upper() == "SPY"