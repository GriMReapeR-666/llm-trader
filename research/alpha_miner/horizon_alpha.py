"""Horizon-Aware Alpha Evaluator.

Implements the Modified LL Method from Bessembinder, Cooper, & Zhang (2023)
to estimate long-horizon alphas from short-horizon returns.

Key Insight: Alpha depends on the return measurement horizon. Monthly alpha
estimates can be misleading for long-horizon investors. For funds with
monthly beta > 1, over 70% have negative 10-year alpha even when monthly
alpha is positive.

References:
    Bessembinder, H., Cooper, M. J., & Zhang, F. (2023). "The (Large) Effect
    of Return Horizon on Fund Alpha." Working Paper, ssrn.com/abstract=4096412.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class HorizonConfig:
    """Configuration for horizon-aware alpha evaluation."""
    short_horizon: int = 21  # Monthly (trading days)
    medium_horizon: int = 63  # Quarterly
    long_horizon: int = 252  # Annual
    decade_horizon: int = 2520  # 10 years
    
    # Market parameters (annualized)
    market_mean_return: float = 0.09  # 9% annual
    market_std: float = 0.19  # 19% annual
    
    # Bootstrap parameters
    n_simulations: int = 1000
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_horizon": self.short_horizon,
            "medium_horizon": self.medium_horizon,
            "long_horizon": self.long_horizon,
            "decade_horizon": self.decade_horizon,
            "market_mean_return": self.market_mean_return,
            "market_std": self.market_std,
            "n_simulations": self.n_simulations,
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class HorizonAlphaResult:
    """Alpha estimates across multiple horizons."""
    monthly_alpha: float
    quarterly_alpha: float
    annual_alpha: float
    decade_alpha: float
    
    monthly_beta: float
    quarterly_beta: float
    annual_beta: float
    decade_beta: float
    
    # Horizon-dependent metrics
    alpha_sign_changes: int
    is_misleading: bool  # Positive monthly, negative decade
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "monthly_alpha": round(self.monthly_alpha, 6),
            "quarterly_alpha": round(self.quarterly_alpha, 6),
            "annual_alpha": round(self.annual_alpha, 6),
            "decade_alpha": round(self.decade_alpha, 6),
            "monthly_beta": round(self.monthly_beta, 4),
            "quarterly_beta": round(self.quarterly_beta, 4),
            "annual_beta": round(self.annual_beta, 4),
            "decade_beta": round(self.decade_beta, 4),
            "alpha_sign_changes": self.alpha_sign_changes,
            "is_misleading": self.is_misleading,
        }


# ---------------------------------------------------------------------------
# Modified LL Method
# ---------------------------------------------------------------------------

class HorizonAlphaEvaluator:
    """Estimate long-horizon alphas from short-horizon returns.
    
    Implements the Modified Levhari-Levy (LL) method from Bessembinder,
    Cooper, & Zhang (2023) to correct for bias in long-horizon parameter
    estimates.
    """
    
    def __init__(self, config: Optional[HorizonConfig] = None):
        self.config = config or HorizonConfig()
    
    def estimate_alphas(self, fund_returns: pd.Series,
                        market_returns: pd.Series,
                        risk_free_rate: float = 0.0) -> HorizonAlphaResult:
        """Estimate alphas across multiple horizons.
        
        Args:
            fund_returns: Fund returns (daily or monthly)
            market_returns: Market returns (same frequency)
            risk_free_rate: Risk-free rate
            
        Returns:
            HorizonAlphaResult with estimates at each horizon
        """
        # Step 1: Estimate monthly parameters
        excess_fund = fund_returns - risk_free_rate
        excess_market = market_returns - risk_free_rate
        
        monthly_alpha, monthly_beta = self._estimate_monthly_params(
            excess_fund, excess_market
        )
        
        # Step 2: Estimate mean and variance of market returns
        mu_m = excess_market.mean()
        sigma_m_sq = excess_market.var()
        
        # Step 3: Estimate long-horizon betas using Modified LL
        annual_beta = self._estimate_long_horizon_beta(
            monthly_beta, monthly_alpha, mu_m, sigma_m_sq, T=12
        )
        quarterly_beta = self._estimate_long_horizon_beta(
            monthly_beta, monthly_alpha, mu_m, sigma_m_sq, T=3
        )
        decade_beta = self._estimate_long_horizon_beta(
            monthly_beta, monthly_alpha, mu_m, sigma_m_sq, T=120
        )
        
        # Step 4: Estimate long-horizon alphas
        annual_alpha = self._estimate_long_horizon_alpha(
            excess_fund, excess_market, annual_beta, T=12
        )
        quarterly_alpha = self._estimate_long_horizon_alpha(
            excess_fund, excess_market, quarterly_beta, T=3
        )
        decade_alpha = self._estimate_long_horizon_alpha(
            excess_fund, excess_market, decade_beta, T=120
        )
        
        # Check for sign changes
        alphas = [monthly_alpha, quarterly_alpha, annual_alpha, decade_alpha]
        sign_changes = sum(
            1 for i in range(1, len(alphas))
            if np.sign(alphas[i]) != np.sign(alphas[i-1])
        )
        
        # Check if alpha is misleading (positive monthly, negative decade)
        is_misleading = monthly_alpha > 0 and decade_alpha < 0
        
        return HorizonAlphaResult(
            monthly_alpha=monthly_alpha,
            quarterly_alpha=quarterly_alpha,
            annual_alpha=annual_alpha,
            decade_alpha=decade_alpha,
            monthly_beta=monthly_beta,
            quarterly_beta=quarterly_beta,
            annual_beta=annual_beta,
            decade_beta=decade_beta,
            alpha_sign_changes=sign_changes,
            is_misleading=is_misleading,
        )
    
    def _estimate_monthly_params(self, excess_fund: pd.Series,
                                 excess_market: pd.Series) -> Tuple[float, float]:
        """Estimate monthly alpha and beta via OLS regression."""
        X = excess_market.values.reshape(-1, 1)
        y = excess_fund.values
        
        # OLS: y = alpha + beta * X
        X_with_const = np.column_stack([np.ones(len(X)), X])
        try:
            params = np.linalg.lstsq(X_with_const, y, rcond=None)[0]
            alpha, beta = params[0], params[1]
        except np.linalg.LinAlgError:
            alpha, beta = 0.0, 1.0
        
        return alpha, beta
    
    def _estimate_long_horizon_beta(self, monthly_beta: float,
                                    monthly_alpha: float,
                                    mu_m: float,
                                    sigma_m_sq: float,
                                    T: int) -> float:
        """Estimate long-horizon beta using Modified LL formula.
        
        From equation (6a) in the paper:
        β_L = (βσ² + (1+μ_i)(1+μ_m))^T - (1+μ_i)^T * (1+μ_m)^T
              / (σ² + (1+μ_m)^2)^T - (1+μ_m)^(2T)
        
        Simplified for monthly parameters.
        """
        # Parameters
        mu_i = monthly_alpha + monthly_beta * mu_m  # Fund mean return
        
        # Numerator: (βσ² + (1+μ_i)(1+μ_m))^T - (1+μ_i)^T * (1+μ_m)^T
        term1 = monthly_beta * sigma_m_sq + (1 + mu_i) * (1 + mu_m)
        numerator = term1**T - (1 + mu_i)**T * (1 + mu_m)**T
        
        # Denominator: (σ² + (1+μ_m)^2)^T - (1+μ_m)^(2T)
        term2 = sigma_m_sq + (1 + mu_m)**2
        denominator = term2**T - (1 + mu_m)**(2*T)
        
        if abs(denominator) < 1e-12:
            return monthly_beta
        
        beta_L = numerator / denominator
        
        # Clamp to reasonable range
        return max(0.0, min(3.0, beta_L))
    
    def _estimate_long_horizon_alpha(self, excess_fund: pd.Series,
                                     excess_market: pd.Series,
                                     long_horizon_beta: float,
                                     T: int) -> float:
        """Estimate long-horizon alpha from rolling N-month returns."""
        if len(excess_fund) < T:
            return 0.0
        
        # Compute rolling N-month compound returns
        fund_rolling = excess_fund.rolling(T).apply(
            lambda x: (1 + x).prod() - 1, raw=True
        )
        market_rolling = excess_market.rolling(T).apply(
            lambda x: (1 + x).prod() - 1, raw=True
        )
        
        # Drop NaN
        fund_rolling = fund_rolling.dropna()
        market_rolling = market_rolling.dropna()
        
        if len(fund_rolling) == 0:
            return 0.0
        
        # Alpha = mean(fund) - beta * mean(market)
        mean_fund = fund_rolling.mean()
        mean_market = market_rolling.mean()
        
        alpha_L = mean_fund - long_horizon_beta * mean_market
        
        # Convert to monthly equivalent
        alpha_monthly = alpha_L / T
        
        return alpha_monthly
    
    def bootstrap_alphas(self, fund_returns: pd.Series,
                         market_returns: pd.Series,
                         n_simulations: Optional[int] = None) -> Dict[str, Any]:
        """Bootstrap distribution of long-horizon alphas.
        
        Implements Steps 4-8 from the paper to correct for Jensen's
        inequality bias in nonlinear transformations.
        """
        n_sims = n_simulations or self.config.n_simulations
        
        # Estimate sample parameters
        excess_fund = fund_returns
        excess_market = market_returns
        
        mu_m = excess_market.mean()
        sigma_m_sq = excess_market.var()
        
        monthly_alpha, monthly_beta = self._estimate_monthly_params(
            excess_fund, excess_market
        )
        
        # Cross-sectional variance for bootstrap
        alpha_var = excess_fund.var()
        beta_var = 0.04  # Typical cross-sectional beta variance
        
        # Bootstrap
        beta_L_samples = []
        alpha_L_samples = []
        
        for _ in range(n_sims):
            # Draw true parameters
            true_beta = np.random.normal(monthly_beta, np.sqrt(beta_var))
            true_alpha = np.random.normal(monthly_alpha, np.sqrt(alpha_var))
            
            # Compute true long-horizon beta
            true_beta_L = self._estimate_long_horizon_beta(
                true_beta, true_alpha, mu_m, sigma_m_sq, T=120
            )
            
            # Simulate returns
            n_obs = len(excess_market)
            simulated_market = np.random.normal(mu_m, np.sqrt(sigma_m_sq), n_obs)
            simulated_fund = true_alpha + true_beta * simulated_market
            
            # Estimate beta from simulated data
            sim_fund = pd.Series(simulated_fund)
            sim_market = pd.Series(simulated_market)
            
            est_alpha, est_beta = self._estimate_monthly_params(sim_fund, sim_market)
            
            # Convert to long-horizon estimate
            est_beta_L = self._estimate_long_horizon_beta(
                est_beta, est_alpha, mu_m, sigma_m_sq, T=120
            )
            
            beta_L_samples.append((true_beta_L, est_beta_L))
        
        # Compute bias correction (Step 8)
        true_betas = [x[0] for x in beta_L_samples]
        est_betas = [x[1] for x in beta_L_samples]
        
        # Regression: true = a + b * estimated
        X = np.column_stack([np.ones(len(est_betas)), est_betas])
        try:
            params = np.linalg.lstsq(X, true_betas, rcond=None)[0]
            a_hat, b_hat = params[0], params[1]
        except np.linalg.LinAlgError:
            a_hat, b_hat = 0.0, 1.0
        
        # Apply correction to actual estimate
        actual_beta_L = self._estimate_long_horizon_beta(
            monthly_beta, monthly_alpha, mu_m, sigma_m_sq, T=120
        )
        corrected_beta_L = a_hat + b_hat * actual_beta_L
        
        return {
            "actual_beta_L": actual_beta_L,
            "corrected_beta_L": corrected_beta_L,
            "bias_intercept": a_hat,
            "bias_slope": b_hat,
            "mean_true_beta_L": np.mean(true_betas),
            "mean_est_beta_L": np.mean(est_betas),
        }
    
    def compute_horizon_adjusted_ic(self, alpha_values: pd.Series,
                                    forward_returns: pd.Series,
                                    horizon: str = "annual") -> float:
        """Compute IC adjusted for return horizon.
        
        This adjusts the Information Coefficient to account for the
        horizon-dependent nature of alpha.
        """
        from scipy import stats as sp_stats
        
        # Determine rolling window based on horizon
        horizon_map = {
            "monthly": 21,
            "quarterly": 63,
            "annual": 252,
            "decade": 2520,
        }
        
        window = horizon_map.get(horizon, 252)
        
        # Compute rolling IC
        if len(alpha_values) < window:
            return 0.0
        
        rolling_ic = alpha_values.rolling(window).corr(forward_returns)
        ic_mean = rolling_ic.mean()
        
        # Apply horizon adjustment factor
        # From the paper: alphas tend to decrease with horizon for high-beta assets
        adjustment_factors = {
            "monthly": 1.0,
            "quarterly": 0.95,
            "annual": 0.85,
            "decade": 0.70,
        }
        
        adjustment = adjustment_factors.get(horizon, 1.0)
        
        return ic_mean * adjustment
    
    def detect_misleading_alpha(self, alpha_values: pd.Series,
                                beta_values: pd.Series,
                                threshold: float = 1.0) -> Dict[str, Any]:
        """Detect when short-horizon alpha is misleading for long-horizon.
        
        From the paper: Among funds with monthly beta > 1 and positive
        monthly alpha, over 70% have negative decade alpha.
        """
        if len(alpha_values) == 0 or len(beta_values) == 0:
            return {"is_misleading": False, "risk_level": "low"}
        
        current_alpha = alpha_values.iloc[-1]
        current_beta = beta_values.iloc[-1]
        
        # High beta + positive alpha = high risk of misleading
        if current_beta > threshold and current_alpha > 0:
            # Estimate probability of sign change
            # From paper: 71.1% have negative decade alpha
            misleading_probability = 0.71
            
            return {
                "is_misleading": True,
                "risk_level": "high",
                "monthly_alpha": current_alpha,
                "monthly_beta": current_beta,
                "misleading_probability": misleading_probability,
                "recommendation": "Evaluate at longer horizon before trading",
            }
        
        return {
            "is_misleading": False,
            "risk_level": "low",
            "monthly_alpha": current_alpha,
            "monthly_beta": current_beta,
        }


# ---------------------------------------------------------------------------
# Integration with IC Evaluator
# ---------------------------------------------------------------------------

def compute_horizon_aware_ic(alpha_values: pd.Series,
                             forward_returns: pd.Series,
                             horizons: Optional[List[str]] = None) -> Dict[str, float]:
    """Compute IC at multiple horizons.
    
    Returns IC values for monthly, quarterly, annual, and decade horizons.
    """
    evaluator = HorizonAlphaEvaluator()
    
    if horizons is None:
        horizons = ["monthly", "quarterly", "annual"]
    
    results = {}
    for horizon in horizons:
        ic = evaluator.compute_horizon_adjusted_ic(
            alpha_values, forward_returns, horizon
        )
        results[f"ic_{horizon}"] = ic
    
    # Compute IC decay rate
    if "ic_monthly" in results and "ic_annual" in results:
        results["ic_decay_rate"] = (
            results["ic_annual"] - results["ic_monthly"]
        ) / results["ic_monthly"] if results["ic_monthly"] != 0 else 0.0
    
    return results
