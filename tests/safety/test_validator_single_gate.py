"""Pin every DeterministicValidator gate on its own.

Mutation testing showed any single gate could be deleted without a test
failing, because existing tests only broke several inputs at once. Each row
here starts from an approved baseline and breaks exactly one input.

Rows marked xfail(strict=True) are known fail-open bugs, tagged with their
docs/audit/ROADMAP.md ID. When a bug is fixed the row starts passing, strict
xfail turns that into a failure, and the fixer removes the marker and ticks
the roadmap box.
"""
import math

import pytest

from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore

NAN = float("nan")


def _proposal(**features):
    base = {"ema_alignment": 1, "mtf_alignment": 1, "adx": 25,
            "near_key_level": True, "confirmed_candle": True, "volume_confirmed": True}
    base.update(features)
    return TradeProposal(strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
                         stop_loss=1.0900, take_profit=1.1200, confidence=.8, features=base)


def _market(**overrides):
    market = {
        "market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
        "symbol_tradable": True, "spread": .0001, "max_spread": .001,
        "completed_candle": True, "breakout_event": False,
        "stop_distance": .01, "min_stop_distance": .0001,
        "high_impact_news": False, "setup_cooldown_seconds": 0,
        # explicit values for the gates that default to "pass" when absent
        "clock_ok": True, "session_allowed": True, "liquidity_ok": True,
        "microstructure_ok": True, "required_data_ok": True,
    }
    market.update(overrides)
    return market


@pytest.fixture
def store(tmp_path):
    return TradingStore(str(tmp_path / "state.db"))


@pytest.fixture
def validator(store):
    return DeterministicValidator(store, min_walk_forward_efficiency=0.5)


def _reasons(result):
    return " | ".join(result.reasons)


def test_baseline_is_approved(validator):
    result = validator.validate(_proposal(), _market())
    assert result.approved, _reasons(result)


# (row id, market overrides, expected reason substring)
MARKET_GATES = [
    ("market_closed", {"market_open": False}, "market not open"),
    ("stale_tick", {"tick_age_ms": 10_000}, "stale tick"),
    ("tick_age_at_limit_is_fresh_boundary", {"tick_age_ms": 5001}, "stale tick"),
    ("clock_unhealthy", {"clock_ok": False}, "clock health rejected tick"),
    ("symbol_not_tradable", {"symbol_tradable": False}, "symbol not tradable"),
    ("spread_wide", {"spread": .01}, "spread too wide"),
    ("spread_just_over", {"spread": .0010001}, "spread too wide"),
    ("session_blocked", {"session_allowed": False}, "session/rollover no-trade filter"),
    ("liquidity_blocked", {"liquidity_ok": False}, "liquidity filter rejected entry"),
    ("microstructure_blocked", {"microstructure_ok": False}, "microstructure filter rejected entry"),
    ("required_data_missing", {"required_data_ok": False, "required_data_reasons": ["calendar feed missing"]},
     "calendar feed missing"),
    ("market_condition_reason", {"market_condition_reasons": ["regime unsuitable"]}, "regime unsuitable"),
    ("microstructure_reason", {"microstructure_reasons": ["book too thin"]}, "book too thin"),
    ("entry_moved", {"execution_price": 1.1050, "entry_tolerance": .0005}, "price moved beyond planned-entry tolerance"),
    ("no_completed_candle", {"completed_candle": False}, "no completed-candle/breakout trigger"),
    ("stop_too_close", {"stop_distance": .00005}, "stop too close"),
    ("rr_below_setup_minimum", {"setup_min_rr": 3.0}, "reward/risk below setup minimum"),
    ("event_window_excluded", {"setup_event_exclusion": True, "high_impact_news": True},
     "setup excludes high-impact event window"),
    ("high_impact_news", {"high_impact_news": True}, "news data missing or high-impact event"),
    ("lag_decayed", {"lag_confidence": 0.1}, "lag confidence decayed below threshold"),
    ("changepoint_high", {"changepoint_probability": 0.9}, "regime changepoint probability above veto threshold"),
    ("walk_forward_dead", {"walk_forward_status": "DEAD"}, "walk-forward decay DEAD"),
    ("wfe_oos_unprofitable", {"walk_forward_wfe": {"wfe": 0.9, "n_folds": 5, "mean_test_sharpe": -0.1}},
     "OOS unprofitable"),
    ("wfe_below_floor", {"walk_forward_wfe": {"wfe": 0.2, "n_folds": 5, "mean_test_sharpe": 0.4}},
     "below floor"),
    ("overfit_high", {"overfit_risk_level": "HIGH"}, "overfit gate REJECT"),
    ("overfit_critical", {"overfit_risk_level": "CRITICAL"}, "overfit gate REJECT"),
    ("phantom_liquidity", {"phantom_liquidity_flag": True}, "phantom liquidity"),
]


@pytest.mark.parametrize("overrides,expected", [pytest.param(o, e, id=i) for i, o, e in MARKET_GATES])
def test_single_market_gate_rejects(validator, overrides, expected):
    result = validator.validate(_proposal(), _market(**overrides))
    assert not result.approved
    assert expected in _reasons(result)


def test_kill_switch_rejects(store, validator):
    store.set_kill_switch(True, "test")
    result = validator.validate(_proposal(), _market())
    assert not result.approved and "kill switch active" in result.reasons


def test_low_confluence_rejects(validator):
    weak = _proposal(ema_alignment=-1, mtf_alignment=0, near_key_level=False,
                     confirmed_candle=False, volume_confirmed=False, adx=5)
    result = validator.validate(weak, _market())
    assert not result.approved
    assert "insufficient deterministic confluence" in _reasons(result)


def test_vpin_toxicity_rejects(validator):
    result = validator.validate(_proposal(vpin_toxicity=0.95), _market())
    assert not result.approved and "vpin toxicity too high" in _reasons(result)


def test_setup_recently_considered_rejects(store, validator):
    first = validator.validate(_proposal(), _market())
    store.log_proposal(_proposal(), first)
    result = validator.validate(_proposal(), _market(setup_cooldown_seconds=900))
    assert not result.approved and "setup recently considered" in _reasons(result)


# ---------------------------------------------------------------- known bugs
# A gate whose input is missing or NaN must fail closed. These pass today.
FAIL_OPEN = [
    ("changepoint_nan", {"changepoint_probability": NAN}, "TS-P3"),
    # missing key -> treated as high-impact (correct); explicit None -> treated as safe
    ("news_flag_none", {"high_impact_news": None}, "P1-H4"),
    ("clock_ok_missing", {"clock_ok": None, "__drop__": "clock_ok"}, "P1-H4"),
    ("session_allowed_missing", {"__drop__": "session_allowed"}, "P1-H4"),
    ("liquidity_ok_missing", {"__drop__": "liquidity_ok"}, "P1-H4"),
    ("microstructure_ok_missing", {"__drop__": "microstructure_ok"}, "P1-H4"),
    ("required_data_ok_missing", {"__drop__": "required_data_ok"}, "P1-H4"),
    ("min_stop_distance_missing", {"__drop__": "min_stop_distance", "stop_distance": 0.0}, "P1-H4"),
]


@pytest.mark.parametrize("overrides,roadmap_id", [
    pytest.param(o, rid, id=i, marks=pytest.mark.xfail(strict=True, reason=f"{rid}: gate fails open"))
    for i, o, rid in FAIL_OPEN
])
def test_missing_or_nan_evidence_fails_closed(validator, overrides, roadmap_id):
    overrides = dict(overrides)
    drop = overrides.pop("__drop__", None)
    market = _market(**overrides)
    if drop:
        market.pop(drop, None)
    result = validator.validate(_proposal(), market)
    assert not result.approved
