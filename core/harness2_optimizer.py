"""core/harness2_optimizer.py

Harness²: Recursive Agent Harnessing for an Open World
Based on Google Research (google-research/harness2).

Optimizes the agent's execution harness without altering underlying LLM model weights.
Implements the 8 editable harness components, Two-Level Recursion (Task-Level &
Harnessing-Level), and the Propose–Probe–Compose (PPC) contrastive workflow.

Ponytail compliant: pure standard library (dataclasses, typing, json, pathlib, time, copy).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("core.harness2_optimizer")


class HarnessComponent(str, Enum):
    """The 8 editable harness components exposed by Harness²."""
    SYSTEM_PROMPT = "system_prompt"    # Context: Trading persona, discipline, risk mindset
    GUARDRAILS = "guardrails"          # Context: Fail-closed risk gates (FROZEN invariant)
    MEMORY = "memory"                  # Context: L0-L4 multi-tier retrieval, context packing
    TOOL_GUIDANCE = "tool_guidance"    # Interface: Preflight discipline, order tools guidance
    SUBAGENTS = "subagents"            # Orchestration: Deliberation, Meta-Reflector, TechnicalNet
    SKILLS = "skills"                  # Context: Quant models, backtest harness, feature extractors
    SCRIPTS = "scripts"                # Interface: Preflight probes, data fetchers, audit scripts
    PLUGINS = "plugins"                # Middleware: MCP server bridges, telemetry, Prometheus


class ComponentRole(str, Enum):
    """Functional role categories for harness components."""
    CONTEXT = "context"
    INTERFACE = "interface"
    ORCHESTRATION = "orchestration"
    MIDDLEWARE = "middleware"


# Component -> Role mapping
COMPONENT_ROLES: Dict[HarnessComponent, ComponentRole] = {
    HarnessComponent.SYSTEM_PROMPT: ComponentRole.CONTEXT,
    HarnessComponent.GUARDRAILS: ComponentRole.CONTEXT,
    HarnessComponent.MEMORY: ComponentRole.CONTEXT,
    HarnessComponent.TOOL_GUIDANCE: ComponentRole.INTERFACE,
    HarnessComponent.SUBAGENTS: ComponentRole.ORCHESTRATION,
    HarnessComponent.SKILLS: ComponentRole.CONTEXT,
    HarnessComponent.SCRIPTS: ComponentRole.INTERFACE,
    HarnessComponent.PLUGINS: ComponentRole.MIDDLEWARE,
}

# Immutable guardrail patterns that must NEVER be relaxed by automated edits
CRITICAL_GUARDRAIL_INVARIANTS = [
    "fail-closed",
    "risk_per_trade",
    "exposure_buckets",
    "margin_headroom",
    "hard_stop",
    "emergency_exit",
]


class HarnessSecurityError(Exception):
    """Raised when an edit attempts to modify or relax frozen guardrails."""
    pass


class InvalidHarnessEditError(Exception):
    """Raised when an edit is malformed, ambiguous, or violates schema."""
    pass


@dataclass
class HarnessEdit:
    """An atomic edit proposed for a harness component."""
    component: HarnessComponent
    find: str = ""                       # Anchor text to replace (empty string = append)
    replace: str = ""                    # New content to substitute or append
    rationale: str = ""                  # Explanation for the modification
    expected_gain: str = ""              # Expected impact (e.g. 'lower latency', 'prevent starvation')
    regime: str = "all"                  # Target market regime (e.g. 'high_volatility', 'ranging', 'all')
    author: str = "harness2_proposer"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["component"] = self.component.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HarnessEdit:
        data = copy.deepcopy(data)
        data["component"] = HarnessComponent(data["component"])
        return cls(**data)


@dataclass
class ProbeMetric:
    """Quantitative behavioral evaluation of a probed harness candidate."""
    candidate_id: str
    pass_invariants: bool
    risk_score: float                   # Lower is safer (0.0 to 1.0)
    deliberation_quality: float         # Higher is better (0.0 to 1.0)
    latency_seconds: float
    starvation_risk: float              # Lower is safer (0.0 to 1.0)
    decision_coherence: float           # Consistency across re-prompts (0.0 to 1.0)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def composite_utility(self) -> float:
        """Composite score for ranking probed candidates."""
        if not self.pass_invariants:
            return -1.0
        # Utility weights: high quality + coherence, penalized by risk, latency, and starvation
        score = (
            0.40 * self.deliberation_quality +
            0.30 * self.decision_coherence -
            0.20 * self.risk_score -
            0.10 * min(1.0, self.latency_seconds / 10.0) -
            0.20 * self.starvation_risk
        )
        return max(0.0, score)


@dataclass
class HarnessState:
    """Represents the complete configuration snapshot of all 8 harness components."""
    version: int = 1
    components: Dict[str, str] = field(default_factory=dict)
    active_regime: str = "normal"
    last_modified: float = field(default_factory=time.time)
    provenance: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Ensure all 8 components are initialized with defaults if missing
        defaults = {
            HarnessComponent.SYSTEM_PROMPT.value: (
                "You are an elite quantitative trading agent operating under fail-closed discipline.\n"
                "You prioritize capital preservation over return. Every proposed action must be backed by causal evidence."
            ),
            HarnessComponent.GUARDRAILS.value: (
                "## HARD RISK INVARIANTS (IMMUTABLE)\n"
                "- Fail-closed: missing or stale market data immediately halts order entry.\n"
                "- Max portfolio heat and risk per trade strictly enforced.\n"
                "- Margin headroom > 2.0x required before any pending order intent.\n"
                "- Emergency exit and hard stop loss attached to 100% of open positions."
            ),
            HarnessComponent.MEMORY.value: (
                "## L0-L4 Memory Hierarchy\n"
                "- L0 Working Memory: Current cycle tick, bar, active order intents.\n"
                "- L1 Short-Term: Last 20 closed cycles, R-multiples, volatility regime.\n"
                "- L2 Episodic: Past drawdown recoveries, regime shifts.\n"
                "- L3 Knowledge Base: Causal DAG nodes, macro narratives.\n"
                "- L4 Metacognitive: Past deliberation reflections and error audits."
            ),
            HarnessComponent.TOOL_GUIDANCE.value: (
                "## Tool Execution Discipline\n"
                "1. Always check symbol tradability and tick age before calling order_check.\n"
                "2. Check spread against threshold; if spread > max_spread, emit HOLD.\n"
                "3. Validate quote freshness: reject ticks older than 3 seconds in live execution."
            ),
            HarnessComponent.SUBAGENTS.value: (
                "TechnicalNet: Microstructure & StatArb signals.\n"
                "MacroMomentum: Causal BFS event overlays.\n"
                "DeliberationGraph: Multi-agent debate & consensus.\n"
                "MetaReflector: Post-trade accountability & hallucination detection."
            ),
            HarnessComponent.SKILLS.value: (
                "QuantSkills: CPCV backtesting, Kalman filtering, Avellaneda-Stoikov market making."
            ),
            HarnessComponent.SCRIPTS.value: (
                "PreflightProbes: QKF smoke test, context window check, MT5 bridge probe."
            ),
            HarnessComponent.PLUGINS.value: (
                "TelemetryPlugins: MT5 MCP bridge, Kronos MCP, Prometheus exporter, SQLite journal."
            ),
        }
        for k, v in defaults.items():
            if k not in self.components:
                self.components[k] = v

    def get(self, comp: HarnessComponent) -> str:
        return self.components.get(comp.value, "")

    def clone(self) -> HarnessState:
        return HarnessState(
            version=self.version,
            components=copy.deepcopy(self.components),
            active_regime=self.active_regime,
            last_modified=self.last_modified,
            provenance=list(self.provenance)
        )

    def apply_edit(self, edit: HarnessEdit) -> HarnessState:
        """Applies an atomic edit, strictly enforcing guardrail immutability."""
        if edit.component == HarnessComponent.GUARDRAILS:
            raise HarnessSecurityError(
                "Security violation: GUARDRAILS component is FROZEN and immutable. Automated edits denied."
            )

        comp_key = edit.component.value
        current_text = self.components.get(comp_key, "")

        # Check for forbidden degradation attempts in any component
        lower_replace = edit.replace.lower()
        if "disable_risk" in lower_replace or "bypass_guardrail" in lower_replace or "skip_validation" in lower_replace:
            raise HarnessSecurityError(
                f"Security violation: edit contains forbidden token attempting to bypass safety: '{edit.replace[:40]}'"
            )

        new_components = copy.deepcopy(self.components)
        if not edit.find:
            # Append mode
            if edit.replace.strip() in current_text:
                raise InvalidHarnessEditError("Edit content already present in component.")
            new_text = current_text.rstrip() + "\n\n" + edit.replace.strip()
        else:
            # Replacement mode
            if edit.find not in current_text:
                raise InvalidHarnessEditError(f"Anchor text '{edit.find[:40]}' not found in component {comp_key}.")
            if current_text.count(edit.find) > 1:
                raise InvalidHarnessEditError(f"Anchor text '{edit.find[:40]}' is ambiguous (occurs multiple times).")
            new_text = current_text.replace(edit.find, edit.replace, 1)

        new_components[comp_key] = new_text
        return HarnessState(
            version=self.version + 1,
            components=new_components,
            active_regime=self.active_regime,
            last_modified=time.time(),
            provenance=self.provenance + [f"edit_{edit.component.value}_{edit.author}_{int(time.time())}"]
        )


class HarnessLibrary:
    """Continual Improvement Library: Persists winning edits, probe metrics, and verified harness templates."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path("data/state/harness2_library.json")
        self.library: Dict[str, Any] = {
            "version": 1,
            "proven_edits": [],
            "regime_profiles": {},
            "history": []
        }
        self.load()

    def load(self) -> None:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.library = json.load(f)
            except Exception as e:
                logger.warning("Could not read harness library at %s: %s", self.storage_path, e)

    def save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.library, f, indent=2)
        except Exception as e:
            logger.error("Failed to save harness library to %s: %s", self.storage_path, e)

    def record_proven_edit(self, edit: HarnessEdit, metric: ProbeMetric) -> None:
        record = {
            "edit": edit.to_dict(),
            "composite_utility": metric.composite_utility,
            "deliberation_quality": metric.deliberation_quality,
            "decision_coherence": metric.decision_coherence,
            "latency_seconds": metric.latency_seconds,
            "timestamp": time.time()
        }
        self.library["proven_edits"].append(record)
        self.save()

    def get_proven_edits(self, component: Optional[HarnessComponent] = None) -> List[Dict[str, Any]]:
        edits = self.library.get("proven_edits", [])
        if component:
            return [e for e in edits if e["edit"]["component"] == component.value]
        return edits


class Harness2Optimizer:
    """The recursive Harness² optimization engine.
    
    Orchestrates:
    1. Propose: Candidate edit synthesis.
    2. Probe: Contrastive simulation without labels.
    3. Compose: Recursive harness assembly & safe deployment.
    """

    def __init__(
        self,
        base_state: Optional[HarnessState] = None,
        library: Optional[HarnessLibrary] = None,
        probe_evaluator: Optional[Callable[[HarnessState, str], ProbeMetric]] = None,
    ):
        self.state = base_state or HarnessState()
        self.library = library or HarnessLibrary()
        self.probe_evaluator = probe_evaluator or self._default_mock_probe

    def propose_candidates(self, bottleneck: str, regime: str = "normal") -> List[HarnessEdit]:
        """Proposes structured harness edit candidates addressing the given bottleneck."""
        candidates: List[HarnessEdit] = []

        # 1. Starvation / Inactivity Bottleneck
        if "starvation" in bottleneck.lower() or "skip_analysis" in bottleneck.lower():
            candidates.append(HarnessEdit(
                component=HarnessComponent.TOOL_GUIDANCE,
                find="",
                replace=(
                    "## Autonomous Pulse & Starvation Prevention\n"
                    "- When market is closed or scanner yields no volatile candidates, do not halt the cycle.\n"
                    "- Advance background incubation, update causal graph nowcasts, and refresh metric textfiles.\n"
                    "- Set bounded sleep interval (max 10s) to keep Prometheus and supervisor heartbeats fresh."
                ),
                rationale="Eliminate skip_analysis cycle starvation by maintaining continuous incubation pulses.",
                expected_gain="heartbeat_liveness_and_continuous_learning",
                regime=regime
            ))

        # 2. MT5 Session / IPC Resilience Bottleneck
        if "ipc" in bottleneck.lower() or "mt5" in bottleneck.lower() or "connection" in bottleneck.lower():
            candidates.append(HarnessEdit(
                component=HarnessComponent.TOOL_GUIDANCE,
                find="",
                replace=(
                    "## Process-Global MT5 Session Discipline\n"
                    "- Retain process-global MT5 session attached; never call shutdown() inside component finally blocks.\n"
                    "- On transient failure, execute exponential backoff retry ladder with terminal_info reachability verification.\n"
                    "- Fail closed immediately on unrecoverable authentication errors."
                ),
                rationale="Prevent IPC connection drops and session destruction during multi-stage backtest runs.",
                expected_gain="zero_ipc_leaks_and_clean_session_sharing",
                regime=regime
            ))

        # 3. Deliberation Coherence / Hallucination Bottleneck
        if "deliberation" in bottleneck.lower() or "coherence" in bottleneck.lower() or "hallucination" in bottleneck.lower():
            candidates.append(HarnessEdit(
                component=HarnessComponent.SUBAGENTS,
                find="",
                replace=(
                    "## Deliberation Invariant: Meta-Reflector Cross-Audit\n"
                    "- Every trade proposal generated by TechnicalNet or MacroMomentum must pass through Meta-Reflector.\n"
                    "- Check for premise drift, regime mismatch, and risk asymmetry before submitting to validation arbiter.\n"
                    "- If consensus confidence < 0.65, emit deliberate NO_TRADE intent."
                ),
                rationale="Ensure multi-agent debate converges on verified causal evidence before placing orders.",
                expected_gain="higher_deliberation_coherence_lower_tail_risk",
                regime=regime
            ))

        # 4. Memory Retrieval Bottleneck
        if "memory" in bottleneck.lower() or "context" in bottleneck.lower():
            candidates.append(HarnessEdit(
                component=HarnessComponent.MEMORY,
                find="",
                replace=(
                    "## Context-Packing Discipline\n"
                    "- Prioritize high-significance causal links (BFS rank > 0.70) in L3 Knowledge Base injection.\n"
                    "- Compact R-multiple evidence chains: summarize trades beyond the 20-trade window into rolling CVaR metrics."
                ),
                rationale="Optimize token context window and eliminate redundant memory clutter.",
                expected_gain="reduced_latency_and_sharp_focus",
                regime=regime
            ))

        return candidates

    def probe_candidate(self, candidate_state: HarnessState, task_context: str) -> ProbeMetric:
        """Probes a candidate harness state contrastively without ground-truth labels."""
        return self.probe_evaluator(candidate_state, task_context)

    def compose(
        self,
        bottleneck: str,
        task_context: str = "live_paper_trading",
        regime: str = "normal",
        min_utility_threshold: float = 0.50
    ) -> Tuple[HarnessState, List[HarnessEdit]]:
        """Executes the full Propose–Probe–Compose (PPC) cycle.
        
        Returns:
            (updated_harness_state, list_of_accepted_edits)
        """
        candidates = self.propose_candidates(bottleneck, regime=regime)
        accepted_edits: List[HarnessEdit] = []
        current_state = self.state

        for edit in candidates:
            # 1. Apply candidate edit
            try:
                provisional_state = current_state.apply_edit(edit)
            except (HarnessSecurityError, InvalidHarnessEditError) as e:
                logger.warning("Rejected edit during apply: %s", e)
                continue

            # 2. Probe candidate
            metric = self.probe_candidate(provisional_state, task_context)
            logger.info(
                "Probed edit on %s: utility=%.3f, pass_invariants=%s",
                edit.component.value, metric.composite_utility, metric.pass_invariants
            )

            # 3. Compose decision: Accept if invariants hold and utility exceeds threshold
            if metric.pass_invariants and metric.composite_utility >= min_utility_threshold:
                current_state = provisional_state
                accepted_edits.append(edit)
                # Save to continual improvement library
                self.library.record_proven_edit(edit, metric)

        self.state = current_state
        return self.state, accepted_edits

    def _default_mock_probe(self, candidate_state: HarnessState, task_context: str) -> ProbeMetric:
        """Deterministic default probe evaluating invariants, structure, and coherence."""
        # 1. Check frozen guardrails are 100% intact
        guardrails_text = candidate_state.get(HarnessComponent.GUARDRAILS)
        has_invariants = all(inv in guardrails_text.lower() for inv in [
            "fail-closed", "risk per trade", "margin headroom", "emergency exit"
        ])

        # 2. Check for token length / bloat (Ponytail penalty if excessively long)
        total_len = sum(len(v) for v in candidate_state.components.values())
        latency_est = 0.5 + (total_len / 5000.0)

        # 3. Check coherence & starvation
        tool_guidance = candidate_state.get(HarnessComponent.TOOL_GUIDANCE)
        starvation_risk = 0.1 if "starvation" in tool_guidance.lower() or "pulse" in tool_guidance.lower() else 0.4
        deliberation_q = 0.85 if "meta-reflector" in candidate_state.get(HarnessComponent.SUBAGENTS).lower() else 0.70
        coherence = 0.80

        return ProbeMetric(
            candidate_id=f"cand_v{candidate_state.version}",
            pass_invariants=has_invariants,
            risk_score=0.15,
            deliberation_quality=deliberation_q,
            latency_seconds=latency_est,
            starvation_risk=starvation_risk,
            decision_coherence=coherence
        )


def create_default_harness_optimizer() -> Harness2Optimizer:
    """Convenience factory creating a ready-to-use Harness² optimizer."""
    return Harness2Optimizer()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    optimizer = create_default_harness_optimizer()
    print("Initial Harness Version:", optimizer.state.version)
    updated_state, edits = optimizer.compose(bottleneck="skip_analysis starvation and IPC leaks")
    print(f"Accepted {len(edits)} edits:")
    for e in edits:
        print(f"  + [{e.component.value}] {e.rationale}")
    print("Updated Harness Version:", updated_state.version)
