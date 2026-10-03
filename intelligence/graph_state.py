"""Typed state definitions, models, and reducers for Agentic Graph workflows.

Strict fail-closed serialization, immutability guarantees, and channel reducers
for LangGraph and durable event-driven execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import operator
from typing import Annotated, Any, Dict, List, Optional, Tuple, TypedDict, Union
from pydantic import BaseModel, Field, field_validator


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeProposalPayload(BaseModel):
    """Deterministic Pydantic validation model for LLM trade hypotheses."""
    symbol: str
    direction: str = Field(..., description="BUY, SELL, or HOLD")
    entry_price: float = Field(default=0.0, ge=0.0)
    stop_loss: float = Field(default=0.0, ge=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    timeframe: str = Field(default="M15")
    setup_name: str = Field(default="UNKNOWN")
    rationale: str = Field(default="")
    risk_fraction: float = Field(default=0.01, ge=0.0, le=0.05)
    idempotency_key: Optional[str] = None
    created_at: str = Field(default_factory=utcnow_iso)

    @field_validator("direction")
    @classmethod
    def normalize_direction(cls, v: str) -> str:
        clean = str(v).strip().upper()
        if clean in ("BUY", "LONG", "B"):
            return "BUY"
        if clean in ("SELL", "SHORT", "S"):
            return "SELL"
        return "HOLD"


class OrderIntentPayload(BaseModel):
    """Immutable order intent ready for broker gateway dispatch."""
    intent_id: str
    symbol: str
    action: str  # "BUY", "SELL"
    order_type: str  # "MARKET", "LIMIT", "STOP"
    volume: float = Field(..., gt=0.0)
    price: float = Field(..., ge=0.0)
    stop_loss: float = Field(..., gt=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)
    magic_number: int = 123456
    comment: str = "LangGraph-Trade"
    risk_usd: float = Field(default=0.0, ge=0.0)
    risk_percent: float = Field(default=0.01, ge=0.0)
    valid_until_utc: Optional[str] = None
    created_at: str = Field(default_factory=utcnow_iso)


class ExecutionReceiptPayload(BaseModel):
    """Broker execution response receipt."""
    ticket: int
    symbol: str
    action: str
    volume: float
    open_price: float
    sl: float
    tp: float
    retcode: int = 10009
    broker_comment: str = "DONE"
    executed_at: str = Field(default_factory=utcnow_iso)
    is_paper: bool = True


def merge_audit_logs(left: Optional[List[str]], right: Optional[Union[List[str], str]]) -> List[str]:
    """Reducer that appends audit trail events safely."""
    res = list(left) if left else []
    if right is None:
        return res
    if isinstance(right, str):
        res.append(right)
    elif isinstance(right, (list, tuple)):
        res.extend(str(item) for item in right)
    return res


def merge_rejection_reasons(left: Optional[List[str]], right: Optional[Union[List[str], str]]) -> List[str]:
    """Reducer that accumulates rejection reasons without duplication."""
    res = list(left) if left else []
    if right is None:
        return res
    items = [right] if isinstance(right, str) else list(right)
    for item in items:
        clean = str(item).strip()
        if clean and clean not in res:
            res.append(clean)
    return res


def merge_hypotheses(left: Optional[List[Dict[str, Any]]], right: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Reducer that collects trade hypotheses across multiple LLM / agent nodes."""
    res = list(left) if left else []
    if right is None:
        return res
    if isinstance(right, dict):
        res.append(right)
    elif isinstance(right, (list, tuple)):
        for item in right:
            if isinstance(item, dict):
                res.append(item)
    return res


class TradingCycleState(TypedDict):
    """Complete Typed State schema for the Deliberative Trading Graph.
    
    All fields are JSON-serializable primitives for fail-closed checkpointing.
    """
    # Session Context & Identity
    session_id: str
    cycle_id: str
    symbol: str
    timeframe: str
    strategy: str
    paper_mode: bool
    
    # Ingested Market & Indicator Snapshot
    market_snapshot: Dict[str, Any]
    feature_vector: Dict[str, Any]
    data_quality_ok: bool
    data_quality_flags: List[str]
    
    # Intelligence & Causal Context
    macro_regime: Dict[str, Any]
    four_state_regime: Dict[str, Any]
    master_expectancy_r: float
    dynamic_strategy_weights: Dict[str, float]
    applied_risk_multiplier: float
    causal_paths: List[Dict[str, Any]]
    causal_cesi_score: float
    retrieved_scenarios: List[Dict[str, Any]]
    retrieved_atomic_facts: List[Dict[str, Any]]
    universal_decision: Optional[Dict[str, Any]]
    
    # Synthesis & Hypothesis Formulation
    raw_hypotheses: Annotated[List[Dict[str, Any]], merge_hypotheses]
    fused_proposal: Optional[Dict[str, Any]]
    fusion_score: float
    signal_arbitration: Optional[Dict[str, Any]]
    
    # Risk Arbitration & Deterministic Safety Gate
    validation_passed: bool
    rejection_reasons: Annotated[List[str], merge_rejection_reasons]
    order_intent: Optional[Dict[str, Any]]
    
    # Broker Routing & Execution
    execution_receipt: Optional[Dict[str, Any]]
    
    # Graph Execution Observability & Status
    status: str  # "INITIALIZED", "FEATURES_BUILT", "HYPOTHESIS_FORMED", "VALIDATED", "EXECUTED", "REJECTED", "ERROR"
    error: Optional[str]
    audit_trace: Annotated[List[str], merge_audit_logs]
    updated_at: str


class FastLaneState(TypedDict):
    """State schema for the high-frequency Fast-Lane Tick & Position Monitoring Graph."""
    tick_id: str
    symbol: str
    bid: float
    ask: float
    spread_pips: float
    open_positions: List[Dict[str, Any]]
    account_equity: float
    account_margin_level: float
    adjustments_applied: Annotated[List[Dict[str, Any]], merge_hypotheses]
    emergency_stops_triggered: Annotated[List[int], operator.add]
    reconciliation_mismatches: Annotated[List[str], merge_rejection_reasons]
    audit_trace: Annotated[List[str], merge_audit_logs]
    updated_at: str


class MemoryReflectionState(TypedDict):
    """State schema for the asynchronous Slow-Lane Post-Trade Reflection Graph."""
    deal_ticket: int
    symbol: str
    strategy: str
    open_time: str
    close_time: str
    entry_price: float
    close_price: float
    realized_pnl: float
    realized_r_multiple: float
    outcome_label: str  # "WIN_TARGET", "LOSS_STOP", "MANUAL_EXIT", "TIMEOUT"
    distilled_atomic_facts: Annotated[List[Dict[str, Any]], merge_hypotheses]
    scenario_key: Optional[str]
    scenario_updated: bool
    macro_dynamics_feedback: Optional[Dict[str, Any]]
    audit_trace: Annotated[List[str], merge_audit_logs]
    updated_at: str


def create_initial_trading_cycle_state(
    symbol: str,
    timeframe: str = "M15",
    strategy: str = "causal_continuation",
    paper_mode: bool = True,
    session_id: Optional[str] = None,
    cycle_id: Optional[str] = None,
    market_snapshot: Optional[Dict[str, Any]] = None,
) -> TradingCycleState:
    """Factory creating a clean, fail-closed TradingCycleState dictionary."""
    now = utcnow_iso()
    sid = session_id or f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    cid = cycle_id or f"cycle_{symbol}_{timeframe}_{int(datetime.now(timezone.utc).timestamp())}"
    
    return {
        "session_id": sid,
        "cycle_id": cid,
        "symbol": str(symbol).strip().upper(),
        "timeframe": str(timeframe).strip().upper(),
        "strategy": str(strategy).strip(),
        "paper_mode": bool(paper_mode),
        "market_snapshot": dict(market_snapshot or {}),
        "feature_vector": {},
        "data_quality_ok": True,
        "data_quality_flags": [],
        "macro_regime": {},
        "four_state_regime": {},
        "master_expectancy_r": 0.0,
        "dynamic_strategy_weights": {},
        "applied_risk_multiplier": 1.0,
        "causal_paths": [],
        "causal_cesi_score": 1.0,
        "retrieved_scenarios": [],
        "retrieved_atomic_facts": [],
        "universal_decision": None,
        "raw_hypotheses": [],
        "fused_proposal": None,
        "fusion_score": 0.0,
        "signal_arbitration": None,
        "validation_passed": False,
        "rejection_reasons": [],
        "order_intent": None,
        "execution_receipt": None,
        "status": "INITIALIZED",
        "error": None,
        "audit_trace": [f"Cycle {cid} initialized for {symbol} ({timeframe}) at {now}"],
        "updated_at": now,
    }
