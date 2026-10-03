"""Alpha Ensemble Parameter Stability Test.

Runs the full parameter stability framework on the alpha ensemble to identify
which alphas are robust (stable IC across parameter variations) vs fragile
(IC collapses when parameters change slightly).

Parameters varied:
  1. IC window: [30, 45, 60, 90, 120]
  2. Normalize method: [zscore, rank]
  3. Forward return periods: [1, 2, 4, 8]
  4. Weighting: [ic_ir, ic, equal]
  5. Shrinkage: [0.0, 0.15, 0.3, 0.45]

Outputs:
  - Per-alpha stability grade (A-F)
  - Heatmap of ensemble Sharpe across IC_window × forward_return
  - List of fragile alphas (grade D/F) that should be demoted or removed
  - List of robust alphas (grade A/B) that should be kept regardless of regime
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

# Project root on path
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from core.alpha_factors import (
    alpha_001, alpha_005, alpha_009, alpha_012, alpha_014,
    alpha_017, alpha_019, alpha_022, alpha_023, alpha_025,
    alpha_028, alpha_030, alpha_034, alpha_035, alpha_036,
    alpha_037, alpha_038, alpha_041, alpha_043, alpha_053,
    alpha_055, alpha_060, alpha_101,
)
from core.alpha_ensemble import (
    combine_alphas, compute_ic_weights, zscore_normalize, rank_normalize,
    ALPHA_CATEGORIES,
)
from core.parameter_stability import (
    ParameterGrid, run_parameter_stability, compute_stability_score,
    generate_param_grid, evaluate_variant, ParameterVariant,
)

# ---------------------------------------------------------------------------
# Alpha registry
# ---------------------------------------------------------------------------

ALPHA_FUNCS = {
    "alpha_001": alpha_001, "alpha_005": alpha_005, "alpha_009": alpha_009,
    "alpha_012": alpha_012, "alpha_014": alpha_014, "alpha_017": alpha_017,
    "alpha_019": alpha_019, "alpha_022": alpha_022, "alpha_023": alpha_023,
    "alpha_025": alpha_025, "alpha_028": alpha_028, "alpha_030": alpha_030,
    "alpha_034": alpha_034, "alpha_035": alpha_035, "alpha_036": alpha_036,
    "alpha_037": alpha_037, "alpha_038": alpha_038, "alpha_041": alpha_041,
    "alpha_043": alpha_043, "alpha_053": alpha_053, "alpha_055": alpha_055,
    "alpha_060": alpha_060, "alpha_101": alpha_101,
}

ALPHA_COLUMNS = list(ALPHA_FUNCS.keys())


# ---------------------------------------------------------------------------
# Synthetic data generator (realistic M15 FX)
# ---------------------------------------------------------------------------

def generate_synthetic_m15(n_bars: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Generate realistic synthetic M15 OHLCV data with regime switches."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n_bars)
    close[0] = 1.1000  # EURUSD-like starting price

    # Regime-switching: trending + mean-reverting phases
    regimes = []
    pos = 0
    while pos < n_bars:
        regime_len = rng.randint(100, 500)
        regime = rng.choice(["trend_up", "trend_down", "range", "volatile"])
        regimes.extend([(regime, i) for i in range(regime_len)])
        pos += regime_len
    regimes = regimes[:n_bars]

    for i in range(1, n_bars):
        regime = regimes[i][0]
        if regime == "trend_up":
            drift = 0.0001
            vol = 0.003
        elif regime == "trend_down":
            drift = -0.0001
            vol = 0.003
        elif regime == "range":
            drift = -0.00005 * (close[i-1] - 1.1000)
            vol = 0.002
        else:  # volatile
            drift = 0.0
            vol = 0.006

        close[i] = close[i-1] * (1 + drift + vol * rng.randn())

    # Build OHLCV
    high = close * (1 + np.abs(rng.randn(n_bars)) * 0.001)
    low = close * (1 - np.abs(rng.randn(n_bars)) * 0.001)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.lognormal(10, 0.5, n_bars)

    df = pd.DataFrame({
        "open": open_,
        "high": np.maximum(high, np.maximum(open_, close)),
        "low": np.minimum(low, np.minimum(open_, close)),
        "close": close,
        "volume": volume,
    })
    return df


# ---------------------------------------------------------------------------
# Compute all alphas
# ---------------------------------------------------------------------------

def compute_all_alphas(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all 23 alpha factors on the given OHLCV data."""
    result = df.copy()
    for name, func in ALPHA_FUNCS.items():
        try:
            result[name] = func(df)
        except Exception as e:
            print(f"  [WARN] {name} failed: {e}")
            result[name] = 0.0
    return result


# ---------------------------------------------------------------------------
# Ensemble Sharpe strategy function (for parameter_stability framework)
# ---------------------------------------------------------------------------

def make_ensemble_strategy_fn(
    alpha_df: pd.DataFrame,
    alpha_columns: List[str],
):
    """Create a strategy function that evaluates ensemble Sharpe for given params."""
    def strategy_fn(params: Dict[str, Any]) -> Dict[str, Any]:
        ic_window = int(params.get("ic_window", 60))
        fwd_periods = int(params.get("fwd_periods", 1))
        normalize = params.get("normalize", "zscore")
        weighting = params.get("weighting", "ic_ir")
        shrinkage = float(params.get("shrinkage", 0.3))

        try:
            result = combine_alphas(
                alpha_df=alpha_df,
                alpha_columns=alpha_columns,
                forward_return_periods=fwd_periods,
                ic_window=ic_window,
                normalize_method=normalize,
                weighting=weighting,
            )
        except Exception:
            return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.5,
                    "win_rate": 0.5, "profit_factor": 1.0, "n_trades": 0,
                    "expectancy_r": 0.0, "calmar": 0.0}

        # Convert composite signal to simple returns
        composite = result.composite.values
        close = alpha_df["close"].values
        n = len(close)

        # Signal: >1 = long, <-1 = short, else flat
        signals = np.zeros(n)
        signals[composite > 1.0] = 1.0
        signals[composite < -1.0] = -1.0

        # Forward returns (1-bar)
        fwd_ret = np.zeros(n)
        fwd_ret[:-1] = (close[1:] - close[:-1]) / close[:-1]

        # Strategy returns (signal at bar t, return at bar t+1)
        strat_ret = signals[:-1] * fwd_ret[:-1]
        strat_ret = strat_ret[np.isfinite(strat_ret)]

        if len(strat_ret) < 50:
            return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.5,
                    "win_rate": 0.5, "profit_factor": 1.0, "n_trades": 0,
                    "expectancy_r": 0.0, "calmar": 0.0}

        # Compute metrics
        mean_r = float(np.mean(strat_ret))
        std_r = float(np.std(strat_ret))
        sharpe = mean_r / max(std_r, 1e-10) * np.sqrt(252 * 96)  # annualize M15

        # Sortino
        downside = strat_ret[strat_ret < 0]
        downside_std = float(np.std(downside)) if len(downside) > 1 else 1e-10
        sortino = mean_r / max(downside_std, 1e-10) * np.sqrt(252 * 96)

        # Max drawdown
        equity = np.cumsum(strat_ret)
        running_max = np.maximum.accumulate(equity)
        drawdown = running_max - equity
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        # Win rate
        wins = strat_ret[strat_ret > 0]
        losses = strat_ret[strat_ret < 0]
        win_rate = len(wins) / max(len(strat_ret), 1)

        # Profit factor
        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-10
        profit_factor = gross_profit / gross_loss

        # Expectancy (R-multiple proxy)
        expectancy_r = mean_r / max(std_r, 1e-10) if std_r > 0 else 0.0

        # Calmar
        calmar = sharpe / max(max_dd, 1e-10) if max_dd > 0 else sharpe

        # Number of trades (signal changes)
        n_trades = int(np.sum(np.abs(np.diff(signals)) > 0))

        return {
            "sharpe": float(sharpe),
            "sortino": float(sortino),
            "max_drawdown": float(max_dd),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "n_trades": n_trades,
            "expectancy_r": float(expectancy_r),
            "calmar": float(calmar),
        }

    return strategy_fn


# ---------------------------------------------------------------------------
# Per-alpha IC stability analysis
# ---------------------------------------------------------------------------

def _vectorized_rolling_spearman(alpha: pd.Series, fwd_ret: pd.Series, window: int) -> pd.Series:
    """Vectorized rolling Spearman using rank transform + rolling Pearson."""
    # Rank-transform both series (Spearman = Pearson of ranks)
    a_rank = alpha.rank(pct=True)
    r_rank = fwd_ret.rank(pct=True)
    # Rolling Pearson of ranks = rolling Spearman
    a_dm = a_rank - a_rank.rolling(window, min_periods=10).mean()
    r_dm = r_rank - r_rank.rolling(window, min_periods=10).mean()
    cov = (a_dm * r_dm).rolling(window, min_periods=10).mean()
    std_a = a_dm.rolling(window, min_periods=10).std()
    std_r = r_dm.rolling(window, min_periods=10).std()
    den = std_a * std_r
    den = den.replace(0, np.nan)
    return (cov / den).fillna(np.nan)


def analyze_per_alpha_stability(
    alpha_df: pd.DataFrame,
    alpha_columns: List[str],
    ic_windows: List[int] = [30, 60, 120],
    fwd_periods_list: List[int] = [1, 2, 4],
) -> Dict[str, Dict[str, Any]]:
    """Analyze IC stability for each alpha across parameter variations.

    For each alpha, compute IC at different IC windows and forward return periods.
    Report which alphas have stable IC (robust) vs volatile IC (fragile).
    """
    results = {}

    for alpha_name in alpha_columns:
        if alpha_name not in alpha_df.columns:
            continue

        alpha_series = alpha_df[alpha_name]
        # Z-score normalize the alpha
        alpha_norm = zscore_normalize(alpha_series)

        ic_values = {}  # (ic_window, fwd_period) -> ic_mean

        for ic_w in ic_windows:
            for fwd_p in fwd_periods_list:
                fwd_ret = alpha_df["close"].pct_change(fwd_p).shift(-fwd_p)
                ic_series = _vectorized_rolling_spearman(alpha_norm, fwd_ret, ic_w)
                ic_valid = ic_series.dropna().values
                if len(ic_valid) < 10:
                    ic_values[(ic_w, fwd_p)] = {
                        "ic_mean": 0.0, "ic_std": 1.0, "ic_ir": 0.0,
                        "ic_positive_pct": 0.0,
                    }
                    continue
                ic_mean = float(np.mean(ic_valid))
                ic_std = float(np.std(ic_valid))
                ic_values[(ic_w, fwd_p)] = {
                    "ic_mean": ic_mean,
                    "ic_std": ic_std,
                    "ic_ir": ic_mean / max(ic_std, 1e-8),
                    "ic_positive_pct": float(np.mean(ic_valid > 0)),
                }

        # Compute stability metrics
        ic_means = [v["ic_mean"] for v in ic_values.values()]
        ic_ir_values = [v["ic_ir"] for v in ic_values.values()]

        ic_means_arr = np.array(ic_means)
        ic_ir_arr = np.array(ic_ir_values)

        mean_ic = float(np.mean(ic_means_arr))
        std_ic = float(np.std(ic_means_arr))
        cv_ic = std_ic / max(abs(mean_ic), 1e-8)

        # Fraction of parameter combos with positive IC
        frac_positive = float(np.mean(ic_means_arr > 0))

        # Fraction with |IC| > 0.02 (meaningful signal)
        frac_meaningful = float(np.mean(np.abs(ic_means_arr) > 0.02))

        # Stability score: low CV + high frac_positive = robust
        stability_score = (1.0 - min(cv_ic, 2.0) / 2.0) * 0.5 + frac_positive * 0.5

        # Grade
        if stability_score >= 0.7 and frac_positive >= 0.7:
            grade = "A"
        elif stability_score >= 0.55 and frac_positive >= 0.5:
            grade = "B"
        elif stability_score >= 0.4 and frac_positive >= 0.4:
            grade = "C"
        elif stability_score >= 0.25 or frac_positive >= 0.25:
            grade = "D"
        else:
            grade = "F"

        category = ALPHA_CATEGORIES.get(alpha_name, "unknown")

        results[alpha_name] = {
            "mean_ic": mean_ic,
            "std_ic": std_ic,
            "cv_ic": cv_ic,
            "frac_positive_ic": frac_positive,
            "frac_meaningful_ic": frac_meaningful,
            "stability_score": stability_score,
            "grade": grade,
            "category": category,
            "ic_ir_median": float(np.median(ic_ir_arr)),
            "ic_ir_range": [float(np.min(ic_ir_arr)), float(np.max(ic_ir_arr))],
            "best_params": max(ic_values.items(), key=lambda x: abs(x[1]["ic_ir"])),
            "worst_params": min(ic_values.items(), key=lambda x: abs(x[1]["ic_ir"])),
        }

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("ALPHA ENSEMBLE PARAMETER STABILITY TEST")
    print("=" * 80)

    # 1. Generate synthetic data
    print("\n[1/4] Generating synthetic M15 data (2000 bars)...")
    df = generate_synthetic_m15(n_bars=800, seed=42)
    print(f"  Generated {len(df)} bars, price range: {df['close'].min():.5f} - {df['close'].max():.5f}")

    # 2. Compute all alphas
    print("\n[2/4] Computing 23 alpha factors...")
    t0 = time.time()
    alpha_df = compute_all_alphas(df)
    print(f"  Computed in {time.time()-t0:.1f}s")

    # Count valid alphas
    valid_alphas = []
    for col in ALPHA_COLUMNS:
        if col in alpha_df.columns:
            valid_vals = alpha_df[col].dropna()
            if len(valid_vals) > 100 and valid_vals.std() > 1e-10:
                valid_alphas.append(col)
    print(f"  Valid alphas with signal: {len(valid_alphas)}/{len(ALPHA_COLUMNS)}")

    # 3. Per-alpha IC stability analysis
    print("\n[3/4] Running per-alpha IC stability analysis...")
    t0 = time.time()
    alpha_stability = analyze_per_alpha_stability(
        alpha_df, valid_alphas,
        ic_windows=[30, 60, 120],
        fwd_periods_list=[1, 2, 4],
    )
    print(f"  Analyzed in {time.time()-t0:.1f}s")

    # 4. Ensemble parameter stability
    print("\n[4/4] Running ensemble parameter stability...")
    t0 = time.time()
    grids = [
        ParameterGrid(name="ic_window", values=[30, 60, 120]),
        ParameterGrid(name="fwd_periods", values=[1, 2, 4]),
        ParameterGrid(name="normalize", values=["zscore", "rank"]),
        ParameterGrid(name="weighting", values=["ic_ir", "ic", "equal"]),
        ParameterGrid(name="shrinkage", values=[0.0, 0.3]),
    ]

    strategy_fn = make_ensemble_strategy_fn(alpha_df, valid_alphas)

    ensemble_result = run_parameter_stability(
        grids=grids,
        strategy_fn=strategy_fn,
        max_variants=50,
        sharpe_threshold=0.5,
        max_dd_limit=0.30,
        use_random=True,
        random_samples=30,
        seed=42,
    )
    print(f"  Evaluated {ensemble_result.stability.n_variants} variants in {time.time()-t0:.1f}s")

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    # Per-alpha stability
    print("\n--- PER-ALPHA IC STABILITY ---")
    print(f"{'Alpha':<12} {'Grade':>5} {'Mean IC':>8} {'Std IC':>8} {'CV':>6} {'IC+':>6} {'Meaningful':>10} {'Category':<15}")
    print("-" * 90)

    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for name in sorted(alpha_stability.keys(), key=lambda x: -alpha_stability[x]["stability_score"]):
        s = alpha_stability[name]
        grade_counts[s["grade"]] = grade_counts.get(s["grade"], 0) + 1
        print(
            f"{name:<12} {s['grade']:>5} {s['mean_ic']:>8.4f} {s['std_ic']:>8.4f} "
            f"{s['cv_ic']:>6.2f} {s['frac_positive_ic']:>5.0%} "
            f"{s['frac_meaningful_ic']:>10.0%} {s['category']:<15}"
        )

    print(f"\nGrade distribution: A={grade_counts.get('A',0)} B={grade_counts.get('B',0)} "
          f"C={grade_counts.get('C',0)} D={grade_counts.get('D',0)} F={grade_counts.get('F',0)}")

    # Fragile vs robust
    fragile = [n for n, s in alpha_stability.items() if s["grade"] in ("D", "F")]
    robust = [n for n, s in alpha_stability.items() if s["grade"] in ("A", "B")]
    marginal = [n for n, s in alpha_stability.items() if s["grade"] == "C"]

    print(f"\n--- ROBUST ALPHAS (grade A/B, keep regardless of regime) ---")
    for name in robust:
        s = alpha_stability[name]
        print(f"  {name}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, IC+={s['frac_positive_ic']:.0%}, cat={s['category']}")

    print(f"\n--- MARGINAL ALPHAS (grade C, use with caution) ---")
    for name in marginal:
        s = alpha_stability[name]
        print(f"  {name}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, IC+={s['frac_positive_ic']:.0%}, cat={s['category']}")

    print(f"\n--- FRAGILE ALPHAS (grade D/F, candidates for removal) ---")
    for name in fragile:
        s = alpha_stability[name]
        print(f"  {name}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, CV={s['cv_ic']:.2f}, IC+={s['frac_positive_ic']:.0%}, cat={s['category']}")

    # Ensemble stability
    print(f"\n--- ENSEMBLE PARAMETER STABILITY ---")
    print(ensemble_result.summary)
    print(f"  Best variant:  Sharpe={ensemble_result.optimal.sharpe:.2f} params={ensemble_result.optimal.params}")
    print(f"  Worst variant: Sharpe={ensemble_result.worst.sharpe:.2f} params={ensemble_result.worst.params}")
    print(f"  Cliff score:   {ensemble_result.stability.cliff_score:.2f}")
    print(f"  Boundary dist: {ensemble_result.stability.boundary_distance:.2f}")

    # Sensitivity
    if ensemble_result.stability.sensitivity:
        print(f"\n  Parameter sensitivity (|correlation with Sharpe|):")
        for pname, sens in sorted(ensemble_result.stability.sensitivity.items(), key=lambda x: -x[1]):
            bar = "█" * int(sens * 40)
            print(f"    {pname:<14} {sens:.3f} {bar}")

    # Heatmap
    if ensemble_result.heatmap:
        hm = ensemble_result.heatmap
        print(f"\n  Heatmap: {hm['x_param']} × {hm['y_param']} (Sharpe)")
        print(f"    {hm['x_param']} values: {hm['x_values']}")
        for i, yv in enumerate(hm['y_values']):
            row_str = " ".join(f"{v:>6.2f}" for v in hm['z_matrix'][i])
            print(f"    {hm['y_param']}={yv:>4.0f}: {row_str}")

    # Save results
    output = {
        "per_alpha_stability": {
            name: {
                "grade": s["grade"],
                "mean_ic": s["mean_ic"],
                "std_ic": s["std_ic"],
                "cv_ic": s["cv_ic"],
                "frac_positive_ic": s["frac_positive_ic"],
                "stability_score": s["stability_score"],
                "category": s["category"],
            }
            for name, s in alpha_stability.items()
        },
        "ensemble_stability": {
            "grade": ensemble_result.stability.grade,
            "n_variants": ensemble_result.stability.n_variants,
            "fraction_profitable": ensemble_result.stability.fraction_profitable,
            "sharpe_median": ensemble_result.stability.sharpe_median,
            "sharpe_std": ensemble_result.stability.sharpe_std,
            "cliff_score": ensemble_result.stability.cliff_score,
            "sensitivity": ensemble_result.stability.sensitivity,
        },
        "robust_alphas": robust,
        "fragile_alphas": fragile,
        "marginal_alphas": marginal,
    }

    out_path = Path("artifacts") / "alpha_stability_report.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved report to {out_path}")

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
