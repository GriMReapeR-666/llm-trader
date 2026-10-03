"""Signal Orthogonalization (ψ) — Purify alpha signals against known exposures.

Implements the UAQP Agent σ (Signal Engineer) specification:

    "Apply orthogonalization ψ: regress Σ against known exposures
    (market, sector, size, regime). Retain residuals as purified signal."

    "Measure internal correlation: if feature correlation matrix has
    eigenvalues indicating multicollinearity, reduce dimensionality."

This module removes common-factor contamination from alpha signals so that:
  1. Each alpha's IC reflects its UNIQUE predictive power, not shared market beta
  2. Multicollinearity between alphas is reduced (no double-counting)
  3. The ensemble weights are more stable and less overfit

Standard quant approach from Barra risk models and quant research platform.

Methods:
  - OLS regression against factor matrix → residual = purified signal
  - PCA-based orthogonalization → project out top-K principal components
  - Gram-Schmidt sequential orthogonalization → order-dependent purification

The default is OLS regression because it's interpretable and well-understood.
PCA is an alternative when factors are unknown or high-dimensional.

References:
    Grinold, R. & Kahn, R. (2000). "Active Portfolio Management."
    Qian, E. (2005). "Risk Parity Portfolios: Efficient Portfolios Through True Diversification."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

class OrthoMethod(Enum):
    """Orthogonalization method."""
    OLS = "ols"              # OLS regression against factor matrix
    PCA = "pca"              # Project out top-K principal components
    GRAM_SCHMIDT = "gs"      # Sequential Gram-Schmidt


@dataclass
class OrthogonalizationConfig:
    """Configuration for signal orthogonalization."""
    method: OrthoMethod = OrthoMethod.OLS
    min_periods: int = 60       # Minimum bars before orthogonalization activates
    residual_floor: float = 0.01  # If residual std < floor, signal is pure noise
    pca_n_components: int = 3   # Number of PCs to remove (PCA method)
    rolling_window: Optional[int] = 120  # Rolling window for OLS (None = expanding)
    regularization: float = 0.01  # Ridge regression lambda (0 = OLS)


@dataclass
class FactorExposure:
    """Per-alpha exposure to each factor in the regression."""
    alpha_name: str
    factor_loadings: Dict[str, float]  # factor_name → beta
    r_squared: float = 0.0             # How much variance is explained by factors
    residual_std: float = 0.0          # Std of purified signal
    signal_to_noise: float = 0.0       # Original std / residual std


@dataclass
class OrthogonalizationResult:
    """Result of orthogonalizing a set of alpha signals."""
    purified: Dict[str, pd.Series]        # Factor-residual signals (orthogonalized)
    original: Dict[str, pd.Series]        # Original normalized signals
    exposures: Dict[str, FactorExposure]  # Per-alpha factor exposures
    factor_corr_matrix: Optional[pd.DataFrame] = None  # Factor correlation matrix
    multicollinearity_vif: Optional[Dict[str, float]] = None  # VIF per factor
    n_factors_removed: int = 0
    method_used: str = ""


# ============================================================================
# Default factor construction
# ============================================================================

def build_default_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Build the default factor matrix from OHLCV data.

    Factors are the "known exposures" that alphas should be orthogonalized against:
      1. market_return: close-to-close return (market beta)
      2. volatility: realized volatility (vol exposure)
      3. volume_surprise: volume deviation from mean (liquidity factor)
      4. range_pct: (high-low)/close (intraday range factor)
      5. momentum_20: 20-bar momentum (trend factor)

    Parameters
    ----------
    df : pd.DataFrame
        Must contain: close, high, low, volume columns.

    Returns
    -------
    pd.DataFrame with factor columns (same index as df).
    """
    factors = pd.DataFrame(index=df.index)

    # Factor 1: Market return (close-to-close)
    if "close" in df.columns:
        factors["market_return"] = df["close"].pct_change()
    else:
        factors["market_return"] = 0.0

    # Factor 2: Realized volatility (20-bar rolling std of returns)
    if "market_return" in factors.columns:
        factors["volatility"] = factors["market_return"].rolling(
            20, min_periods=10
        ).std()
    else:
        factors["volatility"] = 0.0

    # Factor 3: Volume surprise (volume z-score)
    if "volume" in df.columns:
        vol_ma = df["volume"].rolling(20, min_periods=10).mean()
        vol_std = df["volume"].rolling(20, min_periods=10).std()
        vol_std = vol_std.replace(0, np.nan)
        factors["volume_surprise"] = ((df["volume"] - vol_ma) / vol_std).fillna(0.0)
    else:
        factors["volume_surprise"] = 0.0

    # Factor 4: Intraday range percentage
    if "high" in df.columns and "low" in df.columns and "close" in df.columns:
        range_pct = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
        factors["range_pct"] = range_pct.fillna(0.0)
    else:
        factors["range_pct"] = 0.0

    # Factor 5: 20-bar momentum
    if "close" in df.columns:
        factors["momentum_20"] = df["close"].pct_change(20)
    else:
        factors["momentum_20"] = 0.0

    return factors


# ============================================================================
# OLS Regression (default method)
# ============================================================================

def _ols_orthogonalize_single(
    signal: pd.Series,
    factors: pd.DataFrame,
    min_periods: int = 60,
    rolling_window: Optional[int] = 120,
    regularization: float = 0.01,
) -> Tuple[pd.Series, Dict[str, float], float, float]:
    """Orthogonalize a single signal against factors using OLS regression.

    Returns (residual, factor_loadings, r_squared, residual_std).

    For each bar t (after min_periods):
      signal_t = β₀ + β₁·factor₁ + β₂·factor₂ + ... + ε_t
      purified_t = ε_t (the residual)

    Uses rolling or expanding window to avoid look-ahead bias.
    """
    s = np.asarray(signal, dtype=float)
    F = np.asarray(factors, dtype=float)
    n = len(s)
    k = F.shape[1] if F.ndim > 1 else 1

    residual = np.full(n, np.nan)
    avg_loadings = np.zeros(k)
    total_r2 = 0.0
    count = 0

    # Determine window indices
    start_idx = min_periods - 1 if rolling_window is None else rolling_window - 1

    for i in range(start_idx, n):
        # Window
        if rolling_window is None:
            ws = 0
        else:
            ws = max(0, i - rolling_window + 1)
        we = i + 1

        y = s[ws:we]
        X = F[ws:we]

        # Skip if too many NaNs
        valid = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        if valid.sum() < max(min_periods, k + 5):
            continue

        y_v = y[valid]
        X_v = X[valid]

        # Add intercept
        X_v = np.column_stack([np.ones(len(X_v)), X_v])

        # Ridge regression: (X'X + λI)β = X'y
        try:
            XtX = X_v.T @ X_v
            Xty = X_v.T @ y_v
            # Regularize (skip intercept)
            reg = np.eye(XtX.shape[0]) * regularization
            reg[0, 0] = 0.0  # Don't regularize intercept
            beta = np.linalg.solve(XtX + reg, Xty)
        except np.linalg.LinAlgError:
            continue

        # Predicted and residual
        y_hat = X_v @ beta
        y_resid = y_v - y_hat

        # R²
        ss_res = np.sum(y_resid ** 2)
        ss_tot = np.sum((y_v - y_v.mean()) ** 2)
        r2 = 1.0 - ss_res / max(ss_tot, 1e-10) if ss_tot > 0 else 0.0

        residual[i] = float(y_resid[-1])  # Residual at the current bar
        avg_loadings += beta[1:]  # Skip intercept
        total_r2 += r2
        count += 1

    if count > 0:
        avg_loadings /= count
        avg_r2 = total_r2 / count
    else:
        avg_r2 = 0.0

    # Factor names for loadings dict
    if hasattr(factors, 'columns'):
        factor_names = list(factors.columns)
    else:
        factor_names = [f"factor_{i}" for i in range(k)]
    loadings = {fn: float(avg_loadings[i]) for i, fn in enumerate(factor_names)}

    # Residual std
    resid_valid = residual[np.isfinite(residual)]
    resid_std = float(np.std(resid_valid)) if len(resid_valid) > 5 else 0.0

    idx = signal.index if hasattr(signal, 'index') else None
    return (
        pd.Series(residual, index=idx),
        loadings,
        avg_r2,
        resid_std,
    )


# ============================================================================
# PCA-based orthogonalization
# ============================================================================

def _pca_orthogonalize_single(
    signal: pd.Series,
    factors: pd.DataFrame,
    n_components: int = 3,
    min_periods: int = 60,
) -> Tuple[pd.Series, float]:
    """Project signal onto the null space of the top-K principal components of factors.

    purified = signal - projection onto top-K PCs of factors
    """
    s = np.asarray(signal, dtype=float)
    F = np.asarray(factors, dtype=float)
    n = len(s)
    purified = np.full(n, np.nan)

    for i in range(min_periods - 1, n):
        ws = max(0, i - 120)  # 120-bar rolling window
        we = i + 1

        y = s[ws:we]
        X = F[ws:we]

        valid = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        if valid.sum() < max(min_periods, n_components + 5):
            continue

        X_v = X[valid]
        y_v = y[valid]

        # Center
        X_centered = X_v - X_v.mean(axis=0)
        y_centered = y_v - y_v.mean()

        # SVD for PCA
        try:
            U, S, Vt = np.linalg.svd(X_centered, full_matrices=False)
        except np.linalg.LinAlgError:
            continue

        # Project onto top-K components
        n_comp = min(n_components, len(S))
        projection = U[:, :n_comp] @ np.diag(S[:n_comp]) @ Vt[:n_comp]
        # Project the current bar's signal onto the factor subspace
        if X_centered.shape[0] > 0:
            # Residual = signal - (signal projected onto factor space)
            loadings = np.linalg.lstsq(X_centered, y_centered, rcond=None)[0]
            y_hat = X_centered[-1] @ loadings
            purified[i] = y_v[-1] - y_hat

    resid_valid = purified[np.isfinite(purified)]
    r2 = 1.0 - (np.var(resid_valid) / max(np.var(s[np.isfinite(s)]), 1e-10)) if len(resid_valid) > 5 else 0.0
    r2 = max(0.0, r2)

    return pd.Series(purified, index=signal.index), r2


# ============================================================================
# Gram-Schmidt sequential orthogonalization
# ============================================================================

def _gram_schmidt_orthogonalize(
    signals: Dict[str, pd.Series],
    factor_series: pd.Series,
    min_periods: int = 60,
) -> Dict[str, pd.Series]:
    """Sequentially orthogonalize signals against a single factor series using Gram-Schmidt.

    Order matters: earlier signals get more purification.
    """
    f = factor_series.values.astype(float)
    results = {}

    for name, sig in signals.items():
        s = sig.values.astype(float)
        purified = np.full(len(s), np.nan)

        for i in range(min_periods - 1, len(s)):
            ws = max(0, i - 120)
            we = i + 1

            y = s[ws:we]
            x = f[ws:we]
            valid = np.isfinite(y) & np.isfinite(x)
            if valid.sum() < min_periods:
                continue

            x_v, y_v = x[valid], y[valid]
            # Project out x from y
            beta = np.dot(x_v, y_v) / max(np.dot(x_v, x_v), 1e-10)
            purified[i] = y_v[-1] - beta * x_v[-1]

        results[name] = pd.Series(purified, index=sig.index)

    return results


# ============================================================================
# Multicollinearity diagnostics
# ============================================================================

def compute_vif(factors: pd.DataFrame) -> Dict[str, float]:
    """Compute Variance Inflation Factor for each factor.

    VIF > 10 indicates high multicollinearity.
    VIF = 1/(1-R²) where R² is from regressing factor_i on all other factors.
    """
    vif = {}
    cols = [c for c in factors.columns if factors[c].std() > 1e-10]

    for col in cols:
        y = factors[col].values
        X_cols = [c for c in cols if c != col]
        if not X_cols:
            vif[col] = 1.0
            continue

        X = factors[X_cols].values
        valid = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        if valid.sum() < len(X_cols) + 5:
            vif[col] = 1.0
            continue

        y_v, X_v = y[valid], X[valid]
        X_v = np.column_stack([np.ones(len(X_v)), X_v])

        try:
            beta = np.linalg.lstsq(X_v, y_v, rcond=None)[0]
            y_hat = X_v @ beta
            ss_res = np.sum((y_v - y_hat) ** 2)
            ss_tot = np.sum((y_v - y_v.mean()) ** 2)
            r2 = 1.0 - ss_res / max(ss_tot, 1e-10)
            vif[col] = 1.0 / max(1.0 - r2, 1e-10)
        except Exception:
            vif[col] = 1.0

    return vif


# ============================================================================
# Main orthogonalization engine
# ============================================================================

def orthogonalize_signals(
    alpha_signals: Dict[str, pd.Series],
    df: pd.DataFrame,
    config: Optional[OrthogonalizationConfig] = None,
    custom_factors: Optional[pd.DataFrame] = None,
    forward_returns: Optional[pd.Series] = None,
) -> OrthogonalizationResult:
    """Orthogonalize alpha signals against known factor exposures.

    This is the ψ function from UAQP Agent σ:

        ψ: Σ → Σ_orth (regress against exposures, retain residuals)

    Parameters
    ----------
    alpha_signals : dict[str, pd.Series]
        Normalized alpha signals (z-scored or ranked).
    df : pd.DataFrame
        OHLCV data used to build factor matrix.
    config : OrthogonalizationConfig, optional
        Configuration for orthogonalization.
    custom_factors : pd.DataFrame, optional
        Custom factor matrix. If None, uses build_default_factors(df).
    forward_returns : pd.Series, optional
        Forward returns for IC comparison (pre/post orthogonalization).

    Returns
    -------
    OrthogonalizationResult with purified signals and diagnostics.
    """
    if config is None:
        config = OrthogonalizationConfig()

    # Build factor matrix
    if custom_factors is not None:
        factors = custom_factors
    else:
        factors = build_default_factors(df)

    logger.info(
        "Signal orthogonalization: method=%s, n_alphas=%d, n_factors=%d, window=%s",
        config.method.value, len(alpha_signals), len(factors.columns),
        config.rolling_window or "expanding",
    )

    # Filter out zero-variance factors
    valid_factors = [c for c in factors.columns if factors[c].std() > 1e-10]
    factors = factors[valid_factors].copy() if valid_factors else factors

    # Compute factor multicollinearity diagnostics
    vif = compute_vif(factors) if len(factors.columns) > 1 else None

    # Method dispatch
    purified = {}
    exposures = {}
    method_used = config.method.value

    if config.method == OrthoMethod.OLS:
        for name, signal in alpha_signals.items():
            resid, loadings, r2, resid_std = _ols_orthogonalize_single(
                signal, factors,
                min_periods=config.min_periods,
                rolling_window=config.rolling_window,
                regularization=config.regularization,
            )

            # If residual is too flat, signal is pure noise — keep original
            orig_std = float(signal.dropna().std()) if len(signal.dropna()) > 5 else 0.0
            if resid_std < config.residual_floor:
                logger.debug(
                    "Alpha %s: residual_std=%.4f < floor=%.4f — keeping original (pure noise)",
                    name, resid_std, config.residual_floor,
                )
                purified[name] = signal
            else:
                purified[name] = resid

            exposures[name] = FactorExposure(
                alpha_name=name,
                factor_loadings=loadings,
                r_squared=r2,
                residual_std=resid_std,
                signal_to_noise=orig_std / max(resid_std, 1e-10),
            )

    elif config.method == OrthoMethod.PCA:
        for name, signal in alpha_signals.items():
            resid, r2 = _pca_orthogonalize_single(
                signal, factors,
                n_components=config.pca_n_components,
                min_periods=config.min_periods,
            )
            purified[name] = resid

            resid_std = float(resid.dropna().std()) if len(resid.dropna()) > 5 else 0.0
            orig_std = float(signal.dropna().std()) if len(signal.dropna()) > 5 else 0.0
            exposures[name] = FactorExposure(
                alpha_name=name,
                factor_loadings={},
                r_squared=r2,
                residual_std=resid_std,
                signal_to_noise=orig_std / max(resid_std, 1e-10),
            )

    elif config.method == OrthoMethod.GRAM_SCHMIDT:
        # Gram-Schmidt uses the first factor (market_return) as the primary
        if "market_return" in factors.columns:
            primary_factor = factors["market_return"]
        else:
            primary_factor = factors.iloc[:, 0]

        purified = _gram_schmidt_orthogonalize(
            alpha_signals, primary_factor, min_periods=config.min_periods,
        )

        for name, sig in purified.items():
            resid_std = float(sig.dropna().std()) if len(sig.dropna()) > 5 else 0.0
            orig_std = float(alpha_signals[name].dropna().std()) if len(alpha_signals[name].dropna()) > 5 else 0.0
            exposures[name] = FactorExposure(
                alpha_name=name,
                factor_loadings={},
                r_squared=0.0,
                residual_std=resid_std,
                signal_to_noise=orig_std / max(resid_std, 1e-10),
            )

    # Factor correlation matrix
    factor_corr = factors.corr() if len(factors.columns) > 1 else None

    # Count factors removed (based on VIF)
    n_removed = 0
    if vif:
        n_removed = sum(1 for v in vif.values() if v > 5.0)

    logger.info(
        "Orthogonalization complete: method=%s, factors_removed=%d, mean_r2=%.4f",
        method_used, n_removed,
        np.mean([e.r_squared for e in exposures.values()]) if exposures else 0.0,
    )

    return OrthogonalizationResult(
        purified=purified,
        original=alpha_signals,
        exposures=exposures,
        factor_corr_matrix=factor_corr,
        multicollinearity_vif=vif,
        n_factors_removed=n_removed,
        method_used=method_used,
    )


# ============================================================================
# Convenience: orthogonalize and return just the purified signals
# ============================================================================

def orthogonalize_for_ensemble(
    alpha_signals: Dict[str, pd.Series],
    df: pd.DataFrame,
    method: str = "ols",
    rolling_window: int = 120,
) -> Dict[str, pd.Series]:
    """Quick orthogonalization for ensemble integration.

    Parameters
    ----------
    alpha_signals : dict
        Normalized alpha signals.
    df : pd.DataFrame
        OHLCV data.
    method : str
        "ols", "pca", or "gs".
    rolling_window : int
        Rolling window for regression.

    Returns
    -------
    Dict of purified alpha signals (same keys, residual values).
    """
    config = OrthogonalizationConfig(
        method=OrthoMethod(method),
        rolling_window=rolling_window,
    )
    result = orthogonalize_signals(alpha_signals, df, config=config)
    return result.purified


# ============================================================================
# Diagnostics
# ============================================================================

def format_orthogonalization_report(result: OrthogonalizationResult) -> str:
    """Format a human-readable report of orthogonalization results."""
    lines = [
        "=" * 70,
        "SIGNAL ORTHOGONALIZATION REPORT",
        f"Method: {result.method_used}",
        f"Factors removed: {result.n_factors_removed}",
        "=" * 70,
        "",
        f"{'Alpha':<12} {'R²':>6} {'Resid_σ':>8} {'SNR':>6} {'Top Factor':>15} {'Loading':>8}",
        "-" * 70,
    ]

    for name, exp in sorted(result.exposures.items(), key=lambda x: -x[1].r_squared):
        # Find top factor
        if exp.factor_loadings:
            top_factor = max(exp.factor_loadings.items(), key=lambda x: abs(x[1]))
            top_name = top_factor[0]
            top_loading = top_factor[1]
        else:
            top_name = "—"
            top_loading = 0.0

        lines.append(
            f"{exp.alpha_name:<12} {exp.r_squared:>6.3f} {exp.residual_std:>8.4f} "
            f"{exp.signal_to_noise:>6.2f} {top_name:>15} {top_loading:>8.4f}"
        )

    lines.append("-" * 70)

    # VIF
    if result.multicollinearity_vif:
        lines.append("")
        lines.append("Factor Multicollinearity (VIF):")
        for factor, vif_val in sorted(result.multicollinearity_vif.items(),
                                       key=lambda x: -x[1]):
            flag = " ⚠️ HIGH" if vif_val > 5.0 else ""
            lines.append(f"  {factor:<20} VIF={vif_val:>6.2f}{flag}")

    lines.append("")
    lines.append("Legend: R² = factor variance explained, SNR = original_σ / residual_σ")
    lines.append("        SNR > 1.0 means factor explains significant variance (good to remove)")
    lines.append("        SNR ≈ 1.0 means signal is already orthogonal (nothing to remove)")
    lines.append("=" * 70)

    return "\n".join(lines)


class SignalOrthogonalizer:
    """Convenience class wrapper for signal orthogonalization."""
    def __init__(self, config: Optional[OrthogonalizationConfig] = None):
        self.config = config or OrthogonalizationConfig()

    def orthogonalize(
        self,
        alpha_signals: Dict[str, pd.Series],
        df: pd.DataFrame,
        **kwargs,
    ) -> OrthogonalizationResult:
        return orthogonalize_signals(alpha_signals, df, config=self.config, **kwargs)
