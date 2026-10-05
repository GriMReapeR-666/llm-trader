"""BeyondPromptingAdapter llm_fn routing through the shared llm_router adapter.

Both LLM surfaces of the agentic loop — hypothesis generation AND factor
proposals (via AlphaDiscoveryEngine) — must default to the same
``_router_llm_generate`` adapter so every configured provider serves the
loop; an explicit ``llm_fn`` still overrides, and router failure degrades to
the static hypothesis templates exactly as the pre-router behavior did.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))

from research.beyond_prompting_adapter import (  # noqa: E402
    HYPOTHESIS_TEMPLATES,
    BeyondPromptingAdapter,
    _hypothesis_alignment_score,
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """Isolate experience-memory state files and never hit the network."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "1")


def _make_ohlcv(n=300, seed=42):
    import numpy as np
    import pandas as pd
    np.random.seed(seed)
    close = 1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, n))
    df = pd.DataFrame({
        "open": close - np.random.uniform(0, 0.001, n),
        "high": close + np.random.uniform(0, 0.002, n),
        "low": close - np.random.uniform(0, 0.002, n),
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })
    df["returns"] = df["close"].pct_change().fillna(0.0)
    return df


def test_default_llm_fn_is_router_adapter():
    from core.alpha_discovery import _router_llm_generate
    adapter = BeyondPromptingAdapter()
    assert adapter.llm_fn is _router_llm_generate


def test_hypothesis_uses_llm_fn_without_explicit_injection(monkeypatch):
    """No explicit llm_fn given: hypothesis generation must still hit the
    router adapter (this is the regression the wiring fixes)."""
    from research import beyond_prompting_adapter as bpa
    calls = []

    def fake_router(prompt):
        calls.append(prompt)
        return "Volume confirms short-term FX direction on up bars | volume_price | router hypothesis"

    monkeypatch.setattr(bpa, "_router_llm_fn", fake_router)
    adapter = BeyondPromptingAdapter()
    hypo = adapter._llm_hypothesis(0)
    assert calls, "router adapter was not invoked for hypothesis generation"
    assert hypo["category"] == "volume_price"
    assert "router hypothesis" in hypo["rationale"]


def test_router_failure_degrades_to_template(monkeypatch):
    from research import beyond_prompting_adapter as bpa
    import core.alpha_discovery as ad

    def boom(prompt):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(bpa, "_router_llm_fn", boom)
    adapter = BeyondPromptingAdapter()
    hypo = adapter._llm_hypothesis(1)
    assert hypo == HYPOTHESIS_TEMPLATES[1 % len(HYPOTHESIS_TEMPLATES)]
    # And the factor surface degrades too: engine's default fn raises.
    # Stub the router's generate so this asserts the degradation contract and
    # not the live reachability of whichever provider happens to be configured.
    class _DownRouter:
        agents = {"alpha_discovery": {}}

        def generate(self, *a, **kw):
            raise RuntimeError("all providers down")

    from data import llm_router as _lr
    # _router_llm_generate imports get_router inside the function body, so the
    # patch has to land on the source module, not on alpha_discovery.
    monkeypatch.setattr(_lr, "get_router", lambda: _DownRouter(), raising=False)
    with pytest.raises(Exception):
        ad._router_llm_generate("anything")


def test_explicit_llm_fn_still_wins(monkeypatch):
    from research import beyond_prompting_adapter as bpa
    seen = []

    def explicit_fn(prompt):
        seen.append(prompt)
        return "Custom hypothesis about liquidity exhaustion | mean_reversion | explicit fn"

    adapter = BeyondPromptingAdapter(llm_fn=explicit_fn)
    hypo = adapter._llm_hypothesis(0)
    assert seen, "explicit llm_fn must be used"
    assert hypo["category"] == "mean_reversion"
    monkeypatch.setattr(bpa, "_router_llm_fn", lambda p: (_ for _ in ()).throw(AssertionError("router must not run")))
    assert adapter.llm_fn is explicit_fn


def test_run_iteration_routes_factors_through_engine_default(monkeypatch):
    """Factor proposals must go through the engine's router default, with
    explicit-llm_fn callers still overriding — one source of truth. The
    sandbox-validated count is data-dependent on synthetic bars, so the
    contract asserted here is the ROUTING (forwarded fn, real cycle run,
    LLM-sourced hypothesis), not factor quality."""
    from research import beyond_prompting_adapter as bpa

    def factor_fn(prompt):
        if "autonomous alpha factor researcher" in prompt:
            # Engine's factor-generation prompt.
            return ("delta(close, 1) * ts_rank(volume, 20) | volume_price | factor\n"
                    "ts_argmin(low, 10) - ts_argmax(high, 10) | reversal | factor2")
        # Hypothesis prompt.
        return "Rising volume on up bars confirms short-term direction | volume_price | router"

    adapter = BeyondPromptingAdapter(state_file=os.path.join(os.getcwd(), "bp_state.json"),
                                     llm_fn=factor_fn)
    # Loosen sandbox thresholds so evaluation RUNS; the OOS gate stays real.
    adapter.engine.evaluator.min_ic_ir = 0.0
    adapter.engine.evaluator.min_ic_mean = -1.0
    adapter.engine.evaluator.max_turnover = 10.0
    adapter.engine.evaluator.min_sharpe = -10.0

    forwarded = {}
    real_cycle = adapter.engine.run_discovery_cycle

    def spy_cycle(train_df, test_df, llm_generate_fn=None):
        forwarded["fn"] = llm_generate_fn
        return real_cycle(train_df, test_df, llm_generate_fn=llm_generate_fn)

    monkeypatch.setattr(adapter.engine, "run_discovery_cycle", spy_cycle)

    df = _make_ohlcv(300)
    result = adapter.run_iteration(df.iloc[:200], df.iloc[200:], iteration=0)

    assert forwarded["fn"] is factor_fn          # adapter's llm_fn forwarded
    assert adapter.engine.memory.memory.generation_count >= 1  # real cycle ran
    assert result.hypotheses and result.hypotheses[0]["category"] == "volume_price"
    assert result.hypotheses[0]["hypothesis"].startswith("Rising volume")  # LLM, not template
    assert isinstance(result.proposed, int)


def test_production_constructor_gets_router_default():
    """core/auto_quant_tools constructs BeyondPromptingAdapter() bare; the
    default must already be the router adapter (no explicit llm_fn there)."""
    from core.alpha_discovery import _router_llm_generate
    adapter = BeyondPromptingAdapter()  # exactly as auto_quant_tools does
    assert adapter.llm_fn is _router_llm_generate


def test_alignment_score_unchanged():
    assert _hypothesis_alignment_score(
        "volume confirms direction", "delta(close,1) * ts_rank(volume,20)") > 0
    assert _hypothesis_alignment_score("unrelated", "delta(close,1)") == 0.0
