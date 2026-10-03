"""RETIRED — Rich terminal dashboard.

This module provided the Rich ``dashboard`` singleton used as an in-process
display for ``llm-trade --tui``.  It is retired: the single canonical TUI is
the OpenTUI command deck in ``tui_opentui/``, and state is mirrored to the API
server via ``ui/web_dashboard_adapter.py`` (which the deck and web GUI read).

Launch the canonical TUI with::

    python mt5_trader.py deck

(or ``START_DECK.bat``).  Requires ``bun`` (``npm i -g bun``).
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe Rich terminal dashboard (ui/tui_dashboard.py) is retired.\n"
    "The single canonical TUI is the OpenTUI command deck.\n"
    "  Launch it with:  python mt5_trader.py deck   (or START_DECK.bat)\n"
    "  Requires bun:    npm i -g bun  &&  cd tui_opentui && bun install\n"
)

# No dashboard singleton anymore — in-process display state is mirrored to the
# API server via ui/web_dashboard_adapter.py (imported by mt5_trader.py).
dashboard = None


class TUIDashboard:
    """Retired dashboard class — kept importable for legacy callers."""

    def __init__(self, *args, **kwargs) -> None:
        print(_RETIRED_MSG)


def main() -> None:
    print(_RETIRED_MSG)


if __name__ == "__main__":
    main()