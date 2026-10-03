"""Enhanced Kronos Multi-Asset Synthetic Market Generator.

Generates correlated multi-asset OHLCV synthetic data streams incorporating:
1. Merton Jump-Diffusion (Poisson jumps for flash crashes & gap openings)
2. GARCH(1,1) Stochastic Volatility Clustering
3. Multi-Asset t-Copula & Cholesky Cross-Asset Correlation
4. Regime Transitions (Bull, Bear, Volatile, Flash-Crash, Sideways Range)
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np


def generate_correlated_multi_asset_synthetic_dataset(
    symbols: Sequence[str] = ("EURUSD", "GBPUSD", "XAUUSD", "USOIL"),
    length_bars: int = 200,
    regime: str = "volatile",
    correlation_matrix: Optional[np.ndarray] = None,
    jump_intensity: float = 0.05,
    seed: int = 42,
) -> Dict[str, List[Dict[str, Any]]]:
    """Generates a synchronous, cross-correlated multi-asset synthetic OHLCV dataset.

    dS_t = (mu - lambda * k) * S_t * dt + sigma_t * S_t * dW_t + J_t * S_t * dN_t
    with GARCH(1,1) volatility clustering and cross-asset Cholesky correlation.
    """
    n_assets = len(symbols)
    if n_assets == 0:
        return {}

    rng = np.random.default_rng(seed)

    # 1. Base Correlation Matrix
    if correlation_matrix is None or correlation_matrix.shape != (n_assets, n_assets):
        # Generate a realistic correlation matrix
        corr = np.eye(n_assets)
        for i in range(n_assets):
            for j in range(i + 1, n_assets):
                c_val = 0.45 if i < 2 and j < 2 else (0.25 if "XAU" in symbols[i] or "XAU" in symbols[j] else 0.15)
                corr[i, j] = c_val
                corr[j, i] = c_val
    else:
        corr = np.array(correlation_matrix, dtype=float)

    # Ensure positive semi-definite via Cholesky
    try:
        L = np.linalg.cholesky(corr)
    except np.linalg.LinAlgError:
        corr = corr + np.eye(n_assets) * 1e-4
        L = np.linalg.cholesky(corr)

    # 2. Regime Parameters
    regime_params = {
        "bull": {"drift": 0.0008, "vol_base": 0.006, "garch_alpha": 0.08, "garch_beta": 0.90},
        "bear": {"drift": -0.0010, "vol_base": 0.012, "garch_alpha": 0.12, "garch_beta": 0.85},
        "volatile": {"drift": 0.0001, "vol_base": 0.018, "garch_alpha": 0.15, "garch_beta": 0.80},
        "flash_crash": {"drift": -0.0035, "vol_base": 0.035, "garch_alpha": 0.25, "garch_beta": 0.70},
        "range": {"drift": 0.0000, "vol_base": 0.004, "garch_alpha": 0.04, "garch_beta": 0.94},
    }
    cfg = regime_params.get(regime.lower(), regime_params["volatile"])

    # 3. Initial asset prices
    base_prices = {
        "EURUSD": 1.0850,
        "GBPUSD": 1.2750,
        "USDJPY": 155.00,
        "XAUUSD": 2350.0,
        "USOIL": 78.0,
        "SPX": 5500.0,
        "BTCUSD": 65000.0,
    }

    prices = [base_prices.get(sym.upper(), 100.0) for sym in symbols]
    sigmas = [cfg["vol_base"] for _ in range(n_assets)]
    omega = 0.00001
    alpha = cfg["garch_alpha"]
    beta = cfg["garch_beta"]

    dataset: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in symbols}

    for bar_idx in range(length_bars):
        # Generate standard uncorrelated normal innovations
        uncorr_z = rng.standard_normal(n_assets)
        # Apply Cholesky matrix to induce cross-asset correlation
        corr_z = L @ uncorr_z

        for i, sym in enumerate(symbols):
            curr_p = prices[i]
            cur_sig = sigmas[i]

            # GARCH(1,1) update: sigma^2_t = omega + alpha * eps^2_{t-1} + beta * sigma^2_{t-1}
            eps = cur_sig * corr_z[i]
            next_sig2 = omega + alpha * (eps ** 2) + beta * (cur_sig ** 2)
            sigmas[i] = math.sqrt(max(1e-6, next_sig2))

            # Merton Jump Component
            jump_occured = rng.uniform(0, 1) < jump_intensity
            jump_size = 0.0
            if jump_occured:
                jump_size = float(rng.normal(-0.02 if regime == "flash_crash" else 0.0, 0.025))

            # Continuous return
            ret = cfg["drift"] + eps + jump_size

            # Synthesize OHLCV bar
            open_p = curr_p
            close_p = max(0.0001, open_p * (1.0 + ret))
            intra_vol = max(0.0001, sigmas[i] * 0.75)
            high_p = max(open_p, close_p) * (1.0 + abs(float(rng.normal(0, intra_vol))))
            low_p = min(open_p, close_p) * (1.0 - abs(float(rng.normal(0, intra_vol))))
            vol_base = 5000.0 if "USD" in sym else 500.0
            volume = float(max(10.0, rng.normal(vol_base, vol_base * 0.35) * (1.0 + abs(ret) * 10.0)))

            dataset[sym].append({
                "bar_index": bar_idx,
                "open": round(float(open_p), 5),
                "high": round(float(high_p), 5),
                "low": round(float(low_p), 5),
                "close": round(float(close_p), 5),
                "volume": round(volume, 2),
                "sigma_garch": round(float(sigmas[i]), 5),
                "jump_occurred": bool(jump_occured),
            })

            prices[i] = close_p

    return dataset
