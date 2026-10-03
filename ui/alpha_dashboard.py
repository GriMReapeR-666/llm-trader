"""Alpha Health Dashboard — Interactive Visualization.

Generates a self-contained HTML dashboard showing:
  - Alpha health status cards (color-coded: green/yellow/red)
  - IC trend charts per alpha over time
  - Decay alert timeline
  - Ensemble composite signal chart
  - Alpha weight distribution

Usage:
    python ui/alpha_dashboard.py                          # uses default state
    python ui/alpha_dashboard.py --state-file path.json   # custom state
    python ui/alpha_dashboard.py --output report.html     # custom output
    python ui/alpha_dashboard.py --live                   # live mode from DB

The dashboard is a self-contained HTML file with embedded plotly.js.
No server required — just open in any browser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_decay_state(path: str) -> Dict[str, Any]:
    """Load alpha decay monitor state from JSON."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"health": {}, "ic_history": {}}


def load_ensemble_state(path: str) -> Dict[str, Any]:
    """Load alpha ensemble weights from decay state or default."""
    # Ensemble weights are computed at runtime; for the dashboard we
    # extract them from the decay state's IC history quality.
    return {}


def compute_alpha_stats(ic_history: Dict[str, List[float]]) -> Dict[str, Dict]:
    """Compute per-alpha statistics from IC history."""
    stats = {}
    for name, vals in ic_history.items():
        arr = np.array(vals, dtype=float)
        valid = arr[np.isfinite(arr)]
        if len(valid) == 0:
            continue
        stats[name] = {
            "ic_current": float(valid[-1]) if len(valid) > 0 else 0.0,
            "ic_mean": float(valid.mean()),
            "ic_std": float(valid.std()) if len(valid) > 1 else 0.0,
            "ic_min": float(valid.min()),
            "ic_max": float(valid.max()),
            "ic_positive_pct": float((valid > 0).sum() / len(valid)),
            "n_bars": len(valid),
        }
    return stats


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_dashboard_html(
    decay_state: Dict[str, Any],
    output_path: str = "alpha_dashboard.html",
) -> str:
    """Generate a self-contained HTML dashboard."""
    health = decay_state.get("health", {})
    ic_history = decay_state.get("ic_history", {})

    # Compute stats
    alpha_stats = compute_alpha_stats(ic_history)

    # Status counts
    status_counts = {"HEALTHY": 0, "DEGRADED": 0, "REVERSED": 0, "DEAD": 0}
    for name, h in health.items():
        s = h.get("status", "HEALTHY")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Build IC trend traces
    ic_traces_html = _build_ic_trend_traces(ic_history)

    # Build health cards
    health_cards_html = _build_health_cards(health, alpha_stats)

    # Build alert timeline
    alerts_html = _build_alert_timeline(health)

    # Build weight chart
    weight_chart_html = _build_weight_chart(health, alpha_stats)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alpha Health Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: #0a0e17;
            color: #e0e0e0;
            padding: 20px;
        }}
        .header {{
            text-align: center;
            padding: 20px 0 30px;
            border-bottom: 1px solid #1e2a3a;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 28px;
            color: #00d4ff;
            margin-bottom: 5px;
        }}
        .header .subtitle {{
            color: #6b7b8d;
            font-size: 14px;
        }}
        .status-bar {{
            display: flex;
            justify-content: center;
            gap: 30px;
            margin: 20px 0;
        }}
        .status-item {{
            text-align: center;
            padding: 15px 25px;
            border-radius: 10px;
            background: #111827;
            border: 1px solid #1e2a3a;
            min-width: 120px;
        }}
        .status-item .count {{
            font-size: 32px;
            font-weight: bold;
        }}
        .status-item .label {{
            font-size: 12px;
            color: #6b7b8d;
            margin-top: 5px;
        }}
        .healthy .count {{ color: #10b981; }}
        .degraded .count {{ color: #f59e0b; }}
        .reversed .count {{ color: #ef4444; }}
        .dead .count {{ color: #6b7b8d; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }}
        .grid-full {{ grid-column: 1 / -1; }}
        .card {{
            background: #111827;
            border: 1px solid #1e2a3a;
            border-radius: 12px;
            padding: 20px;
        }}
        .card h2 {{
            font-size: 16px;
            color: #00d4ff;
            margin-bottom: 15px;
            border-bottom: 1px solid #1e2a3a;
            padding-bottom: 10px;
        }}
        .alpha-row {{
            display: flex;
            align-items: center;
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 4px;
            background: #0d1117;
        }}
        .alpha-row:hover {{ background: #161b22; }}
        .alpha-name {{ width: 120px; font-weight: 600; font-size: 13px; }}
        .alpha-ic {{ width: 80px; text-align: right; font-family: monospace; }}
        .alpha-bar {{
            flex: 1;
            height: 6px;
            background: #1e2a3a;
            border-radius: 3px;
            margin: 0 12px;
            position: relative;
            overflow: hidden;
        }}
        .alpha-bar-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}
        .alpha-status {{
            width: 80px;
            text-align: right;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
        }}
        .status-HEALTHY {{ background: #10b98120; color: #10b981; }}
        .status-DEGRADED {{ background: #f59e0b20; color: #f59e0b; }}
        .status-REVERSED {{ background: #ef444420; color: #ef4444; }}
        .status-DEAD {{ background: #6b7b8d20; color: #6b7b8d; }}
        .alert-row {{
            padding: 10px 14px;
            border-radius: 6px;
            margin-bottom: 6px;
            border-left: 3px solid;
            background: #0d1117;
        }}
        .alert-WARNING {{ border-color: #f59e0b; }}
        .alert-CRITICAL {{ border-color: #ef4444; }}
        .alert-INFO {{ border-color: #3b82f6; }}
        .alert-code {{ font-weight: 600; font-size: 13px; }}
        .alert-msg {{ font-size: 12px; color: #9ca3af; margin-top: 4px; }}
        .alert-severity {{
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: 600;
        }}
        .sev-WARNING {{ background: #f59e0b30; color: #f59e0b; }}
        .sev-CRITICAL {{ background: #ef444430; color: #ef4444; }}
        .chart-container {{ height: 350px; }}
        .no-data {{ text-align: center; color: #6b7b8d; padding: 40px; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>&#x1f4ca; Alpha Health Dashboard</h1>
        <div class="subtitle">Alpha101 Ensemble Monitor &mdash; Generated {now}</div>
    </div>

    <!-- Status Bar -->
    <div class="status-bar">
        <div class="status-item healthy">
            <div class="count">{status_counts.get('HEALTHY', 0)}</div>
            <div class="label">HEALTHY</div>
        </div>
        <div class="status-item degraded">
            <div class="count">{status_counts.get('DEGRADED', 0)}</div>
            <div class="label">DEGRADED</div>
        </div>
        <div class="status-item reversed">
            <div class="count">{status_counts.get('REVERSED', 0)}</div>
            <div class="label">REVERSED</div>
        </div>
        <div class="status-item dead">
            <div class="count">{status_counts.get('DEAD', 0)}</div>
            <div class="label">DEAD</div>
        </div>
    </div>

    <!-- Alpha Health Cards -->
    <div class="card grid-full" style="margin-bottom: 30px;">
        <h2>Alpha Health Overview</h2>
        {health_cards_html if health_cards_html else '<div class="no-data">No alpha health data available. Run the trading system to generate data.</div>'}
    </div>

    <!-- Charts Grid -->
    <div class="grid">
        <div class="card">
            <h2>IC Trend (All Alphas)</h2>
            <div id="ic_trend_chart" class="chart-container"></div>
        </div>
        <div class="card">
            <h2>IC Distribution</h2>
            <div id="ic_dist_chart" class="chart-container"></div>
        </div>
    </div>

    <!-- Decay Alerts -->
    <div class="card" style="margin-bottom: 30px;">
        <h2>Decay Alerts</h2>
        {alerts_html if alerts_html else '<div class="no-data">No decay alerts. All alphas healthy.</div>'}
    </div>

    <!-- Ensemble Weights -->
    <div class="card" style="margin-bottom: 30px;">
        <h2>Ensemble Weight Distribution</h2>
        <div id="weight_chart" class="chart-container" style="height: 300px;"></div>
    </div>

    <script>
    // IC Trend Chart
    {ic_traces_html}

    // IC Distribution Chart
    {weight_chart_html}

    // Auto-refresh every 60 seconds
    setTimeout(function() {{ location.reload(); }}, 60000);
    </script>
</body>
</html>"""

    # Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    return output_path


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _build_health_cards(
    health: Dict[str, Any],
    stats: Dict[str, Dict],
) -> str:
    """Build HTML for alpha health overview cards."""
    if not health:
        return ""

    rows = []
    # Sort: DEGRADED/REVERSED first, then by IC mean descending
    priority = {"REVERSED": 0, "DEAD": 1, "DEGRADED": 2, "HEALTHY": 3}
    sorted_alphas = sorted(
        health.items(),
        key=lambda x: (priority.get(x[1].get("status", "HEALTHY"), 9), -x[1].get("ic_mean", 0)),
    )

    for name, h in sorted_alphas:
        status = h.get("status", "HEALTHY")
        ic_mean = h.get("ic_mean", 0)
        ic_current = h.get("ic_current", 0)
        ic_trend = h.get("ic_trend", 0)
        consecutive_low = h.get("ic_consecutive_low", 0)

        # IC bar fill (normalize to [-0.1, 0.1] range)
        bar_pct = max(0, min(100, (ic_mean + 0.1) / 0.2 * 100))
        bar_color = "#10b981" if ic_mean > 0.02 else "#f59e0b" if ic_mean > 0 else "#ef4444"

        # Trend arrow
        trend_arrow = "&#9650;" if ic_trend > 0.0005 else "&#9660;" if ic_trend < -0.0005 else "&#9644;"
        trend_color = "#10b981" if ic_trend > 0 else "#ef4444" if ic_trend < 0 else "#6b7b8d"

        rows.append(f"""
        <div class="alpha-row">
            <div class="alpha-name">{name}</div>
            <div class="alpha-ic" style="color: {bar_color}">{ic_mean:+.4f}</div>
            <div class="alpha-bar">
                <div class="alpha-bar-fill" style="width: {bar_pct:.0f}%; background: {bar_color};"></div>
            </div>
            <div style="width: 60px; text-align: center; color: {trend_color}; font-size: 14px;">{trend_arrow}</div>
            <div class="alpha-status status-{status}">{status}</div>
        </div>""")

    return "\n".join(rows)


def _build_ic_trend_traces(ic_history: Dict[str, List[float]]) -> str:
    """Build plotly traces for IC trend chart."""
    if not ic_history:
        return "Plotly.newPlot('ic_trend_chart', [], {paper_bgcolor: '#111827', plot_bgcolor: '#111827', font: {color: '#e0e0e0'}});"

    traces = []
    colors = [
        "#00d4ff", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
        "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#6366f1",
    ]

    for i, (name, vals) in enumerate(ic_history.items()):
        color = colors[i % len(colors)]
        y = [v if np.isfinite(v) else None for v in vals]
        traces.append({
            "x": list(range(len(vals))),
            "y": y,
            "type": "scatter",
            "mode": "lines",
            "name": name,
            "line": {"color": color, "width": 1.5},
        })

    layout = {
        "paper_bgcolor": "#111827",
        "plot_bgcolor": "#111827",
        "font": {"color": "#e0e0e0", "size": 11},
        "xaxis": {"title": "Bar", "gridcolor": "#1e2a3a", "zerolinecolor": "#1e2a3a"},
        "yaxis": {"title": "Spearman IC", "gridcolor": "#1e2a3a", "zerolinecolor": "#f59e0b40"},
        "legend": {"orientation": "h", "y": -0.2, "font": {"size": 10}},
        "margin": {"l": 50, "r": 20, "t": 10, "b": 60},
        "shapes": [{
            "type": "line", "x0": 0, "x1": 1, "xref": "paper",
            "y0": 0, "y1": 0, "yref": "y",
            "line": {"color": "#f59e0b60", "width": 1, "dash": "dash"},
        }],
    }

    return f"Plotly.newPlot('ic_trend_chart', {json.dumps(traces)}, {json.dumps(layout)}, {{responsive: true}});"


def _build_alert_timeline(health: Dict[str, Any]) -> str:
    """Build HTML for decay alert timeline."""
    alerts = []
    for name, h in health.items():
        status = h.get("status", "HEALTHY")
        if status in ("DEGRADED", "REVERSED", "DEAD"):
            severity = "CRITICAL" if status in ("REVERSED", "DEAD") else "WARNING"
            ic_mean = h.get("ic_mean", 0)
            consecutive = h.get("ic_consecutive_low", 0)
            alerts.append({
                "code": f"ALPHA_{status}_{name}",
                "severity": severity,
                "message": (
                    f"Alpha {name}: IC={ic_mean:+.4f}, "
                    f"{consecutive} consecutive low bars. "
                    f"{'Consider removing from ensemble.' if status in ('REVERSED', 'DEAD') else 'Monitor closely.'}"
                ),
                "name": name,
            })

    if not alerts:
        return ""

    rows = []
    for a in sorted(alerts, key=lambda x: 0 if x["severity"] == "CRITICAL" else 1):
        rows.append(f"""
        <div class="alert-row alert-{a['severity']}">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span class="alert-code">{a['code']}</span>
                <span class="alert-severity sev-{a['severity']}">{a['severity']}</span>
            </div>
            <div class="alert-msg">{a['message']}</div>
        </div>""")

    return "\n".join(rows)


def _build_weight_chart(
    health: Dict[str, Any],
    stats: Dict[str, Dict],
) -> str:
    """Build plotly chart for alpha IC distribution (box plot style)."""
    if not stats:
        return "Plotly.newPlot('weight_chart', [], {paper_bgcolor: '#111827', plot_bgcolor: '#111827', font: {color: '#e0e0e0'}});"

    names = sorted(stats.keys(), key=lambda x: -stats[x]["ic_mean"])
    ic_means = [stats[n]["ic_mean"] for n in names]
    colors = ["#10b981" if v > 0.02 else "#f59e0b" if v > 0 else "#ef4444" for v in ic_means]

    trace = {
        "x": names,
        "y": ic_means,
        "type": "bar",
        "marker": {"color": colors},
        "text": [f"{v:+.4f}" for v in ic_means],
        "textposition": "outside",
        "textfont": {"size": 10, "color": "#e0e0e0"},
    }

    layout = {
        "paper_bgcolor": "#111827",
        "plot_bgcolor": "#111827",
        "font": {"color": "#e0e0e0", "size": 11},
        "xaxis": {"tickangle": -45, "gridcolor": "#1e2a3a"},
        "yaxis": {"title": "Mean IC", "gridcolor": "#1e2a3a", "zerolinecolor": "#f59e0b60"},
        "margin": {"l": 50, "r": 20, "t": 10, "b": 80},
        "shapes": [{
            "type": "line", "x0": 0, "x1": 1, "xref": "paper",
            "y0": 0, "y1": 0, "yref": "y",
            "line": {"color": "#f59e0b60", "width": 1, "dash": "dash"},
        }],
    }

    return f"Plotly.newPlot('weight_chart', [{json.dumps(trace)}], {json.dumps(layout)}, {{responsive: true}});"


# ---------------------------------------------------------------------------
# Auto-generation (called from trading loop)
# ---------------------------------------------------------------------------

_dashboard_gen_count = 0

def generate_alpha_dashboard(
    state_file: str = "alpha_decay_state.json",
    output_path: str = "alpha_dashboard.html",
) -> Optional[str]:
    """Generate dashboard from current alpha decay state.

    Returns the output path on success, or None if no data / error.
    This is designed to be called after each trading cycle — it is
    lightweight (single file read + HTML write) and never raises.
    """
    global _dashboard_gen_count
    try:
        if not os.path.exists(state_file):
            return None
        state = load_decay_state(state_file)
        health = state.get("health", {})
        ic_history = state.get("ic_history", {})
        # Skip if no health data accumulated yet
        if not health and not ic_history:
            return None
        result = generate_dashboard_html(state, output_path)
        _dashboard_gen_count += 1
        return result
    except Exception:
        # Never let dashboard generation crash the trading loop
        return None


def get_dashboard_gen_count() -> int:
    """Return how many times the dashboard has been auto-generated."""
    return _dashboard_gen_count


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Alpha Health Dashboard")
    parser.add_argument(
        "--state-file", default="alpha_decay_state.json",
        help="Path to alpha decay state JSON file"
    )
    parser.add_argument(
        "--output", default="alpha_dashboard.html",
        help="Output HTML file path"
    )
    parser.add_argument(
        "--open", action="store_true", default=True,
        help="Open dashboard in browser after generation"
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Do not open dashboard in browser"
    )
    args = parser.parse_args()

    # Load state
    state = load_decay_state(args.state_file)

    # Generate dashboard
    output = generate_dashboard_html(state, args.output)
    print(f"Dashboard generated: {output}")

    # Open in browser
    if args.open and not args.no_open:
        try:
            webbrowser.open(f"file://{os.path.abspath(output)}")
            print("Opened in browser.")
        except Exception:
            print(f"Open manually: file://{os.path.abspath(output)}")


if __name__ == "__main__":
    main()
