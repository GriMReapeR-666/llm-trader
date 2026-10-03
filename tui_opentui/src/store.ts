import { useSyncExternalStore, useRef, useCallback } from "react"
import { DAG_SHARE_DEFAULT, DAG_SHARE_MAX, DAG_SHARE_MIN, loadDeckPrefs, queuePrefsSave, resetDeckPrefs } from "./prefs"
import type { DeckState, StreamItem } from "./types"

type Listener = () => void

let streamSeq = 1
let eventSeq = 1
let alertSeq = 1

export const initialState: DeckState = {
  transport: "connecting",
  bootMode: "unknown",
  mode: "PAPER",
  halted: false,
  now: Date.now(),
  tick: 0,
  view: "stream",
  promptFocused: true,
  tabFocused: false,
  paletteOpen: false,
  confirm: null,
  selectedCycle: 0,
  account: {
    balance: 0,
    equity: 0,
    dayStartEquity: 0,
    peakEquity: 0,
    drawdownPct: 0,
    heatUsedR: 0,
    heatCapR: 6,
    winRate: 0,
    openRiskPct: 0,
    consecutiveLosses: 0,
    equityCurve: [],
    totalTrades: 0,
    openPositions: 0,
  },
  positions: [],
  market: {},
  agents: {
    TechnicalNet: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    MacroAnalyst: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    MarketModel: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    RiskController: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    QuantitativeCoach: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    EventAgent: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    PortfolioAgent: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    IncidentAgent: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
    ResearchAgent: { status: "idle", task: null, lastBeat: Date.now(), runsToday: 0 },
  },
  stream: [],
  streamFollow: true,
  streamKinds: [],
  streamAgent: "",
  streamQuery: "",
  streamUnseen: 0,
  streamExpanded: {},
  dagShare: 0.42,
  cycles: [],
  narratives: [],
  nowcasts: [],
  journalChecks: [],
  opEvents: [],
  alerts: [],
  llmBudgetUsed: 0,
  llmBudgetCap: 200,
  schedulesDue: 0,
  queueHealth: "OK",
  symbols: ["EURUSD", "XAUUSD", "GBPJPY", "US500"],
  tasks: [],
  toolCalls: [],
  selectedTask: 0,
  strategies: [],
  selectedStrategy: 0,
  loopStats: { cyclesCompleted: 0, avgCycleMs: 0, lastCycleTs: 0 },
  rMultiples: [],
  monthlyR: [],
  approvals: [],
  selectedApproval: 0,
  autonomyLevel: "L1_PAPER",
  graphStates: [],
  selectedGraphNode: 0,
  skills: [],
  skillExecutions: [],
  selectedSkill: 0,
  backtests: [],
  selectedBacktest: 0,
  alphaSignals: [],
  selectedAlpha: 0,
  alphaHealth: [],
  economicEvents: [],
  mtf: [],
  scannerResults: [],
  complianceEvents: [],
  chainIntegrity: { valid: true, entriesVerified: 0, durationMs: 0 },
  moduleGroups: [],
  tradeHistory: [],
  errorCodes: [],
  lintIssues: [],
  selectedError: 0,
  workflowExecutions: [],
  selectedWorkflow: 0,
  lifecycleEntries: [],
  edgeEntries: [],
  selectedLifecycle: 0,
  causalChainPaths: [],
  enhancedCausalEdges: [],
  fusionResults: [],
  selectedFusion: 0,
  eventReactions: [],
  researchPipeline: [],
  selectedResearch: 0,
  dataSources: [],
  selectedDataSource: 0,
  llmProviders: [],
  winProbResult: null,
  rlTrainer: {
    qLearning: { totalUpdates: 0, explorationCount: 0, exploitationCount: 0, explorationRate: 0, qTableSize: 0, avgReward100: 0, totalReward: 0, bestReward: 0, worstReward: 0 },
    ppo: { policyLoss: 0, valueLoss: 0, entropy: 0, clipEps: 0.2, gamma: 0.99, gaeLambda: 0.95, backend: "numpy", actions: [] },
    episodes: [],
    ppoUpdates: [],
    bestStates: [],
    isTraining: false,
    episodesCompleted: 0,
  },
  validationPipeline: [],
  selectedValidation: 0,
  memorySystem: {
    tierMetrics: [],
    totalPromptTokens: 0,
    totalRetrievals: 0,
    zeroResultRate: 0,
    staleAtomicFacts: 0,
    contradictedAtomicFacts: 0,
    scenarioDocuments: 0,
    balancedScenarios: 0,
    candidateReview: { status: "CLEAR", totalCandidates: 0, counts: {}, items: [], nextActions: [] },
    workingContextTokens: 0,
    workingContextBudget: 4000,
  },
  deliberation: {
    graphStates: [],
    selectedGraph: 0,
    audit: { overallStatus: "HEALTHY", trainTestGapPct: 0, regimeFlipFrequency30d: 0, rolling30Expectancy: 0, recommendedArchitecture: "", remediationActions: [], riskMultiplier: 1, timestamp: "" },
  },
  systemHealth: {
    services: [],
    processes: [],
    riskManager: {
      balance: 0, initialBalance: 0, peakEquity: 0, drawdownPct: 0, dailyLossPct: 0,
      weeklyCircuitActive: false, permanentHalt: false, consecutiveLosses: 0, consecutiveWins: 0,
      flattenReason: null, ddMultiplier: 1, streakMultiplier: 1, portfolioHeatR: 0, maxPortfolioHeatR: 4,
      circuitBreakers: [], tradeROfLast50: [],
    },
    guardian: null,
    recovery: null,
    kernelDisposed: false,
    contextName: "RootContext",
    pluginCount: 0,
    incidents: { status: "UNKNOWN", cards: [], checkedAt: null, killSwitch: null, selected: 0 },
  },
  settings: {
    autonomyLevels: [],
    configEntries: [],
    selectedConfig: 0,
  },
  autonomyControl: {
    currentLevel: "L1_PAPER",
    halted: false,
    paused: false,
    haltReason: null,
    pauseReason: null,
    flattenReason: null,
    permanentHalt: false,
    actions: [],
    rolloutGate: { status: "idle", manifestHash: null, targetLevel: null, requestedTs: null },
    researchInfluenceGate: { status: "idle", modelId: null, requestedTs: null },
  },
  rewardSystem: {
    entries: [],
    agentSummaries: [],
    totalRNet: 0,
    avgRNet: 0,
    cvarMetrics: [],
    sustainabilityScore: 0,
    selectedEntry: 0,
  },
  security: {
    scans: [],
    secretsHygiene: [],
    journalScope: "LIVE",
    gitScrubStatus: "clean",
    lastScrubTs: null,
    selectedScan: 0,
  },
  growth: {
    monteCarloSims: [],
    projections: [],
    rollingExpectancy: 0,
    rollingSharpe: 0,
    currentGrowthRate: 0,
    selectedSim: 0,
  },
  modelRegistry: {
    artifacts: [],
    selectedModel: 0,
  },
  layaDecisions: {
    modelStatus: {
      loaded: false,
      modelVersion: "",
      inferenceLatencyMs: 0,
      lastInferenceTs: 0,
    },
    benchmark: {
      accuracy: 0,
      rawAccuracy: 0,
      freezeRecall: 0,
      winRate: 0,
      ece: 0,
    },
    counters: {
      totalDecisions: 0,
      overrides: 0,
      vetoes: 0,
    },
    activeBundle: null,
    recentBundles: [],
  },
  metricsExporter: {
    prometheusMetrics: [],
    heartbeats: [],
    exportStatus: "active",
    lastExportTs: 0,
  },
  drills: {
    drills: [],
    selectedDrill: 0,
  },
  intermarketRegimes: [],
  quantMonitor: {},
  mt5: {
    terminal: {
      state: "disconnected",
      connected: false,
      tradeAllowed: false,
      dllsAllowed: false,
      tradeApiDisabled: false,
      name: "MetaTrader 5",
      company: "STANDBY",
      build: 0,
      path: "",
      dataPath: "",
      login: 0,
      server: "STANDBY",
      leverage: 0,
      currency: "USD",
      lastPingMs: 0,
      lastError: null,
    },
    accountLive: {
      balance: 0,
      equity: 0,
      profit: 0,
      margin: 0,
      marginFree: 0,
      marginLevel: 0,
      leverage: 0,
      currency: "USD",
    },
    symbols: [],
    ticks: {},
    pendingOrders: [],
    history: { fromTs: 0, toTs: 0, ordersCount: 0, dealsCount: 0, grossProfit: 0, commission: 0, swap: 0, netProfit: 0 },
    mcpCalls: [],
    mcpSelected: 0,
    preflightReport: null,
    lastCopyRates: null,
  },
  logs: {
    entries: [],
    filter: { level: "WARNING", query: "", regex: false, caseSensitive: false },
    paused: false,
    totalLines: 0,
    matchCount: 0,
    scrollOffset: 0,
  },
}

class DeckStore {
  // Canonical defaults live in initialState; the running store additionally
  // honors persisted preferences (e.g. the CAUSAL DAG split) so an operator's
  // layout choice survives restarts.
  private state: DeckState = { ...initialState, dagShare: loadDeckPrefs().dagShare }
  private listeners = new Set<Listener>()

  get = (): DeckState => this.state

  set = (patch: Partial<DeckState> | ((s: DeckState) => Partial<DeckState>)): void => {
    const p = typeof patch === "function" ? patch(this.state) : patch
    if (!p) return
    let changed = false
    for (const k in p) {
      if (!Object.is(this.state[k as keyof DeckState], p[k as keyof DeckState])) {
        changed = true
        break
      }
    }
    if (!changed) return
    this.state = { ...this.state, ...p }
    for (const l of this.listeners) l()
  }

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l)
    return () => {
      this.listeners.delete(l)
    }
  }

  pushStream(item: Omit<StreamItem, "id">): void {
    const full: StreamItem = { ...item, id: streamSeq++ }
    this.set((s) => {
      const following = s.streamFollow
      return {
        stream: [...s.stream, full].slice(-400),
        // count events that arrive while the viewport is frozen, so the
        // operator can see how much they missed before jumping back to live
        streamUnseen: following ? 0 : Math.min(999, s.streamUnseen + 1),
      }
    })
  }

  pushOpEvent(e: Omit<import("./types").OpEvent, "id">): void {
    const full = { ...e, id: eventSeq++ }
    this.set((s) => ({ opEvents: [full, ...s.opEvents].slice(0, 200) }))
  }

  pushAlert(severity: "warn" | "critical", text: string): void {
    // Avoid duplicate spam - if same text within 60s, skip (Doherty: prevent flood)
    const now = Date.now()
    const exists = this.state.alerts.find((a) => a.text === text && now - a.ts < 60000)
    if (exists) return
    const a = { id: alertSeq++, ts: now, severity, text }
    this.set((s) => ({ alerts: [a, ...s.alerts].slice(0, 20) }))
    this.pushStream({
      ts: a.ts,
      kind: "alert",
      agent: "SYSTEM",
      title: severity === "critical" ? "CRITICAL ALERT" : "WARNING",
      lines: [text],
      severity,
    })
  }

  clearAlertsByText(substring: string): void {
    // Auto-clear when fixed — Postel recoverable, Peak-End satisfying close
    this.set((s) => ({ alerts: s.alerts.filter((a) => !a.text.includes(substring)) }))
  }

  clearStaleAlerts(maxAgeMs: number = 300000): void {
    const now = Date.now()
    this.set((s) => ({ alerts: s.alerts.filter((a) => now - a.ts < maxAgeMs) }))
  }

  toolCallSeq = 1

  addToolCall(c: Omit<import("./types").ToolCall, "id">): void {
    const full = { ...c, id: this.toolCallSeq++ }
    this.set((s) => ({ toolCalls: [...s.toolCalls, full].slice(-300) }))
  }

  patchTask(id: string, patch: Partial<import("./types").TaskItem>): void {
    this.set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    }))
  }

  dismissAlert(id: number): void {
    this.set((s) => ({ alerts: s.alerts.filter((a) => a.id !== id) }))
  }

  setAgent(id: string, patch: Partial<DeckState["agents"][string]>): void {
    this.set((s) => ({
      agents: { ...s.agents, [id]: { ...s.agents[id], ...patch } },
    }))
  }

  setStreamFollow(follow: boolean): void {
    this.set((s) => ({ streamFollow: follow, streamUnseen: follow ? 0 : s.streamUnseen }))
  }

  toggleStreamKind(kind: import("./types").StreamKind): void {
    this.set((s) => {
      const has = s.streamKinds.includes(kind)
      const next = has ? s.streamKinds.filter((k) => k !== kind) : [...s.streamKinds, kind]
      return { streamKinds: next }
    })
  }

  setStreamKinds(kinds: import("./types").StreamKind[]): void {
    this.set({ streamKinds: kinds })
  }

  setStreamAgent(agent: string): void {
    this.set({ streamAgent: agent })
  }

  setStreamQuery(query: string): void {
    this.set({ streamQuery: query })
  }

  setStreamExpanded(id: number, expanded: boolean): void {
    this.set((s) => ({ streamExpanded: { ...s.streamExpanded, [id]: expanded } }))
  }

  /** Toggle one event: multi-line events default to folded (single row). */
  toggleStreamExpanded(id: number): void {
    const item = this.state.stream.find((i) => i.id === id)
    if (!item) return
    const autoExpanded = item.lines.length <= 1
    const current = this.state.streamExpanded[id] ?? autoExpanded
    this.setStreamExpanded(id, !current)
  }

  /** Force every event in the feed to its expanded state. */
  expandAllStreamItems(): void {
    this.set((s) => {
      const map: Record<number, boolean> = {}
      for (const i of s.stream) map[i.id] = true
      return { streamExpanded: map }
    })
  }

  /** Drop all overrides — long (>1 line) events return to their folded default. */
  foldAllStreamItems(): void {
    this.set({ streamExpanded: {} })
  }

  // Causal screen layout — DAG panel share of the content width
  setDagShare(share: number): void {
    const clamped = Math.min(DAG_SHARE_MAX, Math.max(DAG_SHARE_MIN, share))
    this.set({ dagShare: clamped })
    // Persist the operator's choice (debounced — rapid nudges coalesce).
    queuePrefsSave({ version: 1, dagShare: clamped })
  }

  nudgeDagShare(delta: number): void {
    this.setDagShare((this.state.dagShare ?? 0.42) + delta)
  }

  /** Clear persisted prefs and return the live layout state to defaults. */
  resetPrefs(): void {
    resetDeckPrefs()
    this.set({ dagShare: DAG_SHARE_DEFAULT })
  }

  /** Expand/fold the newest event that matches the current filters. */
  toggleNewestStreamItem(): void {
    const s = this.state
    const q = s.streamQuery.trim().toLowerCase()
    for (let i = s.stream.length - 1; i >= 0; i--) {
      const it = s.stream[i]
      if (s.streamKinds.length > 0 && !s.streamKinds.includes(it.kind)) continue
      if (s.streamAgent && it.agent !== s.streamAgent) continue
      if (q) {
        const hay = (it.title + " " + it.lines.join(" ")).toLowerCase()
        if (!hay.includes(q)) continue
      }
      this.toggleStreamExpanded(it.id)
      return
    }
  }

  clearStream(): void {
    const full: StreamItem = {
      id: streamSeq++,
      ts: Date.now(),
      kind: "system",
      agent: "OPERATOR",
      title: "feed cleared",
      lines: [],
    }
    // ack is set directly (not via pushStream) so a frozen feed does not
    // count it as an unseen event
    this.set({ stream: [full], streamUnseen: 0, streamExpanded: {} })
  }
}

export const store = new DeckStore()

export function shallowEqual<T>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) return false
  const keysA = Object.keys(a) as (keyof T)[]
  const keysB = Object.keys(b) as (keyof T)[]
  if (keysA.length !== keysB.length) return false
  for (const k of keysA) {
    if (!Object.prototype.hasOwnProperty.call(b, k) || !Object.is(a[k], b[k])) return false
  }
  return true
}

export function useDeck<T>(
  selector: (s: DeckState) => T,
  equalityFn: (a: T, b: T) => boolean = Object.is
): T {
  const lastSelectedRef = useRef<T | undefined>(undefined)
  const lastStateRef = useRef<DeckState | undefined>(undefined)
  const selectorRef = useRef(selector)
  selectorRef.current = selector
  const equalityFnRef = useRef(equalityFn)
  equalityFnRef.current = equalityFn

  const getSnapshot = useCallback(() => {
    const currentState = store.get()
    if (lastStateRef.current === currentState && lastSelectedRef.current !== undefined) {
      return lastSelectedRef.current
    }
    const nextSelected = selectorRef.current(currentState)
    if (lastSelectedRef.current !== undefined && equalityFnRef.current(lastSelectedRef.current, nextSelected)) {
      return lastSelectedRef.current
    }
    lastStateRef.current = currentState
    lastSelectedRef.current = nextSelected
    return nextSelected
  }, [])

  return useSyncExternalStore(store.subscribe, getSnapshot)
}
