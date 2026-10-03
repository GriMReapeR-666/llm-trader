export type AgentId =
  | "TechnicalNet"
  | "MacroAnalyst"
  | "MarketModel"
  | "RiskController"
  | "QuantitativeCoach"

export type ViewId =
  | "stream"
  | "cockpit"
  | "brain"
  | "causal"
  | "macro"
  | "positions"
  | "journal"
  | "tasks"
  | "strategies"
  | "analytics"
  | "approvals"
  | "map"
  | "graph"
  | "skills"
  | "backtest"
  | "alpha"
  | "diagnostics"
  | "workflows"
  | "lifecycle"
  | "fusion"
  | "research"
  | "datasources"
  | "scanner"
  | "rl"
  | "validation"
  | "memory"
  | "deliberation"
  | "health"
  | "settings"
  | "autonomy"
  | "rewards"
  | "security"
  | "growth"
  | "models"
  | "metrics"
  | "drills"
  | "mt5"
  | "logs"
  | "orchestration"
  | "observatory"

export interface LogEntry {
  id: number
  ts: string
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"
  logger: string
  message: string
}

export interface LogFilter {
  level: string
  query: string
  regex: boolean
  caseSensitive: boolean
}

export type TaskStatus = "queued" | "running" | "done" | "error" | "skipped"

export interface TaskItem {
  id: string
  stage: string
  label: string
  status: TaskStatus
  agent: string
  startedTs?: number
  endedTs?: number
  durationMs?: number
  detail?: string
}

export interface ToolCall {
  id: number
  taskId: string
  ts: number
  tool: string
  argsSummary: string
  resultSummary: string
  latencyMs: number
  ok: boolean
  tokens?: number
}

export interface StrategyState {
  id: string
  name: string
  symbols: string[]
  timeframe: string
  stage: "CANDIDATE" | "EVALUATION" | "FULL" | "RETIRED" | "QUARANTINE"
  gateProgress: number
  trades: number
  winRate: number
  profitFactor: number
  sharpe: number
  maxDD: number
  expectancyR: number
  recoveryFactor: number
  equityCurve: number[]
  activeToday: boolean
  lastCycleTs: number
  notes: string
}

export interface LoopStats {
  cyclesCompleted: number
  avgCycleMs: number
  lastCycleTs: number
}

export type ApprovalKind = "PROMOTION" | "ROLLOUT_L3" | "CONFIG_CHANGE" | "DEMOTION_NOTICE"

export interface Approval {
  id: string
  kind: ApprovalKind
  title: string
  detailLines: string[]
  signatureRequired: boolean
  requestedTs: number
  status: "PENDING" | "APPROVED" | "REJECTED"
  decidedBy?: string
  decidedTs?: number
}

export type AgentStatus = "idle" | "working" | "error"

export interface AgentState {
  status: AgentStatus
  task: string | null
  lastBeat: number
  runsToday: number
}

export type StreamKind =
  | "system"
  | "agent_note"
  | "tool"
  | "thinking"
  | "decision"
  | "order"
  | "validation"
  | "fill"
  | "alert"
  | "user"

export interface StreamItem {
  id: number
  ts: number
  kind: StreamKind
  agent: string
  title: string
  lines: string[]
  severity?: "info" | "warn" | "critical"
  meta?: Record<string, string>
  /** Structured reasoning trail — rendered as a distinct, foldable block
   *  wherever it is part of the event (decision cards especially). */
  reasoning?: string[]
  /** Unified diff of proposed vs validated intent parameters. */
  intentDiff?: string[]
}

export interface Position {
  ticket: number
  symbol: string
  side: "BUY" | "SELL"
  volume: number
  entry: number
  sl: number
  tp: number
  pnl: number
  riskR: number
  unrealizedR: number
  ageMin: number
  strategyId: string
}

export interface CycleBrief {
  id: string
  ts: number
  symbol: string
  timeframe: string
  strategy: string
  regime: string
  decision: "EXECUTE" | "HOLD" | "REJECT"
  confidence: number
  reasoning: string
  intent?: {
    proposed: string[]
    validated: string[]
    diff: string
  }
  features?: { name: string; value: string }[]
}

export interface CausalEdge {
  from: string
  to: string
  strength: number
  lag: string
  samples: number
  direction: 1 | -1
}

export interface Narrative {
  id: string
  headline: string
  tag: string
  weight: number
  ts: number
}

export interface Nowcast {
  name: string
  value: number
  unit: string
  trend: -1 | 0 | 1
}

export interface JournalCheck {
  rule: string
  pass: boolean
  detail: string
}

export interface OpEvent {
  id: number
  ts: number
  actor: string
  type: string
  severity: "info" | "warn" | "critical"
  payload: string
}

export interface Alert {
  id: number
  ts: number
  severity: "warn" | "critical"
  text: string
}

/** P7 incidents pane: one durable health alert + its operator runbook pointer. */
export interface IncidentCard {
  code: string
  severity: "warn" | "critical" | "info"
  count: number
  runbook: string
  first_response: string
}

export interface IncidentsState {
  status: "OK" | "WARNING" | "CRITICAL" | "UNKNOWN"
  cards: IncidentCard[]
  checkedAt: number | null
  killSwitch: { active: boolean; trading_enabled: boolean } | null
  selected: number
}

export interface GraphNode {
  id: string
  label: string
  type: "agent" | "gate" | "data" | "decision" | "action" | "transform"
  status: TaskStatus
  agent?: string
  durationMs?: number
  x: number
  y: number
}

export interface GraphEdge {
  from: string
  to: string
  label?: string
  active?: boolean
}

export interface GraphState {
  id: string
  name: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  currentNode: string
  startedTs: number
  status: "running" | "complete" | "error" | "waiting"
  cycleId: string
}

export interface Skill {
  id: string
  name: string
  category: "analysis" | "execution" | "risk" | "data" | "research" | "memory"
  description: string
  status: "active" | "idle" | "error"
  executions: number
  avgLatencyMs: number
  successRate: number
  lastRunTs?: number
  embedded: boolean
}

export interface SkillExecution {
  id: string
  skillId: string
  ts: number
  input: string
  output: string
  latencyMs: number
  ok: boolean
  error?: string
}

export interface BacktestResult {
  id: string
  strategyId: string
  period: string
  totalTrades: number
  winRate: number
  profitFactor: number
  sharpe: number
  maxDD: number
  calmar: number
  sortino: number
  expectancyR: number
  equityCurve: number[]
  monthlyReturns: number[]
  status: "running" | "complete" | "error"
  startedTs: number
  completedTs?: number
}

export interface AlphaSignal {
  id: string
  name: string
  source: string
  strength: number
  confidence: number
  symbols: string[]
  timeframe: string
  discoveredTs: number
  status: "candidate" | "validated" | "deployed" | "retired"
  ic: number
  sharpe: number
  turnover: number
  decay: number
}

export interface AlphaHealth {
  name: string
  status: "HEALTHY" | "DEGRADED" | "REVERSED" | "DEAD"
  icMean: number
  icCurrent: number
  icTrend: number
  consecutiveLow: number
}

export interface EconomicEvent {
  id: string
  title: string
  time: string
  currency: string
  impact: "HIGH" | "MODERATE" | "LOW"
  actual?: string
  forecast?: string
  previous?: string
  deviation?: string
  country: string
}

export interface ErrorCode {
  code: string
  module: string
  message: string
  severity: "info" | "warn" | "error" | "critical"
  count: number
  lastTs: number
}

export interface LintIssue {
  file: string
  line: number
  rule: string
  severity: "error" | "warning" | "info"
  message: string
}

export interface ScannerResult {
  symbol: string
  score: number
  reasons: string[]
  atr: number
  price: number
  providerContext: {
    providersUsed: string[]
    cftcStatus: string
    providerScore: number
  }
}

export interface ComplianceEvent {
  id: string
  ts: number
  type: string
  agent: string
  symbol: string
  severity: "SUCCESS" | "WARNING" | "CRITICAL" | "INFO"
  details: string
}

export interface ChainIntegrity {
  valid: boolean
  entriesVerified: number
  durationMs: number
}

export interface ModuleGroup {
  name: string
  count: number
  lastTs: number
}

export interface TradeHistoryEntry {
  ticket: number
  strategy: string
  symbol: string
  side: string
  volume: number
  entry: number
  stopLoss: number
  takeProfit: number
  riskAmount: number
  openedAt: string
  closedAt: string
  exitPrice: number
  pnl: number
  exitReason: string
}

export interface WorkflowStep {
  name: string
  passed: boolean
  metrics: Record<string, string | number>
  durationMs: number
  reason?: string
}

export interface WorkflowExecution {
  id: string
  workflowName: string
  symbol: string
  decision: "EXECUTE" | "HOLD" | "REJECT" | "CIRCUIT_BREAKER"
  steps: WorkflowStep[]
  totalLatencyMs: number
  timestamp: number
  ticket?: number
}

export type StrategyLifecycleState =
  | "CANDIDATE"
  | "VALIDATING"
  | "PAPER_PROVEN"
  | "DEMO_PROVEN"
  | "DEPLOYED"
  | "DECAYING"
  | "FROZEN"
  | "RETIRED"

export type EdgeLifecycleState =
  | "CANDIDATE"
  | "MEASURED"
  | "STAT_PASS"
  | "REPLAY_PASS"
  | "DEMO_MATCH"
  | "TINY_LIVE"
  | "FULL"
  | "RETIRING"
  | "RETIRED"
  | "STANDBY"

export interface LifecycleTransition {
  from: string | null
  to: string
  actor: string
  reason: string
  timestamp: number
}

export interface LifecycleEntry {
  id: string
  name: string
  state: StrategyLifecycleState
  drift?: string
  transitions: LifecycleTransition[]
  blocked: boolean
  updatedAt: number
}

export interface EdgeEntry {
  id: string
  name: string
  state: EdgeLifecycleState
  strategyId: string
  transitions: LifecycleTransition[]
  blocked: boolean
  updatedAt: number
}

export type SourceTier = "PRIMARY" | "RUNTIME_FEED" | "UNVERIFIED"

export interface CausalChainHop {
  edgeId: number
  sourceName: string
  targetName: string
  relation: string
  sign: "POSITIVE" | "NEGATIVE" | "CONDITIONAL"
  confidence: number
  decayHalfLifeSec: number
}

export interface CausalChainPath {
  pathId: string
  score: number
  bias: "BUY" | "SELL" | "NEUTRAL"
  horizonSec: number
  hops: CausalChainHop[]
  terminal: string
  timestamp: number
}

export interface EnhancedCausalEdge {
  from: string
  to: string
  strength: number
  lag: string
  samples: number
  direction: 1 | -1
  lifecycle: EdgeLifecycleState
  sourceTier: SourceTier
  sourceEntity: string
  relation: string
  confidence: number
  decayHalfLifeSec: number
  ageSec: number
}

export interface FusionResult {
  id: string
  symbol: string
  strategy: string
  side: "BUY" | "SELL"
  status: "PASS" | "WEAK" | "BLOCK"
  score: number
  eventOk: boolean
  causalAgree: boolean
  propagationAgree: boolean
  taOk: boolean
  agreeingEdges: string[]
  reasons: string[]
  propagationPath?: string
  timestamp: number
}

export interface EventReaction {
  id: string
  eventName: string
  symbol: string
  actual: number
  forecast: number
  previous: number
  surprise: number
  windows: Record<string, number>
  sourceQuality: SourceTier
  timestamp: number
}

export type ResearchStage =
  | "INGEST"
  | "EXTRACT"
  | "VALIDATE"
  | "BACKTEST"
  | "CPCV"
  | "WALK_FORWARD"
  | "MONTE_CARLO"
  | "PAPER"
  | "PROMOTE"
  | "DEPLOY"

export interface ResearchPipelineEntry {
  id: string
  name: string
  source: string
  stage: ResearchStage
  startedTs: number
  stageProgress: number
  metrics: Record<string, string | number>
  notes: string
}

export type DataSourceHealth = "healthy" | "degraded" | "down" | "circuit_open"

export interface DataSource {
  id: string
  name: string
  tier: "REALTIME" | "DAILY" | "WEEKLY" | "FREE_STACK"
  category: string
  health: DataSourceHealth
  lastFetchTs: number
  fetchCount: number
  errorCount: number
  avgLatencyMs: number
  description: string
}

export interface LLMProvider {
  id: string
  name: string
  baseUrl: string
  models: LLMModel[]
  health: DataSourceHealth
  rpm: number
  requestsToday: number
  failoverPriority: number
}

export interface LLMModel {
  id: string
  name: string
  provider: string
  latencyMs: number
  tokensUsed: number
  isDefault: boolean
}

export interface WinProbResult {
  winProbability: number
  confidence: number
  kellyFraction: number
  recommendation: "FULL_SIZE" | "HALF_SIZE" | "QUARTER_SIZE" | "SKIP"
  features: { name: string; value: number; importance: number }[]
}

export interface IntermarketRegime {
  pair: string
  correlation: number
  regime: "RISK_ON" | "RISK_OFF" | "NEUTRAL" | "DIVERGENT"
  lag: string
  signal: string
  strength: number
}

export interface AccountState {
  balance: number
  equity: number
  dayStartEquity: number
  peakEquity: number
  drawdownPct: number
  heatUsedR: number
  heatCapR: number
  winRate: number
  openRiskPct: number
  consecutiveLosses: number
  equityCurve: number[]
  totalTrades: number
  openPositions: number
}

export interface MarketQuote {
  price: number
  changePct: number
  domImbalance: number
  history: number[]
}

export interface RLTrainingMetrics {
  totalUpdates: number
  explorationCount: number
  exploitationCount: number
  explorationRate: number
  qTableSize: number
  avgReward100: number
  totalReward: number
  bestReward: number
  worstReward: number
}

export interface RLEpisode {
  episode: number
  steps: number
  reward: number
  pnl: number | null
}

export interface PPOUpdate {
  update: number
  timesteps: number
  meanReward: number
  meanAdvantage: number
  policyLoss: number
  valueLoss: number
  entropy: number
}

export interface RLTrainerState {
  qLearning: RLTrainingMetrics
  ppo: {
    policyLoss: number
    valueLoss: number
    entropy: number
    clipEps: number
    gamma: number
    gaeLambda: number
    backend: string
    actions: string[]
  }
  episodes: RLEpisode[]
  ppoUpdates: PPOUpdate[]
  bestStates: { key: string; buy: number; sell: number; hold: number }[]
  isTraining: boolean
  episodesCompleted: number
}

export interface ValidationPipelineStage {
  name: string
  status: "complete" | "running" | "queued" | "failed"
  startedTs?: number
  completedTs?: number
  metrics?: Record<string, string | number>
  notes?: string
}

export interface CPCVSplit {
  split: number
  trainSharpe: number
  testSharpe: number
  overfitRatio: number
  passed: boolean
}

export interface ValidationPipelineEntry {
  id: string
  strategyName: string
  symbol: string
  startedTs: number
  stages: ValidationPipelineStage[]
  cpcvSplits: CPCVSplit[]
  overallStatus: "passing" | "failing" | "running"
  deflatedSharpe: number
  pbo: number
}

export interface MemoryTierMetrics {
  name: string
  retrievals: number
  meanResults: number
  zeroResultRate: number
  meanLatencyMs: number
  outcomeLinked: number
  outcomeWinRate: number | null
}

export interface MemoryReviewItem {
  kind: "narrative" | "verified" | "scenario" | "core"
  id: number
  summary: string
  status: string
  createdAt?: string
}

export interface MemoryCandidateReview {
  status: string
  totalCandidates: number
  counts: Record<string, number>
  items: MemoryReviewItem[]
  nextActions: string[]
}

export interface MemorySystemState {
  tierMetrics: MemoryTierMetrics[]
  totalPromptTokens: number
  totalRetrievals: number
  zeroResultRate: number
  staleAtomicFacts: number
  contradictedAtomicFacts: number
  scenarioDocuments: number
  balancedScenarios: number
  candidateReview: MemoryCandidateReview
  workingContextTokens: number
  workingContextBudget: number
}

export type DeliberationNodeStatus = "pending" | "running" | "done" | "error" | "skipped"

export interface DeliberationNode {
  id: string
  name: string
  label: string
  status: DeliberationNodeStatus
  output?: string
  durationMs?: number
  timestamp?: number
}

export interface DeliberationGraphState {
  cycleId: string
  symbol: string
  timeframe: string
  strategy: string
  status: string
  nodes: DeliberationNode[]
  startedTs: number
}

export interface MetaReflectorAudit {
  overallStatus: "HEALTHY" | "WARNING" | "DEGRADED"
  trainTestGapPct: number
  regimeFlipFrequency30d: number
  rolling30Expectancy: number
  recommendedArchitecture: string
  remediationActions: string[]
  riskMultiplier: number
  timestamp: string
}

export interface DeliberationState {
  graphStates: DeliberationGraphState[]
  selectedGraph: number
  audit: MetaReflectorAudit
}

export interface KernelService {
  name: string
  version: string
  healthy: boolean
  uptime: number
  lastCheck: number
  dependencies: string[]
}

export interface CircuitBreakerState {
  level: 1 | 2 | 3
  name: string
  active: boolean
  trigger: string
  effect: string
  threshold: string
  current: string
}

export interface RiskManagerState {
  balance: number
  initialBalance: number
  peakEquity: number
  drawdownPct: number
  dailyLossPct: number
  weeklyCircuitActive: boolean
  permanentHalt: boolean
  consecutiveLosses: number
  consecutiveWins: number
  flattenReason: string | null
  ddMultiplier: number
  streakMultiplier: number
  portfolioHeatR: number
  maxPortfolioHeatR: number
  circuitBreakers: CircuitBreakerState[]
  tradeROfLast50: number[]
}

export interface RuntimeProcess {
  id: string
  name: string
  role: string
  pid: number | null
  status: "ONLINE" | "RUNNING" | "CONNECTED" | "ACTIVE" | "STANDBY" | "OFFLINE" | "LIVE" | "ERROR"
  port?: number | null
  endpoint?: string
  uptime: number
  mem_mb: number
  cpu_pct: number
  healthy: boolean
  details: string
}

export interface GuardianState {
  state: "ARMED" | "TRIPPED"
  severity: string | null
  tripReason: string | null
  tripTime: string | null
  lastHeartbeatAgeS: number
  dailyPeakEquity: number
  initialEquity: number
  auditChainValid: boolean
}

export interface RecoveryState {
  action: string
  crashDetected: boolean
  lastGoodCycle: number
  guardianStale: boolean
  orphanedPositions: Array<{ ticket: number; symbol: string; volume: number; profit: number }>
  brokerPositionCount: number
  reasons: string[]
}

export interface SystemHealthState {
  services: KernelService[]
  processes: RuntimeProcess[]
  riskManager: RiskManagerState
  guardian: GuardianState | null
  recovery: RecoveryState | null
  kernelDisposed: boolean
  contextName: string
  pluginCount: number
  incidents: IncidentsState
}

export interface AutonomyLevel {
  id: string
  name: string
  description: string
  active: boolean
  capabilities: string[]
}

export interface ConfigEntry {
  key: string
  value: string
  category: string
  description: string
  type: "number" | "string" | "boolean" | "select"
  options?: string[]
}

export interface SettingsState {
  autonomyLevels: AutonomyLevel[]
  configEntries: ConfigEntry[]
  selectedConfig: number
}

export type AutonomyLevelId = "L0_SHADOW" | "L1_PAPER" | "L2_MICRO" | "L3_FULL"

export interface ControlPlaneAction {
  id: string
  action: "pause" | "resume" | "flatten" | "killswitch" | "escalate" | "decelerate"
  actor: string
  ts: number
  reason: string
  result: "applied" | "pending" | "rejected"
}

export interface AutonomyControlPlane {
  currentLevel: AutonomyLevelId
  halted: boolean
  paused: boolean
  haltReason: string | null
  pauseReason: string | null
  flattenReason: string | null
  permanentHalt: boolean
  actions: ControlPlaneAction[]
  rolloutGate: {
    status: "idle" | "pending" | "approved" | "active"
    manifestHash: string | null
    targetLevel: AutonomyLevelId | null
    requestedTs: number | null
  }
  researchInfluenceGate: {
    status: "idle" | "pending" | "approved" | "blocked"
    modelId: string | null
    requestedTs: number | null
  }
}

export interface RewardEntry {
  id: string
  tradeId: string
  symbol: string
  strategy: string
  rNet: number
  timestamp: number
  attribution: {
    agent: string
    creditShare: number
    contribution: number
  }[]
}

export interface AgentRewardSummary {
  agent: string
  totalReward: number
  tradeCount: number
  avgCreditShare: number
  sharpeContribution: number
}

export interface CVaRMetric {
  name: string
  value: number
  threshold: number
  status: "pass" | "warn" | "fail"
}

export interface RewardSystemState {
  entries: RewardEntry[]
  agentSummaries: AgentRewardSummary[]
  totalRNet: number
  avgRNet: number
  cvarMetrics: CVaRMetric[]
  sustainabilityScore: number
  selectedEntry: number
}

export interface SecurityScanResult {
  id: string
  scanner: string
  ts: number
  findings: number
  severity: "clean" | "info" | "warn" | "critical"
  detail: string
}

export interface SecretsHygieneCheck {
  file: string
  status: "clean" | "warning" | "violation"
  detail: string
}

export interface SecurityState {
  scans: SecurityScanResult[]
  secretsHygiene: SecretsHygieneCheck[]
  journalScope: "PAPER" | "DEMO" | "LIVE" | "RESEARCH"
  gitScrubStatus: "clean" | "dirty" | "never_run"
  lastScrubTs: number | null
  selectedScan: number
}

export interface MonteCarloSim {
  id: string
  name: string
  finalEquity: number
  maxDrawdown: number
  cagr: number
  sharpe: number
  ruinProbability: number
  path: number[]
}

export interface GrowthProjection {
  horizon: string
  expectedEquity: number
  p5Equity: number
  p95Equity: number
  ruinProb: number
  kellyOptimal: number
  halfKelly: number
}

export interface GrowthState {
  monteCarloSims: MonteCarloSim[]
  projections: GrowthProjection[]
  rollingExpectancy: number
  rollingSharpe: number
  currentGrowthRate: number
  selectedSim: number
}

export interface ModelArtifact {
  id: string
  name: string
  version: string
  family: string
  status: "CANDIDATE" | "APPROVED" | "RETIRED" | "TRAINING"
  metrics: Record<string, number>
  trainingDataHash: string
  schemaVersion: string
  trainedTs: number
  approvedTs?: number
  notes: string
}

export interface ModelState {
  artifacts: ModelArtifact[]
  selectedModel: number
}

export interface LayaModelStatus {
  loaded: boolean
  modelVersion: string
  inferenceLatencyMs: number
  lastInferenceTs: number
}

export interface LayaBenchmarkMetrics {
  accuracy: number
  rawAccuracy: number
  freezeRecall: number
  winRate: number
  ece: number
}

export interface LayaDecisionCounters {
  totalDecisions: number
  overrides: number
  vetoes: number
}

export interface LayaDecision {
  slot: string
  slotLabel: string
  selectedOption: string
  confidence: number
  isOverridden: boolean
  isVetoed: boolean
  reasoning: string
}

export interface LayaDecisionBundle {
  decisions: LayaDecision[]
  counters: LayaDecisionCounters
  modelStatus: LayaModelStatus
  benchmark: LayaBenchmarkMetrics
  timestamp: number
}

export interface LayaDecisionsState {
  modelStatus: LayaModelStatus
  benchmark: LayaBenchmarkMetrics
  counters: LayaDecisionCounters
  activeBundle: LayaDecisionBundle | null
  recentBundles: LayaDecisionBundle[]
}

export interface PrometheusMetric {
  name: string
  value: number
  labels: Record<string, string>
  type: "gauge" | "counter" | "histogram"
}

export interface PrincipalHeartbeat {
  agent: string
  lastBeat: number
  latencyMs: number
  status: "alive" | "stale" | "dead"
}

export interface MetricsState {
  prometheusMetrics: PrometheusMetric[]
  heartbeats: PrincipalHeartbeat[]
  exportStatus: "active" | "paused" | "error"
  lastExportTs: number
}

export interface DrillResult {
  id: string
  name: string
  type: "kill_switch" | "duplicate_block" | "provider_circuit" | "data_quality" | "restart_recovery"
  status: "passed" | "failed" | "running" | "pending"
  startedTs: number
  completedTs?: number
  durationMs?: number
  steps: { name: string; ok: boolean; detail: string }[]
}

export interface DrillsState {
  drills: DrillResult[]
  selectedDrill: number
}

// ── MT5 live bridge ──────────────────────────────────────────────
export type MT5ConnectionState = "connected" | "disconnected" | "connecting" | "error"

export interface MT5Terminal {
  state: MT5ConnectionState
  connected: boolean
  tradeAllowed: boolean
  dllsAllowed: boolean
  tradeApiDisabled: boolean
  name: string
  company: string
  build: number
  path: string
  dataPath: string
  login: number
  server: string
  leverage: number
  currency: string
  lastPingMs: number
  lastError: string | null
}

export interface MT5SymbolSpec {
  symbol: string
  visible: boolean
  tradeMode: number // 0 disabled, 4 full
  tradeModeLabel: string
  digits: number
  point: number
  spread: number
  spreadFloat: boolean
  volumeMin: number
  volumeMax: number
  volumeStep: number
  fillingMode: number
  fillingLabel: string
  contractSize: number
  tickValue: number
  marginInitial: number
  selectOk: boolean
}

export interface MT5Tick {
  symbol: string
  bid: number
  ask: number
  last: number
  volume: number
  timeMs: number
  spreadPts: number
  flags: number
}

export interface MT5PendingOrder {
  ticket: number
  symbol: string
  type: string // buy_limit / sell_limit / buy_stop / sell_stop
  typeLabel: string
  price: number
  sl: number
  tp: number
  volume: number
  magic: number
  comment: string
  placedTs: number
  expiresAt: number | null
  state: "placed" | "partial" | "cancelled"
}

export interface MT5HistorySummary {
  fromTs: number
  toTs: number
  ordersCount: number
  dealsCount: number
  grossProfit: number
  commission: number
  swap: number
  netProfit: number
}

export type MT5McpTool =
  | "mt5.initialize"
  | "mt5.account_info"
  | "mt5.terminal_info"
  | "mt5.symbol_info"
  | "mt5.symbol_select"
  | "mt5.copy_rates"
  | "mt5.copy_ticks"
  | "mt5.symbol_info_tick"
  | "mt5.positions_get"
  | "mt5.orders_get"
  | "mt5.history_orders_get"
  | "mt5.history_deals_get"
  | "mt5.order_send"
  | "mt5.order_check"
  | "paper.pending_order"
  | "paper.modify_sltp"
  | "paper.cancel_order"
  | "paper.emergency_exit"
  | "broker.preflight"
  | "diagnose"
  | "benchmark"

export interface MT5McpCall {
  id: number
  ts: number
  tool: MT5McpTool
  symbol?: string
  argsSummary: string
  resultSummary: string
  latencyMs: number
  ok: boolean
  retcode?: number
  ticket?: number
  traceId?: string
}

export interface MT5State {
  terminal: MT5Terminal
  accountLive: {
    balance: number
    equity: number
    profit: number
    margin: number
    marginFree: number
    marginLevel: number
    leverage: number
    currency: string
  }
  symbols: MT5SymbolSpec[]
  ticks: Record<string, MT5Tick>
  pendingOrders: MT5PendingOrder[]
  history: MT5HistorySummary
  mcpCalls: MT5McpCall[]
  mcpSelected: number
  preflightReport: { symbols: string[]; changed: boolean; ts: number } | null
  lastCopyRates: { symbol: string; timeframe: string; count: number; ts: number } | null
}

export interface QuantMonitor {
  pbo?: number | null
  dsr?: number | null
  bocpd_prob?: number | null
  bocpd_regime?: string | null
  kalman_z?: number | null
  tca_spread_bps?: number | null
  alpha_decay_active?: number | null
  alpha_decay_dead?: number | null
  ewc_regime?: string | null
  wfe?: number | null
  wfe_passed?: boolean | null
  wfe_history?: { wfe: number; passed: boolean }[] | null
  drift_status?: string | null
  drift_fraction?: number | null
  psi_major_count?: number | null
  rolling_sharpe?: number | null
  cvar_95?: number | null
  hurst_exponent?: number | null
  daily_atr_vol?: number | null
  tail_risk_ratio?: number | null
}

export interface TimeframeTrend {
  tf: string
  trend: "BULL" | "BEAR" | "NEUTRAL"
  adx: number
  rsi: number
  bias: string
}

export interface DeckState {
  transport: "connecting" | "live" | "stale" | "mcp"
  /** Real engine boot triage mode, reported by ENGINE_CONFIG / BOOTSTRAP_COMPLETE events ("unknown" until the engine answers). */
  bootMode: "fast" | "full" | "unknown"
  mode: "PAPER" | "LIVE" | "DEMO"
  halted: boolean
  now: number
  /** fast frame counter (~140ms) driving lightweight animations — subscribe only where needed */
  tick: number
  view: ViewId
  promptFocused: boolean
  tabFocused: boolean
  paletteOpen: boolean
  confirm: { action: "pause" | "flatten" | "kill" | "stop"; label: string } | null
  selectedCycle: number
  account: AccountState
  positions: Position[]
  market: Record<string, MarketQuote>
  agents: Record<string, AgentState>
  stream: StreamItem[]
  /** true = auto-follow newest events; false = frozen viewport (paused) */
  streamFollow: boolean
  /** active kind filters; empty array = show all */
  streamKinds: StreamKind[]
  /** active agent filter; "" = show all agents */
  streamAgent: string
  /** live substring filter over title + lines; "" = no search */
  streamQuery: string
  /** events appended while follow was off (shown as a jump-to-live badge) */
  streamUnseen: number
  /** per-event expand override map (event id → expanded).
   *  Events absent from the map follow the default: multi-line (>1 line) events
   *  arrive folded to a single row and expand on demand via the ▸/▾ chip. */
  streamExpanded: Record<number, boolean>
  /** causal screen: fraction of content width given to the CAUSAL DAG panel (0.26–0.62) */
  dagShare: number
  cycles: CycleBrief[]
  narratives: Narrative[]
  nowcasts: Nowcast[]
  journalChecks: JournalCheck[]
  opEvents: OpEvent[]
  alerts: Alert[]
  llmBudgetUsed: number
  llmBudgetCap: number
  schedulesDue: number
  queueHealth: "OK" | "BACKPRESSURE" | "DEGRADED"
  symbols: string[]
  tasks: TaskItem[]
  toolCalls: ToolCall[]
  selectedTask: number
  strategies: StrategyState[]
  selectedStrategy: number
  loopStats: LoopStats
  rMultiples: number[]
  monthlyR: number[]
  approvals: Approval[]
  selectedApproval: number
  autonomyLevel: string
  graphStates: GraphState[]
  selectedGraphNode: number
  skills: Skill[]
  skillExecutions: SkillExecution[]
  selectedSkill: number
  backtests: BacktestResult[]
  selectedBacktest: number
  alphaSignals: AlphaSignal[]
  selectedAlpha: number
  alphaHealth: AlphaHealth[]
  economicEvents: EconomicEvent[]
  mtf: TimeframeTrend[]
  scannerResults: ScannerResult[]
  complianceEvents: ComplianceEvent[]
  chainIntegrity: ChainIntegrity
  moduleGroups: ModuleGroup[]
  tradeHistory: TradeHistoryEntry[]
  errorCodes: ErrorCode[]
  lintIssues: LintIssue[]
  selectedError: number
  workflowExecutions: WorkflowExecution[]
  selectedWorkflow: number
  lifecycleEntries: LifecycleEntry[]
  edgeEntries: EdgeEntry[]
  selectedLifecycle: number
  causalChainPaths: CausalChainPath[]
  enhancedCausalEdges: EnhancedCausalEdge[]
  fusionResults: FusionResult[]
  selectedFusion: number
  eventReactions: EventReaction[]
  researchPipeline: ResearchPipelineEntry[]
  selectedResearch: number
  dataSources: DataSource[]
  selectedDataSource: number
  llmProviders: LLMProvider[]
  winProbResult: WinProbResult | null
  rlTrainer: RLTrainerState
  validationPipeline: ValidationPipelineEntry[]
  selectedValidation: number
  memorySystem: MemorySystemState
  deliberation: DeliberationState
  systemHealth: SystemHealthState
  settings: SettingsState
  autonomyControl: AutonomyControlPlane
  rewardSystem: RewardSystemState
  security: SecurityState
  growth: GrowthState
  modelRegistry: ModelState
  layaDecisions: LayaDecisionsState
  metricsExporter: MetricsState
  drills: DrillsState
  intermarketRegimes: IntermarketRegime[]
  quantMonitor: QuantMonitor
  mt5: MT5State
  logs: {
    entries: LogEntry[]
    filter: LogFilter
    paused: boolean
    totalLines: number
    matchCount: number
    scrollOffset: number
  }
}
