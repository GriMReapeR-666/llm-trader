"""Verify the Rich-based terminal TUIs are retired and the single canonical
TUI is the OpenTUI command deck (``tui_opentui/``).

The retired modules must remain importable (nothing crashes on import) but
their launch entry points must print the pointer to the deck instead of
starting an interactive Rich TUI.
"""
from __future__ import annotations

import contextlib
import io

RETIRED_MODULES = [
    "ui.unified_tui",
    "ui.tui_dashboard",
    "ui.settings_tui",
    "ui.tui",
    "ui.log_monitor",
    "ui.display",
]


def test_retired_modules_import_cleanly():
    for mod in RETIRED_MODULES:
        __import__(mod)


def test_retired_entry_points_point_to_opentui_deck():
    from ui.unified_tui import launch_unified_tui
    from ui.settings_tui import launch_settings
    from ui.log_monitor import run_monitor

    for fn in (launch_unified_tui, launch_settings, run_monitor):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        out = buf.getvalue()
        assert "OpenTUI" in out
        assert "deck" in out


def test_retired_dashboard_classes_print_pointer():
    from ui.tui_dashboard import TUIDashboard
    from ui.tui import Dashboard

    for cls in (TUIDashboard, Dashboard):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cls()
        assert "OpenTUI" in buf.getvalue()


def test_mt5_trader_dashboard_hook_is_web_adapter_not_rich_tui():
    """mt5_trader's in-process dashboard hook must resolve to the web adapter
    (mirrors state to :8000 for the deck and web GUI), never the retired Rich
    dashboard; and the retired TradingDisplay fallback must be gone."""
    import mt5_trader

    # ui/ is on sys.path, so mt5_trader imports the adapter as a top-level
    # module (``web_dashboard_adapter``) — compare by class name, not module id.
    assert mt5_trader.dashboard is None or type(
        mt5_trader.dashboard
    ).__name__ == "WebDashboardAdapter"
    assert mt5_trader.DISPLAY_AVAILABLE is False
    assert mt5_trader.TradingDisplay is None


def test_mt5_trader_has_no_rich_tui_imports():
    """No retired Rich-TUI module may be imported anywhere in mt5_trader."""
    import inspect
    import mt5_trader

    src = inspect.getsource(mt5_trader)
    for mod in ("unified_tui", "tui_dashboard", "log_monitor", "settings_tui"):
        assert f"from ui.{mod}" not in src
        assert f"import ui.{mod}" not in src
    assert "from display import" not in src
    assert "from tui_dashboard import" not in src