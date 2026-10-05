import { store } from "../store"
import type { CycleBrief, ValidationPipelineStage, ValidationPipelineEntry, DeliberationNode, DataSource, StreamKind } from "../types"
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const API = process.env.DECK_API ?? "http://127.0.0.1:8000"
const WS_URL = API.replace(/^http/, "ws") + "/ws/harness-events"

let cachedApiToken: string | null = null

export function getApiToken(): string | null {
  if (cachedApiToken) return cachedApiToken

  const envToken = process.env.GUI_API_TOKEN || process.env.GUI_API_KEY
  if (envToken && envToken.trim()) {
    cachedApiToken = envToken.trim()
    return cachedApiToken
  }

  const tokenPath = process.env.GUI_API_TOKEN_FILE
    ? process.env.GUI_API_TOKEN_FILE
    : process.platform === "win32"
      ? join(process.env.APPDATA || join(homedir(), "AppData", "Roaming"), "llm-trader", "api.token")
      : join(homedir(), ".config", "llm-trader", "api.token")

  try {
    const content = readFileSync(tokenPath, "utf8").trim()
    if (content) {
      cachedApiToken = content
      return cachedApiToken
    }
  } catch {
    // Token file missing or unreadable
  }
  return null
}

export async function apiFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  const token = getApiToken()
  const headers = new Headers(init?.headers)
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  return fetch(input, { ...init, headers })
}

let ws: WebSocket | null = null
let backoff = 1000
let stopped = false
let ok = false
let wsOpen = false
let analyticsTimer: ReturnType<typeof setInterval> | null = null
let snapshotTimer: ReturnType<typeof setInterval> | null = null
let livenessTimer: ReturnType<typeof setInterval> | null = null
let researchTimer: ReturnType<typeof setInterval> | null = null
let phase1Timer: ReturnType<typeof setInterval> | null = null
let probing = false
let lastEventTs = 0 // Track last event timestamp for replay on reconnect

// ---------------------------------------------------------------------------
// Small typed helpers for the server payloads (all JSON, loosely shaped)
// ---------------------------------------------------------------------------

function num(v: unknown, d = 0): number {
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : d
}

function parseTs(v: unknown, d = Date.now()): number {
  if (typeof v === "number") return Number.isFinite(v) ? v : d
  if (typeof v === "string") {
    const t = Date.parse(v)
    if (!Number.isNaN(t)) return t
  }
  return d
}

function str(v: unknown, d = ""): string {
  return v === undefined || v === null ? d : String(v)
}

// ---------------------------------------------------------------------------
// Analytics rollup (existing behavior, extended to feed the risk rail)
// ---------------------------------------------------------------------------

async function refreshAnalytics(): Promise<void> {
  try {
    const res = await apiFetch(`${API}/api/analytics`)
    if (!res.ok) return
    const a: any = await res.json()
    const patch: Record<string, unknown> = {}
    if (Array.isArray(a.equity_curve)) {
      patch.account = { ...(patch.account ?? store.get().account), equityCurve: a.equity_curve }
    }
    if (Array.isArray(a.r_multiples)) patch.rMultiples = a.r_multiples
    if (Array.isArray(a.monthly_r)) patch.monthlyR = a.monthly_r
    if (a.totals && typeof a.totals === "object") {
      patch.account = {
        ...(patch.account ?? store.get().account),
        winRate: num(a.totals.win_rate, store.get().account.winRate),
        totalTrades: num(a.totals.trades, store.get().account.totalTrades),
      }
    }
    if (Array.isArray(a.per_strategy) && a.per_strategy.length > 0) {
      patch.strategies = a.per_strategy.map((s: any) => ({
        id: s.id ?? "",
        name: s.name ?? s.id ?? "",
        symbols: [],
        timeframe: "H1",
        stage: s.stage ?? "CANDIDATE",
        gateProgress: 0,
        trades: Number(s.trades ?? 0),
        winRate: Number(s.win_rate ?? 0),
        profitFactor: Number(s.profit_factor ?? 0),
        sharpe: Number(s.sharpe ?? 0),
        maxDD: Number(s.max_dd ?? 0),
        expectancyR: 0,
        recoveryFactor: 0,
        equityCurve: [],
        activeToday: true,
        lastCycleTs: Date.now(),
        notes: "",
      }))
    }
    if (a.quant_monitor && typeof a.quant_monitor === "object") {
      patch.quantMonitor = a.quant_monitor
    }
    // R-multiple history feeds the SYSTEM HEALTH risk rail
    if (Array.isArray(a.r_multiples) && a.r_multiples.length > 0) {
      const last50 = a.r_multiples.slice(-50).map(num)
      patch.systemHealth = {
        ...store.get().systemHealth,
        riskManager: {
          ...store.get().systemHealth.riskManager,
          tradeROfLast50: last50,
        },
      }
    }
    if (Object.keys(patch).length) store.set(patch as never)
  } catch {
    /* offline — stale banner handles visibility */
  }
}

// ---------------------------------------------------------------------------
// Snapshot bootstrap — real engine state → deck store
// ---------------------------------------------------------------------------

async function bootstrap(): Promise<boolean> {
  try {
    const res = await apiFetch(`${API}/api/snapshot?v=2`)
    if (!res.ok) return false
    const snap = await res.json()
    applySnapshot(snap)
    return true
  } catch {
    return false
  }
}

/** Called periodically so safety / autonomy / brain telemetry stays fresh. */
async function refreshSnapshot(): Promise<void> {
  if (stopped) return
  try {
    const res = await apiFetch(`${API}/api/snapshot?v=2`)
    if (!res.ok) return
    applySnapshot(await res.json())
  } catch {
    /* next tick */
  }
}

// ── brain → deck mappings ────────────────────────────────────────────────

function displayLayer(raw: string): string {
  const key = str(raw).toLowerCase()
  if (key.startsWith("l0") || key.startsWith("0")) return "L0 Working Context"
  if (key.startsWith("l1") || key.startsWith("1")) return "L1 Atomic Facts"
  if (key.startsWith("l2") || key.startsWith("2")) return "L2 Verified Memories"
  if (key.startsWith("l3") || key.startsWith("3")) return "L3 Core Policies"
  return str(raw).toUpperCase() || "UNKNOWN"
}

function mapMemory(brain: any, prev: any): any {
  const m = brain?.memory ?? {}
  const q = m.memory_quality ?? {}
  const layers = q.layers ?? {}
  const review = m.review_queue ?? {}
  const tierMetrics = Object.entries(layers).map(([name, v]: any) => ({
    name: displayLayer(name),
    retrievals: num(v?.retrievals),
    meanResults: num(v?.mean_results),
    zeroResultRate: num(v?.zero_result_rate),
    meanLatencyMs: num(v?.mean_latency_ms),
    outcomeLinked: num(v?.outcome_linked),
    outcomeWinRate: v?.outcome_associated_win_rate == null ? null : num(v.outcome_associated_win_rate),
  }))
  const counts = review.counts ?? {}
  return {
    tierMetrics: tierMetrics.length ? tierMetrics : prev.tierMetrics,
    totalRetrievals: num(q.retrieval_count, prev.totalRetrievals),
    totalPromptTokens: num(q.total_prompt_token_estimate, prev.totalPromptTokens),
    zeroResultRate: q.zero_result_rate == null ? prev.zeroResultRate : num(q.zero_result_rate),
    staleAtomicFacts: num(q.stale_active_atomic_facts, prev.staleAtomicFacts),
    contradictedAtomicFacts: num(q.active_contradicted_atomic_facts, prev.contradictedAtomicFacts),
    scenarioDocuments: num(q.scenario_documents, prev.scenarioDocuments),
    balancedScenarios: num(q.balanced_scenarios, prev.balancedScenarios),
    candidateReview: {
      status: str(review.status, "CLEAR"),
      totalCandidates: num(review.total_candidates),
      counts,
      items: (review.items ?? []).map((it: any) => ({
        kind: it.kind ?? "verified",
        id: it.id,
        summary: str(it.summary, "").slice(0, 240),
        status: str(it.status, ""),
        createdAt: it.created_at ?? it.updated_at ?? undefined,
      })),
      nextActions: review.next_actions ?? [],
    },
    workingContextTokens: prev.workingContextTokens,
    workingContextBudget: prev.workingContextBudget,
  }
}

function mapCausalEdges(brain: any): any[] {
  const rows = (brain?.causal_edges?.edges ?? []).filter((e: any) => e?.source_name && e?.target_name)
  return rows.map((e: any) => {
    const sign = str(e.sign).toUpperCase()
    return {
      from: str(e.source_name),
      to: str(e.target_name),
      strength: num(e.confidence),
      lag: "",
      samples: 0,
      direction: sign === "NEGATIVE" ? -1 : 1,
      lifecycle: "MEASURED",
      sourceTier: "RUNTIME_FEED" as const,
      sourceEntity: str(e.source_name),
      relation: str(e.relation),
      confidence: num(e.confidence),
      decayHalfLifeSec: 0,
      ageSec: 0,
    }
  })
}

function mapNowcasts(brain: any): any[] {
  const items = (brain?.macro_observations?.items ?? []).filter((row: any) => {
    const n = typeof row?.value === "number" ? row.value : Number(row?.value)
    return Number.isFinite(n)
  })
  return items.map((row: any) => ({
    name: str(row.entity, "?"),
    value: Number(row.value),
    unit: str(row.series, ""),
    trend: 0 as const,
  }))
}

// ── risk / safety / supervisor → SYSTEM HEALTH + AUTONOMY ────────────────

function mapCircuitBreakers(snap: any): any[] {
  const halted = !!snap.autonomy?.halted
  const paused = !!snap.autonomy?.paused
  const dd = num(snap.account?.drawdown_pct)
  const safety = snap.safety ?? {}
  const exec = safety.execution ?? {}
  const execTripped = exec.kill_switch_active === true || exec.is_trading_enabled === false
  const rows: any[] = []
  rows.push({
    level: 1,
    name: "PORTFOLIO DRAWDOWN",
    active: dd >= 15,
    trigger: "peak-to-now drawdown",
    effect: "new entries blocked, exits allowed",
    threshold: ">=15% red / >=10% warn",
    current: `${dd.toFixed(1)}%`,
  })
  rows.push({
    level: 2,
    name: "AUTONOMY / SUPERVISOR PAUSE",
    active: paused || str(snap.supervisor?.phase) === "PAUSED",
    trigger: str(snap.autonomy?.pause_reason || "operator / supervisor"),
    effect: "engine paused — resume via CONTROL",
    threshold: "operator pause",
    current: paused ? "PAUSED" : "clear",
  })
  rows.push({
    level: 3,
    name: "EXECUTION KILL SWITCH",
    active: halted || execTripped,
    trigger: str(snap.autonomy?.halt_reason || (execTripped ? "safety supervisor" : "")),
    effect: "all orders cancelled, positions flattened",
    threshold: "hardcoded guardian / operator kill",
    current: halted || execTripped ? "TRIPPED" : "clear",
  })
  return rows
}

function mapSystemHealth(snap: any, prev: any): any {
  const supervisor = snap.supervisor ?? {}
  const components = supervisor.components ?? {}
  const services = Object.values(components).map((c: any) => ({
    name: str(c?.name ?? c?.component ?? "component"),
    version: "",
    healthy: c?.ok !== false,
    uptime: 0,
    lastCheck: num(c?.ts, 0) * 1000,
    dependencies: c?.detail ? [str(c.detail)] : [],
  }))
  const rmPrev = prev?.riskManager ?? {}
  const halt = !!snap.autonomy?.halted
  const paused = !!snap.autonomy?.paused
  const dd = num(snap.account?.drawdown_pct, rmPrev.drawdownPct)
  const losses = num(snap.account?.consecutiveLosses, rmPrev.consecutiveLosses)
  const balance = num(snap.account?.balance, rmPrev.balance)
  const equity = num(snap.account?.equity, rmPrev.equity)
  const heat = num(snap.account?.heatUsedR, rmPrev.portfolioHeatR)
  const cap = num(snap.account?.heatCapR, rmPrev.maxPortfolioHeatR || 6)
  const ddMult = dd >= 20 ? 0 : dd >= 15 ? 0.25 : dd >= 10 ? 0.5 : dd >= 5 ? 0.75 : 1
  const streakMult = losses >= 5 ? 0 : losses >= 4 ? 0.125 : losses >= 3 ? 0.25 : losses === 2 ? 0.5 : 1

  return {
    services: services.length ? services : prev?.services ?? [],
    riskManager: {
      ...rmPrev,
      balance,
      initialBalance: rmPrev.initialBalance || balance,
      peakEquity: Math.max(rmPrev.peakEquity, equity),
      drawdownPct: dd,
      dailyLossPct: rmPrev.dailyLossPct,
      weeklyCircuitActive: rmPrev.weeklyCircuitActive,
      permanentHalt: halt,
      consecutiveLosses: losses,
      consecutiveWins: rmPrev.consecutiveWins,
      flattenReason: halt ? str(snap.autonomy?.halt_reason, rmPrev.flattenReason) : paused ? "paused" : null,
      ddMultiplier: ddMult,
      streakMultiplier: streakMult,
      portfolioHeatR: heat,
      maxPortfolioHeatR: cap,
      circuitBreakers: mapCircuitBreakers(snap),
      tradeROfLast50: rmPrev.tradeROfLast50 ?? [],
    },
    kernelDisposed: str(supervisor.phase) === "STOPPED",
    contextName: str(supervisor.phase, prev?.contextName ?? "OFFLINE"),
    pluginCount: services.length ? services.length : prev?.pluginCount ?? 0,
    incidents: mapIncidents(snap, prev),
  }
}

/** P7 incidents pane: durable health alerts + runbook pointers + kill-switch. */
function mapIncidents(snap: any, prev: any): any {
  const src = snap.incidents ?? {}
  const prevInc = prev?.incidents ?? { cards: [], selected: 0 }
  const cards = Array.isArray(src.cards)
    ? src.cards.map((c: any) => ({
        code: str(c?.code, "UNKNOWN"),
        severity: c?.severity === "critical" ? "critical" : c?.severity === "info" ? "info" : "warn",
        count: num(c?.count, 0),
        runbook: str(c?.runbook, "Universal first response"),
        first_response: str(c?.first_response, ""),
      }))
    : prevInc.cards ?? []
  const checkedMs = src.checked_at ? Date.parse(src.checked_at) : NaN
  const ks = src.kill_switch
  return {
    status: ["OK", "WARNING", "CRITICAL", "UNKNOWN"].includes(src.status) ? src.status : "UNKNOWN",
    cards,
    checkedAt: Number.isFinite(checkedMs) ? checkedMs : null,
    killSwitch: ks ? { active: !!ks.active, trading_enabled: !!ks.trading_enabled } : null,
    selected: Math.min(prevInc.selected ?? 0, Math.max(0, cards.length - 1)),
  }
}

function mapAutonomyControl(snap: any, prev: any): any {
  const a = snap.autonomy ?? {}
  const level = str(a.level, prev?.currentLevel ?? "L1_PAPER")
  const valid = ["L0_SHADOW", "L1_PAPER", "L2_MICRO", "L3_FULL"]
  const halted = !!a.halted
  const paused = !!a.paused
  const actions = Array.isArray(a.history)
    ? a.history
        .filter((h: any) => h?.action)
        .map((h: any) => ({
          id: str(h.ts ?? h.action ?? "evt"),
          action: h.action,
          actor: str(h.actor, "engine"),
          ts: parseTs(h.ts),
          reason: str(h.reason, h.why),
          result: h.result === "applied" || h.status === "applied" ? "applied" : "pending",
        }))
        .slice(-12)
    : prev?.actions ?? []
  return {
    ...prev,
    currentLevel: valid.includes(level) ? level : prev?.currentLevel ?? "L1_PAPER",
    halted,
    paused,
    haltReason: halted ? str(a.halt_reason, prev?.haltReason) : null,
    pauseReason: paused ? str(a.pause_reason, prev?.pauseReason) : null,
    flattenReason: halted ? str(a.halt_reason, prev?.flattenReason) : null,
    permanentHalt: halted,
    actions,
  }
}

// ── harness events → CycleBrief (BRAIN screen / cycle replay) ─────────────

function cyclesFromEvents(events: any[]): CycleBrief[] {
  const byId = new Map<string, any>()
  for (const e of events ?? []) {
    const key = str(e?.correlation_id || e?.trace_id)
    if (!key) continue
    const type = str(e?.event_type || e?.type)
    const p = e?.payload ?? {}
    const ts = parseTs(e?.timestamp ?? e?.ts)
    const cur = byId.get(key) ?? {
      key,
      ts,
      symbol: "",
      timeframe: "",
      strategy: "",
      regime: "",
      decision: "HOLD" as CycleBrief["decision"],
      confidence: 0,
      reasoning: "",
    }
    switch (type) {
      case "CYCLE_SPAN_START":
        cur.symbol = str(p.symbol, cur.symbol)
        cur.timeframe = str(p.timeframe, cur.timeframe)
        cur.ts = ts
        break
      case "CYCLE_SPAN_DONE":
        cur.ts = ts
        break
      case "LLM_DECISION": {
        const side = str(p.side ?? p.action).toUpperCase()
        cur.decision = side === "BUY" || side === "SELL" ? "EXECUTE" : "HOLD"
        cur.symbol = str(p.symbol, cur.symbol)
        cur.confidence = num(p.confidence, cur.confidence)
        const src = p.mainDriver?.source ? `${str(p.mainDriver.source)}→${str(p.mainDriver.target ?? p.symbol)}` : ""
        cur.reasoning = str(p.summary ?? p.reasoning ?? "")
        if (src) cur.reasoning = cur.reasoning ? `${cur.reasoning}\nmain driver: ${src}` : `main driver: ${src}`
        cur.ts = ts
        break
      }
      case "VALIDATION_RESULT":
        if (p.passed === false) cur.decision = "REJECT"
        cur.ts = ts
        break
      default:
        break
    }
    byId.set(key, cur)
  }
  return [...byId.values()]
    .sort((a, b) => b.ts - a.ts)
    .map((c) => ({
      id: c.key,
      ts: c.ts,
      symbol: c.symbol,
      timeframe: c.timeframe,
      strategy: c.strategy,
      regime: c.regime,
      decision: c.decision,
      confidence: c.confidence,
      reasoning: str(c.reasoning).slice(0, 2000),
    }))
}

function applySnapshot(snap: any): void {
  const s = store.get()
  const patch: Record<string, unknown> = {}
  if (snap.account) {
    const prevAcct = s.account
    const balance = typeof snap.account.balance === "number" && snap.account.balance > 0 ? snap.account.balance : prevAcct.balance
    const equity = typeof snap.account.equity === "number" && snap.account.equity > 0 ? snap.account.equity : (balance > 0 ? balance : prevAcct.equity)
    patch.account = { ...prevAcct, ...snap.account, balance, equity }
  }
  if (Array.isArray(snap.positions)) patch.positions = snap.positions
  if (snap.agents) patch.agents = { ...s.agents, ...snap.agents }
  if (snap.market) patch.market = snap.market
  if (Array.isArray(snap.cycles)) patch.cycles = snap.cycles
  if (Array.isArray(snap.narratives)) patch.narratives = snap.narratives
  if (Array.isArray(snap.nowcasts)) patch.nowcasts = snap.nowcasts
  if (Array.isArray(snap.journal_checks)) patch.journalChecks = snap.journal_checks
  if (typeof snap.mode === "string") patch.mode = snap.mode
  if (snap.autonomy?.level) patch.autonomyLevel = snap.autonomy.level
  // halt state surfaces across the shell (banner, control screens)
  if (typeof snap.autonomy?.halted === "boolean") patch.halted = snap.autonomy.halted
  // Live journal reader: PnL summary → account fields. Merge into the
  // pending account patch so balance/equity from snap.account survive.
  if (snap.pnl && typeof snap.pnl === "object") {
    const pnl = snap.pnl
    const pendingAcct: any = patch.account ?? store.get().account
    const acc = { ...pendingAcct }
    if (typeof pnl.win_rate === "number") acc.winRate = pnl.win_rate
    if (typeof pnl.total_trades === "number") acc.totalTrades = pnl.total_trades
    if (typeof pnl.open_positions === "number") acc.openPositions = pnl.open_positions
    patch.account = acc
  }
  // Live journal reader: closed trade history → tradeHistory
  if (Array.isArray(snap.history)) {
    patch.tradeHistory = snap.history.map((h: any) => ({
      ticket: Number(h.ticket ?? 0),
      strategy: String(h.strategy ?? ""),
      symbol: String(h.symbol ?? ""),
      side: String(h.side ?? ""),
      volume: Number(h.volume ?? 0),
      entry: Number(h.entry ?? 0),
      stopLoss: Number(h.stop_loss ?? 0),
      takeProfit: Number(h.take_profit ?? 0),
      riskAmount: Number(h.risk_amount ?? 0),
      openedAt: String(h.opened_at ?? ""),
      closedAt: String(h.closed_at ?? ""),
      exitPrice: Number(h.exit_price ?? 0),
      pnl: Number(h.pnl ?? 0),
      exitReason: String(h.exit_reason ?? ""),
    }))
  }
  if (Array.isArray(snap.approvals)) {
    patch.approvals = snap.approvals.map((a: any, i: number) => ({
      id: a.id ?? `ap-${i}`,
      kind: a.kind ?? "CONFIG_CHANGE",
      title: a.title ?? "",
      detailLines: a.detailLines ?? [],
      signatureRequired: !!a.signatureRequired,
      requestedTs: a.requestedTs ?? Date.now(),
      status: a.status === "APPROVED" ? "APPROVED" : a.status === "REJECTED" ? "REJECTED" : "PENDING",
      decidedBy: a.decidedBy,
    }))
    const firstPending = (patch.approvals as { status: string }[]).findIndex((a) => a.status === "PENDING")
    patch.selectedApproval = Math.max(0, firstPending)
  }
  if (Array.isArray(snap.strategy_registry)) {
    patch.strategies = snap.strategy_registry.map((st: any) => ({
      id: st.id,
      name: st.name ?? st.id,
      symbols: st.symbols ?? [],
      timeframe: st.timeframe ?? "H1",
      stage: st.stage ?? "CANDIDATE",
      gateProgress: Number(st.gate_progress ?? 0),
      trades: Number(st.trades ?? 0),
      winRate: Number(st.win_rate ?? 0),
      profitFactor: Number(st.profit_factor ?? 0),
      sharpe: Number(st.sharpe ?? 0),
      maxDD: Number(st.max_dd ?? 0),
      expectancyR: Number(st.expectancy_r ?? 0),
      recoveryFactor: Number(st.recovery_factor ?? 0),
      equityCurve: [],
      activeToday: true,
      lastCycleTs: Date.now(),
      notes: "",
    }))
  }

  // ── NEW: supervisor components + risk/safety → SYSTEM HEALTH ──────────
  if (snap.supervisor || snap.safety || snap.autonomy || snap.account) {
    patch.systemHealth = mapSystemHealth(snap, s.systemHealth)
  }
  if (snap.autonomy) {
    patch.autonomyControl = mapAutonomyControl(snap, s.autonomyControl)
  }

  // ── NEW: brain observability → memory / macro / causal screens ─────────
  const brain = snap.brain
  if (brain) {
    if (brain.memory) patch.memorySystem = mapMemory(brain, s.memorySystem)
    const edges = mapCausalEdges(brain)
    if (edges.length) patch.enhancedCausalEdges = edges
    const nowcasts = mapNowcasts(brain)
    if (nowcasts.length) patch.nowcasts = nowcasts
  }

  // ── NEW: recent harness events → cycle briefs (BRAIN screen) ───────────
  // Engine-reported boot triage mode ("" = engine hasn't declared — keep "unknown", never guess)
  if (typeof snap.boot_triage_mode === "string" && snap.boot_triage_mode) {
    const bm = snap.boot_triage_mode.toLowerCase()
    patch.bootMode = bm === "off" ? "fast" : "full"
  }
  if (Array.isArray(snap.harness_events)) {
    const cycles = cyclesFromEvents(snap.harness_events)
    if (cycles.length) patch.cycles = cycles
  }

  // ── Graph states from deliberation data (GRAPH screen left panel) ──────
  const dgStates = snap.deliberation?.graphStates
  if (Array.isArray(dgStates) && dgStates.length) {
    patch.graphStates = dgStates.map((dg: any) => ({
      id: str(dg.cycleId || dg.id),
      name: `${str(dg.symbol)} ${str(dg.timeframe)} ${str(dg.strategy)}`.trim(),
      nodes: (dg.nodes || []).map((n: any, i: number) => ({
        id: str(n.id || `n${i}`),
        label: str(n.label || n.name || `Node ${i}`),
        type: "agent" as const,
        status: (n.status === "done" ? "done" : n.status === "running" ? "running" : "queued") as any,
        agent: str(n.name || n.agent || ""),
        durationMs: typeof n.durationMs === "number" ? n.durationMs : undefined,
        x: i * 120,
        y: 0,
      })),
      edges: (dg.nodes || []).slice(0, -1).map((n: any, i: number) => ({
        from: str(n.id || `n${i}`),
        to: str(dg.nodes[i + 1]?.id || `n${i + 1}`),
        label: "",
        active: n.status === "running",
      })),
      currentNode: str((dg.nodes || []).find((n: any) => n.status === "running")?.id || ""),
      startedTs: parseTs(dg.startedTs),
      status: (dg.status === "complete" || dg.status === "EXECUTED" ? "complete"
        : dg.status === "error" || dg.status === "REJECTED" ? "error" : "running") as any,
      cycleId: str(dg.cycleId || dg.id),
    }))
  }

  // ── macro-cognitive state (regime, liquidity, tripwires, decision bundles) ──
  if (snap.macro_state) {
    const ms = snap.macro_state
    patch.macroState = {
      regime: str(ms.regime, "NORMAL_RISK_OFF"),
      netLiquidity: str(ms.net_liquidity, "NEUTRAL_LIQUIDITY"),
      summary: str(ms.summary, ""),
      tripwires: Array.isArray(ms.tripwires) ? ms.tripwires : [],
      lastBundle: ms.last_bundle && typeof ms.last_bundle === "object" ? ms.last_bundle : null,
      causalAttribution: ms.causal_attribution && typeof ms.causal_attribution === "object" ? ms.causal_attribution : null,
    }
  }

  // ── laya decisions state (model status, benchmark, active bundle) ──
  if (snap.macro_state?.last_bundle || snap.laya) {
    const layaSrc = snap.laya ?? {}
    const bundle = layaSrc.last_bundle || snap.macro_state?.last_bundle
    const prevLaya = s.layaDecisions
    const benchmark = layaSrc.benchmark ? {
      accuracy: num(layaSrc.benchmark.validated_accuracy, 0) * 100,
      rawAccuracy: num(layaSrc.benchmark.raw_accuracy, 0) * 100,
      freezeRecall: num(layaSrc.benchmark.freeze_recall, 0) * 100,
      winRate: num(layaSrc.benchmark.win_rate, 0) * 100,
      ece: num(layaSrc.benchmark.ece, 0),
    } : prevLaya.benchmark

    const counters = layaSrc.counters ? {
      totalDecisions: num(layaSrc.counters.total_decisions, prevLaya.counters.totalDecisions),
      overrides: num(layaSrc.counters.overrides, prevLaya.counters.overrides),
      vetoes: num(layaSrc.counters.vetoes, prevLaya.counters.vetoes),
    } : prevLaya.counters

    const modelStatus = layaSrc.model_status ? {
      loaded: Boolean(layaSrc.model_status.loaded),
      modelVersion: str(layaSrc.model_status.model_version, prevLaya.modelStatus.modelVersion),
      inferenceLatencyMs: num(layaSrc.model_status.inference_latency_ms, prevLaya.modelStatus.inferenceLatencyMs),
      lastInferenceTs: num(layaSrc.model_status.last_inference_ts, prevLaya.modelStatus.lastInferenceTs),
    } : prevLaya.modelStatus

    let activeBundle = prevLaya.activeBundle
    if (bundle && typeof bundle === "object") {
      const decisions = bundle.decisions || {}
      activeBundle = {
        decisions: Object.keys(decisions).map((key) => ({
          slot: key,
          slotLabel: key.replace(/_/g, " ").toUpperCase(),
          selectedOption: str(decisions[key]?.selected_option, "INSUFFICIENT_DATA"),
          confidence: typeof decisions[key]?.score === "number" ? decisions[key].score : 0.5,
          isOverridden: !!decisions[key]?.is_overridden,
          isVetoed: !!decisions[key]?.is_veto,
          reasoning: str(decisions[key]?.override_reason || ""),
        })),
        counters,
        modelStatus,
        benchmark,
        timestamp: parseTs(bundle.timestamp),
      }
    }

    patch.layaDecisions = {
      modelStatus,
      benchmark,
      counters,
      activeBundle,
      recentBundles: prevLaya.recentBundles ?? [],
    }
  }

  store.set(patch as never)
}

// ---------------------------------------------------------------------------
// WebSocket event envelope → stream / tasks / alerts
// ---------------------------------------------------------------------------

function upsertLiveTask(trace: string, stage: string, agent: string,
                        status: "queued" | "running" | "done" | "error" | "skipped",
                        durationMs?: number): void {
  const id = `live:${trace}:${stage}`
  const s = store.get()
  const existing = s.tasks.find((t) => t.id === id)
  if (existing) {
    store.set((st) => ({
      tasks: st.tasks.map((t) =>
        t.id === id ? { ...t, status, durationMs: durationMs ?? t.durationMs } : t),
    }))
  } else {
    store.set((st) => ({
      tasks: [...st.tasks.slice(-40), {
        id, stage, label: `${stage} · live`, status, agent,
        durationMs,
      }],
      selectedTask: st.tasks.length === 0 ? 0 : s.selectedTask,
    }))
  }
}

/** Keep BRAIN screen cycle briefs fresh as events stream in. */
function upsertValidationPipeline(payload: any): void {
  const id = str(payload?.correlation_id ?? (payload?.trace_id ?? 'live'))
  if (!id) return
  store.set((s) => {
    const next = [...s.validationPipeline]
    const idx = next.findIndex((vp) => vp.id === id)
    const stage: ValidationPipelineStage = {
      name: str(payload?.stage ?? '?'),
      status: str(payload?.status ?? 'queued') as any,
      startedTs: payload?.started_ts ? Number(payload.started_ts) : undefined,
      completedTs: payload?.completed_ts ? Number(payload.completed_ts) : undefined,
      metrics: payload?.payload && typeof payload.payload === 'object' ? payload.payload : undefined,
      notes: typeof payload?.notes === 'string' ? payload.notes : undefined,
    }
    const existing = idx >= 0 ? next[idx] : undefined
    if (existing) {
      const updated: ValidationPipelineEntry = {
        ...existing,
        stages: [...existing.stages.slice(-24), stage],
        overallStatus: existing.overallStatus,
      }
      if (idx >= 0) next.splice(idx, 1)
      next.unshift(updated)
    } else {
      next.unshift({
        id,
        strategyName: str(payload?.strategy_name ?? (payload?.strategy ?? 'live')),
        symbol: str(payload?.symbol ?? ''),
        startedTs: Date.now(),
        stages: [stage],
        overallStatus: 'running',
        deflatedSharpe: 0,
        pbo: 0,
        cpcvSplits: [],
      })
    }
    return { validationPipeline: next.slice(0, 60) }
  })
}

function upsertDeliberationGraph(payload: any): void {
  const id = str(payload?.correlation_id ?? (payload?.trace_id ?? 'live'))
  if (!id) return
  store.set((s) => {
    const next = [...s.deliberation.graphStates]
    const idx = next.findIndex((g) => g.cycleId === id)
    const node: DeliberationNode = {
      id: str(payload?.node_id ?? payload?.node_label ?? 'n'),
      name: str(payload?.node_id ?? payload?.node_label ?? 'node'),
      label: str(payload?.node_label ?? (payload?.node_id ?? 'node')),
      status: str(payload?.status ?? 'pending') as any,
      output: typeof payload?.output === 'string' ? payload.output.slice(0, 280) : undefined,
      durationMs: payload?.duration_ms ? Number(payload.duration_ms) : undefined,
    }
    const existing = idx >= 0 ? next[idx] : undefined
    if (existing) {
      if (idx >= 0) next.splice(idx, 1)
      next.unshift({ ...existing, nodes: [...existing.nodes.slice(-24), node], startedTs: existing.startedTs || Date.now() })
    } else {
      next.unshift({
        cycleId: id,
        symbol: str(payload?.symbol ?? ''),
        timeframe: str(payload?.timeframe ?? ''),
        strategy: str(payload?.strategy ?? 'deliberative'),
        status: 'running',
        nodes: [node],
        startedTs: Date.now(),
      })
    }
    return { deliberation: { ...s.deliberation, graphStates: next.slice(0, 40) } }
  })
}

function upsertDataSource(payload: any): void {
  const id = str(payload?.source_id ?? 'live')
  if (!id) return
  store.set((s) => {
    const ns = [...s.dataSources]
    const idx = ns.findIndex((ds) => ds.id === id)
    const entry: DataSource = {
      id,
      name: id.charAt(0).toUpperCase() + id.slice(1),
      tier: 'REALTIME',
      category: 'MARKET_DATA',
      health: str(payload?.health ?? 'healthy') as any,
      lastFetchTs: payload?.last_fetch_ts ? Number(payload.last_fetch_ts) : Date.now(),
      fetchCount: payload?.fetch_count ? Number(payload.fetch_count) : 1,
      errorCount: payload?.error_count ? Number(payload.error_count) : 0,
      avgLatencyMs: payload?.latency_ms ? Number(payload.latency_ms) : 0,
      description: '',
    }
    if (idx >= 0) ns[idx] = { ...ns[idx], ...entry, fetchCount: ns[idx].fetchCount + 1 }
    else ns.unshift(entry)
    return { dataSources: ns.slice(0, 60) }
  })
}

function upsertCycle(key: string, patch: Partial<CycleBrief>): void {
  if (!key) return
  store.set((s) => {
    const idx = s.cycles.findIndex((c) => c.id === key)
    const existing = idx >= 0 ? s.cycles[idx] : undefined
    const base: CycleBrief = {
      id: key,
      ts: Date.now(),
      symbol: "",
      timeframe: "",
      strategy: "",
      regime: "",
      decision: "HOLD",
      confidence: 0,
      reasoning: "",
    }
    const merged: CycleBrief = { ...base, ...(existing ?? {}), ...patch, id: key }
    const next = [...s.cycles]
    if (idx >= 0) next.splice(idx, 1)
    next.unshift(merged)
    return { cycles: next.slice(0, 60) }
  })
}

function handleStageEvent(type: string, payload: any): void {
  const trace = String(payload?.trace_id ?? payload?.correlation_id ?? "live")
  const stage = String(payload?.stage ?? "?")
  const actor = String(payload?.actor ?? "SYSTEM")
  const ms = Number(payload?.ms ?? 0) || undefined
  switch (type) {
    case "STAGE_STARTED":
      upsertLiveTask(trace, stage, actor, "running")
      break
    case "STAGE_DONE":
      upsertLiveTask(trace, stage, actor, "done", ms)
      break
    case "STAGE_FAILED":
      upsertLiveTask(trace, stage, actor, "error", ms)
      break
    case "STAGE_ABORTED":
      upsertLiveTask(trace, stage, actor, "skipped", ms)
      break
    case "STAGE_SLA_MISS":
      store.pushAlert("warn", `SLA miss: ${stage} took ${payload?.ms}ms (cap ${payload?.sla_ms}ms)`)
      break
  }
}

function handleEnvelope(msg: any): void {
  // Envelope key normalization: the engine bridge serializes HarnessEvent via
  // to_dict() → { event_type, timestamp, correlation_id, trace_id, payload }.
  const type = String(msg?.type ?? msg?.event_type ?? "")
  const payload = msg?.payload ?? {}
  const ts = parseTs(msg?.ts ?? msg?.timestamp)
  const correlation = String(msg?.correlation_id ?? msg?.trace_id ?? "")
  switch (type) {
    case "HEARTBEAT":
      store.set({ now: ts })
      break
    case "ENGINE_CONFIG": {
      const tmode = String(payload?.news_triage_mode ?? "auto").toLowerCase()
      store.set({ bootMode: tmode === "off" ? "fast" : tmode === "laya" || tmode === "auto" ? "full" : "unknown" })
      store.pushStream({
        ts, kind: "system", agent: "SYSTEM",
        title: tmode === "off" ? "FAST BOOT · news triage off" : `FULL BOOT · news triage '${tmode}'`,
        lines: [tmode === "off"
          ? "Deterministic rule-based triage active — Laya inference skipped this launch"
          : "Laya news triage will run at engine start (normal launch path)"],
      })
      if (tmode === "off") store.pushAlert("warn", "FAST BOOT: news triage off — deterministic fallback active (Laya inference skipped)")
      break
    }
    case "BOOTSTRAP_COMPLETE": {
      const bsTriage = payload?.news_triage ?? null
      const bmode = bsTriage && typeof bsTriage === "object" ? String((bsTriage as any).mode ?? "") : ""
      if (bmode) store.set({ bootMode: bmode === "off" ? "fast" : "full" })
      const bsLines: string[] = [`ready=${payload?.ready ? "yes" : "NO"} · quality=${payload?.quality_score ?? "?"} · triage mode: ${bmode || "n/a"}`]
      const bsIngest = payload?.news_ingest
      if (bsIngest && typeof bsIngest === "object") bsLines.push(`news ingest: ${String((bsIngest as any).status ?? "?")} · ${String((bsIngest as any).articles ?? 0)} articles`)
      store.pushStream({
        ts, kind: "system", agent: "SYSTEM",
        title: `BOOTSTRAP ${payload?.ready ? "COMPLETE" : "BLOCKED"} · ${payload?.phase ?? "?"} · ${payload?.duration_seconds ?? "?"}s`,
        lines: bsLines,
      })
      if (!payload?.ready) store.pushAlert("critical", `Bootstrap BLOCKED at ${payload?.phase ?? "?"} — trading will not start`)
      break
    }
    case "CYCLE_SPAN_START":
      store.set({ tasks: [], selectedTask: 0 })
      upsertLiveTask(correlation || "cycle.span", "cycle.span", payload?.actor ?? "SYSTEM", "running")
      upsertCycle(correlation, {
        ts,
        symbol: String(payload?.symbol ?? ""),
        timeframe: String(payload?.timeframe ?? ""),
      })
      break
    case "CYCLE_SPAN_DONE":
      upsertLiveTask(correlation || "cycle.span", "cycle.span", payload?.actor ?? "SYSTEM", "done", payload?.ms)
      upsertCycle(correlation, { ts })
      break
    case "STAGE_STARTED":
    case "STAGE_DONE":
    case "STAGE_FAILED":
    case "STAGE_ABORTED":
      handleStageEvent(type, { ...payload, correlation_id: msg?.correlation_id, trace_id: msg?.trace_id })
      break
    case "STAGE_SLA_MISS":
      handleStageEvent(type, payload)
      break
    case "EXECUTOR_FAILED":
      store.pushAlert("warn", `executor ${payload?.schedule} failed (${payload?.principal})`)
      break
    case "CONTROL_EXECUTED":
      store.pushStream({
        ts, kind: "system", agent: "OPERATOR",
        title: `control executed: ${payload?.action}`,
        lines: [`actor ${payload?.actor} · source ${payload?.source ?? "engine"}`],
      })
      // Autonomy state changed → re-pull so every control screen reflects it.
      void refreshSnapshot()
      break
    case "APPROVAL_DECISION":
      store.set((s) => ({
        approvals: s.approvals.map((a) =>
          a.id === payload?.approval_id
            ? { ...a, status: payload?.decision === "REJECTED" ? "REJECTED" : "APPROVED",
                decidedBy: payload?.actor, decidedTs: ts }
            : a),
      }))
      break
    case "AGENT_STATUS":
      if (payload.agent) store.setAgent(payload.agent, { status: payload.status, task: payload.task, lastBeat: ts })
      break
    case "LLM_DECISION": {
      const side = String(payload?.side ?? payload?.action ?? "HOLD").toUpperCase()
      store.pushStream({
        ts,
        kind: "decision",
        agent: "MarketModel",
        title: `[ ${side} ${payload?.symbol ?? ""} ]`,
        lines: [String(payload?.summary ?? "")],
      })
      upsertCycle(correlation, {
        ts,
        symbol: String(payload?.symbol ?? ""),
        decision: side === "BUY" || side === "SELL" ? "EXECUTE" : "HOLD",
        confidence: num(payload?.confidence),
        reasoning: str(payload?.summary).slice(0, 2000),
      })
      break
    }
    case "VALIDATION_RESULT":
      store.pushStream({
        ts,
        kind: "validation",
        agent: "RiskController",
        title: payload.passed ? "DeterministicValidator PASS" : "DeterministicValidator REJECT",
        lines: Array.isArray(payload.reasons) ? payload.reasons : [],
        severity: payload.passed ? undefined : "warn",
      })
      if (correlation && payload.passed === false) upsertCycle(correlation, { ts, decision: "REJECT" })
      break
    case "VALIDATION_STEP":
      upsertValidationPipeline(payload)
      store.pushStream({
        ts,
        kind: "tool",
        agent: "RiskController",
        title: `[ VALIDATION ${payload.stage} ]`,
        lines: [
          `${payload.status} · ${payload.duration_ms ? payload.duration_ms + 'ms' : 'dry'}`,
          ...(Array.isArray(payload.payload) ? payload.payload : []),
          ...(typeof payload.notes === 'string' ? [payload.notes] : []),
        ],
        severity: payload.status === 'failed' ? 'warn' : undefined,
      })
      break
    case "DELIBERATION_STEP":
      upsertDeliberationGraph(payload)
      store.pushStream({
        ts,
        kind: "thinking",
        agent: "DeliberativeGraph",
        title: `[ DELIB ${payload.node_label || payload.node_id} ]`,
        lines: [
          `${payload.status}${payload.duration_ms ? ' · ' + payload.duration_ms + 'ms' : ''}`,
          ...(typeof payload.output === 'string' && payload.output ? [payload.output] : []),
        ],
        severity: payload.status === 'error' ? 'warn' : undefined,
      })
      break
    case "DATA_SOURCE_REFRESH":
      upsertDataSource(payload)
      store.pushStream({
        ts,
        kind: "system",
        agent: "DataHub",
        title: `[ DATA ${payload.source_id} ]`,
        lines: [
          `${payload.health}${payload.fetch_count ? ' · ' + payload.fetch_count + ' fetches' : ''}`,
        ],
        severity: payload.health === 'down' || payload.health === 'circuit_open' ? 'warn' : undefined,
      })
      break
    case "ORDER_ROUTED":
    case "ORDER_SUBMIT":
    case "FILL":
      store.pushStream({
        ts,
        kind: type === "FILL" ? "fill" : "order",
        agent: "RiskController",
        title: `${type === "FILL" ? "FILLED" : "ORDER ROUTED"} #${payload.ticket ?? "?"}`,
        lines: [String(payload.detail ?? payload.comment ?? "")],
      })
      break
    case "ALERT":
      if (payload.severity === "critical" || payload.severity === "warn") {
        store.pushAlert(payload.severity, String(payload.text ?? ""))
      }
      break
    case "AGENT_EVENT": {
      const ag = str(payload?.agent, "AGENT")
      const act = str(payload?.action, "ACTION")
      const stg = str(payload?.stage, "")
      const cyc = payload?.cycle_num ? `#${payload.cycle_num} ` : ""
      const sym = payload?.symbol ? `${payload.symbol} ` : ""
      const pData = payload?.data ?? payload?.payload ?? {}
      const flatLines: string[] = []
      if (typeof pData === "object" && pData !== null) {
        for (const [k, v] of Object.entries(pData)) {
          if (v === null || v === undefined) continue
          if (typeof v === "object") {
            const s = JSON.stringify(v).slice(0, 120)
            flatLines.push(`${k}: ${s}${JSON.stringify(v).length > 120 ? "…" : ""}`)
          } else {
            flatLines.push(`${k}: ${String(v).slice(0, 120)}`)
          }
          if (flatLines.length >= 6) break
        }
      }
      const actLower = act.toLowerCase()
      let kind: StreamKind = "tool"
      if (actLower.includes("risk") || actLower.includes("validation") || actLower.includes("gate")) kind = "validation"
      else if (actLower.includes("order") || actLower.includes("fill") || actLower.includes("trade")) kind = "order"
      else if (actLower.includes("decision") || actLower.includes("proposal")) kind = "decision"
      else if (actLower.includes("research") || actLower.includes("study") || actLower.includes("analysis")) kind = "thinking"
      store.pushStream({
        ts,
        kind,
        agent: ag,
        title: `[ ${ag} ${act} ] ${cyc}${sym}${stg}`,
        lines: flatLines,
      })
      break
    }
    default:
      store.pushOpEvent({
        ts,
        actor: String(payload.actor ?? "SYSTEM"),
        type,
        severity: payload.severity ?? "info",
        payload: JSON.stringify(payload).slice(0, 200),
      })
  }
}

// ---------------------------------------------------------------------------
// Liveness: the engine does not emit AGENT_STATUS/HEARTBEAT through the
// bridge, so staleness is decided by an HTTP probe instead of agent beats.
// ---------------------------------------------------------------------------

async function probeApi(): Promise<boolean> {
  if (probing) return store.get().transport !== "stale"
  probing = true
  try {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 3000)
    try {
      const res = await apiFetch(`${API}/api/status`, { signal: ctl.signal })
      return res.ok
    } finally {
      clearTimeout(timer)
    }
  } catch {
    return false
  } finally {
    probing = false
  }
}

// ---------------------------------------------------------------------------
// Neutral MT5 state when only the REST engine is attached (no MCP bridge).
// The store ships demo terminal values that must never masquerade as live.
// ---------------------------------------------------------------------------

function neutralizeMt5ForRestLive(): void {
  const s = store.get()
  store.set({
    mt5: {
      ...s.mt5,
      terminal: {
        ...s.mt5.terminal,
        state: "disconnected",
        connected: false,
        tradeAllowed: false,
        lastError: null,
      },
    },
  })
}

// ---------------------------------------------------------------------------

export async function startLive(): Promise<() => void> {
  stopped = false
  ok = false
  wsOpen = false
  let bootOk = false
  try {
    bootOk = await bootstrap()
  } catch (err: any) {
    store.pushAlert("warn", `Live bootstrap failed: ${String(err?.message ?? err).slice(0,100)} — will retry with fallback`)
    bootOk = false
  }
  if (!bootOk) {
    store.set({ transport: "connecting" })
    // Professional error with fix — not silent
    store.pushOpEvent({ ts: Date.now(), actor: "SYSTEM", type: "BOOT", severity: "warn", payload: `live not on ${API} — deck will fail closed. Fix: python ui/gui_api.py` })
  } else {
    ok = true
    store.set({ transport: "live" })
    store.clearAlertsByText("Live engine not on")
    store.clearAlertsByText("synthetic")
    neutralizeMt5ForRestLive()
    store.pushStream({
      ts: Date.now(),
      kind: "system",
      agent: "SYSTEM",
      title: "attached to live engine",
      lines: [`snapshot loaded from ${API}`, "errors cleared — live data active", "MT5 bridge: run TRANSPORT=mcp or MT5_MCP_AUTOSTART=1 for direct terminal quotes"],
    })
  }

  const connect = () => {
    if (stopped) return
    try {
      const token = getApiToken()
      const wsOpts = token ? ({ headers: { Authorization: `Bearer ${token}` } } as any) : undefined
      ws = new WebSocket(WS_URL, wsOpts)
      ws.onopen = async () => {
        backoff = 1000
        wsOpen = true
        // Recoverable: engine came online after an earlier failed bootstrap.
        if (!ok) {
          const nowOk = await bootstrap()
          if (nowOk) {
            ok = true
            neutralizeMt5ForRestLive()
            store.clearAlertsByText("Live engine not on")
          }
        }
        if (ok) store.set({ transport: "live" })
        store.clearAlertsByText("MCP offline")
        store.clearAlertsByText("Live engine not on")
        store.clearAlertsByText("synthetic engine engaged")
        store.clearAlertsByText("heartbeat stale")
        store.clearStaleAlerts()
        store.pushOpEvent({ ts: Date.now(), actor: "SYSTEM", type: "TRANSPORT_RECOVERED", severity: "info", payload: "live link restored, errors cleared" })
        // Replay missed events from the disconnect window
        if (lastEventTs > 0) {
          try {
            const replayRes = await apiFetch(`${API}/api/harness-events/recent?since=${lastEventTs}`)
            if (replayRes.ok) {
              const replayData = (await replayRes.json()) as { events?: any[] }
              if (Array.isArray(replayData.events)) {
                for (const evt of replayData.events) {
                  handleEnvelope(evt)
                }
                if (replayData.events.length > 0) {
                  store.pushStream({
                    ts: Date.now(), kind: "system", agent: "SYSTEM",
                    title: "replayed missed events", lines: [`Replayed ${replayData.events.length} events from disconnect window`],
                  })
                }
              }
            }
          } catch { /* replay is best-effort */ }
        }
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          // Track last event timestamp for replay on reconnect
          const msgTs = typeof msg?.timestamp === "number" ? msg.timestamp : Date.now()
          if (msgTs > lastEventTs) lastEventTs = msgTs
          // Any engine traffic proves the link is alive.
          if (store.get().transport === "stale") store.set({ transport: "live" })
          store.clearAlertsByText("heartbeat stale")
          handleEnvelope(msg)
        } catch { /* ignore malformed frame */ }
      }
      ws.onclose = () => {
        wsOpen = false
        if (!stopped) setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, 30000)
        store.set((s) => (s.transport === "live" ? { transport: "stale" } : {}))
      }
      ws.onerror = () => {
        try {
          ws?.close()
        } catch { }
      }
    } catch {
      if (!stopped) setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, 30000)
    }
  }
  connect()

  // Liveness timer: the engine emits no AGENT_STATUS/HEARTBEAT, so decide
  // staleness by probing the API. A quiet but alive engine stays LIVE.
  // Require 3 consecutive failures before declaring stale to avoid false positives.
  let consecutiveProbeFailures = 0
  livenessTimer = setInterval(async () => {
    if (stopped) return
    if (!wsOpen && !ok) return // still waiting on bootstrap/WS — not stale yet
    const alive = await probeApi()
    const s = store.get()
    if (s.transport === "connecting" && !wsOpen && !ok) return
    if (alive) {
      consecutiveProbeFailures = 0
      if (s.transport === "stale") {
        store.set({ transport: "live" })
        store.pushStream({
          ts: Date.now(), kind: "system", agent: "SYSTEM",
          title: "live link restored", lines: ["API probe healthy — back online"],
        })
      }
      store.clearAlertsByText("heartbeat stale")
      store.clearAlertsByText("Live engine not on")
    } else {
      consecutiveProbeFailures++
      if (consecutiveProbeFailures >= 3 && (s.transport === "live" || s.transport === "stale")) {
        store.set({ transport: "stale" })
        if (!store.get().alerts.find((a) => a.text.includes("live engine unreachable"))) {
          store.pushAlert("warn", "live engine unreachable (API probe failed 3x) — will retry automatically")
        }
      }
    }
    store.clearStaleAlerts(300000)
  }, 10000)

  await refreshAnalytics()
  analyticsTimer = setInterval(() => void refreshAnalytics(), 60000)

  // Keep safety / autonomy / brain telemetry fresh while attached.
  snapshotTimer = setInterval(() => {
    if (!stopped && ok) void refreshSnapshot()
  }, 30000)

  // Log polling (every 2 seconds when LOGS view is active)
  let logTimer: ReturnType<typeof setInterval> | null = null
  async function refreshLogs(): Promise<void> {
    try {
      const res = await apiFetch(`${API}/api/logs`)
      if (!res.ok) return
      const data: any = await res.json()
      if (Array.isArray(data?.entries) || Array.isArray(data)) {
        const entries: any[] = Array.isArray(data?.entries) ? data.entries : data
        store.set((s) => ({
          logs: {
            ...s.logs,
            entries: entries.slice(-200).map((e: any, i: number) => ({
              id: i,
              ts: e.ts ?? e.timestamp ?? "",
              level: e.level ?? "INFO",
              logger: e.logger ?? e.name ?? "",
              message: e.message ?? e.msg ?? "",
            })),
            totalLines: data.total_lines ?? entries.length,
          },
        }))
      }
    } catch { /* offline */ }
  }
  logTimer = setInterval(() => void refreshLogs(), 2000)

  // Research pipeline polling (every 30s)
  async function refreshResearch(): Promise<void> {
    try {
      const [res, skillsRes, btRes] = await Promise.all([
        apiFetch(`${API}/api/research-pipeline`),
        apiFetch(`${API}/api/skills`),
        apiFetch(`${API}/api/backtests`),
      ])
      if (res.ok) {
        const data = (await res.json()) as any[]
        if (Array.isArray(data)) {
          store.set({ researchPipeline: data.map((e: any) => ({
            id: e.id || "",
            name: e.name || "",
            source: e.source || "",
            stage: e.stage || "INGEST",
            stageProgress: typeof e.stageProgress === "number" ? e.stageProgress : 0,
            startedTs: e.startedTs || 0,
            metrics: e.metrics || {},
            notes: e.notes || "",
          }))})
        }
      }
      if (skillsRes.ok) {
        const data = (await skillsRes.json()) as any[]
        if (Array.isArray(data)) {
          store.set({ skills: data.map((s: any) => ({
            id: s.id || "",
            name: s.name || "",
            category: s.category || "analysis",
            status: s.status || "active",
            embedded: !!s.embedded,
            description: s.description || "",
            executions: s.executions || 0,
            successRate: s.successRate ?? 100,
            avgLatencyMs: s.avgLatencyMs ?? 0,
            lastRunTs: s.lastRunTs ?? null,
          }))})
        }
      }
      if (btRes.ok) {
        const data = (await btRes.json()) as any[]
        if (Array.isArray(data)) {
          store.set({ backtests: data.map((b: any) => ({
            id: b.id || "",
            strategyId: b.strategyId || "",
            period: b.period || "",
            totalTrades: b.totalTrades ?? b.trades ?? 0,
            winRate: b.winRate ?? 0,
            profitFactor: b.profitFactor ?? 0,
            sharpe: b.sharpe ?? 0,
            maxDD: b.maxDD ?? 0,
            calmar: b.calmar ?? 0,
            sortino: b.sortino ?? 0,
            expectancyR: b.expectancyR ?? 0,
            equityCurve: Array.isArray(b.equityCurve) ? b.equityCurve : [],
            monthlyReturns: Array.isArray(b.monthlyReturns) ? b.monthlyReturns : [],
            status: b.status || "complete",
            startedTs: b.startedTs ?? 0,
            completedTs: b.completedTs,
          }))})
        }
      }
    } catch { /* offline */ }
  }
  void refreshResearch()
  researchTimer = setInterval(() => void refreshResearch(), 30000)

  // ── Phase 1: Full-coverage polling (every 30s) ─────────────────────────

  async function refreshPhase1(): Promise<void> {
    try {
      const [
        mktRes, agentRes, taskRes, alphaRes, ecomRes, mtfRes, scanRes,
        complRes, diagRes, wfRes, lcRes, causalRes, rlRes, llmRes,
        winRes, setRes, rewRes, secRes, growRes, modelRes, metricRes,
        drillRes, interRes, alphaHealthRes,
      ] = await Promise.all([
        apiFetch(`${API}/api/market`),
        apiFetch(`${API}/api/agents`),
        apiFetch(`${API}/api/tasks`),
        apiFetch(`${API}/api/alpha-signals`),
        apiFetch(`${API}/api/economic-events`),
        apiFetch(`${API}/api/mtf-trends`),
        apiFetch(`${API}/api/scanner`),
        apiFetch(`${API}/api/compliance-full`),
        apiFetch(`${API}/api/diagnostics`),
        apiFetch(`${API}/api/workflows`),
        apiFetch(`${API}/api/lifecycle-full`),
        apiFetch(`${API}/api/causal-fusion`),
        apiFetch(`${API}/api/rl-training`),
        apiFetch(`${API}/api/llm-providers`),
        apiFetch(`${API}/api/win-probability`),
        apiFetch(`${API}/api/settings-full`),
        apiFetch(`${API}/api/rewards`),
        apiFetch(`${API}/api/security-full`),
        apiFetch(`${API}/api/growth`),
        apiFetch(`${API}/api/model-registry`),
        apiFetch(`${API}/api/metrics-export`),
        apiFetch(`${API}/api/drills`),
        apiFetch(`${API}/api/intermarket`),
        apiFetch(`${API}/api/alpha-health`),
      ]).catch(() => [] as Response[])

      const patch: Record<string, unknown> = {}

      // market — convert bid/ask to MarketQuote { price, changePct, domImbalance, history }
      if (mktRes?.ok) {
        const d = await mktRes.json() as any
        if (d && typeof d === "object") {
          const market: Record<string, any> = {}
          for (const [sym, q] of Object.entries(d) as [string, any][]) {
            const bid = num(q.bid), ask = num(q.ask)
            const price = (bid + ask) / 2 || bid || ask
            market[sym] = {
              price,
              changePct: num(q.spread) > 0 ? ((ask - bid) / price) * 10000 : 0,
              domImbalance: 0,
              history: [price],
            }
          }
          if (Object.keys(market).length) {
            patch.market = market
            patch.symbols = Object.keys(market)
          }
        }
      }

      // agents — Record<string, AgentState>
      if (agentRes?.ok) {
        const d = await agentRes.json() as any
        if (d && typeof d === "object") {
          const agents: Record<string, any> = {}
          for (const [id, a] of Object.entries(d) as [string, any][]) {
            agents[id] = { status: str(a.status, "idle"), task: a.task ?? null,
              lastBeat: parseTs(a.lastBeat), runsToday: num(a.runsToday) }
          }
          if (Object.keys(agents).length) patch.agents = agents
        }
      }

      // tasks
      if (taskRes?.ok) {
        const d = await taskRes.json() as any[]
        if (Array.isArray(d)) {
          patch.tasks = d.map((t: any) => ({
            id: str(t.id), stage: str(t.stage), label: str(t.stage),
            status: str(t.status, "pending"), agent: str(t.agent, ""),
            startedTs: parseTs(t.startedTs),
          }))
        }
      }

      // alpha signals
      if (alphaRes?.ok) {
        const d = await alphaRes.json() as any[]
        if (Array.isArray(d)) {
          patch.alphaSignals = d.map((s: any) => ({
            id: str(s.id), name: str(s.name), source: str(s.source),
            strength: num(s.strength), confidence: num(s.confidence),
            symbols: Array.isArray(s.symbols) ? s.symbols : [],
            timeframe: str(s.timeframe, "D1"),
            discoveredTs: parseTs(s.discoveredTs),
            status: str(s.status, "candidate"),
            ic: num(s.ic), sharpe: num(s.sharpe),
            turnover: num(s.turnover), decay: num(s.decay),
          }))
        }
      }

      // alpha health
      if (alphaHealthRes?.ok) {
        const d = await alphaHealthRes.json() as any[]
        if (Array.isArray(d)) {
          patch.alphaHealth = d.map((h: any) => ({
            name: str(h.name, h.id),
            status: str(h.status, "HEALTHY") as any,
            icMean: num(h.icMean),
            icCurrent: num(h.icCurrent, num(h.ic)),
            icTrend: num(h.icTrend),
            consecutiveLow: num(h.consecutiveLow),
          }))
        }
      }

      // economic events
      if (ecomRes?.ok) {
        const d = await ecomRes.json() as any[]
        if (Array.isArray(d)) {
          patch.economicEvents = d.map((e: any) => {
            const rawImp = str(e.impact, "LOW").toUpperCase()
            const impact: "HIGH" | "MODERATE" | "LOW" =
              rawImp === "HIGH" || rawImp === "RED"
                ? "HIGH"
                : rawImp.includes("MED") || rawImp === "ORANGE" || rawImp === "MODERATE"
                ? "MODERATE"
                : "LOW"
            return {
              id: str(e.id), title: str(e.title), currency: str(e.currency),
              time: str(e.time), impact,
              actual: e.actual == null ? undefined : String(e.actual),
              forecast: e.forecast == null ? undefined : String(e.forecast),
              previous: e.previous == null ? undefined : String(e.previous),
              deviation: e.deviation == null ? undefined : String(e.deviation),
              country: str(e.country) || str(e.currency),
            }
          })
        }
      }

      // mtf trends → store key is `mtf: TimeframeTrend[]`
      if (mtfRes?.ok) {
        const d = await mtfRes.json() as any[]
        if (Array.isArray(d)) {
          patch.mtf = d.map((t: any) => {
            const rawTrend = str(t.trend, "NEUTRAL").toUpperCase()
            const trend = rawTrend.startsWith("BULL") ? "BULL" : rawTrend.startsWith("BEAR") ? "BEAR" : "NEUTRAL"
            return {
              tf: str(t.tf),
              trend,
              adx: num(t.adx, 0),
              rsi: num(t.rsi, 50),
              bias: str(t.bias, "neutral"),
            }
          })
        }
      }

      // scanner → store key is `scannerResults: ScannerResult[]`
      if (scanRes?.ok) {
        const d = await scanRes.json() as any[]
        if (Array.isArray(d)) {
          patch.scannerResults = d.map((s: any) => ({
            symbol: str(s.symbol), score: num(s.score),
            reasons: Array.isArray(s.reasons) ? s.reasons : [],
            atr: num(s.atr), price: num(s.price),
            providerContext: s.providerContext ?? { providersUsed: [], cftcStatus: "", providerScore: 0 },
          }))
        }
      }

      // compliance → split into complianceEvents, chainIntegrity, moduleGroups
      if (complRes?.ok) {
        const d = await complRes.json() as any
        if (d) {
          patch.complianceEvents = Array.isArray(d.events) ? d.events.map((e: any) => ({
            id: str(e.id), type: str(e.type), severity: str(e.severity, "INFO"),
            agent: str(e.agent), symbol: str(e.symbol), details: str(e.details),
            ts: parseTs(e.ts),
          })) : []
          patch.chainIntegrity = d.chainIntegrity ?? { valid: true, entriesVerified: 0, durationMs: 0 }
          patch.moduleGroups = Array.isArray(d.moduleGroups) ? d.moduleGroups.map((g: any) => ({
            name: str(g.name), count: num(g.count), lastTs: parseTs(g.lastTs),
          })) : []
        }
      }

      // diagnostics → split into errorCodes, lintIssues
      if (diagRes?.ok) {
        const d = await diagRes.json() as any
        if (d) {
          patch.errorCodes = Array.isArray(d.errorCodes) ? d.errorCodes.map((e: any) => ({
            code: str(e.code), module: str(e.module, "unknown"),
            message: str(e.value, e.message), severity: str(e.severity, "info"),
            count: num(e.count, 1), lastTs: parseTs(e.lastTs),
          })) : []
          patch.lintIssues = Array.isArray(d.lintIssues) ? d.lintIssues.map((l: any) => ({
            file: str(l.file, ""), line: num(l.line), rule: str(l.rule),
            severity: str(l.severity, "info"), message: str(l.detail, l.message),
          })) : []
        }
      }

      // workflows → store key is `workflowExecutions: WorkflowExecution[]`
      if (wfRes?.ok) {
        const d = await wfRes.json() as any[]
        if (Array.isArray(d)) {
          patch.workflowExecutions = d.map((w: any) => ({
            id: str(w.id), workflowName: str(w.name), symbol: str(w.symbol),
            decision: str(w.decision, "HOLD"),
            steps: Array.isArray(w.steps) ? w.steps : [],
            totalLatencyMs: num(w.totalLatencyMs),
            timestamp: parseTs(w.startedTs),
          }))
        }
      }

      // lifecycle → store key is `lifecycleEntries: LifecycleEntry[]`
      if (lcRes?.ok) {
        const d = await lcRes.json() as any[]
        if (Array.isArray(d)) {
          patch.lifecycleEntries = d.map((e: any) => ({
            id: str(e.id), name: str(e.name),
            state: str(e.state, "CANDIDATE"),
            drift: e.drift ? String(e.drift) : undefined,
            transitions: Array.isArray(e.transitions) ? e.transitions : [],
            blocked: !!e.blocked,
            updatedAt: parseTs(e.updatedAt),
          }))
        }
      }

      // causal fusion
      if (causalRes?.ok) {
        const d = await causalRes.json() as any
        if (d) {
          patch.causalChainPaths = Array.isArray(d.causalChainPaths) ? d.causalChainPaths : []
          patch.fusionResults = Array.isArray(d.fusionResults) ? d.fusionResults : []
          patch.eventReactions = Array.isArray(d.eventReactions) ? d.eventReactions.map((r: any) => ({
            id: str(r.id), eventName: str(r.event, r.eventName),
            symbol: str(r.symbol), actual: num(r.actual), forecast: num(r.forecast),
            previous: num(r.previous), surprise: num(r.surprise),
            windows: r.windows ?? {}, sourceQuality: str(r.sourceQuality, "UNVERIFIED"),
            timestamp: parseTs(r.ts, r.timestamp),
          })) : []
        }
      }

      // rl training
      if (rlRes?.ok) {
        const d = await rlRes.json() as any
        if (d) {
          patch.rlTrainer = {
            qLearning: d.qLearning ?? { totalUpdates: 0, explorationCount: 0,
              exploitationCount: 0, explorationRate: 0, qTableSize: 0,
              avgReward100: 0, totalReward: 0, bestReward: 0, worstReward: 0 },
            ppo: d.ppo ?? { policyLoss: 0, valueLoss: 0, entropy: 0, clipEps: 0,
              gamma: 0, gaeLambda: 0, backend: "", actions: [] },
            episodes: Array.isArray(d.episodes) ? d.episodes : [],
            ppoUpdates: Array.isArray(d.ppoUpdates) ? d.ppoUpdates : [],
            bestStates: Array.isArray(d.bestStates) ? d.bestStates : [],
            isTraining: !!d.isTraining,
            episodesCompleted: num(d.episodesCompleted),
          }
        }
      }

      // llm providers — LLMProvider needs baseUrl, models[], health, rpm, etc.
      if (llmRes?.ok) {
        const d = await llmRes.json() as any[]
        if (Array.isArray(d)) {
          patch.llmProviders = d.map((p: any) => ({
            id: str(p.id), name: str(p.name),
            baseUrl: str(p.baseUrl, "https://api.openai.com"),
            models: Array.isArray(p.models) ? p.models : [],
            health: str(p.health, "healthy"),
            rpm: num(p.rpm), requestsToday: num(p.requestsToday),
            failoverPriority: num(p.failoverPriority),
          }))
        }
      }

      // win probability → store key is `winProbResult: WinProbResult | null`
      if (winRes?.ok) {
        const d = await winRes.json() as any
        if (d) {
          patch.winProbResult = {
            winProbability: d.winProbability != null ? num(d.winProbability) : null,
            confidence: num(d.confidence),
            kellyFraction: num(d.kellyFraction),
            recommendation: str(d.recommendation, "SKIP"),
            features: Array.isArray(d.features) ? d.features.map((f: any) => ({
              name: str(f.name), value: num(f.value), importance: num(f.importance),
            })) : [],
          }
        }
      }

      // settings — SettingsState needs selectedConfig
      if (setRes?.ok) {
        const d = await setRes.json() as any
        if (d) {
          patch.settings = {
            autonomyLevels: Array.isArray(d.autonomyLevels) ? d.autonomyLevels : [],
            configEntries: Array.isArray(d.configEntries) ? d.configEntries : [],
            selectedConfig: 0,
          }
        }
      }

      // rewards → store key is `rewardSystem: RewardSystemState`
      if (rewRes?.ok) {
        const d = await rewRes.json() as any
        if (d) {
          patch.rewardSystem = {
            entries: Array.isArray(d.entries) ? d.entries : [],
            agentSummaries: Array.isArray(d.agentSummaries) ? d.agentSummaries : [],
            totalRNet: num(d.totalRNet), avgRNet: num(d.avgRNet),
            cvarMetrics: Array.isArray(d.cvarMetrics) ? d.cvarMetrics : [],
            sustainabilityScore: num(d.sustainabilityScore),
            selectedEntry: 0,
          }
        }
      }

      // security — SecurityState needs lastScrubTs, selectedScan
      if (secRes?.ok) {
        const d = await secRes.json() as any
        if (d) {
          patch.security = {
            scans: Array.isArray(d.scans) ? d.scans.map((s: any) => ({
              id: str(s.id), scanner: str(s.scanner) || str(s.name) || "security_audit",
              ts: parseTs(s.ts), findings: num(s.findings),
              severity: str(s.severity, "clean"), detail: str(s.detail, ""),
            })) : [],
            secretsHygiene: Array.isArray(d.secretsHygiene) ? d.secretsHygiene : [],
            journalScope: str(d.journalScope, "LIVE"),
            gitScrubStatus: str(d.gitScrubStatus, "clean"),
            lastScrubTs: parseTs(d.lastScrubTs),
            selectedScan: 0,
          }
        }
      }

      // growth — GrowthState needs selectedSim
      if (growRes?.ok) {
        const d = await growRes.json() as any
        if (d) {
          patch.growth = {
            monteCarloSims: Array.isArray(d.monteCarloSims) ? d.monteCarloSims : [],
            projections: Array.isArray(d.projections) ? d.projections : [],
            rollingExpectancy: num(d.rollingExpectancy),
            rollingSharpe: num(d.rollingSharpe),
            currentGrowthRate: num(d.currentGrowthRate),
            selectedSim: 0,
          }
        }
      }

      // model registry → store key is `modelRegistry: ModelState` (needs artifacts wrapper)
      if (modelRes?.ok) {
        const d = await modelRes.json() as any[]
        if (Array.isArray(d)) {
          patch.modelRegistry = {
            artifacts: d.map((m: any) => ({
              id: str(m.id), name: str(m.name), version: str(m.version),
              family: str(m.family, m.kind), status: str(m.status, "CANDIDATE"),
              metrics: m.metrics ?? {}, trainingDataHash: str(m.trainingDataHash, ""),
              schemaVersion: str(m.schemaVersion, ""), trainedTs: parseTs(m.createdTs),
              notes: str(m.notes, ""),
            })),
            selectedModel: 0,
          }
        }
      }

      // metrics export — MetricsState
      if (metricRes?.ok) {
        const d = await metricRes.json() as any
        if (d) {
          patch.metricsExporter = {
            prometheusMetrics: Array.isArray(d.prometheusMetrics) ? d.prometheusMetrics.map((m: any) => ({
              name: str(m.name), value: num(m.value),
              labels: m.labels ?? {}, type: str(m.type, "gauge"),
            })) : [],
            heartbeats: Array.isArray(d.heartbeats) ? d.heartbeats : [],
            exportStatus: str(d.exportStatus, "active"),
            lastExportTs: parseTs(d.lastExportTs),
          }
        }
      }

      // drills → store key is `drills: DrillsState` (needs wrapper)
      if (drillRes?.ok) {
        const d = await drillRes.json() as any[]
        if (Array.isArray(d)) {
          patch.drills = {
            drills: d.map((r: any) => ({
              id: str(r.id), name: str(r.name),
              type: str(r.type, "kill_switch"),
              status: str(r.status, "passed"),
              startedTs: parseTs(r.ts), completedTs: parseTs(r.completedTs),
              durationMs: num(r.durationMs),
              steps: Array.isArray(r.steps) ? r.steps : [],
            })),
            selectedDrill: 0,
          }
        }
      }

      // intermarket
      if (interRes?.ok) {
        const d = await interRes.json() as any[]
        if (Array.isArray(d)) {
          patch.intermarketRegimes = d.map((r: any) => ({
            pair: str(r.pair), correlation: num(r.correlation),
            regime: str(r.regime, "NEUTRAL"), lag: str(r.lag),
            strength: num(r.strength), signal: str(r.signal),
          }))
        }
      }

      if (Object.keys(patch).length) store.set(patch as never)
    } catch { /* offline */ }
  }
  void refreshPhase1()
  phase1Timer = setInterval(() => void refreshPhase1(), 30000)

  return () => {
    stopped = true
    if (livenessTimer) clearInterval(livenessTimer)
    if (analyticsTimer) clearInterval(analyticsTimer)
    if (snapshotTimer) clearInterval(snapshotTimer)
    if (logTimer) clearInterval(logTimer)
    if (researchTimer) clearInterval(researchTimer)
    if (phase1Timer) clearInterval(phase1Timer)
    try {
      ws?.close()
    } catch { }
  }
}

export interface ControlResponse {
  ok: boolean
  control_id?: number
  action?: string
  status?: string
  reason?: string
}

export interface ControlStatus {
  ok: boolean
  control_id: number
  action: string
  actor?: string
  source?: string
  status: "REQUESTED" | "ACKNOWLEDGED" | "APPLIED" | "FAILED" | "EXPIRED" | string
  issued_at?: string
  acked_at?: string
  applied_at?: string
  result?: {
    closed?: number
    cancelled?: number
    failed?: number
    remaining?: number
    sweeps?: number
    error?: string
    [key: string]: any
  }
}

export async function sendControl(action: string, payload?: Record<string, any>): Promise<ControlResponse> {
  try {
    const res = await apiFetch(`${API}/api/control/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: "command-deck", ...(payload ?? {}) }),
    })
    const data: any = await res.json().catch(() => ({}))
    if (res.ok) void refreshSnapshot()
    return {
      ok: res.ok && data.ok !== false,
      control_id: data.control_id,
      action: data.action ?? action,
      status: data.status,
      reason: data.reason ?? data.detail,
    }
  } catch (err: any) {
    return { ok: false, reason: err?.message ?? "network error" }
  }
}

export async function getControlStatus(controlId: number): Promise<ControlStatus | null> {
  try {
    const res = await apiFetch(`${API}/api/control/${controlId}`)
    if (!res.ok) return null
    return (await res.json()) as ControlStatus | null
  } catch {
    return null
  }
}

export async function sendApprovalDecision(approvalId: string, approve: boolean): Promise<boolean> {
  try {
    const res = await apiFetch(`${API}/api/control/approve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        approval_id: approvalId,
        decision: approve ? "APPROVED" : "REJECTED",
        actor: "operator@deck",
      }),
    })
    if (res.ok) void refreshSnapshot()
    return res.ok
  } catch {
    return false
  }
}
