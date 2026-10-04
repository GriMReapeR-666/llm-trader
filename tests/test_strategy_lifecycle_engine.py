import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from operations import strategy_lifecycle as slc
from operations.strategy_evaluation import evaluate as evaluate_lifecycle
from research.setup_registry import SETUPS
from intelligence import llm_orchestrator as orch


# ---------------------------------------------------------------------------
# strategy_lifecycle: durable state machine + append-only history
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded(tmp_path):
    path = str(tmp_path / "lifecycle.db")
    slc.seed_registry(path, {"alpha_v1": object(), "beta_v1": object()},
                      fingerprint="f" * 64)
    return path


def test_seed_is_idempotent_and_states_start_deployed(seeded):
    again = slc.seed_registry(seeded, {"alpha_v1": object()}, fingerprint="f" * 64)
    assert again["count"] == 0
    states = slc.current_states(seeded)
    assert states == {"alpha_v1": "DEPLOYED", "beta_v1": "DEPLOYED"}


def test_operator_demote_freeze_unfreeze_flow(seeded):
    result = slc.transition(seeded, "alpha_v1", "DECAYING",
                            actor="operator:alice", reason="decay")
    assert result["changed"] and result["state"] == "DECAYING"
    frozen = slc.transition(seeded, "alpha_v1", "FROZEN",
                            actor="operator:alice", reason="gate")
    assert frozen["state"] == "FROZEN"
    block = slc.block_info(seeded, "alpha_v1")
    assert block is not None and block["state"] == "FROZEN"
    unfrozen = slc.transition(seeded, "alpha_v1", "DEPLOYED",
                              actor="operator:bob", reason="reviewed")
    assert unfrozen["state"] == "DEPLOYED"
    assert slc.block_info(seeded, "alpha_v1") is None


def test_automation_cannot_promote_or_retire(seeded):
    with pytest.raises(ValueError):
        slc.transition(seeded, "alpha_v1", "VALIDATING", actor="evaluation")
    with pytest.raises(ValueError):
        slc.transition(seeded, "alpha_v1", "RETIRED", actor="evaluation")
    # Auto demotion requires the explicit allow flag even for legal pairs.
    with pytest.raises(ValueError):
        slc.transition(seeded, "alpha_v1", "DECAYING", actor="evaluation")
    ok = slc.transition(seeded, "alpha_v1", "DECAYING", actor="evaluation",
                        allow_auto=True, reason="engine")
    assert ok["changed"]


def test_unknown_transitions_and_states_fail_closed(seeded):
    with pytest.raises(ValueError):
        slc.transition(seeded, "alpha_v1", "WARP_SPEED", actor="operator:x")
    with pytest.raises(ValueError):
        slc.transition(seeded, "alpha_v1", "CANDIDATE", actor="operator:x")
    with pytest.raises(KeyError):
        slc.transition(seeded, "missing_v9", "DECAYING", actor="operator:x")


def test_history_is_append_only(seeded):
    slc.transition(seeded, "alpha_v1", "DECAYING", actor="operator:a")
    con = sqlite3.connect(seeded)
    for tamper in (
        "UPDATE strategy_lifecycle_history SET actor='tampered'",
        "DELETE FROM strategy_lifecycle_history",
    ):
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            con.execute(tamper)
    con.close()


def test_status_board_reports_registry_drift(seeded):
    board = slc.status_board(seeded, registry_specs={"alpha_v1": 1, "beta_v1": 2},
                             registry_fingerprint="a" * 64)
    rows = {row["setup_id"]: row for row in board}
    assert rows["alpha_v1"]["drift"] == "REGISTRY_DRIFT"
    assert not rows["alpha_v1"]["blocked"]
    # Untracked code setup shows as awaiting seed.
    board2 = slc.status_board(seeded, registry_specs={"gamma_v1": 3})
    rows2 = {row["setup_id"]: row for row in board2}
    assert rows2["gamma_v1"]["drift"] == "AWAITING_SEED"

    slc.acknowledge_registry_fingerprint(seeded, "alpha_v1", actor="operator:a",
                                         fingerprint="a" * 64)
    board3 = {row["setup_id"]: row for row in slc.status_board(
        seeded, registry_specs={"alpha_v1": 1}, registry_fingerprint="a" * 64)}
    assert board3["alpha_v1"]["drift"] is None


# ---------------------------------------------------------------------------
# evaluation engine: demotions + alerts from labelled evidence
# ---------------------------------------------------------------------------

def _build_evidence_db(tmp_path: Path) -> str:
    """Two windows per setup: healthy prior month, collapsed current month.

    Rows are real-schema broker-style records (NOT generator fixtures, which
    the synthetic filter now correctly excludes from research datasets).
    """
    from datetime import datetime, timedelta, timezone
    from intelligence.feature_schema import (FEATURE_SCHEMA_VERSION, SCHEMA_HASHES,
                                values_hash)

    path = str(tmp_path / "evidence.db")
    slc.seed_registry(path, {"trend_pullback_v1": 1}, fingerprint="f" * 64)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "close": 1.10, "log_return_1": 0.0004,
        "realized_volatility_20": 0.01, "atr_14": 0.005,
        "ema_50": 1.095, "ema_50_slope": 0.0002, "rsi_14": 58.0,
        "macd_line": 0.001, "macd_signal": 0.0008,
        "macd_histogram": 0.0002, "adx_14": 27.0,
        "bollinger_mid": 1.099, "bollinger_upper": 1.105,
        "bollinger_lower": 1.093, "vwap_20": 1.098,
        "tick_volume": 900, "tick_volume_ratio_20": 1.2,
        "support_20": 1.09, "resistance_20": 1.11,
        "liquidity_sweep_high": 1.109, "liquidity_sweep_low": 1.089,
        "liquidity_sweep_confirmation_high": 1.108,
        "liquidity_sweep_confirmation_low": 1.0895,
    }
    features = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
        "source": "mt5_live",
        "ready": True,
    }

    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS trades (
        ticket INTEGER PRIMARY KEY, magic INTEGER, strategy TEXT, symbol TEXT,
        side TEXT, volume REAL, entry REAL, stop_loss REAL, take_profit REAL,
        risk_amount REAL, opened_at TEXT, closed_at TEXT, exit_price REAL,
        pnl REAL, exit_reason TEXT, payload TEXT NOT NULL, setup_id TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS trade_labels (
        ticket INTEGER PRIMARY KEY, label TEXT NOT NULL, rationale TEXT NOT NULL,
        r_multiple REAL, created_at TEXT NOT NULL, label_rule TEXT,
        review_status TEXT DEFAULT 'AUTO')""")

    ticket = 1

    def insert(month_index: int, r_value: float):
        nonlocal ticket
        opened = base + timedelta(days=30 * month_index + 1, hours=ticket % 20)
        closed = opened + timedelta(hours=2)
        completed_bar = opened - timedelta(minutes=15)
        snapshot = {**features, "completed_bar_time": completed_bar.isoformat(),
                    "captured_at": opened.isoformat()}
        pnl = round(r_value * 10.0, 2)
        payload = {"proposal_features": snapshot}
        cur.execute("""INSERT OR REPLACE INTO trades VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ticket, 460001, "swing", "EURUSD", "BUY", 0.1, 1.0950,
                     1.0900, 1.1070, 10.0, opened.isoformat(), closed.isoformat(),
                     closed.isoformat(), pnl, "target" if r_value > 0 else "stop",
                     json.dumps(payload), "trend_pullback_v1"))
        cur.execute("""INSERT OR REPLACE INTO trade_labels VALUES
            (?,?,?,?,?,?,?)""",
                    (ticket, "WIN" if r_value > 0 else "LOSS",
                     "deterministic", r_value, closed.isoformat(),
                     "deterministic_v1", "AUTO"))
        ticket += 1

    for _ in range(50):
        insert(0, 1.0)
    for _ in range(50):
        insert(1, -0.8)
    # Independent broker submission evidence. Without it the dataset refuses
    # these rows as unbrokered, so the lifecycle evaluator would have nothing
    # to evaluate and the decay decision under test could never fire.
    con.execute("""CREATE TABLE IF NOT EXISTS order_intents (
        intent_key TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
        run_id TEXT, proposal_id TEXT, status TEXT, mt5_ticket INTEGER,
        payload TEXT, result TEXT, error TEXT)""")
    con.execute("""INSERT OR REPLACE INTO order_intents
        (intent_key, created_at, status, mt5_ticket)
        SELECT 'intent:' || ticket, opened_at, 'FILLED', ticket FROM trades""")
    con.commit()
    con.close()
    return path


def test_evaluation_demotes_decayed_setup_and_alerts(tmp_path):
    from research.research_validation import labelled_dataset
    path = _build_evidence_db(tmp_path)
    data = labelled_dataset(path)
    assert data["sample_size"] == 100  # strict dataset accepts real-schema rows

    report = evaluate_lifecycle(
        path,
        registry_specs={"trend_pullback_v1": 1},
        registry_fingerprint="f" * 64,
    )
    by_setup = {row["setup_id"]: row for row in report["setups_evaluated"]}
    row = by_setup["trend_pullback_v1"]
    assert row["decision"] in ("DEMOTED_TO_DECAYING", "FROZEN_PENDING_OPERATOR_REVIEW")
    assert any(t["setup_id"] == "trend_pullback_v1" and t["changed"]
               for t in report["transitions"])
    codes = {a["code"] for a in report["_alerts_raw"]} if "_alerts_raw" in report else None
    assert report["alerts_enqueued"]["count"] >= 1
    states = slc.current_states(path)
    assert states["trend_pullback_v1"] in ("DECAYING", "FROZEN")

    # Second pass on an already-DECAYING row freezes it.
    if states["trend_pullback_v1"] == "DECAYING":
        second = evaluate_lifecycle(path, minimum_samples=100)
        assert slc.current_states(path)["trend_pullback_v1"] == "FROZEN"


def test_evaluation_continues_monitoring_without_samples(tmp_path):
    path = str(tmp_path / "empty.db")
    report = evaluate_lifecycle(path)
    assert report["dataset_sample_size"] == 0
    assert report["transitions"] == []


def test_evaluation_rejects_weak_policy_inputs(tmp_path):
    with pytest.raises(ValueError):
        evaluate_lifecycle(str(tmp_path / "x.db"), minimum_samples=50)
    with pytest.raises(ValueError):
        evaluate_lifecycle(str(tmp_path / "x.db"), max_degradation=1.5)


# ---------------------------------------------------------------------------
# validator integration: FROZEN setups refuse proposals
# ---------------------------------------------------------------------------

def test_validator_blocks_frozen_setup(tmp_path):
    from core.trading_safety import TradeProposal, TradingStore, DeterministicValidator

    db = str(tmp_path / "validator.db")
    store = TradingStore(db)
    slc.seed_registry(db, {"frozen_setup": 1}, fingerprint="f" * 64)
    # Operator path to FROZEN goes through DECAYING (matching the state machine).
    slc.transition(db, "frozen_setup", "DECAYING", actor="operator:op", reason="decay")
    slc.transition(db, "frozen_setup", "FROZEN", actor="operator:op", reason="decayed")

    validator = DeterministicValidator(
        store, lifecycle_gate=lambda sid: slc.block_info(db, sid))

    market = {
        "market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
        "clock_ok": True, "symbol_tradable": True, "spread": 1.0,
        "max_spread": 2.5, "session_allowed": True, "liquidity_ok": True,
        "microstructure_ok": True, "completed_candle": True,
        "stop_distance": 0.01, "min_stop_distance": 0.0,
        "high_impact_news": False, "news_safe": True,
    }
    proposal = TradeProposal(
        "swing", "EURUSD", "BUY", 1.0850, 1.0800, 1.0950, 0.85,
        {"setup_id": "frozen_setup", "ema_alignment": 1, "mtf_alignment": 1,
         "adx": 25, "near_key_level": True, "confirmed_candle": True},
    )
    result = validator.validate(proposal, market)
    assert not result.approved
    assert any("lifecycle FROZEN" in reason for reason in result.reasons)
    trace = result.rule_trace["lifecycle_clear"]
    assert trace["passed"] is False and trace["block"]["state"] == "FROZEN"

    # Unknown setup id stays unblocked (backwards compatible).
    proposal_ok = TradeProposal(
        "swing", "EURUSD", "BUY", 1.0850, 1.0800, 1.0950, 0.85,
        {"setup_id": "untracked_setup", "ema_alignment": 1, "mtf_alignment": 1,
         "adx": 25, "near_key_level": True, "confirmed_candle": True},
    )
    assert validator.validate(proposal_ok, market).approved

    # A gate that raises fails closed.
    def broken_gate(_sid):
        raise RuntimeError("disk error")

    validator_broken = DeterministicValidator(store, lifecycle_gate=broken_gate)
    res_broken = validator_broken.validate(proposal_ok, market)
    assert not res_broken.approved
    assert any("lifecycle UNKNOWN" in reason for reason in res_broken.reasons)


# ---------------------------------------------------------------------------
# model activation verifier
# ---------------------------------------------------------------------------

def test_model_activation_requires_ledger_corroboration_and_artifact(tmp_path):
    from research.model_registry import register, review, activation_status
    from research.research_runs import record as record_run

    db = str(tmp_path / "models.db")
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")

    metrics = {
        "walk_forward_passed": True, "purged_validation_passed": True,
        "point_in_time": True, "sample_gate_passed": True,
        "cost_model_validated": True,
    }
    training_hash = "ab" * 32

    row = register(db, model_key="m1", version="v1", kind="supervised",
                   feature_schema_version="feature_schema_v1",
                   training_data_hash=training_hash, metrics=dict(metrics),
                   artifact_uri=str(artifact))
    review(db, model_id=row["id"], status="APPROVED", reviewer="op")

    # No ledger corroboration yet -> BLOCKED.
    blocked = activation_status(db, model_id=row["id"])
    assert blocked["verdict"] == "BLOCKED"
    assert any("corroborat" in reason for reason in blocked["reasons"])

    # Append a matching passing run -> ELIGIBLE.
    record_run(db, "supervised", {
        "status": "OK",
        "training_data_hash": training_hash,
        "dataset": {"rows": 120},
        "out_of_sample": True,
    })
    eligible = activation_status(db, model_id=row["id"])
    assert eligible["verdict"] == "ACTIVATION_ELIGIBLE"
    assert eligible["checks"]["ledger_runs_matching_dataset_hash"] >= 1

    # Missing artifact file -> BLOCKED again.
    artifact.unlink()
    assert activation_status(db, model_id=row["id"])["verdict"] == "BLOCKED"


# ---------------------------------------------------------------------------
# math fixes: ADX Wilder correctness + IC pairing
# ---------------------------------------------------------------------------

def test_adx_wilder_directional_selection():
    from core.technical_indicators import adx

    n = 200
    idx = pd.RangeIndex(n)
    close = pd.Series(np.linspace(1.0, 2.0, n), index=idx)
    high = close + 0.01
    low = close - 0.01
    series = adx(high, low, close, 14).dropna()

    # Strong one-directional trend must produce a high, bounded ADX.
    assert len(series) > 50
    assert series.iloc[-1] > 40
    assert series.max() <= 100.0001

    # Perfectly alternating bars have no persistent direction: low ADX.
    rng = np.random.default_rng(7)
    flat = pd.Series(1.0 + np.tile([+0.02, -0.02], n // 2), index=idx)
    flat_high = flat + 0.01
    flat_low = flat - 0.01
    flat_adx = adx(flat_high, flat_low, flat.copy(), 14).dropna()
    assert flat_adx.iloc[-1] < 35


def test_information_coefficient_drops_pairs_jointly():
    from core.metrics import calculate_information_coefficient

    forecasts = [0.1, 0.2, float("nan"), 0.4, 0.5, 0.6, 0.7]
    realized = [0.10, 0.20, 0.30, float("nan"), 0.50, 0.60, 0.70]
    ic = calculate_information_coefficient(forecasts, realized)
    # Jointly-dropped pairs leave perfectly aligned data -> IC ~ 1.
    assert ic > 0.99

    with pytest.raises(ValueError):
        calculate_information_coefficient([1, 2, 3], [1, 2])


def test_operator_can_roll_back_a_promotion_to_validating(tmp_path):
    """A mis-promoted row must be returnable to VALIDATING for re-evaluation.

    The ladder previously had no rollback edge, so a row promoted on bad
    evidence could only be demoted as far as DECAYING/FROZEN/RETIRED. Rows that
    already reached DEPLOYED keep using the existing DEPLOYED->DECAYING path.
    """
    import pytest

    from operations import strategy_lifecycle as sl

    db = str(tmp_path / "journal.db")
    sl.register_candidate(db, "alpha_x", reason="discovered")

    sl.transition(db, "alpha_x", "VALIDATING", actor="operator_a",
                  reason="enter validation")
    sl.transition(db, "alpha_x", "PAPER_PROVEN", actor="operator_a",
                  reason="gates passed")

    out = sl.transition(db, "alpha_x", "VALIDATING", actor="operator_a",
                        reason="promotion rested on unscoped evidence",
                        evidence={"defect": "AutoPromoter ignored strategy_key"})
    assert out["changed"] is True
    assert out["from_state"] == "PAPER_PROVEN"
    assert sl.current_states(db)["alpha_x"] == "VALIDATING"

    # Automation may re-validate (graduated authority stops at PAPER_PROVEN)
    # but can never roll itself back out of a state.
    with pytest.raises(ValueError):
        sl.transition(db, "alpha_x", "CANDIDATE", actor="auto_promoter",
                      reason="auto rollback", allow_auto=True)


def test_audit_history_flags_self_transitions_and_empty_evidence(tmp_path):
    """The history table had no auditor at all.

    Nothing checked for self-transitions, missing evidence, or an actor
    performing a move the ladder does not permit for it.
    """
    import json
    import sqlite3

    from operations import strategy_lifecycle as sl

    db = str(tmp_path / "j.db")
    sl.register_candidate(db, "alpha_x", reason="discovered",
                          evidence={"gates": {"x": {"passed": True}}})
    sl.transition(db, "alpha_x", "VALIDATING", actor="operator_a", reason="go",
                  evidence={"gates": {"x": {"passed": True}}})

    con = sqlite3.connect(db)
    # A no-op self-transition and an unauthorized forward promotion.
    con.execute(
        "INSERT INTO strategy_lifecycle_history "
        "(setup_id,from_state,to_state,actor,reason,evidence,created_at) "
        "VALUES ('alpha_x','VALIDATING','VALIDATING','auto_promoter','x','{}','t')")
    con.execute(
        "INSERT INTO strategy_lifecycle_history "
        "(setup_id,from_state,to_state,actor,reason,evidence,created_at) "
        "VALUES ('alpha_x','VALIDATING','PAPER_PROVEN','auto_promoter','x','{}','t')")
    con.commit()
    con.close()

    r = sl.audit_history(db)
    assert r["self_transitions"] == 1, r
    assert r["empty_evidence"] == 2, r
    # The no-op self-transition is unauthorized. The VALIDATING->PAPER_PROVEN
    # row is authorized under graduated authority, so it must NOT be counted.
    assert r["unauthorized_transitions"] == 1, r
    assert r["unauthorized_samples"][0]["from_state"] == "VALIDATING"
    assert r["unauthorized_samples"][0]["to_state"] == "VALIDATING"
    assert r["clean"] is False
    assert r["self_transition_samples"][0]["setup_id"] == "alpha_x"
    assert "AUTO_TRANSITIONS" in r["unauthorized_samples"][0]["reason"]
    assert r["execution_authority"] is False

    # A properly evidenced, sanctioned history is clean.
    db2 = str(tmp_path / "clean.db")
    sl.register_candidate(db2, "ok_x", reason="discovered",
                          evidence={"gates": {"x": {"passed": True}}})
    sl.transition(db2, "ok_x", "VALIDATING", actor="operator_a", reason="go",
                  evidence={"gates": {"x": {"passed": True}}})
    r2 = sl.audit_history(db2)
    assert r2["clean"] is True, r2
    assert r2["self_transitions"] == 0


def test_graduated_authority_bounds_automation_at_paper_proven(tmp_path):
    """Automation may reach PAPER_PROVEN; deployment stays operator-only."""
    import pytest

    from operations import strategy_lifecycle as sl

    db = str(tmp_path / "j.db")
    sl.register_candidate(db, "grad_x", reason="discovered",
                          evidence={"gates": {"x": {"passed": True}}})

    # Automated promotion up to PAPER_PROVEN is permitted.
    out = sl.transition(db, "grad_x", "VALIDATING", actor="auto_promoter",
                        reason="gates", evidence={"gates": {}}, allow_auto=True)
    assert out["state"] == "VALIDATING"
    out = sl.transition(db, "grad_x", "PAPER_PROVEN", actor="auto_promoter",
                        reason="gates", evidence={"gates": {}}, allow_auto=True)
    assert out["state"] == "PAPER_PROVEN"

    # Beyond that an operator is required.
    for target in ("DEMO_PROVEN", "READY_FOR_DEPLOYMENT", "DEPLOYED"):
        with pytest.raises(ValueError):
            sl.transition(db, "grad_x", target, actor="auto_promoter",
                          reason="gates", evidence={"gates": {}}, allow_auto=True)

    assert sl.audit_history(db)["unauthorized_transitions"] == 0
