"""P03/P04 (Kamat 2026b/c) - PRFS tracker + five-tier precision audit."""
import pytest

from core.prfs_counterfactual import (
    ForwardObservation,
    OutcomeTier,
    PRFSConfig,
    PRFSTracker,
    RejectionEvent,
    audit,
    classify_event,
    headline_ratio,
)


def _ev(key="k1", reason="filter_1", ref=100.0):
    return RejectionEvent(key=key, symbol="EURUSD", reason=reason, ref_price=ref)


def _obs(key, prices, start_age=0):
    return [ForwardObservation(key=key, age_bars=start_age + i, price=p)
            for i, p in enumerate(prices)]


def test_saved_windowed_halving():
    tier = classify_event(_ev(), _obs("k1", [100.0, 90.0, 49.0, 60.0]))
    assert tier is OutcomeTier.SAVED_WINDOWED


def test_missed_moon_doubling():
    tier = classify_event(_ev(), _obs("k1", [100.0, 150.0, 205.0]))
    assert tier is OutcomeTier.MISSED_MOON


def test_missed_over_saved_tiebreak():
    # Both trough <=50% and peak >=200%: paper mandates MISSED
    tier = classify_event(_ev(), _obs("k1", [100.0, 45.0, 210.0]))
    assert tier is OutcomeTier.MISSED_MOON


def test_flat_bounded_trajectory():
    tier = classify_event(_ev(), _obs("k1", [100.0, 105.0, 95.0, 102.0]))
    assert tier is OutcomeTier.FLAT


def test_early_death_single_young_sample():
    tier = classify_event(_ev(), _obs("k1", [100.0], start_age=1))
    assert tier is OutcomeTier.SAVED_EARLY_DEATH


def test_unclassifiable_single_old_sample():
    tier = classify_event(_ev(), _obs("k1", [100.0], start_age=10))
    assert tier is OutcomeTier.UNCLASSIFIABLE


def test_unclassifiable_no_valid_prices():
    tier = classify_event(_ev(), [ForwardObservation(key="k1", age_bars=1, price=None)])
    assert tier is OutcomeTier.UNCLASSIFIABLE


def test_tracker_cadence_dense_then_thin():
    t = PRFSTracker(PRFSConfig(horizon_bars=12, dense_bars=2, thin_every=3))
    t.record_rejection(_ev())
    assert t.due("k1", 1) and t.due("k1", 2)
    assert not t.due("k1", 3) and not t.due("k1", 4)
    assert t.due("k1", 5) and not t.due("k1", 6)


def test_tracker_disappearance_termination():
    t = PRFSTracker(PRFSConfig(max_missing=3))
    t.record_rejection(_ev())
    for i in range(3):
        t.record_observation(ForwardObservation(key="k1", age_bars=i, price=None))
    assert not t.due("k1", 4)


def test_audit_report_and_headline():
    t = PRFSTracker()
    t.record_rejection(_ev("a", reason="filter_1"))
    t.record_rejection(_ev("b", reason="filter_1"))
    t.record_rejection(_ev("c", reason="filter_2"))
    for o in _obs("a", [100.0, 40.0]):
        t.record_observation(o)
    for o in _obs("b", [100.0, 250.0]):
        t.record_observation(o)
    for o in _obs("c", [100.0, 101.0]):
        t.record_observation(o)
    rep = audit(t)
    assert rep["filter_1"].saved_windowed == 1
    assert rep["filter_1"].missed == 1
    assert rep["filter_2"].flat == 1
    assert headline_ratio(rep) == 1.0
    # n<5: no confident per-filter verdict (P04: n=4 insufficient)
    assert rep["filter_1"].net_positive is None


def test_net_positive_needs_sample_size():
    row_t = PRFSTracker()
    for i in range(6):
        row_t.record_rejection(_ev(f"s{i}", reason="filter_9"))
        for o in _obs(f"s{i}", [100.0, 40.0]):
            row_t.record_observation(o)
    rep = audit(row_t)
    assert rep["filter_9"].net_positive is True
    assert rep["filter_9"].conservative_ratio is None  # no misses -> None, not inf


def test_rejection_sink_node_prfs_wiring(tmp_path):
    from intelligence.graph_nodes import RejectionSinkNode

    db_file = str(tmp_path / "test_prfs_sink.db")
    sink = RejectionSinkNode(db_path=db_file)
    assert sink.prfs_tracker is not None

    state = {
        "symbol": "EURUSD",
        "cycle_id": "c123",
        "rejection_reasons": ["max_drawdown_limit"],
        "fused_proposal": {"price": 1.0850, "direction": "BUY", "size": 0.1},
    }

    res = sink(state)
    assert res["status"] == "REJECTED"

    # Verify event recorded in tracker
    key = "EURUSD:c123"
    assert key in sink.prfs_tracker._jobs

    # Feed price drops (simulating a drawdown save: 1.0850 -> 0.5000)
    sink.observe_price(key, age_bars=1, price=1.0850)
    sink.observe_price(key, age_bars=2, price=0.5000)

    # Check precision audit
    report = sink.audit_rejections()
    assert "max_drawdown_limit" in report
    assert report["max_drawdown_limit"].saved_windowed == 1

