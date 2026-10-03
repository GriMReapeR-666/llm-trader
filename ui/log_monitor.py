"""RETIRED — Rich terminal log monitor.

This module provided the standalone Rich log-tail monitor (``mt5_trader.py
monitor``).  It is retired: the single canonical TUI is the OpenTUI command
deck in ``tui_opentui/``, whose LOG MONITOR screen tails ``mt5_trader.log``
live with level filters, search, pause and mouse support.

Launch the canonical TUI with::

    python mt5_trader.py monitor    (or  python mt5_trader.py deck)

Requires ``bun`` (``npm i -g bun``).
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe Rich log monitor (ui/log_monitor.py) is retired.\n"
    "Log monitoring lives in the OpenTUI command deck (LOG MONITOR screen).\n"
    "  Launch it with:  python mt5_trader.py monitor   (or  python mt5_trader.py deck)\n"
    "  Requires bun:    npm i -g bun  &&  cd tui_opentui && bun install\n"
)


def run_monitor(*args, **kwargs) -> None:
    """Retired entry point — prints the pointer to the OpenTUI deck."""
    print(_RETIRED_MSG)


def main() -> None:
    run_monitor()


if __name__ == "__main__":
    main()