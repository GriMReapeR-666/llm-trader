"""RETIRED — Rich trading display.

This module provided the Rich ``TradingDisplay`` in-process dashboard (the
fallback display for ``llm-trade --tui``).  It is retired: the single
canonical TUI is the OpenTUI command deck in ``tui_opentui/``, and in-process
state is mirrored to the API server via ``ui/web_dashboard_adapter.py``.

Launch the canonical TUI with::

    python mt5_trader.py deck

(or ``START_DECK.bat``).  Requires ``bun`` (``npm i -g bun``).
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe Rich trading display (ui/display.py) is retired.\n"
    "The single canonical TUI is the OpenTUI command deck.\n"
    "  Launch it with:  python mt5_trader.py deck   (or START_DECK.bat)\n"
    "  Requires bun:    npm i -g bun  &&  cd tui_opentui && bun install\n"
)

# In-process display is the web adapter (mirrors state to :8000 for the deck
# and the web GUI) — see ui/web_dashboard_adapter.py.
TradingDisplay = None


def print_llm_prompt_preview(*args, **kwargs) -> None:
    """Retired helper — prints the pointer to the OpenTUI deck."""
    print(_RETIRED_MSG)


def main() -> None:
    print(_RETIRED_MSG)


if __name__ == "__main__":
    main()