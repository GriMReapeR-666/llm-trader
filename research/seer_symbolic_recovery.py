"""Self-Equation Extractor & Recognizer (SEER).

Symbolic Recovery of Feedback Functions in Algorithmic Trading Signals.
Reference: Igor Merlini et al. (2026), ActarusLab.

Identifies the functional form of market feedback / price impact:
Y(t + 1) = g(X(t)) + f(U(t)) + epsilon(t)
without assuming linearity. Distinguishes between:
1. Linear feedback (f(u) = lambda * u)
2. Non-linear quadratic feedback (f(u) = lambda * u^2 * sgn(u))
3. Asymmetric ReLU impact (f(u) = lambda * max(0, u))
4. Sinusoidal feedback (f(u) = lambda * sin(u))
5. Noise floor / unmanipulated market (no detected impact above noise floor).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

import numpy as np


class FeedbackRegime(str, Enum):
    LINEAR = "LINEAR"
    QUADRATIC = "QUADRATIC"
    ASYMMETRIC_RELU = "ASYMMETRIC_RELU"
    SINUSOIDAL = "SINUSOIDAL"
    NOISE_FLOOR = "NOISE_FLOOR"


@dataclass
class FeedbackModelResult:
    """Discovered feedback equation and statistical diagnostics."""
    regime: FeedbackRegime
    equation: str
    lambda_coeff: float
    r_squared: float
    snr_db: float
    is_impact_detected: bool
    alpha_decay_risk: str
    aic: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "equation": self.equation,
            "lambda_coeff": self.lambda_coeff,
            "r_squared": self.r_squared,
            "snr_db": self.snr_db,
            "is_impact_detected": self.is_impact_detected,
            "alpha_decay_risk": self.alpha_decay_risk,
            "aic": self.aic,
        }


class SEERRecoveryEngine:
    """Lightweight symbolic feedback recovery engine (Merlini 2026)."""

    def fit(
        self,
        signal_u: np.ndarray,
        forward_return_y: np.ndarray,
        baseline_g: Optional[np.ndarray] = None,
        min_r2_threshold: float = 0.05,
        min_snr_db: float = -10.0,
    ) -> FeedbackModelResult:
        """Fit candidate symbolic representations to isolate the market feedback function f(u)."""
        u = np.asarray(signal_u, dtype=np.float64).ravel()
        y = np.asarray(forward_return_y, dtype=np.float64).ravel()

        if baseline_g is not None:
            g = np.asarray(baseline_g, dtype=np.float64).ravel()
            target = y - g
        else:
            target = y

        n = len(u)
        if n < 20 or np.all(u == 0):
            return FeedbackModelResult(
                regime=FeedbackRegime.NOISE_FLOOR,
                equation="f(u) = 0 (insufficient data)",
                lambda_coeff=0.0,
                r_squared=0.0,
                snr_db=-30.0,
                is_impact_detected=False,
                alpha_decay_risk="NEGLIGIBLE",
            )

        ss_tot = float(np.sum((target - np.mean(target)) ** 2))
        if ss_tot <= 1e-12:
            return FeedbackModelResult(
                regime=FeedbackRegime.NOISE_FLOOR,
                equation="f(u) = 0 (zero variance)",
                lambda_coeff=0.0,
                r_squared=0.0,
                snr_db=-30.0,
                is_impact_detected=False,
                alpha_decay_risk="NEGLIGIBLE",
            )

        # Candidate basis library
        candidates = {
            FeedbackRegime.LINEAR: (u, "u"),
            FeedbackRegime.QUADRATIC: ((u ** 2) * np.sign(u), "u^2 * sgn(u)"),
            FeedbackRegime.ASYMMETRIC_RELU: (np.maximum(0.0, u), "max(0, u)"),
            FeedbackRegime.SINUSOIDAL: (np.sin(u), "sin(u)"),
        }

        best_regime = FeedbackRegime.NOISE_FLOOR
        best_r2 = -float("inf")
        best_lambda = 0.0
        best_equation = "f(u) = 0"
        best_snr_db = -30.0
        best_aic = float("inf")

        for regime, (phi, expr) in candidates.items():
            denom = float(np.sum(phi ** 2))
            if denom <= 1e-12:
                continue
            lam = float(np.sum(phi * target) / denom)
            pred = lam * phi
            res = target - pred
            ss_res = float(np.sum(res ** 2))
            r2 = 1.0 - (ss_res / ss_tot)

            # Signal-to-Noise Ratio (SNR) in dB
            var_sig = float(np.var(pred))
            var_noise = float(np.var(res))
            snr_db = 10.0 * math.log10(var_sig / max(1e-12, var_noise)) if var_sig > 1e-12 else -30.0

            # Akaike Information Criterion (AIC) for model selection
            sigma2 = ss_res / n
            aic = n * math.log(max(1e-12, sigma2)) + 2.0 * 2  # 2 parameters (lambda, variance)

            if r2 > best_r2:
                best_r2 = r2
                best_regime = regime
                best_lambda = lam
                best_equation = f"f(u) = {lam:.4f} * {expr}"
                best_snr_db = snr_db
                best_aic = aic

        # Check against noise floor and minimum SNR bounds
        if best_r2 < min_r2_threshold or best_snr_db < min_snr_db:
            return FeedbackModelResult(
                regime=FeedbackRegime.NOISE_FLOOR,
                equation="f(u) = 0 (noise floor)",
                lambda_coeff=0.0,
                r_squared=max(0.0, best_r2),
                snr_db=best_snr_db,
                is_impact_detected=False,
                alpha_decay_risk="NEGLIGIBLE",
                aic=best_aic,
            )

        # Classify alpha decay risk
        if best_r2 > 0.50 and abs(best_lambda) > 0.02:
            decay_risk = "HIGH"
        elif best_r2 > 0.20:
            decay_risk = "MODERATE"
        else:
            decay_risk = "LOW"

        return FeedbackModelResult(
            regime=best_regime,
            equation=best_equation,
            lambda_coeff=best_lambda,
            r_squared=best_r2,
            snr_db=best_snr_db,
            is_impact_detected=True,
            alpha_decay_risk=decay_risk,
            aic=best_aic,
        )

    def simulate_synthetic_benchmark(
        self,
        regime: str,
        n_samples: int = 2000,
        lambda_val: float = 0.05,
        noise_std: float = 0.003,
        seed: int = 42,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Generate synthetic benchmark datasets with known ground truth feedback (SEER-Bench)."""
        rng = np.random.default_rng(seed)
        u = rng.uniform(-1.0, 1.0, size=n_samples)
        noise = rng.normal(0.0, noise_std, size=n_samples)

        reg = regime.lower()
        if reg == "linear":
            y = lambda_val * u + noise
        elif reg == "quadratic":
            y = lambda_val * (u ** 2) * np.sign(u) + noise
        elif reg in ("relu", "asymmetric"):
            y = lambda_val * np.maximum(0.0, u) + noise
        elif reg == "sinusoidal":
            y = lambda_val * np.sin(u) + noise
        elif reg == "noise":
            y = noise
        else:
            raise ValueError(f"Unknown synthetic benchmark regime: {regime}")

        return u, y
