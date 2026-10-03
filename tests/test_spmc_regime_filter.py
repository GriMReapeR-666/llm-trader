import pytest
from core.spmc_regime_filter import SPMCRegimeFilter, SPMCTransitionMatrix


def test_spmc_transition_matrix_properties():
    mat = SPMCTransitionMatrix()
    # Test theoretical properties from Tolusic (2026) Table 3
    assert abs(mat.stationary_g - 0.4987) < 0.005
    assert abs(mat.stationary_b - 0.5012) < 0.005
    assert abs(mat.expected_duration_g - 4.83) < 0.05
    assert abs(mat.expected_duration_b - 4.85) < 0.05


def test_spmc_regime_tracking_and_quarter_sizing():
    flt = SPMCRegimeFilter(rolling_window_k=3, threshold_tau=0.50, bad_regime_multiplier=0.25)
    
    # 1. Start with initial state
    snap = flt.current_state()
    assert snap.trade_count == 0
    assert snap.is_edge_active is True

    # 2. Feed positive trades (winning streak)
    for _ in range(5):
        snap = flt.update(net_pips=25.0)
    
    assert snap.current_regime == "GOOD"
    assert snap.history_state == "GG"
    assert snap.sizing_multiplier == 1.0
    assert snap.posterior_beta > 0.50
    assert snap.is_edge_active is True

    # 3. Feed a sequence of losing trades (entering Bad regime)
    for _ in range(5):
        snap = flt.update(net_pips=-30.0)
    
    # Strategy edge is now recognized as Bad/inactive
    assert snap.history_state == "BB"
    assert snap.posterior_beta < 0.50
    # Position sizing is throttled to 25% (quarter-size)
    assert snap.sizing_multiplier == 0.25
    assert snap.is_edge_active is False

    # 4. Feed a recovery streak (recovering back to Good)
    for _ in range(6):
        snap = flt.update(net_pips=40.0)
    
    assert snap.current_regime == "GOOD"
    assert snap.sizing_multiplier == 1.0
    assert snap.is_edge_active is True
