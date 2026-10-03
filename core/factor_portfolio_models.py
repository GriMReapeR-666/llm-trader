"""Module 12: Factor Models, Portfolio Optimization & Microstructure Math.

Implements the remaining concepts from MASTER_SOURCE_OF_TRUTH §3-4:

1. S-Score — PCA residual normalization for stat arb entry/exit
2. Kyle's Lambda — Informed/uninformed trader decomposition
3. Ledoit-Wolf Shrinkage — Covariance matrix regularization
4. Black-Litterman — Bayesian portfolio allocation from investor views

All functions: execution_authority=False, advisory only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 1. S-Score (PCA Residual StatArb)
# ---------------------------------------------------------------------------

@dataclass
class SScoreResult:
    s_score: float
    signal: str  # "LONG", "SHORT", "NEUTRAL"
    half_life: float
    confidence: float
    residuals: Optional[List[float]] = None
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("residuals") is not None:
            d["residuals"] = [round(r, 6) for r in d["residuals"][:20]]
        return d


def calculate_s_score(
    asset_returns: Optional[List[float]] = None,
    factor_returns: Optional[List[float]] = None,
    loadings: Optional[List[float]] = None,
    entry_threshold: float = 1.5,
) -> SScoreResult:
    """S-Score: normalize PCA residual spread for stat arb entry/exit.

    After regressing asset returns against K eigen-factors:
        R_i = beta_i * F + epsilon_i
    Cumulate epsilon to get spread X_t = sum(epsilon_s)
    Fit OU: dX = kappa * (mu - X) dt + sigma dW
    S-score = (X_t - mu) / sigma

    Entry: s < -1.5 (buy stock, short factors)
    Exit:  s > -0.5 or s < -2.5 (stop loss)
    """
    if not asset_returns or not factor_returns or len(asset_returns) < 30:
        return SScoreResult(s_score=0.0, signal="NEUTRAL", half_life=0.0, confidence=0.0)

    r_i = np.array(asset_returns, dtype=float)
    r_f = np.array(factor_returns, dtype=float)
    n = min(len(r_i), len(r_f))
    r_i, r_f = r_i[:n], r_f[:n]

    # Regress asset returns against factor returns
    if loadings:
        beta = float(loadings[0]) if len(loadings) > 0 else 1.0
    else:
        # OLS: beta = Cov(r_i, r_f) / Var(r_f)
        var_f = float(np.var(r_f))
        beta = float(np.cov(r_i, r_f)[0, 1]) / var_f if var_f > 0 else 1.0

    # Residuals
    residuals = r_i - beta * r_f
    # Cumulative spread (mean of residuals = 0 for stationary spread)
    spread = np.cumsum(residuals)

    # OU parameter estimation via OLS on discretized OU
    # X_{t+1} - X_t = kappa * (mu - X_t) + sigma * epsilon
    if len(spread) < 10:
        return SScoreResult(s_score=0.0, signal="NEUTRAL", half_life=0.0, confidence=0.0)

    dx = np.diff(spread)
    x_lag = spread[:-1]
    # OLS: dx = a + b * x_lag
    x_mean = float(np.mean(x_lag))
    dx_mean = float(np.mean(dx))
    cov_xdx = float(np.mean((x_lag - x_mean) * (dx - dx_mean)))
    var_x = float(np.var(x_lag))

    b = cov_xdx / var_x if var_x > 0 else 0.0
    a = dx_mean - b * x_mean

    kappa = -b
    mu = -a / b if abs(b) > 1e-10 else 0.0

    if kappa <= 0:
        # Non-stationary spread — no mean reversion
        return SScoreResult(s_score=0.0, signal="NEUTRAL", half_life=float("inf"), confidence=0.0)

    half_life = math.log(2) / kappa
    sigma = float(np.std(dx - (a + b * x_lag)))
    sigma_eq = sigma / math.sqrt(2 * kappa) if kappa > 0 else 1.0

    # S-score
    current_spread = float(spread[-1])
    s = (current_spread - mu) / sigma_eq if sigma_eq > 0 else 0.0

    if s < -entry_threshold:
        signal = "LONG"
    elif s > entry_threshold:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    return SScoreResult(
        s_score=round(s, 3),
        signal=signal,
        half_life=round(half_life, 2),
        confidence=min(1.0, abs(s) / (entry_threshold * 2)),
        residuals=[round(float(r), 6) for r in residuals[-20:]],
    )


# ---------------------------------------------------------------------------
# 2. Kyle's Lambda (Price Impact)
# ---------------------------------------------------------------------------

@dataclass
class KylesLambdaResult:
    lambda_est: float  # Price impact per unit of order flow
    informed_flow_pct: float  # Estimated % of informed trading
    adverse_selection_cost: float
    market_depth: float  # Implied depth: 1/lambda
    regime: str  # "INFORMED_DOMINANT", "NOISE_DOMINANT", "BALANCED"
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_kyles_lambda(
    trade_prices: Optional[List[float]] = None,
    trade_volumes: Optional[List[float]] = None,
    trade_sides: Optional[List[str]] = None,
    window: int = 100,
) -> KylesLambdaResult:
    """Kyle (1985): price impact per unit of order flow.

    Lambda = Cov(dP, V) / Var(V)
    where dP = price change, V = signed volume (buy - sell)

    High lambda = thin market, informed traders dominate
    Low lambda = deep market, noise traders dominate
    """
    if not trade_prices or not trade_volumes or len(trade_prices) < 20:
        return KylesLambdaResult(
            lambda_est=0.0, informed_flow_pct=0.0, adverse_selection_cost=0.0,
            market_depth=float("inf"), regime="NOISE_DOMINANT",
        )

    prices = np.array(trade_prices, dtype=float)
    volumes = np.array(trade_volumes, dtype=float)
    n = min(len(prices), len(volumes))
    prices, volumes = prices[:n], volumes[:n]

    # Signed volume
    if trade_sides:
        sides = np.array([1.0 if str(s).lower() == "buy" else -1.0 for s in trade_sides[:n]])
        signed_vol = volumes * sides
    else:
        # Tick rule
        price_diffs = np.diff(prices, prepend=prices[0])
        sides = np.sign(price_diffs)
        signed_vol = volumes * sides

    # Price changes
    dP = np.diff(prices, prepend=prices[0])

    # Use window
    w = min(window, len(dP))
    dP_w = dP[-w:]
    sv_w = signed_vol[-w:]

    # Kyle's lambda: Cov(dP, signed_vol) / Var(signed_vol)
    var_sv = float(np.var(sv_w))
    if var_sv <= 0:
        return KylesLambdaResult(
            lambda_est=0.0, informed_flow_pct=0.0, adverse_selection_cost=0.0,
            market_depth=float("inf"), regime="NOISE_DOMINANT",
        )

    cov = float(np.cov(dP_w, sv_w)[0, 1])
    lam = cov / var_sv

    # Informed flow estimation: E[lambda] = sigma_u / (sigma_u + sigma_v)
    # Approximate: higher lambda → more informed flow
    price_vol = float(np.std(dP_w))
    vol_vol = float(np.std(sv_w))
    informed_pct = abs(lam) * vol_vol / max(price_vol, 1e-10)
    informed_pct = min(1.0, max(0.0, informed_pct))

    # Adverse selection cost: ~lambda * expected signed volume per trade
    avg_vol = float(np.mean(np.abs(sv_w)))
    adv_selection = abs(lam) * avg_vol * 0.5  # Half for passive fill

    # Market depth: 1/lambda (volume needed for 1 unit price change)
    depth = 1.0 / abs(lam) if abs(lam) > 1e-10 else float("inf")

    if informed_pct > 0.6:
        regime = "INFORMED_DOMINANT"
    elif informed_pct < 0.3:
        regime = "NOISE_DOMINANT"
    else:
        regime = "BALANCED"

    return KylesLambdaResult(
        lambda_est=round(float(lam), 8),
        informed_flow_pct=round(informed_pct, 3),
        adverse_selection_cost=round(adv_selection, 6),
        market_depth=round(depth, 2) if depth != float("inf") else float("inf"),
        regime=regime,
    )


# ---------------------------------------------------------------------------
# 3. Ledoit-Wolf Shrinkage Covariance
# ---------------------------------------------------------------------------

@dataclass
class LedoitWolfResult:
    shrinkage_intensity: float  # alpha in [0, 1]
    eigenvalues_before: List[float]
    eigenvalues_after: List[float]
    condition_number_before: float
    condition_number_after: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["eigenvalues_before"] = [round(e, 4) for e in d["eigenvalues_before"][:10]]
        d["eigenvalues_after"] = [round(e, 4) for e in d["eigenvalues_after"][:10]]
        return d


def ledoit_wolf_shrinkage(
    returns_matrix: Optional[List[List[float]]] = None,
) -> LedoitWolfResult:
    """Ledoit-Wolf (2004) Oracle Approximating Shrinkage for covariance estimation.

    Shrinks sample covariance toward a structured target:
        Sigma_shrunk = (1 - alpha) * S + alpha * mu * I
    where S = sample covariance, mu = average eigenvalue, I = identity.

    Critical when N (assets) > T (observations).
    """
    if not returns_matrix or len(returns_matrix) < 2 or len(returns_matrix[0]) < 2:
        return LedoitWolfResult(
            shrinkage_intensity=0.0, eigenvalues_before=[], eigenvalues_after=[],
            condition_number_before=0.0, condition_number_after=0.0,
        )

    R = np.array(returns_matrix, dtype=float)
    n_obs, n_assets = R.shape

    if n_obs < n_assets:
        # Not enough observations — maximum shrinkage
        return LedoitWolfResult(
            shrinkage_intensity=1.0, eigenvalues_before=[], eigenvalues_after=[],
            condition_number_before=float("inf"), condition_number_after=1.0,
        )

    # Sample covariance
    S = np.cov(R, rowvar=False)
    if S.ndim == 0:
        S = S.reshape(1, 1)

    # Ledoit-Wolf optimal shrinkage intensity
    # Simplified analytical solution (Ledoit & Wolf 2004, Proposition 2)
    T = n_obs
    N = n_assets

    # Frobenius norm terms
    sample_var = np.var(S)
    target = np.trace(S) / N * np.eye(N)
    target_var = np.var(target)

    # Optimal alpha
    delta = S - target
    sum_sq = np.sum(delta ** 2)

    # Gamma (estimator of covariance of S)
    R_centered = R - np.mean(R, axis=0)
    gamma = np.zeros((N, N))
    for t in range(T):
        rt = R_centered[t:t+1, :]
        gamma += (rt.T @ rt - S) ** 2
    gamma /= T

    alpha = min(1.0, max(0.0, np.sum(gamma) / max(sum_sq, 1e-10)))

    # Shrunk covariance
    S_shrunk = (1 - alpha) * S + alpha * target

    # Eigenvalue comparison
    eig_before = np.sort(np.linalg.eigvalsh(S))[::-1]
    eig_after = np.sort(np.linalg.eigvalsh(S_shrunk))[::-1]

    cond_before = float(eig_before[0] / max(eig_before[-1], 1e-10))
    cond_after = float(eig_after[0] / max(eig_after[-1], 1e-10))

    return LedoitWolfResult(
        shrinkage_intensity=round(float(alpha), 4),
        eigenvalues_before=[round(float(e), 4) for e in eig_before],
        eigenvalues_after=[round(float(e), 4) for e in eig_after],
        condition_number_before=round(cond_before, 2),
        condition_number_after=round(cond_after, 2),
    )


# ---------------------------------------------------------------------------
# 4. Black-Litterman Portfolio Allocation
# ---------------------------------------------------------------------------

@dataclass
class BlackLittermanResult:
    equilibrium_returns: List[float]
    posterior_returns: List[float]
    posterior_weights: List[str]  # LONG/SHORT/NEUTRAL per asset
    allocation_confidence: float
    input_views_detected: int
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["equilibrium_returns"] = [round(r, 6) for r in d["equilibrium_returns"]]
        d["posterior_returns"] = [round(r, 6) for r in d["posterior_returns"]]
        return d


def black_litterman_allocation(
    cov_matrix: Optional[List[List[float]]] = None,
    market_cap_weights: Optional[List[float]] = None,
    risk_aversion: float = 2.5,
    view_matrix: Optional[List[List[float]]] = None,
    view_returns: Optional[List[float]] = None,
    view_confidences: Optional[List[float]] = None,
    tau: float = 0.05,
) -> BlackLittermanResult:
    """Black-Litterman (1992): Bayesian portfolio allocation from investor views.

    Step 1: Market-implied equilibrium returns: Pi = delta * Sigma * w_mkt
    Step 2: Combine with investor views via Bayesian posterior:
        E[R] = [(tau * Sigma)^-1 + P' * Omega^-1 * P]^-1 * [(tau * Sigma)^-1 * Pi + P' * Omega^-1 * Q]
    """
    if not cov_matrix or not market_cap_weights:
        return BlackLittermanResult(
            equilibrium_returns=[], posterior_returns=[], posterior_weights=[],
            allocation_confidence=0.0, input_views_detected=0,
        )

    Sigma = np.array(cov_matrix, dtype=float)
    w_mkt = np.array(market_cap_weights, dtype=float)
    N = len(w_mkt)

    # Step 1: Market-implied equilibrium returns
    Pi = risk_aversion * Sigma @ w_mkt

    if view_matrix is None or view_returns is None or len(view_returns) == 0:
        # No views → posterior = prior (equilibrium)
        weights_eq = w_mkt / max(np.sum(w_mkt), 1e-10)
        return BlackLittermanResult(
            equilibrium_returns=[round(float(r), 6) for r in Pi],
            posterior_returns=[round(float(r), 6) for r in Pi],
            posterior_weights=["LONG" if w > 0.01 else "SHORT" if w < -0.01 else "NEUTRAL" for w in weights_eq],
            allocation_confidence=0.5,
            input_views_detected=0,
        )

    P = np.array(view_matrix, dtype=float)
    Q = np.array(view_returns, dtype=float)
    K = len(Q)

    # Omega: uncertainty in views (diagonal, proportional to view variance)
    if view_confidences:
        omega_diag = [(1 - c) / max(c, 0.01) for c in view_confidences]
    else:
        omega_diag = [1.0] * K
    Omega = np.diag(omega_diag)

    # Step 2: Posterior
    tau_Sigma = tau * Sigma
    try:
        tau_Sigma_inv = np.linalg.inv(tau_Sigma)
        Omega_inv = np.linalg.inv(Omega)
    except np.linalg.LinAlgError:
        tau_Sigma_inv = np.linalg.pinv(tau_Sigma)
        Omega_inv = np.linalg.pinv(Omega)

    M = tau_Sigma_inv + P.T @ Omega_inv @ P
    try:
        M_inv = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        M_inv = np.linalg.pinv(M)

    posterior_returns = M_inv @ (tau_Sigma_inv @ Pi + P.T @ Omega_inv @ Q)

    # Derive weights from posterior returns (simplified: proportional to return/risk)
    eigvals = np.linalg.eigvalsh(Sigma)
    avg_var = float(np.mean(eigvals))
    weights = posterior_returns / max(avg_var * risk_aversion, 1e-10)
    # Normalize
    w_sum = float(np.sum(np.abs(weights)))
    if w_sum > 0:
        weights = weights / w_sum

    return BlackLittermanResult(
        equilibrium_returns=[round(float(r), 6) for r in Pi],
        posterior_returns=[round(float(r), 6) for r in posterior_returns],
        posterior_weights=["LONG" if w > 0.01 else "SHORT" if w < -0.01 else "NEUTRAL" for w in weights],
        allocation_confidence=min(1.0, 0.5 + K * 0.1),
        input_views_detected=K,
    )
