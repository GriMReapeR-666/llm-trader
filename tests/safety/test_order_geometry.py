"""Pin order geometry: SL/TP side, max stop distance, min ATR stop, minimum RR.

Mutation testing showed every comparison in validate_order_params could be
inverted unnoticed (25 % score). Prices use round numbers (100 -> 95) so the
boundaries are exact in floating point.
"""
import pytest

from order_execution import validate_order_params, validate_pending_entry_geometry

NAN = float("nan")

OK = [
    ("buy_basic", ("buy", 100.0, 99.0, 102.0, None)),
    ("sell_basic", ("sell", 100.0, 101.0, 98.0, None)),
    ("buy_sl_at_5pct", ("buy", 100.0, 95.0, 110.0, None)),
    ("sell_sl_at_5pct", ("sell", 100.0, 105.0, 90.0, None)),
    ("buy_stop_at_half_atr", ("buy", 100.0, 99.0, 102.0, 2.0)),
    ("rr_exactly_2", ("buy", 100.0, 99.0, 102.0, None)),
    ("sell_rr_exactly_2", ("sell", 100.0, 101.0, 98.0, None)),
    ("uppercase_side", ("BUY", 100.0, 99.0, 102.0, None)),
]

REJECT = [
    ("buy_sl_above_price", ("buy", 100.0, 101.0, 102.0, None), "must be BELOW"),
    ("buy_sl_equal_price", ("buy", 100.0, 100.0, 102.0, None), "must be BELOW"),
    ("buy_tp_below_price", ("buy", 100.0, 99.0, 98.0, None), "must be ABOVE"),
    ("buy_tp_equal_price", ("buy", 100.0, 99.0, 100.0, None), "must be ABOVE"),
    ("sell_sl_below_price", ("sell", 100.0, 99.0, 98.0, None), "must be ABOVE"),
    ("sell_sl_equal_price", ("sell", 100.0, 100.0, 98.0, None), "must be ABOVE"),
    ("sell_tp_above_price", ("sell", 100.0, 101.0, 102.0, None), "must be BELOW"),
    ("sell_tp_equal_price", ("sell", 100.0, 101.0, 100.0, None), "must be BELOW"),
    ("buy_sl_over_5pct", ("buy", 100.0, 94.99, 120.0, None), "exceeds max safety"),
    ("sell_sl_over_5pct", ("sell", 100.0, 105.01, 80.0, None), "exceeds max safety"),
    ("stop_under_half_atr", ("buy", 100.0, 99.01, 104.0, 2.0), "too tight"),
    ("rr_below_2", ("buy", 100.0, 99.0, 101.99, None), "below minimum 2.0"),
    ("sell_rr_below_2", ("sell", 100.0, 101.0, 98.01, None), "below minimum 2.0"),
]


@pytest.mark.parametrize("args", [pytest.param(a, id=i) for i, a in OK])
def test_valid_geometry_passes(args):
    side, price, sl, tp, atr = args
    validate_order_params(side, price, sl, tp, atr=atr)


@pytest.mark.parametrize("args,message", [pytest.param(a, m, id=i) for i, a, m in REJECT])
def test_invalid_geometry_rejects(args, message):
    side, price, sl, tp, atr = args
    with pytest.raises(ValueError, match=message):
        validate_order_params(side, price, sl, tp, atr=atr)


# ---------------------------------------------------------------- known bugs
@pytest.mark.xfail(strict=True, reason="known fail-open order geometry (see roadmap_id)")
@pytest.mark.parametrize("args,roadmap_id", [
    pytest.param(("long", 100.0, 101.0, 98.0, None), "P1-F-TS-6", id="unknown_side_skips_all_checks"),
    pytest.param(("buy", 100.0, None, 102.0, None), "PLAN-1.3", id="no_stop_loss_skips_all_checks"),
    pytest.param(("buy", 100.0, 0.0, 102.0, None), "PLAN-1.3", id="zero_stop_loss_skips_all_checks"),
    pytest.param(("buy", NAN, 99.0, 102.0, None), "TS-P5", id="nan_price"),
    pytest.param(("buy", 100.0, NAN, 102.0, None), "TS-P5", id="nan_stop"),
])
def test_incomplete_or_invalid_orders_are_rejected(args, roadmap_id):
    side, price, sl, tp, atr = args
    with pytest.raises(ValueError):
        validate_order_params(side, price, sl, tp, atr=atr)


# bid 1.1000 / ask 1.1002
PENDING_OK = [
    ("buy_limit_below_ask", "buy_limit", 1.0990),
    ("buy_stop_above_ask", "buy_stop", 1.1010),
    ("sell_limit_above_bid", "sell_limit", 1.1010),
    ("sell_stop_below_bid", "sell_stop", 1.0990),
]
PENDING_REJECT = [
    ("buy_limit_at_ask", "buy_limit", 1.1002),
    ("buy_limit_above_ask", "buy_limit", 1.1010),
    ("buy_stop_at_ask", "buy_stop", 1.1002),
    ("buy_stop_below_ask", "buy_stop", 1.0990),
    ("sell_limit_at_bid", "sell_limit", 1.1000),
    ("sell_limit_below_bid", "sell_limit", 1.0990),
    ("sell_stop_at_bid", "sell_stop", 1.1000),
    ("sell_stop_above_bid", "sell_stop", 1.1010),
]


@pytest.mark.parametrize("otype,entry", [pytest.param(o, e, id=i) for i, o, e in PENDING_OK])
def test_pending_entry_not_marketable_passes(otype, entry):
    validate_pending_entry_geometry(otype, entry, 1.1000, 1.1002)


@pytest.mark.parametrize("otype,entry", [pytest.param(o, e, id=i) for i, o, e in PENDING_REJECT])
def test_marketable_pending_entry_rejects(otype, entry):
    with pytest.raises(ValueError, match="marketable or crossed"):
        validate_pending_entry_geometry(otype, entry, 1.1000, 1.1002)


@pytest.mark.parametrize("otype,entry,bid,ask,message", [
    ("market", 1.1, 1.1, 1.1002, "invalid pending order type"),
    ("buy_limit", NAN, 1.1, 1.1002, "finite positive"),
    ("buy_limit", 1.09, 0.0, 1.1002, "finite positive"),
    ("buy_limit", 1.09, 1.1003, 1.1002, "inverted bid/ask"),
    ("buy_limit", "x", 1.1, 1.1002, "numeric"),
])
def test_pending_geometry_bad_inputs_reject(otype, entry, bid, ask, message):
    with pytest.raises(ValueError, match=message):
        validate_pending_entry_geometry(otype, entry, bid, ask)
