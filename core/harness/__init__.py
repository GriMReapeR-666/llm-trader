"""Trading Harness Microkernel Package.

Inspired by DeepSeek Harness (dsh) & Cordis meta-framework:
- Spatiotemporal Composability (Context DI + Reversible Disposables)
- Append-Only Event Stream & Event Sourcing
- Speculative Counterfactual Hypothesis Branching & Replay
- Capability-Based Security Sandboxing
- Multi-Paradigm Quantitative & Algorithmic Trading Workflows
- Macroeconomic Event-Driven Alpha & Surprise Transmission Engine
"""
from __future__ import annotations

from core.harness.disposable import Disposable, CompositeDisposable
from core.harness.kernel import TradingContext, HarnessPlugin
from core.harness.event_stream import EventStream, HarnessEvent
from core.harness.session_fork import SessionForkEngine, Hypothesis, BranchResult
from core.harness.sandbox import CapabilitySandbox, CapabilityToken, PermissionLevel
from core.harness.workflows import (
    BaseTradingWorkflow,
    WorkflowStepTelemetry,
    WorkflowExecutionResult,
    StatisticalArbitrageWorkflow,
    MeanReversionWorkflow,
    MacroCausalMomentumWorkflow,
    MicrostructureBreakoutWorkflow,
    NeuralFoundationWorkflow,
    MacroEventAlphaWorkflow,
    QuantWorkflowEngine,
)

__all__ = [
    "Disposable",
    "CompositeDisposable",
    "TradingContext",
    "HarnessPlugin",
    "EventStream",
    "HarnessEvent",
    "SessionForkEngine",
    "Hypothesis",
    "BranchResult",
    "CapabilitySandbox",
    "CapabilityToken",
    "PermissionLevel",
    "BaseTradingWorkflow",
    "WorkflowStepTelemetry",
    "WorkflowExecutionResult",
    "StatisticalArbitrageWorkflow",
    "MeanReversionWorkflow",
    "MacroCausalMomentumWorkflow",
    "MicrostructureBreakoutWorkflow",
    "NeuralFoundationWorkflow",
    "MacroEventAlphaWorkflow",
    "QuantWorkflowEngine",
]
