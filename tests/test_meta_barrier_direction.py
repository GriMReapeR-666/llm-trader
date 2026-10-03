"""Regression tests for directional triple-barrier labelling.

`TripleBarrierPathDependentLabeler.apply` used to have no direction parameter:
every event was labelled as if long, so a winning short (entered at a top,
exited lower) touched the geometric lower barrier first and was labelled
y=0 with a negative R -- exactly inverted. A losing short was labelled y=1.
The meta-classifier trained on these labels learned that good shorts are bad.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "data", "core", "intelligence", "operations", "research", "ui"):
    _p = os.path.join(_ROOT, _sub) if _sub else _ROOT
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from core.meta_labeling import TripleBarrierPathDependentLabeler as L


def _prices():
    # bottom (90) at bar 10, top (110) at bar 20
    out = [100.0 - i for i in range(11)]
    out += [90.0 + 2.0 * i for i in range(1, 11)]
    out += [110.0 - 1.5 * i for i in range(1, 11)]
    return out


def _kw(prices):
    return dict(upper_r=1.0, lower_r=1.0, vertical_barrier_bars=30,
                volatility=[1.0] * len(prices))


def test_winning_short_is_labelled_profit_not_loss():
    """The exact regression: entry 110, exit 108.5 must be y=1 with R>0."""
    (e,) = L.apply(_prices(), [20], sides=["short"], **_kw(_prices()))
    assert e.label == 1, e
    assert e.realized_r > 0, e
    assert e.touch_type == "UPPER_TP", e


def test_losing_short_is_labelled_loss_not_profit():
    """Entry 110 as a short is a loser when price keeps rising; must be y=0."""
    prices = _prices()
    (e,) = L.apply(prices, [10], sides=["short"], **_kw(prices))
    assert e.label == 0, e
    assert e.realized_r < 0, e
    assert e.touch_type == "LOWER_SL", e


def test_winning_long_is_unchanged():
    (e,) = L.apply(_prices(), [10], sides=["long"], **_kw(_prices()))
    assert (e.label, e.touch_type) == (1, "UPPER_TP"), e
    assert e.realized_r > 0, e


def test_side_spellings_normalise():
    prices = _prices()
    variants = (
        L.apply(prices, [20], sides=["SELL"], **_kw(prices))[0],
        L.apply(prices, [20], sides=[-1], **_kw(prices))[0],
        L.apply(prices, [20], sides=["s"], **_kw(prices))[0],
    )
    for e in variants:
        assert (e.label, e.touch_type) == (1, "UPPER_TP"), e
        assert e.realized_r > 0, e


def test_default_is_all_long_backwards_compatible():
    """Omitting sides must reproduce the historical all-long behaviour."""
    prices = _prices()
    old = L.apply(prices, [10, 20], **_kw(prices))
    new = L.apply(prices, [10, 20], sides=["long", "long"], **_kw(prices))
    assert [(e.touch_type, e.label, e.realized_r) for e in old] == \
           [(e.touch_type, e.label, e.realized_r) for e in new]
