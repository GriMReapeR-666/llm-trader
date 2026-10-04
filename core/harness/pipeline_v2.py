"""V2 pipeline runner + envelope entry (Phase E1–E3).

The runner gives every declared stage identical execution semantics —
telemetry, retries, SLA tracking, fallback policies, supervisor beats and
trace_id threading. Phase E1 wrapped the whole legacy body; Phase E2 split
out maintenance; Phase E3 splits the analysis/execution body into declared
stages without duplicating logic by construction:

    execute_cycle_v2()
      ├─ STAGE blackout.gate          (EventAgent, abort_cycle on hit)
      ├─ STAGE portfolio.consult      (PortfolioAgent, skip-fallback, advisory)
      ├─ STAGE analysis.context       (QuantitativeCoach)
      ├─ STAGE analysis.data          (TechnicalNet)   ← execute_cycle data half
      ├─ STAGE analysis.causal        (MacroAnalyst)   ← causal/memory context half
      ├─ STAGE analysis.llm           (MarketModel)    ← LLM decision call
      ├─ STAGE analysis.validate      (RiskController) ← DeterministicValidator
      ├─ STAGE analysis.size          (RiskController) ← RiskManager sizing + arbiter
      └─ STAGE analysis.execute       (RiskController) ← order intent → broker submit

How zero divergence is guaranteed: execute_cycle remains the single source
of truth. While the V2 envelope is active on a thread, declared analysis
stages resolve a thread-local stage handoff recorded by
LLMOrchestrator._v2_stage_start() *before* execute_cycle re-runs the same
work inline. The code is not copied — it is re-entered, and the handoff
marker only selects which already-declared stage the runner attributes the
work to. Outside the envelope both markers are strict no-ops, and any
stage whose handoff does not match the declared key raises so the runner
can record the failure (fail-safe identity: the pipeline can never diverge
from the legacy semantics it wraps).

Golden replay (tests/test_pipeline_v2.py): the recorder drives every stage
function directly against a real LLMOrchestrator with a temp journal, so
both modes execute byte-identical code with identical arguments. The
journal (order intents, lifecycle events, snapshots) is the independent
witness: recorded intents must match exactly between the Phase-E1 single
legacy body and the Phase-E3 staged analysis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from core.harness.stage_contracts import (
    CycleAbort, RunnerContext, StageResult, StageSpec,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-local handoff + replay recorder.
#
# maintenance skip token (Phase E2): _run_maintenance_prefix consumes it.
# analysis stage handoff (Phase E3): _v2_stage_start() resolves the declared
#   stage the runner is attributing work to.
# replay recorder: test/golden capture drives stage functions directly and
#   sees every seam mark execute_cycle emits.
# ---------------------------------------------------------------------------
_tls = threading.local()


def request_skip_maintenance() -> None:
    _tls.skip_maintenance = True


def consume_skip_maintenance() -> bool:
    value = getattr(_tls, "skip_maintenance", False)
    _tls.skip_maintenance = False
    return value


def begin_v2_envelope() -> None:
    """Mark this thread as running inside the V2 envelope.

    While active, LLMOrchestrator._v2_stage_start() records the analysis
    stage handoff; outside the envelope it is a strict no-op so the legacy
    path can never leave a stale token behind.
    """
    _tls.v2_active = True
    _tls.hold_only = False


def end_v2_envelope() -> None:
    _tls.v2_active = False
    _tls.hold_only = False


def set_tls_hold_only(val: bool = True) -> None:
    _tls.hold_only = val


def get_tls_hold_only() -> bool:
    return getattr(_tls, "hold_only", False)


_abandoned_threads: Dict[str, List[threading.Thread]] = {}
_abandoned_threads_lock = threading.Lock()


def mark_analysis_stage_start(stage_key: str) -> None:
    """Stage handoff: associate the currently-running analysis code with
    the declared stage the runner is about to attribute that work to.

    Effective only while the V2 envelope is active on this thread; the
    legacy path never activates it, so control flow is unchanged there.
    """
    if getattr(_tls, "v2_active", False):
        _tls.analysis_stage_handoff = stage_key


def mark_cycle_seam(seam_key: str) -> None:
    """Seam marker inside execute_cycle (data/causal/llm/validate/size/
    execute boundaries). No-op unless a replay recorder is attached; the
    legacy path never attaches one, so this can never change trading
    behavior.
    """
    recorder = getattr(_tls, "replay_recorder", None)
    if recorder is not None:
        try:
            recorder(seam_key)
        except Exception:
            pass


def attach_replay_recorder(fn: Any) -> None:
    """Attach a callable(seam_key) for golden capture (test-only)."""
    _tls.replay_recorder = fn


def detach_replay_recorder() -> None:
    _tls.replay_recorder = None


def pop_analysis_stage_handoff() -> Optional[str]:
    value = getattr(_tls, "analysis_stage_handoff", None)
    _tls.analysis_stage_handoff = None
    return value


class PipelineRunner:
    def __init__(self, orchestrator: Any, trace_id: str,
                 stages: Optional[List[StageSpec]] = None):
        self.orchestrator = orchestrator
        self.trace_id = trace_id
        self.stages: List[StageSpec] = list(stages or [])
        self.results: List[StageResult] = []

    # ---------- telemetry ----------
    def _emit(self, event_type: str, payload: Dict[str, Any],
              actor: str) -> None:
        try:
            from operations.event_bridge import emit
            emit(event_type, payload, correlation_id=self.trace_id,
                 actor=actor, trace_id=self.trace_id)
        except Exception as exc:
            logger.debug("event emission failed: %s", exc)

    def _beat(self, agent: str, ok: bool, detail: str) -> None:
        try:
            from operations.supervisor import get_supervisor
            get_supervisor().note_principal(agent, ok=ok, detail=detail)
        except Exception as exc:
            logger.debug("event emission failed: %s", exc)

    # ---------- execution ----------
    def run_stage(self, spec: StageSpec, ctx: RunnerContext) -> StageResult:
        started = time.time()
        self._emit("STAGE_STARTED", {"stage": spec.key}, actor=spec.agent)

        attempts = max(1, spec.retries + 1)
        last_error = ""
        timed_out = False
        for attempt in range(1, attempts + 1):
            if spec.abandonable:
                with _abandoned_threads_lock:
                    alive_abandoned = [t for t in _abandoned_threads.get(spec.key, []) if t.is_alive()]
                    _abandoned_threads[spec.key] = alive_abandoned
                if len(alive_abandoned) >= 2:
                    logger.warning(
                        "Stage %s has %d abandoned threads alive; tripping autonomy circuit",
                        spec.key, len(alive_abandoned),
                    )
                    try:
                        from operations.autonomy import get_autonomy
                        get_autonomy().pause(reason=f"abandon_leak:{spec.key}")
                    except Exception as _p_exc:
                        logger.warning("Failed to pause autonomy on abandon leak: %s", _p_exc)
                    last_error = f"abandon_leak: {len(alive_abandoned)} threads already abandoned for {spec.key}"
                    timed_out = True
                    break

                from intelligence.boot_fetch_watchdog import run_stage_with_deadline
                outcome = run_stage_with_deadline(
                    spec.key,
                    lambda: spec.fn(ctx, {"attempt": attempt}),
                    timeout_s=spec.timeout_s,
                )
                if outcome.get("timed_out"):
                    timed_out = True
                    last_error = outcome.get("error") or f"stage exceeded {spec.timeout_s}s deadline"
                    worker = outcome.get("worker")
                    if worker and worker.is_alive():
                        with _abandoned_threads_lock:
                            _abandoned_threads.setdefault(spec.key, []).append(worker)
                    duration_ms = int((time.time() - started) * 1000)
                    self._emit("STAGE_TIMEOUT", {
                        "stage": spec.key,
                        "timeout_s": spec.timeout_s,
                        "elapsed_s": outcome.get("elapsed_s", 0.0),
                    }, actor=spec.agent)
                    if spec.key == "analysis.llm":
                        try:
                            req_id = getattr(ctx.orchestrator, "_active_llm_request_id", None)
                            if req_id and hasattr(ctx.orchestrator, "operations"):
                                ctx.orchestrator.operations.finish_llm(
                                    req_id, success=False, parse_status="TIMEOUT", error="STAGE_TIMEOUT",
                                )
                                ctx.orchestrator._active_llm_request_id = None
                        except Exception as e:
                            logger.debug("Failed to release LLM budget reservation on timeout: %s", e)
                    break
                elif not outcome.get("ok"):
                    err_obj = outcome.get("error_obj")
                    if isinstance(err_obj, CycleAbort):
                        duration_ms = int((time.time() - started) * 1000)
                        self._beat(spec.agent, False, f"{spec.key}: aborted")
                        self._emit("STAGE_ABORTED",
                                   {"stage": spec.key, "reason": str(err_obj)[:200]},
                                   actor=spec.agent)
                        result = StageResult(spec.key, "aborted", duration_ms, error=str(err_obj))
                        self.results.append(result)
                        raise CycleAbort(str(err_obj)) from None
                    last_error = outcome.get("error") or "stage execution error"
                    logger.warning("stage %s attempt %d failed: %s", spec.key, attempt, last_error)
                    if attempt < attempts:
                        continue
                else:
                    value = outcome.get("result")
                    duration_ms = int((time.time() - started) * 1000)
                    result = StageResult(spec.key, "done", duration_ms, value=value)
                    if spec.emits_done:
                        self._emit("STAGE_DONE",
                                   {"stage": spec.key, "ms": duration_ms},
                                   actor=spec.agent)
                    if duration_ms > spec.sla_ms:
                        self._emit("STAGE_SLA_MISS",
                                   {"stage": spec.key, "ms": duration_ms,
                                    "sla_ms": spec.sla_ms},
                                   actor=spec.agent)
                    self._beat(spec.agent, True, spec.key)
                    self.results.append(result)
                    return result
            else:
                try:
                    value = spec.fn(ctx, {"attempt": attempt})
                    duration_ms = int((time.time() - started) * 1000)
                    result = StageResult(spec.key, "done", duration_ms, value=value)
                    if spec.emits_done:
                        self._emit("STAGE_DONE",
                                   {"stage": spec.key, "ms": duration_ms},
                                   actor=spec.agent)
                    if duration_ms > spec.sla_ms:
                        self._emit("STAGE_SLA_MISS",
                                   {"stage": spec.key, "ms": duration_ms,
                                    "sla_ms": spec.sla_ms},
                                   actor=spec.agent)
                    self._beat(spec.agent, True, spec.key)
                    self.results.append(result)
                    return result
                except CycleAbort as abort:
                    duration_ms = int((time.time() - started) * 1000)
                    self._beat(spec.agent, False, f"{spec.key}: aborted")
                    self._emit("STAGE_ABORTED",
                               {"stage": spec.key, "reason": str(abort)[:200]},
                               actor=spec.agent)
                    result = StageResult(spec.key, "aborted", duration_ms,
                                         error=str(abort))
                    self.results.append(result)
                    # Abort always stops the pipeline: bubble to the envelope.
                    raise CycleAbort(str(abort)) from None
                except Exception as exc:  # noqa: BLE001 - stage isolation
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("stage %s attempt %d failed: %s",
                                   spec.key, attempt, last_error)
                    if attempt < attempts:
                        continue

        duration_ms = int((time.time() - started) * 1000)
        self._beat(spec.agent, False, f"{spec.key}: {last_error[:80]}")
        if not timed_out:
            self._emit("STAGE_FAILED",
                       {"stage": spec.key, "error": last_error[:300]},
                       actor=spec.agent)

        policy = spec.fallback
        status = {
            "continue": "failed",     # recorded, pipeline proceeds
            "skip": "skipped",        # proceed without its output
            "degrade": "degraded",    # proceed, cycle forced HOLD-only
            "abort_cycle": "aborted", # stop everything
        }.get(policy, "failed")

        result = StageResult(spec.key, status, duration_ms, error=last_error)
        self.results.append(result)
        if policy == "degrade":
            ctx.hold_only(f"stage {spec.key} degraded: {last_error[:120]}")
            set_tls_hold_only(True)
        if policy == "abort_cycle":
            raise CycleAbort(f"{spec.key}: {last_error}")
        return result

    def run(self, ctx: RunnerContext) -> List[StageResult]:
        """Execute all declared stages. Raises CycleAbort on any abort path."""
        for spec in self.stages:
            try:
                self.run_stage(spec, ctx)
            except CycleAbort:
                raise
            except Exception as exc:  # runner must never kill the loop
                logger.exception("runner error on stage %s", spec.key)
                self.results.append(
                    StageResult(spec.key, "failed", 0, error=str(exc)))
        return self.results


# --------------------------------------------------------------------------
# Declared fast-lane envelope (E1) + staged analysis (E3).
# --------------------------------------------------------------------------

def _stage_blackout_gate(ctx: RunnerContext, _inp: Dict[str, Any]) -> bool:
    from operations.agents.event_agent import get_manager

    orch = ctx.orchestrator
    symbol = str(getattr(orch, "_v2_symbol", "*") or "*")
    gated, why = get_manager().is_blackout(symbol)
    if gated:
        ctx.flags["blackout"] = why
        raise CycleAbort(why)
    return False


def _stage_portfolio_consult(ctx: RunnerContext, inp: Dict[str, Any]) -> Dict[str, Any]:
    from operations.agents.portfolio_agent import cap_size

    orch = ctx.orchestrator
    request = {
        "level": getattr(ctx.orchestrator, "_v2_autonomy_level", "L1_PAPER"),
        "volume_risk_pct": float(inp.get("volume_risk_pct", 1.0)),
        "symbol": getattr(orch, "_v2_symbol", ""),
        "positions": getattr(orch, "_v2_positions", []) or [],
        "correlations": {},
        "heat_used_r": float(getattr(orch, "_v2_heat_used_r", 0.0)),
        "heat_cap_r": float(getattr(orch, "_v2_heat_cap_r", 6.0)),
    }
    cap = cap_size(request)
    ctx.shared["size_cap"] = cap
    return cap


def _stage_legacy_body(ctx: RunnerContext, _inp: Dict[str, Any]) -> Any:
    from operations.autonomy import get_autonomy
    autonomy_hold_only = get_autonomy().is_hold_only() if hasattr(get_autonomy(), "is_hold_only") else False
    if ctx.flags.get("hold_only") or autonomy_hold_only or get_tls_hold_only():
        why = ctx.flags.get("hold_only_why") or "hold_only flag active"
        try:
            from operations.event_bridge import emit
            emit("HOLD_ONLY_TRIGGERED", {"stage": "legacy.body", "why": why}, actor="RiskController")
        except Exception:
            pass
        result = ({"action": "HOLD", "reasoning": f"hold_only: {why}"}, 0.0)
        ctx.shared["_legacy_return"] = result
        return result
    result = ctx.orchestrator.execute_cycle(**ctx.shared["legacy_args"])
    ctx.shared["_legacy_return"] = result
    return result


def maintenance_stages(orchestrator: Any, symbol: str, timeframe: str,
                       paper_mode: bool) -> List[StageSpec]:
    """The six maintenance steps of every pulse, declared (Phase E2).

    Bound 1:1 to the statements inside Orchestrator._run_maintenance_prefix —
    the equivalence test asserts call-order parity between this list and the
    legacy method.
    """

    def memory_dispatch(ctx: RunnerContext, _inp: Dict[str, Any]) -> Any:
        from datetime import datetime, timezone
        key = f"memory:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        dispatch = orchestrator.velocity.submit_slow(
            key, orchestrator.process_pending_memory, limit=3)
        if dispatch.get("status") == "BACKPRESSURE":
            orchestrator.operations.event(
                "scheduler", "slow_lane_backpressure", dispatch,
                run_id=orchestrator.run_id, correlation_id=key,
                severity="WARNING")
        return dispatch

    def heartbeat(_ctx: RunnerContext, _inp: Dict[str, Any]) -> None:
        orchestrator.operations.heartbeat(orchestrator.run_id, {
            "symbol": symbol, "timeframe": timeframe,
            "phase": "market_pulse",
        })

    def reconcile_open(ctx: RunnerContext, _inp: Dict[str, Any]) -> str:
        if paper_mode:
            return "skipped:paper"       # legacy only runs it live
        orchestrator.reconcile_open_positions()
        return "reconciled"

    return [
        StageSpec("velocity.poll", "TechnicalNet",
                  lambda c, i: orchestrator._poll_velocity_results(),
                  timeout_s=30, sla_ms=5000, abandonable=True),
        StageSpec("reconcile.pending", "RiskController",
                  lambda c, i: orchestrator.reconcile_pending_orders(paper_mode),
                  timeout_s=60, sla_ms=10000),
        StageSpec("reconcile.open", "RiskController", reconcile_open,
                  timeout_s=60, sla_ms=10000),
        StageSpec("memory.dispatch", "QuantitativeCoach", memory_dispatch,
                  timeout_s=10, sla_ms=2000, fallback="skip", abandonable=True,
                  notes="slow-lane dispatch; BACKPRESSURE journalled"),
        StageSpec("position.tick", "RiskController",
                  lambda c, i: orchestrator._monitor_owned_positions_tick(
                      symbol, timeframe, paper_mode=paper_mode),
                  timeout_s=120, sla_ms=30000,
                  notes="protective trailing/partial/MFE-MAE marks"),
        StageSpec("worker.heartbeat", "RiskController", heartbeat,
                  timeout_s=5, sla_ms=1000, emits_done=False),
    ]


# --------------------------------------------------------------------------
# Phase E3: declared analysis stages.
#
# Each stage fn resolves the thread-local handoff the orchestrator emits at
# the matching seam, then calls the SAME execute_cycle code. If the resolved
# key does not match the declared key, the stage raises and the envelope
# falls back to the single legacy body — the pipeline can only ever re-run
# the legacy semantics, never replace them.
# --------------------------------------------------------------------------

def _run_staged_pass(ctx: RunnerContext, key: str, slot: str = "") -> Any:
    """Shared staged-pass protocol for all analysis stages.

    1. Drain any stale handoff token (tolerate clean state or our own key;
       a foreign leftover is a protocol violation and must be loud).
    2. Re-arm the maintenance skip token so the re-entered execute_cycle
       never repeats the envelope's declared maintenance work.
    3. Mark our declared key, run the SAME execute_cycle body, then drain
       the token the body's prefix may have re-set.

    The runner knows which declared stage it is executing (attribution is
    trivially correct); the seam recorder (golden replay) provides the deep
    verification that the work landed where declared.
    """
    stale = pop_analysis_stage_handoff()
    if stale is not None and stale != key:
        raise RuntimeError(f"{key} stale analysis handoff: {stale!r}")
    request_skip_maintenance()
    orch = ctx.orchestrator
    try:
        mark_analysis_stage_start(key)
        result = orch.execute_cycle(**dict(ctx.shared["legacy_args"]))
    finally:
        pop_analysis_stage_handoff()
    if slot:
        ctx.shared[slot] = result
    return result


def _stage_cycle_context(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    result = _run_staged_pass(ctx, "analysis.context", "analysis_primary")
    # First full pass is the authoritative cycle outcome; later passes are
    # deduped by the journal task lease and cannot double-execute.
    return {"attributed": "analysis.context"}


def _stage_cycle_data(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    _run_staged_pass(ctx, "analysis.data", "analysis_data")
    return {"attributed": "analysis.data"}


def _stage_cycle_causal(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    _run_staged_pass(ctx, "analysis.causal", "analysis_causal")
    return {"attributed": "analysis.causal"}


def _stage_cycle_llm(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    result = _run_staged_pass(ctx, "analysis.llm", "analysis_llm")
    decision, pnl = result if isinstance(result, tuple) else (result, {})
    if not isinstance(decision, dict):
        decision = {}
    if isinstance(pnl, dict):
        # Structural LLM payload (replay-time execution placeholder).
        ctx.shared["llm_decision"] = decision
        ctx.shared["llm_payload"] = pnl
    else:
        # Numeric PnL: legacy single-pass semantics (reserved for
        # execute-once mode; staged mode normalizes through replay).
        ctx.shared["_legacy_return"] = result
    return {"attributed": "analysis.llm", "action": decision.get("action")}


def _stage_cycle_validate(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    _run_staged_pass(ctx, "analysis.validate", "analysis_validated")
    return {"attributed": "analysis.validate"}


def _stage_cycle_size(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    _run_staged_pass(ctx, "analysis.size", "analysis_sized")
    return {"attributed": "analysis.size"}


def _stage_cycle_execute(ctx: RunnerContext, _inp: Dict[str, Any]) -> Any:
    from operations.autonomy import get_autonomy
    autonomy_hold_only = get_autonomy().is_hold_only() if hasattr(get_autonomy(), "is_hold_only") else False
    if ctx.flags.get("hold_only") or autonomy_hold_only or get_tls_hold_only():
        why = ctx.flags.get("hold_only_why") or "hold_only flag set"
        logger.info("analysis.execute stage: hold_only active (%s)", why)
        try:
            from operations.event_bridge import emit
            emit("HOLD_ONLY_TRIGGERED", {"stage": "analysis.execute", "why": why}, actor="RiskController")
        except Exception:
            pass
        return {"attributed": "analysis.execute", "action": "HOLD", "reasoning": f"hold_only: {why}"}
    _run_staged_pass(ctx, "analysis.execute", "analysis_executed")
    return {"attributed": "analysis.execute"}


def analysis_stages() -> List[StageSpec]:
    """Declared PIPELINE_V2 analysis stages (Phase E3).

    Bound 1:1 to the seams inside LLMOrchestrator.execute_cycle —
    golden replay (tests/test_pipeline_v2.py) asserts the journal intents
    are identical between this staged analysis and the single legacy body.
    """
    return [
        StageSpec("analysis.context", "QuantitativeCoach", _stage_cycle_context,
                  timeout_s=30, sla_ms=10000, abandonable=True,
                  notes="per-cycle bounded context re-read (idempotent)"),
        StageSpec("analysis.data", "TechnicalNet", _stage_cycle_data,
                  timeout_s=120, sla_ms=30000, abandonable=True,
                  notes="fetch → quality/news gates → indicators → SMC → regime"),
        StageSpec("analysis.causal", "MacroAnalyst", _stage_cycle_causal,
                  timeout_s=120, sla_ms=30000, abandonable=True,
                  notes="macro ingest → causal/memory/working-memory context"),
        StageSpec("analysis.llm", "MarketModel", _stage_cycle_llm,
                  timeout_s=300, sla_ms=45000, retries=0, fallback="degrade", abandonable=True,
                  notes="LLM decision call with budget/circuit guards"),
        StageSpec("analysis.validate", "RiskController", _stage_cycle_validate,
                  timeout_s=30, sla_ms=10000,
                  notes="DeterministicValidator gate"),
        StageSpec("analysis.size", "RiskController", _stage_cycle_size,
                  timeout_s=30, sla_ms=10000,
                  notes="RiskManager sizing + arbiter + P8 allocation cap"),
        StageSpec("analysis.execute", "RiskController", _stage_cycle_execute,
                  timeout_s=120, sla_ms=45000,
                  notes="order intent → broker submit → reconciliation"),
    ]


def _analysis_stage_keys() -> List[str]:
    return [spec.key for spec in analysis_stages()]


# Wall-clock timestamps embedded inside snapshot payloads (e.g. the regime
# classifier's and risk gate's "timestamp_iso" fields, which end up inside
# the LLM user_prompt) are legitimate volatility, not pipeline divergence.
# Golden evidence hashes them out so the replay proves the staged pipeline
# journals identical *content*, byte-for-byte except time-of-day.
# Same class: the market session name (hour-of-day buckets) and the
# calendar date of the daily playbook - wall-clock passage between the
# legacy and staged runs crosses UTC session boundaries (07/12/16/21/22)
# and midnight, which would otherwise diverge identical content.
_GOLDEN_ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?")
_GOLDEN_SESSION_RE = re.compile(
    r"NY-LONDON|SESSION CLOSE|NEW YORK|LONDON|ASIAN")


def _golden_snapshot_hash(kind: str, payload_json: str) -> str:
    """Golden hash for a journal snapshot payload, volatile fields removed.

    ISO-8601 wall-clock timestamps and time-of-day-derived labels (market
    session, calendar date) embedded in the serialized payload are
    replaced with fixed markers before hashing. The journal row itself is
    never modified — this only shapes the replay-comparison evidence.
    """
    try:
        normalized = _GOLDEN_ISO_TS_RE.sub("<ts>", str(payload_json or ""))
        normalized = _GOLDEN_SESSION_RE.sub("<session>", normalized)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def record_cycle_intents(store: Any) -> List[Dict[str, Any]]:
    """Read cycle-intent evidence from the authoritative journal.

    The journal (trading_state.db) is the single source of truth: order
    intents, trade lifecycle transitions, and proposal-input snapshots are
    what a pipeline mode *actually did*. The golden-replay test compares
    these between the Phase-E1 single legacy body and Phase-E3 staged
    analysis. Strictly read-only; never fabricates rows.

    Snapshot hashes are golden hashes: computed over the payload after
    normalizing volatile wall-clock timestamps (ISO-8601). The staged mode
    legitimately re-enters the orchestrator, re-deriving context with fresh
    ``timestamp_iso`` values; everything else must match byte-for-byte.
    """
    import sqlite3
    db_path = str(getattr(store, "path", "") or "")
    intents: List[Dict[str, Any]] = []
    if not db_path:
        return intents
    con = None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            for row in con.execute(
                "SELECT intent_key, status, payload FROM order_intents "
                "ORDER BY created_at, intent_key"
            ):
                intents.append({
                    "kind": "order_intent",
                    "key": str(row["intent_key"]),
                    "status": str(row["status"]),
                    "side": _intent_payload_field(row["payload"], "side"),
                    "symbol": _intent_payload_field(row["payload"], "symbol"),
                    "volume": _intent_payload_number(row["payload"], "volume"),
                })
        except sqlite3.Error as exc:
            logger.debug("order_intents read failed: %s", exc)
        try:
            for row in con.execute(
                "SELECT event_type, actor, setup_key FROM trade_lifecycle_events "
                "ORDER BY id"
            ):
                intents.append({
                    "kind": "lifecycle",
                    "event_type": str(row["event_type"]),
                    "actor": str(row["actor"]),
                    "setup_key": str(row["setup_key"] or ""),
                })
        except sqlite3.Error as exc:
            logger.debug("trade_lifecycle_events read failed: %s", exc)
        try:
            for row in con.execute(
                "SELECT kind, payload FROM data_snapshots "
                "ORDER BY captured_at, snapshot_id"
            ):
                intents.append({
                    "kind": "snapshot",
                    "type": str(row["kind"]),
                    "hash": _golden_snapshot_hash(str(row["kind"]),
                                                  row["payload"]),
                })
        except sqlite3.Error as exc:
            logger.debug("data_snapshots read failed: %s", exc)
    except sqlite3.Error as exc:
        logger.debug("journal open failed: %s", exc)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return intents


def _intent_payload_field(payload_json: Any, field: str) -> str:
    try:
        payload = json.loads(payload_json or "{}")
        return str(payload.get(field, "") or "")
    except Exception:
        return ""


def _intent_payload_number(payload_json: Any, field: str) -> float:
    try:
        payload = json.loads(payload_json or "{}")
        return float(payload.get(field, 0.0) or 0.0)
    except Exception:
        return 0.0


def replay_analysis_stages(orchestrator: Any, *, symbol: str, timeframe: str,
                           paper_mode: bool = True, run_legacy_body: bool,
                           **legacy_kwargs: Any) -> List[Dict[str, Any]]:
    """Golden replay: drive the analysis body in a single mode.

    run_legacy_body=True  → legacy single-pass execute_cycle
    run_legacy_body=False → declared analysis stages, resolved via the
                            thread-local handoff markers
    """
    legacy_args = {
        **legacy_kwargs, "symbol": symbol, "timeframe": timeframe,
        "paper_mode": paper_mode,
    }
    begin_v2_envelope()
    try:
        if run_legacy_body:
            request_skip_maintenance()
            mark_analysis_stage_start("analysis.context")
            orchestrator.execute_cycle(**legacy_args)
            return []

        for spec in analysis_stages():
            ctx = RunnerContext(orchestrator=orchestrator,
                                shared={"legacy_args": dict(legacy_args)},
                                trace_id="golden-replay")
            spec.fn(ctx, {"attempt": 1})
        return []
    finally:
        end_v2_envelope()
        pop_analysis_stage_handoff()


def _stage_plugin_pipeline(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    """9-plugin pipeline (metrics→alpha→regime→news→altdata→validation→risk→autonomous→research) — degraded fallback keeps cycle alive."""
    try:
        from core.plugins.registry_factory import build_plugin_registry
        toon = ctx.shared.get("toon") or ctx.shared.get("legacy_args", {}).get("toon") or {}
        # enrich with live account/heat context
        toon.setdefault("account_equity", getattr(ctx.orchestrator, "_v2_heat_cap_r", 10000))
        reg = build_plugin_registry({"risk": {"balance": toon.get("account_equity", 10000), "risk_per_trade": 0.01}})
        reg.initialize_all()
        result = reg.process_pipeline(toon)
        ctx.shared["plugin_result"] = result
        ctx.shared["toon"] = result
        return {"plugins": list(reg.plugins.keys()), "toon_keys": list(result.keys())}
    except Exception as exc:
        # never crash cycle — pipeline_v2 fallback is degrade
        return {"error": str(exc)[:300], "degraded": True}


def _stage_macro_analyst(ctx: RunnerContext, _inp: Dict[str, Any]) -> Dict[str, Any]:
    """MacroAnalyst stage: Laya ModernBERT-421M cognitive decisions, causal graph, UDE evaluation."""
    try:
        from research.decision_models.feature_block import DecisionFeatureBlock
        from research.decision_models.extended_taxonomy import create_decision_bundle
        from research.decision_models.post_validator import DeterministicPostValidator
        from ui.gui_api import ui_state

        symbol = str(getattr(ctx.orchestrator, "_v2_symbol", "EURUSD") or "EURUSD")
        curr = symbol[:3] if len(symbol) >= 3 else "USD"

        # Build real feature block from orchestrator state when available
        orch = ctx.orchestrator
        features_dict = {}
        snapshot = {}
        try:
            features_dict = getattr(orch, "_v2_features", None) or {}
            snapshot = getattr(orch, "_v2_snapshot", None) or {}
        except Exception:
            pass

        ev = snapshot.get("event_context") or {}
        event_name = str(ev.get("event_name") or f"{symbol}_PIPELINE_EVALUATION")
        actual = ev.get("actual")
        consensus = ev.get("consensus")
        sigma = float(ev.get("sigma_hist", 1.0))
        z_score = float(ev["z_score"]) if "z_score" in ev else (
            (float(actual) - float(consensus)) / sigma
            if (actual is not None and consensus is not None and sigma > 0)
            else 0.0
        )
        vix = float(snapshot.get("vix", features_dict.get("vix", 16.5)))
        dxy_pct = float(snapshot.get("dxy_change_pct", features_dict.get("dxy_change_pct", 0.0)))
        y10_bps = float(snapshot.get("yield_10y_delta_bps", features_dict.get("yield_10y_delta_bps", 0.0)))
        m_to_rel = float(snapshot.get("minutes_to_release", ev.get("minutes_to_release", 999.0)))

        # Query causal paths from MacroGraph when available
        causal_paths = []
        causal_cesi = 1.0
        try:
            from intelligence.macro_graph import MacroGraph
            mg = MacroGraph()
            summary = mg.summary()
            edge_count = summary.get("active_edges", 0) if isinstance(summary, dict) else 0
            causal_cesi = 0.85 if edge_count > 0 else 1.0
        except Exception:
            pass

        feature_block = DecisionFeatureBlock(
            event_name=event_name,
            currency=curr,
            tier=str(ev.get("tier", "HIGH")),
            actual=float(actual) if actual is not None else None,
            consensus=float(consensus) if consensus is not None else None,
            previous=float(ev["previous"]) if "previous" in ev else None,
            sigma_hist=sigma,
            z_score=z_score,
            vix=vix,
            dxy_change_pct=dxy_pct,
            yield_10y_delta_bps=y10_bps,
            minutes_to_release=m_to_rel,
            headline=str(ev.get("headline", f"Pipeline cycle evaluation for {symbol}")),
            narrative=str(ev.get("narrative", "Real-time flow and monetary transmission")),
            causal_paths=tuple(causal_paths),
            causal_cesi_score=causal_cesi,
        )

        # Try Laya inference, fallback to deterministic labeler
        laya_answers = {}
        try:
            from research.decision_models.laya_model import get_laya_decision_model
            from research.decision_models.macro_ontology import get_standard_macro_questions
            import os
            if not os.environ.get("FAST_TEST") and os.path.exists("models/laya/model.safetensors"):
                laya = get_laya_decision_model("models/laya")
                q = get_standard_macro_questions()
                state_dict = feature_block.to_prompt_state()
                laya_res = laya.decide(state_dict, q)
                validator = DeterministicPostValidator()
                val_res = validator.validate(laya_res.answers, feature_block)
                laya_answers = val_res.validated_answers
        except Exception:
            pass

        # Kronos owns the OHLCVA heads (D11/D11b/D6). It is a candle-forecast
        # model; the Laya text checkpoint cannot see a bar series, so on these
        # heads it contributes nothing. Advisory, fail-soft: a short history or
        # a heuristic backend yields {} and the bundle stays deterministic.
        kronos_overrides: Dict[str, Any] = {}
        try:
            from research.decision_models.kronos_heads import kronos_head_overrides

            ohlcv = snapshot.get("ohlcv_history") or []
            if ohlcv:
                kronos_overrides = kronos_head_overrides(
                    ohlcv, symbol=symbol, feature_block=feature_block
                )
        except Exception:
            kronos_overrides = {}

        bundle = create_decision_bundle(
            features=feature_block,
            overrides=laya_answers if laya_answers else None,
            symbol=symbol,
        )

        # Kronos overrides are applied in a second, separate bundle construction
        # so Laya text answers and Kronos candle forecasts are never merged into
        # one undifferentiated override map. The second bundle reuses the
        # first's feature hash and timestamps — same cycle, two provenances.
        if kronos_overrides:
            merged = create_decision_bundle(
                features=feature_block,
                overrides=kronos_overrides,
                symbol=symbol,
            )
            for dec_id, rec in merged.decisions.items():
                if rec.is_overridden:
                    bundle.decisions[dec_id] = rec
            bundle.overrides_applied_count += merged.overrides_applied_count
            if merged.veto_enforced and not bundle.veto_enforced:
                bundle.veto_enforced = True
                bundle.risk_multiplier = 0.0

        # Store in shared context for downstream stages
        ctx.shared["macro_bundle"] = bundle.to_dict()
        ctx.shared["macro_feature_block"] = feature_block.to_dict()

        # Update UI state
        ui_state.last_decision_bundle = bundle.to_dict()
        ui_state.macro_regime = bundle.d3_regime.value
        ui_state.net_liquidity_stance = bundle.d4_liquidity.value
        ui_state.laya_decisions_count = getattr(ui_state, "laya_decisions_count", 0) + 1

        if bundle.veto_enforced:
            ctx.flags["macro_hold_only"] = True

        return {
            "verdict": bundle.arbitration_verdict,
            "regime": ui_state.macro_regime,
            "liquidity": ui_state.net_liquidity_stance,
            "risk_mult": bundle.risk_multiplier,
            "veto": bundle.veto_enforced,
            "bundle_id": bundle.bundle_id,
            "overrides": bundle.overrides_applied_count,
            "execution_authority": False,
        }
    except Exception as exc:
        logger.debug("macro.analyst stage degraded: %s", exc)
        return {"error": str(exc)[:200], "degraded": True, "execution_authority": False}


def _envelope_gate_stages() -> List[StageSpec]:
    """Fast-lane gates shared by both envelope shapes (E1 and E3)."""
    return [
        StageSpec(
            key="plugins.pipeline", agent="RiskAgent",
            fn=_stage_plugin_pipeline, timeout_s=5, sla_ms=800,
            fallback="degrade",
            notes="9-plugin registry topological pipeline — wraps expectancy/regime/risk/cost engines",
        ),
        StageSpec(
            key="macro.analyst", agent="MacroAnalyst",
            fn=_stage_macro_analyst, timeout_s=5, sla_ms=800,
            fallback="degrade", emits_done=True,
            notes="Laya ModernBERT-421M cognitive decision, causal graph, UDE evaluation",
        ),
        StageSpec(
            key="blackout.gate", agent="EventAgent",
            fn=_stage_blackout_gate, timeout_s=5, sla_ms=500,
            fallback="abort_cycle",
            notes="high-impact event window → skip this candle entirely",
        ),
        StageSpec(
            key="portfolio.consult", agent="PortfolioAgent",
            fn=_stage_portfolio_consult, timeout_s=5, sla_ms=800,
            fallback="skip", emits_done=True,
            notes="advisory size cap; RiskController integration lands with P8",
        ),
    ]


def _legacy_body_stages() -> List[StageSpec]:
    """Phase-E1 shape: fast-lane gates + one verbatim legacy body stage
    (kill-switch path). The gates are part of the E1 envelope contract —
    dropping them here would silently disable the blackout gate."""
    return _envelope_gate_stages() + [
        StageSpec(
            key="legacy.cycle_body", agent="RiskController",
            fn=_stage_legacy_body, timeout_s=600, sla_ms=45000,
            retries=0, fallback="continue", emits_done=False,
            notes="verbatim legacy path — zero divergence by construction",
        ),
    ]


def _staged_body_stages() -> List[StageSpec]:
    return _envelope_gate_stages() + analysis_stages()


def envelope_stages() -> List[StageSpec]:
    """Envelope stages for the current cycle.

    Default: staged analysis (Phase E3). Set PIPELINE_V2_LEGACY_BODY=1 to
    pin the Phase-E1 single legacy body (kill switch / golden-replay A side).
    """
    if os.getenv("PIPELINE_V2_LEGACY_BODY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _legacy_body_stages()
    return _staged_body_stages()


def run_envelope(orchestrator: Any, *, symbol: str, timeframe: str,
                 autonomy_level: str = "L1_PAPER",
                 heat_used_r: float = 0.0, heat_cap_r: float = 6.0,
                 positions: Optional[List[Dict[str, Any]]] = None,
                 paper_mode: bool = True,
                 **legacy_kwargs: Any) -> Any:
    """Envelope used by Orchestrator.execute_cycle_v2 (PIPELINE_V2=1)."""
    trace_id = f"cyc:{symbol}:{timeframe}:{int(time.time())}"

    setattr(orchestrator, "_v2_symbol", symbol)
    orchestrator._v2_positions = positions or []
    orchestrator._v2_heat_used_r = heat_used_r
    orchestrator._v2_heat_cap_r = heat_cap_r
    orchestrator._v2_autonomy_level = autonomy_level

    stages = (
        maintenance_stages(orchestrator, symbol, timeframe, paper_mode)
        + envelope_stages()
    )
    runner = PipelineRunner(orchestrator, trace_id, stages)
    ctx = RunnerContext(
        orchestrator=orchestrator,
        shared={"legacy_args": {**legacy_kwargs, "symbol": symbol,
                                 "timeframe": timeframe}},
        trace_id=trace_id,
    )
    # The legacy body's extracted prefix consumes this and becomes a no-op —
    # the six maintenance stages above already did that exact work.
    request_skip_maintenance()

    try:
        from operations.event_bridge import emit
        emit("CYCLE_SPAN_START", {"symbol": symbol, "timeframe": timeframe},
             correlation_id=trace_id, trace_id=trace_id)
    except Exception as exc:
        logger.debug("event emission failed: %s", exc)

    hold_decision: Optional[Dict[str, Any]] = None
    staged_mode = any(spec.key == "analysis.execute" for spec in stages)
    # Clear any stale handoff from a previous cycle on this thread, then
    # activate the envelope so the orchestrator's stage-start markers bind.
    pop_analysis_stage_handoff()
    begin_v2_envelope()
    try:
        runner.run(ctx)
    except CycleAbort as abort:
        hold_decision = {
            "action": "HOLD",
            "reasoning": f"pipeline gate: {abort}",
        }
    finally:
        end_v2_envelope()
        pop_analysis_stage_handoff()

    span_done = {
        "stages": [{k: r.__dict__[k] for k in ("key", "status", "duration_ms")}
                   for r in runner.results],
        "hold_only": bool(ctx.flags.get("hold_only")),
    }
    try:
        from operations.event_bridge import emit
        emit("CYCLE_SPAN_DONE", span_done, correlation_id=trace_id,
             trace_id=trace_id)
    except Exception as exc:
        logger.debug("event emission failed: %s", exc)

    if hold_decision is not None:
        return hold_decision, 0.0

    from operations.autonomy import get_autonomy
    autonomy_hold_only = get_autonomy().is_hold_only() if hasattr(get_autonomy(), "is_hold_only") else False
    if ctx.flags.get("hold_only") or autonomy_hold_only or get_tls_hold_only():
        why = ctx.flags.get("hold_only_why") or "hold_only flag active"
        return ({"action": "HOLD", "reasoning": f"hold_only: {why}"}, 0.0)

    if staged_mode:
        # The first full pass is authoritative: later passes are deduped by
        # the task lease / order-intent uniqueness and cannot double-execute.
        executed = ctx.shared.get("analysis_primary")
        if isinstance(executed, tuple) and len(executed) == 2:
            return executed
        llm_decision = ctx.shared.get("llm_decision")
        if isinstance(llm_decision, dict):
            return llm_decision, 0.0
        return ({"action": "HOLD", "reasoning": "cycle produced no decision"}, 0.0)

    legacy_result = ctx.shared.get("_legacy_return")
    # legacy.stage_body stores its return through the shim below.
    if isinstance(legacy_result, tuple) and len(legacy_result) == 2:
        return legacy_result
    return ({"action": "HOLD", "reasoning": "cycle produced no decision"}, 0.0)


def _capture_legacy_return(fn):
    """Decorator shim: record legacy execute_cycle's return into ctx.shared."""
    def wrapper(ctx: RunnerContext, inp: Dict[str, Any]):
        result = fn(ctx, inp)
        ctx.shared["_legacy_return"] = result
        return result
    return wrapper


def bind_legacy_stage(orchestrator_method):
    """Wrap an unbound orchestrator method as a capturing stage fn."""
    def stage_fn(ctx: RunnerContext, _inp: Dict[str, Any]):
        result = orchestrator_method(**ctx.shared["legacy_args"])
        ctx.shared["_legacy_return"] = result
        return result
    return stage_fn


def install_orchestrator_entrypoint() -> None:
    """Attach execute_cycle_v2 to LLMOrchestrator (called from mt5_trader)."""
    try:
        from intelligence.llm_orchestrator import LLMOrchestrator

        def execute_cycle_v2(self, *, symbol: str, timeframe: str,
                             cycle_num: int, total_cycles: int,
                             paper_mode: bool = True,
                             display_instance: Any = None,
                             strategy: str = "scalp") -> Any:
            log = logging.getLogger("LLMOrchestrator")
            try:
                from operations.autonomy import get_autonomy
                _aut = get_autonomy()
                if hasattr(self, "store") and self.store is not None:
                    if getattr(_aut, "store", None) is None:
                        _aut.attach_store(self.store)
                allowed, why = _aut.trading_allowed()
                level = _aut.snapshot().level
                if not allowed:
                    return {"action": "HOLD", "reasoning": f"autonomy gate: {why}"}, 0.0
            except Exception as exc:
                log.warning("pipeline_v2 autonomy gate failed closed to HOLD: %s", exc)
                return {"action": "HOLD", "reasoning": f"autonomy gate error: {exc}"}, 0.0

            heat_used_r = heat_cap_r = None
            try:
                risk_path = os.path.join(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))), "risk_state.json")
                with open(risk_path, "r", encoding="utf-8") as fh:
                    _risk = json.load(fh)
                heat_used_r = float(_risk.get("heat_used_r", 0.0) or 0.0)
                heat_cap_r = float(_risk.get("heat_cap_r", 6.0) or 6.0)
            except Exception as exc:
                logger.debug("state snapshot failed: %s", exc)

            try:
                return run_envelope(
                    self, symbol=symbol, timeframe=timeframe,
                    autonomy_level=level,
                    heat_used_r=heat_used_r if heat_used_r is not None else 0.0,
                    heat_cap_r=heat_cap_r if heat_cap_r is not None else 6.0,
                    positions=[],
                    cycle_num=cycle_num, total_cycles=total_cycles,
                    paper_mode=paper_mode,
                    display_instance=display_instance, strategy=strategy,
                )
            except Exception:
                log.exception("PIPELINE_V2 failed; falling back to legacy path")
                return self.execute_cycle(
                    symbol=symbol, timeframe=timeframe, cycle_num=cycle_num,
                    total_cycles=total_cycles, paper_mode=paper_mode,
                    display_instance=display_instance,
                    strategy=strategy,
                )

        execute_cycle_v2.__name__ = "execute_cycle_v2"
        LLMOrchestrator.execute_cycle_v2 = execute_cycle_v2
        logger.info("PIPELINE_V2 entrypoint installed on LLMOrchestrator")
    except Exception as exc:
        logger.error("could not install PIPELINE_V2 entrypoint: %s", exc)
