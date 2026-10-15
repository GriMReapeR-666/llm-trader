from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import threading

@dataclass
class UIState:
    # System Status
    is_connected: bool = False
    is_running: bool = False
    cycle_count: int = 0
    uptime: str = "00:00:00"
    workflow_state: str = "IDLE"  # Moved from original "Status" section
    
    # Account
    balance: float = 0.0
    equity: float = 0.0
    pnl: float = 0.0
    active_account_id: str = "N/A"
    
    # Logs / Reasoning
    logs: List[Dict[str, Any]] = field(default_factory=list)
    
    # Dashboard Metrics
    active_narrative: str = "Initializing system..."
    
    # Trading Data
    positions: List[Dict[str, Any]] = field(default_factory=list)
    order_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # News
    news_events: List[Dict[str, Any]] = field(default_factory=list)
    news_evidence_summary: Optional[Dict[str, Any]] = None  # Layer-0 triage cycle summary
    news_kill_switch_active: bool = False  # D9 headline tripwire (advisory; market-data confirmation gates risk)

    # Technical Indicators & Multi-Timeframe
    technical_indicators: Dict[str, Any] = field(default_factory=dict)
    mtf_data: Dict[str, Any] = field(default_factory=dict)
    smc_structure: Dict[str, Any] = field(default_factory=dict)
    macro_intelligence: Dict[str, Any] = field(default_factory=dict)
    
    # Brain / RL
    rl_metrics: Dict[str, List[Any]] = field(default_factory=lambda: {"episodes": [], "rewards": [], "epsilon": []})
    q_table_top: List[Dict[str, Any]] = field(default_factory=list)
    token_usage: List[Dict[str, Any]] = field(default_factory=list)

    system_health: Dict[str, Any] = field(default_factory=lambda: {
        "api_latency": 0,
        "memory_usage": 0, 
        "token_usage": 0,
        "win_rate": 0.0,
        "drawdown": 0.0,
        "session_pnl": 0.0
    })

    # ---------------------------------------------------------
    # LangGraph Live Streaming & Pipeline Visualization Fields
    # ---------------------------------------------------------
    active_graph_node: str = "IDLE"
    node_statuses: Dict[str, str] = field(default_factory=lambda: {
        "technical_net": "PENDING",
        "macro_analyst": "PENDING",
        "memory_retriever": "PENDING",
        "market_model": "PENDING",
        "signal_arbitrator": "PENDING",
        "debate_protocol": "PENDING",
        "risk_controller": "PENDING",
        "order_execution": "PENDING",
        "rejection_sink": "PENDING",
    })
    graph_audit_trail: List[str] = field(default_factory=list)
    last_fused_proposal: Optional[Dict[str, Any]] = None
    last_validation_result: Optional[Dict[str, Any]] = None
    last_graph_update_ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ---------------------------------------------------------
    # Macro-Cognitive Telemetry Fields
    # ---------------------------------------------------------
    macro_regime: str = "NORMAL_RISK_OFF"
    net_liquidity_stance: str = "NEUTRAL_LIQUIDITY"
    active_narrative_summary: str = ""
    active_tripwires_status: List[Dict[str, Any]] = field(default_factory=list)
    last_decision_bundle: Optional[Dict[str, Any]] = None
    last_causal_attribution: Optional[Dict[str, Any]] = None

    # Methods to update thread-safely
    def add_log(self, log: Dict[str, Any]):
        self.logs.append(log)
        if len(self.logs) > 100:
            self.logs.pop(0)

    def update_graph_step(self, node_name: str, state_update: Dict[str, Any]):
        """Update live graph pipeline state when a node executes."""
        self.active_graph_node = str(node_name)
        self.last_graph_update_ts = datetime.now(timezone.utc).isoformat()
        
        # Ensure dynamic node key is registered
        if node_name not in self.node_statuses:
            self.node_statuses[node_name] = "PENDING"

        # Mark current node active, previous as done
        for k in list(self.node_statuses.keys()):
            if k == node_name:
                self.node_statuses[k] = "RUNNING"
            elif self.node_statuses[k] == "RUNNING":
                self.node_statuses[k] = "COMPLETED"

        # Ingest state values
        if "audit_trace" in state_update:
            traces = state_update["audit_trace"]
            if isinstance(traces, list):
                for t in traces:
                    if t not in self.graph_audit_trail:
                        self.graph_audit_trail.append(str(t))
                        self.add_log({"time": datetime.now().strftime("%H:%M:%S"), "message": str(t), "type": "GRAPH"})

        if "fused_proposal" in state_update and state_update["fused_proposal"]:
            self.last_fused_proposal = state_update["fused_proposal"]

        if "validation_passed" in state_update:
            self.last_validation_result = {
                "passed": state_update["validation_passed"],
                "reasons": state_update.get("rejection_reasons", []),
            }

        # Mark final status and handle conditional branch exclusion
        if node_name in ("order_execution", "rejection_sink"):
            self.node_statuses[node_name] = "COMPLETED"
            self.workflow_state = "EXECUTED" if node_name == "order_execution" else "REJECTED"
            if node_name == "order_execution":
                self.node_statuses["rejection_sink"] = "SKIPPED"
            elif node_name == "rejection_sink":
                self.node_statuses["order_execution"] = "SKIPPED"

    def reset_graph_pipeline(self, symbol: str = "", timeframe: str = ""):
        """Reset node status indicators for a fresh bar-close cycle."""
        self.active_graph_node = "START"
        self.node_statuses = {
            "technical_net": "PENDING",
            "macro_analyst": "PENDING",
            "memory_retriever": "PENDING",
            "market_model": "PENDING",
            "signal_arbitrator": "PENDING",
            "debate_protocol": "PENDING",
            "risk_controller": "PENDING",
            "order_execution": "PENDING",
            "rejection_sink": "PENDING",
        }
        self.workflow_state = "ANALYZING"
        if symbol:
            self.add_log({
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": f"Starting LangGraph cycle for {symbol} ({timeframe})",
                "type": "CYCLE_START"
            })

# Global Instance
state = UIState()
ui_state = state  # alias for package-style importers (intelligence.graph_nodes)
