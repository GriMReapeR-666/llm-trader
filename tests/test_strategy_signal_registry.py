"""Regression: per-strategy signal registry (PU-0156 §8.1).

Contract pinned here:
  1. All 13 registered labels build finite, correctly-sized causal signals;
     perturbing future bars never changes past scores (no lookahead).
  2. Signals are distinct: no pair correlates at ~1.0.
  3. Unknown labels raise (never silently mislabel a generic run); explicit
     allow_fallback returns None.
  4. End-to-end: run_full_validation on a registered label produces
     ruleset == "strategy:<name>", strategy_implemented=True, and DIFFERENT
     headline metrics than the generic default run on the same bars.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from research.strategy_signal_registry import (
    build_signal, has_signal, registry_names, variant_names,
)

LABELS = registry_names()


def _prices(n=2500, seed=11):
    rng = np.random.default_rng(seed)
    c = 1.085 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.0003, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.0003, n)))
    return c, h, l


def test_all_labels_build_finite_causal_signals():
    c, h, l = _prices()
    for name in LABELS + variant_names():
        s = build_signal(name, c, h, l)
        assert len(s) == len(c), name
        assert np.all(np.isfinite(s)), name
    # Causality: future perturbation leaves past scores unchanged.
    c2, h2, l2 = c.copy(), h.copy(), l.copy()
    c2[1800:] += 0.01; h2[1800:] += 0.01
    for name in LABELS + variant_names():
        before = build_signal(name, c, h, l)
        after = build_signal(name, c2, h2, l2)
        assert np.array_equal(before[:1800], after[:1800]), f"lookahead in {name}"


def test_signals_are_distinct():
    c, h, l = _prices()
    sigs = {name: build_signal(name, c, h, l) for name in LABELS}
    import itertools
    for a, b in itertools.combinations(LABELS, 2):
        corr = float(np.corrcoef(sigs[a], sigs[b])[0, 1])
        assert corr < 0.95, f"{a} ~ {b}: corr={corr:.3f}"


def test_unknown_label_refuses_mislabel():
    c, h, l = _prices()
    with pytest.raises(KeyError):
        build_signal("definitely_not_registered", c, h, l)
    assert build_signal("definitely_not_registered", c, h, l,
                        allow_fallback=True) is None
    assert not has_signal("definitely_not_registered")


def _bars_list(n=1200, seed=5):
    c, h, l = _prices(n, seed)
    from datetime import datetime, timedelta, timezone
    t = datetime(2026, 1, 5, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        out.append({"open": c[i] * (1 + 0.00005), "high": h[i], "low": l[i],
                    "close": c[i], "volume": 1000.0,
                    "time": (t + timedelta(hours=i)).isoformat()})
    return out


def test_validation_labels_registered_strategy_and_differs_from_default():
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

    bars = _bars_list()
    with patch("research.continuous_backtest_pipeline.fetch_ohlc", None, create=True):
        # Force the pipeline's own fetch path to use our bars via monkeypatch
        # of fetch_ohlc inside run_full_validation (imported lazily there).
        import research.continuous_backtest_pipeline as cbp
        with patch.object(cbp, "fetch_ohlc", create=True) as _missing:
            pass  # placeholder; real patch below
    # The pipeline imports fetch_ohlc lazily inside the stage; patch the
    # provider module it reaches.
    from data import market_data_provider as mdp
    import pandas as pd

    df = pd.DataFrame(_bars_list())
    df.index = pd.DatetimeIndex([b["time"] for b in _bars_list()])
    with patch.object(mdp, "fetch_ohlc", lambda **kw: df):
        pipe = ContinuousBacktestPipeline()
        res_default = pipe.run_full_validation(
            symbol="EURUSD", strategy_name="default", timeframe="H1",
            bars=len(bars), run_walkforward=False, run_dsr=False,
            compare_live=False)
        res_labeled = pipe.run_full_validation(
            symbol="EURUSD", strategy_name="meanrev_bollinger", timeframe="H1",
            bars=len(bars), run_walkforward=False, run_dsr=False,
            compare_live=False)

    bt_default = res_default["stages"]["backtest"]
    bt_labeled = res_labeled["stages"]["backtest"]
    assert bt_default["ruleset"] == "deterministic_signal"
    assert bt_labeled["ruleset"] == "strategy:meanrev_bollinger"
    assert bt_labeled.get("strategy_implemented") is True
    # Different signals must move the headline metrics on the same bars.
    assert (bt_labeled["total_trades"], bt_labeled["sharpe"]) != \
           (bt_default["total_trades"], bt_default["sharpe"])


def test_variant_resolves_causal_and_honestly_labeled():
    """@sprt_confirm variants: causal, finite, distinct, and honestly labeled.

    Variants are explicitly-ordered research hypotheses (never silent
    replacements of a base label's certification result), so they resolve
    only by exact `name@sprt_confirm` and carry that full string into the
    engine's ruleset attribution.
    """
    assert set(variant_names()) == {
        "agent_regime_supervisor@sprt_confirm", "kronos_h1@sprt_confirm",
        "gold_momentum_h4@sprt_confirm", "macro_nfp_pullback@sprt_confirm",
        "model_bocpd_changepoint@sprt_confirm",
    }
    c, h, l = _prices()
    s = build_signal("agent_regime_supervisor@sprt_confirm", c, h, l)
    assert len(s) == len(c) and np.all(np.isfinite(s))
    # Causality: future perturbation leaves past scores unchanged.
    c2, h2, l2 = c.copy(), h.copy(), l.copy()
    c2[1800:] += 0.01; h2[1800:] += 0.01
    assert np.array_equal(build_signal("agent_regime_supervisor@sprt_confirm", c, h, l)[:1800],
                          build_signal("agent_regime_supervisor@sprt_confirm", c2, h2, l2)[:1800])
    # A variant is distinct from both its base and the raw sprt signal.
    base = build_signal("agent_regime_supervisor", c, h, l)
    sprt = build_signal("sprt_hypothesis_gate", c, h, l)
    assert float(np.corrcoef(s, base)[0, 1]) < 0.95
    assert float(np.corrcoef(s, sprt)[0, 1]) < 0.95
    # Every variant differs from its own base's scores (real transformation,
    # not a relabel).
    for v in variant_names():
        b = v.split("@")[0]
        sv, sb = build_signal(v, c, h, l), build_signal(b, c, h, l)
        assert not np.array_equal(sv, sb), v
    # Unknown variant names still refuse to mislabel.
    with pytest.raises(KeyError):
        build_signal("not_a_label@sprt_confirm", c, h, l)
    # The certification label set is untouched by variant registration.
    assert not {v.split("@")[0] + "@sprt_confirm" for v in variant_names()} & set(registry_names())
