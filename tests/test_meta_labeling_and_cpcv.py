"""Unit Test Suite for Module 02: Triple Barrier Meta-Labeling & CPCV Validation Engine."""
import numpy as np
import pytest

from core.meta_labeling import (
    BarrierEvent,
    TripleBarrierPathDependentLabeler,
    apply_triple_barrier,
    MetaLabelingProbabilityClassifier,
    MetaLabeler,
    calculate_order_flow_imbalance,
    calculate_cot_commercial_hedger_index,
    calculate_operating_accruals_anomaly,
)
from core.cpcv_validation import CombinatorialPurgedCrossValidator


def test_triple_barrier_labeling_take_profit_and_stop_loss():
    # Sequence of prices: [100, 101, 102, 103, 104, 105, 95, 94]
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 95.0, 94.0]
    events = [0]  # Enter at bar 0 ($100.0)
    volatility = [2.0] * len(prices)  # 1 ATR = $2.0

    # Upper barrier: 100 + (1.5 * 2.0) = $103.0 (hit at bar 3)
    # Lower barrier: 100 - (1.0 * 2.0) = $98.0
    results = TripleBarrierPathDependentLabeler.apply(
        prices=prices,
        events=events,
        upper_r=1.5,
        lower_r=1.0,
        vertical_barrier_bars=5,
        volatility=volatility,
    )
    assert len(results) == 1
    ev = results[0]
    assert ev.touch_type == "UPPER_TP"
    assert ev.label == 1
    assert ev.exit_price == 103.0
    assert ev.exit_index == 3
    assert ev.realized_r >= 1.5


def test_meta_labeling_probability_classifier():
    np.random.seed(42)
    # 50 training setups with features X and labels y
    X = np.random.normal(0, 1, (60, 3))
    y = (X[:, 0] + X[:, 1] > 0).astype(int)

    clf = MetaLabelingProbabilityClassifier(confidence_threshold=0.60)
    clf.fit(X, y)

    probas = clf.predict_proba(X)
    assert len(probas) == len(X)
    assert np.all((probas >= 0.0) & (probas <= 1.0))

    signals = clf.filter_signals(X)
    assert len(signals) == len(X)


def test_free_data_inefficiency_signals():
    # 1. Order Flow Imbalance: Bid Delta = +500, Ask Delta = -200 -> OFI = +700
    ofi = calculate_order_flow_imbalance(delta_bid_qty=500.0, delta_ask_qty=-200.0)
    assert ofi == 700.0

    # 2. CFTC COT Commercial Index: Current = 80k, 3Y Min = 20k, 3Y Max = 100k -> (60/80)*100 = 75.0%
    cot_idx = calculate_cot_commercial_hedger_index(
        current_net_commercial=80000.0,
        rolling_3y_min_net=20000.0,
        rolling_3y_max_net=100000.0,
    )
    assert pytest.approx(cot_idx, 1e-4) == 75.0

    # 3. Operating Accruals Anomaly: Net Income = $120M, Operating Cash Flow = $150M, Total Assets = $1000M
    # Accruals = (120 - 150) / 1000 = -0.03 (High Cash Quality)
    accruals = calculate_operating_accruals_anomaly(
        net_income=120.0,
        operating_cash_flow=150.0,
        total_assets=1000.0,
    )
    assert pytest.approx(accruals, 1e-4) == -0.03


def test_combinatorial_purged_cross_validator():
    cpcv = CombinatorialPurgedCrossValidator(n_splits=6, n_test_splits=2, embargo_pct=0.01)
    splits = cpcv.split(n_samples=300)
    # C(6, 2) = 15 combinations
    assert len(splits) == 15

    for s in splits:
        assert len(s["train_indices"]) > 0
        assert len(s["test_indices"]) > 0
        # Ensure no overlap between train and test
        assert len(set(s["train_indices"]).intersection(set(s["test_indices"]))) == 0

    # Test PBO calculation (fractional rank-degradation methodology —
    # Bailey & López de Prado: PBO = fraction of strategies whose OOS rank
    # falls below their IS rank; matches the fractional PBO>0.35 gate used
    # by auto_quant_tools. The pre-fix binary 0.0/1.0 only flagged the
    # extreme case where the single best-IS model collapses OOS.)
    pbo = CombinatorialPurgedCrossValidator.calculate_probability_of_backtest_overfitting(
        in_sample_sharpes=[2.5, 2.1, 1.8, 1.5],
        out_of_sample_sharpes=[0.8, 1.9, 1.6, 1.2],  # Model 0 top IS (2.5) → lowest OOS (0.8)
    )
    # IS ranks [3,2,1,0] vs OOS ranks [0,3,2,1]: only model 0 degraded → 1/4
    assert pbo == 0.25
