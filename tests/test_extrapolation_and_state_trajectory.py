"""Unit tests for Module 01: Predictive Analysis, Extrapolation Mathematics & State Trajectory Forecasting."""
import math
import numpy as np
import pytest

from intelligence.extrapolation_and_state_trajectory import (
    calculate_linear_extrapolation,
    calculate_ballistic_quadratic_trajectory,
    calculate_logistic_sigmoid_trajectory,
    simulate_discrete_state_space_trajectory,
    calculate_maximal_lyapunov_predictability_horizon,
    calculate_jacobian_sensitivity_matrix,
    evaluate_five_tier_prediction_hierarchy,
    detect_real_world_telemetry_anomaly,
    encode_twelve_domain_geopolitical_state_vector,
    StateSpaceTrajectoryForecaster,
)


def test_linear_extrapolation_and_clamping():
    # Base: y=100, dy/dt=5, h=4 -> 120
    val = calculate_linear_extrapolation(y_t=100.0, dy_dt=5.0, horizon_h=4.0)
    assert val == 120.0

    # Test clamping: max_val=110 -> 110
    val_clamped = calculate_linear_extrapolation(y_t=100.0, dy_dt=5.0, horizon_h=4.0, max_val=110.0)
    assert val_clamped == 110.0


def test_ballistic_quadratic_trajectory():
    # Dropping from 100m height with v0 = 0 under gravity g = 9.8 m/s^2
    # y(t) = 100 - 0.5 * 9.8 * t^2
    res = calculate_ballistic_quadratic_trajectory(
        initial_position_y0=100.0,
        initial_velocity_v0=0.0,
        acceleration_g=9.8,
        elapsed_time_t=2.0,
    )
    assert pytest.approx(res["position_y_t"], 1e-2) == 100.0 - 0.5 * 9.8 * 4.0
    assert pytest.approx(res["velocity_v_t"], 1e-2) == -19.6
    # t_impact = sqrt(2 * 100 / 9.8) = sqrt(20.408) ~ 4.5175s
    assert pytest.approx(res["impact_time_t_impact"], 1e-2) == math.sqrt(200.0 / 9.8)
    assert res["impact_velocity_v_impact"] > 0.0


def test_logistic_sigmoid_carrying_capacity():
    # Capacity K = 1000, initial y0 = 100, growth rate r = 0.5, large t -> approaches 1000
    val_mid = calculate_logistic_sigmoid_trajectory(
        carrying_capacity_k=1000.0,
        initial_value_y0=100.0,
        intrinsic_growth_rate_r=0.5,
        elapsed_time_t=5.0,
    )
    assert 100.0 < val_mid < 1000.0

    val_inf = calculate_logistic_sigmoid_trajectory(
        carrying_capacity_k=1000.0,
        initial_value_y0=100.0,
        intrinsic_growth_rate_r=0.5,
        elapsed_time_t=50.0,
    )
    assert pytest.approx(val_inf, abs=1.0) == 1000.0


def test_discrete_state_space_trajectory_and_damping():
    # Stable 2D oscillator / decay system
    F = np.array([[0.90, 0.05], [0.00, 0.90]])
    B = np.array([[0.10], [0.10]])
    u = np.array([1.0])
    x0 = np.array([10.0, 5.0])

    traj = simulate_discrete_state_space_trajectory(
        state_transition_f=F,
        control_input_b=B,
        control_vector_u=u,
        initial_state_x0=x0,
        steps=5,
    )
    assert traj.shape == (6, 2)
    assert np.all(np.isfinite(traj))


def test_maximal_lyapunov_predictability_horizon():
    # lambda = 0.1 / day, eps_0 = 0.01, eps_max = 1.0 -> t_pred = 1/0.1 * ln(100) = 10 * 4.605 = 46.05 days
    t_pred = calculate_maximal_lyapunov_predictability_horizon(
        maximal_lyapunov_exponent_lambda=0.1,
        initial_uncertainty_epsilon=0.01,
        max_tolerable_error_epsilon_max=1.0,
    )
    assert pytest.approx(t_pred, 1e-2) == 46.0517

    # Stable non-chaotic (lambda <= 0) -> infinite horizon
    t_stable = calculate_maximal_lyapunov_predictability_horizon(
        maximal_lyapunov_exponent_lambda=0.0,
        initial_uncertainty_epsilon=0.01,
        max_tolerable_error_epsilon_max=1.0,
    )
    assert t_stable >= 1e5


def test_jacobian_sensitivity_matrix():
    # Target function: Z_0 = X_0^2 + 2*X_1, Z_1 = 3*X_0 * X_1
    def target_func(x):
        return np.array([x[0] ** 2 + 2.0 * x[1], 3.0 * x[0] * x[1]])

    x0 = np.array([2.0, 3.0])
    # Analytical Jacobian:
    # dZ_0/dX_0 = 2*X_0 = 4, dZ_0/dX_1 = 2
    # dZ_1/dX_0 = 3*X_1 = 9, dZ_1/dX_1 = 3*X_0 = 6
    jac = calculate_jacobian_sensitivity_matrix(target_func, x0)
    assert pytest.approx(jac[0, 0], 1e-3) == 4.0
    assert pytest.approx(jac[0, 1], 1e-3) == 2.0
    assert pytest.approx(jac[1, 0], 1e-3) == 9.0
    assert pytest.approx(jac[1, 1], 1e-3) == 6.0


def test_five_tier_prediction_hierarchy():
    tier1 = evaluate_five_tier_prediction_hierarchy(1, "Haber-Bosch floor", "THERMODYNAMICS")
    assert tier1["base_confidence"] >= 0.99
    assert tier1["requires_parachute_hedging"] is False

    tier4 = evaluate_five_tier_prediction_hierarchy(4, "Moving average cross", "TECHNICAL_PRICE")
    assert tier4["base_confidence"] <= 0.60
    assert tier4["requires_parachute_hedging"] is True


def test_detect_real_world_telemetry_anomaly():
    # Baseline mean 100, std 10, reading 130 (Z = 3.0 > 2.5) -> Anomaly
    res_anom = detect_real_world_telemetry_anomaly(
        sensor_reading=130.0,
        baseline_mean=100.0,
        baseline_std=10.0,
        anomaly_threshold_sigma=2.5,
    )
    assert res_anom["is_anomaly"] is True
    assert res_anom["z_score"] == 3.0

    # Reading 105 (Z = 0.5 < 2.5) -> Normal
    res_norm = detect_real_world_telemetry_anomaly(
        sensor_reading=105.0,
        baseline_mean=100.0,
        baseline_std=10.0,
        anomaly_threshold_sigma=2.5,
    )
    assert res_norm["is_anomaly"] is False


def test_encode_twelve_domain_geopolitical_state_vector():
    vec = encode_twelve_domain_geopolitical_state_vector(
        thucydides_parity_ratio=0.90,
        regime_institutionalization_score=0.55,
        opec_spare_capacity_mbpd=1.8,
    )
    assert len(vec) == 12
    assert vec[0] == 0.90
    assert vec[6] == 1.8


def test_state_space_trajectory_forecaster_engine():
    forecaster = StateSpaceTrajectoryForecaster(dimension=12)
    x0 = encode_twelve_domain_geopolitical_state_vector()
    u = np.zeros(12)
    traj = forecaster.forecast_trajectory(current_state=x0, control_inputs=u, horizon_steps=10)
    assert traj.shape == (11, 12)
    assert np.all(np.isfinite(traj))
