"""Alpha Combination Engine — Alpha101-style Alpha Ensemble.

Combines multiple individual alpha factors into a single composite signal score,
weighted by their historical predictive power (Information Coefficient).

Design follows the quant research platform approach:
  1. Normalize each alpha to z-scores (removes scale differences)
  2. Compute rolling Spearman IC (rank correlation with forward returns)
  3. Weight alphas by their IC_IR (IC / std(IC)) — higher IR = more weight
  4. Combine into a composite signal
  5. Report quality metrics per alpha and overall

References:
    Kakushadze, Z. (2016). "101 Formulaic Alphas." SSRN 2701346.
    quant research platform alpha ranking methodology.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


# ---------------------------------------------------------------------------
# Data classes for results
# ---------------------------------------------------------------------------

@dataclass
class AlphaQuality:
    """Quality metrics for a single alpha factor."""
    name: str
    ic_mean: float = 0.0          # Mean Information Coefficient
    ic_std: float = 0.0           # Standard deviation of IC
    ic_ir: float = 0.0            # IC Information Ratio (ic_mean / ic_std)
    ic_positive_pct: float = 0.0  # % of periods with positive IC
    turnover: float = 0.0         # Average absolute daily change in normalized alpha
    half_life: int = 0            # Bars until IC decays to 50% of its peak
    stability: float = 0.0        # Rank correlation of IC over first/second half
    weight: float = 0.0           # Assigned weight in the ensemble


@dataclass
class EnsembleResult:
    """Result of combining multiple alphas into a composite signal."""
    composite: pd.Series                    # The composite signal (z-score)
    composite_raw: pd.Series                # Raw weighted sum before z-scoring
    alpha_weights: Dict[str, float]         # Final weights per alpha
    alpha_qualities: Dict[str, AlphaQuality]  # Quality metrics per alpha
    n_alphas_used: int                      # How many alphas had valid IC
    ic_mean_overall: float = 0.0            # Weighted average IC across alphas


# ---------------------------------------------------------------------------
# Alpha Category Registry & Regime Multipliers
# ---------------------------------------------------------------------------

# Map each alpha to its primary category for regime-based weighting.
ALPHA_CATEGORIES = {
    "alpha_001": "reversal",       # rank(Ts_ArgMax(SignedPower(...))) - 0.5
    "alpha_005": "volume_price",   # -1 * correlation(rank(delta(log(volume))), rank((close-open)/open))
    "alpha_009": "volume_price",   # -1 * correlation(open, volume, 10)
    "alpha_012": "divergence",     # sign(delta(volume)) * (-1 * delta(close))
    "alpha_014": "reversal",       # -1 * rank(delta(returns, 3))
    "alpha_017": "composite",      # Triple rank: price level x accel x volume
    "alpha_019": "mean_reversion", # Long-term mean reversion x short-term momentum
    "alpha_022": "volume_price",   # -1 * delta(correlation(high, volume, 5))
    "alpha_023": "reversal",       # Overbought high reversal
    "alpha_025": "composite",      # Multi-factor bearish compound
    "alpha_028": "composite",      # Price below VWAP correlation zone
    "alpha_030": "volume_price",   # Directional consensus x volume ratio
    "alpha_034": "mean_reversion", # Mean reversion proxy
    "alpha_035": "volume_price",   # Volume-weighted price momentum
    "alpha_036": "momentum",       # Momentum continuation
    "alpha_037": "momentum",       # Trend following
    "alpha_038": "momentum",       # Momentum strength
    "alpha_041": "momentum",       # Price momentum
    "alpha_043": "volume_price",   # Volume-weighted directional move
    "alpha_053": "reversal",       # Short-term reversal
    "alpha_055": "composite",      # Multi-factor signal
    "alpha_060": "momentum",       # Trend continuation
    "alpha_101": "composite",      # Complex multi-factor
}

# Regime -> category weight multipliers.
# >1.0 = boost, <1.0 = suppress, 0.0 = zero out.
REGIME_ALPHA_MULTIPLIERS = {
    "LOW_VOL_TREND": {
        "momentum": 1.5,       # Trend alphas get boosted
        "reversal": 0.5,       # Reversal alphas suppressed in trends
        "mean_reversion": 0.3, # Mean-reversion suppressed in trends
        "volume_price": 1.2,   # Volume-price moderately useful
        "divergence": 0.8,     # Divergence slightly suppressed
        "composite": 1.0,      # Composite neutral
    },
    "LOW_VOL_RANGE": {
        "momentum": 0.3,       # Momentum suppressed in ranges
        "reversal": 1.5,       # Reversal alphas get boosted
        "mean_reversion": 1.8, # Mean-reversion strongly boosted
        "volume_price": 1.2,   # Volume-price useful for range detection
        "divergence": 1.3,     # Divergence useful in ranges
        "composite": 1.0,      # Composite neutral
    },
    "HIGH_VOL_TREND": {
        "momentum": 1.3,       # Momentum moderately boosted
        "reversal": 0.3,       # Reversal suppressed in volatile trends
        "mean_reversion": 0.2, # Mean-reversion very suppressed
        "volume_price": 1.5,   # Volume-price strongly useful in volatile trends
        "divergence": 1.0,     # Divergence neutral
        "composite": 1.0,      # Composite neutral
    },
    "HIGH_VOL_CRISIS": {
        "momentum": 0.2,       # Everything suppressed in crisis
        "reversal": 0.3,
        "mean_reversion": 0.2,
        "volume_price": 0.5,
        "divergence": 0.3,
        "composite": 0.5,
    },
}


#: Learned MARS regime multipliers. Module-level so tests can point it at a
#: tmp_path instead of the shared artifacts/ tree — a test that writes the
#: real file makes the branch it covers unobservable (and dirties production
#: data), while a test that skips the write never reaches the branch at all.
MARS_MULTIPLIERS_FILE = "artifacts/mars_alpha_multipliers.json"

#: Applied alpha-weight deltas from the DSR shadow-deploy gate. Same rationale
#: as MARS_MULTIPLIERS_FILE.
META_ALPHA_WEIGHTS_FILE = "artifacts/meta_alpha_weights.json"


def _apply_regime_adjustments(
    weights: Dict[str, float],
    regime: Optional[Dict[str, Any]],
) -> Dict[str, float]:
    """Apply regime-based multipliers to alpha weights.

    Loads MARS multipliers from artifacts/mars_alpha_multipliers.json when available,
    falling back to hardcoded REGIME_ALPHA_MULTIPLIERS.

    Parameters
    ----------
    weights : dict
        Original IC-based weights.
    regime : dict or None
        Regime allocation from HierarchicalMetaPortfolioEngine.
        Must contain 'primary_regime' key.

    Returns
    -------
    Adjusted weights (renormalized to sum to 1.0).
    """
    if not regime or not isinstance(regime, dict):
        return weights
    regime_name = regime.get("primary_regime", "")

    # Try to load MARS-learned multipliers from artifacts first
    multipliers = None
    try:
        import json as _json
        from pathlib import Path as _Path
        mars_path = _Path(MARS_MULTIPLIERS_FILE)
        if mars_path.exists():
            mars_data = _json.loads(mars_path.read_text(encoding="utf-8"))
            # MARS stores per-alpha multipliers keyed by alpha name
            if isinstance(mars_data, dict) and regime_name in mars_data:
                multipliers = mars_data[regime_name]
            elif isinstance(mars_data, dict) and "updated_multipliers" in mars_data:
                multipliers = mars_data["updated_multipliers"].get(regime_name)
            elif isinstance(mars_data, dict) and "multipliers" in mars_data:
                multipliers = mars_data["multipliers"]
    except Exception:
        pass

    # Fall back to hardcoded multipliers if MARS not available
    if not multipliers:
        multipliers = REGIME_ALPHA_MULTIPLIERS.get(regime_name)
    if not multipliers:
        return weights
    adjusted = {}
    for alpha_name, w in weights.items():
        # Extract category from alpha name (alpha_012 -> alpha_012)
        cat = ALPHA_CATEGORIES.get(alpha_name, "composite")
        mult = multipliers.get(cat, 1.0)
        adjusted[alpha_name] = w * mult
    # Renormalize
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total for k, v in adjusted.items()}
    else:
        # All zeroed out — fall back to equal weights
        adjusted = {k: 1.0 / len(adjusted) for k in adjusted}
    return adjusted


def _apply_decay_health(
    weights: Dict[str, float],
    decay_health: Dict[str, Any],
) -> Dict[str, float]:
    """Apply decay health adjustments to alpha weights.

    When the AlphaDecayMonitor reports an alpha as DEAD or REVERSED,
    its weight is zeroed. DEGRADED alphas get halved weight. This wires
    the decay monitor's orthogonalized IC assessment into the ensemble's
    periodic re-weighting.

    Parameters
    ----------
    weights : dict
        Current IC-based weights.
    decay_health : dict
        AlphaDecayMonitor.get_summary()['alphas'] — per-alpha health status.

    Returns
    -------
    Adjusted weights (renormalized to sum to 1.0).
    """
    if not decay_health:
        return weights
    adjusted = {}
    for alpha_name, w in weights.items():
        info = decay_health.get(alpha_name)
        if not info:
            adjusted[alpha_name] = w
            continue
        status = info.get("status", "HEALTHY")
        if status in ("DEAD", "REVERSED"):
            adjusted[alpha_name] = 0.0
            log.info(
                "Decay filter: %s weight zeroed (status=%s, IC=%.4f)",
                alpha_name, status, info.get("ic_current", 0),
            )
        elif status == "DEGRADED":
            adjusted[alpha_name] = w * 0.5
            log.debug(
                "Decay filter: %s weight halved (DEGRADED, IC=%.4f)",
                alpha_name, info.get("ic_current", 0),
            )
        else:
            adjusted[alpha_name] = w
    # Renormalize
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total for k, v in adjusted.items()}
    else:
        # All alphas dead — fall back to equal weights as last resort
        adjusted = {k: 1.0 / len(adjusted) for k in adjusted}
    return adjusted


def _apply_meta_adjustments(weights: Dict[str, float]) -> Dict[str, float]:
    """Apply validated meta-loop prior adjustments from artifacts/meta_alpha_weights.json.

    When Loop 4 (MetaLoop) validates Δpriors through the DSR shadow-deploy gate,
    it persists applied alpha weight adjustments. This function blends those
    validated adjustments into the current alpha weights.
    """
    if not weights:
        return weights
    try:
        import json as _json
        from pathlib import Path as _Path
        meta_path = _Path(META_ALPHA_WEIGHTS_FILE)
        if not meta_path.exists():
            meta_path = _Path(__file__).resolve().parent.parent / "artifacts" / "meta_alpha_weights.json"
        if not meta_path.exists():
            return weights
        meta_data = _json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta_data, dict) or not meta_data:
            return weights

        adjusted = dict(weights)
        modified = False
        for alpha_name, delta in meta_data.items():
            if alpha_name in adjusted:
                try:
                    if isinstance(delta, dict):
                        new_val = delta.get("proposed") or delta.get("new_value")
                    else:
                        new_val = delta
                    if new_val is not None:
                        adjusted[alpha_name] = max(0.0, float(new_val))
                        modified = True
                except (ValueError, TypeError):
                    pass
        if modified:
            total = sum(adjusted.values())
            if total > 0:
                return {k: v / total for k, v in adjusted.items()}
        return weights
    except Exception:
        return weights


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def zscore_normalize(series: pd.Series, window: Optional[int] = None) -> pd.Series:
    """Z-score normalize a series.

    If *window* is None, uses expanding window (full history up to each bar).
    If *window* is set, uses a rolling window for normalization.
    """
    if window is None:
        mean = series.expanding(min_periods=20).mean()
        std = series.expanding(min_periods=20).std()
    else:
        mean = series.rolling(window, min_periods=20).mean()
        std = series.rolling(window, min_periods=20).std()
    std = std.replace(0, np.nan)
    return ((series - mean) / std).fillna(0.0)


def rank_normalize(series: pd.Series, window: Optional[int] = None) -> pd.Series:
    """Percentile rank normalize to [0, 1]."""
    if window is None:
        return series.expanding(min_periods=20).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )
    return series.rolling(window, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


# ---------------------------------------------------------------------------
# Information Coefficient (IC) calculation
# ---------------------------------------------------------------------------

def rolling_ic(
    alpha: pd.Series,
    forward_returns: pd.Series,
    window: int = 60,
    stride: int = 1,
) -> pd.Series:
    """Rolling Spearman rank IC between alpha and forward returns.

    Spearman is used instead of Pearson because alpha signals are often
    non-linearly related to returns — rank correlation is more robust.

    Uses a manual loop for correctness with pandas rolling.apply.

    stride > 1 evaluates the windowed correlation every `stride` bars and
    forward-fills in between. Anchors are strictly backward-looking (the IC
    at anchor i uses only data up to i), so filling forward introduces no
    look-ahead; between-anchor values are merely stale by up to `stride`
    bars. Overlapping-window ICs are highly autocorrelated anyway, so the
    effective sample size of a strided estimate is close to the dense one —
    this turns an O(n·window) scipy loop into O(n/stride·window) for large
    histories where the IC only feeds scalar ensemble weights.

    Architectural Separation (P0-4):
    -------------------------------
    Measurement vs. Allocation are decoupled:
    - `measure_ic()`: strictly calculates empirical rolling Spearman IC, IC_IR,
      autocorrelation, half-life, and stability for reporting and risk evaluation.
      It NEVER modifies weights or applies shrinkage overrides.
    - `allocate_weights()`: takes quality measurements and computes trading
      portfolio weights. It enforces James-Stein shrinkage (P0-2), regime
      multipliers, decay knockout (P0-1), and meta-loop adjustments.
    """
    a = alpha.values.astype(float)
    r = forward_returns.values.astype(float)
    n = len(a)

    stride = max(1, int(stride))
    if stride == 1:
        result = np.full(n, np.nan)
        for i in range(window - 1, n):
            start = i - window + 1
            x = a[start : i + 1]
            y = r[start : i + 1]
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() >= 10:
                xs, ys = x[mask], y[mask]
                # spearmanr is undefined for constant inputs and emits a
                # ConstantInputWarning (weekend/flat data); IC is 0 there.
                if np.ptp(xs) == 0 or np.ptp(ys) == 0:
                    result[i] = 0.0
                else:
                    corr, _ = sp_stats.spearmanr(xs, ys)
                    result[i] = corr
            else:
                result[i] = np.nan

    # Strided path: anchors every `stride` bars, causal forward-fill.
    anchors = np.arange(window - 1, n, stride)
    anchor_vals = np.full(len(anchors), np.nan)
    for j, i in enumerate(anchors):
        start = i - window + 1
        x = a[start : i + 1]
        y = r[start : i + 1]
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() >= 10:
            xs, ys = x[mask], y[mask]
            if np.ptp(xs) == 0 or np.ptp(ys) == 0:
                anchor_vals[j] = 0.0
            else:
                corr, _ = sp_stats.spearmanr(xs, ys)
                anchor_vals[j] = corr

    result = np.full(n, np.nan)
    result[anchors] = anchor_vals
    filled = pd.Series(result, index=alpha.index).ffill()
    return filled


def expanding_ic(
    alpha: pd.Series,
    forward_returns: pd.Series,
    min_periods: int = 40,
) -> pd.Series:
    """Expanding-window Spearman IC (no look-ahead bias)."""
    a = alpha.values.astype(float)
    r = forward_returns.values.astype(float)
    n = len(a)
    result = np.full(n, np.nan)

    for i in range(min_periods - 1, n):
        x = a[: i + 1]
        y = r[: i + 1]
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() >= 10:
            xs, ys = x[mask], y[mask]
            if np.ptp(xs) == 0 or np.ptp(ys) == 0:
                result[i] = 0.0
            else:
                corr, _ = sp_stats.spearmanr(xs, ys)
                result[i] = corr

    return pd.Series(result, index=alpha.index)


# ---------------------------------------------------------------------------
# Alpha quality assessment
# ---------------------------------------------------------------------------

def compute_alpha_quality(
    alpha: pd.Series,
    forward_returns: pd.Series,
    name: str,
    ic_window: int = 60,
    ic_stride: int = 1,
) -> AlphaQuality:
    """Compute quality metrics for a single alpha factor."""
    ic = rolling_ic(alpha, forward_returns, window=ic_window, stride=ic_stride)

    # IC statistics (ignore NaNs)
    ic_valid = ic.dropna()
    ic_mean = float(ic_valid.mean()) if len(ic_valid) > 0 else 0.0
    ic_std = float(ic_valid.std()) if len(ic_valid) > 1 else 1.0
    ic_ir = ic_mean / max(ic_std, 1e-8)
    ic_positive_pct = float((ic_valid > 0).sum() / max(len(ic_valid), 1))

    # Turnover: average absolute change in z-scored alpha per bar
    alpha_norm = zscore_normalize(alpha)
    turnover = float(alpha_norm.diff().abs().mean())

    # Half-life: how many bars until IC decays to 50% of its peak
    half_life = _compute_half_life(ic_valid)

    # Stability: Spearman correlation of IC in first half vs second half
    stability = _compute_stability(ic_valid)

    return AlphaQuality(
        name=name,
        ic_mean=ic_mean,
        ic_std=ic_std,
        ic_ir=ic_ir,
        ic_positive_pct=ic_positive_pct,
        turnover=turnover,
        half_life=half_life,
        stability=stability,
    )


def _compute_half_life(ic: pd.Series) -> int:
    """Estimate the half-life of IC autocorrelation."""
    if len(ic) < 10:
        return 0
    autocorr = [ic.autocorr(lag=i) for i in range(1, min(61, len(ic) // 2))]
    if not autocorr or autocorr[0] <= 0:
        return 0
    peak = autocorr[0]
    for i, val in enumerate(autocorr):
        if val < peak / 2:
            return i + 1
    return len(autocorr)


def _compute_stability(ic: pd.Series) -> float:
    """Spearman correlation of IC in first half vs second half.

    High stability means the alpha's predictive power is consistent over time.
    """
    arr = ic.dropna().values
    if len(arr) < 20:
        return 0.0
    half = len(arr) // 2
    first_half = arr[:half]
    second_half = arr[half : half + half]  # ensure equal length
    if len(first_half) != len(second_half) or len(first_half) < 10:
        return 0.0
    if np.ptp(first_half) == 0 or np.ptp(second_half) == 0:
        return 0.0   # constant IC halves: stability undefined, treat as 0
    corr, _ = sp_stats.spearmanr(first_half, second_half)
    return float(corr) if np.isfinite(corr) else 0.0


# ---------------------------------------------------------------------------
# Weight calculation
# ---------------------------------------------------------------------------

def measure_ic(
    normalized: Dict[str, pd.Series],
    forward_returns: pd.Series,
    ic_window: int = 60,
    ic_stride: int = 1,
) -> Dict[str, AlphaQuality]:
    """Empirical Information Coefficient (IC) quality measurement for alpha signals.

    Architectural Boundary (P0-4):
    -----------------------------
    This function is strictly for MEASUREMENT and REPORTING (e.g. risk reports,
    diagnostics, quality auditing). It calculates empirical rolling Spearman IC,
    mean IC, IC standard deviation, Information Ratio, turnover, half-life, and
    temporal stability across split halves.

    It NEVER applies portfolio optimization, shrinkage overrides, James-Stein
    dampening, regime multipliers, decay knockout, or meta-weights.
    Those decision logic belong exclusively to `allocate_weights()`.
    """
    qualities = {}
    for col, norm_series in normalized.items():
        qualities[col] = compute_alpha_quality(
            norm_series, forward_returns, name=col, ic_window=ic_window, ic_stride=ic_stride
        )
    return qualities


def compute_ic_weights(
    qualities: Dict[str, AlphaQuality],
    min_ir: float = 0.0,
    shrinkage: Optional[float] = None,
    t_eff: Optional[int] = None,
) -> Dict[str, float]:
    """Compute ensemble weights from alpha quality metrics with James-Stein shrinkage.

    P0-2 & P0-4 Architectural Separation:
    - Measurement (IC_IR, stability, turnover) is computed upstream by `measure_ic()`.
    - This function implements portfolio optimization / James-Stein shrinkage.
    - If N/T > 0.25, logs WARNING and enforces shrinkage lambda = min(0.5, r).
    - If T_eff < 100, enforces shrinkage floor to combat small-sample noise.

    Weighting scheme (Alpha101-style + shrinkage):
      raw_i = max(IC_IR_i - min_ir, 0)
      w_i = (1 - λ) * raw_i / sum(raw) + λ * (1/N)   where λ = shrinkage
    """
    n = len(qualities)
    if n == 0:
        return {}

    t = int(t_eff) if t_eff is not None and t_eff > 0 else 60
    r = n / float(t)

    if r > 0.25:
        log.warning(
            "Ensemble N/T ratio r=%.3f > 0.25 (N=%d, T=%d). High noise sensitivity regime; "
            "applying James-Stein shrinkage toward equal weights.",
            r, n, t,
        )

    # Base user shrinkage or default 0.3
    user_lam = float(shrinkage) if shrinkage is not None else 0.3

    # Enforce shrinkage if r > 0.25 or t < 100
    if r > 0.25:
        lam = float(np.clip(max(user_lam, min(0.5, r)), 0.0, 0.5))
    elif t < 100:
        lam = float(np.clip(max(user_lam, min(0.5, max(0.15, r))), 0.0, 0.5))
    else:
        lam = float(np.clip(user_lam, 0.0, 0.5))

    raw = {}
    for name, q in qualities.items():
        raw[name] = max(q.ic_ir - min_ir, 0.0)
    total = sum(raw.values())
    if total <= 0:
        return {name: 1.0 / n for name in qualities}

    equal_w = 1.0 / n
    shrunk = {}
    for name, val in raw.items():
        w_raw = val / total
        w = (1 - lam) * w_raw + lam * equal_w
        shrunk[name] = w

    s = sum(shrunk.values())
    return {k: v / s for k, v in shrunk.items()} if s > 0 else {name: 1.0 / n for name in qualities}


def allocate_weights(
    qualities: Dict[str, AlphaQuality],
    regime: Optional[Any] = None,
    decay_health: Optional[Dict[str, Any]] = None,
    min_ir: float = 0.0,
    shrinkage: Optional[float] = None,
    ic_window: int = 60,
    meta_adjustments: bool = True,
    weighting: str = "ic_ir",
) -> Dict[str, float]:
    """Allocate portfolio weights to alpha signals based on quality metrics and constraints.

    Architectural Boundary (P0-4 & P0-2):
    -----------------------------------
    This function represents the DECISION / ALLOCATION path.
    Unlike `measure_ic()`, this function always applies:
    1. James-Stein shrinkage toward equal weights (P0-2), scaling lambda = min(0.5, max(shrinkage, r))
       where r = N / T_eff. Logs a warning if r > 0.25. Enforces T_eff >= 100 or shrinkage.
    2. Minimum IR gating (min_ir > 0.0 zeroes out raw weights for alphas below threshold).
    3. Macro / Microstructure regime multipliers (`_apply_regime_adjustments`).
    4. Alpha decay knockout (P0-1) where DEAD/REVERSED alphas are strictly zeroed out.
    5. Meta-loop validated adjustments (`_apply_meta_adjustments`).
    """
    if not qualities:
        return {}

    # 1. Base weights from chosen weighting scheme with James-Stein shrinkage
    if weighting == "ic_ir":
        weights = compute_ic_weights(qualities, min_ir=min_ir, shrinkage=shrinkage, t_eff=ic_window)
    elif weighting == "ic":
        raw = {name: max(q.ic_mean, 0.0) for name, q in qualities.items()}
        total = sum(raw.values())
        weights = {name: v / total if total > 0 else 1.0 / len(raw) for name, v in raw.items()}
    else:  # equal
        weights = {name: 1.0 / len(qualities) for name in qualities}

    # 2. Apply regime multipliers
    weights = _apply_regime_adjustments(weights, regime)

    # 3. Apply decay health adjustments (DEAD/REVERSED -> 0.0, DEGRADED -> 0.5)
    if decay_health:
        weights = _apply_decay_health(weights, decay_health)

    # 4. Apply meta-loop adjustments
    if meta_adjustments:
        weights = _apply_meta_adjustments(weights)

    return weights


# ---------------------------------------------------------------------------
# Main ensemble engine
# ---------------------------------------------------------------------------

def orthogonalize_pairwise(
    normalized: Dict[str, pd.Series],
    *,
    window: int = 480,
    min_periods: int = 120,
    stride: int = 0,
    regularization: float = 0.01,
    relative_floor: float = 0.01,
) -> Tuple[Dict[str, pd.Series], Dict[str, Dict[str, float]]]:
    """Mutually orthogonalize sibling alphas so IC weighting has dispersion.

    The IC-IR weighting scheme degenerates to equal weight when all alphas
    are highly-correlated variants of the same signal: their rolling ICs are
    statistically indistinguishable, so no weight ordering is meaningful
    (observed on the 6-alpha persistence family: every IC_IR negative, all
    weights = 1/6). Residualizing each alpha against its siblings removes the
    shared TSMOM component and leaves each alpha's *unique* information, whose
    IC differs across alphas — giving the IC-IR weighting something to
    differentiate.

    Causality: each residual at bar t comes from an OLS fit on the window
    ending at t (rolling, never full-sample). The alphas themselves are
    strictly backward-looking, so no future information can enter. Sibling
    order matters (each alpha absorbs the later ones' shared component), so
    order is fixed by the caller's registry order (longest lookback first).

    Returns (purified dict, diagnostics dict per alpha: r_squared, resid_std,
    kept_original).
    """
    from core.signal_orthogonalizer import _ols_orthogonalize_single

    names = list(normalized.keys())
    n = len(names)
    if n < 2:
        return dict(normalized), {nm: {} for nm in names}

    purified: Dict[str, pd.Series] = {}
    diag: Dict[str, Dict[str, float]] = {}

    # Sibling order: registry order (caller passes longest-lookback first).
    # Alpha i is residualized against alphas 0..i-1; alpha 0 stays raw.
    for i, name in enumerate(names):
        sig = normalized[name]
        if i == 0:
            purified[name] = sig
            diag[name] = {"r_squared": 0.0, "resid_std": float(sig.std()), "kept_original": False}
            continue
        sibs = [normalized[nm] for nm in names[:i]]
        X = pd.concat(sibs, axis=1)
        X.columns = names[:i]
        resid, _loadings, r2, resid_std = _ols_orthogonalize_single(
            sig, X,
            min_periods=min_periods,
            rolling_window=window,
            regularization=regularization,
        )
        # A residual with negligible std relative to the original means the
        # alpha carried no unique information (fully absorbed by siblings —
        # e.g. an exact duplicate). Fall back to the raw alpha so the
        # ensemble never silently drops a member. The floor is RELATIVE to
        # the original std: the ridge term biases even exact fits to a small
        # nonzero residual, so an absolute floor cannot distinguish
        # "absorbed" from "tiny unique signal".
        orig_std = float(sig.dropna().std()) if len(sig.dropna()) > 5 else 0.0
        absorb_floor = max(relative_floor * orig_std, 1e-12)
        if resid_std < absorb_floor:
            log.warning(
                "pairwise ortho: %s residual std %.6f < %.2f%% of original "
                "(fully absorbed by siblings) - keeping original",
                name, resid_std, relative_floor * 100,
            )
            purified[name] = sig
            diag[name] = {"r_squared": float(r2), "resid_std": resid_std, "kept_original": True}
            continue
        if stride > 1:
            # Forward-fill the residual between evaluation anchors, matching
            # the IC-stride semantics of rolling_ic.
            anchor = pd.Series(np.nan, index=resid.index)
            anchor.iloc[::stride] = resid.iloc[::stride]
            resid = anchor.ffill()
        purified[name] = resid
        diag[name] = {
            "r_squared": float(r2),
            "resid_std": resid_std,
            "orig_std": orig_std,
            "kept_original": False,
        }

    return purified, diag


def combine_alphas(
    alpha_df: pd.DataFrame,
    alpha_columns: List[str],
    forward_return_col: str = "_fwd_return",
    forward_return_periods: int = 1,
    ic_window: int = 60,
    ic_stride: int = 1,
    normalize_method: str = "zscore",
    weighting: str = "ic_ir",
    min_ir: float = 0.0,
    shrinkage: Optional[float] = None,
    regime: Optional[Dict[str, Any]] = None,
    orthogonalize: bool = False,
    ortho_method: str = "ols",
    ortho_rolling_window: int = 120,
    mutual_orthogonalize: bool = False,
    mutual_ortho_window: int = 480,
    mutual_ortho_stride: int = 0,
    decay_health: Optional[Dict[str, Any]] = None,
    decay_monitor: Optional[Any] = None,
    alive_only: bool = False,
) -> EnsembleResult:
    """Combine multiple alpha factors into a composite signal.

    Parameters
    ----------
    alpha_df : pd.DataFrame
        DataFrame containing alpha columns and OHLCV data.
    alpha_columns : list[str]
        Names of the alpha columns to combine (e.g. ["alpha_012", "alpha_053"]).
    forward_return_col : str
        Column name for forward returns. If not present, computed from "close".
    forward_return_periods : int
        How many bars ahead to look for forward returns (for IC calculation).
    ic_window : int
        Rolling window size for IC computation.
    ic_stride : int
        Stride for IC evaluation on large histories (1 = every bar). With a
        stride > 1 the rolling IC is computed every `ic_stride` bars and
        causally forward-filled; recommended when n_bars / stride still
        leaves a representative sample (e.g. stride=25 at 100k M15 bars).
    normalize_method : str
        "zscore" or "rank" — how to normalize individual alphas.
    weighting : str
        "ic_ir" — weight by IC Information Ratio.
        "equal" — equal weight to all alphas.
        "ic" — weight by mean IC.
    mutual_orthogonalize : bool
        Residualize each alpha against its siblings (fixed registry order)
        before IC computation. Removes the shared component of highly-
        correlated alpha families so IC-IR weights reflect unique per-alpha
        information instead of degenerating to equal weight. Causal: rolling
        window regression, residual at t uses data through t only.
    mutual_ortho_window : int
        Rolling regression window in bars.
    mutual_ortho_stride : int
        Optional stride for the residual series on large histories (0 = every
        bar). Match this to the IC stride when striding.
    decay_health : dict, optional
        Output of AlphaDecayMonitor.get_summary()['alphas']. When provided,
        alphas with DEAD/REVERSED status get zeroed weight, DEGRADED alphas
        get halved weight. This wires the decay monitor's orthogonalized IC
        into the ensemble's periodic re-weighting.

    Returns
    -------
    EnsembleResult with composite signal, weights, and quality metrics.
    """
    df = alpha_df.copy()

    # --- Compute forward returns if not present ---
    if forward_return_col not in df.columns:
        df[forward_return_col] = df["close"].pct_change(forward_return_periods).shift(
            -forward_return_periods
        )

    fwd_ret = df[forward_return_col]

    # --- Normalize each alpha ---
    normalize = zscore_normalize if normalize_method == "zscore" else rank_normalize
    normalized = {}
    for col in alpha_columns:
        if col in df.columns:
            normalized[col] = normalize(df[col])

    if not normalized:
        raise ValueError("No valid alpha columns found in the DataFrame")

    # --- Signal orthogonalization (ψ) ---
    # Regress each alpha against market factors, retain purified residuals.
    # Removes common-factor contamination so IC reflects UNIQUE predictive power.
    if orthogonalize:
        try:
            from core.signal_orthogonalizer import orthogonalize_for_ensemble
            normalized = orthogonalize_for_ensemble(
                normalized, df, method=ortho_method, rolling_window=ortho_rolling_window,
            )
        except Exception as e:
            log.warning(
                "Signal orthogonalization failed (falling back to raw): %s", e
            )

    # --- Mutual orthogonalization (sibling residualization) ---
    # Distinct from `orthogonalize` (which purifies against MARKET factors
    # from df): this removes the SHARED component between the alphas
    # themselves so their ICs differ and IC-IR weighting is meaningful.
    mutual_diag: Optional[Dict[str, Dict[str, float]]] = None
    if mutual_orthogonalize:
        try:
            normalized, mutual_diag = orthogonalize_pairwise(
                normalized,
                window=mutual_ortho_window,
                min_periods=min(120, max(30, mutual_ortho_window // 4)),
                stride=mutual_ortho_stride,
            )
        except Exception as e:
            log.warning(
                "Mutual orthogonalization failed (falling back to raw): %s", e
            )
            mutual_diag = None

    # Wire decay monitor if passed directly
    if decay_monitor is not None and decay_health is None:
        try:
            summary = decay_monitor.get_summary()
            decay_health = summary.get("alphas", {})
        except Exception as e:
            log.warning("Failed to extract decay health from decay_monitor: %s", e)

    if alive_only and decay_health:
        alive_cols = [
            c for c in alpha_columns
            if decay_health.get(c, {}).get("status") not in ("DEAD", "REVERSED")
        ]
        if alive_cols:
            alpha_columns = alive_cols
            normalized = {k: v for k, v in normalized.items() if k in alpha_columns}

    # --- Compute quality metrics for each alpha via decoupled measurement engine (P0-4) ---
    qualities = measure_ic(normalized, fwd_ret, ic_window=ic_window, ic_stride=ic_stride)

    # --- Compute weights via decoupled allocation engine (P0-4 & P0-2) ---
    weights = allocate_weights(
        qualities,
        regime=regime,
        decay_health=decay_health,
        min_ir=min_ir,
        shrinkage=shrinkage,
        ic_window=ic_window,
        meta_adjustments=True,
        weighting=weighting,
    )

    # --- Minimum active alpha guard ---
    # If fewer than 3 alphas have non-zero weight, the ensemble lacks diversification.
    active_alphas = [name for name, w in weights.items() if w > 0.0]
    if len(active_alphas) < 3:
        log.warning(
            "Ensemble diversification warning: only %d alphas with non-zero weight "
            "(%s). Composite signal may be unreliable.",
            len(active_alphas), active_alphas,
        )

    # Assign weights to quality objects
    for name, w in weights.items():
        qualities[name].weight = w

    # --- Compute composite signal ---
    composite_raw = pd.Series(0.0, index=df.index)
    for col, norm_series in normalized.items():
        w = weights.get(col, 0.0)
        composite_raw = composite_raw.add(norm_series * w, fill_value=0.0)

    # Z-score the composite itself
    composite = zscore_normalize(composite_raw)

    # --- Overall quality ---
    n_used = sum(1 for q in qualities.values() if q.ic_ir > 0)
    ic_mean_overall = sum(
        q.ic_mean * q.weight for q in qualities.values() if q.weight > 0
    )

    return EnsembleResult(
        composite=composite,
        composite_raw=composite_raw,
        alpha_weights=weights,
        alpha_qualities=qualities,
        n_alphas_used=n_used,
        ic_mean_overall=ic_mean_overall,
    )


# ---------------------------------------------------------------------------
# Signal interpretation helpers
# ---------------------------------------------------------------------------

def interpret_composite(
    result: EnsembleResult,
    current_idx: int = -1,
    threshold_entry: float = 1.0,
    threshold_exit: float = 0.0,
) -> Dict[str, any]:
    """Interpret the composite signal at a given bar index.

    Returns a dict with:
      - signal: "LONG", "SHORT", "NEUTRAL"
      - strength: float (absolute composite value)
      - confidence: "HIGH" / "MEDIUM" / "LOW"
      - contributing_alphas: list of (name, weight, direction) sorted by contribution
      - quality_report: summary text
    """
    comp = result.composite.iloc[current_idx]
    raw = result.composite_raw.iloc[current_idx]

    if comp > threshold_entry:
        signal = "LONG"
    elif comp < -threshold_entry:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    strength = abs(comp)
    if strength > 2.0:
        confidence = "HIGH"
    elif strength > 1.0:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Top contributing alphas
    contributions = []
    for name, w in sorted(result.alpha_weights.items(), key=lambda x: -abs(x[1])):
        q = result.alpha_qualities.get(name)
        if q:
            contributions.append({
                "alpha": name,
                "weight": w,
                "ic_ir": q.ic_ir,
                "direction": "BULL" if w > 0 else "BEAR",
            })

    # Quality summary
    n_strong = sum(
        1 for q in result.alpha_qualities.values() if abs(q.ic_ir) > 0.5
    )
    quality_report = (
        f"Ensemble: {result.n_alphas_used}/{len(result.alpha_weights)} alphas active, "
        f"{n_strong} with |IC_IR|>0.5, overall IC={result.ic_mean_overall:.4f}"
    )

    return {
        "signal": signal,
        "strength": strength,
        "confidence": confidence,
        "contributing_alphas": contributions,
        "quality_report": quality_report,
        "composite_value": comp,
        "raw_value": raw,
    }


def format_quality_report(result: EnsembleResult) -> str:
    """Format a human-readable quality report for all alphas."""
    lines = [
        "=" * 70,
        "ALPHA ENSEMBLE QUALITY REPORT",
        "=" * 70,
        f"Alphas used: {result.n_alphas_used}/{len(result.alpha_weights)}",
        f"Overall IC:  {result.ic_mean_overall:.4f}",
        "",
        f"{'Alpha':<12} {'Weight':>8} {'IC_Mean':>8} {'IC_IR':>8} {'IC+%':>7} "
        f"{'Turnover':>9} {'HL':>5} {'Stable':>7}",
        "-" * 70,
    ]

    for name, q in sorted(
        result.alpha_qualities.items(),
        key=lambda x: -abs(x[1].weight),
    ):
        lines.append(
            f"{q.name:<12} {q.weight:>8.3f} {q.ic_mean:>8.4f} {q.ic_ir:>8.3f} "
            f"{q.ic_positive_pct:>6.1%} {q.turnover:>9.4f} {q.half_life:>5d} "
            f"{q.stability:>7.3f}"
        )

    lines.append("=" * 70)
    lines.append("Legend: IC_IR = IC/Stdev(IC), IC+% = %% positive IC,")
    lines.append("        HL = half-life (bars), Stable = 1st-half/2nd-half IC correlation")
    return "\n".join(lines)
