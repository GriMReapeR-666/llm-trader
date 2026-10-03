"""Tests for raw vs purified IC comparison in AlphaDecayMonitor.

Verifies that:
- Raw IC is computed when orthogonalize=True
- Summary includes ic_raw_mean, ic_raw_current, signal_purity
- format_report shows the comparison table
- No raw IC when orthogonalize=False
- Persistence saves/loads raw IC fields
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from core.alpha_decay_monitor import AlphaDecayMonitor, AlphaHealth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    opn = close + rng.randn(n) * 0.1
    vol = rng.uniform(1000, 5000, n)
    df = pd.DataFrame({
        "open": opn, "high": high, "low": low, "close": close, "volume": vol,
    })
    returns = df["close"].pct_change()
    for i, name in enumerate(["alpha_001", "alpha_012", "alpha_053"]):
        noise = rng.randn(n) * 0.5
        df[name] = returns.shift(1) * (0.3 + i * 0.1) + noise
    return df


def _alpha_cols():
    return ["alpha_001", "alpha_012", "alpha_053"]


# ---------------------------------------------------------------------------
# Raw IC computation
# ---------------------------------------------------------------------------

class TestRawICComputed:
    def test_raw_ic_populated_when_orthogonalized(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        health = monitor.update(df, _alpha_cols())
        for name in _alpha_cols():
            h = health[name]
            assert h.ic_raw_current != 0.0 or h.ic_raw_mean != 0.0, (
                f"{name}: raw IC should be computed when orthogonalize=True"
            )

    def test_raw_ic_zero_when_not_orthogonalized(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=False, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        health = monitor.update(df, _alpha_cols())
        for name in _alpha_cols():
            h = health[name]
            assert h.ic_raw_current == 0.0
            assert h.ic_raw_mean == 0.0

    def test_purified_differs_from_raw(self, tmp_path):
        """When orthogonalization is on, purified IC should differ from raw."""
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        health = monitor.update(df, _alpha_cols())
        # At least one alpha should show difference between raw and purified
        differences = []
        for name in _alpha_cols():
            h = health[name]
            if abs(h.ic_raw_mean) > 0.001:
                differences.append(abs(h.ic_mean - h.ic_raw_mean))
        assert any(d > 0.001 for d in differences), (
            "Expected at least one alpha to show difference between raw and purified IC"
        )


# ---------------------------------------------------------------------------
# Summary includes comparison data
# ---------------------------------------------------------------------------

class TestSummaryComparison:
    def test_summary_has_raw_fields(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        summary = monitor.get_summary()
        for name in _alpha_cols():
            info = summary["alphas"][name]
            assert "ic_raw_current" in info
            assert "ic_raw_mean" in info
            assert "signal_purity" in info

    def test_signal_purity_finite(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        summary = monitor.get_summary()
        for name in _alpha_cols():
            purity = summary["alphas"][name].get("signal_purity", 0.0)
            assert np.isfinite(purity), f"Purity not finite: {purity}"

    def test_no_raw_fields_when_not_orthogonalized(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=False, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        summary = monitor.get_summary()
        for name in _alpha_cols():
            info = summary["alphas"][name]
            assert "ic_raw_mean" not in info
            assert "signal_purity" not in info


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_report_shows_comparison_section(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        report = monitor.format_report()
        assert "RAW vs PURIFIED IC COMPARISON" in report
        assert "Purity" in report

    def test_report_shows_interpretation(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        report = monitor.format_report()
        # Should contain at least one interpretation label
        assert any(
            label in report
            for label in [
                "genuine alpha", "market beta", "market correlation",
                "No edge detected", "amplified by denoising",
            ]
        )

    def test_report_no_comparison_when_not_orthogonalized(self, tmp_path):
        df = _make_df()
        monitor = AlphaDecayMonitor(
            orthogonalize=False, ic_window=60,
            state_file=str(tmp_path / "state.json"),
        )
        monitor.update(df, _alpha_cols())
        report = monitor.format_report()
        assert "RAW vs PURIFIED IC COMPARISON" not in report


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_raw_ic_saved_and_loaded(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        df = _make_df()
        # Run and save
        m1 = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60, state_file=state_file,
        )
        m1.update(df, _alpha_cols())
        # Reload
        m2 = AlphaDecayMonitor(
            orthogonalize=True, ic_window=60, state_file=state_file,
        )
        for name in _alpha_cols():
            h = m2._health.get(name)
            assert h is not None
            assert h.ic_raw_current != 0.0 or h.ic_raw_mean != 0.0

    def test_raw_ic_not_in_state_when_not_orthogonalized(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        df = _make_df()
        m1 = AlphaDecayMonitor(
            orthogonalize=False, ic_window=60, state_file=state_file,
        )
        m1.update(df, _alpha_cols())
        with open(state_file) as f:
            data = json.load(f)
        for name in _alpha_cols():
            entry = data["health"][name]
            # Should not have raw IC fields (or they should be 0)
            assert entry.get("ic_raw_current", 0.0) == 0.0
            assert entry.get("ic_raw_mean", 0.0) == 0.0
