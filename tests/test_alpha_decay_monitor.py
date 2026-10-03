"""Tests for the Alpha Decay Monitor."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alpha_decay_monitor import (
    AlphaDecayMonitor,
    AlphaHealth,
    DecayAlert,
    _compute_ic_trend,
    _compute_rolling_ic,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_data() -> pd.DataFrame:
    """Generate 300 bars of OHLCV + alpha data with known IC patterns."""
    np.random.seed(42)
    n = 300
    close_vals = 1.1000 + np.cumsum(np.random.randn(n) * 0.0010)
    close = pd.Series(close_vals)
    fwd_ret = close.pct_change().shift(-1)

    df = pd.DataFrame({
        "open": close_vals + np.random.randn(n) * 0.0003,
        "high": close_vals + np.abs(np.random.randn(n) * 0.0005),
        "low": close_vals - np.abs(np.random.randn(n) * 0.0005),
        "close": close_vals,
        "volume": np.random.randint(1000, 50000, size=n).astype(float),
        # Good alpha: positively correlated with forward returns
        "alpha_good": fwd_ret.fillna(0).values * 100 + np.random.randn(n) * 0.001,
        # Noise alpha: no predictive power
        "alpha_noise": np.random.randn(n) * 0.001,
        # Bad alpha: negatively correlated (will reverse)
        "alpha_bad": -fwd_ret.fillna(0).values * 80 + np.random.randn(n) * 0.001,
    })
    return df


@pytest.fixture
def temp_state_file():
    """Temporary state file for persistence tests."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# IC computation tests
# ---------------------------------------------------------------------------

class TestICComputation:
    def test_rolling_ic_length(self, sample_data):
        ic = _compute_rolling_ic(
            sample_data["alpha_good"],
            sample_data["close"].pct_change().shift(-1),
            window=60,
        )
        assert len(ic) == len(sample_data)

    def test_rolling_ic_range(self, sample_data):
        ic = _compute_rolling_ic(
            sample_data["alpha_good"],
            sample_data["close"].pct_change().shift(-1),
            window=60,
        )
        valid = ic.dropna()
        assert (valid >= -1.01).all()
        assert (valid <= 1.01).all()

    def test_ic_trend_positive_for_improving(self):
        """Linearly increasing IC should have positive trend."""
        ic = pd.Series(np.linspace(0, 0.5, 100))
        trend = _compute_ic_trend(ic, window=50)
        assert trend > 0

    def test_ic_trend_negative_for_decaying(self):
        """Linearly decreasing IC should have negative trend."""
        ic = pd.Series(np.linspace(0.5, 0, 100))
        trend = _compute_ic_trend(ic, window=50)
        assert trend < 0

    def test_ic_trend_zero_for_flat(self):
        """Constant IC should have near-zero trend."""
        ic = pd.Series([0.1] * 100)
        trend = _compute_ic_trend(ic, window=50)
        assert abs(trend) < 0.001


# ---------------------------------------------------------------------------
# Monitor lifecycle tests
# ---------------------------------------------------------------------------

class TestAlphaDecayMonitor:
    def test_update_returns_health(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(ic_window=20, state_file=temp_state_file)
        health = monitor.update(
            sample_data, ["alpha_good", "alpha_noise", "alpha_bad"]
        )
        assert len(health) == 3
        for name in ["alpha_good", "alpha_noise", "alpha_bad"]:
            assert name in health
            assert isinstance(health[name], AlphaHealth)

    def test_health_has_ic_values(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(ic_window=20, state_file=temp_state_file)
        health = monitor.update(sample_data, ["alpha_good"])
        h = health["alpha_good"]
        assert h.ic_current != 0 or h.ic_mean != 0
        assert h.ic_std >= 0

    def test_good_alpha_is_healthy(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(
            ic_window=20, decay_threshold=0.02, consecutive_bars=5,
            state_file=temp_state_file,
        )
        health = monitor.update(sample_data, ["alpha_good"])
        # Good alpha should be HEALTHY (IC > threshold)
        assert health["alpha_good"].status in ("HEALTHY", "DEGRADED")

    def test_no_alerts_for_healthy_alphas(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(
            ic_window=20, consecutive_bars=10, state_file=temp_state_file
        )
        monitor.update(sample_data, ["alpha_good"])
        alerts = monitor.check_decay()
        # Healthy alpha should not trigger alerts
        degraded = [a for a in alerts if "good" in a.alpha_name]
        assert len(degraded) == 0


# ---------------------------------------------------------------------------
# Decay detection tests
# ---------------------------------------------------------------------------

class TestDecayDetection:
    def test_degraded_alpha_triggers_warning(self):
        """An alpha with consistently low IC should trigger WARNING."""
        np.random.seed(42)
        n = 200
        # Create alpha with IC near zero (no predictive power)
        df = pd.DataFrame({
            "open": 1.1 + np.random.randn(n) * 0.001,
            "high": 1.1 + np.abs(np.random.randn(n) * 0.001),
            "low": 1.1 - np.abs(np.random.randn(n) * 0.001),
            "close": 1.1 + np.cumsum(np.random.randn(n) * 0.0001),
            "volume": np.random.randint(1000, 50000, n).astype(float),
            "alpha_dead": np.random.randn(n) * 0.001,  # pure noise
        })
        monitor = AlphaDecayMonitor(
            ic_window=20,
            decay_threshold=0.05,
            consecutive_bars=3,
            cooldown_seconds=0,  # no cooldown for testing
        )
        monitor.update(df, ["alpha_dead"])
        # Need multiple updates to build consecutive low count
        for _ in range(5):
            monitor.update(df, ["alpha_dead"])
        alerts = monitor.check_decay()
        # Should get at least one alert for the noise alpha
        dead_alerts = [a for a in alerts if "dead" in a.alpha_name]
        assert len(dead_alerts) >= 1
        assert dead_alerts[0].severity in ("WARNING", "CRITICAL")

    def test_reversed_alpha_triggers_critical(self):
        """An alpha with negative IC should trigger CRITICAL."""
        np.random.seed(42)
        n = 200
        close = 1.1 + np.cumsum(np.random.randn(n) * 0.0001)
        fwd = pd.Series(close).pct_change().shift(-1)
        df = pd.DataFrame({
            "open": 1.1 + np.random.randn(n) * 0.001,
            "high": 1.1 + np.abs(np.random.randn(n) * 0.001),
            "low": 1.1 - np.abs(np.random.randn(n) * 0.001),
            "close": close,
            "volume": np.random.randint(1000, 50000, n).astype(float),
            # Strongly negatively correlated with forward returns
            "alpha_reversed": -fwd.fillna(0).values * 100 + np.random.randn(n) * 0.001,
        })
        monitor = AlphaDecayMonitor(
            ic_window=20,
            reversal_threshold=-0.01,
            consecutive_bars=3,
            cooldown_seconds=0,
        )
        for _ in range(5):
            monitor.update(df, ["alpha_reversed"])
        alerts = monitor.check_decay()
        critical = [a for a in alerts if a.severity == "CRITICAL"]
        assert len(critical) >= 1


# ---------------------------------------------------------------------------
# Status determination tests
# ---------------------------------------------------------------------------

class TestStatus:
    def test_healthy_status(self):
        monitor = AlphaDecayMonitor()
        status = monitor._determine_status(
            ic_current=0.1, ic_mean=0.08, ic_trend=0.001, consecutive_low=0
        )
        assert status == "HEALTHY"

    def test_degraded_status(self):
        monitor = AlphaDecayMonitor(consecutive_bars=3)
        status = monitor._determine_status(
            ic_current=0.01, ic_mean=0.015, ic_trend=-0.002, consecutive_low=5
        )
        assert status == "DEGRADED"

    def test_reversed_status(self):
        monitor = AlphaDecayMonitor(
            reversal_threshold=-0.01, consecutive_bars=3
        )
        status = monitor._determine_status(
            ic_current=-0.02, ic_mean=-0.015, ic_trend=-0.001, consecutive_low=5
        )
        assert status == "REVERSED"

    def test_dead_status(self):
        monitor = AlphaDecayMonitor(reversal_threshold=-0.01)
        status = monitor._determine_status(
            ic_current=0.01, ic_mean=-0.02, ic_trend=-0.001, consecutive_low=0
        )
        assert status == "DEAD"


# ---------------------------------------------------------------------------
# State persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load(self, sample_data, temp_state_file):
        monitor1 = AlphaDecayMonitor(
            ic_window=20, state_file=temp_state_file
        )
        monitor1.update(sample_data, ["alpha_good", "alpha_noise"])
        assert os.path.exists(temp_state_file)

        # Load into new monitor
        monitor2 = AlphaDecayMonitor(
            ic_window=20, state_file=temp_state_file
        )
        assert "alpha_good" in monitor2._health
        assert "alpha_noise" in monitor2._health
        assert monitor2._health["alpha_good"].ic_mean != 0

    def test_load_missing_file(self):
        monitor = AlphaDecayMonitor(state_file="/nonexistent/path.json")
        # Should not crash
        assert len(monitor._health) == 0


# ---------------------------------------------------------------------------
# Summary and reporting tests
# ---------------------------------------------------------------------------

class TestReporting:
    def test_get_summary(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(ic_window=20, state_file=temp_state_file)
        monitor.update(sample_data, ["alpha_good", "alpha_noise"])
        summary = monitor.get_summary()
        assert summary["total_alphas"] == 2
        assert "alphas" in summary
        assert "alpha_good" in summary["alphas"]

    def test_format_report(self, sample_data, temp_state_file):
        monitor = AlphaDecayMonitor(ic_window=20, state_file=temp_state_file)
        monitor.update(sample_data, ["alpha_good", "alpha_noise"])
        report = monitor.format_report()
        assert "ALPHA DECAY MONITOR" in report
        assert "alpha_good" in report

    def test_alert_outbox_upserts_instead_of_piling_duplicates(self, tmp_path):
        """_emit_alert refreshes the unresolved outbox row; live IC metrics in
        the payload must not mint a new fingerprint per check (audit
        2026-09-26: 185 PENDING copies of one alpha's alert)."""
        from alert_outbox import status, claim, acknowledge

        db = str(tmp_path / "decay_outbox.db")
        monitor = AlphaDecayMonitor(alert_db_path=db)

        def _alert(ic: float) -> DecayAlert:
            return DecayAlert(
                alpha_name="alpha_041", severity="CRITICAL",
                code="ALPHA_REVERSED_alpha_041", ic_value=ic,
                ic_mean=0.01, ic_trend=-0.002, consecutive_low=6,
                message="Alpha alpha_041 has REVERSED", timestamp=0.0,
            )

        monitor._emit_alert(_alert(-0.05))
        monitor._emit_alert(_alert(-0.06))   # new metrics, same condition
        monitor._emit_alert(_alert(-0.07))   # and again
        counts = status(db)["counts"]
        assert counts.get("PENDING", 0) == 1, counts
        # Latest payload wins on the single row.
        item = claim(db)[0]
        assert item["payload"]["details"]["ic_current"] == -0.07
        # After delivery, a recurring occurrence legitimately starts fresh.
        acknowledge(db, item["id"], delivered=True)
        monitor._emit_alert(_alert(-0.08))
        counts = status(db)["counts"]
        assert counts == {"SENT": 1, "PENDING": 1}, counts

    def test_alert_code_format(self):
        alert = DecayAlert(
            alpha_name="alpha_012",
            severity="WARNING",
            code="ALPHA_DECAY_alpha_012",
            ic_value=0.01,
            ic_mean=0.015,
            ic_trend=-0.001,
            consecutive_low=5,
            message="Test alert",
        )
        assert alert.alpha_name == "alpha_012"
        assert alert.severity == "WARNING"
