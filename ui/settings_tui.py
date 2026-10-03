"""RETIRED — Rich/questionary settings shell.

Settings are now managed in the single canonical TUI — the OpenTUI command
deck (``tui_opentui/``, SYSTEM CONFIG screen) — or via the secure CLI
(``python mt5_trader.py accounts list``).

Launch the canonical TUI with::

    python mt5_trader.py deck

(or ``python mt5_trader.py settings --tui``).  Requires ``bun``.
"""
from __future__ import annotations


_RETIRED_MSG = (
    "\nThe Rich settings shell (ui/settings_tui.py) is retired.\n"
    "Settings are managed from the OpenTUI command deck (SYSTEM CONFIG screen).\n"
    "  Launch it with:  python mt5_trader.py deck   (or START_DECK.bat)\n"
    "  Secure accounts: python mt5_trader.py accounts list\n"
)


def launch_settings(*args, **kwargs) -> None:
    """Retired entry point — prints the pointer to the OpenTUI deck."""
    print(_RETIRED_MSG)


def main() -> None:
    launch_settings()


if __name__ == "__main__":
    main()