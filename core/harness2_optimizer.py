"""Harness² — Recursive Agent Harnessing for the LLM Trader (frozen weights).

Implements the two coupled levels from google-research/harness2:

* Task-level recursion — contrastive Propose–Probe–Compose over the eight
  editable harness components. No ground-truth verifier is required: edits are
  supported or rejected by behavioural contrasts between executions of the same
  task.
* Harnessing-level recursion — a persistent improvement library that stores
  adopted harnesses and evidence-supported lessons, and feeds later proposals.

Trading-specific safety deviation from the paper: ``GUARDRAILS`` is a frozen
component. Automatic edits to risk gates, margin invariants, or kill switches
are rejected fail-closed. A harness optimiser must never be able to widen its
own risk envelope. Guardrail *tightening* remains a human/operator change.

The module is dependency-free (stdlib only) and never mutates source files:
edits are an in-memory overlay that can be rendered into an LLM context or
applied by a sandboxed executor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.harness.kernel import HarnessPlugin, TradingContext
from core.state_persistence import StoreBackedState

logger = logging.getLogger("Harness2Optimizer")


class HarnessComponent(str, Enum):
    """The eight editable components exposed by Harness²."""
    SYSTEM_PROMPT = "system_prompt"
    GUARDRAILS = "guardrails"
    MEMORY = "memory"
    TOOL_GUIDANCE = "tool_guidance"
    SUBAGENTS = "subagents"
    SKILLS = "skills"
    SCRIPTS = "scripts"
    PLUGINS = "plugins"


class EditScope(str, Enum):
    """Scoped proposals: invariant (cross-domain) and domain-specific."""
    INVARIANT = "invariant"
    DOMAIN = "domain"


class HarnessSafetyError(ValueError):
    """Base class for fail-closed harness edit rejections."""


class FrozenComponentError(HarnessSafetyError):
    """Raised when an automatic edit targets a frozen guardrail component."""


class InvalidEditError(HarnessSafetyError):
    """Raised when an edit cannot be applied unambiguously or safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Guardrail / prompt-injection screening
# ---------------------------------------------------------------------------

FORBIDDEN_EDIT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\b(bypass|disable|skip|override|circumvent)\b.{0,40}\b(risk|guardrail|validator|kill\s*switch|stop\s*loss|margin)", "risk_bypass"),
    (r"\b(no|without|remove|widen|loosen)\b.{0,20}\b(stop\s*loss|guardrail|margin\s*(check|limit)|risk\s*(gate|limit|check))", "risk_weakening"),
    (r"\b(unlimited|maximum|max)\s+leverage\b", "leverage_escalation"),
    (r"\bmartingale|grid\s+averaging|average\s+down\b", "ruin_strategy"),
    (r"\b(ignore|forget|discard)\b.{0,30}\b(instructions|guardrails|risk\s*rules)\b", "instruction_override"),
    (r"\b(jailbreak|prompt\s*injection|developer\s*mode)\b", "prompt_injection"),
    (r"\b(execute|route|submit)\b.{0,30}\b(without|before)\b.{0,30}\b(validation|risk\s*check|preflight)", "validation_bypass"),
)


def scan_edit_text(text: str) -> Optional[str]:
    """Return the first forbidden-behaviour tag found, or None when clean."""
    lowered = (text or "").lower()
    for pattern, tag in FORBIDDEN_EDIT_PATTERNS:
        if re.search(pattern, lowered):
            return tag
    return None


# ---------------------------------------------------------------------------
# Manifest — the eight components mapped onto this repository
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentSpec:
    """Repository mapping and mutability policy for one harness component."""
    component: HarnessComponent
    role: str
    repo_paths: Tuple[str, ...]
    mutable: bool
    description: str


def default_component_specs() -> Dict[HarnessComponent, ComponentSpec]:
    return {
        HarnessComponent.SYSTEM_PROMPT: ComponentSpec(
            component=HarnessComponent.SYSTEM_PROMPT,
            role="context",
            repo_paths=("intelligence/llm_orchestrator.py", "intelligence/debate_protocol.py"),
            mutable=True,
            description="Trading persona, discipline, and risk mindset injected before analysis.",
        ),
        HarnessComponent.GUARDRAILS: ComponentSpec(
            component=HarnessComponent.GUARDRAILS,
            role="context",
            repo_paths=(
                "core/trading_safety.py",
                "core/risk_manager.py",
                "core/margin_safety.py",
                "core/kill_switch.py",
            ),
            mutable=False,
            description="Fail-closed deterministic risk gates and margin invariants. Frozen by design.",
        ),
        HarnessComponent.MEMORY: ComponentSpec(
            component=HarnessComponent.MEMORY,
            role="context",
            repo_paths=("intelligence/trading_memory.py", "intelligence/local_agent_memory.py"),
            mutable=True,
            description="L0-L4 multi-tier memory, market context, and trade journal retrieval.",
        ),
        HarnessComponent.TOOL_GUIDANCE: ComponentSpec(
            component=HarnessComponent.TOOL_GUIDANCE,
            role="interface",
            repo_paths=("core/trading_tools.py", "core/auto_quant_tools.py"),
            mutable=True,
            description="MT5 tool palette, causal-reasoning tools, and preflight risk checks.",
        ),
        HarnessComponent.SUBAGENTS: ComponentSpec(
            component=HarnessComponent.SUBAGENTS,
            role="orchestration",
            repo_paths=(
                "intelligence/deliberative_graph.py",
                "intelligence/meta_reflector.py",
                "intelligence/debate_protocol.py",
            ),
            mutable=True,
            description="StatArb, MacroMomentum, Deliberation, and Meta-Reflector delegation.",
        ),
        HarnessComponent.SKILLS: ComponentSpec(
            component=HarnessComponent.SKILLS,
            role="context",
            repo_paths=("core/skill_registry.py",),
            mutable=True,
            description="Quant models, backtest harness, and feature-extractor skills.",
        ),
        HarnessComponent.SCRIPTS: ComponentSpec(
            component=HarnessComponent.SCRIPTS,
            role="interface",
            repo_paths=("scripts", "data_fetcher.py"),
            mutable=True,
            description="Data fetchers, preflight probes, and audit scripts.",
        ),
        HarnessComponent.PLUGINS: ComponentSpec(
            component=HarnessComponent.PLUGINS,
            role="middleware",
            repo_paths=(
                "core/harness/plugins.py",
                "operations/prometheus_exporter.py",
                "kronos_mcp",
            ),
            mutable=True,
            description="MCP servers, Prometheus exporters, telemetry, and middleware.",
        ),
    }


@dataclass(frozen=True)
class HarnessManifest:
    """Validated, ordered view of the eight editable components."""
    specs: Mapping[HarnessComponent, ComponentSpec]

    def __post_init__(self) -> None:
        missing = [c.value for c in HarnessComponent if c not in self.specs]
        if missing:
            raise ValueError(f"Harness manifest missing components: {missing}")

    @classmethod
    def default(cls) -> "HarnessManifest":
        return cls(specs=default_component_specs())

    def spec(self, component: HarnessComponent) -> ComponentSpec:
        return self.specs[component]

    def is_mutable(self, component: HarnessComponent) -> bool:
        return bool(self.specs[component].mutable)

    def validate_edit(self, edit: "HarnessEdit") -> Optional[str]:
        """Return a rejection reason, or None when the edit is admissible."""
        spec = self.specs.get(edit.component)
        if spec is None:
            return "unknown_component"
        if not spec.mutable:
            return "guardrails_frozen"
        if not (edit.rationale or "").strip():
            return "missing_rationale"
        if len(edit.replace) > 8000:
            return "replacement_too_large"
        if edit.scope not in (EditScope.INVARIANT, EditScope.DOMAIN):
            return "invalid_scope"
        tag = scan_edit_text(edit.replace)
        if tag:
            return f"forbidden_edit:{tag}"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": {
                c.value: {
                    "role": s.role,
                    "repo_paths": list(s.repo_paths),
                    "mutable": s.mutable,
                    "description": s.description,
                }
                for c, s in self.specs.items()
            }
        }


# ---------------------------------------------------------------------------
# Harness state and edits (in-memory overlay, never source mutation)
# ---------------------------------------------------------------------------

DEFAULT_COMPONENTS: Dict[HarnessComponent, str] = {
    HarnessComponent.SYSTEM_PROMPT: (
        "You are ApexQuant, a disciplined institutional FX trader. "
        "One setup, one invalidation, one size. Risk is decided by the "
        "deterministic validator, never by persuasion. If data is stale or "
        "unavailable, return HOLD."
    ),
    HarnessComponent.GUARDRAILS: (
        "FAIL-CLOSED INVARIANTS (frozen): reject on stale ticks, wide spreads, "
        "closed market, untradable symbol, margin headroom below 2x required, "
        "daily loss cap breach, or kill-switch active. Guardrails cannot be "
        "edited by the optimiser."
    ),
    HarnessComponent.MEMORY: (
        "L0 policy anchors -> L1 atomic facts -> L2 scenario playbooks -> "
        "L3 reviewed strategy memory -> L4 current market context. Retrieve "
        "before proposing; cite memory ids in the rationale."
    ),
    HarnessComponent.TOOL_GUIDANCE: (
        "MT5 tools are read-only until the deterministic validator approves. "
        "Call the preflight probe before any order intent. Never fabricate "
        "prices, ATR, or gate states."
    ),
    HarnessComponent.SUBAGENTS: (
        "StatArb assesses relative value; MacroMomentum assesses regime and "
        "catalysts; Deliberation weighs both; Meta-Reflector can veto on "
        "health, drawdown, or alpha decay."
    ),
    HarnessComponent.SKILLS: (
        "Available skills: quant models, purged walk-forward backtest, feature "
        "extractors, journal audit. Prefer a validated skill over free-form "
        "code. Skills are advisory, never order-authorised."
    ),
    HarnessComponent.SCRIPTS: (
        "Scripts cover data fetch, broker/MT5 preflight, reconciliation, and "
        "audit. A script failure is a NO-TRADE signal, not an invitation to "
        "improvise."
    ),
    HarnessComponent.PLUGINS: (
        "Middleware owns telemetry, Prometheus export, MCP bridges, and "
        "approval outboxes. Telemetry must never carry secrets or order "
        "authority."
    ),
}


@dataclass(frozen=True)
class HarnessEdit:
    """A single component-level patch proposed by an editor."""
    edit_id: str
    component: HarnessComponent
    scope: EditScope
    find: str
    replace: str
    rationale: str
    source: str = "editor"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "component": self.component.value,
            "scope": self.scope.value,
            "find": self.find,
            "replace": self.replace,
            "rationale": self.rationale,
            "source": self.source,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HarnessEdit":
        return cls(
            edit_id=str(data.get("edit_id") or _stable_hash(data)[:16]),
            component=HarnessComponent(str(data["component"])),
            scope=EditScope(str(data.get("scope", EditScope.INVARIANT.value))),
            find=str(data.get("find", "")),
            replace=str(data.get("replace", "")),
            rationale=str(data.get("rationale", "")),
            source=str(data.get("source", "library")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class HarnessState:
    """Executable harness overlay: component text plus applied-edit lineage."""
    components: Mapping[HarnessComponent, str] = field(default_factory=lambda: dict(DEFAULT_COMPONENTS))
    version: str = "h0"
    parent_version: Optional[str] = None
    edits: Tuple[HarnessEdit, ...] = ()

    @classmethod
    def base(cls) -> "HarnessState":
        return cls(components=dict(DEFAULT_COMPONENTS), version="h0")

    @property
    def fingerprint(self) -> str:
        return _stable_hash({c.value: self.components.get(c, "") for c in HarnessComponent})

    def text(self, component: HarnessComponent) -> str:
        return self.components.get(component, "")

    def render(self, exclude: Sequence[HarnessComponent] = ()) -> str:
        """Render the harness as an LLM-ready context block."""
        excluded = set(exclude)
        blocks = []
        for component in HarnessComponent:
            if component in excluded:
                continue
            text = self.components.get(component, "").strip()
            if text:
                blocks.append(f"## {component.value}\n{text}")
        return "\n\n".join(blocks)

    def apply(self, edit: HarnessEdit) -> "HarnessState":
        """Return a successor harness; fail-closed on unsafe or ambiguous edits."""
        if edit.component is HarnessComponent.GUARDRAILS:
            raise FrozenComponentError("guardrails are frozen; operator approval is required")
        current = self.components.get(edit.component, "")
        if edit.find:
            occurrences = current.count(edit.find)
            if occurrences == 0:
                raise InvalidEditError(f"anchor not found in {edit.component.value}")
            if occurrences > 1:
                raise InvalidEditError(f"anchor is ambiguous in {edit.component.value}")
            new_text = current.replace(edit.find, edit.replace, 1)
        else:
            if not edit.replace.strip():
                raise InvalidEditError("empty append edit")
            if edit.replace.strip() in current:
                raise InvalidEditError("edit already present")
            new_text = (current.rstrip() + "\n\n" + edit.replace.strip()).strip()

        if len(new_text) > 20000:
            raise InvalidEditError("component text exceeds 20000 characters")
        components = dict(self.components)
        components[edit.component] = new_text
        return HarnessState(
            components=components,
            version=f"{self.version}+{edit.edit_id[:8]}",
            parent_version=self.version,
            edits=tuple(self.edits) + (edit,),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": {c.value: self.components.get(c, "") for c in HarnessComponent},
            "version": self.version,
            "parent_version": self.parent_version,
            "edits": [e.to_dict() for e in self.edits],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HarnessState":
        raw_components = data.get("components") or {}
        components = {
            c: str(raw_components.get(c.value, DEFAULT_COMPONENTS[c]))
            for c in HarnessComponent
        }
        return cls(
            components=components,
            version=str(data.get("version", "h0")),
            parent_version=data.get("parent_version"),
            edits=tuple(HarnessEdit.from_dict(e) for e in data.get("edits", [])),
        )


# ---------------------------------------------------------------------------
# Task, execution record, and behavioural contrast
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HarnessTask:
    """An open-world trading task: a setup, regime, or execution decision."""
    task_id: str
    domain: str = "forex"
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    regime: str = "UNKNOWN"
    objective: str = "risk_adjusted_expectancy"
    context: Mapping[str, Any] = field(default_factory=dict)

    def signature(self) -> str:
        return _stable_hash({
            "domain": self.domain,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "objective": self.objective,
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "objective": self.objective,
            "context": dict(self.context),
            "signature": self.signature(),
        }


REQUIRED_METRICS = ("trades", "expectancy_r", "max_drawdown_r")


def compute_execution_score(metrics: Mapping[str, Any]) -> float:
    """Score a harness execution from risk-adjusted trading metrics.

    The score is a contrast signal, not a verifier: it ranks executions of the
    same task. Guardrail violations dominate every other term.
    """
    def _num(key: str, default: float = 0.0) -> float:
        try:
            value = float(metrics.get(key, default) or default)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    trades = max(0.0, _num("trades"))
    expectancy = _num("expectancy_r")
    win_rate = min(1.0, max(0.0, _num("win_rate")))
    profit_factor = min(5.0, max(0.0, _num("profit_factor")))
    drawdown = max(0.0, _num("max_drawdown_r"))
    slippage = max(0.0, _num("slippage_bps"))
    violations = max(0.0, _num("guardrail_violations"))
    unauthorized = max(0.0, _num("unauthorized_actions"))
    tool_errors = max(0.0, _num("tool_errors"))

    score = (
        expectancy
        + 0.20 * win_rate
        + 0.05 * profit_factor
        - 0.30 * drawdown
        - 0.002 * slippage
        - 10.0 * violations
        - 20.0 * unauthorized
        - 0.05 * tool_errors
    )
    if trades <= 0:
        score -= 0.01
    return round(score, 6)


@dataclass
class ExecutionRecord:
    """One execution of a harness on one task under matched conditions."""
    task_id: str
    harness_version: str
    harness_fingerprint: str
    ok: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    deliverable: Dict[str, Any] = field(default_factory=dict)
    trajectory: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=_utc_now)

    @property
    def score(self) -> float:
        if not self.ok:
            return float("-inf")
        return compute_execution_score(self.metrics)

    def metric(self, key: str, default: float = 0.0) -> float:
        try:
            value = float(self.metrics.get(key, default) or default)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "harness_version": self.harness_version,
            "harness_fingerprint": self.harness_fingerprint,
            "ok": self.ok,
            "metrics": dict(self.metrics),
            "deliverable": dict(self.deliverable),
            "trajectory": dict(self.trajectory),
            "error": self.error,
            "duration_ms": self.duration_ms,
            "score": self.score,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ContrastPolicy:
    """Thresholds governing what counts as evidence-supported improvement."""
    min_gain: float = 0.02
    min_trades: int = 5
    drawdown_tolerance_r: float = 0.10
    max_tool_error_delta: float = 1.0
    max_slippage_delta_bps: float = 3.0


@dataclass
class BehavioralContrast:
    """Behavioural comparison between a reference and a candidate execution."""
    edit_id: Optional[str]
    component: Optional[str]
    supported: bool
    reason: str
    score_delta: float
    metric_deltas: Dict[str, float]
    regressions: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "component": self.component,
            "supported": self.supported,
            "reason": self.reason,
            "score_delta": self.score_delta,
            "metric_deltas": dict(self.metric_deltas),
            "regressions": list(self.regressions),
            "observations": list(self.observations),
        }


def compare_executions(
    reference: ExecutionRecord,
    candidate: ExecutionRecord,
    policy: ContrastPolicy,
    edit: Optional[HarnessEdit] = None,
) -> BehavioralContrast:
    """Contrast a candidate against a reference without ground-truth labels."""
    edit_id = edit.edit_id if edit else None
    component = edit.component.value if edit else None
    keys = sorted(set(reference.metrics) | set(candidate.metrics))
    deltas = {
        key: round(candidate.metric(key) - reference.metric(key), 6)
        for key in keys
    }

    if not candidate.ok:
        return BehavioralContrast(
            edit_id=edit_id,
            component=component,
            supported=False,
            reason="candidate_execution_failed",
            score_delta=float("-inf"),
            metric_deltas=deltas,
            regressions=["execution_failed"],
        )

    regressions: List[str] = []
    observations: List[str] = []

    violations = candidate.metric("guardrail_violations")
    if violations > reference.metric("guardrail_violations"):
        regressions.append("guardrail_violations")
    if candidate.metric("unauthorized_actions") > reference.metric("unauthorized_actions"):
        regressions.append("unauthorized_actions")
    if candidate.metric("max_drawdown_r") > reference.metric("max_drawdown_r") + policy.drawdown_tolerance_r:
        regressions.append("drawdown_regression")
    if candidate.metric("tool_errors") > reference.metric("tool_errors") + policy.max_tool_error_delta:
        regressions.append("tool_reliability_regression")
    if candidate.metric("slippage_bps") > reference.metric("slippage_bps") + policy.max_slippage_delta_bps:
        regressions.append("slippage_regression")

    if regressions:
        return BehavioralContrast(
            edit_id=edit_id,
            component=component,
            supported=False,
            reason="hard_regression",
            score_delta=float("-inf"),
            metric_deltas=deltas,
            regressions=regressions,
        )

    trades = candidate.metric("trades")
    if trades < policy.min_trades:
        return BehavioralContrast(
            edit_id=edit_id,
            component=component,
            supported=False,
            reason="insufficient_evidence",
            score_delta=0.0,
            metric_deltas=deltas,
            observations=[f"trades={int(trades)} below min_trades={policy.min_trades}"],
        )

    if not reference.ok:
        return BehavioralContrast(
            edit_id=edit_id,
            component=component,
            supported=True,
            reason="recovered_failed_reference",
            score_delta=round(candidate.score, 6),
            metric_deltas=deltas,
            observations=["reference execution failed; candidate executed successfully"],
        )

    score_delta = round(candidate.score - reference.score, 6)
    for key in ("expectancy_r", "win_rate", "max_drawdown_r", "trades", "slippage_bps", "tool_errors"):
        if key in deltas:
            observations.append(f"{key}: {deltas[key]:+.4f}")

    if score_delta < policy.min_gain:
        return BehavioralContrast(
            edit_id=edit_id,
            component=component,
            supported=False,
            reason="no_supported_contrast",
            score_delta=score_delta,
            metric_deltas=deltas,
            observations=observations,
        )

    return BehavioralContrast(
        edit_id=edit_id,
        component=component,
        supported=True,
        reason="supported_by_behavioral_contrast",
        score_delta=score_delta,
        metric_deltas=deltas,
        observations=observations,
    )


# ---------------------------------------------------------------------------
# Editors, executors, composers
# ---------------------------------------------------------------------------

class ProbeExecutor:
    """Protocol for executing a harness. Implementations must not alter weights."""

    def execute(self, harness: HarnessState, task: HarnessTask, phase: str = "probe") -> ExecutionRecord:
        raise NotImplementedError


class CallableProbeExecutor(ProbeExecutor):
    """Adapts ``fn(harness, task, phase) -> ExecutionRecord | dict`` to a probe."""

    def __init__(self, fn: Callable[[HarnessState, HarnessTask, str], Any]):
        self._fn = fn

    def execute(self, harness: HarnessState, task: HarnessTask, phase: str = "probe") -> ExecutionRecord:
        try:
            result = self._fn(harness, task, phase)
        except Exception as exc:  # fail-closed: a crashed probe is not evidence
            logger.warning("harness probe failed for %s: %s", task.task_id, exc)
            return ExecutionRecord(
                task_id=task.task_id,
                harness_version=harness.version,
                harness_fingerprint=harness.fingerprint,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(result, ExecutionRecord):
            return result
        if isinstance(result, Mapping):
            ok = bool(result.get("ok", True))
            metrics = dict(result.get("metrics") or {})
            return ExecutionRecord(
                task_id=task.task_id,
                harness_version=harness.version,
                harness_fingerprint=harness.fingerprint,
                ok=ok,
                metrics=metrics,
                deliverable=dict(result.get("deliverable") or {}),
                trajectory=dict(result.get("trajectory") or {}),
                error=str(result.get("error", "")),
                duration_ms=float(result.get("duration_ms", 0.0) or 0.0),
            )
        return ExecutionRecord(
            task_id=task.task_id,
            harness_version=harness.version,
            harness_fingerprint=harness.fingerprint,
            ok=False,
            error=f"unsupported probe result: {type(result).__name__}",
        )


class NullProbeExecutor(ProbeExecutor):
    """Fail-closed default: no executor means no evidence and no adoption."""

    def execute(self, harness: HarnessState, task: HarnessTask, phase: str = "probe") -> ExecutionRecord:
        return ExecutionRecord(
            task_id=task.task_id,
            harness_version=harness.version,
            harness_fingerprint=harness.fingerprint,
            ok=False,
            error="no_probe_executor_configured",
        )


class HarnessEditor:
    """Protocol for proposing scoped harness edits."""

    def propose(
        self,
        task: HarnessTask,
        harness: HarnessState,
        scope: EditScope,
        history: Sequence[ExecutionRecord],
        library: Optional["HarnessImprovementLibrary"],
        budget: int = 1,
    ) -> List[HarnessEdit]:
        raise NotImplementedError


class CallableHarnessEditor(HarnessEditor):
    """Adapts an LLM/editor callable into the Propose stage."""

    def __init__(self, fn: Callable[..., List[HarnessEdit]]):
        self._fn = fn

    def propose(self, task, harness, scope, history, library, budget=1):
        try:
            result = self._fn(
                task=task, harness=harness, scope=scope,
                history=list(history), library=library, budget=budget,
            )
        except Exception as exc:
            logger.warning("harness editor failed: %s", exc)
            return []
        return [e for e in (result or []) if isinstance(e, HarnessEdit)]


_INVARIANT_TEMPLATES: Tuple[Tuple[HarnessComponent, str, str], ...] = (
    (
        HarnessComponent.TOOL_GUIDANCE,
        "## Preflight discipline\nBefore any order intent: verify symbol tradability, "
        "tick age < max, spread < cap, margin headroom > 2x required, and journal "
        "write success. On any failed probe, emit HOLD.",
        "A failed probe must stop the cycle instead of being narrated away.",
    ),
    (
        HarnessComponent.MEMORY,
        "## Evidence retrieval\nBefore proposing a setup, retrieve L1 atomic facts "
        "and the top-3 L2 scenario playbooks for the current regime; cite the "
        "retrieved ids. Never reuse a scenario whose review status is not ACTIVE.",
        "Uncited context is indistinguishable from hallucinated context.",
    ),
    (
        HarnessComponent.TOOL_GUIDANCE,
        "## Autonomous pulse and starvation prevention\nWhen the market is closed "
        "or the scanner yields no candidates, do not halt the cycle. Advance "
        "incubation, refresh causal nowcasts, and emit a bounded heartbeat with a "
        "sleep interval no longer than 10 seconds.",
        "Skip-analysis starvation starves the learning loop and observability.",
    ),
    (
        HarnessComponent.TOOL_GUIDANCE,
        "## Process-global MT5 session discipline\nRetain the process-global MT5 "
        "session; never shut it down inside a component finally block. On a "
        "transient failure use exponential backoff and verify terminal_info "
        "reachability; fail closed on unrecoverable authentication errors.",
        "Session destruction during multi-stage runs leaks IPC handles.",
    ),
    (
        HarnessComponent.SUBAGENTS,
        "## Deliberation quorum\nRequire the Meta-Reflector risk veto before any "
        "size increase. StatArb and MacroMomentum must both state invalidation "
        "conditions before execution.",
        "Independent invalidation reduces single-agent confirmation bias.",
    ),
    (
        HarnessComponent.SYSTEM_PROMPT,
        "## Discipline\nOne setup, one invalidation, one size. No revenge trades, "
        "no averaging down, no widening stops. If the deterministic validator "
        "rejects, stop.",
        "Discipline failures are harness failures, not model failures.",
    ),
)

_DOMAIN_TEMPLATES: Dict[str, Tuple[HarnessComponent, str, str]] = {
    "LOW_VOL_TREND": (
        HarnessComponent.MEMORY,
        "## Regime playbook - low-volatility trend\nFavour pullback continuation "
        "entries with ATR-normalised invalidation. Treat compressed spreads as a "
        "confirmation input, not a reason to oversize.",
    ),
    "HIGH_VOL_TREND": (
        HarnessComponent.TOOL_GUIDANCE,
        "## Regime playbook - high-volatility trend\nWiden the preflight spread "
        "cap checks, halve default size, and require a second confirmation at the "
        "session high/low before routing.",
    ),
    "LOW_VOL_RANGE": (
        HarnessComponent.SKILLS,
        "## Regime playbook - low-volatility range\nPrefer mean-reversion skills "
        "with OU half-life filtering and a time stop. Reject trend-continuation "
        "setups without a range-edge catalyst.",
    ),
    "HIGH_VOL_CRISIS": (
        HarnessComponent.SYSTEM_PROMPT,
        "## Crisis posture\nCapital preservation dominates. Trade only A+ "
        "liquidity, cut size to the defensive policy multiplier, and require the "
        "Meta-Reflector to confirm no alpha-decay or correlation-breakdown alert.",
    ),
}

_DEFAULT_DOMAIN_TEMPLATE = (
    HarnessComponent.SKILLS,
    "## Domain skill selection\nRoute the setup through the validated quant skill "
    "closest to the current regime, then attach the journal-audit evidence. "
    "Free-form execution is a last resort.",
    "Selecting a validated skill beats re-deriving the model under time pressure.",
)


class DeterministicHarnessEditor(HarnessEditor):
    """Evidence-driven, model-free editor.

    Reuses supported edits from the improvement library first, then falls back
    to invariant and regime templates. It cannot propose guardrail edits.
    """

    def propose(self, task, harness, scope, history, library, budget=1):
        proposals: List[HarnessEdit] = []
        seen: set = set()
        manifest = HarnessManifest.default()

        def _add(component: HarnessComponent, replace: str, rationale: str,
                 source: str = "template", metadata: Optional[Dict[str, Any]] = None) -> None:
            if len(proposals) >= max(1, int(budget)):
                return
            if not manifest.is_mutable(component):
                return
            if replace.strip() in harness.text(component):
                return
            tag = scan_edit_text(replace)
            if tag:
                return
            key = (component, replace.strip())
            if key in seen:
                return
            seen.add(key)
            digest = _stable_hash({"task": task.signature(), "component": component.value, "replace": replace})
            proposals.append(HarnessEdit(
                edit_id=f"e_{digest[:12]}",
                component=component,
                scope=scope,
                find="",
                replace=replace,
                rationale=rationale,
                source=source,
                metadata=metadata or {},
            ))

        if library is not None and history:
            for lesson in library.reusable_edits(task, scope, limit=max(1, int(budget) * 2)):
                if not lesson.edit:
                    continue
                edit = HarnessEdit.from_dict(lesson.edit)
                if edit.scope != scope:
                    continue
                _add(edit.component, edit.replace, edit.rationale,
                     source="library", metadata={"lesson_id": lesson.lesson_id,
                                                 "score_delta": lesson.score_delta})

        if scope is EditScope.INVARIANT:
            reference = history[-1] if history else None
            ordered = list(_INVARIANT_TEMPLATES)
            if reference is not None:
                if reference.metric("tool_errors") > 0:
                    ordered.sort(key=lambda t: t[0] is not HarnessComponent.TOOL_GUIDANCE)
                elif reference.metric("trades") <= 0:
                    ordered.sort(key=lambda t: t[0] is not HarnessComponent.MEMORY)
            for component, replace, rationale in ordered:
                _add(component, replace, rationale)
        else:
            template = _DOMAIN_TEMPLATES.get(task.regime.upper(), _DEFAULT_DOMAIN_TEMPLATE)
            _add(template[0], template[1], template[2],
                 metadata={"regime": task.regime.upper()})

        return proposals[: max(1, int(budget))]


@dataclass
class Probe:
    edit: HarnessEdit
    harness: HarnessState
    execution: ExecutionRecord
    contrast: BehavioralContrast

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edit": self.edit.to_dict(),
            "harness": self.harness.to_dict(),
            "execution": self.execution.to_dict(),
            "contrast": self.contrast.to_dict(),
        }


class HarnessComposer:
    """Protocol for composing a successor harness from probe evidence."""

    def compose(
        self,
        task: HarnessTask,
        base: HarnessState,
        probes: Sequence[Probe],
        session_index: int = 0,
    ) -> Tuple[HarnessState, List[str], List[str]]:
        raise NotImplementedError


class GreedyEvidenceComposer(HarnessComposer):
    """Selectively merges only edits supported by a behavioural contrast."""

    def compose(self, task, base, probes, session_index=0):
        supported = [p for p in probes if p.contrast.supported]
        supported.sort(key=lambda p: (-p.contrast.score_delta, p.edit.edit_id))
        if supported and session_index:
            shift = session_index % len(supported)
            supported = supported[shift:] + supported[:shift]

        composed = base
        decisions: List[str] = []
        rejections: List[str] = []
        for probe in supported:
            try:
                composed = composed.apply(probe.edit)
            except HarnessSafetyError as exc:
                rejections.append(f"{probe.edit.edit_id}: {exc}")
                continue
            decisions.append(
                f"adopted {probe.edit.component.value} ({probe.edit.scope.value}) "
                f"delta={probe.contrast.score_delta:+.4f}"
            )

        if not decisions:
            rejections.append("no_supported_edits")
        return composed, decisions, rejections


# ---------------------------------------------------------------------------
# Improvement library (harnessing-level recursion)
# ---------------------------------------------------------------------------

@dataclass
class Lesson:
    """Evidence-supported lesson about when and why a harness edit worked."""
    lesson_id: str
    component: str
    scope: str
    situation: str
    action: str
    evidence: Dict[str, Any]
    score_delta: float
    edit: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Lesson":
        return cls(
            lesson_id=str(data.get("lesson_id") or _stable_hash(data)[:16]),
            component=str(data.get("component", "")),
            scope=str(data.get("scope", EditScope.INVARIANT.value)),
            situation=str(data.get("situation", "")),
            action=str(data.get("action", "")),
            evidence=dict(data.get("evidence") or {}),
            score_delta=float(data.get("score_delta", 0.0) or 0.0),
            edit=dict(data.get("edit") or {}),
            created_at=str(data.get("created_at", _utc_now())),
        )


@dataclass
class LibraryEntry:
    """An adopted harness plus the lessons from the task that produced it."""
    entry_id: str
    task_id: str
    domain: str
    regime: str
    harness: Dict[str, Any]
    lessons: List[Lesson]
    score_delta: float
    evidence: Dict[str, Any]
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "domain": self.domain,
            "regime": self.regime,
            "harness": self.harness,
            "lessons": [l.to_dict() for l in self.lessons],
            "score_delta": self.score_delta,
            "evidence": self.evidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LibraryEntry":
        return cls(
            entry_id=str(data.get("entry_id") or _stable_hash(data)[:16]),
            task_id=str(data.get("task_id", "")),
            domain=str(data.get("domain", "forex")),
            regime=str(data.get("regime", "UNKNOWN")),
            harness=dict(data.get("harness") or {}),
            lessons=[Lesson.from_dict(l) for l in data.get("lessons", [])],
            score_delta=float(data.get("score_delta", 0.0) or 0.0),
            evidence=dict(data.get("evidence") or {}),
            created_at=str(data.get("created_at", _utc_now())),
        )


class HarnessImprovementLibrary(StoreBackedState):
    """Persistent, per-domain library of adopted harnesses and lessons."""
    store_key = "harness2_library_state"
    DEFAULT_FILE = "harness2_library.json"

    def __init__(self, state_file: str = DEFAULT_FILE, store: Optional[Any] = None,
                 max_entries: int = 200):
        self.state_file = state_file
        self.store = store
        self.max_entries = max(1, int(max_entries))
        self.entries: List[LibraryEntry] = []
        if store is not None:
            self.attach_store(store)
        self._load()

    def _load(self) -> None:
        data = self._load_state_dict()
        raw = data.get("entries", []) if isinstance(data, dict) else []
        loaded: List[LibraryEntry] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                loaded.append(LibraryEntry.from_dict(item))
            except Exception as exc:
                logger.debug("skipping malformed harness2 library entry: %s", exc)
        self.entries = loaded[-self.max_entries:]

    def _save(self) -> None:
        self._save_state_dict({
            "entries": [e.to_dict() for e in self.entries[-self.max_entries:]],
            "last_saved": time.time(),
        })

    def add(self, entry: LibraryEntry) -> bool:
        """Append an entry; duplicate (task, harness) pairs are ignored."""
        fingerprint = (entry.harness or {}).get("fingerprint") or _stable_hash(entry.harness)
        for existing in self.entries:
            existing_fp = (existing.harness or {}).get("fingerprint") or _stable_hash(existing.harness)
            if existing.task_id == entry.task_id and existing_fp == fingerprint:
                return False
        if not entry.entry_id:
            entry.entry_id = f"lib_{_stable_hash(entry.to_dict())[:16]}"
        self.entries.append(entry)
        self.entries = self.entries[-self.max_entries:]
        self._save()
        return True

    def _matches(self, entry: LibraryEntry, task: HarnessTask) -> bool:
        return (
            entry.task_id == task.task_id
            or entry.domain.lower() == task.domain.lower()
            or entry.regime.upper() == task.regime.upper()
        )

    def retrieve(self, task: HarnessTask, limit: int = 5) -> List[LibraryEntry]:
        """Return entries relevant to a task, best evidence first."""
        scored: List[Tuple[float, LibraryEntry]] = []
        for entry in self.entries:
            score = 0.0
            if entry.domain.lower() == task.domain.lower():
                score += 3.0
            if entry.regime.upper() == task.regime.upper():
                score += 2.0
            if entry.task_id == task.task_id:
                score += 1.0
            score += min(max(entry.score_delta, 0.0), 2.0)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        return [entry for _, entry in scored[: max(1, int(limit))]]

    def reusable_edits(self, task: HarnessTask, scope: EditScope,
                       limit: int = 5) -> List[Lesson]:
        """Return sanitized, component-mutable lessons applicable to a scope."""
        manifest = HarnessManifest.default()
        out: List[Lesson] = []
        seen: set = set()
        for entry in self.retrieve(task, limit=max(1, int(limit)) * 2):
            for lesson in entry.lessons:
                if lesson.scope != scope.value:
                    continue
                try:
                    component = HarnessComponent(lesson.component)
                except ValueError:
                    continue
                if not manifest.is_mutable(component):
                    continue
                if scan_edit_text(lesson.edit.get("replace", "")):
                    continue
                key = (lesson.component, lesson.edit.get("replace", ""))
                if key in seen:
                    continue
                seen.add(key)
                out.append(lesson)
                if len(out) >= max(1, int(limit)):
                    return out
        return out

    def lessons_for(self, domain: str = "", regime: str = "",
                    component: Optional[HarnessComponent] = None,
                    limit: int = 10) -> List[Lesson]:
        out: List[Lesson] = []
        for entry in reversed(self.entries):
            if domain and entry.domain.lower() != domain.lower():
                continue
            if regime and entry.regime.upper() != regime.upper():
                continue
            for lesson in entry.lessons:
                if component is not None and lesson.component != component.value:
                    continue
                out.append(lesson)
                if len(out) >= max(1, int(limit)):
                    return out
        return out

    def status(self) -> Dict[str, Any]:
        return {
            "entries": len(self.entries),
            "domains": sorted({e.domain for e in self.entries}),
            "regimes": sorted({e.regime for e in self.entries}),
            "last_entry": self.entries[-1].created_at if self.entries else None,
        }


class ExperienceReflector:
    """Extracts reusable lessons only when execution contrasts support them."""

    def reflect(self, task: HarnessTask, step: "StepRecord",
                policy: ContrastPolicy) -> Optional[LibraryEntry]:
        if not step.adopted:
            return None
        applied = set(step.applied_edit_ids)
        supported = [
            p for p in step.probes
            if p.contrast.supported and p.edit.edit_id in applied
        ]
        if not supported:
            return None

        lessons: List[Lesson] = []
        for probe in supported:
            situation = (
                f"domain={task.domain} regime={task.regime} "
                f"symbol={task.symbol} timeframe={task.timeframe}"
            )
            lessons.append(Lesson(
                lesson_id=f"les_{_stable_hash({'task': task.signature(), 'edit': probe.edit.to_dict()})[:16]}",
                component=probe.edit.component.value,
                scope=probe.edit.scope.value,
                situation=situation,
                action=probe.edit.rationale or probe.edit.replace[:240],
                evidence=probe.contrast.to_dict(),
                score_delta=probe.contrast.score_delta,
                edit=probe.edit.to_dict(),
            ))

        harness_dict = step.composed.to_dict()
        entry_id = f"lib_{_stable_hash({'task': task.signature(), 'harness': harness_dict.get('fingerprint')})[:16]}"
        return LibraryEntry(
            entry_id=entry_id,
            task_id=task.task_id,
            domain=task.domain,
            regime=task.regime,
            harness=harness_dict,
            lessons=lessons,
            score_delta=round(step.composed_execution.score - step.reference.score, 6)
            if step.composed_execution.ok and step.reference.ok else 0.0,
            evidence={
                "reference": step.reference.to_dict(),
                "composed": step.composed_execution.to_dict(),
                "decisions": list(step.decisions),
                "rejections": list(step.rejections),
                "min_gain": policy.min_gain,
            },
        )


# ---------------------------------------------------------------------------
# Contrastive step and two-level optimizer
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    """Full evidence trail for one Propose–Probe–Compose step."""
    step_index: int
    task_id: str
    base_version: str
    reference: ExecutionRecord
    proposals: List[HarnessEdit] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)
    composed: Optional[HarnessState] = None
    composed_execution: Optional[ExecutionRecord] = None
    adopted: bool = False
    decisions: List[str] = field(default_factory=list)
    rejections: List[str] = field(default_factory=list)
    applied_edit_ids: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "task_id": self.task_id,
            "base_version": self.base_version,
            "reference": self.reference.to_dict(),
            "proposals": [p.to_dict() for p in self.proposals],
            "probes": [p.to_dict() for p in self.probes],
            "composed": self.composed.to_dict() if self.composed else None,
            "composed_execution": self.composed_execution.to_dict() if self.composed_execution else None,
            "adopted": self.adopted,
            "decisions": list(self.decisions),
            "rejections": list(self.rejections),
            "applied_edit_ids": list(self.applied_edit_ids),
            "timestamp": self.timestamp,
        }


class ContrastiveStep:
    """Propose, probe, and compose a harness for one task."""

    def __init__(
        self,
        executor: ProbeExecutor,
        editor: Optional[HarnessEditor] = None,
        composer: Optional[HarnessComposer] = None,
        manifest: Optional[HarnessManifest] = None,
        policy: Optional[ContrastPolicy] = None,
        max_edits: int = 8,
    ):
        self.executor = executor
        self.editor = editor or DeterministicHarnessEditor()
        self.composer = composer or GreedyEvidenceComposer()
        self.manifest = manifest or HarnessManifest.default()
        self.policy = policy or ContrastPolicy()
        self.max_edits = max(1, int(max_edits))

    def _execute(self, harness: HarnessState, task: HarnessTask, phase: str) -> ExecutionRecord:
        started = time.time()
        record = self.executor.execute(harness, task, phase)
        if record.duration_ms <= 0:
            record.duration_ms = round((time.time() - started) * 1000.0, 2)
        if not record.task_id:
            record.task_id = task.task_id
        if not record.harness_version:
            record.harness_version = harness.version
        if not record.harness_fingerprint:
            record.harness_fingerprint = harness.fingerprint
        return record

    def reference(self, task: HarnessTask, harness: HarnessState) -> ExecutionRecord:
        return self._execute(harness, task, "reference")

    def validate(self, edits: Iterable[HarnessEdit], harness: HarnessState,
                 rejections: List[str]) -> List[HarnessEdit]:
        valid: List[HarnessEdit] = []
        seen: set = set()
        for edit in edits:
            reason = self.manifest.validate_edit(edit)
            if reason is None:
                try:
                    successor = harness.apply(edit)
                except HarnessSafetyError as exc:
                    reason = str(exc)
                else:
                    if successor.fingerprint == harness.fingerprint:
                        reason = "no_op_edit"
            if reason:
                rejections.append(f"{edit.edit_id}: {reason}")
                continue
            key = (edit.component, edit.find, edit.replace)
            if key in seen:
                rejections.append(f"{edit.edit_id}: duplicate_edit")
                continue
            seen.add(key)
            valid.append(edit)
            if len(valid) >= self.max_edits:
                break
        return valid

    def propose(self, task: HarnessTask, harness: HarnessState, scope: EditScope,
                history: Sequence[ExecutionRecord],
                library: Optional[HarnessImprovementLibrary],
                budget: int, rejections: List[str]) -> List[HarnessEdit]:
        try:
            edits = self.editor.propose(task, harness, scope, history, library, budget)
        except Exception as exc:
            rejections.append(f"editor_failed:{type(exc).__name__}:{exc}")
            return []
        return self.validate(edits, harness, rejections)

    def probe(self, task: HarnessTask, harness: HarnessState,
              edits: Sequence[HarnessEdit],
              reference: ExecutionRecord) -> List[Probe]:
        probes: List[Probe] = []
        for edit in edits:
            try:
                candidate = harness.apply(edit)
            except HarnessSafetyError as exc:
                logger.debug("edit rejected during probe: %s", exc)
                continue
            execution = self._execute(candidate, task, "candidate")
            contrast = compare_executions(reference, execution, self.policy, edit)
            probes.append(Probe(edit=edit, harness=candidate, execution=execution, contrast=contrast))
        return probes

    def compose(self, task: HarnessTask, harness: HarnessState, probes: Sequence[Probe],
                session_index: int = 0) -> Tuple[HarnessState, List[str], List[str]]:
        return self.composer.compose(task, harness, probes, session_index=session_index)

    def decide(
        self,
        reference: ExecutionRecord,
        composed: HarnessState,
        composed_execution: ExecutionRecord,
        base: HarnessState,
        policy: Optional[ContrastPolicy] = None,
    ) -> Tuple[bool, str]:
        policy = policy or self.policy
        if composed.fingerprint == base.fingerprint:
            return False, "preserved_current_harness"
        if not composed_execution.ok:
            return False, "composed_execution_failed"
        contrast = compare_executions(reference, composed_execution, policy, None)
        if not contrast.supported:
            return False, f"composed_not_supported:{contrast.reason}"
        return True, "adopted_supported_composition"

    def run(
        self,
        task: HarnessTask,
        harness: HarnessState,
        history: Optional[Sequence[ExecutionRecord]] = None,
        library: Optional[HarnessImprovementLibrary] = None,
        step_index: int = 0,
    ) -> StepRecord:
        history_list = list(history or [])
        reference = self.reference(task, harness)
        history_list.append(reference)

        rejections: List[str] = []
        invariant = self.propose(task, harness, EditScope.INVARIANT, history_list, library, 1, rejections)
        domain = self.propose(task, harness, EditScope.DOMAIN, history_list, library, 1, rejections)
        probes = self.probe(task, harness, invariant + domain, reference)

        composed, decisions, compose_rejections = self.compose(task, harness, probes, 0)
        rejections.extend(compose_rejections)
        composed_execution = (
            self._execute(composed, task, "composed")
            if composed.fingerprint != harness.fingerprint
            else reference
        )
        adopted, decision = self.decide(reference, composed, composed_execution, harness)
        decisions.append(decision)

        base_edit_ids = {e.edit_id for e in harness.edits}
        applied = [e.edit_id for e in composed.edits if e.edit_id not in base_edit_ids]
        if not adopted:
            applied = []

        return StepRecord(
            step_index=step_index,
            task_id=task.task_id,
            base_version=harness.version,
            reference=reference,
            proposals=invariant + domain,
            probes=probes,
            composed=composed if adopted else harness,
            composed_execution=composed_execution if adopted else reference,
            adopted=adopted,
            decisions=decisions,
            rejections=rejections,
            applied_edit_ids=applied,
        )


@dataclass
class OptimizationResult:
    """Outcome of running task-level recursion for one open-world task."""
    task_id: str
    mode: str
    k: int
    adopted_harness: HarnessState
    reference_execution: ExecutionRecord
    adopted_execution: ExecutionRecord
    steps: List[StepRecord] = field(default_factory=list)
    library_entry_ids: List[str] = field(default_factory=list)
    gain: float = 0.0
    timestamp: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "k": self.k,
            "adopted_harness": self.adopted_harness.to_dict(),
            "reference_execution": self.reference_execution.to_dict(),
            "adopted_execution": self.adopted_execution.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "library_entry_ids": list(self.library_entry_ids),
            "gain": self.gain,
            "timestamp": self.timestamp,
        }


class Harness2Optimizer:
    """Two-level recursive harness optimiser with frozen model weights.

    Task-level recursion refines an in-memory harness for the current trading
    task. Harnessing-level recursion updates ``HarnessImprovementLibrary`` with
    only evidence-supported lessons. Guardrails are never edited.
    """

    def __init__(
        self,
        executor: ProbeExecutor,
        editor: Optional[HarnessEditor] = None,
        composer: Optional[HarnessComposer] = None,
        library: Optional[HarnessImprovementLibrary] = None,
        reflector: Optional[ExperienceReflector] = None,
        manifest: Optional[HarnessManifest] = None,
        policy: Optional[ContrastPolicy] = None,
        base_harness: Optional[HarnessState] = None,
    ):
        self.executor = executor
        self.manifest = manifest or HarnessManifest.default()
        self.policy = policy or ContrastPolicy()
        self.library = library or HarnessImprovementLibrary()
        self.reflector = reflector or ExperienceReflector()
        self.step = ContrastiveStep(
            executor=executor,
            editor=editor,
            composer=composer,
            manifest=self.manifest,
            policy=self.policy,
        )
        self.base_harness = base_harness or HarnessState.base()

    def _reflect(self, task: HarnessTask, step: StepRecord) -> Optional[str]:
        entry = self.reflector.reflect(task, step, self.policy)
        if entry is None:
            return None
        self.library.add(entry)
        return entry.entry_id

    def _parallel(
        self,
        task: HarnessTask,
        base: HarnessState,
        k: int,
    ) -> Tuple[HarnessState, ExecutionRecord, ExecutionRecord, List[StepRecord]]:
        step = self.step
        references = [step.reference(task, base) for _ in range(k)]
        reference = next((r for r in references if r.ok), references[0])

        rejections: List[str] = []
        invariant = step.propose(task, base, EditScope.INVARIANT, references, self.library, k, rejections)
        domain = step.propose(task, base, EditScope.DOMAIN, references, self.library, k, rejections)
        unique: List[HarnessEdit] = []
        seen: set = set()
        for edit in invariant + domain:
            key = (edit.component, edit.find, edit.replace)
            if key in seen:
                continue
            seen.add(key)
            unique.append(edit)

        probes = step.probe(task, base, unique[: max(k * 2, 1)], reference)
        if not probes:
            return base, reference, reference, [StepRecord(
                step_index=0, task_id=task.task_id, base_version=base.version,
                reference=reference, rejections=rejections,
                decisions=["preserved_current_harness"],
                composed=base, composed_execution=reference,
            )]

        best: Optional[Tuple[float, HarnessState, ExecutionRecord, List[str], List[str]]] = None
        for session in range(k):
            composed, decisions, compose_rejections = step.compose(task, base, probes, session)
            if composed.fingerprint == base.fingerprint:
                continue
            execution = step._execute(composed, task, f"composed_{session}")
            adopted, decision = step.decide(reference, composed, execution, base)
            decisions.append(decision)
            if not adopted:
                continue
            if best is None or execution.score > best[0]:
                best = (execution.score, composed, execution, decisions, compose_rejections)

        if best is None:
            return base, reference, reference, [StepRecord(
                step_index=0, task_id=task.task_id, base_version=base.version,
                reference=reference, proposals=unique, probes=probes,
                composed=base, composed_execution=reference,
                decisions=["preserved_current_harness"], rejections=rejections,
            )]

        _, composed, execution, decisions, compose_rejections = best
        base_edit_ids = {e.edit_id for e in base.edits}
        applied = [e.edit_id for e in composed.edits if e.edit_id not in base_edit_ids]
        record = StepRecord(
            step_index=0,
            task_id=task.task_id,
            base_version=base.version,
            reference=reference,
            proposals=unique,
            probes=probes,
            composed=composed,
            composed_execution=execution,
            adopted=True,
            decisions=decisions,
            rejections=rejections + compose_rejections,
            applied_edit_ids=applied,
        )
        return composed, reference, execution, [record]

    def _sequential(
        self,
        task: HarnessTask,
        base: HarnessState,
        k: int,
    ) -> Tuple[HarnessState, ExecutionRecord, ExecutionRecord, List[StepRecord]]:
        current = base
        reference: Optional[ExecutionRecord] = None
        history: List[ExecutionRecord] = []
        steps: List[StepRecord] = []
        for index in range(k):
            record = self.step.run(task, current, history=history, library=self.library,
                                   step_index=index)
            steps.append(record)
            if reference is None:
                reference = record.reference
            if record.adopted:
                current = record.composed
                assert record.composed_execution is not None
                history.append(record.composed_execution)
            else:
                history.append(record.reference)
        final = steps[-1]
        adopted_execution = final.composed_execution if final.adopted else final.reference
        return current, reference or final.reference, adopted_execution, steps

    def optimize_task(
        self,
        task: HarnessTask,
        k: int = 1,
        mode: str = "parallel",
        base_harness: Optional[HarnessState] = None,
    ) -> OptimizationResult:
        """Run task-level recursion and then update the improvement harness."""
        k = max(1, int(k))
        mode = (mode or "parallel").lower()
        if mode not in ("parallel", "sequential"):
            raise ValueError(f"unknown recursion mode: {mode}")
        base = base_harness or self.base_harness

        if mode == "parallel" and k > 1:
            adopted, reference, executed, steps = self._parallel(task, base, k)
        elif mode == "parallel":
            record = self.step.run(task, base, library=self.library, step_index=0)
            steps = [record]
            reference = record.reference
            if record.adopted:
                adopted = record.composed or base
                executed = record.composed_execution or reference
            else:
                adopted, executed = base, reference
        else:
            adopted, reference, executed, steps = self._sequential(task, base, k)

        entry_ids = [eid for eid in (self._reflect(task, s) for s in steps) if eid]
        gain = (
            round(executed.score - reference.score, 6)
            if executed.ok and reference.ok else 0.0
        )
        logger.info(
            "Harness2 %s k=%d task=%s adopted=%s gain=%.4f library=%d",
            mode, k, task.task_id,
            adopted.fingerprint != base.fingerprint, gain, len(self.library.entries),
        )
        return OptimizationResult(
            task_id=task.task_id,
            mode=mode,
            k=k,
            adopted_harness=adopted,
            reference_execution=reference,
            adopted_execution=executed,
            steps=steps,
            library_entry_ids=entry_ids,
            gain=gain,
        )

    def run_stream(
        self,
        tasks: Sequence[HarnessTask],
        k: int = 1,
        mode: str = "parallel",
    ) -> List[OptimizationResult]:
        """Process an open-world task stream; each task starts from the base harness.

        The improvement library persists across tasks, so later proposals can
        reuse earlier evidence without ever inheriting a task-specific harness.
        """
        return [self.optimize_task(task, k=k, mode=mode) for task in tasks]


class Harness2Plugin(HarnessPlugin):
    """Mount the optimiser services into a ``TradingContext``."""

    name = "harness2_optimizer"
    version = "1.0.0"
    dependencies: List[str] = []

    def __init__(
        self,
        executor: Optional[ProbeExecutor] = None,
        editor: Optional[HarnessEditor] = None,
        library: Optional[HarnessImprovementLibrary] = None,
        state_file: str = HarnessImprovementLibrary.DEFAULT_FILE,
    ):
        self.executor = executor or NullProbeExecutor()
        self.editor = editor
        self.library = library
        self.state_file = state_file

    def apply(self, ctx: TradingContext) -> None:
        store = None
        try:
            store = ctx.get("store", None) or ctx.get("trading_store", None)
        except Exception:
            store = None
        library = self.library or HarnessImprovementLibrary(state_file=self.state_file, store=store)
        optimizer = Harness2Optimizer(
            executor=self.executor,
            editor=self.editor,
            library=library,
        )
        ctx.provide("harness2_library", library)
        ctx.provide("harness2_optimizer", optimizer)
        logger.info("Harness2 plugin mounted (library entries=%d)", len(library.entries))


__all__ = [
    "HarnessComponent",
    "EditScope",
    "HarnessSafetyError",
    "FrozenComponentError",
    "InvalidEditError",
    "ComponentSpec",
    "HarnessManifest",
    "default_component_specs",
    "DEFAULT_COMPONENTS",
    "HarnessEdit",
    "HarnessState",
    "HarnessTask",
    "ContrastPolicy",
    "ExecutionRecord",
    "BehavioralContrast",
    "compare_executions",
    "compute_execution_score",
    "ProbeExecutor",
    "CallableProbeExecutor",
    "NullProbeExecutor",
    "HarnessEditor",
    "CallableHarnessEditor",
    "DeterministicHarnessEditor",
    "Probe",
    "HarnessComposer",
    "GreedyEvidenceComposer",
    "Lesson",
    "LibraryEntry",
    "HarnessImprovementLibrary",
    "ExperienceReflector",
    "StepRecord",
    "ContrastiveStep",
    "OptimizationResult",
    "Harness2Optimizer",
    "Harness2Plugin",
    "scan_edit_text",
    "FORBIDDEN_EDIT_PATTERNS",
]
