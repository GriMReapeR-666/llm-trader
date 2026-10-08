"""Simple test for Alpha Mining Module.

Verifies basic functionality of the alpha mining pipeline.
"""
import json
import sys
import os

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

import numpy as np
import pandas as pd
from research.alpha_miner import (
    AlphaExpression,
    ExpressionGenerator,
    ICEvaluator,
    GeneticOperators,
    PCAProjector,
    GridSelector,
    GAEngine,
    GAConfig,
)


def generate_test_data(n_bars: int = 500) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    
    # Price process
    close = np.cumsum(np.random.randn(n_bars) * 0.001) + 1.1000
    high = close * (1 + np.abs(np.random.randn(n_bars) * 0.0005))
    low = close * (1 - np.abs(np.random.randn(n_bars) * 0.0005))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.randint(1000, 10000, n_bars).astype(float)
    
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_normalizers_are_causal():
    """Regression: `cs_*` normalizers must not read future bars.

    They were full-sample (`rank(pct=True)` / `.mean()` / `.std()` over the whole
    series), so appending bars silently changed the value at earlier bars. That
    look-ahead is what let the GA register alphas graded "strong" at IC -0.127
    that no live bar could reproduce.

    The oracle: evaluate on N bars, evaluate again on N+future bars, and require
    the first N values to be bit-identical. Appending the future must change
    nothing in the past.
    """
    from research.alpha_miner.expression_tree import create_leaf, create_unary

    rng = np.random.default_rng(7)
    n, cut, m = 260, 200, 60
    idx = pd.RangeIndex(n)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    ohlcv = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": pd.Series(rng.lognormal(10, 0.3, n), index=idx),
    }, index=idx)

    # A genuinely uncertain future path - a constant tail hides the bug.
    fut_close = pd.Series(close.iloc[-1] + np.cumsum(rng.normal(0, 1, m)),
                          index=pd.RangeIndex(n, n + m))
    fut_vol = pd.Series(rng.lognormal(10, 0.3, m), index=pd.RangeIndex(n, n + m))
    extended = pd.concat([ohlcv, pd.DataFrame({
        "open": fut_close, "high": fut_close * 1.01, "low": fut_close * 0.99,
        "close": fut_close, "volume": fut_vol,
    })], axis=0)

    leaf = create_leaf(field="close")
    for op in ("cs_rank", "cs_scale", "cs_normalize"):
        for window in (5, 20, 100):
            expr = create_unary(op, leaf, window=window)
            before = expr.evaluate(ohlcv).iloc[:cut].to_numpy()
            after = expr.evaluate(extended).iloc[:cut].to_numpy()
            assert np.allclose(before, after, equal_nan=True), (
                f"{op}(window={window}) changed past values when future bars were "
                f"appended -> look-ahead bias (max divergence "
                f"{np.nanmax(np.abs(before - after)):.6g})"
            )


def test_causality_guard_rejects_future_constructs():
    """Both stacks share one rule set; causality is a hard reject, not a penalty."""
    from core.alpha_causality import find_lookahead_violations, is_causal
    from research.alpha_miner.alpha_judge import judge_candidate

    # Causal: `cs_rank`/`ts_rank` are rolling and must NOT trip the guard, which
    # is why the rules rely on a \b boundary (in `_` is a word character).
    for expr in ("ts_rank(close, 20)", "cs_rank(close, 20)", "cs_normalize(volume, 20)",
                 "divide(subtract(close, ts_mean(close, 20)), ts_std(close, 20))"):
        assert is_causal(expr), f"false positive on causal expression: {expr}"

    # Look-ahead, including negative periods passed as any argument.
    for expr in ("rank(close)", "zscore(close)", "normalize(close)",
                 "shift(close, -3)", "ts_delay(close, -2)", "close.shift(-1)",
                 "ts_rank(close.shift(-1), 20)"):
        violations = find_lookahead_violations(expr)
        assert violations, f"missed look-ahead: {expr}"
        assert not judge_candidate(expr).accepted, f"judge accepted look-ahead: {expr}"

    # The GA stack used to pass `rank`/`cs_normalize` at a constant 8.0/10.0.
    assert judge_candidate("rank(close)").lookahead == 0.0


def test_alpha_strategy_serialises_unmeasured_metrics():
    """Regression: `ic_ir=None` crashed `to_dict()` and silently froze the registry.

    `_save_alpha_strategies` swallowed the TypeError into a `logger.warning`, so
    `strategy_registry.json` stayed frozen at its pre-fix contents while every
    call site reported success.
    """
    from research.alpha_miner.strategy_bridge import AlphaStrategy

    strategy = AlphaStrategy(
        strategy_id="alpha_test", name="T", expression="ts_rank(close, 20)",
        ic_mean=-0.0312, ic_ir=None, ic_quality="good",
        shap_values={}, created_at=1.0,
    )
    payload = strategy.to_dict()  # must not raise
    assert payload["ic_ir"] is None
    assert payload["ic_mean"] == -0.0312
    assert json.loads(json.dumps(payload))["ic_ir"] is None  # JSON round-trip


def test_expression_tree():
    """Test expression tree creation and evaluation."""
    print("Testing expression tree...")
    
    # Create a simple expression: ts_mean(close, 20)
    close_leaf = AlphaExpression(operator="leaf", field="close")
    expr = AlphaExpression(operator="ts_mean", children=[close_leaf], window=20)
    
    # Generate test data
    ohlcv = generate_test_data()
    
    # Evaluate
    result = expr.evaluate(ohlcv)
    
    assert result is not None
    assert len(result) == len(ohlcv)
    assert not result.isna().all()
    
    print(f"  Expression: {expr.to_string()}")
    print(f"  Result shape: {result.shape}")
    print(f"  Mean value: {result.mean():.4f}")
    print("  [PASS] Passed")


def test_expression_generator():
    """Test expression generation."""
    print("\nTesting expression generator...")
    
    generator = ExpressionGenerator(seed=42)
    
    # Generate single expression
    expr = generator.generate_depth_1()
    assert expr is not None
    print(f"  Generated expression: {expr.to_string()}")
    
    # Generate population
    population = generator.generate_population(size=100, depth=1)
    assert len(population) == 100
    print(f"  Generated population of {len(population)} expressions")
    
    # Check diversity
    from research.alpha_miner.expression_generator import compute_population_diversity
    diversity = compute_population_diversity(population)
    print(f"  Population diversity: {diversity:.2f}")
    
    print("  [PASS] Passed")


def test_ic_evaluator():
    """Test IC calculation."""
    print("\nTesting IC evaluator...")
    
    evaluator = ICEvaluator(min_observations=20)
    
    # Generate test data
    ohlcv = generate_test_data()
    
    # Create simple expression
    close_leaf = AlphaExpression(operator="leaf", field="close")
    expr = AlphaExpression(operator="ts_returns", children=[close_leaf], window=5)
    
    # Evaluate expression
    alpha_values = expr.evaluate(ohlcv)
    
    # Calculate forward returns
    from research.alpha_miner.ic_evaluator import calculate_forward_returns_5day
    forward_returns = calculate_forward_returns_5day(ohlcv["close"])
    
    # Calculate IC
    ic = evaluator.calculate_ic(alpha_values, forward_returns)
    
    print(f"  IC: {ic:.4f}")
    
    # Full evaluation
    result = evaluator.evaluate_expression(expr, ohlcv, forward_returns)
    print(f"  IC Mean: {result.ic_mean:.4f}")
    print(f"  IC Std: {result.ic_std:.4f}")
    print(f"  IC IR: {result.ic_ir:.4f}")
    print(f"  Quality: {result.ic_quality}")
    
    print("  [PASS] Passed")


def test_pca_projector():
    """Test PCA projection."""
    print("\nTesting PCA projector...")
    
    projector = PCAProjector(n_components=2)
    
    # Generate test data
    ohlcv = generate_test_data()
    
    # Generate expressions
    generator = ExpressionGenerator(seed=42)
    expressions = generator.generate_population(size=50, depth=1)
    
    # Compute behavior matrix
    from research.alpha_miner.ic_evaluator import calculate_forward_returns_5day
    forward_returns = calculate_forward_returns_5day(ohlcv["close"])
    
    behavior_matrix = projector.compute_behavior_matrix(expressions, ohlcv, forward_returns)
    print(f"  Behavior matrix shape: {behavior_matrix.shape}")
    
    if not behavior_matrix.empty and len(behavior_matrix) >= 20:
        # Fit PCA
        pca_result = projector.fit_transform(behavior_matrix)
        print(f"  PCA coordinates shape: {pca_result.behavior_coords.shape}")
        print(f"  Explained variance ratio: {pca_result.explained_variance_ratio}")
        
        # Get behavior grid
        grid = projector.get_behavior_grid(pca_result.behavior_coords, grid_size=5)
        print(f"  Grid cells occupied: {len(grid)}")
    
    print("  [PASS] Passed")


def test_pca_projector_sanitizes_inf():
    """+/-inf from an expression must not abort PCA/quality-diversity selection."""
    print("\nTesting PCA projector inf sanitization...")

    import research.alpha_miner.pca_projector as pca_mod
    from research.alpha_miner.ic_evaluator import calculate_forward_returns_5day

    ohlcv = generate_test_data()
    forward_returns = calculate_forward_returns_5day(ohlcv["close"])
    expressions = ExpressionGenerator(seed=7).generate_population(size=30, depth=1)

    real_eval = pca_mod.evaluate_with_cache

    def poisoned(expr, frame):
        values = np.asarray(real_eval(expr, frame), dtype=float)
        values[3:6] = np.inf
        values[9:11] = -np.inf
        return values

    pca_mod.evaluate_with_cache = poisoned
    try:
        behavior_matrix = PCAProjector(n_components=2).compute_behavior_matrix(
            expressions, ohlcv, forward_returns)
    finally:
        pca_mod.evaluate_with_cache = real_eval

    values = behavior_matrix.to_numpy(dtype=float)
    assert not np.isinf(values).any(), "inf survived compute_behavior_matrix"
    assert not np.isnan(values).any(), "NaN survived compute_behavior_matrix"

    pca_result = PCAProjector(n_components=2).fit_transform(behavior_matrix)
    assert np.isfinite(pca_result.behavior_coords).all(), "non-finite PCA coordinates"

    print("  [PASS] Passed")


def test_ga_engine():
    """Test GA evolution (small scale)."""
    print("\nTesting GA engine (small scale)...")
    
    # Configure for fast test
    config = GAConfig(
        population_size=50,
        generations=2,
        max_depth=2,
        grid_size=5,
        parent_pool_size=20,
    )
    
    engine = GAEngine(config=config, seed=42)
    
    # Generate test data
    ohlcv = generate_test_data(n_bars=200)
    
    # Run evolution
    result = engine.evolve(ohlcv, verbose=False)
    
    print(f"  Best expressions: {len(result.best_expressions)}")
    print(f"  Best IC: {result.best_ics[0] if result.best_ics else 0:.4f}")
    print(f"  Evolution steps: {len(result.evolution_history)}")
    
    # Check if we got any significant alphas
    significant = engine.get_significant_alphas(result, min_ic=0.01)
    print(f"  Significant alphas (|IC|>0.01): {len(significant)}")
    
    print("  [PASS] Passed")


def test_ga_rejects_thin_ic_evidence():
    """Alphas scored on too few IC windows must not win selection."""
    print("\nTesting GA minimum IC-observation guard...")

    from research.alpha_miner.expression_tree import AlphaExpression
    from research.alpha_miner.ic_evaluator import calculate_forward_returns_5day

    ohlcv = generate_test_data(n_bars=300)
    forward_returns = calculate_forward_returns_5day(ohlcv["close"])

    min_observations = 200
    config = GAConfig(population_size=10, generations=1,
                      min_ic_observations=min_observations)
    engine = GAEngine(config=config, seed=1)

    # ts_rank over a constant produces a degenerate alpha whose IC rests on a
    # handful of windows; it must be scored as worthless, not as a high IC.
    degenerate = AlphaExpression(operator="ts_rank", window=15, constant=5.0)
    results = engine._evaluate_population([degenerate], ohlcv, forward_returns)
    result = results[degenerate.to_string()]

    assert result.n_observations < min_observations, (
        "fixture is no longer thin-evidence: n=%s" % result.n_observations)
    assert result.ic_mean == 0.0, (
        "thin-evidence alpha kept ic_mean=%s" % result.ic_mean)

    print("  [PASS] Passed")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("ALPHA MINING MODULE TESTS")
    print("=" * 60)
    
    try:
        test_expression_tree()
        test_expression_generator()
        test_ic_evaluator()
        test_pca_projector()
        test_ga_engine()
        test_optimal_strategy()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED [OK]")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


def test_optimal_strategy():
    """Test optimal strategy statistics (Firoozye & Koshiyama)."""
    print("\nTesting optimal strategy statistics...")
    
    from research.alpha_miner.optimal_strategy import (
        OptimalStrategyEvaluator,
        TLSOptimizer,
        compute_strategy_quality,
        compute_optimal_ic_target,
    )
    
    evaluator = OptimalStrategyEvaluator()
    
    # Generate synthetic strategy returns with known IC
    np.random.seed(42)
    n = 1000
    ic_true = 0.15
    
    # Generate signal and returns with specified correlation
    signal = np.random.randn(n)
    returns = ic_true * signal + np.sqrt(1 - ic_true**2) * np.random.randn(n)
    strategy_returns = signal * returns
    
    # Compute statistics
    stats = evaluator.compute_statistics(strategy_returns, signal)
    
    print(f"  True IC: {ic_true:.4f}")
    print(f"  Estimated IC: {stats.ic:.4f}")
    print(f"  Sharpe ratio: {stats.sharpe_ratio:.4f}")
    print(f"  Skewness: {stats.skewness:.4f}")
    print(f"  Kurtosis: {stats.kurtosis:.4f}")
    print(f"  Jarque-Bera stat: {stats.jarque_bera_stat:.2f}")
    
    # Test IC target computation
    target_sharpe = 0.5
    ic_required = compute_optimal_ic_target(target_sharpe)
    print(f"  IC required for SR={target_sharpe:.2f}: {ic_required:.4f}")
    
    # Test TLS optimizer
    tls = TLSOptimizer()
    X = np.random.randn(n, 3)
    y = 0.5 * X[:, 0] + 0.3 * X[:, 1] + np.random.randn(n) * 0.5
    tls_result = tls.fit(X, y)
    print(f"  TLS R^2: {tls_result['r_squared']:.4f}")
    print(f"  TLS correlation: {tls_result['correlation']:.4f}")
    
    # Test convenience function
    quality = compute_strategy_quality(strategy_returns, signal)
    print(f"  compute_strategy_quality: {quality['sharpe_ratio']:.4f}")
    
    print("  [PASS] Passed")


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
