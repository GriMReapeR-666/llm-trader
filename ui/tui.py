"""RETIRED — early Rich terminal dashboard.

This legacy Rich TUI (the predecessor of ``ui/unified_tui.py``) is retired.
The project has a single canonical TUI — the OpenTUI command deck in
``tui_opentui/``.

Launch the canonical TUI with::

    python mt5_trader.py deck

(or ``START_DECK.bat``).  Requires ``bun`` (``npm i -g bun``).
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe legacy Rich terminal TUI (ui/tui.py) is retired.\n"
    "The single canonical TUI is the OpenTUI command deck.\n"
    "  Launch it with:  python mt5_trader.py deck   (or START_DECK.bat)\n"
    "  Requires bun:    npm i -g bun  &&  cd tui_opentui && bun install\n"
)


class Dashboard:
    """Retired dashboard class — kept importable for legacy callers."""

    def __init__(self, *args, **kwargs) -> None:
        print(_RETIRED_MSG)


def main() -> None:
    print(_RETIRED_MSG)


if __name__ == "__main__":
    main()