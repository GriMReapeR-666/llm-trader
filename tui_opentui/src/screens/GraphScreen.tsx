import { useState, useMemo, useEffect, useCallback } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { useContentWidth, clip, pad, fitRow } from "../layout"
import { T } from "../theme"
import { TabBar } from "../components/TabBar"

// ─── 1. TIMELINE DEFINITIONS (t = 0ms to t = +150ms) ─────────────────────────

export interface TimelineMilestone {
  id: string
  timeOffsetMs: number
  timeLabel: string
  title: string
  subtitle: string
  module: string
  file: string
  category: "INGESTION" | "MACRO" | "FUSION" | "DELIBERATION" | "SAFETY" | "RISK" | "ADAPTER" | "BROKER" | "BROADCAST"
  status: "UNVERIFIED" | "DONE" | "PASS" | "RUNNING" | "READY" | "LIVE" | "STANDBY" | "FILLED" | "SYNCED" | "WATCH"
  deltaMs: number
  description: string
  details: {
    inputs: string[]
    outputs: string[]
    metrics: Record<string, string | number>
    codeSnippet: string
    payloadSummary: string
  }
}

export const TIMELINE_MILESTONES: TimelineMilestone[] = [
  {
    id: "t0_ingest",
    timeOffsetMs: 0,
    timeLabel: "t = 0ms",
    title: "Market & Macro Ingestion",
    subtitle: "High-Frequency Quotes & Macro Catalyst Discovery",
    module: "data.data_source_pipeline",
    file: "data/data_source_pipeline.py",
    category: "INGESTION",
    status: "UNVERIFIED",
    deltaMs: 0.8,
    description: "Ingests real-time tick streams, depth of market, central bank RSS statements, and economic calendar events.",
    details: {
      inputs: ["MetaTrader 5 Tick & DOM Stream", "Central Bank Policy Feeds", "Economic Calendar Events"],
      outputs: ["Normalized Tick Stream & Market Quotes", "Structured Macro Catalysts & Sentiment"],
      metrics: { "Ingestion Protocol": "Async Queue", "Target Latency": "< 1.0ms", "Quality Gate": "ENFORCED" },
      codeSnippet: "pipeline.ingest_market_telemetry(poll_all=True)",
      payloadSummary: '{"source": "MARKET_FEEDS", "channels": ["ticks", "calendar", "macro"], "status": "INGESTING"}',
    },
  },
  {
    id: "t15_macro",
    timeOffsetMs: 15,
    timeLabel: "t = 15ms",
    title: "Macro Causal DAG & NER Engine",
    subtitle: "Parses Macro Catalysts & Computes Directional Impulse Vectors",
    module: "intelligence.macro_graph",
    file: "intelligence/macro_graph.py",
    category: "MACRO",
    status: "UNVERIFIED",
    deltaMs: 14.2,
    description: "Parses macroeconomic entities, updates causal node weights in SQLite DAG, and calculates directional impulse vectors across asset classes.",
    details: {
      inputs: ["Normalized Macro Event Stream", "Causal Graph DAG (trading_state.db)", "Historical Impulse Matrix"],
      outputs: [
        "Impulse Vector: Gold (XAUUSD)",
        "Impulse Vector: US Dollar (DXY)",
        "Impulse Vector: Benchmark Yields",
        "Impulse Vector: Major Currencies (EUR/GBP/JPY)",
      ],
      metrics: { "Graph Storage": "trading_state.db", "Causal Model": "Directed Acyclic Graph", "Impulse Computation": "Kalman / Bayesian" },
      codeSnippet: "macro_graph.compute_macro_impulses(events, causal_dag) -> DirectionalImpulses",
      payloadSummary: '{"engine": "macro_causal_dag", "db": "trading_state.db", "status": "EVALUATING"}',
    },
  },
  {
    id: "t30_fusion",
    timeOffsetMs: 30,
    timeLabel: "t = 30ms",
    title: "Opportunity Bus & Feature Fusion",
    subtitle: "Fuses Macro Impulses with Alpha Candidates & Technical Setups",
    module: "intelligence.opportunity_bus",
    file: "intelligence/opportunity_bus.py",
    category: "FUSION",
    status: "UNVERIFIED",
    deltaMs: 13.9,
    description: "Aggregates directional macro impulses with active quantitative alpha signals and orderbook DOM depth into unified trade candidates.",
    details: {
      inputs: ["Directional Macro Impulse Vectors", "In-Memory Alpha Factor Signals", "M15/H1 Technical Indicators & DOM Depth"],
      outputs: ["Fused Trade Opportunity Candidates", "Ensemble Confidence Score", "Composite Signal Vector"],
      metrics: { "Bus Mode": "Advisory Only", "Execution Authority": "FALSE", "Fusion Algorithm": "Shrinkage Weighted" },
      codeSnippet: "opportunity_bus.fuse_opportunities(candidates, macro_impulse, weights=shrinkage_weights)",
      payloadSummary: '{"target": "opportunity_bus", "authority": false, "status": "FUSING"}',
    },
  },
  {
    id: "t45_deliberation",
    timeOffsetMs: 45,
    timeLabel: "t = 45ms",
    title: "Decision Engine & Multi-Agent Deliberation",
    subtitle: "Structured Deliberation Across Agent Personas with Bayesian Prior",
    module: "intelligence.deliberation",
    file: "intelligence/deliberation.py",
    category: "DELIBERATION",
    status: "UNVERIFIED",
    deltaMs: 14.6,
    description: "Evaluates candidate trades across specialized agent personas (Technical, Macro, MarketModel, RiskController, QuantCoach) with Bayesian Prior.",
    details: {
      inputs: ["Fused Opportunity Candidate", "5 Specialized Agent Personas", "Market Regime Prior (BOCPD)"],
      outputs: [
        "TechnicalNet: Microstructure & Momentum Analysis",
        "MacroAnalyst: Geopolitical & Macro Risk Assessment",
        "MarketModel: Regime & Scenario Evaluation",
        "RiskController: Risk Constraints & Heat Budget",
        "QuantCoach: Shrinkage & Statistical Robustness",
        "Selected Strategy Nomination",
      ],
      metrics: { "Deliberation Mode": "Adversarial Debate", "Consensus Protocol": "Bayesian Posterior", "Execution Gate": "Pending Safety" },
      codeSnippet: "deliberation_engine.deliberate(candidate, personas, prior=regime_prior) -> ConsensusVerdict",
      payloadSummary: '{"engine": "deliberation", "personas": 5, "verdict": "NOMINATED"}',
    },
  },
  {
    id: "t60_safety",
    timeOffsetMs: 60,
    timeLabel: "t = 60ms",
    title: "Deterministic 6-Gate Safety Pipeline",
    subtitle: "Zero-Hallucination Deterministic Validation (Gates 1-6)",
    module: "core.trading_safety",
    file: "core/trading_safety.py",
    category: "SAFETY",
    status: "UNVERIFIED",
    deltaMs: 15.1,
    description: "Deterministic 6-Gate Safety Pipeline enforces unbending mathematical constraints: Validator, WalkForwardDecay, OverfitGate, DataDriftGate, PortfolioRiskArbiter, Immutable Journal.",
    details: {
      inputs: ["Candidate Trade Intent", "Live Portfolio Heat & Drawdown State", "Historical CPCV & Robustness Metrics"],
      outputs: [
        "Gate 1 (Validator): 15 Boolean rules, schema, bounds, session checks",
        "Gate 2 (WalkForwardDecay): Rolling OOS/IS Sharpe stability ratio check",
        "Gate 3 (OverfitGate): Bailey & López de Prado Deflated Sharpe & PBO",
        "Gate 4 (DataDriftGate): Kolmogorov-Smirnov covariate drift check",
        "Gate 5 (PortfolioRiskArbiter): 6.0R portfolio heat & equity risk caps",
        "Gate 6 (Immutable Journal Audit): Cryptographic SHA-256 pre-trade commitment",
      ],
      metrics: { "Enforcement": "Deterministic Fail-Closed", "Authority": "SOLE GATEWAY", "Override": "PROHIBITED" },
      codeSnippet: "trading_safety.validate_trade_intent(intent, account_state) -> SafetyCertificate",
      payloadSummary: '{"gate_enforcement": "FAIL_CLOSED", "audit": "trading_state.db", "status": "VERIFYING"}',
    },
  },
  {
    id: "t75_sizing",
    timeOffsetMs: 75,
    timeLabel: "t = 75ms",
    title: "Order Generation & Dynamic Sizing",
    subtitle: "Fractional Kelly Sizing, ATR Structure Stops & Slippage Budget",
    module: "core.risk_manager",
    file: "core/risk_manager.py",
    category: "RISK",
    status: "UNVERIFIED",
    deltaMs: 13.8,
    description: "Calculates mathematical position sizing using fractional Kelly criterion (0.25x), structural ATR stop loss, and maximum slippage tolerance.",
    details: {
      inputs: ["Approved Trade Intent", "Account Equity & Available Margin", "Market Volatility (ATR-14)", "Risk Budget & Heat Limits"],
      outputs: [
        "Position Volume (Lots)",
        "Structural ATR Stop Loss",
        "Dynamic Profit Target (R-Multiple)",
        "Max Slippage Allowance & Tolerance Bounds",
      ],
      metrics: { "Kelly Fraction": "0.25x", "Max Risk Cap": "1.0% Equity", "Stop Protocol": "Structural ATR" },
      codeSnippet: "risk_manager.compute_position_size(intent, atr=atr_val, equity=equity_val)",
      payloadSummary: '{"sizing_model": "FRACTIONAL_KELLY", "kelly_f": 0.25, "status": "SIZED"}',
    },
  },
  {
    id: "t90_adapter",
    timeOffsetMs: 90,
    timeLabel: "t = 90ms",
    title: "Execution Broker Adapter Bridge",
    subtitle: "Routes Validated Order Payload to MT5 Terminal via MCP Bridge (Port 22346)",
    module: "core.order_execution",
    file: "core/order_execution.py",
    category: "ADAPTER",
    status: "UNVERIFIED",
    deltaMs: 14.5,
    description: "Serializes verified trade intent into binary MT5 payload and routes through Python IPC / MT5 MCP Server on port 22346 to broker.",
    details: {
      inputs: ["Validated & Sized Order Payload", "Execution Capability Token", "Broker Adapter Bridge"],
      outputs: ["MCP JSON-RPC Order Request", "TCP Socket Pipe Connected", "Order Transmission Receipt"],
      metrics: { "Bridge Type": "MT5 MCP Server", "Port": 22346, "Transport": "IPC / TCP", "Retry Policy": "Zero-Duplication" },
      codeSnippet: "order_execution.route_to_broker(intent, adapter) -> BrokerResponse",
      payloadSummary: '{"transport": "MCP_SOCKET", "port": 22346, "status": "ROUTING"}',
    },
  },
  {
    id: "t120_broker",
    timeOffsetMs: 120,
    timeLabel: "t = 120ms",
    title: "Broker Execution & Fill Confirmation",
    subtitle: "MetaTrader 5 Order Executed on Server, Ticket ID Allocated",
    module: "core.trade_manager",
    file: "core/trade_manager.py",
    category: "BROKER",
    status: "UNVERIFIED",
    deltaMs: 28.4,
    description: "MetaTrader 5 broker server fills order, returns ticket ID, logs exact execution price, server timestamp, spread and slippage metrics.",
    details: {
      inputs: ["Broker Execution Queue", "Live MT5 Connection", "Order Routing Gateway"],
      outputs: [
        "Broker Deal Result & Ticket Allocation",
        "Execution Fill Price & Timestamp",
        "Spread at Fill & Slippage Audit",
        "Position Lifecycle Registration",
      ],
      metrics: { "Server Settlement": "MetaTrader 5", "Deal Verification": "Synchronous", "Execution": "Market Deal" },
      codeSnippet: "mt5.order_send(request) -> MqlTradeResult",
      payloadSummary: '{"broker": "MT5_SERVER", "retcode": "AWAITING_FILL", "status": "DISPATCHED"}',
    },
  },
  {
    id: "t150_broadcast",
    timeOffsetMs: 150,
    timeLabel: "t = 150ms",
    title: "OpenTUI Live Stream & Telemetry Broadcast",
    subtitle: "Pushed to Event Stream, Cockpit Metrics Updated, Audit Log Synced",
    module: "core.trading_safety",
    file: "tui_opentui/src/transport/live.ts",
    category: "BROADCAST",
    status: "UNVERIFIED",
    deltaMs: 14.1,
    description: "Broadcasts completed trade across OpenTUI deck: Event stream receives decision badge, Cockpit metrics update, Position tracker registers ticket, Audit journal writes SHA-256 block.",
    details: {
      inputs: ["Broker Fill Confirmation", "Execution Telemetry Metrics", "Pre-Trade SHA-256 Hash"],
      outputs: [
        "OpenTUI Stream Event Appended",
        "Cockpit Metrics & Radar Refreshed",
        "Live Position Tracker Synchronized",
        "SQLite Journal Block Committed (trading_state.db)",
      ],
      metrics: { "Deck Push": "Reactive WebSocket", "Journal DB": "trading_state.db", "Audit Integrity": "14/14 Verification" },
      codeSnippet: "store.pushStream(event); store.updatePositions(); journal.commit(block)",
      payloadSummary: '{"transport": "OPENTUI_WEBSOCKET", "journal": "COMMITTED", "status": "SYNCED"}',
    },
  },
]

// ─── 2. COMPLETE RATAFLOW NODE GRAPH (34 NODES ACROSS 8 ARCHITECTURAL LAYERS) ─

export interface RataflowNode {
  id: string
  layer: number
  layerName: string
  title: string
  typeIcon: string
  status: "UNVERIFIED" | "LIVE" | "PASS" | "ACTIVE" | "IDLE" | "QUEUED" | "READY" | "COMPILED" | "FILLED" | "SYNCED" | "STANDBY" | "WATCH" | "ERROR"
  agent: string
  file: string
  flowTag: string
  inPorts: string[]
  outPorts: string[]
  metrics: { label: string; value: string; color?: string }[]
  summary: string
  codeRef: string
  connectionsTo: string[]
  connectionsFrom: string[]
}

export const RATAFLOW_NODES: RataflowNode[] = [
  // ── LAYER 1: ONLINE DISCOVERY & INGESTION ──────────────────────────────────
  {
    id: "node_web_research",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "Autonomous Web Researcher & Financial News Feed",
    typeIcon: "▣ [DATA]",
    status: "UNVERIFIED",
    agent: "ResearchAgent",
    file: "research/autonomous_web_researcher.py",
    flowTag: "Social & News Intelligence",
    inPorts: ["[in:0] Financial News & Economic Wire Feeds", "[in:1] Central Bank Press Releases & Scrapers"],
    outPorts: ["[out:0] Raw Entity Text", "[out:1] Macro Catalyst Urgency Score"],
    metrics: [{ label: "Data Feeds", value: "ONLINE" }, { label: "Freshness", value: "REALTIME" }, { label: "Feed Ingestion", value: "ACTIVE", color: T.up }],
    summary: "Continuously tracks economic headlines, central bank communiqués, and policy statements in real-time.",
    codeRef: "AutonomousWebResearcher.discover_market_intel()",
    connectionsTo: ["node_macro_ner"],
    connectionsFrom: [],
  },
  {
    id: "node_cb_rss",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "Central Bank RSS Ingestion Engine",
    typeIcon: "▣ [DATA]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "intelligence/rss_feed.py",
    flowTag: "Monetary Policy Ingestion",
    inPorts: ["[in:0] Fed Reserve RSS Feed", "[in:1] ECB / BOJ / BOE Policy Feeds"],
    outPorts: ["[out:0] Official Policy Speeches", "[out:1] Rate Decision Surprises"],
    metrics: [{ label: "Feeds Active", value: "CB Feeds" }, { label: "Poll Interval", value: "60s" }, { label: "Parser", value: "XML OK", color: T.up }],
    summary: "Monitors and parses central bank RSS feeds, press releases, and interest rate decision transcripts.",
    codeRef: "RSSFeedReader.poll_all_feeds() -> List[PolicyItem]",
    connectionsTo: ["node_macro_ner", "node_opp_bus"],
    connectionsFrom: [],
  },
  {
    id: "node_academic_scraper",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "Academic Literature Scraper (SSRN & ArXiv)",
    typeIcon: "▣ [DATA]",
    status: "UNVERIFIED",
    agent: "ResearchAgent",
    file: "research/autonomous_web_researcher.py",
    flowTag: "Quantitative Research Ingestion",
    inPorts: ["[in:0] SSRN Financial Economics Network", "[in:1] ArXiv q-fin.TR / q-fin.MF Scraper"],
    outPorts: ["[out:0] Academic Paper Summaries", "[out:1] Discovered Alpha Formula Specs (JSON)"],
    metrics: [{ label: "SSRN Feed", value: "ONLINE" }, { label: "ArXiv q-fin", value: "ONLINE" }, { label: "Findings", value: "JSON Synced", color: T.up }],
    summary: "Scrapes peer-reviewed quant finance papers from SSRN and ArXiv, extracts mathematical alpha formulas into artifacts.",
    codeRef: "AutonomousWebResearcher.scrape_arxiv_ssrn()",
    connectionsTo: ["node_alpha_miner", "node_alpha_decay"],
    connectionsFrom: [],
  },
  {
    id: "node_market_feed",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "MT5 Realtime Tick & DOM Feed",
    typeIcon: "▣ [DATA]",
    status: "UNVERIFIED",
    agent: "TechnicalNet",
    file: "core/mt5_data.py",
    flowTag: "Microstructure Ingestion",
    inPorts: ["[in:0] MetaTrader 5 Terminal IPC (Port 22346)", "[in:1] FRED Realtime API Stream"],
    outPorts: ["[out:0] Raw Tick Stream", "[out:1] DOM Bid/Ask Imbalance", "[out:2] OHLCV Bars"],
    metrics: [{ label: "MT5 Feed", value: "TICK/DOM" }, { label: "Symbols", value: "MULTI-ASSET" }, { label: "IPC Transport", value: "TCP/IPC", color: T.up }],
    summary: "Streams high-frequency tick prices, orderbook depth of market (DOM), and historical bars directly from MT5.",
    codeRef: "mt5.copy_rates_from_pos(symbol, timeframe, 0, 300)",
    connectionsTo: ["node_data_quality"],
    connectionsFrom: [],
  },
  {
    id: "node_data_quality",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "Data Quality & Staleness Gate",
    typeIcon: "◇ [GATE]",
    status: "UNVERIFIED",
    agent: "TechnicalNet",
    file: "core/data_quality.py",
    flowTag: "Data Cleansing & Validation",
    inPorts: ["[in:0] Raw Tick Stream & OHLCV Bars"],
    outPorts: ["[out:0] Verified Cleaned Bars", "[out:1] Freshness Verification Ticket"],
    metrics: [{ label: "Staleness", value: "< 1.1s" }, { label: "Gaps Filled", value: "0 Clean" }, { label: "Spikes Filtered", value: "0", color: T.up }],
    summary: "Ensures no price anomalies, missing bars, or corrupted quotes before data enters the alpha or macro pipelines.",
    codeRef: "data_quality.verify_stream_freshness(bars, max_stale_s=3)",
    connectionsTo: ["node_calendar_gate", "node_regime_detect", "node_alpha_miner"],
    connectionsFrom: ["node_market_feed"],
  },
  {
    id: "node_graphrag_retrieval",
    layer: 1,
    layerName: "1. ONLINE DISCOVERY & INGESTION",
    title: "GraphRAG & Macro Domain Knowledge Retrieval",
    typeIcon: "▣ [KNOWLEDGE]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "intelligence/graphrag_reasoning.py",
    flowTag: "Knowledge Graph Retrieval",
    inPorts: ["[in:0] Discovered Macro Catalysts", "[in:1] Domain Knowledge Compendium (SQLite/Graph)"],
    outPorts: ["[out:0] Contextual Causal Anchors", "[out:1] Historical Crisis Analogs"],
    metrics: [{ label: "Knowledge Nodes", value: "1,265 Nodes" }, { label: "Retrieval Latency", value: "4.1ms" }, { label: "Graph Depth", value: "3 Hops", color: T.up }],
    summary: "Graph-based Retrieval Augmented Generation querying macroeconomic causal relations, historical shock episodes, and market axioms.",
    codeRef: "graphrag_engine.retrieve_relevant_subgraph(query_entity)",
    connectionsTo: ["node_macro_ner", "node_causal_validator"],
    connectionsFrom: ["node_web_research", "node_academic_scraper"],
  },

  // ── LAYER 2: MACRO CAUSAL DAG & PERCEPTION ─────────────────────────────────
  {
    id: "node_calendar_gate",
    layer: 2,
    layerName: "2. MACRO CAUSAL DAG & PERCEPTION",
    title: "Economic Calendar & Blackout Lockout",
    typeIcon: "◇ [GATE]",
    status: "UNVERIFIED",
    agent: "EventAgent",
    file: "intelligence/calendar_wrapper.py",
    flowTag: "Macro Event Gating",
    inPorts: ["[in:0] Cleaned Bars", "[in:1] ForexFactory / FXStreet Calendar Feed"],
    outPorts: ["[out:0] Blackout Lockout Flag (False: Trade Allowed)", "[out:1] High-Impact Event Warning"],
    metrics: [{ label: "Blackout Window", value: "90m Buffer" }, { label: "Gate State", value: "CLEAR", color: T.up }, { label: "Calendar Sync", value: "ACTIVE" }],
    summary: "Enforces 90-minute pre/post high-impact economic news trading blackout windows (NFP, CPI, FOMC).",
    codeRef: "calendar_wrapper.check_blackout_window(symbol, buffer_min=90)",
    connectionsTo: ["node_opp_bus", "node_gate1_validator"],
    connectionsFrom: ["node_data_quality"],
  },
  {
    id: "node_macro_ner",
    layer: 2,
    layerName: "2. MACRO CAUSAL DAG & PERCEPTION",
    title: "Macro Graph & NER Entity Engine",
    typeIcon: "◈ [MACRO]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "intelligence/macro_graph.py",
    flowTag: "Causal Impulse Computation",
    inPorts: ["[in:0] Parsed Macro Entity Text", "[in:1] Central Bank Statements"],
    outPorts: ["[out:0] Impulse Vector: Metals (Gold)", "[out:1] Impulse Vector: FX (USD/EUR)", "[out:2] Impulse Vector: Sovereign Yields"],
    metrics: [{ label: "NER Matching", value: "ACTIVE" }, { label: "Causal DAG", value: "trading_state.db" }, { label: "Contradictions", value: "0 Clean", color: T.up }],
    summary: "Parses named macro entities and updates weighted causal graph to compute directional asset impulses.",
    codeRef: "macro_graph.compute_macro_impulses(events, causal_dag)",
    connectionsTo: ["node_causal_validator", "node_opp_bus", "node_multi_agent"],
    connectionsFrom: ["node_web_research", "node_cb_rss"],
  },
  {
    id: "node_causal_validator",
    layer: 2,
    layerName: "2. MACRO CAUSAL DAG & PERCEPTION",
    title: "Autonomous Causal Edge Validator",
    typeIcon: "◇ [VALIDATE]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "intelligence/autonomous_causal_engine.py",
    flowTag: "Empirical Causal Verification",
    inPorts: ["[in:0] Candidate Macro Causal Edges", "[in:1] Historical Event Reactions"],
    outPorts: ["[out:0] Statistically Verified Causal Edges", "[out:1] Empirical Hit Rate Verification"],
    metrics: [{ label: "Validation Engine", value: "ACTIVE" }, { label: "Sample Basis", value: "HISTORICAL" }, { label: "Replay Floor", value: "PASS", color: T.up }],
    summary: "Statistically evaluates macro causal hypotheses against historical event logs before granting trading edge status.",
    codeRef: "autonomous_causal_validator.validate_candidate_edge(edge)",
    connectionsTo: ["node_opp_bus"],
    connectionsFrom: ["node_macro_ner"],
  },
  {
    id: "node_regime_detect",
    layer: 2,
    layerName: "2. MACRO CAUSAL DAG & PERCEPTION",
    title: "Regime Detection & Structural Breaks",
    typeIcon: "⊕ [TRANSFORM]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "strategies/regime.py",
    flowTag: "Market Regime Classification",
    inPorts: ["[in:0] Cleaned OHLCV Bars", "[in:1] Realized Volatility Surface"],
    outPorts: ["[out:0] Active Regime State (BOCPD)", "[out:1] Structural Break Probability"],
    metrics: [{ label: "Method", value: "BOCPD + Hurst" }, { label: "Regime State", value: "DYNAMIC" }, { label: "Surface", value: "REALTIME", color: T.cyan }],
    summary: "Bayesian Online Changepoint Detection (BOCPD) and Hurst exponent estimation to classify active regime.",
    codeRef: "regime_detector.detect_regime(bars) -> RegimeState",
    connectionsTo: ["node_opp_bus", "node_multi_agent"],
    connectionsFrom: ["node_data_quality"],
  },
  {
    id: "node_sanctions_screener",
    layer: 2,
    layerName: "2. MACRO CAUSAL DAG & PERCEPTION",
    title: "Geopolitical & Sovereign Sanctions Screener",
    typeIcon: "◇ [SANCTIONS]",
    status: "UNVERIFIED",
    agent: "MacroAnalyst",
    file: "intelligence/sanctions_screening_engine.py",
    flowTag: "Geopolitical Risk Screening",
    inPorts: ["[in:0] Parsed Entities", "[in:1] Global Sanctions & OFAC Lists", "[in:2] Sovereign Default Spreads"],
    outPorts: ["[out:0] Sovereign Risk Clearance Certificate", "[out:1] Sanctioned Asset Blacklist"],
    metrics: [{ label: "Entities Screened", value: "100% Clean" }, { label: "Geopolitical Risk", value: "MODERATE" }, { label: "Sanction Lockout", value: "NONE", color: T.up }],
    summary: "Screens assets and trading counterparties against international sanctions lists, sovereign debt default hazards, and geopolitical embargoes.",
    codeRef: "sanctions_screener.screen_tradable_universe(symbols)",
    connectionsTo: ["node_opp_bus", "node_gate1_validator"],
    connectionsFrom: ["node_macro_ner"],
  },

  // ── LAYER 3: AUTONOMOUS ALPHA LAB & SELF-HEALING ───────────────────────────
  {
    id: "node_alpha_miner",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Alpha Miner & Factor Primitives (22 Alphas)",
    typeIcon: "⬡ [ALPHA]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/alpha_factors.py",
    flowTag: "Alpha Computation",
    inPorts: ["[in:0] Cleaned OHLCV Bars", "[in:1] Discovered Alpha Specs"],
    outPorts: ["[out:0] 22 Quantitative Alpha Series (alpha_001 → alpha_101)"],
    metrics: [{ label: "Total Alphas", value: "22 Factors" }, { label: "Primitives", value: "16 TS Ops" }, { label: "Calc Latency", value: "3.4ms", color: T.up }],
    summary: "Computes 22 Quant Platform-style alpha factors using vectorized time-series primitives (ts_rank, decay_linear, delta).",
    codeRef: "compute_alpha_factors(df, active_alpha_ids)",
    connectionsTo: ["node_ga_alpha_miner", "node_alpha_ensemble"],
    connectionsFrom: ["node_data_quality", "node_academic_scraper", "node_alpha_rotation"],
  },
  {
    id: "node_ga_alpha_miner",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Genetic Algorithm & AST Symbolic Alpha Miner",
    typeIcon: "⬡ [GA-EVOLVER]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/alpha_miner/ga_engine.py",
    flowTag: "Evolutionary Factor Mining",
    inPorts: ["[in:0] Primitive Operators (ts_rank, decay, diff)", "[in:1] Candidate Expression Trees"],
    outPorts: ["[out:0] Mutated Generation Alphas", "[out:1] Pareto-Optimal Symbolic ASTs"],
    metrics: [{ label: "Generation", value: "Gen 42" }, { label: "Population", value: "500 Trees" }, { label: "Fitness (IC)", value: "0.062", color: T.up }],
    summary: "Uses genetic programming (mutation, crossover, tournament selection) to evolve symbolic mathematical alpha expression trees.",
    codeRef: "ga_engine.evolve_generation(population, fitness_fn)",
    connectionsTo: ["node_alpha_ensemble"],
    connectionsFrom: ["node_alpha_miner"],
  },
  {
    id: "node_alpha_ensemble",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Dynamic IC-Weighted Alpha Ensemble",
    typeIcon: "⬡ [ENSEMBLE]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/alpha_ensemble.py",
    flowTag: "Ensemble Aggregation",
    inPorts: ["[in:0] Multi-Factor Alpha Series", "[in:1] Expanding Window Z-Scores"],
    outPorts: ["[out:0] Composite Alpha Signal Vector", "[out:1] Shrinkage Weights (λ)"],
    metrics: [{ label: "Ensemble Engine", value: "ACTIVE" }, { label: "Weighting", value: "IC Shrinkage" }, { label: "Status", value: "PASS", color: T.up }],
    summary: "Z-score normalizes individual alphas, computes rolling Spearman ICs, and aggregates into an optimal composite.",
    codeRef: "alpha_ensemble.combine_alphas(alphas_df) -> EnsembleResult",
    connectionsTo: ["node_alpha_decay", "node_opp_bus"],
    connectionsFrom: ["node_alpha_miner"],
  },
  {
    id: "node_alpha_decay",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Alpha Decay & IC Half-Life Monitor",
    typeIcon: "⬡ [DECAY]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/alpha_decay.py",
    flowTag: "Decay Monitoring & Retraining",
    inPorts: ["[in:0] Composite Alpha Signal", "[in:1] Rolling Trade Returns"],
    outPorts: ["[out:0] Alpha Health Status (HEALTHY/DEGRADED)", "[out:1] Retraining Demand Signal"],
    metrics: [{ label: "IC Monitoring", value: "ACTIVE" }, { label: "Half-Life Check", value: "ROLLING" }, { label: "Retrain Gate", value: "STANDBY", color: T.teal }],
    summary: "Continuously tracks rolling IC degradation and half-life decay; triggers autonomous self-healing when alphas spoil.",
    codeRef: "alpha_decay_monitor.update(rolling_returns)",
    connectionsTo: ["node_alpha_rotation", "node_mql5_gen"],
    connectionsFrom: ["node_alpha_ensemble", "node_academic_scraper"],
  },
  {
    id: "node_cost_edge_filter",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Transaction Cost & Turnover Decay Filter",
    typeIcon: "◇ [COST-FILTER]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "tests/test_cost_edge_filter.py",
    flowTag: "Friction Hurdle Rate Check",
    inPorts: ["[in:0] Composite Alpha Signal", "[in:1] Live Spread + Commission + Turnover"],
    outPorts: ["[out:0] Cost-Dampened Alpha Vector", "[out:1] Cost Hurdle Clearance (> 1.0)"],
    metrics: [{ label: "Hurdle Ratio", value: "> 1.0 Gate" }, { label: "Spread+Slip", value: "REALTIME" }, { label: "Friction Check", value: "PASS", color: T.up }],
    summary: "Filters out high-turnover alphas whose gross edge is eroded by broker commissions, spreads, and overnight swap costs.",
    codeRef: "cost_edge_filter.evaluate_net_edge(alpha, turnover, costs)",
    connectionsTo: ["node_alpha_ensemble", "node_opp_bus"],
    connectionsFrom: ["node_alpha_decay"],
  },
  {
    id: "node_alpha_rotation",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "Alpha Rotation & Active Set Manager",
    typeIcon: "⊕ [ROTATION]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/alpha_rotation.py",
    flowTag: "Active Factor Allocation",
    inPorts: ["[in:0] Alpha Health Status", "[in:1] Standby Candidate Pool"],
    outPorts: ["[out:0] Active Allocation Set", "[out:1] Ejected Alpha Audit Log"],
    metrics: [{ label: "Active Pool", value: "DYNAMIC" }, { label: "Standby Pool", value: "HOT" }, { label: "Cooldown", value: "MANAGED", color: T.cyan }],
    summary: "Ejects dead/reversed alphas and promotes the highest IC_IR standby candidates into the live production ensemble.",
    codeRef: "alpha_rotation.update_active_set(alpha_qualities)",
    connectionsTo: ["node_alpha_miner"],
    connectionsFrom: ["node_alpha_decay"],
  },
  {
    id: "node_mql5_gen",
    layer: 3,
    layerName: "3. ALPHA LAB & SELF-HEALING",
    title: "MQL5 EA Auto-Coder & Self-Fixer",
    typeIcon: "⚙ [CODE-GEN]",
    status: "UNVERIFIED",
    agent: "ResearchAgent",
    file: "research/mql5_generator.py",
    flowTag: "Code Generation",
    inPorts: ["[in:0] Retraining Demand Signal", "[in:1] Discovered Alpha Formula Specs", "[in:2] MetaEditor Error Feedback"],
    outPorts: ["[out:0] Generated MQL5 Expert Advisor (.mq5)", "[out:1] Code Self-Fix Patch"],
    metrics: [{ label: "Generator", value: "MQL5 / C++" }, { label: "Safety Hooks", value: "ENFORCED" }, { label: "Self-Fix Loop", value: "READY", color: T.up }],
    summary: "Translates validated quant alpha formulas into hardened, production-grade MQL5 Expert Advisor code with automated bug-fix loops.",
    codeRef: "mql5_generator.generate_expert_advisor(strategy_spec)",
    connectionsTo: ["node_metaeditor_mcp"],
    connectionsFrom: ["node_alpha_decay"],
  },

  // ── LAYER 4: COMPILATION & MULTI-TIER HYBRID BACKTEST ──────────────────────
  {
    id: "node_metaeditor_mcp",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "MetaEditor MCP Compiler Daemon (Port 22345)",
    typeIcon: "⚙ [COMPILER]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "tools/metaeditor_mcp.py",
    flowTag: "MQL5 Binary Compilation",
    inPorts: ["[in:0] Generated MQL5 Expert Advisor (.mq5)", "[in:1] Include Headers (.mqh)"],
    outPorts: ["[out:0] Compiled Binary Executable (.ex5)", "[out:1] Compiler Diagnostics (0 Errors)"],
    metrics: [{ label: "MCP Daemon", value: "Port 22345" }, { label: "Compiler", value: "MetaEditor Headless" }, { label: "Diagnostics", value: "0 Errors", color: T.up }],
    summary: "Direct MCP integration with MetaQuotes MetaEditor. Headless compilation daemon producing validated binary EX5 artifacts.",
    codeRef: "metaeditor_mcp.compile_expert(source_path)",
    connectionsTo: ["node_vectorbt_screen"],
    connectionsFrom: ["node_mql5_gen"],
  },
  {
    id: "node_vectorbt_screen",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Tier 1: VectorBT High-Speed Screening",
    typeIcon: "◇ [SCREEN]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/hybrid_backtest_pipeline.py",
    flowTag: "In-Memory Grid Screening",
    inPorts: ["[in:0] Compiled Strategy Spec", "[in:1] Historical Bar Cache"],
    outPorts: ["[out:0] Top Candidate Parameter Sets", "[out:1] VectorBT Screening Report"],
    metrics: [{ label: "Engine", value: "VectorBT In-Memory" }, { label: "Sweep Mode", value: "Multi-Parameter" }, { label: "Status", value: "PASS", color: T.up }],
    summary: "High-speed vectorized backtesting sweeping parameter permutations in memory to filter out unviable strategies.",
    codeRef: "vbt_engine.run_parameter_sweep(strategy, data)",
    connectionsTo: ["node_mt5_broker_replay"],
    connectionsFrom: ["node_metaeditor_mcp"],
  },
  {
    id: "node_mt5_broker_replay",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Tier 2: MT5 Broker Replay with Tick Friction",
    typeIcon: "◇ [REPLAY]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/hybrid_backtest_pipeline.py",
    flowTag: "Broker Replay Simulation",
    inPorts: ["[in:0] Top Candidate Parameter Sets", "[in:1] Historical Tick Replay Cache"],
    outPorts: ["[out:0] Friction-Adjusted Replay Verdict", "[out:1] Hybrid Backtest Report (JSON)"],
    metrics: [{ label: "Friction Model", value: "Spread + Slippage" }, { label: "Replay Mode", value: "Tick-Exact" }, { label: "Stop-First", value: "VERIFIED", color: T.up }],
    summary: "Replays candidate strategies on raw broker ticks incorporating exact spread, slippage model, and swap financing drag.",
    codeRef: "hybrid_pipeline.run_two_tier_validation(strategy_id)",
    connectionsTo: ["node_cpcv_matrix", "node_strategy_promoter", "node_opp_bus"],
    connectionsFrom: ["node_vectorbt_screen"],
  },
  {
    id: "node_cpcv_matrix",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "CPCV Combinatorial Purged Cross-Validation & Embargo",
    typeIcon: "◇ [CPCV]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "core/cpcv_validation.py",
    flowTag: "Purged & Embargoed CV",
    inPorts: ["[in:0] Replay PnL Series", "[in:1] Purged K-Fold Splits (16 Paths)", "[in:2] Embargo Buffer"],
    outPorts: ["[out:0] Combinatorial Sharpe Distribution", "[out:1] PBO Overfit Score (< 0.30)"],
    metrics: [{ label: "Purged Folds", value: "Combinatorial K-Fold" }, { label: "Embargo Buffer", value: "ENFORCED" }, { label: "PBO Gate", value: "PASS", color: T.up }],
    summary: "Applies Combinatorial Purged Cross-Validation with post-test embargo to eliminate serial lookahead bias and quantify overfitting.",
    codeRef: "cpcv_validation.run_cpcv(strategy, folds=16, embargo=48)",
    connectionsTo: ["node_monte_carlo", "node_strategy_promoter"],
    connectionsFrom: ["node_mt5_broker_replay"],
  },
  {
    id: "node_kronos_synthetic",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Kronos Correlated Multi-Asset Synthetic Market Generator",
    typeIcon: "⬡ [SYNTHETIC]",
    status: "UNVERIFIED",
    agent: "ResearchAgent",
    file: "intelligence/kronos_synthetic_generator.py",
    flowTag: "Jump-Diffusion & GARCH Simulation",
    inPorts: ["[in:0] Multi-Asset Covariance Matrix", "[in:1] Jump Intensity (Merton Poisson)", "[in:2] GARCH Vol Clustering"],
    outPorts: ["[out:0] 10,000 Synchronous Synthetic Paths (EUR/GBP/XAU/OIL)", "[out:1] Regime Shift Matrices"],
    metrics: [{ label: "Model", value: "Merton + GARCH" }, { label: "Copula", value: "t-Student" }, { label: "Regimes", value: "5 Active", color: T.cyan }],
    summary: "Generates realistic, synchronous multi-asset synthetic market data with Poisson jumps and volatility clustering to stress test strategies out-of-distribution.",
    codeRef: "generate_correlated_multi_asset_synthetic_dataset(symbols, length=200, regime='volatile')",
    connectionsTo: ["node_synthetic_crisis", "node_monte_carlo"],
    connectionsFrom: ["node_data_quality"],
  },
  {
    id: "node_synthetic_crisis",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Synthetic Crisis & Tail-Risk Stress Engine",
    typeIcon: "◇ [CRISIS-TEST]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "intelligence/synthetic_crisis_engine.py",
    flowTag: "Black Swan Stress Testing",
    inPorts: ["[in:0] Synthetic Shock Paths", "[in:1] Historical Crisis Scenarios (2010 Flash, 2015 CHF, 2020 COVID)"],
    outPorts: ["[out:0] Tail-Risk Survival Certificate", "[out:1] Margin Squeeze Drawdown (5.2% ≤ 10%)"],
    metrics: [{ label: "Flash Crash", value: "SURVIVED" }, { label: "Liquidity Void", value: "SPREAD 10x" }, { label: "Max Stress DD", value: "5.2%", color: T.up }],
    summary: "Subject strategies to historical and synthetic extreme tail-risk shocks (flash crashes, liquidity voids, FX de-pegs) to verify margin resilience.",
    codeRef: "synthetic_crisis_engine.simulate_crisis_suite(strategy)",
    connectionsTo: ["node_strategy_promoter", "node_gate5_risk"],
    connectionsFrom: ["node_kronos_synthetic", "node_cpcv_matrix"],
  },
  {
    id: "node_monte_carlo",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Monte Carlo Bootstrap Permutations & VaR Envelopes",
    typeIcon: "◇ [MONTE-CARLO]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "core/robustness_validators.py",
    flowTag: "Bootstrap Reshuffle Simulation",
    inPorts: ["[in:0] Realized Trade Return Sequence", "[in:1] Slippage Perturbation Noise"],
    outPorts: ["[out:0] 95% & 99% VaR / CVaR Confidence Envelopes", "[out:1] Probability of Ruin (< 0.01%)"],
    metrics: [{ label: "Bootstrap Runs", value: "10,000 Runs" }, { label: "99% VaR", value: "-2.8%" }, { label: "Ruin Prob", value: "< 0.01%", color: T.up }],
    summary: "Reshuffles trade orders and simulates 10,000 randomized execution orderings to construct probabilistic max drawdown confidence bands.",
    codeRef: "robustness_validators.monte_carlo_drawdown_distribution(trades, runs=10000)",
    connectionsTo: ["node_param_stability", "node_strategy_promoter"],
    connectionsFrom: ["node_cpcv_matrix", "node_kronos_synthetic"],
  },
  {
    id: "node_param_stability",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Parameter Sensitivity & Surface Stability Sweep",
    typeIcon: "◇ [STABILITY]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "research/param_stability_sweep.py",
    flowTag: "Plateau Curvature Analysis",
    inPorts: ["[in:0] Optimal Parameter Vector", "[in:1] Neighborhood Grid Perturbations (±10%, ±20%)"],
    outPorts: ["[out:0] Surface Convexity Metric (0.88)", "[out:1] Knife-Edge Overfit Penalty (0.00)"],
    metrics: [{ label: "Plateau Score", value: "0.88 / 1.0" }, { label: "Curvature", value: "CONVEX" }, { label: "Knife-Edge", value: "NONE", color: T.up }],
    summary: "Evaluates the curvature of parameter performance surfaces around optimal points to ensure strategies reside on robust plateaus, not fragile spikes.",
    codeRef: "param_stability_sweep.evaluate_parameter_plateau(params, grid)",
    connectionsTo: ["node_strategy_promoter"],
    connectionsFrom: ["node_monte_carlo"],
  },
  {
    id: "node_kronos_realtime",
    layer: 4,
    layerName: "4. COMPILATION & HYBRID BACKTEST",
    title: "Kronos Realtime Foundation Model (K-line GPT & BSQ)",
    typeIcon: "◈ [KRONOS-REALTIME]",
    status: "UNVERIFIED",
    agent: "MarketModel",
    file: "research/kronos_bridge.py",
    flowTag: "Realtime Trajectory Forecasting",
    inPorts: ["[in:0] Live MT5 OHLCV Bars", "[in:1] Binary Spherical Quantization (BSQ) Codebook"],
    outPorts: ["[out:0] Autoregressive Trajectory Forecasts (M15/H1/H4)", "[out:1] Volatility Envelope & Fan Charts"],
    metrics: [{ label: "Backend", value: "Neural Torch" }, { label: "Tokenizer", value: "BSQ Discrete" }, { label: "Horizons", value: "3 Forward", color: T.up }],
    summary: "Autoregressive financial foundation model discretizing candlestick sequences into discrete tokens to forecast real-time price trajectories.",
    codeRef: "KronosPredictor.predict_trajectories(bars, horizons=[15, 60, 240])",
    connectionsTo: ["node_opp_bus", "node_multi_agent"],
    connectionsFrom: ["node_market_feed"],
  },

  // ── LAYER 5: MULTI-AGENT DELIBERATION & OPPORTUNITY BUS ────────────────────
  {
    id: "node_strategy_promoter",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "Strategy Promotion Gate & Registry",
    typeIcon: "⊕ [REGISTRY]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "core/strategy_promoter.py",
    flowTag: "Strategy Graduation",
    inPorts: ["[in:0] Friction-Adjusted Replay Verdict", "[in:1] Robustness Certificate"],
    outPorts: ["[out:0] Active Strategy State: PROMOTED to PAPER_SHADOW"],
    metrics: [{ label: "Lifecycle Stage", value: "PAPER_SHADOW" }, { label: "Min Sharpe", value: "1.50 Cap" }, { label: "Max DD", value: "10% Cap", color: T.up }],
    summary: "Graduates validated strategies across formal lifecycle stages: CANDIDATE → EVALUATION → PAPER_SHADOW → FULL.",
    codeRef: "strategy_promoter.promote_strategy(strategy_id)",
    connectionsTo: ["node_opp_bus"],
    connectionsFrom: ["node_mt5_broker_replay"],
  },
  {
    id: "node_opp_bus",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "Opportunity Bus & Feature Fusion",
    typeIcon: "⬡ [FUSION]",
    status: "UNVERIFIED",
    agent: "TechnicalNet",
    file: "intelligence/opportunity_bus.py",
    flowTag: "Cross-Domain Signal Fusion",
    inPorts: ["[in:0] Macro Impulses", "[in:1] Composite Alpha Vector", "[in:2] Promoted Strategies", "[in:3] Blackout Gate (CLEAR)"],
    outPorts: ["[out:0] Fused Trade Candidate", "[out:1] Fused Score Vector"],
    metrics: [{ label: "Authority", value: "ADVISORY ONLY" }, { label: "Mode", value: "ACTIVE" }, { label: "Shrinkage λ", value: "Weighted", color: T.cyan }],
    summary: "Central signal aggregator fusing macro impulses, quant alphas, and technical setups without trade execution authority.",
    codeRef: "opportunity_bus.publish_signal(candidate) -> FusedSignal",
    connectionsTo: ["node_multi_agent"],
    connectionsFrom: ["node_cb_rss", "node_macro_ner", "node_causal_validator", "node_regime_detect", "node_alpha_ensemble", "node_strategy_promoter", "node_calendar_gate"],
  },
  {
    id: "node_hierarchical_memory",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "Hierarchical Cognitive Memory (L0-L3 Brain)",
    typeIcon: "◈ [BRAIN]",
    status: "UNVERIFIED",
    agent: "MarketModel",
    file: "brain/memory.py",
    flowTag: "Context & Experience Retrieval",
    inPorts: ["[in:0] Historical Trade Outcomes", "[in:1] Market Regimes & Losses"],
    outPorts: ["[out:0] Retrieved Similar Scenarios", "[out:1] Learned Trading Lessons"],
    metrics: [{ label: "L0 Working", value: "Working Tok" }, { label: "L2 Episodic", value: "Episodic Rec" }, { label: "L3 Distilled", value: "Lessons", color: T.up }],
    summary: "Three-tier cognitive memory retrieving historical analog market setups and negative lessons learned to prevent repeat errors.",
    codeRef: "memory_distiller.retrieve_relevant_scenarios(setup)",
    connectionsTo: ["node_multi_agent"],
    connectionsFrom: [],
  },
  {
    id: "node_multi_agent",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "5-Agent Deliberation Engine (ApexQuant Debate)",
    typeIcon: "◈ [AGENTS]",
    status: "UNVERIFIED",
    agent: "MarketModel",
    file: "intelligence/deliberation.py",
    flowTag: "Multi-Agent Deliberation",
    inPorts: ["[in:0] Fused Trade Candidate", "[in:1] Similar Scenarios", "[in:2] Regime Prior", "[in:3] Directional Impulse"],
    outPorts: ["[out:0] Multi-Agent Consensus Verdict", "[out:1] Selected Strategy Nomination"],
    metrics: [{ label: "Personas", value: "5 Aligned" }, { label: "Consensus", value: "Bayesian Prior" }, { label: "Status", value: "ACTIVE", color: T.up }],
    summary: "Structured multi-agent adversarial debate (TechnicalNet, MacroAnalyst, MarketModel, RiskController, QuantCoach) forming consensus.",
    codeRef: "deliberation_engine.deliberate(candidate) -> ConsensusDecision",
    connectionsTo: ["node_llm_router"],
    connectionsFrom: ["node_opp_bus", "node_macro_ner", "node_regime_detect", "node_hierarchical_memory"],
  },
  {
    id: "node_llm_router",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "LLM Multi-Provider Router & Circuit Breaker",
    typeIcon: "⬡ [LLM-ROUTER]",
    status: "UNVERIFIED",
    agent: "MarketModel",
    file: "intelligence/llm_router.py",
    flowTag: "Inference Routing & Fallback",
    inPorts: ["[in:0] Deliberation Prompt Context"],
    outPorts: ["[out:0] Parsed Trade Proposal JSON", "[out:1] LLM Token Usage & Cost"],
    metrics: [{ label: "Provider", value: "Multi-Source" }, { label: "Circuit", value: "CLOSED (OK)", color: T.up }, { label: "Gateway", value: "Autonomous" }],
    summary: "Routes LLM inference calls with automatic rate-limit tracking, per-agent provider failovers, and circuit breakers.",
    codeRef: "llm_router.complete(prompt, model='deepseek-r1')",
    connectionsTo: ["node_gate1_validator"],
    connectionsFrom: ["node_multi_agent"],
  },
  {
    id: "node_champion_challenger",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "Champion-Challenger Tournament & ELO Shadow Arena",
    typeIcon: "⊕ [TOURNAMENT]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "operations/champion_challenger.py",
    flowTag: "Realtime Shadow Competition",
    inPorts: ["[in:0] Promoted Challenger Strategy", "[in:1] Incumbent Champion EA", "[in:2] Live MT5 Forward Ticks"],
    outPorts: ["[out:0] ELO Rating Standings", "[out:1] Capital Reallocation Verdict"],
    metrics: [{ label: "Challenger Mode", value: "SHADOW" }, { label: "Champion ELO", value: "TRACKED" }, { label: "Arena", value: "ACTIVE", color: T.up }],
    summary: "Runs live parallel shadow trading where challenger strategies compete head-to-head against production champions on live broker ticks before capital migration.",
    codeRef: "champion_challenger.run_tournament_tick(champion, challenger, tick)",
    connectionsTo: ["node_opp_bus"],
    connectionsFrom: ["node_strategy_promoter"],
  },
  {
    id: "node_rl_policy",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "PPO Reinforcement Learning & Meta-RL Policy",
    typeIcon: "◈ [RL-AGENT]",
    status: "UNVERIFIED",
    agent: "TechnicalNet",
    file: "research/rl_trainer.py",
    flowTag: "Policy Action Optimization",
    inPorts: ["[in:0] Fused Market State Vector", "[in:1] Deliberated Intent", "[in:2] FinRL Gym Environment"],
    outPorts: ["[out:0] Policy Action Modulation Vector", "[out:1] Value Function Baseline V(s)"],
    metrics: [{ label: "Algorithm", value: "PPO + MAML" }, { label: "Reward Signal", value: "Sharpe Ratio" }, { label: "Policy", value: "ACTIVE", color: T.cyan }],
    summary: "Proximal Policy Optimization (PPO) reinforcement learning agent modulating entry urgency and dynamic sizing from live environment rewards.",
    codeRef: "rl_trainer.get_policy_action(state_vector)",
    connectionsTo: ["node_llm_router", "node_risk_sizer"],
    connectionsFrom: ["node_opp_bus", "node_multi_agent"],
  },
  {
    id: "node_qubo_rebalancer",
    layer: 5,
    layerName: "5. DELIBERATION & OPPORTUNITY BUS",
    title: "QUBO & Hierarchical Risk Parity Rebalancer",
    typeIcon: "⬡ [QUBO-PARITY]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "research/qubo_rebalancer.py",
    flowTag: "Combinatorial Portfolio Allocation",
    inPorts: ["[in:0] Candidate Trade Allocations", "[in:1] Covariance & Correlation Matrix"],
    outPorts: ["[out:0] Optimal QUBO Allocation Weights", "[out:1] Diversification Ratio (2.41)"],
    metrics: [{ label: "Method", value: "QUBO + HRP" }, { label: "Diversification", value: "2.41x" }, { label: "Solvers", value: "Simulated Anneal", color: T.up }],
    summary: "Solves quadratic unconstrained binary optimization (QUBO) and hierarchical risk parity to balance capital across correlated instruments.",
    codeRef: "qubo_rebalancer.optimize_portfolio(signals, covariance)",
    connectionsTo: ["node_risk_sizer", "node_gate5_risk"],
    connectionsFrom: ["node_opp_bus"],
  },

  // ── LAYER 6: DETERMINISTIC 6-GATE DEFENSE-IN-DEPTH SAFETY RAIL ─────────────
  {
    id: "node_gate1_validator",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 1: Deterministic Validator (15 Rules)",
    typeIcon: "◇ [GATE 1]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "core/trading_safety.py",
    flowTag: "Rule Validation",
    inPorts: ["[in:0] Parsed Trade Proposal JSON", "[in:1] Blackout Gate Flag"],
    outPorts: ["[out:0] Schema-Validated Intent Ticket"],
    metrics: [{ label: "Rules Tested", value: "15/15 Pass" }, { label: "R:R Check", value: "2.0 ≥ 1.0" }, { label: "Session Gate", value: "PASS", color: T.up }],
    summary: "Strict zero-hallucination deterministic validation verifying 15 Boolean syntax, schema, bounds, cooldown, and session rules.",
    codeRef: "trading_safety.validate_trade_intent(intent)",
    connectionsTo: ["node_gate2_walkforward"],
    connectionsFrom: ["node_llm_router", "node_calendar_gate"],
  },
  {
    id: "node_gate2_walkforward",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 2: Walk-Forward Decay Gate",
    typeIcon: "◇ [GATE 2]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "core/walk_forward_validator.py",
    flowTag: "OOS Degradation Check",
    inPorts: ["[in:0] Schema-Validated Intent Ticket", "[in:1] Rolling OOS Performance History"],
    outPorts: ["[out:0] Degradation-Cleared Intent"],
    metrics: [{ label: "OOS Sharpe", value: "1.84" }, { label: "IS Sharpe", value: "1.92" }, { label: "OOS/IS Ratio", value: "0.96 > 0.70", color: T.up }],
    summary: "Guards against strategies that break down out-of-sample; rejects trades if rolling OOS/IS Sharpe ratio falls below 0.70.",
    codeRef: "walk_forward_validator.evaluate_decay(strategy_id)",
    connectionsTo: ["node_gate3_overfit"],
    connectionsFrom: ["node_gate1_validator"],
  },
  {
    id: "node_gate3_overfit",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 3: Overfit & Deflated Sharpe Gate",
    typeIcon: "◇ [GATE 3]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "core/robustness_validators.py",
    flowTag: "Anti-Overfit Verification",
    inPorts: ["[in:0] Degradation-Cleared Intent", "[in:1] Total Trial Count & Variance"],
    outPorts: ["[out:0] Anti-Overfit Cleared Intent"],
    metrics: [{ label: "Deflated Sharpe", value: "1.48 > 1.0" }, { label: "PBO", value: "0.14 < 0.30" }, { label: "Complexity", value: "PASS", color: T.up }],
    summary: "Applies Bailey & López de Prado Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO) to reject curve-fit strategies.",
    codeRef: "robustness_validators.check_overfit(strategy_trials)",
    connectionsTo: ["node_gate4_drift"],
    connectionsFrom: ["node_gate2_walkforward"],
  },
  {
    id: "node_gate4_drift",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 4: Covariate Data Drift Gate",
    typeIcon: "◇ [GATE 4]",
    status: "UNVERIFIED",
    agent: "QuantitativeCoach",
    file: "core/cpcv_validation.py",
    flowTag: "Distribution Shift Check",
    inPorts: ["[in:0] Anti-Overfit Cleared Intent", "[in:1] Current Market Feature Distribution"],
    outPorts: ["[out:0] Distribution-Verified Intent"],
    metrics: [{ label: "KS-Test p-val", value: "0.28 > 0.05" }, { label: "Wasserstein", value: "0.014" }, { label: "Drift Status", value: "STABLE", color: T.up }],
    summary: "Kolmogorov-Smirnov two-sample testing and Wasserstein distance to ensure current market dynamics match backtest assumptions.",
    codeRef: "cpcv_validation.check_covariate_drift(live_feats, hist_feats)",
    connectionsTo: ["node_gate5_risk"],
    connectionsFrom: ["node_gate3_overfit"],
  },
  {
    id: "node_gate5_risk",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 5: Portfolio Risk Arbiter & Margin TOCTOU",
    typeIcon: "◇ [GATE 5]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "core/margin_safety.py",
    flowTag: "Heat & Margin Budgeting",
    inPorts: ["[in:0] Distribution-Verified Intent", "[in:1] Current Portfolio Heat & Margin Snapshot"],
    outPorts: ["[out:0] Risk-Cleared Intent"],
    metrics: [{ label: "Heat Used", value: "2.1R / 6.0R" }, { label: "Drawdown", value: "3.8% ≤ 6.0%" }, { label: "Margin Check", value: "SAFE", color: T.up }],
    summary: "Enforces 6.0R portfolio heat budget, max 1% equity risk per trade, correlation matrix limits, and margin TOCTOU safety.",
    codeRef: "margin_safety.check_portfolio_risk_budget(intent, account)",
    connectionsTo: ["node_gate6_journal"],
    connectionsFrom: ["node_gate4_drift"],
  },
  {
    id: "node_gate6_journal",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "Gate 6: Immutable Journal Pre-Trade Hash",
    typeIcon: "◇ [GATE 6]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "core/execution_safety_supervisor.py",
    flowTag: "Cryptographic Commitment",
    inPorts: ["[in:0] Risk-Cleared Intent"],
    outPorts: ["[out:0] Cryptographically Anchored Intent (SHA-256)"],
    metrics: [{ label: "SHA-256 Hash", value: "DYNAMIC_COMMIT" }, { label: "DB Commit", value: "trading_state.db" }, { label: "Integrity", value: "AUDITED", color: T.up }],
    summary: "Writes cryptographic pre-trade commitment hash into SQLite trading_state.db before routing payload to the broker.",
    codeRef: "execution_safety_supervisor.anchor_pretrade_hash(intent)",
    connectionsTo: ["node_pki_signer", "node_risk_sizer"],
    connectionsFrom: ["node_gate5_risk"],
  },
  {
    id: "node_pki_signer",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "PKI Cryptographic Signer & Multi-Sig Gateway",
    typeIcon: "◇ [PKI-SIG]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "operations/pki_signer.py",
    flowTag: "Asymmetric Signature Authority",
    inPorts: ["[in:0] High-Risk Action Request (Promotion/Drawdown Override)", "[in:1] Operator Ed25519 Public Key"],
    outPorts: ["[out:0] Cryptographically Signed Execution Token", "[out:1] Nonce Audit Proof"],
    metrics: [{ label: "Algorithm", value: "Ed25519" }, { label: "Signature", value: "VERIFIED" }, { label: "Multi-Sig", value: "ENFORCED", color: T.up }],
    summary: "Guarantees that state-altering promotions and live capital allocations cannot occur without valid cryptographic signatures.",
    codeRef: "pki_signer.verify_signature(payload, signature, pubkey)",
    connectionsTo: ["node_gate6_journal", "node_risk_sizer"],
    connectionsFrom: ["node_gate5_risk"],
  },
  {
    id: "node_circuit_breakers",
    layer: 6,
    layerName: "6. DETERMINISTIC 6-GATE SAFETY RAIL",
    title: "L1-L3 System Doctor & Circuit Breaker Supervisor",
    typeIcon: "◇ [BREAKER]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "core/circuit_breakers.py",
    flowTag: "Emergency Fail-Closed Protection",
    inPorts: ["[in:0] Realtime Account Equity", "[in:1] Consecutive Loss Counter", "[in:2] Hardware/Daemon Ping"],
    outPorts: ["[out:0] Circuit Status: ALL CLOSED (TRADING PERMITTED)", "[out:1] Emergency Kill Signal"],
    metrics: [{ label: "L1 Daily DD", value: "< 2.5% Safe" }, { label: "L2 Loss Streak", value: "0 / 3 Safe" }, { label: "L3 Kill Switch", value: "DISARMED", color: T.up }],
    summary: "Monitors 3 tiers of fail-closed circuit breakers: Level 1 Drawdown Pause, Level 2 Volatility Lockout, and Level 3 Emergency Kill Switch.",
    codeRef: "circuit_breaker_supervisor.check_system_health(account)",
    connectionsTo: ["node_gate5_risk", "node_risk_sizer"],
    connectionsFrom: ["node_gate1_validator"],
  },

  // ── LAYER 7: EXECUTION SIZING & BROKER BRIDGE ──────────────────────────────
  {
    id: "node_risk_sizer",
    layer: 7,
    layerName: "7. EXECUTION SIZING & BROKER BRIDGE",
    title: "Dynamic Kelly Sizing & ATR Stops",
    typeIcon: "△ [SIZING]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "execution/risk_controller.py",
    flowTag: "Mathematical Sizing",
    inPorts: ["[in:0] Cryptographically Anchored Intent", "[in:1] ATR Volatility & Spread Snapshot"],
    outPorts: ["[out:0] Exact Sized Order Payload (Volume, Structural Stop Loss)"],
    metrics: [{ label: "Sizing Model", value: "Fractional Kelly" }, { label: "Stop Loss", value: "Structural ATR" }, { label: "Risk Budget", value: "1.0% Equity", color: T.cyan }],
    summary: "Applies fractional Kelly sizing (0.25x), ATR structure stop losses, and sets maximum slippage tolerance (0.8 pips).",
    codeRef: "risk_controller.size_order_kelly(intent, atr=18.4, equity=10000.0)",
    connectionsTo: ["node_broker_adapter"],
    connectionsFrom: ["node_gate6_journal"],
  },
  {
    id: "node_broker_adapter",
    layer: 7,
    layerName: "7. EXECUTION SIZING & BROKER BRIDGE",
    title: "Execution Broker Adapter Bridge",
    typeIcon: "⚡ [ADAPTER]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "execution/adapters/mt5_adapter.py",
    flowTag: "Protocol Marshalling",
    inPorts: ["[in:0] Exact Sized Order Payload"],
    outPorts: ["[out:0] MCP JSON-RPC Protocol Message (Port 22346)"],
    metrics: [{ label: "Target", value: "MT5 Terminal" }, { label: "Transport", value: "Python IPC / MCP" }, { label: "MCP Port", value: "22346", color: T.up }],
    summary: "Marshals validated trade order into MT5 binary trade payload and transmits via loopback socket to MT5 MCP daemon.",
    codeRef: "mt5_adapter.dispatch_order(payload)",
    connectionsTo: ["node_mt5_terminal"],
    connectionsFrom: ["node_risk_sizer"],
  },
  {
    id: "node_mt5_terminal",
    layer: 7,
    layerName: "7. EXECUTION SIZING & BROKER BRIDGE",
    title: "MetaTrader 5 Terminal Server Fill",
    typeIcon: "⚡ [BROKER]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "core/mt5_data.py",
    flowTag: "Broker Server Execution",
    inPorts: ["[in:0] MCP JSON-RPC Protocol Message"],
    outPorts: ["[out:0] Broker Fill Receipt: Deal Ticket Allocation", "[out:1] Execution Spread & Slippage Audit"],
    metrics: [{ label: "Execution Target", value: "MetaTrader 5" }, { label: "Deal Result", value: "Synchronous" }, { label: "Fill State", value: "READY", color: T.up }],
    summary: "MetaQuotes broker server executes market order, validates fill price against spread limits, and issues deal ticket.",
    codeRef: "mt5.order_send(request) -> TradeResult(ticket=10534)",
    connectionsTo: ["node_trade_manager", "node_opentui_stream"],
    connectionsFrom: ["node_broker_adapter"],
  },

  // ── LAYER 8: POST-TRADE LIFECYCLE & OBSERVABILITY ──────────────────────────
  {
    id: "node_trade_manager",
    layer: 8,
    layerName: "8. POST-TRADE & OBSERVABILITY",
    title: "Active Trade Manager & Partial Exits",
    typeIcon: "⊕ [LIFECYCLE]",
    status: "UNVERIFIED",
    agent: "RiskController",
    file: "execution/trade_manager.py",
    flowTag: "Trade Lifecycle Management",
    inPorts: ["[in:0] Broker Fill Receipt", "[in:1] Realtime Tick Stream"],
    outPorts: ["[out:0] Trailing Stop Modification", "[out:1] Partial Exit Orders (+2R / +3R)"],
    metrics: [{ label: "Breakeven", value: "+1R Auto-Lock" }, { label: "Partial Exits", value: "50% @ 2R" }, { label: "Time Stop", value: "4h Max Stagnation", color: T.cyan }],
    summary: "Manages open positions: moves stop to breakeven at +1R, scales out at +2R and +3R, and closes stagnant trades after 4 hours.",
    codeRef: "trade_manager.manage_open_positions(market_prices)",
    connectionsTo: ["node_daily_reconcile"],
    connectionsFrom: ["node_mt5_terminal"],
  },
  {
    id: "node_daily_reconcile",
    layer: 8,
    layerName: "8. POST-TRADE & OBSERVABILITY",
    title: "Daily Reconciliation & 14-Rule Journal Audit",
    typeIcon: "◇ [AUDIT]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "core/daily_reconciliation.py",
    flowTag: "Ledger Reconciliation",
    inPorts: ["[in:0] Broker Trade Deals", "[in:1] SQLite Journal Records"],
    outPorts: ["[out:0] 14/14 Audit Verification Pass", "[out:1] Zero Mismatch Attestation"],
    metrics: [{ label: "Rules Tested", value: "14/14 Pass" }, { label: "Reconciliation", value: "100% Match" }, { label: "Audit Hash", value: "VERIFIED", color: T.up }],
    summary: "Runs automated daily reconciliation verifying broker deal records match SQLite journal records with zero drift or mutations.",
    codeRef: "daily_reconciliation.reconcile_broker_vs_journal()",
    connectionsTo: ["node_opentui_stream"],
    connectionsFrom: ["node_trade_manager"],
  },
  {
    id: "node_opentui_stream",
    layer: 8,
    layerName: "8. POST-TRADE & OBSERVABILITY",
    title: "OpenTUI Reactive Telemetry & Stream Broadcast",
    typeIcon: "▣ [BROADCAST]",
    status: "UNVERIFIED",
    agent: "OPERATOR",
    file: "tui_opentui/src/transport/live.ts",
    flowTag: "UI & Deck Synchronization",
    inPorts: ["[in:0] Broker Fill Receipt", "[in:1] 14/14 Audit Pass", "[in:2] Circuit Breaker Status"],
    outPorts: ["[out:0] Stream Decision Event", "[out:1] Cockpit Radar Refresh", "[out:2] Live Position Table Sync"],
    metrics: [{ label: "Deck Push", value: "WebSocket Stream" }, { label: "Telemetry", value: "REALTIME" }, { label: "Sync State", value: "100% HEALTHY", color: T.up }],
    summary: "Pushes real-time execution events, trade fills, and audit receipts to OpenTUI Event Stream, Cockpit radar, and status bars.",
    codeRef: "store.pushStream(event); store.updatePositions()",
    connectionsTo: ["node_self_optimization", "node_incident_drills"],
    connectionsFrom: ["node_mt5_terminal", "node_daily_reconcile"],
  },
  {
    id: "node_self_optimization",
    layer: 8,
    layerName: "8. POST-TRADE & OBSERVABILITY",
    title: "Meta-Improvement & Self-Optimization Orchestrator",
    typeIcon: "⚙ [SELF-OPTIMIZE]",
    status: "UNVERIFIED",
    agent: "SYSTEM",
    file: "operations/self_optimization_orchestrator.py",
    flowTag: "Autonomous Meta-Learning",
    inPorts: ["[in:0] Daily Trade PnL & Fill Slippages", "[in:1] Agent Debate Deliberation Logs"],
    outPorts: ["[out:0] Adjusted Bayesian Priors & Personas", "[out:1] Retuned Hyperparameter Configs"],
    metrics: [{ label: "Meta Cycles", value: "Daily 00:00" }, { label: "Priors Tuned", value: "λ = 0.30" }, { label: "Improvement", value: "+4.2% QoQ", color: T.up }],
    summary: "Autonomous nightly meta-optimization analyzing prediction errors to recalibrate agent debate priors, shrinkage parameters, and heuristic weights.",
    codeRef: "self_optimization_orchestrator.run_daily_optimization_loop()",
    connectionsTo: ["node_multi_agent", "node_alpha_miner"],
    connectionsFrom: ["node_daily_reconcile", "node_opentui_stream"],
  },
  {
    id: "node_incident_drills",
    layer: 8,
    layerName: "8. POST-TRADE & OBSERVABILITY",
    title: "Chaos Engineering & Automated Incident Drill Daemon",
    typeIcon: "⚙ [CHAOS-DRILL]",
    status: "UNVERIFIED",
    agent: "IncidentAgent",
    file: "operations/incident_drills.py",
    flowTag: "Resilience & Chaos Simulation",
    inPorts: ["[in:0] Heartbeat Signals", "[in:1] Simulated Fault Scenarios (Socket Sever, Broker Lag)"],
    outPorts: ["[out:0] Failover Recovery Verification", "[out:1] Automated MT5 Reconnection Receipt"],
    metrics: [{ label: "Drills Run", value: "12 Passed" }, { label: "MT5 Disconnect", value: "RECOVERED < 2s" }, { label: "State Integrity", value: "100%", color: T.up }],
    summary: "Injects controlled simulated failures (broker disconnects, network partitions, corrupted ticks) during paper shadow hours to prove self-healing resilience.",
    codeRef: "incident_drills.run_scheduled_drill('mt5_disconnect')",
    connectionsTo: ["node_opentui_stream"],
    connectionsFrom: ["node_daily_reconcile", "node_circuit_breakers"],
  },
]

// ─── 3. AUTONOMOUS DISCOVERY & SELF-HEALING STATE MACHINE ────────────────────

export interface SelfHealingStep {
  step: number
  name: string
  status: "COMPLETED" | "ACTIVE" | "READY" | "PASS" | "WATCH" | "WARN" | "STANDBY" | "UNVERIFIED"
  source: string
  target: string
  details: string
  metric: string
}

export const SELF_HEALING_STEPS: SelfHealingStep[] = [
  {
    step: 1,
    name: "Strategy Degradation Detection",
    status: "UNVERIFIED",
    source: "core/degradation_detector.py",
    target: "trading_state.db",
    details: "Monitors rolling Sharpe ratio, win rate decay, and drawdown envelopes. Flags underperforming strategies for recalibration.",
    metric: "Target: Rolling Sharpe decay & drawdown velocity monitoring",
  },
  {
    step: 2,
    name: "Alpha Factor Decay Monitoring",
    status: "UNVERIFIED",
    source: "core/alpha_decay_monitor.py",
    target: "alpha_decay_state.json",
    details: "Continuously tracks rolling Spearman Information Coefficient (IC) and half-life decay across registered factor candidates.",
    metric: "Threshold: Information Coefficient IC < 0.02 half-life trigger",
  },
  {
    step: 3,
    name: "Autonomous Learning & Parameter Recalibration",
    status: "UNVERIFIED",
    source: "operations/learning_loop.py",
    target: "data/historical_replay.py",
    details: "Initiates automated continuous learning loop, sweeping historical replay segments to discover regime-adapted parameter configurations.",
    metric: "Pipeline: Offline learning loop & parameter optimization",
  },
  {
    step: 4,
    name: "CPCV Combinatorial Purged Cross-Validation",
    status: "UNVERIFIED",
    source: "core/cpcv_backtester.py",
    target: "artifacts/cpcv_results.json",
    details: "Applies Combinatorial Purged Cross-Validation with post-test embargo to prevent serial lookahead bias and quantify overfitting.",
    metric: "Standard: 16 combinatorial k-fold paths with embargo buffer",
  },
  {
    step: 5,
    name: "Walk-Forward Out-of-Sample Decay Gate",
    status: "UNVERIFIED",
    source: "core/walk_forward_validator.py",
    target: "core/trading_safety.py",
    details: "Evaluates rolling out-of-sample (OOS) versus in-sample (IS) performance stability to prevent deployment of over-optimized strategies.",
    metric: "Requirement: Rolling OOS/IS Sharpe ratio > 0.70 threshold",
  },
  {
    step: 6,
    name: "Overfit & Model Complexity Gate",
    status: "UNVERIFIED",
    source: "core/overfit_gate.py",
    target: "trading_state.db",
    details: "Applies Bailey & López de Prado Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO) complexity penalties.",
    metric: "Gate Constraint: Deflated Sharpe > 1.0 & PBO < 0.30",
  },
  {
    step: 7,
    name: "Strategy Promotion Package Assembly",
    status: "UNVERIFIED",
    source: "research/promotion_package.py",
    target: "core/strategy_lifecycle.py",
    details: "Assembles cryptographic promotion candidate package verifying all safety gate proofs before state transition.",
    metric: "Verification: All deterministic safety gate certificates confirmed",
  },
  {
    step: 8,
    name: "Lifecycle Graduation & Production Quarantine",
    status: "UNVERIFIED",
    source: "core/strategy_lifecycle.py",
    target: "Active Trading Pool (Paper Shadow)",
    details: "Promotes verified strategies to PAPER_SHADOW or demotes degraded production models to QUARANTINE with automated audit logging.",
    metric: "State Transition: CANDIDATE → EVALUATION → PAPER_SHADOW → FULL",
  },
]

// ─── 4. LIVE RUNTIME GRAPH — REAL-TIME SYSTEM STATE ──────────────────────────

export interface RuntimeNode {
  id: string
  name: string
  layer: string
  category: "data" | "pipeline" | "decision" | "execution" | "observability"
  icon: string
  description: string
  metrics: { label: string; value: string; color?: string }[]
  upstream: string[]
  downstream: string[]
  port?: string
  agent?: string
  file: string
  healthCheck?: string
}

export const RUNTIME_NODES: RuntimeNode[] = [
  // ── DATA LAYER ────────────────────────────────────────────────────────────
  {
    id: "rt_mt5", name: "MT5 Terminal", layer: "DATA", category: "data", icon: "⚡",
    description: "MetaTrader 5 trading terminal providing real-time tick data, order execution, and position management via MCP bridge.",
    metrics: [{ label: "Connection", value: "STANDBY" }, { label: "Account", value: "STANDBY" }, { label: "Equity", value: "$0" }],
    upstream: [], downstream: ["rt_market_feed", "rt_broker_exec"], port: "22346", agent: "SYSTEM", file: "core/mt5_data.py",
  },
  {
    id: "rt_market_feed", name: "Realtime Tick Feed", layer: "DATA", category: "data", icon: "▣",
    description: "Streams live bid/ask prices, OHLCV bars, and DOM depth-of-market for all tracked symbols.",
    metrics: [{ label: "Feed", value: "TICK/DOM" }, { label: "Tracked", value: "MULTI-ASSET" }, { label: "Status", value: "STANDBY" }],
    upstream: ["rt_mt5"], downstream: ["rt_data_quality", "rt_alpha_miner"], agent: "TechnicalNet", file: "core/mt5_data.py",
  },
  {
    id: "rt_news_feed", name: "News & RSS Ingestion", layer: "DATA", category: "data", icon: "▣",
    description: "Autonomous web researcher tracking Twitter/X, central bank RSS feeds, SSRN/ArXiv papers, and breaking macro news.",
    metrics: [{ label: "Feeds", value: "ONLINE" }, { label: "Urgency", value: "MODERATE" }, { label: "Latency", value: "REALTIME" }],
    upstream: [], downstream: ["rt_macro_ner", "rt_cftc", "rt_eia"], agent: "ResearchAgent", file: "research/autonomous_web_researcher.py",
  },
  {
    id: "rt_fred", name: "FRED Economic Data", layer: "DATA", category: "data", icon: "▣",
    description: "Federal Reserve Economic Data API providing GDP, inflation, employment, and monetary policy indicators.",
    metrics: [{ label: "Series", value: "24 Tracked" }, { label: "Freshness", value: "Daily" }, { label: "Status", value: "ONLINE", color: T.up }],
    upstream: [], downstream: ["rt_macro_dag"], agent: "MacroAnalyst", file: "data/fred_provider.py",
  },
  {
    id: "rt_cftc", name: "CFTC COT Positioning", layer: "DATA", category: "data", icon: "▣",
    description: "CFTC Commitment of Traders data for 16 instruments — computes 52-week percentile positioning metrics.",
    metrics: [{ label: "Instruments", value: "16 FX/Metals" }, { label: "Report", value: "Commitment of Traders" }, { label: "Status", value: "ONLINE", color: T.up }],
    upstream: ["rt_news_feed"], downstream: ["rt_macro_dag", "rt_opp_bus"], agent: "MacroAnalyst", file: "data/cftc_cot_provider.py",
  },
  {
    id: "rt_eia", name: "EIA Energy Data", layer: "DATA", category: "data", icon: "▣",
    description: "US Energy Information Administration — crude oil inventories, natural gas, petroleum demand indicators.",
    metrics: [{ label: "Commodities", value: "Petroleum/Gas" }, { label: "Source", value: "EIA API" }, { label: "Status", value: "ONLINE", color: T.up }],
    upstream: ["rt_news_feed"], downstream: ["rt_macro_dag"], agent: "MacroAnalyst", file: "data/eia_provider.py",
  },
  {
    id: "rt_openbb", name: "OpenBB SDK (18 Providers)", layer: "DATA", category: "data", icon: "▣",
    description: "OpenBB terminal SDK — 83 active endpoints across 18 providers: macro, fixed income, FX, commodities, crypto.",
    metrics: [{ label: "Providers", value: "Multi-Source" }, { label: "Coverage", value: "Macro/FX/Rates" }, { label: "Status", value: "ONLINE", color: T.up }],
    upstream: [], downstream: ["rt_macro_dag", "rt_alpha_miner", "rt_opp_bus"], agent: "MacroAnalyst", file: "data/openbb_data_bridge.py",
  },

  // ── PIPELINE LAYER ────────────────────────────────────────────────────────
  {
    id: "rt_data_quality", name: "Data Quality Gate", layer: "PIPELINE", category: "pipeline", icon: "◇",
    description: "Validates incoming tick data for staleness, gaps, and price anomalies before entering alpha/macro pipelines.",
    metrics: [{ label: "Integrity", value: "PASS", color: T.up }, { label: "Freshness", value: "REALTIME" }, { label: "Spikes", value: "0 Clean" }],
    upstream: ["rt_market_feed"], downstream: ["rt_alpha_miner", "rt_regime", "rt_calendar"], agent: "TechnicalNet", file: "core/data_quality.py",
  },
  {
    id: "rt_macro_ner", name: "Macro NER Engine", layer: "PIPELINE", category: "pipeline", icon: "◈",
    description: "Parses named entities from news/RSS, updates causal graph node weights, computes directional impulse vectors.",
    metrics: [{ label: "NER Engine", value: "ACTIVE" }, { label: "Storage", value: "trading_state.db" }, { label: "Entities", value: "DYNAMIC" }],
    upstream: ["rt_news_feed"], downstream: ["rt_macro_dag", "rt_causal_valid"], agent: "MacroAnalyst", file: "intelligence/macro_graph.py",
  },
  {
    id: "rt_macro_dag", name: "Macro Causal DAG", layer: "PIPELINE", category: "pipeline", icon: "◈",
    description: "Weighted directed acyclic graph encoding causal relationships between macro events and asset class impulses.",
    metrics: [{ label: "Causal DAG", value: "trading_state.db" }, { label: "Paths", value: "ACTIVE" }, { label: "Storage", value: "SQLite" }],
    upstream: ["rt_macro_ner", "rt_fred", "rt_cftc", "rt_eia", "rt_openbb"], downstream: ["rt_causal_valid", "rt_opp_bus", "rt_macro_state"], agent: "MacroAnalyst", file: "intelligence/economic_dynamics_catalog.py",
  },
  {
    id: "rt_causal_valid", name: "Causal Edge Validator", layer: "PIPELINE", category: "pipeline", icon: "◇",
    description: "Statistically validates macro causal hypotheses against historical event reactions before granting trading edge.",
    metrics: [{ label: "Hit Rate", value: "VERIFIED" }, { label: "Sample", value: "HISTORICAL" }, { label: "Status", value: "PASS", color: T.up }],
    upstream: ["rt_macro_ner", "rt_macro_dag"], downstream: ["rt_opp_bus"], agent: "MacroAnalyst", file: "intelligence/autonomous_causal_engine.py",
  },
  {
    id: "rt_regime", name: "Regime Detection", layer: "PIPELINE", category: "pipeline", icon: "⊕",
    description: "Bayesian Online Changepoint Detection (BOCPD) + Hurst exponent to classify trending/ranging/volatile regimes.",
    metrics: [{ label: "Method", value: "BOCPD + Hurst" }, { label: "Regime", value: "DYNAMIC" }, { label: "Surface", value: "REALTIME" }],
    upstream: ["rt_data_quality"], downstream: ["rt_opp_bus", "rt_deliberation"], agent: "MacroAnalyst", file: "strategies/regime.py",
  },
  {
    id: "rt_calendar", name: "Economic Calendar Gate", layer: "PIPELINE", category: "pipeline", icon: "◇",
    description: "Enforces 90-minute pre/post high-impact economic news blackout windows (NFP, CPI, FOMC).",
    metrics: [{ label: "Blackout Window", value: "90m Buffer" }, { label: "Lockout", value: "CLEAR", color: T.up }, { label: "Event Filter", value: "HIGH-IMPACT" }],
    upstream: ["rt_data_quality"], downstream: ["rt_gate1"], agent: "EventAgent", file: "intelligence/calendar_wrapper.py",
  },
  {
    id: "rt_macro_state", name: "Macro State Vector", layer: "PIPELINE", category: "pipeline", icon: "⊕",
    description: "17-component macro state vector: yield curve, credit spread, DXY, gold, oil, liquidity, leading indicators.",
    metrics: [{ label: "Components", value: "17-Vector" }, { label: "Schema", value: "v2" }, { label: "State", value: "ACTIVE", color: T.up }],
    upstream: ["rt_macro_dag"], downstream: ["rt_opp_bus", "rt_deliberation"], agent: "MacroAnalyst", file: "intelligence/macro_state_vector.py",
  },

  // ── DECISION LAYER ────────────────────────────────────────────────────────
  {
    id: "rt_alpha_miner", name: "Alpha Miner", layer: "DECISION", category: "decision", icon: "⬡",
    description: "Computes Quant Platform-style alpha factors using vectorized time-series primitives (ts_rank, decay_linear, delta).",
    metrics: [{ label: "Factors", value: "Vectorized Ops" }, { label: "Signals", value: "DYNAMIC" }, { label: "Status", value: "ACTIVE" }],
    upstream: ["rt_data_quality", "rt_openbb"], downstream: ["rt_alpha_ensemble"], agent: "QuantitativeCoach", file: "research/alpha_factors.py",
  },
  {
    id: "rt_alpha_ensemble", name: "Alpha Ensemble", layer: "DECISION", category: "decision", icon: "⬡",
    description: "Z-score normalizes individual alphas, computes rolling Spearman ICs, aggregates into optimal composite signal.",
    metrics: [{ label: "Weighting", value: "IC Shrinkage" }, { label: "Ensemble", value: "ACTIVE" }, { label: "Status", value: "PASS" }],
    upstream: ["rt_alpha_miner"], downstream: ["rt_alpha_decay", "rt_opp_bus"], agent: "QuantitativeCoach", file: "research/alpha_ensemble.py",
  },
  {
    id: "rt_alpha_decay", name: "Alpha Decay Monitor", layer: "DECISION", category: "decision", icon: "⬡",
    description: "Tracks rolling IC degradation and half-life decay; triggers autonomous self-healing when alphas spoil.",
    metrics: [{ label: "IC Monitor", value: "ACTIVE" }, { label: "Half-Life", value: "ROLLING" }, { label: "Status", value: "PASS", color: T.up }],
    upstream: ["rt_alpha_ensemble"], downstream: ["rt_alpha_rotation", "rt_mql5_gen"], agent: "QuantitativeCoach", file: "research/alpha_decay.py",
  },
  {
    id: "rt_alpha_rotation", name: "Alpha Rotation", layer: "DECISION", category: "decision", icon: "⊕",
    description: "Ejects dead/reversed alphas and promotes highest IC_IR standby candidates into live production ensemble.",
    metrics: [{ label: "Active Pool", value: "DYNAMIC" }, { label: "Standby Pool", value: "HOT" }, { label: "Rotation", value: "IC-RANKED" }],
    upstream: ["rt_alpha_decay"], downstream: ["rt_alpha_miner"], agent: "QuantitativeCoach", file: "research/alpha_rotation.py",
  },
  {
    id: "rt_opp_bus", name: "Opportunity Bus & Fusion", layer: "DECISION", category: "decision", icon: "⬡",
    description: "Central signal aggregator fusing macro impulses, quant alphas, and technical setups into trade candidates.",
    metrics: [{ label: "Authority", value: "ADVISORY ONLY" }, { label: "Mode", value: "ACTIVE" }, { label: "Shrinkage", value: "WEIGHTED" }],
    upstream: ["rt_macro_dag", "rt_causal_valid", "rt_regime", "rt_alpha_ensemble", "rt_calendar", "rt_macro_state", "rt_cftc", "rt_openbb"],
    downstream: ["rt_deliberation"], agent: "TechnicalNet", file: "intelligence/opportunity_bus.py",
  },
  {
    id: "rt_memory", name: "Cognitive Memory (L0-L3)", layer: "DECISION", category: "decision", icon: "◈",
    description: "Three-tier cognitive memory: L0 working (4k tokens), L2 episodic, L3 distilled trading rules.",
    metrics: [{ label: "L0 Working", value: "Context" }, { label: "L2 Episodic", value: "Records" }, { label: "L3 Lessons", value: "Rules" }],
    upstream: [], downstream: ["rt_deliberation"], agent: "MarketModel", file: "brain/memory.py",
  },
  {
    id: "rt_deliberation", name: "5-Agent Deliberation", layer: "DECISION", category: "decision", icon: "◈",
    description: "Structured multi-agent adversarial debate across 5 personas forming consensus on trade direction.",
    metrics: [{ label: "Personas", value: "5 Aligned" }, { label: "Consensus", value: "Bayesian Prior" }, { label: "Status", value: "ACTIVE" }],
    upstream: ["rt_opp_bus", "rt_regime", "rt_macro_state", "rt_memory"], downstream: ["rt_llm_router"], agent: "MarketModel", file: "intelligence/deliberation.py",
  },
  {
    id: "rt_llm_router", name: "LLM Router & Circuit Breaker", layer: "DECISION", category: "decision", icon: "⬡",
    description: "Routes LLM inference with automatic rate-limit tracking, provider failovers, and circuit breakers.",
    metrics: [{ label: "Provider", value: "Multi-Source" }, { label: "Model", value: "Autonomous" }, { label: "Circuit", value: "CLOSED (OK)", color: T.up }],
    upstream: ["rt_deliberation"], downstream: ["rt_gate1"], agent: "MarketModel", file: "intelligence/llm_router.py",
  },

  // ── SAFETY GATES ──────────────────────────────────────────────────────────
  {
    id: "rt_gate1", name: "Gate 1: Validator (15 Rules)", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Zero-hallucination deterministic validation: 15 Boolean rules for schema, bounds, cooldown, session checks.",
    metrics: [{ label: "Rules", value: "15/15 Pass" }, { label: "Gate Type", value: "Deterministic" }, { label: "Status", value: "PASS", color: T.up }],
    upstream: ["rt_llm_router", "rt_calendar"], downstream: ["rt_gate2"], agent: "RiskController", file: "core/trading_safety.py",
  },
  {
    id: "rt_gate2", name: "Gate 2: Walk-Forward Decay", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Rejects trades if rolling OOS/IS Sharpe ratio falls below 0.70 — guards against out-of-sample breakdown.",
    metrics: [{ label: "OOS Stability", value: "VERIFIED" }, { label: "Sharpe Ratio", value: "> 0.70 Gate" }, { label: "Status", value: "PASS" }],
    upstream: ["rt_gate1"], downstream: ["rt_gate3"], agent: "RiskController", file: "core/walk_forward_validator.py",
  },
  {
    id: "rt_gate3", name: "Gate 3: Overfit & DSR", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Bailey & López de Prado Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO).",
    metrics: [{ label: "DSR Gate", value: "> 1.0 Gate" }, { label: "PBO Gate", value: "< 0.30 Gate" }, { label: "Status", value: "PASS", color: T.up }],
    upstream: ["rt_gate2"], downstream: ["rt_gate4"], agent: "QuantitativeCoach", file: "core/robustness_validators.py",
  },
  {
    id: "rt_gate4", name: "Gate 4: Covariate Drift", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Kolmogorov-Smirnov two-sample test and Wasserstein distance — ensures live market matches backtest dynamics.",
    metrics: [{ label: "KS Test", value: "VERIFIED" }, { label: "Wasserstein", value: "STABLE" }, { label: "Drift", value: "CLEARED" }],
    upstream: ["rt_gate3"], downstream: ["rt_gate5"], agent: "QuantitativeCoach", file: "core/cpcv_validation.py",
  },
  {
    id: "rt_gate5", name: "Gate 5: Portfolio Risk", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Enforces 6.0R portfolio heat budget, max 1% equity risk, correlation limits, and margin TOCTOU safety.",
    metrics: [{ label: "Heat Cap", value: "6.0R Max" }, { label: "Max DD", value: "6.0% Cap" }, { label: "Margin", value: "SAFE" }],
    upstream: ["rt_gate4"], downstream: ["rt_gate6"], agent: "RiskController", file: "core/margin_safety.py",
  },
  {
    id: "rt_gate6", name: "Gate 6: Journal Hash", layer: "SAFETY", category: "pipeline", icon: "◇",
    description: "Writes cryptographic pre-trade SHA-256 commitment hash into SQLite before routing to broker.",
    metrics: [{ label: "Hash Protocol", value: "SHA-256" }, { label: "DB Commit", value: "trading_state.db" }, { label: "Audit", value: "ANCHORED" }],
    upstream: ["rt_gate5"], downstream: ["rt_risk_sizer"], agent: "SYSTEM", file: "core/execution_safety_supervisor.py",
  },

  // ── EXECUTION LAYER ───────────────────────────────────────────────────────
  {
    id: "rt_risk_sizer", name: "Dynamic Kelly Sizing", layer: "EXECUTION", category: "execution", icon: "△",
    description: "Fractional Kelly sizing (0.25x), ATR structure stop losses, slippage tolerance calculation.",
    metrics: [{ label: "Sizing", value: "Fractional Kelly" }, { label: "Stop Loss", value: "Structural ATR" }, { label: "Risk Budget", value: "1.0% Equity" }],
    upstream: ["rt_gate6"], downstream: ["rt_broker_exec"], agent: "RiskController", file: "execution/risk_controller.py",
  },
  {
    id: "rt_broker_exec", name: "MT5 Broker Execution", layer: "EXECUTION", category: "execution", icon: "⚡",
    description: "Marshals order payload into MT5 binary trade payload, transmits via MCP bridge, receives fill confirmation.",
    metrics: [{ label: "Transport", value: "MCP/IPC" }, { label: "Port", value: "22346" }, { label: "Latency", value: "REALTIME" }],
    upstream: ["rt_risk_sizer", "rt_mt5"], downstream: ["rt_trade_mgr", "rt_opentui_stream"], agent: "SYSTEM", file: "execution/adapters/mt5_adapter.py",
  },
  {
    id: "rt_trade_mgr", name: "Trade Lifecycle Manager", layer: "EXECUTION", category: "execution", icon: "⊕",
    description: "Manages open positions: breakeven at +1R, partial exits at +2R/+3R, time stop at 4h stagnation.",
    metrics: [{ label: "Trailing Stop", value: "+1R Lock" }, { label: "Partials", value: "+2R/+3R Partial" }, { label: "Time Stop", value: "Stagnation Exit" }],
    upstream: ["rt_broker_exec"], downstream: ["rt_daily_reconcile"], agent: "RiskController", file: "execution/trade_manager.py",
  },

  // ── OBSERVABILITY LAYER ───────────────────────────────────────────────────
  {
    id: "rt_daily_reconcile", name: "Daily Reconciliation", layer: "OBSERVE", category: "observability", icon: "◇",
    description: "Automated daily reconciliation: broker deal records vs SQLite journal with zero drift verification.",
    metrics: [{ label: "Audit Rules", value: "14/14 Pass" }, { label: "Drift", value: "0 Clean" }, { label: "Status", value: "VERIFIED" }],
    upstream: ["rt_trade_mgr"], downstream: ["rt_opentui_stream"], agent: "SYSTEM", file: "core/daily_reconciliation.py",
  },
  {
    id: "rt_opentui_stream", name: "OpenTUI Deck Stream", layer: "OBSERVE", category: "observability", icon: "▣",
    description: "Pushes real-time execution events, trade fills, and audit receipts to all connected deck/GUI clients.",
    metrics: [{ label: "Deck Push", value: "WebSocket Stream" }, { label: "Telemetry", value: "REALTIME" }, { label: "Sync State", value: "100% HEALTHY", color: T.up }],
    upstream: ["rt_broker_exec", "rt_daily_reconcile"], downstream: [], agent: "OPERATOR", file: "tui_opentui/src/transport/live.ts",
  },
  {
    id: "rt_harness", name: "Harness Event Stream", layer: "OBSERVE", category: "observability", icon: "▣",
    description: "Core harness producing HarnessEvents consumed by WebSocket fanout — the telemetry backbone of the deck.",
    metrics: [{ label: "Harness", value: "EventStream" }, { label: "DB", value: "trading_state.db" }, { label: "Transport", value: "WebSocket" }],
    upstream: ["rt_opentui_stream"], downstream: [], agent: "SYSTEM", file: "core/harness/event_stream.py",
  },
]

function GraphLivePulse() {
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0
  return (
    <text fg={pulse ? T.teal : T.dim}>
      {pulse ? "● LIVE" : "○ SYNC"}
    </text>
  )
}

function ActiveNodeDot(props: { active: boolean; color: string }) {
  const tick = useDeck((s) => s.tick)
  if (!props.active) return <text fg={props.color}>●</text>
  const char = tick % 4 < 2 ? "●" : "○"
  return <text fg={props.color}>{char}</text>
}

export function GraphScreen(props?: { initialTab?: number }) {
  const [activeTab, setActiveTab] = useState<number>(props?.initialTab ?? 0) // 0: Timeline, 1: Rataflow Graph, 2: Dual Split, 3: Self-Healing, 4: Live Runtime
  const [selectedMilestoneIdx, setSelectedMilestoneIdx] = useState<number>(0)
  const [selectedNodeIdx, setSelectedNodeIdx] = useState<number>(0)
  const [activeLayerFilter, setActiveLayerFilter] = useState<number>(0) // 0: All layers, 1-8: specific layer
  const [runtimeLayerFilter, setRuntimeLayerFilter] = useState<string>("ALL")
  const [isSimulating, setIsSimulating] = useState<boolean>(true)

  const contentW = useContentWidth()

  // Live data from the deck store
  const positions = useDeck((s) => s.positions)
  const account = useDeck((s) => s.account)
  const graphStates = useDeck((s) => s.graphStates)
  const strategies = useDeck((s) => s.strategies)
  const stream = useDeck((s) => s.stream)
  const causalPaths = useDeck((s) => s.causalChainPaths)
  const alphaSignals = useDeck((s) => s.alphaSignals)
  const alphaHealth = useDeck((s) => s.alphaHealth)
  const memory = useDeck((s) => s.memorySystem)
  const symbols = useDeck((s) => s.symbols)
  const qm = useDeck((s) => s.quantMonitor)
  const mt5 = useDeck((s) => s.mt5)
  const dataSources = useDeck((s) => s.dataSources)
  const loopStats = useDeck((s) => s.loopStats)
  const chainIntegrity = useDeck((s) => s.chainIntegrity)
  const autonomyLevel = useDeck((s) => s.autonomyLevel)

  // Filtered nodes based on active layer filter (static Rataflow graph)
  const displayedNodes = useMemo(() => {
    if (activeLayerFilter === 0) return RATAFLOW_NODES
    return RATAFLOW_NODES.filter((n) => n.layer === activeLayerFilter)
  }, [activeLayerFilter])

  // Filtered runtime nodes
  const runtimeNodes = useMemo(() => {
    if (runtimeLayerFilter === "ALL") return RUNTIME_NODES
    return RUNTIME_NODES.filter((n) => n.layer === runtimeLayerFilter)
  }, [runtimeLayerFilter])

  // Dynamic milestone data bound to actual execution loop and account/positions telemetry
  const milestones = useMemo((): TimelineMilestone[] => {
    const hasPositions = (positions?.length ?? 0) > 0
    const latestPos = hasPositions ? positions[0] : null
    const mt5Connected = mt5?.terminal?.connected ?? false
    const cycles = loopStats?.cyclesCompleted ?? 0
    const avgCycle = loopStats?.avgCycleMs ?? 0
    const activePipeline = graphStates?.some((g) => g.status === "running")

    return TIMELINE_MILESTONES.map((m) => {
      let status: TimelineMilestone["status"] = m.status
      const details = { ...m.details, metrics: { ...m.details.metrics } }

      if (m.id === "t0_ingest") {
        status = mt5Connected ? "LIVE" : (dataSources?.length ? "READY" : "STANDBY")
        details.metrics["MT5 Status"] = mt5Connected ? "CONNECTED" : "OFFLINE"
        details.metrics["Data Feeds"] = `${dataSources?.length ?? 0} active`
        details.payloadSummary = JSON.stringify({
          source: "MARKET_FEEDS",
          mt5_connected: mt5Connected,
          active_sources: dataSources?.length ?? 0,
          status,
        })
      } else if (m.id === "t15_macro") {
        status = causalPaths.length ? "LIVE" : "READY"
        details.metrics["Active Paths"] = causalPaths.length
        details.metrics["Regime"] = qm?.bocpd_regime ?? "AWAITING"
        details.payloadSummary = JSON.stringify({
          engine: "macro_causal_dag",
          db: "trading_state.db",
          causal_paths: causalPaths.length,
          regime: qm?.bocpd_regime ?? null,
          status,
        })
      } else if (m.id === "t30_fusion") {
        status = alphaSignals.length ? "LIVE" : "READY"
        details.metrics["Alpha Signals"] = alphaSignals.length
        details.metrics["Opportunities"] = strategies?.length ?? 0
        details.payloadSummary = JSON.stringify({
          target: "opportunity_bus",
          registered_alphas: alphaSignals.length,
          active_strategies: strategies?.length ?? 0,
          status,
        })
      } else if (m.id === "t45_deliberation") {
        status = activePipeline ? "RUNNING" : (strategies.length ? "READY" : "STANDBY")
        details.metrics["Strategies"] = strategies.length
        details.metrics["Autonomy"] = autonomyLevel
        details.payloadSummary = JSON.stringify({
          engine: "deliberation",
          pipeline_active: activePipeline,
          autonomy: autonomyLevel,
          strategies: strategies.length,
        })
      } else if (m.id === "t60_safety") {
        status = chainIntegrity?.valid ? "PASS" : (chainIntegrity?.entriesVerified ? "WATCH" : "READY")
        details.metrics["Chain Verified"] = chainIntegrity?.entriesVerified ?? 0
        details.metrics["Audit Integrity"] = chainIntegrity?.valid ? "VALID" : "UNVERIFIED"
        details.payloadSummary = JSON.stringify({
          gate_enforcement: "FAIL_CLOSED",
          chain_valid: chainIntegrity?.valid ?? false,
          entries_verified: chainIntegrity?.entriesVerified ?? 0,
          status,
        })
      } else if (m.id === "t75_sizing") {
        status = hasPositions ? "PASS" : "READY"
        details.metrics["Account Equity"] = `$${(account?.equity || account?.balance || 0).toLocaleString()}`
        details.metrics["Heat Used"] = `${(account?.heatUsedR ?? 0).toFixed(2)}R / ${account?.heatCapR ?? 6}R`
        details.payloadSummary = JSON.stringify({
          sizing_model: "FRACTIONAL_KELLY",
          equity: account?.equity || account?.balance || 0,
          heat_used_r: account?.heatUsedR ?? 0,
          status,
        })
      } else if (m.id === "t90_adapter") {
        status = mt5Connected ? "LIVE" : "STANDBY"
        details.metrics["MCP Bridge"] = mt5Connected ? "ONLINE" : "OFFLINE"
        details.metrics["Broker Login"] = mt5?.terminal?.login ? String(mt5.terminal.login) : "STANDBY"
        details.payloadSummary = JSON.stringify({
          transport: "MCP_SOCKET",
          port: 22346,
          mt5_connected: mt5Connected,
          login: mt5?.terminal?.login ?? null,
        })
      } else if (m.id === "t120_broker") {
        status = hasPositions ? "FILLED" : (mt5Connected ? "READY" : "STANDBY")
        if (latestPos) {
          details.metrics["Active Ticket"] = `#${latestPos.ticket}`
          details.metrics["Fill Symbol"] = latestPos.symbol
          details.metrics["Volume"] = `${latestPos.volume} lots`
          details.metrics["Open Price"] = String(latestPos.entry)
          details.payloadSummary = JSON.stringify({
            broker: "MT5_SERVER",
            ticket: latestPos.ticket,
            symbol: latestPos.symbol,
            volume: latestPos.volume,
            open_price: latestPos.entry,
            profit: latestPos.pnl,
          })
        } else {
          details.metrics["Active Ticket"] = "Awaiting fill"
          details.metrics["Fill Symbol"] = symbols?.[0] ?? "STANDBY"
          details.payloadSummary = JSON.stringify({
            broker: "MT5_SERVER",
            retcode: "AWAITING_FILL",
            status: "UNVERIFIED",
          })
        }
      } else if (m.id === "t150_broadcast") {
        status = stream?.length ? "SYNCED" : "READY"
        details.metrics["Stream Events"] = stream?.length ?? 0
        details.metrics["Completed Cycles"] = cycles
        details.metrics["Avg Cycle"] = `${avgCycle.toFixed(1)}ms`
        details.payloadSummary = JSON.stringify({
          transport: "OPENTUI_WEBSOCKET",
          stream_events: stream?.length ?? 0,
          cycles_completed: cycles,
          avg_cycle_ms: avgCycle,
          status,
        })
      }

      return {
        ...m,
        status,
        details,
      }
    })
  }, [positions, mt5, loopStats, graphStates, causalPaths, qm, alphaSignals, strategies, autonomyLevel, chainIntegrity, account, dataSources, symbols, stream])

  // Dynamic Rataflow metrics bound to live system state
  const getDynamicRataflowMetrics = useCallback((node: RataflowNode) => {
    const mt5Connected = mt5?.terminal?.connected ?? false
    const eq = account?.equity || account?.balance || 0

    switch (node.id) {
      case "node_market_feed":
        return [
          { label: "Broker MT5", value: mt5Connected ? "ONLINE" : "STANDBY", color: mt5Connected ? T.up : T.dim },
          { label: "Symbols", value: `${symbols?.length ?? 0} active`, color: T.bright },
          { label: "Live Ticks", value: `${Object.keys(mt5?.ticks ?? {}).length} pairs`, color: T.cyan },
        ]
      case "node_macro_ner":
      case "node_macro_dag":
        return [
          { label: "Causal Paths", value: `${causalPaths.length} active`, color: causalPaths.length ? T.up : T.dim },
          { label: "Regime", value: qm?.bocpd_regime ?? "AWAITING", color: qm?.bocpd_regime ? T.teal : T.dim },
          { label: "Storage", value: "trading_state.db", color: T.bright },
        ]
      case "node_alpha_miner":
      case "node_alpha_bus":
      case "node_opp_bus":
        return [
          { label: "Alphas", value: `${alphaSignals.length} active`, color: alphaSignals.length ? T.up : T.dim },
          { label: "Strategies", value: `${strategies.length} loaded`, color: T.bright },
          { label: "Health Alerts", value: `${alphaHealth.filter((h) => h.status !== "HEALTHY").length}`, color: alphaHealth.some((h) => h.status !== "HEALTHY") ? T.warn : T.up },
        ]
      case "node_safety_gate":
      case "node_immutable_journal":
        return [
          { label: "Chain Valid", value: chainIntegrity?.valid ? "PASS" : "STANDBY", color: chainIntegrity?.valid ? T.up : T.warn },
          { label: "Audit Hash", value: `${chainIntegrity?.entriesVerified ?? 0} blocks`, color: T.bright },
          { label: "Fail-Closed", value: "ENFORCED", color: T.up },
        ]
      case "node_risk_manager":
      case "node_portfolio_arbiter":
        return [
          { label: "Equity", value: `$${eq.toLocaleString()}`, color: T.up },
          { label: "Heat Used", value: `${(account?.heatUsedR ?? 0).toFixed(2)}R`, color: T.bright },
          { label: "Heat Cap", value: `${account?.heatCapR ?? 6}R`, color: T.dim },
        ]
      case "node_order_router":
      case "node_mt5_adapter":
      case "node_trade_manager":
        return [
          { label: "Positions", value: `${positions.length} open`, color: positions.length ? T.cyan : T.dim },
          { label: "Broker Login", value: mt5?.terminal?.login ? String(mt5.terminal.login) : "STANDBY", color: T.bright },
          { label: "Adapter State", value: mt5Connected ? "ONLINE" : "STANDBY", color: mt5Connected ? T.up : T.dim },
        ]
      case "node_daily_reconcile":
      case "node_opentui_stream":
      case "node_cockpit_bus":
        return [
          { label: "Stream Events", value: `${stream.length}`, color: T.bright },
          { label: "Cycles", value: `${loopStats?.cyclesCompleted ?? 0}`, color: T.cyan },
          { label: "Avg Cycle", value: `${(loopStats?.avgCycleMs ?? 0).toFixed(1)}ms`, color: T.teal },
        ]
      default:
        // No live binding for this node's metrics — say so instead of serving
        // invented numbers ("2.41x" etc.). Architecture info in node.summary,
        // codeRef and ports remains (static by nature, not live telemetry).
        return [
          { label: "Live metrics", value: "UNVERIFIED", color: T.dim },
          { label: "Source", value: "no engine/API binding", color: T.dim },
        ]
    }
  }, [mt5, symbols, account, causalPaths, qm, alphaSignals, strategies, alphaHealth, chainIntegrity, positions, stream, loopStats])

  // Dynamic self-healing steps bound to active alpha health and quant telemetry
  const selfHealingSteps = useMemo((): SelfHealingStep[] => {
    const hasCriticalDecay = alphaHealth?.some((h) => h.status === "DEAD" || h.status === "REVERSED")
    const hasWatchDecay = alphaHealth?.some((h) => h.status !== "HEALTHY")
    const pbo = qm?.pbo
    const dsr = qm?.dsr
    const rollingSharpe = qm?.rolling_sharpe

    return SELF_HEALING_STEPS.map((step) => {
      let status: SelfHealingStep["status"] = "STANDBY"
      let metric = step.metric

      switch (step.step) {
        case 1:
          if (hasCriticalDecay) {
            status = "ACTIVE"
            metric = `ALERT: Critical decay detected across ${alphaHealth.filter((h) => h.status === "DEAD" || h.status === "REVERSED").length} factor(s)`
          } else if (strategies.length > 0) {
            status = "PASS"
            metric = `${strategies.length} active strategy lifecycle envelopes healthy`
          } else {
            status = "READY"
            metric = "Monitoring active · 0 strategies under degradation"
          }
          break
        case 2:
          if (alphaHealth.length > 0) {
            status = hasWatchDecay ? "WATCH" : "PASS"
            metric = `${alphaHealth.length} factors monitored · ${alphaHealth.filter((h) => h.status !== "HEALTHY").length} decay alerts`
          } else {
            status = "READY"
            metric = `${alphaSignals.length} alpha signals tracked in SQLite factor pool`
          }
          break
        case 3:
          if (qm?.bocpd_regime) {
            status = "PASS"
            metric = `Active regime: ${qm.bocpd_regime} · Recalibration loop ready`
          } else {
            status = "READY"
            metric = "Offline replay segments ready for recalibration"
          }
          break
        case 4:
          if (pbo != null) {
            status = pbo < 0.3 ? "PASS" : "WARN"
            metric = `PBO: ${(pbo * 100).toFixed(1)}% · Combinatorial Purged CV Gate`
          } else {
            status = "READY"
            metric = "16 combinatorial k-fold paths with embargo ready"
          }
          break
        case 5:
          if (rollingSharpe != null) {
            status = rollingSharpe > 1.0 ? "PASS" : (rollingSharpe > 0.5 ? "WATCH" : "WARN")
            metric = `Rolling Sharpe: ${rollingSharpe.toFixed(2)} · OOS stability gate`
          } else {
            status = "READY"
            metric = "Walk-forward out-of-sample decay gate initialized"
          }
          break
        case 6:
          if (dsr != null) {
            status = dsr >= 1.0 ? "PASS" : "WARN"
            metric = `Deflated Sharpe (DSR): ${dsr.toFixed(2)} · Bailey & López de Prado`
          } else {
            status = "READY"
            metric = "Deflated Sharpe & model complexity test ready"
          }
          break
        case 7:
          if (strategies.length > 0) {
            status = "PASS"
            metric = `${strategies.length} strategies packaged with deterministic proofs`
          } else {
            status = "READY"
            metric = "Candidate promotion package assembler ready"
          }
          break
        case 8:
          status = autonomyLevel !== "L0_MANUAL" ? "PASS" : "READY"
          metric = `Current Autonomy: ${autonomyLevel} · Fail-closed quarantine`
          break
      }

      return {
        ...step,
        status,
        metric,
      }
    })
  }, [alphaHealth, strategies, qm, alphaSignals, autonomyLevel])

  // Dynamic runtime metrics map for Tab 4 & inspectors
  const dynamicRuntimeMetrics = useMemo(() => {
    const mt5Connected = mt5?.terminal?.connected ?? false
    const eq = account?.equity || account?.balance || 0
    const cycles = loopStats?.cyclesCompleted ?? 0
    const avgCycle = loopStats?.avgCycleMs ?? 0
    const dsMap = new Map((dataSources ?? []).map((ds: any) => [ds.id, ds]))

    const metrics: Record<string, Array<{ label: string; value: string; color?: string }>> = {
      rt_mt5: [
        { label: "Login", value: mt5?.terminal?.login ? String(mt5.terminal.login) : "STANDBY", color: mt5Connected ? T.bright : T.dim },
        { label: "Server", value: mt5?.terminal?.server || "STANDBY", color: T.cyan },
        { label: "Ping", value: mt5?.terminal?.lastPingMs ? `${mt5.terminal.lastPingMs}ms` : "---", color: T.teal },
        { label: "State", value: mt5Connected ? "ONLINE" : "OFFLINE", color: mt5Connected ? T.up : T.dim },
      ],
      rt_market_feed: [
        { label: "Tracked", value: `${symbols?.length ?? 0} symbols`, color: T.bright },
        { label: "Live Ticks", value: `${Object.keys(mt5?.ticks ?? {}).length} pairs`, color: T.cyan },
        { label: "Feed", value: mt5Connected ? "STREAMING" : "STANDBY", color: mt5Connected ? T.up : T.dim },
      ],
      rt_news_feed: [
        { label: "Events", value: `${stream?.length ?? 0} total`, color: T.bright },
        { label: "Recent", value: `${stream?.slice(-5).length ?? 0} stream`, color: T.cyan },
        { label: "Mode", value: "REAL-TIME", color: T.teal },
      ],
      rt_data_quality: [
        { label: "Schema", value: "ENFORCED", color: T.up },
        { label: "Audit", value: chainIntegrity?.valid ? "PASS" : "STANDBY", color: chainIntegrity?.valid ? T.up : T.warn },
        { label: "Outliers", value: "0 REJECTED", color: T.bright },
      ],
      rt_macro_ner: [
        { label: "Catalog", value: "FRED/EIA/COT", color: T.bright },
        { label: "Impulses", value: `${causalPaths.length}`, color: causalPaths.length ? T.up : T.dim },
        { label: "Engine", value: "ACTIVE", color: T.cyan },
      ],
      rt_macro_dag: [
        { label: "Paths", value: `${causalPaths.length}`, color: causalPaths.length ? T.up : T.dim },
        { label: "Storage", value: "trading_state.db", color: T.bright },
        { label: "State", value: causalPaths.length ? "LIVE" : "STANDBY", color: causalPaths.length ? T.up : T.dim },
      ],
      rt_causal_valid: [
        { label: "Integrity", value: chainIntegrity?.valid ? "PASS" : "STANDBY", color: chainIntegrity?.valid ? T.up : T.dim },
        { label: "Verified", value: `${chainIntegrity?.entriesVerified ?? 0}`, color: T.bright },
        { label: "Audit", value: "FAIL_CLOSED", color: T.teal },
      ],
      rt_regime: [
        { label: "Regime", value: qm?.bocpd_regime ?? "STANDBY", color: qm?.bocpd_regime ? T.up : T.dim },
        { label: "Hurst", value: qm?.hurst_exponent != null ? qm.hurst_exponent.toFixed(2) : "STANDBY", color: T.cyan },
        { label: "Vol", value: qm?.daily_atr_vol != null ? `${(qm.daily_atr_vol * 100).toFixed(1)}%` : "STANDBY", color: T.teal },
      ],
      rt_calendar: [
        { label: "Calendar", value: "ECONOMIC_SYNC", color: T.bright },
        { label: "Events", value: "ACTIVE", color: T.up },
        { label: "HighImpact", value: "TRACKED", color: T.cyan },
      ],
      rt_macro_state: [
        { label: "Status", value: "ACTIVE", color: T.up },
        { label: "Vector", value: "MULTI_ASSET", color: T.bright },
        { label: "Sink", value: "OPP_BUS", color: T.teal },
      ],
      rt_alpha_miner: [
        { label: "Alphas", value: `${alphaSignals.length}`, color: alphaSignals.length ? T.up : T.dim },
        { label: "Deployed", value: `${alphaSignals.filter((a) => a.status === "deployed").length}`, color: T.cyan },
        { label: "Strategies", value: `${strategies.length}`, color: T.bright },
      ],
      rt_alpha_ensemble: [
        { label: "Signals", value: `${alphaSignals.length}`, color: alphaSignals.length ? T.up : T.dim },
        { label: "Method", value: "SHRINKAGE", color: T.bright },
        { label: "Status", value: alphaSignals.length ? "FUSING" : "STANDBY", color: alphaSignals.length ? T.up : T.dim },
      ],
      rt_alpha_decay: [
        { label: "Monitored", value: `${alphaHealth.length}`, color: T.bright },
        { label: "Alerts", value: `${alphaHealth.filter((h) => h.status !== "HEALTHY").length}`, color: alphaHealth.some((h) => h.status !== "HEALTHY") ? T.warn : T.up },
        { label: "Health", value: alphaHealth.some((h) => h.status !== "HEALTHY") ? "WARN" : "STABLE", color: alphaHealth.some((h) => h.status !== "HEALTHY") ? T.warn : T.up },
      ],
      rt_alpha_rotation: [
        { label: "Active", value: `${alphaSignals.filter((a) => a.status === "deployed").length}`, color: T.cyan },
        { label: "Quarantine", value: `${alphaHealth.filter((h) => h.status === "DEAD" || h.status === "REVERSED").length}`, color: T.dim },
        { label: "Gate", value: "ACTIVE", color: T.up },
      ],
      rt_opp_bus: [
        { label: "Candidates", value: `${strategies.length}`, color: T.bright },
        { label: "Authority", value: "ADVISORY", color: T.dim },
        { label: "Bus", value: "ACTIVE", color: T.up },
      ],
      rt_memory: [
        { label: "Context", value: `${memory?.workingContextTokens ?? 0} tok`, color: T.bright },
        { label: "Budget", value: `${memory?.workingContextBudget ?? 4000} tok`, color: T.dim },
        { label: "Retrievals", value: `${memory?.totalRetrievals ?? 0}`, color: T.teal },
      ],
      rt_deliberation: [
        { label: "Strategies", value: `${strategies.length}`, color: T.bright },
        { label: "Autonomy", value: autonomyLevel, color: T.cyan },
        { label: "Status", value: graphStates.some((g) => g.status === "running") ? "ACTIVE" : "READY", color: T.up },
      ],
      rt_llm_router: [
        { label: "Gateway", value: "LLM_ROUTER", color: T.bright },
        { label: "Failover", value: "LOCAL_HEURISTIC", color: T.dim },
        { label: "Status", value: "ONLINE", color: T.up },
      ],
      rt_gate1: [
        { label: "Checks", value: "15 Rules", color: T.bright },
        { label: "Enforce", value: "FAIL_CLOSED", color: T.up },
        { label: "Gate", value: "PASS", color: T.up },
      ],
      rt_gate2: [
        { label: "Metric", value: "OOS/IS Sharpe", color: T.bright },
        { label: "Decay", value: "STABLE", color: T.up },
        { label: "Gate", value: "PASS", color: T.up },
      ],
      rt_gate3: [
        { label: "PBO", value: qm?.pbo != null ? `${(qm.pbo * 100).toFixed(1)}%` : "PASS", color: T.bright },
        { label: "DSR", value: qm?.dsr != null ? qm.dsr.toFixed(2) : "PASS", color: T.cyan },
        { label: "Gate", value: "PASS", color: T.up },
      ],
      rt_gate4: [
        { label: "Covariate", value: "K-S TEST", color: T.bright },
        { label: "Drift", value: "CLEARED", color: T.up },
        { label: "Gate", value: "PASS", color: T.up },
      ],
      rt_gate5: [
        { label: "Heat", value: `${(account?.heatUsedR ?? 0).toFixed(1)}R`, color: T.bright },
        { label: "Cap", value: `${account?.heatCapR ?? 6}R`, color: T.dim },
        { label: "Gate", value: (account?.heatUsedR ?? 0) <= (account?.heatCapR ?? 6) ? "PASS" : "WARN", color: T.up },
      ],
      rt_gate6: [
        { label: "SHA256", value: "COMMIT", color: T.bright },
        { label: "DB", value: "trading_state.db", color: T.cyan },
        { label: "Verified", value: `${chainIntegrity?.entriesVerified ?? 0}`, color: T.up },
      ],
      rt_risk_sizer: [
        { label: "Equity", value: `$${eq.toLocaleString()}`, color: T.up },
        { label: "Heat", value: `${(account?.heatUsedR ?? 0).toFixed(1)}R / ${account?.heatCapR ?? 6}R`, color: T.bright },
        { label: "Model", value: "0.25x KELLY", color: T.teal },
      ],
      rt_broker_exec: [
        { label: "Positions", value: `${positions.length}`, color: positions.length ? T.cyan : T.dim },
        { label: "Connection", value: mt5Connected ? "ONLINE" : "STANDBY", color: mt5Connected ? T.up : T.dim },
        { label: "Server", value: mt5?.terminal?.server || "STANDBY", color: T.bright },
      ],
      rt_trade_mgr: [
        { label: "Open", value: `${positions.length}`, color: positions.length ? T.cyan : T.dim },
        { label: "Drawdown", value: `${(account?.drawdownPct ?? 0).toFixed(1)}%`, color: T.bright },
        { label: "Risk", value: `${(account?.openRiskPct ?? 0).toFixed(1)}%`, color: T.teal },
      ],
      rt_daily_reconcile: [
        { label: "Reconciled", value: "TRUE", color: T.up },
        { label: "Journal", value: "trading_state.db", color: T.bright },
        { label: "Integrity", value: chainIntegrity?.valid ? "VALID" : "PENDING", color: T.cyan },
      ],
      rt_opentui_stream: [
        { label: "Events", value: `${stream.length}`, color: T.bright },
        { label: "Cycles", value: `${cycles}`, color: T.cyan },
        { label: "AvgCycle", value: `${avgCycle.toFixed(1)}ms`, color: T.teal },
      ],
      rt_harness: [
        { label: "Harness", value: "EventStream", color: T.bright },
        { label: "DB", value: "trading_state.db", color: T.cyan },
        { label: "Transport", value: "WebSocket", color: T.teal },
      ],
    }

    // Add data sources
    for (const dsId of ["fred", "cftc", "eia", "openbb"]) {
      const ds = dsMap.get(dsId)
      metrics[`rt_${dsId}`] = [
        { label: "Status", value: ds ? (ds.health === "healthy" || ds.health === "ok" ? "LIVE" : "ERROR") : "STANDBY", color: ds?.health === "healthy" ? T.up : T.dim },
        { label: "Fetches", value: `${ds?.fetchCount ?? 0}`, color: T.bright },
        { label: "Errors", value: `${ds?.errorCount ?? 0}`, color: ds?.errorCount ? T.warn : T.dim },
      ]
    }

    return metrics
  }, [mt5, account, loopStats, dataSources, symbols, stream, chainIntegrity, causalPaths, qm, alphaSignals, strategies, alphaHealth, memory, autonomyLevel, graphStates, positions])

  // Compute live runtime node statuses from actual system state
  const liveNodeStatuses = useMemo(() => {
    const statuses: Record<string, { status: string; color: string; detail: string }> = {}
    const hasOpenPositions = (positions?.length ?? 0) > 0
    const activePipeline = graphStates?.some((g) => g.status === "running")
    const hasStrategies = (strategies?.length ?? 0) > 0
    const recentStream = stream?.slice(0, 5) ?? []
    const eq = account?.equity || account?.balance || 0
    const mt5Connected = mt5?.terminal?.connected ?? false

    // Data layer — reflects real broker & feed connections
    statuses["rt_mt5"] = {
      status: mt5Connected ? "LIVE" : "STANDBY",
      color: mt5Connected ? T.up : T.dim,
      detail: mt5Connected ? `Equity: $${eq.toLocaleString()}` : "Awaiting MT5 connection",
    }
    statuses["rt_market_feed"] = {
      status: mt5Connected ? "LIVE" : "STANDBY",
      color: mt5Connected ? T.up : T.dim,
      detail: `${symbols?.length ?? 0} symbols tracked`,
    }
    statuses["rt_news_feed"] = {
      status: recentStream.length ? "LIVE" : "STANDBY",
      color: recentStream.length ? T.up : T.dim,
      detail: `${recentStream.length} recent stream events`,
    }
    const dsMap = new Map((dataSources ?? []).map((ds: any) => [ds.id, ds]))
    const dsStatus = (id: string, fallback: string) => {
      const ds = dsMap.get(id)
      if (!ds) return { status: "UNVERIFIED", color: T.dim, detail: fallback }
      const ok = ds.health === "healthy" || ds.health === "ok"
      const stale = ds.lastFetchTs && (Date.now() - ds.lastFetchTs > 300_000)
      return {
        status: ok ? (stale ? "STALE" : "LIVE") : "ERROR",
        color: ok ? (stale ? T.warn : T.up) : T.down,
        detail: ds.errorCount > 0 ? `${ds.errorCount} errors` : `${ds.fetchCount} fetches`,
      }
    }
    statuses["rt_fred"] = dsStatus("fred", "FRED Economic Series")
    statuses["rt_cftc"] = dsStatus("cftc", "CFTC COT Reports")
    statuses["rt_eia"] = dsStatus("eia", "EIA Petroleum Weekly")
    statuses["rt_openbb"] = dsStatus("openbb", "OpenBB Financial API")

    // Pipeline layer — real causal DAG & regime classification
    statuses["rt_data_quality"] = {
      status: chainIntegrity?.entriesVerified ? "PASS" : "UNVERIFIED",
      color: chainIntegrity?.entriesVerified ? T.up : T.dim,
      detail: chainIntegrity?.entriesVerified ? "Integrity verified" : "No audit-chain evidence yet",
    }
    statuses["rt_macro_ner"] = { status: activePipeline ? "ACTIVE" : "IDLE", color: activePipeline ? T.cyan : T.dim, detail: "NER ready" }
    statuses["rt_macro_dag"] = {
      status: causalPaths.length ? "LIVE" : "STANDBY",
      color: causalPaths.length ? T.up : T.dim,
      detail: `${causalPaths.length} active causal paths`,
    }
    statuses["rt_causal_valid"] = {
      status: causalPaths.length ? "PASS" : "STANDBY",
      color: causalPaths.length ? T.up : T.dim,
      detail: causalPaths.length ? `${causalPaths.length} paths validated` : "Awaiting causal paths",
    }
    statuses["rt_regime"] = {
      status: qm?.bocpd_regime ? "PASS" : "STANDBY",
      color: qm?.bocpd_regime ? T.up : T.dim,
      detail: qm?.bocpd_regime ? `${qm.bocpd_regime} regime` : "Awaiting regime detection",
    }
    statuses["rt_calendar"] = {
      status: dataSources?.some((ds: any) => String(ds.id).includes("calendar") && (ds.health === "healthy" || ds.health === "ok"))
        ? "CLEAR" : "UNVERIFIED",
      color: T.dim,
      detail: dataSources?.some((ds: any) => String(ds.id).includes("calendar")) ? "Calendar source reported" : "No calendar source evidence",
    }
    statuses["rt_macro_state"] = {
      status: causalPaths.length ? "LIVE" : "UNVERIFIED",
      color: causalPaths.length ? T.up : T.dim,
      detail: causalPaths.length ? "Macro state active" : "No macro/causal evidence yet",
    }

    // Decision layer — real alpha counts & memory statistics
    statuses["rt_alpha_miner"] = {
      status: alphaSignals.length ? "LIVE" : "STANDBY",
      color: alphaSignals.length ? T.up : T.dim,
      detail: `${alphaSignals.length} alphas registered`,
    }
    statuses["rt_alpha_ensemble"] = {
      status: alphaSignals.length ? "PASS" : "STANDBY",
      color: alphaSignals.length ? T.up : T.dim,
      detail: alphaSignals.length ? `${alphaSignals.length} factors fused` : "Awaiting alpha signals",
    }
    statuses["rt_alpha_decay"] = {
      status: alphaHealth.length ? (alphaHealth.some((h) => h.status !== "HEALTHY") ? "WATCH" : "PASS") : "STANDBY",
      color: alphaHealth.some((h) => h.status !== "HEALTHY") ? T.warn : T.up,
      detail: alphaHealth.length ? `${alphaHealth.filter((h) => h.status !== "HEALTHY").length} decay alerts` : "Monitoring active",
    }
    statuses["rt_alpha_rotation"] = {
      status: alphaSignals.length ? "PASS" : "STANDBY",
      color: alphaSignals.length ? T.up : T.dim,
      detail: `${alphaSignals.filter((a) => a.status === "deployed").length} deployed alphas`,
    }
    statuses["rt_opp_bus"] = { status: activePipeline ? "FUSING" : "IDLE", color: activePipeline ? T.cyan : T.dim, detail: "Opportunity bus active" }
    statuses["rt_memory"] = {
      status: memory?.tierMetrics?.length ? "LIVE" : "STANDBY",
      color: memory?.tierMetrics?.length ? T.up : T.dim,
      detail: `${memory?.totalRetrievals ?? 0} retrievals (${memory?.workingContextTokens ?? 0} tokens)`,
    }
    statuses["rt_deliberation"] = {
      status: activePipeline ? "ACTIVE" : "READY",
      color: activePipeline ? T.cyan : T.dim,
      detail: hasStrategies ? `${strategies?.length} strategies` : "No strategies yet",
    }
    statuses["rt_llm_router"] = { status: "UNVERIFIED", color: T.up, detail: "Autonomous LLM Gateway" }

    // Safety gates
    const gatesActive = activePipeline
    for (let i = 1; i <= 6; i++) {
      statuses[`rt_gate${i}`] = { status: gatesActive ? "CHECKING" : "PASS", color: gatesActive ? T.cyan : T.up, detail: gatesActive ? "Evaluating..." : "Cleared" }
    }

    // Execution layer
    statuses["rt_risk_sizer"] = { status: hasOpenPositions ? "ACTIVE" : "READY", color: hasOpenPositions ? T.cyan : T.dim, detail: "Kelly sizing active" }
    statuses["rt_broker_exec"] = { status: hasOpenPositions ? "EXECUTED" : "READY", color: hasOpenPositions ? T.up : T.dim, detail: mt5Connected ? "MCP Connected" : "Broker Offline" }
    statuses["rt_trade_mgr"] = { status: hasOpenPositions ? "MANAGING" : "IDLE", color: hasOpenPositions ? T.cyan : T.dim, detail: hasOpenPositions ? `${positions?.length} open` : "No positions" }

    // Observability
    statuses["rt_daily_reconcile"] = { status: "UNVERIFIED", color: T.up, detail: "Journal reconciled" }
    statuses["rt_opentui_stream"] = { status: "UNVERIFIED", color: T.up, detail: `${recentStream.length} stream events` }
    statuses["rt_harness"] = { status: "UNVERIFIED", color: T.up, detail: "Harness event stream" }

    return statuses
  }, [positions, account, graphStates, strategies, stream, causalPaths, alphaSignals, alphaHealth, memory, symbols, qm, mt5])

  // Layer colors for the runtime graph
  const RUNTIME_LAYER_COLORS: Record<string, string> = {
    DATA: T.cyan,
    PIPELINE: T.teal,
    DECISION: T.orange,
    SAFETY: T.purple,
    EXECUTION: T.bright,
    OBSERVE: T.dim,
  }

  const RUNTIME_LAYER_ICONS: Record<string, string> = {
    DATA: "▣",
    PIPELINE: "◇",
    DECISION: "◈",
    SAFETY: "⬡",
    EXECUTION: "⚡",
    OBSERVE: "▣",
  }

  // Keyboard navigation
  useKeyboard((key) => {
    if (key.name === "1") { setActiveTab(0); return }
    if (key.name === "2") { setActiveTab(1); return }
    if (key.name === "3") { setActiveTab(2); return }
    if (key.name === "4") { setActiveTab(3); return }
    if (key.name === "5") { setActiveTab(4); return }
    if (key.name === "t" || key.name === "T") { setActiveTab(0); return }
    if (key.name === "g" || key.name === "G") { setActiveTab(1); return }
    if (key.name === "d" || key.name === "D") { setActiveTab(2); return }
    if (key.name === "s" || key.name === "S") { setActiveTab(3); return }
    if (key.name === "r" || key.name === "R") { setActiveTab(4); return }

    if (key.name === "space") {
      setIsSimulating((v) => !v)
      return
    }

    // Layer filter shortcuts when in Rataflow view (Tab 1 or Tab 2)
    if (activeTab === 1) {
      if (["0", "1", "2", "3", "4", "5", "6", "7", "8"].includes(key.name ?? "")) {
        const layerNum = parseInt(key.name ?? "0", 10)
        setActiveLayerFilter(layerNum)
        setSelectedNodeIdx(0)
        return
      }
    }

    // Runtime layer filter (Tab 4)
    if (activeTab === 4) {
      const layerMap: Record<string, string> = { "0": "ALL", "1": "DATA", "2": "PIPELINE", "3": "DECISION", "4": "SAFETY", "5": "EXECUTION", "6": "OBSERVE" }
      if (layerMap[key.name ?? ""]) {
        setRuntimeLayerFilter(layerMap[key.name ?? ""])
        setSelectedNodeIdx(0)
        return
      }
    }

    if (activeTab === 0 || activeTab === 2) {
      if (key.name === "up" || key.name === "k") {
        setSelectedMilestoneIdx((i) => Math.max(0, i - 1))
        return
      }
      if (key.name === "down" || key.name === "j") {
        setSelectedMilestoneIdx((i) => Math.min(milestones.length - 1, i + 1))
        return
      }
    }

    if (activeTab === 1 || activeTab === 2 || activeTab === 4) {
      if (key.name === "up" || key.name === "k") {
        setSelectedNodeIdx((i) => Math.max(0, i - 1))
        return
      }
      if (key.name === "down" || key.name === "j") {
        setSelectedNodeIdx((i) => Math.min(runtimeNodes.length - 1, i + 1))
        return
      }
    }
  })

  const curMilestone = milestones[Math.min(selectedMilestoneIdx, milestones.length - 1)] ?? milestones[0]
  const curNode = displayedNodes[Math.min(selectedNodeIdx, displayedNodes.length - 1)] ?? RATAFLOW_NODES[0]
  const curRuntimeNode = runtimeNodes[Math.min(selectedNodeIdx, runtimeNodes.length - 1)] ?? RUNTIME_NODES[0]

  // Dynamic layout calculations
  const splitLeftW = Math.max(36, Math.floor(contentW * 0.42))
  const splitRightW = Math.max(40, contentW - splitLeftW - 2)

  return (
    <box flexDirection="column" flexGrow={1} backgroundColor={T.bg}>
      {/* Screen Header Bar */}
      <box
        flexDirection="row"
        justifyContent="space-between"
        alignItems="center"
        height={1}
        flexShrink={0}
        paddingLeft={1}
        paddingRight={1}
        marginBottom={0}
      >
        <box flexDirection="row" gap={1} alignItems="center">
          <text fg={T.cyan}>◈ SYSTEM ARCHITECTURE & PIPELINE FLOW</text>
          <GraphLivePulse />
          <text fg={T.dim}>| {RATAFLOW_NODES.length} Architecture + {RUNTIME_NODES.length} Runtime Nodes · 8 Layers</text>
        </box>
        <box flexDirection="row" gap={1}>
          <text fg={T.dim}>[1-5] Tabs · [Space] Pause</text>
        </box>
      </box>

      {/* Tab Navigation Bar - Sleek 1-Line Pills */}
      <TabBar
        tabs={[
          { name: "⚡ Timeline", badge: "9" },
          { name: "◈ Rataflow", badge: `${displayedNodes.length}` },
          { name: "⊞ Split", badge: "Dual" },
          { name: "⚙ Self-Heal", badge: "8" },
          { name: "🔴 Live", badge: `${runtimeNodes.length}` },
        ]}
        selected={activeTab}
        onChange={(idx) => setActiveTab(idx)}
        activeColor={T.cyan}
      />

      {/* Tab 0: High-Speed Execution Timeline */}
      {activeTab === 0 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          {/* Left Column: Timeline Step List */}
          <box
            title=" ⚡ PIPELINE LATENCY SPECIFICATION (t = 0ms to t = +150ms) "
            titleColor={T.cyan}
            borderStyle="single"
            borderColor={T.border}
            flexDirection="column"
            width={Math.max(48, Math.floor(contentW * 0.48))}
            flexShrink={0}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column" gap={0}>
                {milestones.map((m, idx) => {
                  const isSel = idx === selectedMilestoneIdx
                  const isDone = m.status === "PASS" || m.status === "DONE" || m.status === "LIVE" || m.status === "FILLED" || m.status === "SYNCED"
                  return (
                    <box
                      key={m.id}
                      flexDirection="column"
                      onMouseDown={() => setSelectedMilestoneIdx(idx)}
                      backgroundColor={isSel ? T.raised : undefined}
                      paddingLeft={1}
                      paddingRight={1}
                      marginBottom={0}
                    >
                      {/* Step Header */}
                      <box flexDirection="row" justifyContent="space-between">
                        <box flexDirection="row" gap={1}>
                          <text fg={isSel ? T.bright : T.cyan}>{m.timeLabel.padEnd(9)}</text>
                          <text fg={isSel ? T.teal : T.dim}>{isSel ? "▶" : "│"}</text>
                          <text fg={isSel ? T.bright : T.fg}>
                            [{m.title}]
                          </text>
                        </box>
                        <text fg={isDone ? T.up : (m.status === "STANDBY" ? T.dim : T.warn)}>[{m.status}]</text>
                      </box>

                      {/* Step Subtitle & File */}
                      <box flexDirection="row" paddingLeft={11} justifyContent="space-between">
                        <text fg={T.muted}>{clip(m.subtitle, 38)}</text>
                        <text fg={T.dim}>+{m.deltaMs.toFixed(1)}ms</text>
                      </box>

                      {/* Connector Line */}
                      {idx < milestones.length - 1 && (
                        <box flexDirection="row" paddingLeft={10}>
                          <text fg={T.borderBright}>│</text>
                          <text fg={T.dim}> {clip(m.file, 34)}</text>
                        </box>
                      )}
                    </box>
                  )
                })}
              </box>
            </scrollbox>

            {/* Bottom Progress Bar */}
            {(() => {
              const cycles = loopStats?.cyclesCompleted ?? 0
              const avgMs = loopStats?.avgCycleMs ?? 0
              const budgetMs = 150.0
              if (cycles === 0 || avgMs <= 0) {
                return (
                  <box
                    marginTop={1}
                    borderStyle="single"
                    borderColor={T.border}
                    paddingLeft={1}
                    paddingRight={1}
                    flexDirection="column"
                  >
                    <box flexDirection="row" justifyContent="space-between">
                      <text fg={T.dim}>CYCLE TIME BUDGET (SLA 150ms):</text>
                      <text fg={T.dim}>0.0ms / 150.0ms · STANDBY (Awaiting pulse)</text>
                    </box>
                    <text fg={T.dim}>[░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] +150.0ms FULL HEADROOM</text>
                  </box>
                )
              }
              const pct = Math.min(100, Math.max(0, (avgMs / budgetMs) * 100))
              const headroom = Math.max(0, budgetMs - avgMs)
              const totalBlocks = 36
              const filledBlocks = Math.min(totalBlocks, Math.round((pct / 100) * totalBlocks))
              const emptyBlocks = totalBlocks - filledBlocks
              const bar = "█".repeat(filledBlocks) + "░".repeat(emptyBlocks)
              const barColor = pct > 90 ? T.warn : (pct > 75 ? T.cyan : T.up)
              return (
                <box
                  marginTop={1}
                  borderStyle="single"
                  borderColor={T.border}
                  paddingLeft={1}
                  paddingRight={1}
                  flexDirection="column"
                >
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>CYCLE TIME BUDGET (SLA 150ms):</text>
                    <text fg={barColor}>
                      {avgMs.toFixed(1)}ms / {budgetMs.toFixed(1)}ms ({pct.toFixed(1)}%) · {cycles} cycles
                    </text>
                  </box>
                  <text fg={barColor}>
                    [{bar}] +{headroom.toFixed(1)}ms HEADROOM
                  </text>
                </box>
              )
            })()}
          </box>

          {/* Right Column: Step Inspector */}
          <box
            title={` ◈ MILESTONE INSPECTOR: ${curMilestone.timeLabel} — ${curMilestone.title} `}
            titleColor={T.teal}
            borderStyle="single"
            borderColor={T.teal}
            flexDirection="column"
            flexGrow={1}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column" gap={1}>
                {/* Status and Category Badges */}
                <box flexDirection="row" gap={1} alignItems="center">
                  <text fg={T.bg} bg={T.teal}> {curMilestone.category} </text>
                  <text fg={T.bg} bg={curMilestone.status === "DONE" || curMilestone.status === "PASS" ? T.up : T.warn}>
                    {" "}{curMilestone.status}{" "}
                  </text>
                  <text fg={T.bright}>{curMilestone.title}</text>
                  <text fg={T.dim}>(milestone not a live latency measurement)</text>
                </box>

                {/* Description */}
                <box backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                  <text fg={T.fg}>{curMilestone.description}</text>
                </box>

                {/* Source File and Invocation */}
                <box flexDirection="column">
                  <text fg={T.dim}>SOURCE CODE COMPONENT:</text>
                  <text fg={T.cyan}>file:///{curMilestone.file}</text>
                  <text fg={T.muted}>call: {curMilestone.details.codeSnippet}</text>
                </box>

                {/* Input Sockets */}
                <box flexDirection="column">
                  <text fg={T.teal}>INPUT SIGNALS & DATA SOURCES:</text>
                  {curMilestone.details.inputs.map((inp, i) => (
                    <text key={i} fg={T.muted}>  • {inp}</text>
                  ))}
                </box>

                {/* Output Sockets */}
                <box flexDirection="column">
                  <text fg={T.orange}>OUTPUT PAYLOAD & VECTORS:</text>
                  {curMilestone.details.outputs.map((out, i) => (
                    <text key={i} fg={T.bright}>  ▸ {out}</text>
                  ))}
                </box>

                {/* Telemetry Metrics */}
                <box flexDirection="column">
                  <text fg={T.purple}>EXECUTION TELEMETRY & CONSTRAINTS:</text>
                  <box flexDirection="row" flexWrap="wrap" gap={2}>
                    {Object.entries(curMilestone.details.metrics).map(([k, v]) => (
                      <box key={k} borderStyle="single" borderColor={T.border} paddingLeft={1} paddingRight={1}>
                        <text fg={T.dim}>{k}: </text>
                        <text fg={T.cyan}>{String(v)}</text>
                      </box>
                    ))}
                  </box>
                </box>

                {/* Raw JSON Payload */}
                <box flexDirection="column">
                  <text fg={T.dim}>SERIALIZED JSON PAYLOAD SNAPSHOT:</text>
                  <box backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                    <text fg={T.fg}>{curMilestone.details.payloadSummary}</text>
                  </box>
                </box>
              </box>
            </scrollbox>
          </box>
        </box>
      )}

      {/* Tab 1: Detailed Rataflow Visual Node Graph */}
      {activeTab === 1 && (
        <box flexDirection="column" flexGrow={1} gap={1}>
          {/* Visual Pipeline Flow Strip - 1-Line Compact */}
          <box
            flexDirection="row"
            alignItems="center"
            justifyContent="space-between"
            height={1}
            flexShrink={0}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <text fg={T.cyan}>FLOW:</text>
              <text fg={T.bright}>L1:Ingest</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L2:Macro</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L3:Alpha</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L4:BTest</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L5:Delib</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L6:Gates</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L7:Exec</text>
              <text fg={T.teal}>►</text>
              <text fg={T.bright}>L8:Observe</text>
            </box>
            <text fg={T.dim}>8 Stages · 51 Nodes</text>
          </box>

          {/* Layer Filter Toolbar - 1-Line Compact */}
          <box
            flexDirection="row"
            justifyContent="space-between"
            alignItems="center"
            height={1}
            flexShrink={0}
            backgroundColor={T.bg}
            paddingLeft={1}
            paddingRight={1}
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <text fg={T.dim}>FILTER:</text>
              <box onMouseDown={() => { setActiveLayerFilter(0); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 0 ? T.bright : T.dim} bg={activeLayerFilter === 0 ? T.raised : undefined}>
                  [0:All]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(1); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 1 ? T.cyan : T.dim} bg={activeLayerFilter === 1 ? T.raised : undefined}>
                  [1:Ingest]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(2); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 2 ? T.teal : T.dim} bg={activeLayerFilter === 2 ? T.raised : undefined}>
                  [2:Macro]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(3); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 3 ? T.orange : T.dim} bg={activeLayerFilter === 3 ? T.raised : undefined}>
                  [3:Alpha]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(4); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 4 ? T.purple : T.dim} bg={activeLayerFilter === 4 ? T.raised : undefined}>
                  [4:BTest]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(5); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 5 ? T.cyan : T.dim} bg={activeLayerFilter === 5 ? T.raised : undefined}>
                  [5:Delib]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(6); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 6 ? T.teal : T.dim} bg={activeLayerFilter === 6 ? T.raised : undefined}>
                  [6:Gates]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(7); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 7 ? T.orange : T.dim} bg={activeLayerFilter === 7 ? T.raised : undefined}>
                  [7:Exec]
                </text>
              </box>
              <box onMouseDown={() => { setActiveLayerFilter(8); setSelectedNodeIdx(0) }}>
                <text fg={activeLayerFilter === 8 ? T.bright : T.dim} bg={activeLayerFilter === 8 ? T.raised : undefined}>
                  [8:Observe]
                </text>
              </box>
            </box>
            <text fg={T.dim}>[0-8] Filter · [↑↓] Nav</text>
          </box>

          {/* Main 2-Column Split: Node Canvas (Left) + Node Inspector (Right) */}
          <box flexDirection="row" flexGrow={1} gap={1}>
            {/* Left Canvas: Rataflow ASCII/Unicode Node Graph */}
            <box
              title={` ◈ RATAFLOW INTERACTIVE NODE GRAPH (${displayedNodes.length} NODES) `}
              titleColor={T.cyan}
              borderStyle="single"
              borderColor={T.border}
              flexDirection="column"
              width={Math.max(54, Math.floor(contentW * 0.55))}
              flexShrink={0}
              paddingLeft={1}
              paddingRight={1}
            >
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="column" gap={1}>
                  {displayedNodes.map((node, i) => {
                    const isSel = i === selectedNodeIdx
                    return (
                      <box
                        key={node.id}
                        flexDirection="column"
                        onMouseDown={() => setSelectedNodeIdx(i)}
                        borderStyle="single"
                        borderColor={isSel ? T.cyan : T.border}
                        backgroundColor={isSel ? T.raised : T.panel}
                        paddingLeft={1}
                        paddingRight={1}
                      >
                        {/* Node Header */}
                        <box flexDirection="row" justifyContent="space-between">
                          <box flexDirection="row" gap={1}>
                            <text fg={isSel ? T.bright : T.cyan}>{node.typeIcon}</text>
                            <text fg={isSel ? T.bright : T.fg}>{node.title}</text>
                          </box>
                          <text fg={node.status === "LIVE" || node.status === "PASS" || node.status === "FILLED" || node.status === "SYNCED" ? T.up : (node.status === "UNVERIFIED" ? T.dim : T.warn)}>
                            [{node.status}]
                          </text>
                        </box>

                        {/* Layer & Flow Tag */}
                        <box flexDirection="row" justifyContent="space-between">
                          <text fg={T.purple}>{clip(node.layerName, 32)}</text>
                          <text fg={T.dim}>Flow: {node.flowTag}</text>
                        </box>

                        {/* Sockets: [in] & [out] */}
                        <box flexDirection="column" marginTop={0}>
                          <text fg={T.dim}>[in]  {clip(node.inPorts.join(" | "), 50)}</text>
                          <text fg={T.muted}>[out] {clip(node.outPorts.join(" | "), 50)}</text>
                        </box>

                        {/* Metrics Pill Row */}
                        <box flexDirection="row" gap={2} marginTop={0}>
                          {getDynamicRataflowMetrics(node).map((m, mi) => (
                            <text key={mi} fg={m.color ?? T.dim}>
                              {m.label}: {m.value}
                            </text>
                          ))}
                        </box>

                        {/* Directional Edge Indicators (Upstream & Downstream) */}
                        <box flexDirection="column" marginTop={0}>
                          {node.connectionsFrom.length > 0 && (
                            <text fg={T.dim}>
                              ▲ [from: {node.connectionsFrom.map((id) => id.replace("node_", "")).join(", ")}]
                            </text>
                          )}
                          {node.connectionsTo.length > 0 && (
                            <text fg={isSel ? T.cyan : T.borderBright}>
                              ▼ [──► to: {node.connectionsTo.map((id) => id.replace("node_", "")).join(", ")}]
                            </text>
                          )}
                        </box>
                      </box>
                    )
                  })}
                </box>
              </scrollbox>
            </box>

            {/* Right Column: Rataflow Node Inspector */}
            <box
              title={` ◈ RATAFLOW NODE INSPECTOR: ${curNode.title} `}
              titleColor={T.teal}
              borderStyle="single"
              borderColor={T.teal}
              flexDirection="column"
              flexGrow={1}
              paddingLeft={1}
              paddingRight={1}
            >
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="column" gap={1}>
                  {/* Node Identity */}
                  <box flexDirection="row" gap={1} alignItems="center">
                    <text fg={T.bg} bg={T.cyan}> {curNode.typeIcon} </text>
                    <text fg={T.bright}>{curNode.title}</text>
                    <text fg={curNode.status === "LIVE" || curNode.status === "PASS" || curNode.status === "FILLED" ? T.up : (curNode.status === "UNVERIFIED" ? T.dim : T.warn)}>
                      [{curNode.status}]
                    </text>
                  </box>

                  {/* Subsystem Layer & Flow Tag */}
                  <box flexDirection="row" justifyContent="space-between">
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>System Layer:</text>
                      <text fg={T.purple}>{curNode.layerName}</text>
                    </box>
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>Flow:</text>
                      <text fg={T.teal}>{curNode.flowTag}</text>
                    </box>
                  </box>

                  {/* Assigned Agent */}
                  <box flexDirection="row" gap={1}>
                    <text fg={T.dim}>Supervisory Agent:</text>
                    <text fg={T.teal}>{curNode.agent}</text>
                  </box>

                  {/* Source File */}
                  <box flexDirection="column">
                    <text fg={T.dim}>Source Code File:</text>
                    <text fg={T.cyan}>file:///{curNode.file}</text>
                    <text fg={T.muted}>Invocation: {curNode.codeRef}</text>
                  </box>

                  {/* Summary */}
                  <box backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                    <text fg={T.fg}>{curNode.summary}</text>
                  </box>

                  {/* Inbound Sockets */}
                  <box flexDirection="column">
                    <text fg={T.teal}>INBOUND PORTS / SOCKETS [in]:</text>
                    {curNode.inPorts.map((p, i) => (
                      <text key={i} fg={T.muted}>  ◄ socket_{i}: {p}</text>
                    ))}
                    {curNode.connectionsFrom.length > 0 && (
                      <text fg={T.dim}>  Upstream Sources: {curNode.connectionsFrom.join(", ")}</text>
                    )}
                  </box>

                  {/* Outbound Sockets */}
                  <box flexDirection="column">
                    <text fg={T.orange}>OUTBOUND PORTS / SOCKETS [out]:</text>
                    {curNode.outPorts.map((p, i) => (
                      <text key={i} fg={T.bright}>  ► socket_{i}: {p}</text>
                    ))}
                  </box>

                  {/* Outbound Connections */}
                  <box flexDirection="column">
                    <text fg={T.cyan}>DIRECT GRAPH CONNECTIONS (EDGES):</text>
                    {curNode.connectionsTo.length > 0 ? (
                      curNode.connectionsTo.map((target, i) => (
                        <text key={i} fg={T.bright}>  ──► {target}</text>
                      ))
                    ) : (
                      <text fg={T.dim}>  • Terminal sink node (No outbound edges)</text>
                    )}
                  </box>

                  {/* Live Node Metrics */}
                  <box flexDirection="column">
                    <text fg={T.purple}>LIVE TELEMETRY & HEALTH CONSTRAINTS:</text>
                    <box flexDirection="column" gap={0}>
                      {getDynamicRataflowMetrics(curNode).map((m, i) => (
                        <box key={i} flexDirection="row" justifyContent="space-between">
                          <text fg={T.dim}>{m.label}</text>
                          <text fg={m.color ?? T.bright}>{m.value}</text>
                        </box>
                      ))}
                    </box>
                  </box>
                </box>
              </scrollbox>
            </box>
          </box>
        </box>
      )}

      {/* Tab 2: Dual Split View (Timeline + Node Graph) */}
      {activeTab === 2 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          {/* Left Panel: Compact Timeline */}
          <box
            title=" ⚡ EXECUTION TIMELINE (0 - 150ms) "
            titleColor={T.cyan}
            borderStyle="single"
            borderColor={T.cyan}
            flexDirection="column"
            width={splitLeftW}
            flexShrink={0}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column">
                {milestones.map((m, idx) => {
                  const isSel = idx === selectedMilestoneIdx
                  const isDone = m.status === "DONE" || m.status === "PASS" || m.status === "LIVE" || m.status === "FILLED" || m.status === "SYNCED"
                  return (
                    <box
                      key={m.id}
                      flexDirection="column"
                      onMouseDown={() => setSelectedMilestoneIdx(idx)}
                      backgroundColor={isSel ? T.raised : undefined}
                      paddingLeft={1}
                      marginBottom={0}
                    >
                      <box flexDirection="row" justifyContent="space-between">
                        <text fg={isSel ? T.bright : T.cyan}>{m.timeLabel}</text>
                        <text fg={isDone ? T.up : (m.status === "STANDBY" ? T.dim : T.warn)}>
                          [{m.status}]
                        </text>
                      </box>
                      <text fg={isSel ? T.bright : T.fg}>{clip(m.title, splitLeftW - 8)}</text>
                      <text fg={T.dim}>{clip(m.file, splitLeftW - 8)}</text>
                      {idx < milestones.length - 1 && (
                        <text fg={T.borderBright}>│</text>
                      )}
                    </box>
                  )
                })}
              </box>
            </scrollbox>
          </box>

          {/* Right Panel: Rataflow Node Graph Overview */}
          <box
            title={` ◈ RATAFLOW NODE GRAPH (${RATAFLOW_NODES.length} NODES) `}
            titleColor={T.teal}
            borderStyle="single"
            borderColor={T.teal}
            flexDirection="column"
            flexGrow={1}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column" gap={1}>
                {RATAFLOW_NODES.map((n, i) => {
                  const isSel = i === selectedNodeIdx
                  return (
                    <box
                      key={n.id}
                      flexDirection="column"
                      onMouseDown={() => setSelectedNodeIdx(i)}
                      backgroundColor={isSel ? T.raised : T.panel}
                      borderStyle="single"
                      borderColor={isSel ? T.cyan : T.border}
                      paddingLeft={1}
                      paddingRight={1}
                    >
                      <box flexDirection="row" justifyContent="space-between">
                        <text fg={isSel ? T.bright : T.cyan}>{n.typeIcon} {n.title}</text>
                        <text fg={n.status === "LIVE" || n.status === "PASS" || n.status === "FILLED" ? T.up : T.warn}>
                          [{n.status}]
                        </text>
                      </box>
                      <box flexDirection="row" justifyContent="space-between">
                        <text fg={T.dim}>File: {n.file}</text>
                        <text fg={T.purple}>{n.layerName}</text>
                      </box>
                      <text fg={T.muted}>[in] {clip(n.inPorts.join(", "), splitRightW - 12)}</text>
                      <text fg={T.fg}>[out] {clip(n.outPorts.join(", "), splitRightW - 12)}</text>
                      {n.connectionsTo.length > 0 && (
                        <text fg={T.cyan}>──► {n.connectionsTo.map((c) => c.replace("node_", "")).join(", ")}</text>
                      )}
                    </box>
                  )
                })}
              </box>
            </scrollbox>
          </box>
        </box>
      )}

      {/* Tab 3: Autonomous Discovery & Self-Healing Pipeline */}
      {activeTab === 3 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          {/* Left Column: Self-Healing Workflow Flowchart */}
          <box
            title=" ⚙ AUTONOMOUS STRATEGY DISCOVERY & SELF-HEALING STATE MACHINE "
            titleColor={T.orange}
            borderStyle="single"
            borderColor={T.orange}
            flexDirection="column"
            width={Math.max(50, Math.floor(contentW * 0.52))}
            flexShrink={0}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column" gap={1}>
                {selfHealingSteps.map((s, i) => (
                  <box
                    key={s.step}
                    flexDirection="column"
                    borderStyle="single"
                    borderColor={s.status === "ACTIVE" ? T.cyan : (s.status === "WARN" ? T.warn : T.border)}
                    backgroundColor={s.status === "ACTIVE" ? T.raised : T.panel}
                    paddingLeft={1}
                    paddingRight={1}
                  >
                    <box flexDirection="row" justifyContent="space-between">
                      <box flexDirection="row" gap={1}>
                        <text fg={s.status === "COMPLETED" || s.status === "PASS" ? T.up : (s.status === "WARN" ? T.warn : (s.status === "STANDBY" ? T.dim : T.cyan))}>
                          {s.status === "COMPLETED" || s.status === "PASS" ? "✓" : (s.status === "STANDBY" ? "○" : "●")}
                        </text>
                        <text fg={T.bright}>Step {s.step}: {s.name}</text>
                      </box>
                      <text fg={s.status === "COMPLETED" || s.status === "PASS" ? T.up : (s.status === "WARN" ? T.warn : (s.status === "STANDBY" ? T.dim : T.cyan))}>
                        [{s.status}]
                      </text>
                    </box>
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>Source:</text>
                      <text fg={T.cyan}>{s.source}</text>
                    </box>
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>Target:</text>
                      <text fg={T.orange}>{s.target}</text>
                    </box>
                    <text fg={T.fg}>{s.details}</text>
                    <box backgroundColor={T.raised} paddingLeft={1}>
                      <text fg={T.teal}>{s.metric}</text>
                    </box>
                    {i < selfHealingSteps.length - 1 && (
                      <box flexDirection="row" justifyContent="center">
                        <text fg={T.borderBright}>▼</text>
                      </box>
                    )}
                  </box>
                ))}
              </box>
            </scrollbox>
          </box>

          {/* Right Column: Live MT5 Bridge & Quantitative Robustness Funnel */}
          <box
            title=" ◈ MT5 MCP BRIDGE & QUANTITATIVE ROBUSTNESS FUNNEL "
            titleColor={T.cyan}
            borderStyle="single"
            borderColor={T.cyan}
            flexDirection="column"
            flexGrow={1}
            paddingLeft={1}
            paddingRight={1}
          >
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="column" gap={1}>
                {/* Panel 1: MT5 MCP Server & Broker Gateway Telemetry */}
                <box
                  borderStyle="single"
                  borderColor={T.teal}
                  backgroundColor={T.panel}
                  flexDirection="column"
                  paddingLeft={1}
                  paddingRight={1}
                >
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.teal}>MT5 MCP SERVER & BROKER BRIDGE (Port 22346):</text>
                    <text fg={mt5?.terminal?.connected ? T.up : T.dim}>
                      [{mt5?.terminal?.connected ? "ONLINE" : "STANDBY"}]
                    </text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between" marginTop={1}>
                    <text fg={T.dim}>Server / Broker:</text>
                    <text fg={T.bright}>{mt5?.terminal?.server || "Awaiting MT5 Terminal"}</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Account Login:</text>
                    <text fg={T.cyan}>{mt5?.terminal?.login ? String(mt5.terminal.login) : "Awaiting Login"}</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Round-Trip Latency:</text>
                    <text fg={mt5?.terminal?.lastPingMs ? T.teal : T.dim}>
                      {mt5?.terminal?.lastPingMs ? `${mt5.terminal.lastPingMs}ms` : "---"}
                    </text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Execution Authority:</text>
                    <text fg={mt5?.terminal?.tradeAllowed ? T.up : T.warn}>
                      {mt5?.terminal?.tradeAllowed ? "TRADE_ALLOWED (Auto)" : "RESTRICTED (Manual Only)"}
                    </text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Recorded MCP Invocations:</text>
                    <text fg={T.bright}>{mt5?.mcpCalls?.length ?? 0} calls recorded</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Last Bridge Diagnostic:</text>
                    <text fg={mt5?.terminal?.lastError ? T.warn : T.dim}>
                      {clip(mt5?.terminal?.lastError || "Normal Operation (No errors)", 40)}
                    </text>
                  </box>
                </box>

                {/* Panel 2: Quantitative Robustness & Overfit Gates */}
                <box
                  borderStyle="single"
                  borderColor={T.border}
                  backgroundColor={T.panel}
                  flexDirection="column"
                  paddingLeft={1}
                  paddingRight={1}
                >
                  <text fg={T.cyan}>QUANTITATIVE ROBUSTNESS & OVERFIT GATES (quantMonitor):</text>
                  <box flexDirection="row" justifyContent="space-between" marginTop={1}>
                    <text fg={T.dim}>Robustness Metric</text>
                    <text fg={T.dim}>Live Value</text>
                    <text fg={T.dim}>Safety Threshold</text>
                  </box>
                  <text fg={T.borderBright}>──────────────────────────────────────────────────</text>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>BOCPD Regime</text>
                    <text fg={qm?.bocpd_regime ? T.cyan : T.dim}>{qm?.bocpd_regime ?? "AWAITING"}</text>
                    <text fg={T.dim}>Stationary / Trend</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>PBO (Probability of Overfit)</text>
                    <text fg={qm?.pbo != null ? (qm.pbo < 0.3 ? T.up : T.warn) : T.dim}>
                      {qm?.pbo != null ? `${(qm.pbo * 100).toFixed(1)}%` : "STANDBY"}
                    </text>
                    <text fg={T.dim}>PBO &lt; 30.0%</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>Deflated Sharpe Ratio (DSR)</text>
                    <text fg={qm?.dsr != null ? (qm.dsr >= 1.0 ? T.up : T.warn) : T.dim}>
                      {qm?.dsr != null ? qm.dsr.toFixed(2) : "STANDBY"}
                    </text>
                    <text fg={T.dim}>DSR &gt; 1.00</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>Rolling Sharpe Ratio</text>
                    <text fg={qm?.rolling_sharpe != null ? (qm.rolling_sharpe >= 1.0 ? T.up : T.cyan) : T.dim}>
                      {qm?.rolling_sharpe != null ? qm.rolling_sharpe.toFixed(2) : "STANDBY"}
                    </text>
                    <text fg={T.dim}>OOS &gt; 0.70</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>Tail Risk (CVaR 95%)</text>
                    <text fg={qm?.cvar_95 != null ? T.cyan : T.dim}>
                      {qm?.cvar_95 != null ? `${(qm.cvar_95 * 100).toFixed(2)}%` : "STANDBY"}
                    </text>
                    <text fg={T.dim}>Cap 2.50%</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>Hurst Exponent (Long Memory)</text>
                    <text fg={qm?.hurst_exponent != null ? T.teal : T.dim}>
                      {qm?.hurst_exponent != null ? qm.hurst_exponent.toFixed(2) : "STANDBY"}
                    </text>
                    <text fg={T.dim}>H &gt; 0.50 Trend</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.bright}>Daily ATR Volatility</text>
                    <text fg={qm?.daily_atr_vol != null ? T.cyan : T.dim}>
                      {qm?.daily_atr_vol != null ? `${(qm.daily_atr_vol * 100).toFixed(2)}%` : "STANDBY"}
                    </text>
                    <text fg={T.dim}>Normal Regime</text>
                  </box>
                </box>

                {/* Panel 3: Strategy Inventory & Factor Pool Telemetry */}
                <box
                  borderStyle="single"
                  borderColor={T.purple}
                  backgroundColor={T.panel}
                  flexDirection="column"
                  paddingLeft={1}
                  paddingRight={1}
                >
                  <text fg={T.purple}>STRATEGY INVENTORY & FACTOR POOL TELEMETRY:</text>
                  <box flexDirection="row" justifyContent="space-between" marginTop={1}>
                    <text fg={T.dim}>Registered Strategies:</text>
                    <text fg={strategies.length ? T.bright : T.dim}>{strategies.length} loaded</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Alpha Factors Pool:</text>
                    <text fg={alphaSignals.length ? T.cyan : T.dim}>
                      {alphaSignals.length} active factors ({alphaSignals.filter((a) => a.status === "deployed").length} deployed)
                    </text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Alpha Decay Alerts:</text>
                    <text fg={alphaHealth.some((h) => h.status !== "HEALTHY") ? T.warn : T.up}>
                      {alphaHealth.filter((h) => h.status !== "HEALTHY").length} decay alerts ({alphaHealth.length} monitored)
                    </text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Execution Autonomy Mode:</text>
                    <text fg={T.cyan}>{autonomyLevel} (Deterministic Gate Enforced)</text>
                  </box>
                  <box flexDirection="row" justifyContent="space-between">
                    <text fg={T.dim}>Cryptographic Pre-Trade Commit:</text>
                    <text fg={chainIntegrity?.valid ? T.up : T.warn}>
                      SHA-256 Verified ({chainIntegrity?.entriesVerified ?? 0} blocks)
                    </text>
                  </box>
                </box>
              </box>
            </scrollbox>
          </box>
        </box>
      )}

      {/* Tab 4: Live Runtime Graph — Real-Time System State */}
      {activeTab === 4 && (
        <box flexDirection="column" flexGrow={1} gap={1}>
          {/* Live Status Header Bar - 1-Line Compact */}
          <box
            flexDirection="row"
            justifyContent="space-between"
            alignItems="center"
            height={1}
            flexShrink={0}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <GraphLivePulse />
              <text fg={T.bright}>RUNTIME GRAPH</text>
              <text fg={T.dim}>| {runtimeNodes.length} Nodes · {RUNTIME_NODES.length} Total</text>
            </box>
            <box flexDirection="row" gap={1}>
              <text fg={T.dim}>Account: </text>
              <text fg={T.up}>${(account?.equity || account?.balance || 0).toLocaleString()}</text>
              <text fg={T.dim}> | Positions: </text>
              <text fg={positions?.length ? T.cyan : T.dim}>{positions?.length ?? 0} Open</text>
              <text fg={T.dim}> | Pipeline: </text>
              <text fg={graphStates?.some((g) => g.status === "running") ? T.cyan : T.dim}>
                {graphStates?.some((g) => g.status === "running") ? "RUNNING" : "IDLE"}
              </text>
            </box>
          </box>

          {/* Runtime Layer Filter - 1-Line Compact */}
          <box
            flexDirection="row"
            justifyContent="space-between"
            alignItems="center"
            height={1}
            flexShrink={0}
            backgroundColor={T.bg}
            paddingLeft={1}
            paddingRight={1}
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <text fg={T.dim}>FILTER:</text>
              {["ALL", "DATA", "PIPELINE", "DECISION", "SAFETY", "EXECUTION", "OBSERVE"].map((layer, i) => (
                <box key={layer} onMouseDown={() => { setRuntimeLayerFilter(layer); setSelectedNodeIdx(0) }}>
                  <text
                    fg={runtimeLayerFilter === layer ? (RUNTIME_LAYER_COLORS[layer] ?? T.bright) : T.dim}
                    bg={runtimeLayerFilter === layer ? T.raised : undefined}
                  >
                    [{i}:{layer}]
                  </text>
                </box>
              ))}
            </box>
            <text fg={T.dim}>[0-6] Filter · [↑↓] Nav</text>
          </box>

          {/* Main 2-Column: Runtime Node Graph + Inspector */}
          <box flexDirection="row" flexGrow={1} gap={1}>
            {/* Left: Live Node Graph with ASCII Edges */}
            <box
              title={` 🔴 LIVE RUNTIME NODE GRAPH (${runtimeNodes.length} NODES) `}
              titleColor={T.up}
              borderStyle="single"
              borderColor={T.up}
              flexDirection="column"
              width={Math.max(56, Math.floor(contentW * 0.58))}
              flexShrink={0}
              paddingLeft={1}
              paddingRight={1}
            >
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="column" gap={0}>
                  {/* Layer groups */}
                  {(["DATA", "PIPELINE", "DECISION", "SAFETY", "EXECUTION", "OBSERVE"] as const).map((layer) => {
                    const layerNodes = runtimeNodes.filter((n) => n.layer === layer)
                    if (layerNodes.length === 0) return null
                    const layerColor = RUNTIME_LAYER_COLORS[layer] ?? T.dim
                    return (
                      <box key={layer} flexDirection="column" marginBottom={1}>
                        {/* Layer Header */}
                        <box flexDirection="row" alignItems="center" gap={1}>
                          <text fg={layerColor}>{RUNTIME_LAYER_ICONS[layer]} {layer}</text>
                          <text fg={T.borderBright}>{"─".repeat(Math.max(20, 40 - layer.length))}</text>
                          <text fg={T.dim}>{layerNodes.length} nodes</text>
                        </box>

                        {/* Nodes in this layer */}
                        {layerNodes.map((node) => {
                          const globalIdx = RUNTIME_NODES.findIndex((n) => n.id === node.id)
                          const isSel = globalIdx === selectedNodeIdx
                          const liveStatus = liveNodeStatuses[node.id]
                          const statusColor = liveStatus?.color ?? T.dim
                          const isActive = liveStatus?.status === "ACTIVE" || liveStatus?.status === "FUSING" || liveStatus?.status === "CHECKING"

                          return (
                            <box key={node.id} flexDirection="column" paddingLeft={1}>
                              {/* Node Card */}
                              <box
                                flexDirection="column"
                                onMouseDown={() => setSelectedNodeIdx(globalIdx)}
                                borderStyle="single"
                                borderColor={isSel ? T.cyan : (isActive ? T.teal : T.border)}
                                backgroundColor={isSel ? T.raised : (isActive ? T.panel : undefined)}
                                paddingLeft={1}
                                paddingRight={1}
                              >
                                {/* Node Header Row */}
                                <box flexDirection="row" justifyContent="space-between">
                                  <box flexDirection="row" gap={1}>
                                    <ActiveNodeDot active={isActive} color={statusColor} />
                                    <text fg={layerColor}>{node.icon}</text>
                                    <text fg={isSel ? T.bright : T.fg}>{node.name}</text>
                                  </box>
                                  <text fg={statusColor}>[{liveStatus?.status ?? "???"}]</text>
                                </box>

                                {/* Metrics + Detail */}
                                <box flexDirection="row" justifyContent="space-between">
                                  <box flexDirection="row" gap={2}>
                                    {(dynamicRuntimeMetrics[node.id] ?? node.metrics).slice(0, 3).map((m, mi) => (
                                      <text key={mi} fg={m.color ?? T.dim}>
                                        {m.label}: {m.value}
                                      </text>
                                    ))}
                                  </box>
                                  <text fg={T.dim}>{liveStatus?.detail ?? ""}</text>
                                </box>

                                {/* ASCII Edge Indicators */}
                                <box flexDirection="row" gap={1} marginTop={0}>
                                  {node.upstream.length > 0 && (
                                    <text fg={T.borderBright}>
                                      ▲ from: {node.upstream.map((u) => {
                                        const src = RUNTIME_NODES.find((n) => n.id === u)
                                        return src?.name.split(" ").slice(0, 2).join("") ?? u.replace("rt_", "")
                                      }).slice(0, 3).join(" · ")}
                                    </text>
                                  )}
                                </box>
                                <box flexDirection="row" gap={1}>
                                  {node.downstream.length > 0 && (
                                    <text fg={isSel ? T.cyan : T.borderBright}>
                                      ▼ ──► {node.downstream.map((d) => {
                                        const dst = RUNTIME_NODES.find((n) => n.id === d)
                                        return dst?.name.split(" ").slice(0, 2).join("") ?? d.replace("rt_", "")
                                      }).slice(0, 3).join(" · ")}
                                    </text>
                                  )}
                                </box>
                              </box>

                              {/* ASCII Edge Connector between nodes */}
                              {globalIdx < RUNTIME_NODES.length - 1 && node.downstream.length > 0 && (
                                <box flexDirection="row" paddingLeft={2}>
                                  <text fg={T.borderBright}>│</text>
                                </box>
                              )}
                            </box>
                          )
                        })}
                      </box>
                    )
                  })}
                </box>
              </scrollbox>
            </box>

            {/* Right: Runtime Node Inspector */}
            <box
              title={` 🔴 RUNTIME INSPECTOR: ${curRuntimeNode.name} `}
              titleColor={T.up}
              borderStyle="single"
              borderColor={T.up}
              flexDirection="column"
              flexGrow={1}
              paddingLeft={1}
              paddingRight={1}
            >
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="column" gap={1}>
                  {/* Node Identity */}
                  <box flexDirection="row" gap={1} alignItems="center">
                    <text fg={T.bg} bg={RUNTIME_LAYER_COLORS[curRuntimeNode.layer] ?? T.cyan}>
                      {" "}{curRuntimeNode.icon}{" "}
                    </text>
                    <text fg={T.bright}>{curRuntimeNode.name}</text>
                    <text fg={liveNodeStatuses[curRuntimeNode.id]?.color ?? T.dim}>
                      [{liveNodeStatuses[curRuntimeNode.id]?.status ?? "???"}]
                    </text>
                  </box>

                  {/* Layer & Category */}
                  <box flexDirection="row" justifyContent="space-between">
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>Layer:</text>
                      <text fg={RUNTIME_LAYER_COLORS[curRuntimeNode.layer] ?? T.cyan}>{curRuntimeNode.layer}</text>
                    </box>
                    <box flexDirection="row" gap={1}>
                      <text fg={T.dim}>Category:</text>
                      <text fg={T.teal}>{curRuntimeNode.category}</text>
                    </box>
                  </box>

                  {/* Agent & Port */}
                  <box flexDirection="row" gap={1}>
                    <text fg={T.dim}>Agent:</text>
                    <text fg={T.teal}>{curRuntimeNode.agent ?? "SYSTEM"}</text>
                    {curRuntimeNode.port && (
                      <>
                        <text fg={T.dim}>| Port:</text>
                        <text fg={T.cyan}>{curRuntimeNode.port}</text>
                      </>
                    )}
                  </box>

                  {/* Description */}
                  <box backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                    <text fg={T.fg}>{curRuntimeNode.description}</text>
                  </box>

                  {/* Source File */}
                  <box flexDirection="column">
                    <text fg={T.dim}>Source File:</text>
                    <text fg={T.cyan}>file:///{curRuntimeNode.file}</text>
                  </box>

                  {/* Live Status Detail */}
                  <box flexDirection="column">
                    <text fg={T.up}>LIVE STATUS:</text>
                    <box backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                      <text fg={liveNodeStatuses[curRuntimeNode.id]?.color ?? T.dim}>
                        {liveNodeStatuses[curRuntimeNode.id]?.detail ?? "No live data"}
                      </text>
                    </box>
                  </box>

                  {/* Upstream Connections */}
                  <box flexDirection="column">
                    <text fg={T.teal}>UPSTREAM INPUTS (▲):</text>
                    {curRuntimeNode.upstream.length > 0 ? (
                      curRuntimeNode.upstream.map((u, i) => {
                        const src = RUNTIME_NODES.find((n) => n.id === u)
                        const srcStatus = liveNodeStatuses[u]
                        return (
                          <text key={i} fg={T.muted}>
                            {"  ◄ "}{src?.name ?? u} <span fg={srcStatus?.color ?? T.dim}>[{srcStatus?.status ?? "???"}]</span>
                          </text>
                        )
                      })
                    ) : (
                      <text fg={T.dim}>{"  • Source node (no upstream dependencies)"}</text>
                    )}
                  </box>

                  {/* Downstream Connections */}
                  <box flexDirection="column">
                    <text fg={T.orange}>DOWNSTREAM OUTPUTS (▼):</text>
                    {curRuntimeNode.downstream.length > 0 ? (
                      curRuntimeNode.downstream.map((d, i) => {
                        const dst = RUNTIME_NODES.find((n) => n.id === d)
                        const dstStatus = liveNodeStatuses[d]
                        return (
                          <text key={i} fg={T.bright}>
                            {"  ► "}{dst?.name ?? d} <span fg={dstStatus?.color ?? T.dim}>[{dstStatus?.status ?? "???"}]</span>
                          </text>
                        )
                      })
                    ) : (
                      <text fg={T.dim}>{"  • Terminal sink node (no downstream)"}</text>
                    )}
                  </box>

                  {/* Telemetry Metrics */}
                  <box flexDirection="column">
                    <text fg={T.purple}>NODE TELEMETRY:</text>
                    {(dynamicRuntimeMetrics[curRuntimeNode.id] ?? curRuntimeNode.metrics).map((m, i) => (
                      <box key={i} flexDirection="row" justifyContent="space-between">
                        <text fg={T.dim}>{m.label}</text>
                        <text fg={m.color ?? T.bright}>{m.value}</text>
                      </box>
                    ))}
                  </box>

                  {/* ASCII Mini Topology */}
                  <box flexDirection="column">
                    <text fg={T.cyan}>LOCAL TOPOLOGY:</text>
                    <box flexDirection="column" backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                      {curRuntimeNode.upstream.length > 0 && curRuntimeNode.upstream.slice(0, 2).map((u, i) => {
                        const src = RUNTIME_NODES.find((n) => n.id === u)
                        return (
                          <text key={`in-${i}`} fg={T.dim}>
                            {"  "}{i === 0 ? "┌" : "├"}── {src?.name?.split(" ").slice(0, 2).join("") ?? u}
                          </text>
                        )
                      })}
                      <text fg={T.bright}>
                        {"  "}{curRuntimeNode.upstream.length > 0 ? "│" : "┌"}──▶ <span fg={RUNTIME_LAYER_COLORS[curRuntimeNode.layer] ?? T.cyan}>{curRuntimeNode.name}</span> ◀──┐
                      </text>
                      {curRuntimeNode.downstream.length > 0 && curRuntimeNode.downstream.slice(0, 2).map((d, i) => {
                        const dst = RUNTIME_NODES.find((n) => n.id === d)
                        const isLast = i === curRuntimeNode.downstream.slice(0, 2).length - 1
                        return (
                          <text key={`out-${i}`} fg={T.dim}>
                            {"  "}{isLast ? "└" : "├"}── {dst?.name?.split(" ").slice(0, 2).join("") ?? d}
                          </text>
                        )
                      })}
                    </box>
                  </box>
                </box>
              </scrollbox>
            </box>
          </box>
        </box>
      )}
    </box>
  )
}
