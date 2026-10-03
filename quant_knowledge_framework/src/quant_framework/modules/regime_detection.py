"""
Regime Detection Modules
=========================
Implements market regime classification using multiple methods.
"""

import warnings
from typing import Dict, List, Optional
import logging

import numpy as np

from quant_framework.modules.interface import quant_module, quant_tool, QuantModule, registry

logger = logging.getLogger(__name__)


@quant_module(
    id="regime_detection.hmm",
    name="Hidden Markov Model Regime Classifier",
    version="1.1.0",
    category="regime_detection",
    tags=["hmm", "regime", "volatility", "states"],
)
class HMMRegimeClassifier(QuantModule):
    """
    Classifies market regime (calm / normal / high_vol, ranked by within-state
    volatility) using a Gaussian Hidden Markov Model trained on returns and
    optionally volatility.

    Usage:
        clf = HMMRegimeClassifier(n_states=2)
        clf.fit(returns=[...])        # explicit training
        clf.classify(returns=[...])   # predict

    classify() also auto-fits on first use, so
    HMMRegimeClassifier().classify(returns=[...]) works out of the box.
    """

    def __init__(self, n_states: int = 3):
        self.n_states = n_states
        self._fitted = False
        self._fitted_features: Optional[int] = None
        self._state_labels: Dict[int, str] = {}
        try:
            from hmmlearn import hmm
            self.model = hmm.GaussianHMM(
                n_components=n_states,
                covariance_type="full",
                n_iter=200,
                random_state=42,
            )
        except ImportError:
            self.model = None

    def _prepare_X(self, returns: List[float], volatility: Optional[List[float]]) -> Optional[np.ndarray]:
        returns_arr = np.asarray(returns, dtype=float).reshape(-1, 1)
        if not np.all(np.isfinite(returns_arr)):
            return None
        if volatility is not None:
            vol_arr = np.asarray(volatility, dtype=float).reshape(-1, 1)
            if len(vol_arr) != len(returns_arr) or not np.all(np.isfinite(vol_arr)):
                return None
            return np.hstack([returns_arr, vol_arr])
        return returns_arr

    @staticmethod
    def _labels_for(n_states: int) -> List[str]:
        """Volatility-rank labels: the highest-vol state always reads as high_vol."""
        base = ["calm", "normal", "high_vol", "extreme", "panic"]
        if n_states <= 1:
            return ["normal"]
        if n_states == 2:
            return ["calm", "high_vol"]
        if n_states <= len(base):
            return base[:n_states]
        return [f"regime_{i}" for i in range(n_states)]

    def _label_states_by_volatility(self, X: np.ndarray) -> Dict[int, str]:
        """Label each HMM state by its within-state volatility rank."""
        states = self.model.predict(X)
        vols: Dict[int, float] = {}
        for s in range(self.n_states):
            mask = states == s
            vols[s] = float(np.std(X[mask, 0])) if mask.any() else float("inf")
        order = sorted(range(self.n_states), key=lambda s: vols[s])
        labels = self._labels_for(self.n_states)
        return {s: labels[i] for i, s in enumerate(order)}

    @quant_tool(
        module_id="regime_detection.hmm",
        name="regime_detection.hmm.fit",
        description="Train the HMM on a returns (and optional volatility) sequence",
        parameters={
            "returns": {"type": "array", "description": "Training returns sequence"},
            "volatility": {"type": "array", "description": "Training volatility sequence", "required": False},
        },
        returns={
            "fitted": {"type": "bool", "description": "True when training succeeded"},
            "n_samples": {"type": "int", "description": "Number of training samples"},
            "n_states": {"type": "int", "description": "Number of HMM states"},
            "log_likelihood": {"type": "float", "description": "Log-likelihood of the training data"},
            "state_labels": {"type": "object", "description": "Per-state volatility-rank labels"},
        },
    )
    def fit(self, returns: List[float], volatility: Optional[List[float]] = None) -> Dict:
        if self.model is None:
            return {"error": "hmmlearn not installed"}

        X = self._prepare_X(returns, volatility)
        if X is None:
            return {"error": "returns/volatility must be finite, equal-length sequences"}

        min_samples = max(10, self.n_states * 5)
        if len(X) < min_samples:
            return {"error": f"need at least {min_samples} samples to fit, got {len(X)}"}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model.fit(X)
        except Exception as exc:
            logger.warning("HMM fit failed: %s", exc)
            return {"error": f"HMM fit failed: {exc}"}

        self._fitted = True
        self._fitted_features = X.shape[1]
        self._state_labels = self._label_states_by_volatility(X)

        return {
            "fitted": True,
            "n_samples": int(len(X)),
            "n_states": int(self.n_states),
            "log_likelihood": float(self.model.score(X)),
            "state_labels": {str(k): v for k, v in self._state_labels.items()},
        }

    @quant_tool(
        module_id="regime_detection.hmm",
        name="regime_detection.hmm.classify",
        description="Classify current market regime using trained HMM (auto-fits when unfitted)",
        parameters={
            "returns": {"type": "array", "description": "Recent returns sequence"},
            "volatility": {"type": "array", "description": "Recent volatility sequence", "required": False},
        },
        returns={
            "current_regime": {"type": "int", "description": "Current detected regime index"},
            "regime_label": {"type": "string", "description": "Volatility-rank label of the current regime"},
            "confidence": {"type": "float", "description": "Classification confidence"},
            "regimes": {"type": "array", "description": "Most likely regime for each timestep"},
        },
    )
    def classify(self, returns: List[float], volatility: Optional[List[float]] = None) -> Dict:
        if self.model is None:
            return {"error": "hmmlearn not installed"}

        X = self._prepare_X(returns, volatility)
        if X is None:
            return {"error": "returns/volatility must be finite, equal-length sequences"}

        # Auto-fit so classify works out of the box; refit when the feature
        # layout changed since the last fit (e.g. volatility now provided).
        if not self._fitted or X.shape[1] != self._fitted_features:
            fit_result = self.fit(returns, volatility)
            if "error" in fit_result:
                return fit_result

        regimes = self.model.predict(X)
        probs = self.model.predict_proba(X)

        current_regime = int(regimes[-1])
        confidence = float(np.max(probs[-1]))

        return {
            "current_regime": current_regime,
            "regime_label": self._state_labels.get(current_regime, f"regime_{current_regime}"),
            "confidence": confidence,
            "regimes": regimes.tolist(),
        }


@quant_module(
    id="regime_detection.hurst",
    name="Hurst Exponent Calculator",
    version="1.1.0",
    category="regime_detection",
    tags=["hurst", "persistence", "mean-reversion", "trending"],
)
class HurstCalculator(QuantModule):
    """
    Calculates Hurst exponent to determine if a time series is:
    - Trending (H > 0.55)
    - Mean-reverting (H < 0.45)
    - Random walk (H ≈ 0.50)
    """

    @quant_tool(
        module_id="regime_detection.hurst",
        name="regime_detection.hurst.calculate",
        description="Calculate Hurst exponent for a time series",
        parameters={
            "prices": {"type": "array", "description": "Price series"},
            "max_lag": {"type": "int", "description": "Maximum lag", "default": 20},
        },
        returns={
            "hurst_exponent": {"type": "float"},
            "interpretation": {"type": "string"},
            "confidence": {"type": "float"},
        },
    )
    def calculate(self, prices: List[float], max_lag: int = 20) -> Dict:
        prices = np.asarray(prices, dtype=float)
        if len(prices) < 20:
            return {
                "hurst_exponent": 0.5,
                "interpretation": "random_walk",
                "confidence": 0.0,
                "warning": "need at least 20 prices for a reliable estimate",
            }
        if not np.all(np.isfinite(prices)) or np.any(prices <= 0):
            return {"error": "prices must be positive and finite"}

        # tau(lag) = standard deviation of the lag-differences of the
        # log-price series.  For a random walk tau grows like lag**0.5; for a
        # persistent (trending) series like lag**H with H > 0.5; for a
        # mean-reverting series with H < 0.5.  The log-log slope of tau
        # against the lag is therefore the Hurst exponent.
        log_prices = np.log(prices)
        max_lag = int(max(3, max_lag))
        lags = np.arange(2, min(max_lag, len(log_prices) // 2))
        if len(lags) < 2:
            return {
                "hurst_exponent": 0.5,
                "interpretation": "random_walk",
                "confidence": 0.0,
                "warning": "series too short for the requested max_lag",
            }

        tau = []
        for lag in lags:
            diffs = log_prices[lag:] - log_prices[:-lag]
            std = float(np.std(diffs, ddof=1))
            tau.append(max(std, 1e-12))

        log_lags = np.log(lags.astype(float))
        log_tau = np.log(np.array(tau))
        slope, intercept = np.polyfit(log_lags, log_tau, 1)
        H = float(slope)

        # Regression R^2 doubles as the confidence of the estimate.
        fitted = slope * log_lags + intercept
        ss_res = float(np.sum((log_tau - fitted) ** 2))
        ss_tot = float(np.sum((log_tau - np.mean(log_tau)) ** 2))
        confidence = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        if H > 0.55:
            interpretation = "trending"
        elif H < 0.45:
            interpretation = "mean_reverting"
        else:
            interpretation = "random_walk"

        return {
            "hurst_exponent": H,
            "interpretation": interpretation,
            "confidence": float(min(1.0, max(0.0, confidence))),
        }
