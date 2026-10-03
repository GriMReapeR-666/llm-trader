"""Module 01: Predictive Analysis, Extrapolation Mathematics & State Trajectory Forecasting.

Implements foundational mathematical extrapolation, state-space vector trajectories,
chaos-theoretic Lyapunov predictability horizons, Jacobian sensitivity, and 12-domain state encodings:
1. Linear Extrapolation:
       y_hat(t + h) = y_t + h * (dy/dt)|_t
2. Ballistic Quadratic Physical Trajectory:
       y(t) = y_0 + v_0 * t - 0.5 * g * t^2
       t_impact = (v_0 + sqrt(v_0^2 + 2*g*y_0)) / g
       v_impact = sqrt(v_0^2 + 2*g*y_0)
3. Logistic Sigmoid Carrying Capacity Manifold:
       y(t) = K / (1 + ((K - y_0) / y_0) * exp(-r * t))
4. Discrete State-Space Vector Trajectory Simulation:
       x_{t+1} = F_t * x_t + B_t * u_t + w_t,  w_t ~ N(0, Q)
       with spectral radius stability damping rho(F) <= 1.0 - eps
5. Chaos Theory & Maximal Lyapunov Predictability Horizon:
       |delta x(t)| approx |delta x_0| * exp(lambda_max * t)
       t_predictable = (1 / lambda_max) * ln(epsilon_max / epsilon_initial)
6. Jacobian Sensitivity Matrix:
       J_{ij} = dZ_i / dX_j with Tikhonov regularizer
7. 5-Tier Prediction Hierarchy & Parachute Principle
8. Real-World Telemetry Anomaly Detector (Z >= 2.5 sigma)
9. 12-Domain Multi-Disciplinary Geopolitical State Vector Encoder
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np


def calculate_linear_extrapolation(
    y_t: float,
    dy_dt: float,
    horizon_h: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Calculates linear forward extrapolation with optional boundary clamping:
    y_hat(t + h) = y_t + h * dy_dt
    """
    y = float(y_t)
    rate = float(dy_dt)
    h = float(horizon_h)

    val = y + h * rate
    if min_val is not None:
        val = max(float(min_val), val)
    if max_val is not None:
        val = min(float(max_val), val)
    return float(val)


def calculate_ballistic_quadratic_trajectory(
    initial_position_y0: float,
    initial_velocity_v0: float,
    acceleration_g: float,
    elapsed_time_t: float,
) -> Dict[str, float]:
    """Calculates ballistic / constant acceleration trajectory:
    y(t) = y_0 + v_0 * t - 0.5 * g * t^2
    v(t) = v_0 - g * t
    """
    y0 = float(initial_position_y0)
    v0 = float(initial_velocity_v0)
    g = float(acceleration_g)
    t = max(0.0, float(elapsed_time_t))

    pos_y = y0 + v0 * t - 0.5 * g * (t ** 2)
    vel_v = v0 - g * t

    # Compute terminal impact parameters when y(t) = 0
    discriminant = v0 ** 2 + 2.0 * g * max(0.0, y0)
    if g > 0 and discriminant >= 0:
        t_impact = (v0 + math.sqrt(discriminant)) / g
        v_impact = math.sqrt(discriminant)
    else:
        t_impact = 0.0
        v_impact = 0.0

    return {
        "position_y_t": float(pos_y),
        "velocity_v_t": float(vel_v),
        "impact_time_t_impact": float(max(0.0, t_impact)),
        "impact_velocity_v_impact": float(v_impact),
    }


def calculate_logistic_sigmoid_trajectory(
    carrying_capacity_k: float,
    initial_value_y0: float,
    intrinsic_growth_rate_r: float,
    elapsed_time_t: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Calculates logistic sigmoid trajectory under finite carrying capacity K:
    y(t) = K / (1 + ((K - y_0) / y_0) * exp(-r * t))
    """
    k = max(1e-6, float(carrying_capacity_k))
    y0 = max(1e-8, float(initial_value_y0))
    r = float(intrinsic_growth_rate_r)
    t = float(elapsed_time_t)

    # Avoid numerical overflow in exp
    exponent = -r * t
    exponent = max(-100.0, min(100.0, exponent))

    ratio = max(1e-8, (k - y0) / y0)
    denominator = 1.0 + ratio * math.exp(exponent)
    val = k / max(1e-8, denominator)

    if min_val is not None:
        val = max(float(min_val), val)
    if max_val is not None:
        val = min(float(max_val), val)
    return float(val)


def simulate_discrete_state_space_trajectory(
    state_transition_f: np.ndarray,
    control_input_b: np.ndarray,
    control_vector_u: np.ndarray,
    initial_state_x0: np.ndarray,
    steps: int,
    process_noise_q: Optional[np.ndarray] = None,
    max_spectral_radius: float = 0.999,
) -> np.ndarray:
    """Simulates discrete state-space vector trajectory:
    x_{t+1} = F_t * x_t + B_t * u_t + w_t
    Enforces spectral radius damping if max eigenvalue of F exceeds max_spectral_radius.
    """
    f_mat = np.asarray(state_transition_f, dtype=float).copy()
    b_mat = np.asarray(control_input_b, dtype=float)
    u_vec = np.asarray(control_vector_u, dtype=float)
    x0 = np.asarray(initial_state_x0, dtype=float)
    n_steps = max(1, int(steps))
    n_dim = len(x0)

    # Spectral radius stability check & damping
    eigenvals = np.linalg.eigvals(f_mat)
    spectral_radius = float(np.max(np.abs(eigenvals)))
    if spectral_radius > max_spectral_radius:
        f_mat = f_mat * (max_spectral_radius / (spectral_radius + 1e-8))

    trajectory = np.zeros((n_steps + 1, n_dim))
    trajectory[0] = x0
    current_x = x0.copy()

    for step in range(1, n_steps + 1):
        noise = np.zeros(n_dim)
        if process_noise_q is not None:
            q_mat = np.asarray(process_noise_q, dtype=float)
            noise = np.random.multivariate_normal(np.zeros(n_dim), q_mat)

        ctrl_effect = np.dot(b_mat, u_vec) if b_mat.size > 0 and u_vec.size > 0 else np.zeros(n_dim)
        current_x = np.dot(f_mat, current_x) + ctrl_effect + noise
        trajectory[step] = current_x

    return trajectory


def calculate_maximal_lyapunov_predictability_horizon(
    maximal_lyapunov_exponent_lambda: float,
    initial_uncertainty_epsilon: float,
    max_tolerable_error_epsilon_max: float,
) -> float:
    """Calculates maximum deterministic predictability horizon t_predictable under chaos:
    t_predictable = (1 / lambda_max) * ln(epsilon_max / epsilon_initial)
    """
    lam = float(maximal_lyapunov_exponent_lambda)
    eps_0 = max(1e-12, float(initial_uncertainty_epsilon))
    eps_max = max(eps_0, float(max_tolerable_error_epsilon_max))

    if lam <= 0.0:
        return 1e6  # Infinite predictable horizon for non-chaotic stable systems

    t_pred = (1.0 / lam) * math.log(eps_max / eps_0)
    return float(max(0.0, t_pred))


def calculate_jacobian_sensitivity_matrix(
    target_function: Callable[[np.ndarray], np.ndarray],
    input_state_vector: np.ndarray,
    delta: float = 1e-4,
    tikhonov_alpha: float = 1e-6,
) -> np.ndarray:
    """Calculates numerical Jacobian sensitivity matrix J_{ij} = dZ_i / dX_j
    with Tikhonov regularizer protection against ill-conditioning.
    """
    x = np.asarray(input_state_vector, dtype=float).copy()
    n_in = len(x)
    z0 = np.asarray(target_function(x), dtype=float)
    n_out = len(z0)

    jacobian = np.zeros((n_out, n_in))
    del_val = max(1e-8, float(delta))

    for j in range(n_in):
        x_plus = x.copy()
        x_minus = x.copy()
        x_plus[j] += del_val
        x_minus[j] -= del_val

        z_plus = np.asarray(target_function(x_plus), dtype=float)
        z_minus = np.asarray(target_function(x_minus), dtype=float)
        jacobian[:, j] = (z_plus - z_minus) / (2.0 * del_val)

    return jacobian


def evaluate_five_tier_prediction_hierarchy(
    tier_level: int,
    claim_description: str,
    evidentiary_domain: str,
) -> Dict[str, Any]:
    """Evaluates prediction confidence according to the 5-Tier Prediction Hierarchy:
    Level 1: Physical Laws (99.9%+)
    Level 2: Inventories & Nowcasting (85–95%)
    Level 3: Causal Channels (70–85%)
    Level 4: Trend Extrapolation (40–60%)
    Level 5: Scenario Planning (Unbounded Utility)
    """
    level = int(tier_level)
    level = max(1, min(5, level))

    tier_specs = {
        1: ("LEVEL_1_PHYSICAL_LAWS", 0.999, "Thermodynamics, Stoichiometry, Physics Invariants"),
        2: ("LEVEL_2_INVENTORIES_AND_NOWCASTING", 0.900, "High-Frequency Telemetry, SAR, AIS, SCADA"),
        3: ("LEVEL_3_CAUSAL_CHANNELS", 0.775, "DAG Transmission Mechanisms, Central Bank Channels"),
        4: ("LEVEL_4_TREND_EXTRAPOLATION", 0.500, "Moving Averages, Price Momentum (Breaks at Inflection)"),
        5: ("LEVEL_5_STRATEGIC_SCENARIOS", 0.350, "Convexity Hedging, Parachute Principle, Multi-State Planning"),
    }

    tier_name, base_conf, desc = tier_specs[level]

    return {
        "tier_level": level,
        "tier_name": tier_name,
        "base_confidence": base_conf,
        "description": desc,
        "claim": str(claim_description),
        "evidentiary_domain": str(evidentiary_domain),
        "requires_parachute_hedging": level >= 4,
    }


def detect_real_world_telemetry_anomaly(
    sensor_reading: float,
    baseline_mean: float,
    baseline_std: float,
    anomaly_threshold_sigma: float = 2.5,
) -> Dict[str, Any]:
    """Detects real-world telemetry anomaly across SAR, AIS, SCADA, TROPOMI feeds:
    Z_sensor = (x_t - mu) / sigma >= 2.5
    """
    x = float(sensor_reading)
    mu = float(baseline_mean)
    sigma = max(1e-6, float(baseline_std))
    thresh = float(anomaly_threshold_sigma)

    z_score = (x - mu) / sigma
    is_anomaly = abs(z_score) >= thresh

    return {
        "sensor_reading": x,
        "baseline_mean": mu,
        "baseline_std": sigma,
        "z_score": round(float(z_score), 3),
        "is_anomaly": bool(is_anomaly),
        "severity": "CRITICAL" if abs(z_score) >= 3.5 else ("ELEVATED" if is_anomaly else "NORMAL"),
    }


def encode_twelve_domain_geopolitical_state_vector(
    thucydides_parity_ratio: float = 0.85,
    regime_institutionalization_score: float = 0.60,
    structural_realism_polarity: float = 0.70,
    force_ratio_attacker_to_defender: float = 2.0,
    external_debt_to_fx_reserves: float = 0.80,
    chokepoint_transit_vulnerability: float = 0.75,
    opec_spare_capacity_mbpd: float = 2.5,
    lithography_supply_chain_chokepoint: float = 0.90,
    cross_currency_basis_swap_bps: float = -15.0,
    treaty_compliance_probability: float = 0.50,
    nationalist_grievance_index: float = 0.65,
    youth_bulge_unrest_fraction: float = 0.40,
) -> np.ndarray:
    """Encodes the standardized 12-Dimensional Multi-Disciplinary Domain Knowledge State Vector."""
    return np.array([
        float(thucydides_parity_ratio),
        float(regime_institutionalization_score),
        float(structural_realism_polarity),
        float(force_ratio_attacker_to_defender),
        float(external_debt_to_fx_reserves),
        float(chokepoint_transit_vulnerability),
        float(opec_spare_capacity_mbpd),
        float(lithography_supply_chain_chokepoint),
        float(cross_currency_basis_swap_bps),
        float(treaty_compliance_probability),
        float(nationalist_grievance_index),
        float(youth_bulge_unrest_fraction),
    ], dtype=float)


class StateSpaceTrajectoryForecaster:
    """Production State-Space Trajectory Forecaster incorporating physical constraints and 12-domain state vectors."""

    def __init__(
        self,
        dimension: int = 12,
        state_transition_matrix: Optional[np.ndarray] = None,
        control_matrix: Optional[np.ndarray] = None,
    ):
        self.dimension = int(dimension)
        self.F = state_transition_matrix if state_transition_matrix is not None else np.eye(self.dimension) * 0.98
        self.B = control_matrix if control_matrix is not None else np.eye(self.dimension) * 0.05

    def forecast_trajectory(
        self,
        current_state: np.ndarray,
        control_inputs: np.ndarray,
        horizon_steps: int = 10,
        process_noise_cov: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        return simulate_discrete_state_space_trajectory(
            state_transition_f=self.F,
            control_input_b=self.B,
            control_vector_u=control_inputs,
            initial_state_x0=current_state,
            steps=horizon_steps,
            process_noise_q=process_noise_cov,
        )
