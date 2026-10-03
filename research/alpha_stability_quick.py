"""Quick alpha stability diagnostic — per-alpha IC stability only.

Runs fast vectorized IC across parameter variations to classify alphas
as robust (A/B) vs fragile (D/F). Skips the slow ensemble combo loop.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from typing import Dict, List, Any
import numpy as np
import pandas as pd

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
from core.alpha_ensemble import zscore_normalize, ALPHA_CATEGORIES

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


def generate_synthetic_m15(n_bars: int = 3000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = np.zeros(n_bars)
    close[0] = 1.1000
    regimes = []
    pos = 0
    while pos < n_bars:
        regime_len = rng.randint(100, 400)
        regime = rng.choice(["trend_up", "trend_down", "range", "volatile"])
        regimes.extend([(regime, i) for i in range(regime_len)])
        pos += regime_len
    regimes = regimes[:n_bars]
    for i in range(1, n_bars):
        r = regimes[i][0]
        if r == "trend_up":    drift, vol = 0.0001, 0.003
        elif r == "trend_down": drift, vol = -0.0001, 0.003
        elif r == "range":      drift = -0.00005*(close[i-1]-1.1000); vol=0.002
        else:                   drift, vol = 0.0, 0.006
        close[i] = close[i-1] * (1 + drift + vol * rng.randn())
    high = close * (1 + np.abs(rng.randn(n_bars))*0.001)
    low  = close * (1 - np.abs(rng.randn(n_bars))*0.001)
    open_= np.roll(close, 1); open_[0] = close[0]
    vol  = rng.lognormal(10, 0.5, n_bars)
    return pd.DataFrame({"open": open_, "high": np.maximum(high, np.maximum(open_, close)),
                          "low": np.minimum(low, np.minimum(open_, close)),
                          "close": close, "volume": vol})


def compute_all_alphas(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for name, func in ALPHA_FUNCS.items():
        try:    result[name] = func(df)
        except: result[name] = 0.0
    return result


def vectorized_rolling_spearman(alpha: pd.Series, fwd_ret: pd.Series, window: int) -> pd.Series:
    """Vectorized rolling Spearman via rank transform + rolling Pearson."""
    a_rank = alpha.rank(pct=True)
    r_rank = fwd_ret.rank(pct=True)
    a_dm = a_rank - a_rank.rolling(window, min_periods=10).mean()
    r_dm = r_rank - r_rank.rolling(window, min_periods=10).mean()
    cov = (a_dm * r_dm).rolling(window, min_periods=10).mean()
    std_a = a_dm.rolling(window, min_periods=10).std()
    std_r = r_dm.rolling(window, min_periods=10).std()
    den = (std_a * std_r).replace(0, np.nan)
    return (cov / den).fillna(np.nan)


def analyze_alpha_stability(alpha_df, alpha_columns,
                            ic_windows=(30, 60, 120),
                            fwd_periods=(1, 2, 4)):
    results = {}
    for alpha_name in alpha_columns:
        if alpha_name not in alpha_df.columns:
            continue
        alpha_norm = zscore_normalize(alpha_df[alpha_name])
        ic_data = {}
        for ic_w in ic_windows:
            for fwd_p in fwd_periods:
                fwd_ret = alpha_df["close"].pct_change(fwd_p).shift(-fwd_p)
                ic = vectorized_rolling_spearman(alpha_norm, fwd_ret, ic_w)
                valid = ic.dropna().values
                if len(valid) < 10:
                    ic_data[(ic_w, fwd_p)] = {"ic_mean": 0.0, "ic_ir": 0.0}
                    continue
                ic_mean = float(np.mean(valid))
                ic_std  = float(np.std(valid))
                ic_data[(ic_w, fwd_p)] = {
                    "ic_mean": ic_mean,
                    "ic_ir": ic_mean / max(ic_std, 1e-8),
                    "ic_positive_pct": float(np.mean(valid > 0)),
                }
        # Aggregate
        means = np.array([v["ic_mean"] for v in ic_data.values()])
        irs   = np.array([v["ic_ir"] for v in ic_data.values()])
        frac_pos = float(np.mean(means > 0))
        frac_meaningful = float(np.mean(np.abs(means) > 0.02))
        cv = float(np.std(means) / max(abs(np.mean(means)), 1e-8))
        stab = (1.0 - min(cv, 2.0)/2.0)*0.5 + frac_pos*0.5
        if stab >= 0.7 and frac_pos >= 0.7:   grade = "A"
        elif stab >= 0.55 and frac_pos >= 0.5: grade = "B"
        elif stab >= 0.4 and frac_pos >= 0.4:  grade = "C"
        elif stab >= 0.25 or frac_pos >= 0.25: grade = "D"
        else:                                    grade = "F"
        best_k  = max(ic_data, key=lambda k: abs(ic_data[k]["ic_ir"]))
        worst_k = min(ic_data, key=lambda k: abs(ic_data[k]["ic_ir"]))
        results[alpha_name] = {
            "mean_ic": float(np.mean(means)), "std_ic": float(np.std(means)),
            "cv_ic": cv, "frac_positive_ic": frac_pos,
            "frac_meaningful_ic": frac_meaningful,
            "stability_score": stab, "grade": grade,
            "category": ALPHA_CATEGORIES.get(alpha_name, "unknown"),
            "ic_ir_median": float(np.median(irs)),
            "best_params": {"ic_window": best_k[0], "fwd_periods": best_k[1],
                            "ic_ir": ic_data[best_k]["ic_ir"]},
            "worst_params": {"ic_window": worst_k[0], "fwd_periods": worst_k[1],
                             "ic_ir": ic_data[worst_k]["ic_ir"]},
        }
    return results


def main():
    print("=" * 80)
    print("ALPHA ENSEMBLE PARAMETER STABILITY TEST")
    print("=" * 80)

    print("\n[1/3] Generating synthetic M15 data (3000 bars)...")
    df = generate_synthetic_m15(3000, seed=42)

    print("[2/3] Computing 23 alpha factors...")
    t0 = time.time()
    alpha_df = compute_all_alphas(df)
    print(f"  Done in {time.time()-t0:.1f}s")

    valid_alphas = []
    for col in ALPHA_COLUMNS:
        if col in alpha_df.columns:
            v = alpha_df[col].dropna()
            if len(v) > 100 and v.std() > 1e-10:
                valid_alphas.append(col)
    print(f"  Valid alphas with signal: {len(valid_alphas)}/{len(ALPHA_COLUMNS)}")

    print("[3/3] Analyzing per-alpha IC stability across parameter variations...")
    t0 = time.time()
    results = analyze_alpha_stability(
        alpha_df, valid_alphas,
        ic_windows=[30, 60, 120],
        fwd_periods=[1, 2, 4],
    )
    print(f"  Done in {time.time()-t0:.1f}s\n")

    # ── Report ──
    print("=" * 80)
    print("PER-ALPHA IC STABILITY REPORT")
    print("=" * 80)
    print(f"{'Alpha':<12} {'Grade':>5} {'MeanIC':>8} {'StdIC':>8} {'CV':>6} {'IC+':>6} {'Meaning':>8} {'Category':<15}")
    print("-" * 90)

    gc = {"A":0,"B":0,"C":0,"D":0,"F":0}
    for name in sorted(results, key=lambda x: -results[x]["stability_score"]):
        s = results[name]; gc[s["grade"]] = gc.get(s["grade"],0)+1
        print(f"{name:<12} {s['grade']:>5} {s['mean_ic']:>8.4f} {s['std_ic']:>8.4f} "
              f"{s['cv_ic']:>6.2f} {s['frac_positive_ic']:>5.0%} "
              f"{s['frac_meaningful_ic']:>8.0%} {s['category']:<15}")

    print(f"\nGrade distribution: A={gc['A']} B={gc['B']} C={gc['C']} D={gc['D']} F={gc['F']}")
    total = sum(gc.values())
    print(f"Robust (A+B): {gc['A']+gc['B']}/{total} = {(gc['A']+gc['B'])/total:.0%}")
    print(f"Fragile (D+F): {gc['D']+gc['F']}/{total} = {(gc['D']+gc['F'])/total:.0%}")

    robust   = sorted([n for n,s in results.items() if s["grade"] in ("A","B")], key=lambda x: -results[x]["stability_score"])
    marginal = sorted([n for n,s in results.items() if s["grade"]=="C"], key=lambda x: -results[x]["stability_score"])
    fragile  = sorted([n for n,s in results.items() if s["grade"] in ("D","F")], key=lambda x: -results[x]["stability_score"])

    print(f"\n{'='*80}")
    print("ROBUST ALPHAS (Grade A/B) — keep, use with full weight")
    print(f"{'='*80}")
    for n in robust:
        s = results[n]
        print(f"  {n}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, IC+={s['frac_positive_ic']:.0%}, "
              f"best=IC_W{s['best_params']['ic_window']}_F{s['best_params']['fwd_periods']}(IR={s['best_params']['ic_ir']:.3f})")

    print(f"\n{'='*80}")
    print("MARGINAL ALPHAS (Grade C) — use with reduced weight or regime-gated")
    print(f"{'='*80}")
    for n in marginal:
        s = results[n]
        print(f"  {n}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, IC+={s['frac_positive_ic']:.0%}")

    print(f"\n{'='*80}")
    print("FRAGILE ALPHAS (Grade D/F) — candidates for removal or zero weight")
    print(f"{'='*80}")
    for n in fragile:
        s = results[n]
        print(f"  {n}: IC={s['mean_ic']:.4f}±{s['std_ic']:.4f}, CV={s['cv_ic']:.2f}, "
              f"IC+={s['frac_positive_ic']:.0%}, cat={s['category']}")
        print(f"    Best:  IC_W{s['best_params']['ic_window']}_F{s['best_params']['fwd_periods']} IR={s['best_params']['ic_ir']:.3f}")
        print(f"    Worst: IC_W{s['worst_params']['ic_window']}_F{s['worst_params']['fwd_periods']} IR={s['worst_params']['ic_ir']:.3f}")

    # Save
    out = {"per_alpha": results, "robust": robust, "marginal": marginal, "fragile": fragile,
           "grade_distribution": gc}
    out_path = Path("artifacts") / "alpha_stability_report.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
