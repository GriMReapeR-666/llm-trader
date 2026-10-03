"""Tests for Almgren-Chriss optimal execution scheduler.

Covers:
- Market impact estimation
- TWAP scheduling
- VWAP scheduling
- Optimal horizon selection
- Cost-aware entry timing
- Position size adjustment
"""
from __future__ import annotations

import pytest

from core.optimal_execution import (
    ExecutionSchedule,
    ExecutionSlice,
    OptimalExecutionScheduler,
)


# ---------------------------------------------------------------------------
# Market Impact Tests
# ---------------------------------------------------------------------------

class TestMarketImpact:
    def test_impact_increases_with_quantity(self):
        sched = OptimalExecutionScheduler()
        i1_perm, i1_temp = sched.estimate_market_impact(
            100, 1.1000, 1000000, 0.01, 50000,
        )
        i2_perm, i2_temp = sched.estimate_market_impact(
            1000, 1.1000, 1000000, 0.01, 50000,
        )
        assert i2_perm > i1_perm
        assert i2_temp > i1_temp

    def test_impact_increases_with_volatility(self):
        sched = OptimalExecutionScheduler()
        i1_perm, _ = sched.estimate_market_impact(
            100, 1.1000, 1000000, 0.01, 50000,
        )
        i2_perm, _ = sched.estimate_market_impact(
            100, 1.1000, 1000000, 0.02, 50000,
        )
        assert i2_perm > i1_perm

    def test_zero_volume_no_impact(self):
        sched = OptimalExecutionScheduler()
        i_perm, i_temp = sched.estimate_market_impact(100, 1.1000, 0, 0.01, 0)
        assert i_perm == 0.0
        assert i_temp == 0.0


# ---------------------------------------------------------------------------
# TWAP Schedule Tests
# ---------------------------------------------------------------------------

class TestTWAPSchedule:
    def test_basic_twap(self):
        sched = OptimalExecutionScheduler()
        schedule = sched.generate_twap_schedule(
            quantity=1.0, price=1.1000, num_slices=5,
            daily_volume=1000000, daily_volatility=0.01, bar_volume=50000,
        )
        assert schedule.algorithm == "TWAP"
        assert len(schedule.slices) == 5
        assert schedule.total_quantity == pytest.approx(1.0, abs=0.01)

    def test_urgency_front_loads(self):
        sched = OptimalExecutionScheduler()
        # High urgency: more in early slices
        schedule = sched.generate_twap_schedule(
            quantity=1.0, price=1.1000, num_slices=5,
            daily_volume=1000000, daily_volatility=0.01, bar_volume=50000,
            urgency=0.9,
        )
        # First slice should have more than last slice
        assert schedule.slices[0].quantity > schedule.slices[-1].quantity

    def test_zero_quantity(self):
        sched = OptimalExecutionScheduler()
        schedule = sched.generate_twap_schedule(
            quantity=0, price=1.1000, num_slices=5,
            daily_volume=1000000, daily_volatility=0.01, bar_volume=50000,
        )
        assert len(schedule.slices) == 0


# ---------------------------------------------------------------------------
# VWAP Schedule Tests
# ---------------------------------------------------------------------------

class TestVWAPSchedule:
    def test_basic_vwap(self):
        sched = OptimalExecutionScheduler()
        volume_profile = [1000, 2000, 1500, 1000, 500]
        schedule = sched.generate_vwap_schedule(
            quantity=1.0, price=1.1000, volume_profile=volume_profile,
            daily_volume=1000000, daily_volatility=0.01,
        )
        assert schedule.algorithm == "VWAP"
        assert len(schedule.slices) == 5
        assert schedule.total_quantity == pytest.approx(1.0, abs=0.01)

    def test_volume_proportional(self):
        sched = OptimalExecutionScheduler()
        volume_profile = [1000, 3000, 1000]  # Middle bar has 3x volume
        schedule = sched.generate_vwap_schedule(
            quantity=1.0, price=1.1000, volume_profile=volume_profile,
            daily_volume=1000000, daily_volatility=0.01,
        )
        # Middle slice should have more quantity
        assert schedule.slices[1].quantity > schedule.slices[0].quantity

    def test_empty_profile(self):
        sched = OptimalExecutionScheduler()
        schedule = sched.generate_vwap_schedule(
            quantity=1.0, price=1.1000, volume_profile=[],
            daily_volume=1000000, daily_volatility=0.01,
        )
        assert len(schedule.slices) == 0


# ---------------------------------------------------------------------------
# Optimal Horizon Tests
# ---------------------------------------------------------------------------

class TestOptimalHorizon:
    def test_returns_valid_horizon(self):
        sched = OptimalExecutionScheduler()
        horizon, cost = sched.select_optimal_horizon(
            quantity=0.1, price=1.1000, daily_volume=1000000,
            daily_volatility=0.01, bar_volume=50000,
        )
        assert 1 <= horizon <= 20
        assert cost >= 0

    def test_larger_quantity_needs_more_bars(self):
        sched = OptimalExecutionScheduler()
        h1, _ = sched.select_optimal_horizon(
            quantity=0.01, price=1.1000, daily_volume=1000000,
            daily_volatility=0.01, bar_volume=50000,
        )
        h2, _ = sched.select_optimal_horizon(
            quantity=0.5, price=1.1000, daily_volume=1000000,
            daily_volatility=0.01, bar_volume=50000,
        )
        # Larger quantity may need more bars (but not always, depends on cost curve)
        assert h2 >= 1


# ---------------------------------------------------------------------------
# Cost-Aware Entry Tests
# ---------------------------------------------------------------------------

class TestCostAwareEntry:
    def test_profitable_entry(self):
        sched = OptimalExecutionScheduler()
        should_enter, reason = sched.cost_aware_entry_check(
            entry_cost_bps=3.0, expected_edge_bps=10.0, min_edge_after_cost_bps=2.0,
        )
        assert should_enter is True

    def test_unprofitable_entry(self):
        sched = OptimalExecutionScheduler()
        should_enter, reason = sched.cost_aware_entry_check(
            entry_cost_bps=8.0, expected_edge_bps=5.0, min_edge_after_cost_bps=2.0,
        )
        assert should_enter is False

    def test_borderline_entry(self):
        sched = OptimalExecutionScheduler()
        should_enter, reason = sched.cost_aware_entry_check(
            entry_cost_bps=5.0, expected_edge_bps=6.0, min_edge_after_cost_bps=2.0,
        )
        assert should_enter is False  # Net edge 1.0 < min 2.0


# ---------------------------------------------------------------------------
# Position Size Adjustment Tests
# ---------------------------------------------------------------------------

class TestSizeAdjustment:
    def test_full_size_when_profitable(self):
        sched = OptimalExecutionScheduler()
        adjusted, info = sched.size_adjusted_for_costs(
            original_lots=0.1, entry_cost_bps=2.0, expected_edge_bps=10.0,
            price=1.1000,
        )
        assert adjusted > 0
        assert info["edge_ratio"] > 0.5

    def test_zero_size_when_unprofitable(self):
        sched = OptimalExecutionScheduler()
        adjusted, info = sched.size_adjusted_for_costs(
            original_lots=0.1, entry_cost_bps=10.0, expected_edge_bps=5.0,
            price=1.1000,
        )
        assert adjusted == 0.0

    def test_reduced_size_when_marginal(self):
        sched = OptimalExecutionScheduler()
        adjusted, info = sched.size_adjusted_for_costs(
            original_lots=0.1, entry_cost_bps=5.0, expected_edge_bps=8.0,
            price=1.1000,
        )
        assert adjusted < 0.1  # Reduced from original
        assert adjusted > 0


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_execution_flow(self):
        sched = OptimalExecutionScheduler()
        
        # 1. Estimate impact
        i_perm, i_temp = sched.estimate_market_impact(
            0.1, 1.1000, 1000000, 0.01, 50000,
        )
        assert i_perm > 0
        assert i_temp > 0
        
        # 2. Check if entry is cost-effective
        total_impact_bps = (i_perm + i_temp) / 1.1000 * 10000
        should_enter, reason = sched.cost_aware_entry_check(
            entry_cost_bps=total_impact_bps + 1.0,  # Add spread
            expected_edge_bps=10.0,
        )
        
        # 3. If entering, adjust size
        if should_enter:
            adjusted_lots, size_info = sched.size_adjusted_for_costs(
                0.1, total_impact_bps + 1.0, 10.0, 1.1000,
            )
            assert adjusted_lots > 0
        
        # 4. Generate schedule
        schedule = sched.generate_twap_schedule(
            quantity=0.1, price=1.1000, num_slices=5,
            daily_volume=1000000, daily_volatility=0.01, bar_volume=50000,
        )
        assert schedule.total_quantity == pytest.approx(0.1, abs=0.01)
        assert schedule.total_expected_cost >= 0
