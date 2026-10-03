"""Tests for the Alpha Health Dashboard."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from ui.alpha_dashboard import (
    _build_alert_timeline,
    _build_health_cards,
    _build_ic_trend_traces,
    _build_weight_chart,
    compute_alpha_stats,
    generate_alpha_dashboard,
    generate_dashboard_html,
    get_dashboard_gen_count,
    load_decay_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_decay_state() -> dict:
    """Sample decay state with multiple alphas."""
    return {
        "health": {
            "alpha_012": {
                "ic_current": 0.05, "ic_mean": 0.04, "ic_std": 0.02,
                "ic_trend": 0.001, "ic_consecutive_low": 0,
                "status": "HEALTHY", "last_alert_time": 0, "alert_count": 0,
            },
            "alpha_055": {
                "ic_current": 0.01, "ic_mean": 0.008, "ic_std": 0.015,
                "ic_trend": -0.0005, "ic_consecutive_low": 6,
                "status": "DEGRADED", "last_alert_time": 0, "alert_count": 1,
            },
            "alpha_017": {
                "ic_current": -0.03, "ic_mean": -0.025, "ic_std": 0.01,
                "ic_trend": -0.002, "ic_consecutive_low": 10,
                "status": "REVERSED", "last_alert_time": 0, "alert_count": 2,
            },
        },
        "ic_history": {
            "alpha_012": [0.03, 0.04, 0.05, 0.06, 0.04, 0.05],
            "alpha_055": [0.02, 0.015, 0.01, 0.008, 0.005, 0.003],
            "alpha_017": [-0.01, -0.015, -0.02, -0.025, -0.03, -0.025],
        },
    }


@pytest.fixture
def temp_html():
    """Temporary HTML output file."""
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Data loading tests
# ---------------------------------------------------------------------------

class TestLoading:
    def test_load_valid_state(self, tmp_path):
        state = {"health": {"alpha_012": {"status": "HEALTHY"}}, "ic_history": {}}
        path = tmp_path / "state.json"
        path.write_text(json.dumps(state))
        loaded = load_decay_state(str(path))
        assert "alpha_012" in loaded["health"]

    def test_load_missing_file(self):
        loaded = load_decay_state("/nonexistent/path.json")
        assert loaded == {"health": {}, "ic_history": {}}

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not json {{{")
        loaded = load_decay_state(str(path))
        assert loaded == {"health": {}, "ic_history": {}}


# ---------------------------------------------------------------------------
# Stats computation tests
# ---------------------------------------------------------------------------

class TestStats:
    def test_compute_alpha_stats(self):
        ic_history = {
            "alpha_012": [0.03, 0.04, 0.05, 0.06, 0.04],
            "alpha_055": [0.01, 0.005, -0.002, 0.003, 0.001],
        }
        stats = compute_alpha_stats(ic_history)
        assert "alpha_012" in stats
        assert "alpha_055" in stats
        assert stats["alpha_012"]["ic_mean"] > 0
        assert stats["alpha_012"]["ic_positive_pct"] == 1.0

    def test_empty_history(self):
        stats = compute_alpha_stats({})
        assert stats == {}

    def test_all_nan_history(self):
        stats = compute_alpha_stats({"alpha": [float("nan"), float("nan")]})
        assert "alpha" not in stats


# ---------------------------------------------------------------------------
# HTML generation tests
# ---------------------------------------------------------------------------

class TestHTMLGeneration:
    def test_generates_valid_html(self, sample_decay_state, temp_html):
        output = generate_dashboard_html(sample_decay_state, temp_html)
        assert os.path.exists(output)
        content = Path(output).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "Alpha Health Dashboard" in content
        assert "plotly" in content.lower()

    def test_contains_alpha_names(self, sample_decay_state, temp_html):
        output = generate_dashboard_html(sample_decay_state, temp_html)
        content = Path(output).read_text(encoding="utf-8")
        assert "alpha_012" in content
        assert "alpha_055" in content
        assert "alpha_017" in content

    def test_contains_status_counts(self, sample_decay_state, temp_html):
        output = generate_dashboard_html(sample_decay_state, temp_html)
        content = Path(output).read_text(encoding="utf-8")
        assert "HEALTHY" in content
        assert "DEGRADED" in content
        assert "REVERSED" in content

    def test_contains_alerts(self, sample_decay_state, temp_html):
        output = generate_dashboard_html(sample_decay_state, temp_html)
        content = Path(output).read_text(encoding="utf-8")
        assert "ALPHA_DECAY" in content or "ALPHA_REVERSED" in content or "ALPHA_DEGRADED" in content

    def test_empty_state(self, temp_html):
        output = generate_dashboard_html({}, temp_html)
        content = Path(output).read_text(encoding="utf-8")
        assert "No alpha health data" in content

    def test_creates_parent_dirs(self, tmp_path):
        output = str(tmp_path / "subdir" / "dashboard.html")
        generate_dashboard_html({}, output)
        assert os.path.exists(output)


# ---------------------------------------------------------------------------
# Component builder tests
# ---------------------------------------------------------------------------

class TestComponents:
    def test_health_cards_empty(self):
        result = _build_health_cards({}, {})
        assert result == ""

    def test_health_cards_with_data(self, sample_decay_state):
        result = _build_health_cards(sample_decay_state["health"], {})
        assert "alpha_012" in result
        assert "HEALTHY" in result
        assert "DEGRADED" in result

    def test_ic_trend_traces_empty(self):
        result = _build_ic_trend_traces({})
        assert "Plotly.newPlot" in result

    def test_ic_trend_traces_with_data(self, sample_decay_state):
        result = _build_ic_trend_traces(sample_decay_state["ic_history"])
        assert "Plotly.newPlot" in result
        assert "alpha_012" in result

    def test_alert_timeline_empty(self):
        result = _build_alert_timeline({})
        assert result == ""

    def test_alert_timeline_with_alerts(self, sample_decay_state):
        result = _build_alert_timeline(sample_decay_state["health"])
        assert "alert-row" in result

    def test_weight_chart_empty(self):
        result = _build_weight_chart({}, {})
        assert "Plotly.newPlot" in result

    def test_weight_chart_with_data(self, sample_decay_state):
        stats = compute_alpha_stats(sample_decay_state["ic_history"])
        result = _build_weight_chart(sample_decay_state["health"], stats)
        assert "Plotly.newPlot" in result


# ---------------------------------------------------------------------------
# Auto-generation tests (called from trading loop)
# ---------------------------------------------------------------------------

class TestAutoGeneration:
    def test_generate_returns_path_with_valid_state(self, tmp_path, sample_decay_state):
        state_file = str(tmp_path / "state.json")
        output_file = str(tmp_path / "dashboard.html")
        Path(state_file).write_text(json.dumps(sample_decay_state))
        result = generate_alpha_dashboard(state_file, output_file)
        assert result is not None
        assert os.path.exists(result)

    def test_generate_returns_none_for_missing_file(self, tmp_path):
        result = generate_alpha_dashboard(
            str(tmp_path / "nonexistent.json"),
            str(tmp_path / "dashboard.html"),
        )
        assert result is None

    def test_generate_returns_none_for_empty_state(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        Path(state_file).write_text(json.dumps({"health": {}, "ic_history": {}}))
        result = generate_alpha_dashboard(
            state_file, str(tmp_path / "dashboard.html")
        )
        assert result is None

    def test_generate_never_raises(self, tmp_path):
        # Should not raise even with garbage input
        state_file = str(tmp_path / "bad.json")
        Path(state_file).write_text("not json")
        result = generate_alpha_dashboard(
            state_file, str(tmp_path / "dashboard.html")
        )
        assert result is None

    def test_gen_count_increments(self, tmp_path, sample_decay_state):
        from ui import alpha_dashboard as mod
        initial = get_dashboard_gen_count()
        state_file = str(tmp_path / "state.json")
        Path(state_file).write_text(json.dumps(sample_decay_state))
        generate_alpha_dashboard(state_file, str(tmp_path / "out.html"))
        assert get_dashboard_gen_count() == initial + 1
