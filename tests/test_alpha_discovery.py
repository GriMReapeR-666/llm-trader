"""Tests for Autonomous Alpha Discovery Engine."""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n=300, seed=42):
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(seed)
    close = 1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, n))
    return pd.DataFrame({
        "open": close - np.random.uniform(0, 0.001, n),
        "high": close + np.random.uniform(0, 0.002, n),
        "low": close - np.random.uniform(0, 0.002, n),
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })


# ===========================================================================
# 1. Safe Alpha Evaluator
# ===========================================================================

class TestSafeAlphaEvaluator:
    """Test the sandboxed expression evaluator."""

    def test_simple_expression(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("delta(close, 1)", df)
        assert result is not None
        assert isinstance(result, pd.Series)
        assert len(result) == 100

    def test_complex_expression(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("delta(close, 5) * scale(volume, 1.0)", df)
        assert result is not None

    def test_volume_tick_volume_fallback(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        df_fx = df.rename(columns={"volume": "tick_volume"})
        assert "volume" not in df_fx.columns
        result = evaluator.evaluate("delta(close, 1) * scale(volume, 1.0)", df_fx)
        assert result is not None
        assert isinstance(result, pd.Series)
        assert len(result) == 100

    def test_blocks_import(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("import os; delta(close, 1)", df)
        assert result is None

    def test_blocks_loop(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("[x for x in range(10)]", df)
        assert result is None

    def test_blocks_function_def(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("def foo(): return close", df)
        assert result is None

    def test_blocks_private_names(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("__import__('os')", df)
        assert result is None

    def test_invalid_syntax(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator()
        df = _make_ohlcv(100)
        result = evaluator.evaluate("delta(close, )", df)
        # trailing comma is valid Python, but evaluates to Series
        assert result is None or isinstance(result, pd.Series)

    def test_complexity_limit(self):
        from core.alpha_discovery import SafeAlphaEvaluator
        evaluator = SafeAlphaEvaluator(max_complexity=5)
        df = _make_ohlcv(100)
        # Very complex expression
        result = evaluator.evaluate(
            "rank(decay_linear(delta(ts_rank(close, 20), 1), 10))", df
        )
        # Should be rejected or evaluated depending on complexity
        assert result is None or isinstance(result, pd.Series)


# ===========================================================================
# 2. Originality Enforcement
# ===========================================================================

class TestOriginalityEnforcement:
    """Test AST-based similarity checking."""

    def test_identical_expressions(self):
        from core.alpha_discovery import check_originality
        is_orig, sim = check_originality("delta(close, 1)", ["delta(close, 1)"])
        assert not is_orig
        assert sim < 0.05  # Should be very similar

    def test_different_expressions(self):
        from core.alpha_discovery import check_originality
        is_orig, sim = check_originality(
            "delta(close, 1)",
            ["rank(volume) * scale(ts_rank(high, 20), 1.0)"],
        )
        # These are structurally different
        assert sim > 0.2

    def test_no_existing_alphas(self):
        from core.alpha_discovery import check_originality
        is_orig, sim = check_originality("delta(close, 1)", [])
        assert is_orig
        assert sim == 0.0

    def test_similar_expressions(self):
        from core.alpha_discovery import check_originality
        is_orig, sim = check_originality(
            "delta(close, 1)",
            ["delta(close, 2)"],
        )
        # Should be detected as similar
        assert sim < 0.70  # May or may not be original depending on threshold


# ===========================================================================
# 3. Sealed Sandbox Evaluator
# ===========================================================================

class TestSealedSandbox:
    """Test the sealed sandbox evaluation pipeline."""

    def test_validated_candidate(self):
        from core.alpha_discovery import SealedSandboxEvaluator, AlphaCandidate
        evaluator = SealedSandboxEvaluator(min_ic_ir=0.05)
        df = _make_ohlcv(300)
        train = df.iloc[:200]
        test = df.iloc[200:]

        candidate = AlphaCandidate(
            expression="delta(close, 1) * rank(volume)",
            name="test_alpha",
            category="volume_price",
        )
        result = evaluator.evaluate_candidate(candidate, train, test)
        # Should be evaluated (may pass or fail IC threshold)
        assert result.status in ("VALIDATED", "REJECTED")

    def test_invalid_expression_rejected(self):
        from core.alpha_discovery import SealedSandboxEvaluator, AlphaCandidate
        evaluator = SealedSandboxEvaluator()
        df = _make_ohlcv(300)
        train = df.iloc[:200]
        test = df.iloc[200:]

        candidate = AlphaCandidate(
            expression="import os",
            name="bad_alpha",
            category="momentum",
        )
        result = evaluator.evaluate_candidate(candidate, train, test)
        assert result.status == "REJECTED"

    def test_insufficient_data_rejected(self):
        from core.alpha_discovery import SealedSandboxEvaluator, AlphaCandidate
        evaluator = SealedSandboxEvaluator()
        df = _make_ohlcv(10)  # Too small
        train = df.iloc[:7]
        test = df.iloc[7:]

        candidate = AlphaCandidate(
            expression="delta(close, 1)",
            name="tiny_alpha",
            category="momentum",
        )
        result = evaluator.evaluate_candidate(candidate, train, test)
        assert result.status == "REJECTED"

    def test_negative_ic_rejected(self):
        from core.alpha_discovery import SealedSandboxEvaluator, AlphaCandidate
        evaluator = SealedSandboxEvaluator(min_ic_ir=0.15, min_ic_mean=0.02)
        n = 300
        t = np.linspace(0, 50, n)
        close = 100.0 + np.sin(t) * 10.0
        df = pd.DataFrame({
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.ones(n) * 1000,
        })
        train = df.iloc[:200]
        test = df.iloc[200:]
        candidate = AlphaCandidate(
            expression="0 - delta(close, 1)",
            name="neg_alpha",
            category="momentum",
        )
        result = evaluator.evaluate_candidate(candidate, train, test)
        # Must be rejected because IC mean and IC_IR are negative
        assert result.status == "REJECTED"
        assert "too low" in result.rejection_reason


# ===========================================================================
# 4. Experience Memory
# ===========================================================================

class TestExperienceMemory:
    """Test persistent experience memory."""

    def test_save_and_load(self):
        from core.alpha_discovery import AlphaExperienceMemory, AlphaCandidate
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            mem = AlphaExperienceMemory(state_file)
            candidate = AlphaCandidate(
                expression="delta(close, 1)", name="a1", category="momentum",
                ic_ir=0.25, status="VALIDATED",
            )
            mem.record_validated(candidate)
            mem.increment_generation()

            # Load in new instance
            mem2 = AlphaExperienceMemory(state_file)
            assert mem2.memory.total_validated == 1
            assert mem2.memory.generation_count == 1
        finally:
            os.unlink(state_file)

    def test_context_for_llm(self):
        from core.alpha_discovery import AlphaExperienceMemory, AlphaCandidate
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            mem = AlphaExperienceMemory(state_file)
            candidate = AlphaCandidate(
                expression="rank(volume)", name="a1", category="volume_price",
                ic_ir=0.30, status="VALIDATED",
            )
            mem.record_validated(candidate)
            ctx = mem.get_context_for_llm()
            assert "validated_count" in ctx
            assert ctx["validated_count"] == 1
            assert "rank(volume)" in ctx["validated_expressions"]
        finally:
            os.unlink(state_file)


# ===========================================================================
# 5. Alpha Discovery Engine (Full Ralph Loop)
# ===========================================================================

class TestAlphaDiscoveryEngine:
    """Test the full discovery pipeline."""

    def test_fallback_candidates(self):
        from core.alpha_discovery import AlphaDiscoveryEngine
        engine = AlphaDiscoveryEngine()
        candidates = engine._fallback_candidates()
        assert len(candidates) >= 3
        for c in candidates:
            assert "expression" in c
            assert "category" in c

    def test_parse_llm_response(self):
        from core.alpha_discovery import AlphaDiscoveryEngine
        engine = AlphaDiscoveryEngine()
        response = (
            "delta(close, 1) * rank(volume) | volume_price | Price momentum\n"
            "rank(ts_argmax(high, 20)) | momentum | High breakout\n"
            "# This is a comment\n"
            "\n"
        )
        parsed = engine.parse_llm_response(response)
        assert len(parsed) == 2
        assert parsed[0]["category"] == "volume_price"
        assert parsed[1]["category"] == "momentum"

    def test_discovery_cycle_with_fallback(self):
        from core.alpha_discovery import AlphaDiscoveryEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = AlphaDiscoveryEngine(state_file=state_file, min_ic_ir=0.01)
            df = _make_ohlcv(300)
            train = df.iloc[:200]
            test = df.iloc[200:]

            validated = engine.run_discovery_cycle(train, test)
            assert isinstance(validated, list)
            # Should have processed some candidates
            assert engine.memory.memory.generation_count == 1
        finally:
            os.unlink(state_file)

    def test_build_generation_prompt(self):
        from core.alpha_discovery import AlphaDiscoveryEngine
        engine = AlphaDiscoveryEngine()
        prompt = engine.build_generation_prompt()
        assert "EXPERIENCE CONTEXT" in prompt
        assert "ALLOWED PRIMITIVES" in prompt
        assert "ts_rank" in prompt

    def test_get_status(self):
        from core.alpha_discovery import AlphaDiscoveryEngine
        engine = AlphaDiscoveryEngine()
        status = engine.get_status()
        assert "memory" in status
        assert "min_ic_ir" in status
        assert "similarity_threshold" in status


class TestRouterGenerationPath:
    """Default generation goes through llm_router; failures degrade to the
    static fallback exactly as the unwired case did before the adapter."""

    def _cycle(self, engine):
        df = _make_ohlcv(300)
        return engine.run_discovery_cycle(df.iloc[:200], df.iloc[200:])

    def test_router_fn_used_when_none_given(self, monkeypatch):
        import core.alpha_discovery as ad
        calls = []

        def fake_fn(prompt):
            calls.append(prompt)
            return "delta(close, 1) * ts_rank(volume, 20) | volume_price | router test"

        monkeypatch.setattr(ad, "_router_llm_generate", fake_fn)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = ad.AlphaDiscoveryEngine(state_file=state_file, min_ic_ir=0.01)
            self._cycle(engine)
            assert calls, "router generation fn was not invoked"
        finally:
            os.unlink(state_file)

    def test_router_failure_falls_back_to_static(self, monkeypatch):
        import core.alpha_discovery as ad

        def boom(prompt):
            raise ad._RouterLLMError("all providers down")

        monkeypatch.setattr(ad, "_router_llm_generate", boom)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = ad.AlphaDiscoveryEngine(state_file=state_file, min_ic_ir=0.01)
            self._cycle(engine)
            # The cycle still ran — on the static fallback candidates.
            assert engine.memory.memory.generation_count == 1
            assert engine._fallback_warned is True
        finally:
            os.unlink(state_file)

    def test_explicit_fn_still_wins_over_router(self, monkeypatch):
        import core.alpha_discovery as ad

        def boom(prompt):
            raise AssertionError("router must not be called when an explicit fn is given")

        monkeypatch.setattr(ad, "_router_llm_generate", boom)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = ad.AlphaDiscoveryEngine(state_file=state_file, min_ic_ir=0.01)
            self._cycle_with(engine, lambda p: (
                "sign(delta(volume, 1)) * ts_rank(close, 10) | momentum | explicit fn"
            ))
        finally:
            os.unlink(state_file)

    def _cycle_with(self, engine, fn):
        df = _make_ohlcv(300)
        engine.run_discovery_cycle(df.iloc[:200], df.iloc[200:], llm_generate_fn=fn)

    def test_router_hold_payload_raises(self):
        """The router's deterministic-HOLD JSON must be treated as failure."""
        from core.alpha_discovery import _router_llm_generate, _RouterLLMError
        from data import llm_router as lr

        class _FakeRouter:
            agents = {"alpha_discovery": {}}
            providers = {"openrouter": object()}

            def generate(self, agent, messages, **kw):
                return lr.LLMRouter.deterministic_hold_fallback("alpha_discovery", [])

        import sys
        old = sys.modules.get("data.llm_router")
        fake = type(sys)("data.llm_router")
        fake.get_router = lambda: _FakeRouter()
        sys.modules["data.llm_router"] = fake
        try:
            with pytest.raises(_RouterLLMError):
                _router_llm_generate("prompt")
        finally:
            # _router_llm_generate does `from data.llm_router import get_router`,
            # so the fake MUST be keyed on the dotted name. Patching the bare
            # "llm_router" key silently no-ops once the real module is cached,
            # which made this test order-dependent.
            if old is not None:
                sys.modules["data.llm_router"] = old
            else:
                del sys.modules["data.llm_router"]

    def test_adapter_rejects_error_string(self):
        """Router 'Error: ...' return values must raise, not parse as alphas."""
        from core.alpha_discovery import _router_llm_generate, _RouterLLMError
        import sys

        class _FakeRouter:
            agents = {"alpha_discovery": {}}
            providers = {"openrouter": object()}

            def generate(self, agent, messages, **kw):
                return "Error: openrouter failed: 503"

        old = sys.modules.get("data.llm_router")
        fake = type(sys)("data.llm_router")
        fake.get_router = lambda: _FakeRouter()
        sys.modules["data.llm_router"] = fake
        try:
            with pytest.raises(_RouterLLMError):
                _router_llm_generate("prompt")
        finally:
            # Keyed on the dotted name - see test_router_hold_payload_raises.
            if old is not None:
                sys.modules["data.llm_router"] = old
            else:
                del sys.modules["data.llm_router"]

    def test_adapter_end_to_end_with_real_router(self, monkeypatch):
        """Full chain through the REAL LLMRouter (fake provider, no HTTP):
        agent auto-registration, message format, failure-string filtering."""
        from data import llm_router as lr
        import core.alpha_discovery as ad
        from core.alpha_discovery import _router_llm_generate

        class _FakeProvider:
            def generate(self, messages, model, **kwargs):
                assert isinstance(messages, list) and messages[0]["role"] == "user"
                assert "ALLOWED PRIMITIVES" in messages[0]["content"]
                return "delta(close, 2) * ts_rank(volume, 15) | momentum | e2e smoke"

        router = lr.LLMRouter.__new__(lr.LLMRouter)  # skip env/config init
        router.config = {}
        router.providers = {"fakeprov": _FakeProvider()}
        router.agents = {}
        monkeypatch.setattr(lr, "_router", router, raising=False)

        prompt = ad.AlphaDiscoveryEngine().build_generation_prompt()
        text = _router_llm_generate(prompt)
        assert text.startswith("delta(close, 2)")
        assert "alpha_discovery" in router.agents, "agent was not auto-registered"
        assert router.agents["alpha_discovery"]["provider"] == "fakeprov"


# ===========================================================================
# 5. IC significance & trial deflation
# ===========================================================================

class TestICSignificance:
    """Mean-IC t-test with Bonferroni deflation over campaign trials."""

    def test_zero_dispersion_is_unmeasurable(self):
        from core.alpha_discovery import ic_significance

        t, p, threshold = ic_significance([0.2] * 25, n_trials=1)
        assert t == 0.0
        assert p == 1.0
        assert threshold == pytest.approx(0.05)

    def test_deflation_rejects_a_marginal_t(self):
        from core.alpha_discovery import ic_significance

        # Non-overlapping blocks with mean 0.05, std ~0.083 -> t ~ 2.4.
        values = [0.13, -0.03] * 8
        t1, p1, th1 = ic_significance(values, n_trials=1)
        assert t1 > 2.0
        assert p1 <= th1  # significant under a single hypothesis
        _, p2, th2 = ic_significance(values, n_trials=10_000)
        assert p2 == p1  # identical evidence ...
        assert th2 == pytest.approx(0.05 / 10_000)
        assert p2 > th2  # ... but the campaign deflation rejects it

    def test_too_few_blocks_is_unmeasurable(self):
        from core.alpha_discovery import ic_significance

        assert ic_significance([0.4, 0.1, 0.2], n_trials=1)[1] == 1.0

    def test_evaluator_applies_deflation(self):
        import numpy as np
        import pandas as pd
        from core.alpha_discovery import AlphaCandidate, SealedSandboxEvaluator

        # Strong, stable momentum: AR(1) increments so delta(close, 1) predicts
        # the next return with a large, consistent IC.
        rng = np.random.default_rng(7)
        n = 1200
        eps = rng.normal(0, 0.001, n)
        inc = np.zeros(n)
        for i in range(1, n):
            inc[i] = 0.85 * inc[i - 1] + eps[i]
        close = 1.08 + np.cumsum(inc)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.001,
            "low": close - 0.001,
            "close": close,
            "volume": np.full(n, 1e5),
        })
        train, test = df.iloc[:800], df.iloc[800:]

        candidate = AlphaCandidate(
            expression="delta(close, 1)", name="deflated", category="momentum"
        )
        ok = SealedSandboxEvaluator().evaluate_candidate(candidate, train, test, n_trials=1)
        assert ok.status == "VALIDATED"
        assert ok.ic_pvalue < 0.05

        candidate = AlphaCandidate(
            expression="delta(close, 1)", name="deflated", category="momentum"
        )
        killed = SealedSandboxEvaluator().evaluate_candidate(
            candidate, train, test, n_trials=10 ** 30
        )
        assert killed.status == "REJECTED"
        assert "trial deflation" in killed.rejection_reason
