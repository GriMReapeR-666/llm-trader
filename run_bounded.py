"""Shared wait-bound for external API calls (the per-source deadline pattern).

Every third-party client in this repo (newsapi, fredapi, scrapers) issues
requests with no effective timeout, so *any* call site outside a bounded
wrapper can hang a scheduler lane, an agent tool, or the boot path. This
module is the canonical helper: the call runs on a daemon thread and the
caller stops waiting at ``budget_s`` seconds.

Threads are never killed (impossible in Python); the budget is therefore a
"when do we stop waiting" bound, not a hard cancellation — good enough to
guarantee no single provider can stall its caller, which is the contract.

``data/api_manager._run_bounded`` and
``intelligence/boot_fetch_watchdog.run_stage_with_deadline`` predate this
module and keep their local implementations; new call sites should use
this one.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple


def run_bounded(fn: Callable[[], Any], budget_s: float) -> Tuple[bool, Any, Optional[BaseException]]:
    """Run ``fn`` with a hard wait-bound; never propagates.

    Returns ``(ok, result, error)``:
    * ``ok=True``  — ``fn`` returned within the budget; ``result`` is its value.
    * ``ok=False`` with ``error`` — ``fn`` raised (contained, not propagated),
      or the deadline passed (a synthetic ``TimeoutError``; the worker thread
      is abandoned and lingers until its next check).
    """
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # contained; re-raising would defeat the bound
            box["error"] = exc

    worker = threading.Thread(target=_target, name="run-bounded", daemon=True)
    worker.start()
    worker.join(timeout=max(0.0, float(budget_s)))
    if worker.is_alive():
        return False, None, TimeoutError(
            "call exceeded %ss budget; abandoned (hung provider?)" % budget_s)
    if "error" in box:
        return False, None, box["error"]
    return True, box.get("result"), None
