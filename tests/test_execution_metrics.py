from pathlib import Path
import json
import pytest

from agent_operations import AgentOperations
from journal_audit import qualified_metrics
from trading_safety import TradingStore
from trading_safety import TradeProposal


def test_execution_quality_reports_rates_and_broker_metrics(tmp_path: Path):
    path = str(tmp_path / "metrics.db")
    TradingStore(path)
    operations = AgentOperations(path)
    statuses = ["FILLED", "PARTIAL", "PENDING", "CANCELLED", "EXPIRED", "REJECTED", "UNRESOLVED"]
    with operations._con() as con:
        for index, status in enumerate(statuses):
            con.execute("""INSERT INTO order_intents(
                intent_key,created_at,updated_at,run_id,proposal_id,status,payload,result,error
            ) VALUES(?,?,?,?,?,?,?,?,?)""", (
                f"intent-{index}", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00",
                "run", "proposal", status, json.dumps({"entry": 100.0}),
                json.dumps({"accepted_price": 100.2}) if status == "FILLED" else "{}", ""))
    store = TradingStore(path)
    store.record_execution_report(event_type="FILLED", execution_latency_ms=12.0,
                                  spread_at_submit=0.2, spread_at_fill=0.3,
                                  implementation_shortfall=0.1, requote_count=1,
                                  commission=-0.2, swap=-0.1)
    store.record_execution_report(event_type="PARTIAL", execution_latency_ms=30.0,
                                  spread_at_submit=0.4, spread_at_fill=0.5,
                                  implementation_shortfall=0.2, requote_count=2,
                                  commission=-0.1, swap=-0.05)

    result = qualified_metrics(path)["execution_quality"]

    assert result["status_counts"]["FILLED"] == 1
    assert result["partial_rate"] == 1 / 7
    assert result["unresolved_rate"] == 1 / 7
    assert result["latency_ms_p50"] == 12.0
    assert result["latency_ms_p95"] == 30.0
    assert result["spread_at_fill_mean"] == 0.4
    assert result["implementation_shortfall_mean"] == pytest.approx(0.15)
    assert result["requote_count"] == 3


def test_qualified_metrics_reports_descriptive_calibration(tmp_path: Path):
    path = str(tmp_path / "calibration.db")
    store = TradingStore(path)
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1, 1.0, 1.3, 0.8,
                             {"setup_id": "calibration", "confidence": 0.8})
    store.record_open_trade(1, proposal, 1.0, 1.0, {"features": {"confidence": 0.8}})
    store.record_close(1, 1.3, 0.3, "target")
    # Independent submission evidence: descriptive sections only admit
    # broker-evidenced tickets. AgentOperations creates the canonical
    # order_intents schema (qualified_metrics reads status/payload columns).
    AgentOperations(path)
    with store._connect() as con:
        con.execute(
            "INSERT INTO order_intents "
            "(intent_key, created_at, updated_at, status, payload, result, error, mt5_ticket) "
            "VALUES ('intent-cal', ?, ?, 'FILLED', '{}', '{}', '', 1)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00"))
    report = qualified_metrics(path)
    # The raw row is still useful for operational calibration diagnostics, but
    # it must not count as research evidence until a deterministic label and a
    # hashed point-in-time feature snapshot are present.
    assert report["sample_size"] == 0
    assert report["descriptive_sample_size"] == 1
    assert report["research_quality"]["status"] == "INSUFFICIENT_DATA"
    exclusions = report["research_quality"]["excluded"]
    # The trade carries a deterministic-broker submission record but no
    # deterministic close label, so it must be excluded under a recorded
    # reason. `missing_deterministic_label` fires here (the submission record
    # is present, so `unbrokered_trade` does not).
    #
    # `trade_labels_table_missing` must NOT fire: the table is part of
    # TradingStore's DDL (it feeds the net-EV expectancy gate), so its absence
    # would mean a regression of that fix. This assertion used to name that key
    # as an acceptable outcome back when the table was missing from the schema.
    assert sum(exclusions.values()) >= 1
    assert exclusions.get("trade_labels_table_missing", 0) == 0
    calibration = report["trade_quality"]["calibration"]
    assert calibration["status"] == "DESCRIPTIVE_ONLY"
    assert calibration["sample_size"] == 1
    assert calibration["brier_score"] == pytest.approx(0.04)


def test_qualified_metrics_exposes_sample_gated_risk_quality(tmp_path: Path):
    path = str(tmp_path / "risk-quality.db")
    TradingStore(path)
    risk_quality = qualified_metrics(path)["portfolio_quality"]["risk_quality"]
    assert risk_quality["status"] == "INSUFFICIENT_DATA"
    assert risk_quality["sample_size"] == 0
    assert risk_quality["sharpe_r"] is None
    assert risk_quality["cvar_5_r"] is None
    assert risk_quality["quarter_kelly_risk_fraction"] is None
    assert risk_quality["calmar_r"] is None
    assert risk_quality["omega_r"] is None
    assert risk_quality["risk_of_ruin_10pct"] is None
    assert risk_quality["risk_of_ruin_status"] == "INSUFFICIENT_DATA"


def test_adaptive_growth_metrics_are_explicitly_sample_gated(tmp_path: Path):
    path = str(tmp_path / "adaptive-metrics.db")
    TradingStore(path)
    result = qualified_metrics(path)
    adaptive = result["adaptive_metrics"]
    assert adaptive["growth_quotient"]["comparisons"] == []
    assert adaptive["drawdown_compression"]["comparisons"] == []
    assert adaptive["regime_alignment_efficiency"]["status"] == "INSUFFICIENT_DATA"
    assert adaptive["regime_alignment_efficiency"]["efficiency"] is None
    assert result["setup_decay"]["status"] == "INSUFFICIENT_DATA"
    assert result["setup_decay"]["comparisons"] == []


def test_proposal_quality_keeps_counterfactuals_fail_closed(tmp_path: Path):
    path = str(tmp_path / "proposal-quality.db")
    store = TradingStore(path)
    with store._connect() as con:
        con.executemany(
            """INSERT INTO proposals(created_at,setup_key,strategy,symbol,side,payload,approved,reasons)
               VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("2026-08-10T00:00:00+00:00", "setup-a", "scalp", "EURUSD", "BUY", "{}", 1, "[]"),
                ("2026-08-10T00:01:00+00:00", "setup-b", "scalp", "EURUSD", "BUY", "{}", 0, "[\"spread\"]"),
            ],
        )
    quality = qualified_metrics(path)["proposal_quality"]
    assert quality["total_proposals"] == 2
    assert quality["approved_proposals"] == 1
    assert quality["rejected_proposals"] == 1
    assert quality["approval_rate"] == 0.5
    assert quality["raw_vs_validated_status"] == "INSUFFICIENT_DATA"
    assert quality["raw_vs_validated_lift_r"] is None
