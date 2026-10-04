"""Unified kill-switch authority for trading operations.

Combines manual operator kill-switch in TradingStore and D9 news-side latch into
a single gate: entries_blocked(store=None) -> Tuple[bool, str].
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from data.kill_switch_latch import get_kill_switch_latch

logger = logging.getLogger(__name__)


def entries_blocked(store: Optional[Any] = None) -> Tuple[bool, str]:
    """Check if new trade entries are blocked.

    Returns (True, reason) if entries must be blocked:
    - Manual kill switch active in store -> (True, "kill switch active")
    - News kill switch latch is active -> (True, "news kill-switch latched")
    - Any store read error fails closed -> (True, f"kill-switch unreadable: {exc}")

    Returns (False, "") if entries are permitted.
    """
    # 1. Manual kill switch from store
    if store is not None:
        try:
            if hasattr(store, "is_kill_switch_active"):
                if store.is_kill_switch_active():
                    return True, "kill switch active"
            elif isinstance(store, dict):
                if store.get("kill_switch_active") or store.get("kill_switch"):
                    return True, "kill switch active"
        except Exception as exc:
            logger.warning("Failed to check manual kill switch in store: %s", exc)
            return True, f"kill-switch unreadable: {exc}"

    # 2. News kill switch latch
    try:
        latch = get_kill_switch_latch()
        if store is not None and getattr(latch, "_store", None) is not store:
            latch.attach_store(store)
        snap = latch.snapshot()
        if snap.get("latched"):
            return True, "news kill-switch latched"
    except Exception as exc:
        logger.warning("Failed to check kill switch latch: %s", exc)
        return True, f"kill-switch unreadable: {exc}"

    return False, ""
