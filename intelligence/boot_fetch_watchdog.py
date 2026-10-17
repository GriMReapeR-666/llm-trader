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
    "realtime_news_ingest": 90.0,
}

#: Cap on concurrently-outstanding abandoned stage workers.
#:
#: Python cannot kill a blocked thread, so an abandoned stage keeps running: it
#: continues fetching and continues writing to the journal.  ``pre_pulse_macro_ingest``
#: runs once per PULSE and unconditionally spawns four of these, so a provider
#: that consistently overruns its budget added one lingering thread per pulse,
#: unbounded.  Each holds a DB connection, so the pile-up competes with the very
#: work it was meant to protect -- the budget was supposed to bound the session,
#: but a pile of unbounded leftovers quietly removed that guarantee.
#:
#: Rather than pretend it can cancel them, the watchdog refuses to start a stage
#: once too many are already outstanding.  That makes the degradation explicit
#: and bounded: the stage returns a skipped marker (the fail-open contract the
#: caller already handles) instead of adding to the pile.  Override with
#: ``LLM_TRADER_MAX_ABANDONED_STAGES``.
MAX_ABANDONED_STAGES: int = 8

_abandoned_lock = threading.Lock()
#: Records of lingering abandoned workers, keyed by a UNIQUE id -- never by stage
#: name.  The same stage abandons on every pulse it overruns, so keying by name
#: made each new worker overwrite the previous record: the count stayed at 1
#: forever and the cap below never tripped, which is exactly the case it exists
#: for. Each record: {"stage", "thread", "abandoned_at", "budget_s"}.
_abandoned_stages: Dict[str, Dict[str, Any]] = {}
_abandoned_seq = 0


def _max_abandoned() -> int:
    raw = os.environ.get("LLM_TRADER_MAX_ABANDONED_STAGES", "").strip()
    if not raw:
        return MAX_ABANDONED_STAGES
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid LLM_TRADER_MAX_ABANDONED_STAGES=%r", raw)
        return MAX_ABANDONED_STAGES
    return value if value >= 0 else MAX_ABANDONED_STAGES


def _reap_abandoned() -> int:
    """Drop finished workers from the registry; return how many still linger."""
    with _abandoned_lock:
        for name, rec in list(_abandoned_stages.items()):
            thread = rec.get("thread")
            if thread is None or not thread.is_alive():
                _abandoned_stages.pop(name, None)
        return len(_abandoned_stages)


def outstanding_abandoned_stages() -> Dict[str, Any]:
    """Lingering abandoned stage workers: count, names and ages in seconds.

    Reaps first, so the reported count is current rather than whatever was true
    when the last stage was dispatched -- an observer that reports stale counts
    would send you looking for a leak that has already drained.
    """
    _reap_abandoned()
    with _abandoned_lock:
        now = time.time()
        return {
            "count": len(_abandoned_stages),
            "cap": _max_abandoned(),
            "stages": {
                str(rec.get("stage", key)): {
                    "age_s": round(now - float(rec.get("abandoned_at", now)), 1),
                    "budget_s": rec.get("budget_s"),
                }
                for key, rec in sorted(_abandoned_stages.items())
            },
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
    timeout_s: Optional[float] = None,
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
    if timeout_s is not None:
        budget_s = float(timeout_s)
    elif budgets is not None and stage in budgets:
        budget_s = float(budgets[stage])
    else:
        resolved = budgets or stage_budgets()
        budget_s = float(resolved.get(stage, _DEFAULT_STAGE_BUDGETS.get(stage, 30.0)))
    started = time.monotonic()
    outcome: Dict[str, Any] = {
        "stage": stage, "ok": False, "result": None, "error": None,
        "timed_out": False, "elapsed_s": 0.0, "budget_s": budget_s,
    }
    box: Dict[str, Any] = {}

    # Bound the pile before adding to it. Python cannot kill a blocked stage, so
    # the only honest control is refusing to start one when too many are already
    # lingering. Fail-open, like a timeout: the caller sees a skipped stage and
    # the session proceeds on degraded data.
    _reap_abandoned()
    with _abandoned_lock:
        _at_cap = len(_abandoned_stages) >= _max_abandoned()
    if _at_cap:
        outcome["skipped_at_cap"] = True
        outcome["error"] = (
            f"skipped: {len(_abandoned_stages)} abandoned stage worker(s) already "
            f"outstanding (cap {_max_abandoned()}); not starting another")
        logger.warning(
            "Boot fetch stage '%s' skipped: abandoned-worker cap reached "
            "(%d outstanding, cap %d)", stage, len(_abandoned_stages), _max_abandoned())
        if emit_event is not None:
            try:
                emit_event("boot_fetch_stage_skipped_at_cap", {
                    "stage": stage, "outstanding": len(_abandoned_stages),
                    "cap": _max_abandoned(),
                })
            except Exception:
                pass
        return outcome

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
    outcome["worker"] = worker

    if worker.is_alive():
        # Deadline missed: abandon the worker (daemon thread; cannot be
        # killed) and fail open with a WARNING journal event. The thread keeps
        # running and holding a journal connection, so it is registered rather
        # than dropped: an unregistered abandoned worker is invisible, and the
        # per-pulse caller would keep adding one every cycle.
        outcome["timed_out"] = True
        outcome["error"] = (
            f"stage exceeded {budget_s:.0f}s budget; abandoned (hung provider?)")
        with _abandoned_lock:
            global _abandoned_seq
            _abandoned_seq += 1
            _abandoned_stages[f"{stage}#{_abandoned_seq}"] = {
                "stage": stage, "thread": worker,
                "abandoned_at": time.time(), "budget_s": budget_s,
            }
            _outstanding = len(_abandoned_stages)
        outcome["abandoned_outstanding"] = _outstanding
        logger.warning(
            "Boot fetch stage '%s' exceeded %.1fs budget; abandoning worker "
            "and continuing degraded (%d abandoned worker(s) outstanding)",
            stage, budget_s, _outstanding)
        if emit_event is not None:
            try:
                emit_event("boot_fetch_stage_timeout", {
                    "stage": stage, "budget_s": budget_s,
                    "elapsed_s": outcome["elapsed_s"],
                    "abandoned_outstanding": _outstanding,
                })
            except Exception:
                pass
        return outcome

    if "error" in box:
        outcome["error"] = str(box["error"])
        outcome["error_obj"] = box["error"]
        logger.debug("Boot fetch stage '%s' failed: %s", stage, box["error"])
    else:
        outcome["ok"] = True
        outcome["result"] = box.get("result")
    return outcome
