"""
Regime Detection Modules
========================
Implements market regime classification using multiple methods:
- Hidden Markov Models (HMM)
- Hurst Exponent
- ADX/ATR filters
- Bayesian Online Changepoint Detection (BOCPD)
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from universal_quant_interface import quant_module, quant_tool, QuantModule, registry


# =============================================================================
# Module 1: Hidden Markov Model Regime Classifier
# =============================================================================

@quant_module(
    id="regime_detection.hmm",
    name="Hidden Markov Model Regime Classifier",
    version="1.0.0",
    category="regime_detection",
    tags=["hmm", "regime", "volatility", "states", "gaussian"]
)
class HMMRegimeClassifier:
    """
    Classifies market regime (trending, range-bound, high-volatility crisis)
    using a Gaussian Hidden Markov Model trained on returns and volatility.
    
    References:
    - Hamilton, J.D. (1989). A New Approach to the Economic Analysis of Nonstationary Time Series.
    - https://hmmlearn.readthedocs.io/
    """

    def __init__(self, n_states: int = 3, covariance_type: str = "full", random_state: int = 42):
        try:
            from hmmlearn import hmm
            self.model = hmm.GaussianHMM(
                n_components=n_states,
                covariance_type=covariance_type,
                random_state=random_state
            )
            self.n_states = n_states
            self.is_trained = False
        except ImportError:
            raise ImportError("hmmlearn required: pip install hmmlearn")

    @quant_tool(
        module_id="regime_detection.hmm",
        name="regime_detection.hmm.train",
        description="Train HMM on historical returns and volatility data",
        parameters={
            "returns": {"type": "array", "description": "Time series of returns"},
            "volatility": {"type": "array", "description": "Optional volatility series"},
        },
        returns={
            "n_states": {"type": "int"},
            "converged": {"type": "bool"},
            "log_likelihood": {"type": "float"},
        }
    )
    def train(self, returns: List[float], volatility: Optional[List[float]] = None) -> Dict:
        """Train the HMM on historical data."""
        returns = np.array(returns).reshape(-1, 1)
        if volatility is not None:
            vol = np.array(volatility).reshape(-1, 1)
            X = np.hstack([returns, vol])
        else:
            X = returns

        self.model.fit(X)
        self.is_trained = True
        return {
            "n_states": self.n_states,
            "converged": True,
            "log_likelihood": float(self.model.monitor_.history[-1]) if hasattr(self.model, 'monitor_') else 0.0
        }

    @quant_tool(
        module_id="regime_detection.hmm",
        name="regime_detection.hmm.classify",
        description="Classify current market regime using trained HMM",
        parameters={
            "returns": {"type": "array", "description": "Recent returns sequence"},
            "volatility": {"type": "array", "description": "Recent volatility sequence"},
        },
        returns={
            "regimes": {"type": "array", "description": "Most likely regime for each timestep"},
            "probabilities": {"type": "array", "description": "Regime probability matrix"},
            "current_regime": {"type": "int", "description": "Current detected regime index"},
            "confidence": {"type": "float", "description": "Classification confidence"},
        }
    )
    def classify(self, returns: List[float], volatility: Optional[List[float]] = None) -> Dict:
        """Classify market regime for given data."""
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        returns = np.array(returns).reshape(-1, 1)
        if volatility is not None:
            vol = np.array(volatility).reshape(-1, 1)
            X = np.hstack([returns, vol])
        else:
            X = returns

        regimes = self.model.predict(X)
        probs = self.model.predict_proba(X)

        current_regime = int(regimes[-1])
        confidence = float(np.max(probs[-1]))

        # Map regimes to human-readable labels
        regime_labels = self._map_regime_labels(regimes, returns)

        return {
            "regimes": regime_labels.tolist(),
            "probabilities": probs.tolist(),
            "current_regime": current_regime,
            "confidence": confidence,
        }

    def _map_regime_labels(self, regimes: np.ndarray, returns: np.ndarray) -> np.ndarray:
        """Map numeric regimes to meaningful labels based on volatility and return characteristics."""
        labels = np.zeros_like(regimes)
        for i, regime in enumerate(np.unique(regimes)):
            mask = regimes == regime
            regime_returns = returns[mask]
            regime_vol = np.std(regime_returns) if len(regime_returns) > 1 else 0.0

            if regime_vol > np.mean(np.abs(returns)) * 2:
                labels[mask] = 2  # High-volatility crisis
            elif np.mean(regime_returns) > 0:
                labels[mask] = 0  # Trending
            else:
                labels[mask] = 1  # Range-bound
        return labels


# =============================================================================
# Module 2: Hurst Exponent Calculator
# =============================================================================

@quant_module(
    id="regime_detection.hurst",
    name="Hurst Exponent Calculator",
    version="1.0.0",
    category="regime_detection",
    tags=["hurst", "persistence", "mean-reversion", "trending"]
)
class HurstCalculator:
    """
    Calculates Hurst exponent to determine if a time series is:
    - Trending (H > 0.55): persistent
    - Mean-reverting (H < 0.45): anti-persistent
    - Random walk (H ≈ 0.50): geometric Brownian motion
    
    Reference: Hurst, H.E. (1951). Long-term storage capacity of reservoirs.
    """

    @quant_tool(
        module_id="regime_detection.hurst",
        name="regime_detection.hurst.calculate",
        description="Calculate Hurst exponent for a time series",
        parameters={
            "prices": {"type": "array", "description": "Price series (close prices)"},
            "max_lag": {"type": "int", "description": "Maximum lag for calculation", "default": 20},
        },
        returns={
            "hurst_exponent": {"type": "float", "description": "Hurst exponent value"},
            "interpretation": {"type": "string", "description": "trending/mean-reverting/random_walk"},
            "confidence": {"type": "float", "description": "Calculation confidence"},
        }
    )
    def calculate(self, prices: List[float], max_lag: int = 20) -> Dict:
        """Calculate Hurst exponent using rescaled range analysis."""
        prices = np.array(prices)
        returns = np.diff(np.log(prices))

        # Calculate R/S for different lags
        lags = range(2, min(max_lag, len(returns) // 2))
        tau = [np.sqrt(np.std(np.subtract.outer(returns[:lag], returns[:lag]).diagonal(offset=lag), ddof=1))
               for lag in lags]

        # Avoid log(0)
        tau = [t if t > 1e-10 else 1e-10 for t in tau]
        lags = [lag for lag, t in zip(lags, tau) if t > 1e-10]

        if len(lags) < 2:
            return {
                "hurst_exponent": 0.5,
                "interpretation": "random_walk",
                "confidence": 0.0
            }

        # Log-log regression
        log_lags = np.log10(np.array(lags))
        log_tau = np.log10(np.array(tau[:len(log_lags)]))

        # Linear regression: log(tau) = H * log(lag) + c
        coeffs = np.polyfit(log_lags, log_tau, 1)
        H = coeffs[0]

        # Interpretation
        if H > 0.55:
            interpretation = "trending"
        elif H < 0.45:
            interpretation = "mean_reverting"
        else:
            interpretation = "random_walk"

        confidence = min(1.0, len(lags) / 10.0)

        return {
            "hurst_exponent": float(H),
            "interpretation": interpretation,
            "confidence": float(confidence)
        }


# =============================================================================
# Module 3: ADX/ATR Regime Filter
# =============================================================================

@quant_module(
    id="regime_detection.adx_atr",
    name="ADX/ATR Trend and Volatility Filter",
    version="1.0.0",
    category="regime_detection",
    tags=["adx", "atr", "trend", "volatility", "filter"]
)
class ADXATRFilter:
    """
    Classifies market regime using Average Directional Index (ADX) and
    Average True Range (ATR) percentiles.
    
    - Strong trend: ADX > 25
    - Weak trend/range: ADX < 20
    - High volatility: ATR > 85th percentile
    """

    @quant_tool(
        module_id="regime_detection.adx_atr",
        name="regime_detection.adx_atr.classify",
        description="Classify market regime using ADX and ATR",
        parameters={
            "high": {"type": "array", "description": "High prices"},
            "low": {"type": "array", "description": "Low prices"},
            "close": {"type": "array", "description": "Close prices"},
            "adx_threshold_trend": {"type": "float", "description": "ADX threshold for trend", "default": 25.0},
            "adx_threshold_range": {"type": "float", "description": "ADX threshold for range", "default": 20.0},
            "atr_percentile_threshold": {"type": "float", "description": "ATR percentile for high-vol", "default": 85.0},
        },
        returns={
            "regime": {"type": "string", "description": "Detected regime"},
            "adx": {"type": "float", "description": "Current ADX value"},
            "atr_percentile": {"type": "float", "description": "Current ATR percentile"},
            "confidence": {"type": "float", "description": "Classification confidence"},
        }
    )
    def classify(
        self,
        high: List[float],
        low: List[float],
        close: List[float],
        adx_threshold_trend: float = 25.0,
        adx_threshold_range: float = 20.0,
        atr_percentile_threshold: float = 85.0
    ) -> Dict:
        """Classify market regime using ADX and ATR."""
        high = np.array(high)
        low = np.array(low)
        close = np.array(close)

        # Calculate True Range
        tr1 = high[1:] - low[1:]
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))

        # ATR (14-period)
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
        atr_series = pd.Series(tr).rolling(14).mean().dropna().values if len(tr) >= 14 else tr

        # ATR percentile
        current_atr = atr_series[-1] if len(atr_series) > 0 else atr
        atr_percentile = np.percentile(atr_series, 90) if len(atr_series) > 0 else current_atr

        # Simplified ADX calculation
        plus_dm = np.diff(high)
        minus_dm = -np.diff(low)
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0

        tr_smooth = pd.Series(tr).rolling(14).sum().dropna()
        plus_di = 100 * pd.Series(plus_dm).rolling(14).sum().dropna() / tr_smooth
        minus_di = 100 * pd.Series(minus_dm).rolling(14).sum().dropna() / tr_smooth

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = pd.Series(dx).rolling(14).mean().dropna().iloc[-1] if len(dx) >= 14 else np.mean(dx)

        # Classification
        is_high_vol = current_atr > np.percentile(atr_series, atr_percentile_threshold) if len(atr_series) > 0 else False

        if adx > adx_threshold_trend and not is_high_vol:
            regime = "low_vol_trend"
        elif adx < adx_threshold_range and not is_high_vol:
            regime = "low_vol_range"
        elif adx > adx_threshold_trend and is_high_vol:
            regime = "high_vol_trend"
        else:
            regime = "high_vol_crisis"

        return {
            "regime": regime,
            "adx": float(adx),
            "atr_percentile": float(atr_percentile),
            "confidence": min(1.0, adx / 50.0)
        }


# =============================================================================
# Module 4: Bayesian Online Changepoint Detection (BOCPD)
# =============================================================================

@quant_module(
    id="regime_detection.bocpd",
    name="Bayesian Online Changepoint Detection",
    version="1.0.0",
    category="regime_detection",
    tags=["bocpd", "changepoint", "regime", "bayesian", "real-time"]
)
class BOCPDDetector:
    """
    Detects regime changes in real-time using Bayesian Online Changepoint Detection.
    
    Reference: Adams, R.P. & MacKay, D.J.C. (2007). Bayesian Online Changepoint Detection.
    """

    def __init__(self, hazard_lambda: float = 250.0, mu_0: float = 0.0, kappa_0: float = 1.0, alpha_0: float = 2.0, beta_0: float = 1.0):
        self.hazard_lambda = hazard_lambda
        self.mu_0 = mu_0
        self.kappa_0 = kappa_0
        self.alpha_0 = alpha_0
        self.beta_0 = beta_0
        self.reset()

    def reset(self):
        """Reset detector state."""
        self.run_lengths = np.array([1.0])
        self.mus = np.array([self.mu_0])
        self.kappas = np.array([self.kappa_0])
        self.alphas = np.array([self.alpha_0])
        self.betas = np.array([self.beta_0])

    @quant_tool(
        module_id="regime_detection.bocpd",
        name="regime_detection.bocpd.update",
        description="Update BOCPD with new observation and detect changepoints",
        parameters={
            "observation": {"type": "float", "description": "New data point (e.g., return)"},
        },
        returns={
            "changepoint_probability": {"type": "float", "description": "Probability of changepoint at this step"},
            "run_length": {"type": "int", "description": "Most likely run length"},
            "current_mean": {"type": "float", "description": "Current posterior mean"},
            "current_variance": {"type": "float", "description": "Current posterior variance"},
        }
    )
    def update(self, observation: float) -> Dict:
        """Update BOCPD with new observation."""
        # Calculate growth probabilities
        n = len(self.run_lengths)
        hazard = 1.0 / self.hazard_lambda

        # Predictive probabilities
        pred_probs = np.zeros(n)
        for i in range(n):
            # Student-t predictive distribution
            nu = 2 * self.alphas[i]
            x = observation - self.mus[i]
            scale = np.sqrt(self.betas[i] * (self.kappas[i] + 1) / (self.alphas[i] * self.kappas[i]))
            pred_probs[i] = self._student_t_pdf(x, nu, scale)

        # Calculate growth probabilities
        growth_probs = np.zeros(n + 1)
        growth_probs[1:] = pred_probs * (1 - hazard)
        growth_probs[0] = np.sum(pred_probs * hazard)

        # Normalize
        total = np.sum(growth_probs)
        if total > 1e-10:
            growth_probs = growth_probs / total

        # Detect changepoint
        changepoint_prob = float(growth_probs[0])

        # Update parameters
        new_run_lengths = np.zeros(n + 1)
        new_mus = np.zeros(n + 1)
        new_kappas = np.zeros(n + 1)
        new_alphas = np.zeros(n + 1)
        new_betas = np.zeros(n + 1)

        # Run length 0 (changepoint occurred)
        new_run_lengths[0] = 1.0
        new_mus[0] = self.mu_0
        new_kappas[0] = self.kappa_0
        new_alphas[0] = self.alpha_0
        new_betas[0] = self.beta_0

        # Other run lengths
        for i in range(n):
            new_run_lengths[i + 1] = self.run_lengths[i] + 1
            new_kappas[i + 1] = self.kappas[i] + 1
            new_alphas[i + 1] = self.alphas[i] + 0.5
            new_betas[i + 1] = self.betas[i] + 0.5 * self.kappas[i] / (self.kappas[i] + 1) * (observation - self.mus[i])**2
            new_mus[i + 1] = (self.kappas[i] * self.mus[i] + observation) / (self.kappas[i] + 1)

        # Prune very small probabilities
        mask = growth_probs > 1e-10
        self.run_lengths = new_run_lengths[mask]
        self.mus = new_mus[mask]
        self.kappas = new_kappas[mask]
        self.alphas = new_alphas[mask]
        self.betas = new_betas[mask]

        # Normalize
        total = np.sum(self.run_lengths)
        if total > 1e-10:
            self.run_lengths = self.run_lengths / total

        return {
            "changepoint_probability": changepoint_prob,
            "run_length": int(np.argmax(self.run_lengths)),
            "current_mean": float(np.average(self.mus, weights=self.run_lengths)),
            "current_variance": float(np.average(self.betas / (self.alphas - 1), weights=self.run_lengths)) if np.any(self.alphas > 1) else 1.0
        }

    def _student_t_pdf(self, x: float, nu: float, scale: float) -> float:
        """Student-t probability density function."""
        from scipy.stats import t
        return float(t.pdf(x, nu, loc=0, scale=scale))


# =============================================================================
# Module 5: Regime Router
# =============================================================================

@quant_module(
    id="regime_detection.router",
    name="Dynamic Strategy Regime Router",
    version="1.0.0",
    category="regime_detection",
    tags=["router", "allocation", "dynamic", "strategy"]
)
class RegimeRouter:
    """
    Routes capital weights dynamically based on detected regime.
    """

    def __init__(self):
        self.regime_weights = {
            "low_vol_trend": {"trend": 1.0, "mean_reversion": 0.0},
            "low_vol_range": {"trend": 0.0, "mean_reversion": 1.0},
            "high_vol_trend": {"trend": 0.75, "mean_reversion": 0.25},
            "high_vol_crisis": {"trend": 0.0, "mean_reversion": 0.0},
        }

    @quant_tool(
        module_id="regime_detection.router",
        name="regime_detection.router.allocate",
        description="Get capital allocation weights for current regime",
        parameters={
            "regime": {"type": "string", "description": "Detected regime name"},
        },
        returns={
            "weights": {"type": "dict", "description": "Strategy weights"},
            "regime": {"type": "string", "description": "Current regime"},
            "action": {"type": "string", "description": "Recommended action"},
        }
    )
    def allocate(self, regime: str) -> Dict:
        """Get capital allocation for detected regime."""
        if regime not in self.regime_weights:
            regime = "high_vol_crisis"

        weights = self.regime_weights[regime]

        if weights["trend"] == 0.0 and weights["mean_reversion"] == 0.0:
            action = "CASH_PRESERVATION"
        elif weights["trend"] > weights["mean_reversion"]:
            action = "DEPLOY_TREND"
        else:
            action = "DEPLOY_MEAN_REVERSION"

        return {
            "weights": weights,
            "regime": regime,
            "action": action
        }
