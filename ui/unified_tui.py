"""RETIRED — Rich-based unified terminal TUI.

This module was the Python Rich terminal dashboard (EventFeed, CycleReplay,
screens).  It is retired: the project has a single canonical TUI — the OpenTUI
command deck in ``tui_opentui/`` (FEED, REPLAY, LOG MONITOR, DSR, NEWS, ...).

Launch the canonical TUI with::

    python mt5_trader.py deck

(or ``START_DECK.bat``).  Requires ``bun`` (``npm i -g bun``).
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe Rich-based terminal TUI (ui/unified_tui.py) is retired.\n"
    "The single canonical TUI is the OpenTUI command deck.\n"
    "  Launch it with:  python mt5_trader.py deck   (or START_DECK.bat)\n"
    "  Requires bun:    npm i -g bun  &&  cd tui_opentui && bun install\n"
)


def launch_unified_tui(*args, **kwargs) -> None:
    """Retired entry point — prints the pointer to the OpenTUI deck."""
    print(_RETIRED_MSG)


def main() -> None:
    launch_unified_tui()


if __name__ == "__main__":
    main()