"""Property-based tests (hypothesis) for sizing, validator and latch invariants.

Each property states what a fail-closed safety layer must guarantee. Known
violations are strict xfails tagged with their docs/audit/ROADMAP.md ID; a
fix flips them to XPASS, which fails the run until the marker is removed.
Ported from docs/audit/work/mutation/props (Phase 1, PR 1.3).
"""
import math
import os
import tempfile
from types import SimpleNamespace

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck, assume, example

from core import risk_manager
from core.risk_manager import RiskManager
from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore, PortfolioRiskArbiter
from core.order_execution import validate_order_params, validate_pending_entry_geometry
from data.kill_switch_latch import KillSwitchLatch

SETTINGS = settings(max_examples=300, deadline=None, derandomize=True, database=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])


class FakeMT5:
    def __init__(self, equity, spec):
        self._equity, self._spec = equity, spec

    def account_info(self):
        return SimpleNamespace(equity=self._equity)

    def symbol_info(self, symbol):
        return self._spec

    def positions_get(self, *a, **k):
        return []


def _rm(tmpdir, equity, spec, monkeypatch):
    monkeypatch.setattr(risk_manager, "mt5", FakeMT5(equity, spec))
    rm = RiskManager(balance=equity, risk_per_trade=0.01,
                     state_file=os.path.join(tmpdir, "risk_state.json"))
    rm.spmc_filter = None
    return rm


# ---------------------------------------------------------------- P1 sizing
@SETTINGS
@given(
    equity=st.floats(100, 1_000_000),
    entry=st.floats(0.5, 5000),
    stop_frac=st.floats(0.0005, 0.04),
    tick_size=st.sampled_from([0.00001, 0.0001, 0.001, 0.01, 0.1, 1.0]),
    tick_value=st.floats(0.01, 10),
    step=st.sampled_from([0.001, 0.01, 0.1, 1.0]),
    min_mult=st.integers(1, 10),
    side=st.sampled_from([1, -1]),
)
@pytest.mark.xfail(strict=True, reason="TS-P1 / F-RM-1: lot rounding can exceed the 1 % risk cap")
def test_p1_position_risk_never_exceeds_one_r(equity, entry, stop_frac, tick_size, tick_value, step, min_mult,
                                              side, monkeypatch, tmp_path_factory):
    """Dollar risk of the returned volume must never exceed equity * risk_per_trade
    (fresh state: no streak bonus, no Kelly history, confidence 1.0, no ATR overlay)."""
    spec = SimpleNamespace(trade_tick_size=tick_size, trade_tick_value=tick_value,
                           volume_min=step * min_mult, volume_max=500.0, volume_step=step)
    rm = _rm(str(tmp_path_factory.mktemp("p1")), equity, spec, monkeypatch)
    sl = entry * (1 - side * stop_frac)
    vol = rm.calc_position_size("XYZ", entry, sl, confidence=1.0)
    loss_per_lot = abs(entry - sl) / tick_size * tick_value
    assert vol >= 0
    if vol > 0:
        risk = vol * loss_per_lot
        assert risk <= equity * 0.01 * (1 + 1e-6), (vol, risk, equity * 0.01)
        assert vol >= spec.volume_min - 1e-9
        # volume must be a whole number of broker steps
        assert abs(round(vol / step) * step - vol) < 1e-6, (vol, step)


# ------------------------------------------------- P2 drawdown ladder (monotone, fail-closed)
@SETTINGS
@given(peak=st.floats(1, 1e7), equity=st.one_of(st.floats(-1e7, 1e7), st.just(math.nan), st.just(0.0)))
def test_p2_drawdown_ladder_monotone_and_fail_closed(peak, equity, monkeypatch, tmp_path_factory):
    rm = _rm(str(tmp_path_factory.mktemp("p2")), 10_000.0, None, monkeypatch)
    rm.peak_equity = peak
    m = rm.get_drawdown_multiplier(equity)
    assert m in (0.0, 0.25, 0.5, 0.75, 1.0)
    if not (math.isfinite(equity) and equity > 0):
        # Unknown/zero/negative equity must not size at full risk.
        assert m == 0.0, f"equity={equity} peak={peak} -> multiplier {m}"
    else:
        dd = (peak - equity) / peak
        if dd >= 0.20:
            assert m == 0.0
        # monotone: a deeper drawdown never yields a bigger multiplier
        deeper = rm.get_drawdown_multiplier(equity * 0.9)
        assert deeper <= m


# --------------------------------------------- P3 validator: any failing gate => rejected
def _proposal():
    return TradeProposal(
        strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
        stop_loss=1.0900, take_profit=1.1200, confidence=.8,
        features={"ema_alignment": 1, "mtf_alignment": 1, "adx": 25,
                  "near_key_level": True, "confirmed_candle": True, "volume_confirmed": True})


def _market():
    return {"market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
            "symbol_tradable": True, "spread": .0001, "max_spread": .001,
            "completed_candle": True, "breakout_event": False,
            "stop_distance": .01, "min_stop_distance": .0001,
            "high_impact_news": False, "setup_cooldown_seconds": 0,
            "clock_ok": True, "session_allowed": True, "liquidity_ok": True,
            "microstructure_ok": True, "required_data_ok": True}


NAN = float("nan")
PERTURBATIONS = {
    "stale_tick": {"tick_age_ms": 10_000},
    "nan_tick_age": {"tick_age_ms": NAN},
    "nan_spread": {"spread": NAN},
    "wide_spread": {"spread": 0.01},
    "nan_stop_distance": {"stop_distance": NAN},
    "closed": {"market_open": False},
    "news": {"high_impact_news": True},
    "changepoint_high": {"changepoint_probability": 0.9},
    "changepoint_nan": {"changepoint_probability": NAN},
    "lag_nan": {"lag_confidence": NAN},
    "wf_dead": {"walk_forward_status": "DEAD"},
    "overfit_high": {"overfit_risk_level": "HIGH"},
    "wfe_nan_oos": {"walk_forward_wfe": {"wfe": NAN, "n_folds": 5, "mean_test_sharpe": NAN}},
    "entry_moved_nan_tol": {"execution_price": 1.2, "entry_tolerance": NAN},
    "max_spread_inf_spread_missing": {"max_spread": float("inf"), "spread": float("inf")},
    "nan_max_tick_age": {"max_tick_age_ms": NAN},
    "negative_tick_age": {"tick_age_ms": -10**9},
}


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(keys=st.lists(st.sampled_from(sorted(PERTURBATIONS)), min_size=1, max_size=3, unique=True))
def test_p3_validator_rejects_any_failing_gate(keys, tmp_path_factory):
    store = TradingStore(str(tmp_path_factory.mktemp("p3") / "s.db"))
    v = DeterministicValidator(store, min_walk_forward_efficiency=0.5)
    base = v.validate(_proposal(), _market())
    assert base.approved, base.reasons
    market = _market()
    for k in keys:
        market.update(PERTURBATIONS[k])
    res = v.validate(_proposal(), market)
    assert not res.approved, f"perturbations {keys} were APPROVED"
    # approval and trace must agree
    assert res.approved == (not res.reasons)


# ------------------------------------------------ P4 arbiter: approval implies limits hold
@SETTINGS
@given(equities=st.lists(st.floats(1_000, 100_000), min_size=1, max_size=6),
       new_risk=st.floats(0, 5_000), corr=st.floats(0, 5_000))
def test_p4_arbiter_approval_respects_limits(equities, new_risk, corr, tmp_path_factory):
    store = TradingStore(str(tmp_path_factory.mktemp("p4") / "s.db"))
    arb = PortfolioRiskArbiter(store)
    peak = 0.0
    for eq in equities:
        peak = max(peak, eq)
        ok, reason = arb.permit(eq, new_risk, correlated_risk=corr)
        dd = (peak - eq) / peak
        if dd >= arb.max_drawdown_fraction:
            assert not ok and store.is_kill_switch_active()
        if ok:
            assert store.open_risk() + new_risk <= eq * arb.max_open_risk_fraction + 1e-9
            assert corr + new_risk <= eq * arb.max_correlated_risk_fraction + 1e-9
            assert dd < arb.max_drawdown_fraction


# --------------------------------------------- P5 order geometry: accepted => geometry valid
finite_or_nan = st.one_of(st.floats(0.01, 10_000), st.just(NAN), st.just(float("inf")), st.none())


@SETTINGS
@given(side=st.sampled_from(["buy", "sell", "BUY", "Sell", "long"]),
       price=finite_or_nan, sl=finite_or_nan, tp=finite_or_nan)
@pytest.mark.xfail(strict=True, reason="TS-P5: NaN price/stop accepted by validate_order_params")
def test_p5_validate_order_params_accepts_only_sane_geometry(side, price, sl, tp):
    try:
        validate_order_params(side, price if price is not None else NAN, sl, tp)
    except (ValueError, TypeError, ZeroDivisionError):
        return
    # accepted: everything must be finite, positive and correctly ordered with RR >= 2
    assert price is not None and math.isfinite(price) and price > 0, f"accepted price={price}"
    assert sl is not None and math.isfinite(sl) and sl > 0, f"accepted without valid SL: sl={sl}"
    if side.lower() == "buy":
        assert sl < price
    elif side.lower() == "sell":
        assert sl > price
    else:
        pytest.fail(f"accepted unknown side {side!r}")


@SETTINGS
@given(otype=st.sampled_from(["buy_limit", "sell_limit", "buy_stop", "sell_stop"]),
       entry=st.floats(0.5, 2.0), bid=st.floats(0.5, 2.0), spread=st.floats(0, 0.01))
def test_p5b_pending_geometry_never_marketable(otype, entry, bid, spread):
    ask = bid + spread
    try:
        validate_pending_entry_geometry(otype, entry, bid, ask)
    except ValueError:
        return
    if otype == "buy_limit":
        assert entry < ask
    elif otype == "buy_stop":
        assert entry > ask
    elif otype == "sell_limit":
        assert entry > bid
    else:
        assert entry < bid


# ------------------------------------------------- P6 kill-switch latch never self-clears
@SETTINGS
@given(ops=st.lists(st.one_of(
    st.tuples(st.just("observe"), st.integers(0, 3)),
    st.tuples(st.just("vix"), st.one_of(st.none(), st.just(NAN), st.floats(20, 80))),
    st.tuples(st.just("restart"), st.booleans()),
), max_size=12))
def test_p6_latch_only_clears_on_explicit_calm(ops, tmp_path_factory):
    path = str(tmp_path_factory.mktemp("p6") / "latch.json")
    t = [1000.0]
    latch = KillSwitchLatch(path=path, now_fn=lambda: t[0], vix_clear_threshold=20.0)
    latch.apply_market_data(halt_flag=True, now=t[0])
    for op, arg in ops:
        t[0] += 60
        if op == "observe":
            latch.observe(arg, ["headline"] if arg else [], now=t[0])
        elif op == "vix":
            latch.apply_market_data(vix=arg, now=t[0])
        else:
            if arg:  # simulate a torn/corrupt checkpoint on crash
                with open(path, "w") as fh:
                    fh.write('{"latched": tr')
            latch = KillSwitchLatch(path=path, now_fn=lambda: t[0], vix_clear_threshold=20.0)
        # VIX values drawn are >= 20 (or None/NaN), so nothing may ever clear the latch
        assert latch.snapshot()["latched"], f"latch cleared after {op}={arg}"
