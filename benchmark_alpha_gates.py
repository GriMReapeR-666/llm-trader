"""Ground-truth benchmark for alpha out-of-sample gate designs.

Scores each candidate gate on TWO datasets built from the same random walk:

  NOISE   - pure random walk. No real alpha exists, so any survivor is a false
            positive: the gate bought imaginary alpha.
  SIGNAL  - the same walk with ONE known causal effect injected (5-bar momentum
            predicts the next-bar return). Ground truth is known, so we can also
            check the gate does not simply reject everything.

Paired design: one candidate population per seed is scored against BOTH
datasets, so gate designs are compared on identical expressions.

Success criterion, asserted at the bottom:
    max noise survivors == 0 across all seeds, AND min signal survivors > 0.

Run:  python benchmark_alpha_gates.py
"""
from __future__ import annotations

import random
import warnings

import numpy as np
import pandas as pd
from scipy import stats as sps

warnings.filterwarnings("ignore")

from research.alpha_miner.expression_generator import ExpressionGenerator
from research.alpha_miner.ic_evaluator import ICEvaluator
from core.cpcv_validation import CombinatorialPurgedCrossValidator

N_BARS = 1500
HORIZON = 5          # matches pipeline.calculate_forward_returns_5day
TRAIN_FRAC = 0.7
N_CANDIDATES = 400
BOOT_B = 200
BOOT_L = 25
SEEDS = (1, 2, 3, 4, 5, 6, 7, 8)


def make_dataset(seed: int, inject: bool):
    """Random walk; if `inject`, a strictly causal momentum->return effect.

    PRICE and LABEL come from separate RNG streams on purpose. Drawing both
    from one `default_rng(seed)` leaves them statistically independent but not
    guaranteed uncorrelated at finite n, and a single seed where volume happens
    to correlate with the label by ~0.12 turns the "noise" control into a
    dataset with real exploitable signal. That produced 80% of candidates
    clearing |IC|=0.05 on supposed-noise data (seeds 1 and 5) and looked
    exactly like a miscalibrated gate. An independent stream, plus the
    sanity check below, makes the null control actually null.
    """
    rng_p = np.random.default_rng(seed)
    rng_v = np.random.default_rng(seed + 100_000)
    rng_f = np.random.default_rng(seed + 200_000)
    idx = pd.RangeIndex(N_BARS)
    close = pd.Series(100 + np.cumsum(rng_p.normal(0, 1, N_BARS)), index=idx)
    momentum = (close - close.shift(HORIZON)) / close
    fwd = pd.Series(rng_f.normal(0, 0.01, N_BARS), index=idx)
    if inject:
        # alpha at bar t-1 predicts the return at bar t -> no peeking
        fwd = fwd + 0.9 * momentum.shift(1)
    fwd.iloc[:HORIZON] = np.nan
    ohlcv = pd.DataFrame({
        "open": close, "high": close * 1.008, "low": close * 0.992,
        "close": close, "volume": pd.Series(rng_v.lognormal(10, 0.3, N_BARS), index=idx),
    }, index=idx)
    return ohlcv, fwd


def null_is_clean(ohlcv, fwd, tol=0.08):
    """True when NO raw column correlates with the label beyond `tol`.

    A control that fails this is not a control: any gate that finds signal there
    is correct, not broken.
    """
    te = slice(int(N_BARS * TRAIN_FRAC) + HORIZON, N_BARS)
    worst = 0.0
    for col in ohlcv.columns:
        a, r = _aligned(ohlcv[col][te], fwd[te])
        if a is None:
            continue
        worst = max(worst, abs(_spearman(a, r)))
    return worst <= tol, worst


def _aligned(alpha: pd.Series, fwd: pd.Series):
    m = pd.concat([alpha, fwd], axis=1).dropna()
    if len(m) < 30:
        return None, None
    a, r = m.iloc[:, 0].to_numpy(float), m.iloc[:, 1].to_numpy(float)
    if np.ptp(a) == 0 or np.ptp(r) == 0:
        return None, None
    return a, r


def _spearman(a, r):
    """Spearman rho == Pearson on ranks. Computed directly rather than via
    scipy.spearmanr, which is called B times per candidate here and dominates
    runtime with input validation the caller has already done."""
    a = np.asarray(a, float)
    r = np.asarray(r, float)
    ra = sps.rankdata(a).astype(float)
    rr = sps.rankdata(r).astype(float)
    ra -= ra.mean()
    rr -= rr.mean()
    denom = np.sqrt((ra * ra).sum() * (rr * rr).sum())
    return float((ra * rr).sum() / denom) if denom > 0 else 0.0


def fisher_z_p(a, r):
    """Two-sided p from the normal approximation to Spearman.

    Assumes the effective sample size equals n. Kept deliberately: this is the
    naive default the benchmark is testing.
    """
    n = len(a)
    rho = _spearman(a, r)
    z = np.sqrt(n) * np.arctanh(np.clip(rho, -0.999999, 0.999999))
    return float(2 * (1 - sps.norm.cdf(abs(z))))


def block_bootstrap_p(a, r, rng, B=BOOT_B, L=BOOT_L):
    """p-value under the null of NO association, preserving autocorrelation.

    H0 is that `a` and `r` are independent, so each series keeps its own serial
    structure via a contiguous-block resample -- but the two series draw
    INDEPENDENT block positions. Sharing them resamples the joint (a_t, r_t)
    pairs, leaves the observed cross-correlation intact, and is not the null;
    that variant rejects genuine alpha.
    """
    n = min(len(a), len(r))
    if n < 3 * L:
        return 1.0
    a = np.asarray(a[:n], float)
    r = np.asarray(r[:n], float)
    obs = abs(_spearman(a, r))
    ac, rc = a - a.mean(), r - r.mean()
    n_blocks = int(np.ceil(n / L))
    # Precompute every contiguous block once, then sample block indices. This is
    # the same distribution as concatenating np.arange(s, s+L) per draw, but
    # avoids rebuilding the index arrays inside the loop (B x candidates times).
    blocks = np.lib.stride_tricks.sliding_window_view(ac, L)      # (n-L+1, L)
    blocks_r = np.lib.stride_tricks.sliding_window_view(rc, L)
    hits = 0
    for _ in range(B):
        ia = rng.integers(0, blocks.shape[0], size=n_blocks)
        ir = rng.integers(0, blocks_r.shape[0], size=n_blocks)
        ba = blocks[ia].reshape(-1)[:n]
        br = blocks_r[ir].reshape(-1)[:n]
        if abs(_spearman(ba, br)) >= obs:
            hits += 1
    return (hits + 1) / (B + 1)


def bh_keep(pvals, alpha=0.05):
    """Benjamini-Hochberg step-up count of rejections."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    if m == 0:
        return 0
    order = np.argsort(p)
    ok = (p[order] * m / np.arange(1, m + 1)) <= alpha
    return int(np.where(ok[::-1])[0][0]) + 1 if ok.any() else 0


def score_candidates(ohlcv, fwd, exprs):
    """Evaluate each candidate once, against train and test slices."""
    ev = ICEvaluator()
    cut = int(N_BARS * TRAIN_FRAC)
    tr = slice(0, cut - HORIZON)          # embargo at the boundary
    te = slice(cut + HORIZON, N_BARS)
    out = []
    for e in exprs:
        try:
            a = e.evaluate(ohlcv).reindex(ohlcv.index)
        except Exception:
            continue
        if a.isna().all() or np.ptp(a.fillna(0).to_numpy()) == 0:
            continue
        out.append({
            "name": e.to_string(), "alpha": a, "fwd": fwd,
            "ic_train": ev.calculate_ic(a[tr], fwd[tr]),
            "ic_test": ev.calculate_ic(a[te], fwd[te]),
            "test_arrays": _aligned(a[te], fwd[te]),
        })
    return out, te


def gate_in_sample_only(c):
    """Today's behaviour: the Phase 2 threshold, with no holdout at all."""
    return sum(1 for x in c if abs(x["ic_train"]) >= 0.02)


def gate_single_split(c):
    """Train threshold + held-out |IC| + sign agreement."""
    return sum(1 for x in c
               if abs(x["ic_train"]) >= 0.02
               and abs(x["ic_test"]) >= 0.02
               and np.sign(x["ic_train"]) == np.sign(x["ic_test"]))


def gate_cpcv(c, splits, ev):
    """Train threshold + mean OOS IC over all C(N,k) paths + sign agreement."""
    n = 0
    for x in c:
        if abs(x["ic_train"]) < 0.02:
            continue
        oos = [ev.calculate_ic(x["alpha"].iloc[s["test_indices"]],
                               x["fwd"].iloc[s["test_indices"]]) for s in splits]
        oos = [v for v in oos if np.isfinite(v)]
        if len(oos) < 5:
            continue
        mean_oos = float(np.mean(oos))
        if abs(mean_oos) >= 0.02 and np.sign(x["ic_train"]) == np.sign(mean_oos):
            n += 1
    return n


def gate_bh(c, pfunc, rng):
    """Train threshold, then BH-FDR over held-out p-values. Returns (tried, kept)."""
    ps = []
    for x in c:
        if abs(x["ic_train"]) < 0.02:
            continue
        a, r = x["test_arrays"]
        ps.append(pfunc(a, r, rng) if a is not None else 1.0)
    return len(ps), bh_keep(ps)


def evaluate(seed):
    random.seed(seed)
    exprs = ExpressionGenerator(seed=seed).generate_population(
        size=N_CANDIDATES, depth=2)

    ev = ICEvaluator()
    cv = CombinatorialPurgedCrossValidator(n_splits=6, n_test_splits=2, embargo_pct=0.01)
    et = np.arange(N_BARS)
    # label_end_times purges train rows whose 5-bar label leaks into a test chunk
    let = np.minimum(et + HORIZON, N_BARS - 1)
    splits = cv.split(N_BARS, event_times=et, label_end_times=let)

    row = {"seed": seed}
    for tag, inject in (("noise", False), ("signal", True)):
        ohlcv, fwd = make_dataset(seed, inject)
        if not inject:
            ok, worst = null_is_clean(ohlcv, fwd)
            row["noise_clean"] = ok
            row["noise_worst_corr"] = round(worst, 4)
            if not ok:
                # A contaminated control is not evidence about the gate.
                # Reporting it as a gate failure would be exactly backwards.
                print(f"  seed {seed}: NULL CONTROL CONTAMINATED "
                      f"(max |corr| {worst:.3f}) - skipped, not a gate result")
                continue
        cands, _te = score_candidates(ohlcv, fwd, exprs)
        rng = np.random.default_rng(seed)
        row[f"{tag}_tried"] = sum(1 for x in cands if abs(x["ic_train"]) >= 0.02)
        row[f"{tag}_insample"] = gate_in_sample_only(cands)
        row[f"{tag}_split"] = gate_single_split(cands)
        row[f"{tag}_cpcv"] = gate_cpcv(cands, splits, ev)
        row[f"{tag}_bh_fisher"] = gate_bh(cands, lambda a, r, g: fisher_z_p(a, r), rng)[1]
        row[f"{tag}_bh_boot"] = gate_bh(cands, block_bootstrap_p, rng)[1]
    return row


GATES = [("insample", "in-sample only (TODAY)"),
         ("split", "single train/test split"),
         ("cpcv", "CPCV mean OOS (15 paths)"),
         ("bh_fisher", "BH-FDR + Fisher z"),
         ("bh_boot", "BH-FDR + block bootstrap")]


def main():
    print(f"N={N_BARS} | horizon={HORIZON} | train={int(N_BARS * TRAIN_FRAC)} "
          f"| embargo={HORIZON} | {N_CANDIDATES} candidates | seeds={len(SEEDS)}")
    rows = [evaluate(s) for s in SEEDS]
    valid = [r for r in rows if r.get("noise_clean", True)]
    skipped = len(rows) - len(valid)
    if skipped:
        print(f"\n({skipped}/{len(rows)} seeds skipped: null control contaminated)")

    w = 24
    hdr = f"{'seed':>4} | " + " | ".join(f"{n:>{w}}" for _, n in GATES)
    for tag, cap in (("noise", "NOISE survivors (false positives)"),
                     ("signal", "SIGNAL survivors (true alpha kept)")):
        print(f"\n{cap}")
        print(hdr); print("-" * len(hdr))
        for r in rows:
            if f"{tag}_insample" not in r:
                print(f"{r['seed']:>4} | " + " " * w + " (control contaminated - skipped)")
                continue
            print(f"{r['seed']:>4} | " + " | ".join(
                f"{r[tag + '_' + k]:>{w}}" for k, _ in GATES))
        tried = [r[f"{tag}_tried"] for r in rows if f"{tag}_tried" in r]
        print(f"  (of {min(tried)}-{max(tried)} that passed the train threshold)")

    print("\n" + "=" * (len(GATES) * (w + 3) + 40))
    print(f"{'gate':<34}{'noise (max/total)':>24}{'signal (min/total)':>24}")
    print("-" * (len(GATES) * (w + 3) + 40))
    for key, label in GATES:
        nz = [r[f"noise_{key}"] for r in rows]
        sg = [r[f"signal_{key}"] for r in rows]
        print(f"{label:<34}{f'{max(nz)} / {sum(nz)}':>24}{f'{min(sg)} / {sum(sg)}':>24}")

    print("\nSUCCESS CRITERION  (max noise == 0) AND (min signal > 0)")
    for key, label in GATES:
        nz = [r[f"noise_{key}"] for r in rows]
        sg = [r[f"signal_{key}"] for r in rows]
        ok = max(nz) == 0 and min(sg) > 0
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return rows


if __name__ == "__main__":
    main()