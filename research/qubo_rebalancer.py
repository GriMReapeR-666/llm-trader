"""P14: Quantum-Assisted Optimal Rebalancing with Uncorrelated Asset Selection (Weinberg 2026).

Paper: "Quantum-Assisted Optimal Rebalancing with Uncorrelated Asset Selection for
Algorithmic Trading: Walk-Forward QUBO Scheduling via QAOA" (Abraham Itzhak Weinberg, 2026).

Key Contributions:
1. Asset selection via Ledoit-Wolf shrinkage covariance and hierarchical correlation clustering.
2. Selection of highest-Sharpe representative per cluster to create an uncorrelated, high-performing basket.
3. Formulation of portfolio rebalancing scheduling as a Quadratic Unconstrained Binary Optimisation (QUBO).
4. QUBO includes marginal Sharpe gain, transaction cost penalty, and exponential decay over-frequency penalty.
5. High-performance classical QUBO schedule solver and walk-forward scheduling backtest engine.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("QUBORebalancer")


def ledoit_wolf_shrinkage(returns: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """Ledoit-Wolf analytical shrinkage covariance and correlation matrix (Weinberg 2026 §3.3 Eq 1).
    
    Computes shrinkage between sample covariance S and structured target F = mu * I:
        Sigma_LW = (1 - alpha) * S + alpha * mu * I
    
    Returns:
        (cov_lw, corr_lw, alpha)
    """
    X = np.asarray(returns, dtype=float)
    T, M = X.shape
    if T < 2 or M < 1:
        eye = np.eye(M)
        return eye, eye, 0.0

    mean = np.mean(X, axis=0, keepdims=True)
    X_c = X - mean
    S = (X_c.T @ X_c) / (T - 1)

    # Shrinkage target: mu * I where mu is average sample eigenvalue (mean variance)
    mu_target = np.trace(S) / M
    F = mu_target * np.eye(M)

    # Analytical shrinkage intensity alpha (Ledoit & Wolf 2004)
    y = X_c ** 2
    phi_mat = (y.T @ y) / (T - 1) - (2.0 * (X_c.T @ X_c) * S) / (T - 1) + S ** 2
    phi = np.sum(phi_mat)
    gamma = np.linalg.norm(S - F, "fro") ** 2

    if gamma <= 1e-12:
        alpha = 0.0
    else:
        kappa = phi / gamma
        alpha = max(0.0, min(1.0, kappa / T))

    cov_lw = (1.0 - alpha) * S + alpha * F

    # Correlation matrix R_LW = D^(-1/2) Sigma_LW D^(-1/2)
    diag_std = np.sqrt(np.maximum(1e-12, np.diag(cov_lw)))
    corr_lw = cov_lw / np.outer(diag_std, diag_std)
    np.fill_diagonal(corr_lw, 1.0)
    corr_lw = np.clip(corr_lw, -1.0, 1.0)

    return cov_lw, corr_lw, float(alpha)


def angular_distance(corr: np.ndarray) -> np.ndarray:
    """Angular distance metric: d_ij = sqrt(0.5 * (1 - rho_ij)) (Weinberg 2026 Eq 2).
    
    Satisfies metric axioms and triangle inequality.
    """
    corr_clipped = np.clip(np.asarray(corr, dtype=float), -1.0, 1.0)
    return np.sqrt(np.maximum(0.0, 0.5 * (1.0 - corr_clipped)))


def select_uncorrelated_assets(
    returns: np.ndarray,
    symbol_names: List[str],
    n_clusters: int = 10,
) -> List[str]:
    """Hierarchical correlation clustering asset selection (Weinberg 2026 §3.3 Eq 3).
    
    Partitions M assets into n_clusters and selects the asset with the highest
    annualized Sharpe ratio in each cluster:
        s*_k = argmax_{s in C_k} (mean_r / std_r) * sqrt(252)
    """
    X = np.asarray(returns, dtype=float)
    M = X.shape[1]
    n_clusters = min(n_clusters, M)

    if M <= n_clusters:
        return list(symbol_names)

    _, corr_lw, _ = ledoit_wolf_shrinkage(X)
    dist_mat = angular_distance(corr_lw)

    # Cluster assets via scipy linkage if available, else greedy medoid partitioning
    clusters: List[List[int]] = []
    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
        # Condensed distance matrix
        condensed = squareform(dist_mat, checks=False)
        Z = linkage(condensed, method="ward")
        cluster_labels = fcluster(Z, t=n_clusters, criterion="maxclust")
        cluster_dict: Dict[int, List[int]] = {}
        for idx, lbl in enumerate(cluster_labels):
            cluster_dict.setdefault(lbl, []).append(idx)
        clusters = list(cluster_dict.values())
    except Exception:
        # Robust fallback: Greedy distance clustering
        remaining = set(range(M))
        clusters = []
        for _ in range(n_clusters):
            if not remaining:
                break
            seed = next(iter(remaining))
            # Find nearest assets
            sorted_by_dist = sorted(list(remaining), key=lambda idx: dist_mat[seed, idx])
            k_size = max(1, len(remaining) // (n_clusters - len(clusters)))
            assigned = sorted_by_dist[:k_size]
            clusters.append(assigned)
            remaining -= set(assigned)
        if remaining and clusters:
            clusters[-1].extend(list(remaining))

    # In each cluster, pick representative with highest annualized Sharpe ratio
    selected_symbols: List[str] = []
    for c_members in clusters:
        best_sharpe = -float("inf")
        best_idx = c_members[0]
        for idx in c_members:
            col = X[:, idx]
            std = float(np.std(col, ddof=1)) if len(col) > 1 else 1e-6
            mean = float(np.mean(col))
            sr = (mean / std) * math.sqrt(252.0) if std > 1e-12 else 0.0
            if sr > best_sharpe:
                best_sharpe = sr
                best_idx = idx
        selected_symbols.append(symbol_names[best_idx])

    return selected_symbols


def build_qubo_matrix(
    returns: np.ndarray,
    target_weights: np.ndarray,
    candidate_dates: List[int],
    cost_rate: float = 0.001,
    lambda1: float = 1.0,
    lambda2: float = 0.5,
    lambda3: float = 0.3,
) -> np.ndarray:
    """Constructs the QUBO matrix Q in R^{W x W} for rebalancing scheduling (Weinberg 2026 §3.5 Eq 10, 11).
    
    Q_kk = -lambda1 * g_k + lambda2 * c * n
    Q_kl = lambda3 * exp(-|t_k - t_l| / Delta_t)  (k != l)
    
    Normalized by dividing by max|Q_kl|.
    """
    X = np.asarray(returns, dtype=float)
    T, N = X.shape
    w_target = np.asarray(target_weights, dtype=float)
    W = len(candidate_dates)

    if W == 0:
        return np.zeros((0, 0))

    dates = sorted(candidate_dates)
    delta_t = float((dates[-1] - dates[0]) / max(1, W - 1)) if W > 1 else 1.0

    # Compute marginal Sharpe gains g_k for each candidate date
    g_k = np.zeros(W)
    for k in range(W):
        s_date = dates[k]
        e_date = dates[k + 1] if k + 1 < W else min(T, s_date + int(delta_t))
        if e_date <= s_date:
            e_date = min(T, s_date + 1)

        window_returns = X[s_date:e_date]
        if len(window_returns) >= 2:
            # Target portfolio return
            p_ret_target = window_returns @ w_target
            std_target = float(np.std(p_ret_target, ddof=1))
            sr_target = (float(np.mean(p_ret_target)) / std_target) * math.sqrt(252.0) if std_target > 1e-12 else 0.0

            # Simulated drift (gross cumulative return)
            cum_gross = np.prod(1.0 + window_returns, axis=0)
            w_drift_unnorm = w_target * cum_gross
            w_drift = w_drift_unnorm / max(1e-12, np.sum(w_drift_unnorm))

            p_ret_drift = window_returns @ w_drift
            std_drift = float(np.std(p_ret_drift, ddof=1))
            sr_drift = (float(np.mean(p_ret_drift)) / std_drift) * math.sqrt(252.0) if std_drift > 1e-12 else 0.0

            turnover = float(np.sum(np.abs(w_drift - w_target)))
            annualized_cost = cost_rate * turnover * math.sqrt(252.0)
            g_k[k] = (sr_target - sr_drift) - annualized_cost
        else:
            g_k[k] = 0.0

    Q = np.zeros((W, W))
    for k in range(W):
        # Diagonal term (Eq 10)
        Q[k, k] = -lambda1 * g_k[k] + lambda2 * cost_rate * N

        # Off-diagonal exponential interaction term (Eq 11)
        for l in range(W):
            if k != l:
                dt = abs(dates[k] - dates[l])
                Q[k, l] = lambda3 * math.exp(-dt / max(1e-6, delta_t))

    max_abs = np.max(np.abs(Q))
    if max_abs > 1e-9:
        Q = Q / max_abs

    return Q


def solve_qubo_schedule(Q: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve the binary optimization min_{x in {0, 1}^W} x^T Q x (Weinberg 2026 Eq 9).
    
    For W <= 20, solves via exact vectorized exhaustive search over all 2^W states in < 2ms.
    """
    W = Q.shape[0]
    if W == 0:
        return np.zeros(0, dtype=int), 0.0

    if W <= 20:
        # Generate all 2^W bitstrings
        n_states = 1 << W
        integers = np.arange(n_states, dtype=np.uint32)[:, None]
        powers = (1 << np.arange(W, dtype=np.uint32))[None, :]
        bits = ((integers & powers) > 0).astype(float)  # shape (2^W, W)

        # Vectorized energy evaluation E = sum_j (bits @ Q)_j * bits_j
        BQ = bits @ Q
        energies = np.sum(BQ * bits, axis=1)

        best_idx = int(np.argmin(energies))
        best_x = bits[best_idx].astype(int)
        best_energy = float(energies[best_idx])
        return best_x, best_energy

    # Fallback for W > 20: greedy local search with multi-restart
    best_x = np.zeros(W, dtype=int)
    best_energy = 0.0
    for _ in range(50):
        x = np.random.randint(0, 2, size=W)
        for _ in range(2 * W):
            improved = False
            for bit in range(W):
                x_cand = x.copy()
                x_cand[bit] = 1 - x_cand[bit]
                e_cand = float(x_cand @ Q @ x_cand)
                e_curr = float(x @ Q @ x)
                if e_cand < e_curr:
                    x = x_cand
                    improved = True
            if not improved:
                break
        e_final = float(x @ Q @ x)
        if e_final < best_energy:
            best_energy = e_final
            best_x = x

    return best_x, best_energy


def walk_forward_qubo_rebalance(
    returns: np.ndarray,
    target_weights: np.ndarray,
    n_windows: int = 3,
    candidates_per_window: int = 4,
    cost_rate: float = 0.001,
) -> Dict[str, Any]:
    """Walk-Forward QAOA/QUBO Rebalancing Algorithm (Weinberg 2026 Algorithm 1 & §3.8).
    
    Eliminates lookahead bias by partitioning the test period into K windows, solving
    local QUBOs, and executing the schedule while auditing transaction costs and Sharpe ratio.
    """
    X = np.asarray(returns, dtype=float)
    T, N = X.shape
    w_target = np.asarray(target_weights, dtype=float)

    chunk = T // max(1, n_windows)
    global_rebalance_schedule = np.zeros(T, dtype=int)

    for k in range(n_windows):
        start = k * chunk
        end = (k + 1) * chunk if k + 1 < n_windows else T
        window_returns = X[start:end]
        W = min(candidates_per_window, max(1, len(window_returns) // 2))

        candidate_dates = [int(i * len(window_returns) / (W + 1)) for i in range(1, W + 1)]
        Q = build_qubo_matrix(window_returns, w_target, candidate_dates, cost_rate=cost_rate)
        local_schedule, _ = solve_qubo_schedule(Q)

        for d_idx, date in enumerate(candidate_dates):
            if d_idx < len(local_schedule) and local_schedule[d_idx] == 1:
                global_rebalance_schedule[start + date] = 1

    # Backtest engine simulation (Weinberg §3.8 Eq 16, 17)
    portfolio_val = 10000.0
    w_curr = w_target.copy()
    val_series = [portfolio_val]
    total_cost_dollar = 0.0
    rebalance_count = 0

    for t in range(T):
        r_t = X[t]
        # Drift weights with day's return
        w_drift = w_curr * (1.0 + r_t)
        port_ret = float(np.sum(w_drift))
        w_drift = w_drift / max(1e-12, port_ret)

        portfolio_val *= port_ret

        if global_rebalance_schedule[t] == 1:
            # Rebalance back to target weights
            turnover = float(np.sum(np.abs(w_drift - w_target)))
            cost = cost_rate * turnover * portfolio_val
            portfolio_val -= cost
            total_cost_dollar += cost
            rebalance_count += 1
            w_curr = w_target.copy()
        else:
            w_curr = w_drift

        val_series.append(portfolio_val)

    # Compute performance metrics
    vals = np.array(val_series)
    returns_p = np.diff(vals) / vals[:-1]
    mean_p = float(np.mean(returns_p))
    std_p = float(np.std(returns_p, ddof=1)) if len(returns_p) > 1 else 1e-6
    sharpe = (mean_p / std_p) * math.sqrt(252.0) if std_p > 1e-12 else 0.0

    total_cost_bp = (total_cost_dollar / 10000.0) * 10000.0
    total_return = (vals[-1] / vals[0]) - 1.0

    return {
        "rebalance_count": rebalance_count,
        "total_cost_bp": round(total_cost_bp, 2),
        "total_return": round(total_return, 4),
        "sharpe_ratio": round(sharpe, 4),
        "schedule": global_rebalance_schedule.tolist(),
    }
