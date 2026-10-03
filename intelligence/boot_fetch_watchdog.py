"""Boot fetch watchdog: per-source bounded execution for the pre-pulse
news/indicators ingest path.

Before this, each stage of `pre_pulse_macro_ingest` (calendar collection,
bulk calendar ingest, FRED/NewsAPI data-source pipeline, realtime news) was
guarded against exceptions but NOT against hangs: a wedged provider call
(e.g. a requests call with no effective timeout, or a dead MCP/calendar
bridge) blocked the pulse thread indefinitely — the same class of silent
20+ minute pre-first-cycle stall as the CPU Laya triage found in session 2.

`run_stage_with_deadline` runs each stage on a daemon thread and abandons it
at the stage's deadline. Abandoned stages fail OPEN: the session proceeds on
degraded data instead of hanging, and the watchdog emits a WARNING event so
the stall is visible in the journal instead of silent.

Threads are never killed (Python cannot kill a blocked thread); an abandoned
stage's thread lingers until its next check. The stage budget is therefore a
"when do we stop waiting" bound, not a hard cancellation — good enough to
guarantee the session never stalls on one provider, which is the contract.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("LLMOrchestrator")

# Per-stage default budgets (seconds). A provider that hasn't answered by
# its budget is abandoned for this pulse; the next pulse starts fresh.
_DEFAULT_STAGE_BUDGETS: Dict[str, float] = {
    "calendar_collect": 30.0,
    "bulk_calendar_ingest": 30.0,
    "data_source_pipeline": 45.0,
    "realtime_news_ingest": 45.0,
}


def stage_budgets() -> Dict[str, float]:
    """Resolve per-stage budgets from env, falling back to the defaults.

    Env shape: LLM_TRADER_BOOT_STAGE_BUDGETS="calendar_collect=10,
    realtime_news_ingest=20" (floats in seconds; unknown stage names are
    reported, not raised, so a typo can never break boot).
    """
    budgets = dict(_DEFAULT_STAGE_BUDGETS)
    raw = os.environ.get("LLM_TRADER_BOOT_STAGE_BUDGETS", "")
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid boot stage budget %r (expected name=seconds)", part)
            continue
        if name not in _DEFAULT_STAGE_BUDGETS:
            logger.warning(
                "Unknown boot stage %r in LLM_TRADER_BOOT_STAGE_BUDGETS "
                "(known: %s)", name, ", ".join(sorted(_DEFAULT_STAGE_BUDGETS)))
            continue
        budgets[name] = value_f
    return budgets


def run_stage_with_deadline(
    stage: str,
    fn: Callable[[], Any],
    *,
    budgets: Optional[Dict[str, float]] = None,
    emit_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run one boot-fetch stage under its deadline; fail open on timeout.

    Returns a per-stage outcome dict (``ok``, ``result``/``error``,
    ``elapsed_s``) so callers can fold real telemetry into the ingest cache
    instead of a bare pass/fail. Exceptions inside ``fn`` are caught and
    reported the same way as before (degraded, not fatal); a deadline miss
    additionally emits a ``boot_fetch_stage_timeout`` event when an emitter
    is provided.
    """
    resolved = budgets or stage_budgets()
    budget_s = float(resolved.get(stage, _DEFAULT_STAGE_BUDGETS.get(stage, 30.0)))
    started = time.monotonic()
    outcome: Dict[str, Any] = {
        "stage": stage, "ok": False, "result": None, "error": None,
        "timed_out": False, "elapsed_s": 0.0, "budget_s": budget_s,
    }
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - stage failures must never crash boot
            box["error"] = exc

    worker = threading.Thread(
        target=_target, name=f"boot-stage-{stage}", daemon=True)
    worker.start()
    worker.join(timeout=budget_s)
    outcome["elapsed_s"] = round(time.monotonic() - started, 3)

    if worker.is_alive():
        # Deadline missed: abandon the worker (daemon thread; cannot be
        # killed) and fail open with a WARNING journal event.
        outcome["timed_out"] = True
        outcome["error"] = (
            f"stage exceeded {budget_s:.0f}s budget; abandoned (hung provider?)")
        logger.warning(
            "Boot fetch stage '%s' exceeded %.1fs budget; abandoning worker "
            "and continuing degraded", stage, budget_s)
        if emit_event is not None:
            try:
                emit_event("boot_fetch_stage_timeout", {
                    "stage": stage, "budget_s": budget_s,
                    "elapsed_s": outcome["elapsed_s"],
                })
            except Exception:
                pass
        return outcome

    if "error" in box:
        outcome["error"] = str(box["error"])
        logger.debug("Boot fetch stage '%s' failed: %s", stage, box["error"])
    else:
        outcome["ok"] = True
        outcome["result"] = box.get("result")
    return outcome
