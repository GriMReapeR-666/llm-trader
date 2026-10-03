import { useMemo } from "react"
import { useDeck } from "../store"
import { fitRow } from "../layout"
import type { Seg } from "../layout"
import { T } from "../theme"
import { DataTable, type DataColumn } from "./DataTable"
import { Badge } from "./Badge"

export type SubsystemHealth = {
  id: string
  name: string
  stage: string
  status: "REAL" | "HEURISTIC" | "DUMMY" | "NOT_WIRED" | "DEFERRED" | "UNVERIFIED"
  wiring: string
  gate: string
  detail: string
  source?: "LIVE" | "STATIC-AUDIT"
}

const STATUS_CFG: Record<SubsystemHealth["status"], { label: string; color: string; icon: string }> = {
  REAL: { label: "REAL", color: T.up, icon: "✔" },
  HEURISTIC: { label: "HEURISTIC", color: T.warn, icon: "◐" },
  DUMMY: { label: "DUMMY", color: T.down, icon: "✗" },
  NOT_WIRED: { label: "NOT WIRED", color: T.dim, icon: "○" },
  DEFERRED: { label: "DEFERRED", color: T.purple, icon: "◇" },
  UNVERIFIED: { label: "UNVERIFIED", color: T.dim, icon: "◌" },
}

// ── Live-derived rows (recomputed from real deck state every render) ────────
function liveRows(opts: {
  mt5Connected: boolean
  strategies: number
  alphaSignals: number
  streamEvents: number
  cycles: number
}): SubsystemHealth[] {
  const { mt5Connected, strategies, alphaSignals, streamEvents, cycles } = opts
  const rows: SubsystemHealth[] = []

  // The deck currently receives NO Kronos backend report from the engine, so
  // the honest verdict is UNVERIFIED — never REAL/HEURISTIC by inference.
  rows.push({
    id: "live-kronos",
    name: "Kronos Forecast",
    stage: "ALPHA",
    status: "UNVERIFIED",
    wiring: "llm_advisory only",
    gate: "risk_adj 0.5-1.0",
    detail: "no engine-reported Kronos backend in deck state (engine logs it at boot)",
    source: "LIVE",
  })

  rows.push({
    id: "live-mt5",
    name: "MT5 Bridge",
    stage: "EXECUTION",
    status: mt5Connected ? "REAL" : "UNVERIFIED",
    wiring: "MCP transport / internal update",
    gate: "DeterministicValidator sole authority",
    detail: mt5Connected ? "terminal connected (deck snapshot)" : "terminal not connected",
    source: "LIVE",
  })

  rows.push({
    id: "live-alpha",
    name: "Alpha Signals",
    stage: "ALPHA",
    status: alphaSignals > 0 ? "REAL" : "UNVERIFIED",
    wiring: "mining enrichment per cycle",
    gate: "composite IC reported by engine",
    detail: `${alphaSignals} signal${alphaSignals === 1 ? "" : "s"} registered in deck state`,
    source: "LIVE",
  })

  rows.push({
    id: "live-strategies",
    name: "Strategy Registry",
    stage: "DECISION",
    status: strategies > 0 ? "REAL" : "UNVERIFIED",
    wiring: "snapshot strategies[]",
    gate: "progressive gates (engine-side)",
    detail: `${strategies} strateg${strategies === 1 ? "y" : "ies"} reported by engine`,
    source: "LIVE",
  })

  rows.push({
    id: "live-loop",
    name: "Cycle Loop",
    stage: "SYSTEM",
    status: cycles > 0 ? "REAL" : "UNVERIFIED",
    wiring: "engine CYCLE_COMPLETE / heartbeat",
    gate: "n/a",
    detail: cycles > 0 ? `${cycles} cycles completed` : "no cycles observed yet",
    source: "LIVE",
  })

  rows.push({
    id: "live-stream",
    name: "Event Stream",
    stage: "OBSERVE",
    status: streamEvents > 0 ? "REAL" : "UNVERIFIED",
    wiring: "WS harness-events + replay",
    gate: "n/a",
    detail: `${streamEvents} stream event${streamEvents === 1 ? "" : "s"} in deck state`,
    source: "LIVE",
  })

  return rows
}

// ── Static self-audit rows (deck's own historical review, 2026-09-24). ──────
// These describe engine-side code reality as assessed in that audit; the deck
// cannot re-verify them from live data, so they are explicitly marked
// STATIC-AUDIT / UNVERIFIED rather than presented as live status.
const STATIC_AUDIT_ROWS: SubsystemHealth[] = [
  { id: "cpcv", name: "CPCV Purged CV", stage: "VALIDATE", status: "UNVERIFIED", wiring: "auto_quant→DeterministicValidator", gate: "PBO<0.25 DSR>0.70", detail: "2026-09-24 audit: live; correct purge still test-only — not re-verified since", source: "STATIC-AUDIT" },
  { id: "walkforward", name: "Walk-Forward Decay", stage: "VALIDATE", status: "UNVERIFIED", wiring: "RiskManager 0.5x / DeterministicValidator HOLD", gate: "DEAD->0 DECAYING->0.5", detail: "2026-09-24 audit: fixed P0 gate tests 10 passed — not re-verified since", source: "STATIC-AUDIT" },
  { id: "synthetic-garch", name: "GARCH Synthetic", stage: "RESEARCH", status: "UNVERIFIED", wiring: "kronos_synthetic_generator", gate: "advisory", detail: "2026-09-24 audit: GARCH(1,1)+Cholesky+Merton real — not re-verified since", source: "STATIC-AUDIT" },
  { id: "synthetic-drift", name: "Drift Synthetic", stage: "RESEARCH", status: "UNVERIFIED", wiring: "KronosPredictor.generate_synthetic_regime", gate: "none", detail: "2026-09-24 audit: drift_map constants — not re-verified since", source: "STATIC-AUDIT" },
  { id: "alpha-discovery", name: "Alpha Discovery", stage: "ALPHA", status: "UNVERIFIED", wiring: "auto_quant 500c -> LLM prompt", gate: "fallback 5 static exprs", detail: "2026-09-24 audit: FactorMiner DSL real, often fallback — not re-verified since", source: "STATIC-AUDIT" },
  { id: "regime-hmm", name: "Regime HMM", stage: "ALPHA", status: "UNVERIFIED", wiring: "MacroRegimeClassifier", gate: "sigmoid not Baum-Welch", detail: "2026-09-24 audit: rule-based thresholds — not re-verified since", source: "STATIC-AUDIT" },
  { id: "vbt", name: "VectorBT Engine", stage: "BACKTEST", status: "UNVERIFIED", wiring: "vbt_engine numpy fallback", gate: "never calls vectorbt", detail: "2026-09-24 audit: header promises vectorbt — not re-verified since", source: "STATIC-AUDIT" },
  { id: "ppo-numpy", name: "PPO Trainer", stage: "RESEARCH", status: "UNVERIFIED", wiring: "NumpyActorCritic linear", gate: "torch missing -> linear", detail: "2026-09-24 audit: linear W*pi+b softmax — not re-verified since", source: "STATIC-AUDIT" },
  { id: "finrl", name: "FinRL Env", stage: "RESEARCH", status: "UNVERIFIED", wiring: "auto_quant random choice", gate: "30 random steps", detail: "2026-09-24 audit: np.random.choice no policy — not re-verified since", source: "STATIC-AUDIT" },
  { id: "meta-loop", name: "Meta Loop", stage: "META", status: "UNVERIFIED", wiring: "meta_loop hash-chain", gate: "corpus 0 patterns", detail: "2026-09-24 audit: shadow DSR proxy — not re-verified since", source: "STATIC-AUDIT" },
  { id: "mars-maml", name: "MARS MAML", stage: "META", status: "UNVERIFIED", wiring: "artifact -> alpha_ensemble", gate: "mean_ic*5 clip", detail: "2026-09-24 audit: single-sample variance — not re-verified since", source: "STATIC-AUDIT" },
  { id: "ewc", name: "EWC Forgetting", stage: "META", status: "UNVERIFIED", wiring: "RLTrainer Q+ Fisher penalty", gate: "snapshots 5 synthetic", detail: "2026-09-24 audit: math real, data synthetic — not re-verified since", source: "STATIC-AUDIT" },
  { id: "evolutionary", name: "Evolutionary", stage: "META", status: "UNVERIFIED", wiring: "auto_quant Sharpe fitness", gate: "pop10 gen2 simplified", detail: "2026-09-24 audit: P1 deferred full CPCV — not re-verified since", source: "STATIC-AUDIT" },
  { id: "grpo-ppo", name: "GRPO/PPO MetaRL", stage: "META", status: "UNVERIFIED", wiring: "artifacts promotion_safe False", gate: "PBO<0.25 DSR>0.70", detail: "2026-09-24 audit: advisory until promotion gate — not re-verified since", source: "STATIC-AUDIT" },
  { id: "kb", name: "Knowledge Base", stage: "RESEARCH", status: "NOT_WIRED", wiring: "0 callers hot path", gate: "41+15 files isolated", detail: "2026-09-24 audit: MASTER_TRANSCRIPT never injected to LLM prompt", source: "STATIC-AUDIT" },
  { id: "scrapers", name: "Scrapers ETL", stage: "RESEARCH", status: "NOT_WIRED", wiring: "tools/scrapers 14 files manual", gate: "0 orchestrator imports", detail: "2026-09-24 audit: build_knowledge_base CLI only", source: "STATIC-AUDIT" },
  { id: "analytical", name: "Analytical 01-07", stage: "RESEARCH", status: "NOT_WIRED", wiring: "7 frameworks test only", gate: "GLOBAL_REGISTRY unref", detail: "2026-09-24 audit: geopolitical/commodity not wired", source: "STATIC-AUDIT" },
  { id: "harness", name: "TradingContext Harness", stage: "SYSTEM", status: "UNVERIFIED", wiring: "--harness default off", gate: "dual path 44 plugins", detail: "2026-09-24 audit: P2 cleanup direct MarketDataProvider — not re-verified since", source: "STATIC-AUDIT" },
  { id: "cost", name: "Cost Model", stage: "EXECUTION", status: "UNVERIFIED", wiring: "3 models diverged", gate: "vbt 0.0002 vs TCE vs assess_costs", detail: "2026-09-24 audit: PaperBroker 0.5 pips not shared — not re-verified since", source: "STATIC-AUDIT" },
  { id: "hbm", name: "TradingContext", stage: "SYSTEM", status: "UNVERIFIED", wiring: "TradingContext + CapabilitySandbox", gate: "DeterministicValidator sole authority", detail: "2026-09-24 audit: only RiskController submits — not re-verified since", source: "STATIC-AUDIT" },
  { id: "laya-macro", name: "Laya ModernBERT", stage: "MACRO", status: "UNVERIFIED", wiring: "opportunity_fusion→MacroFusionGate", gate: "D0-D13 firewall", detail: "2026-09-24 audit: acc/recall validated, execution_authority=False — not re-verified since", source: "STATIC-AUDIT" },
]

export function OrchestrationHealthMatrix(props: { compact?: boolean; width?: number }) {
  const mt5 = useDeck((s) => s.mt5)
  const strategies = useDeck((s) => s.strategies)
  const alphaSignals = useDeck((s) => s.alphaSignals)
  const stream = useDeck((s) => s.stream)
  const loopStats = useDeck((s) => s.loopStats)

  const rows = useMemo(() => {
    const live = liveRows({
      mt5Connected: mt5?.terminal?.connected ?? false,
      strategies: strategies.length,
      alphaSignals: alphaSignals.length,
      streamEvents: stream.length,
      cycles: loopStats?.cyclesCompleted ?? 0,
    })
    return [...live, ...STATIC_AUDIT_ROWS]
  }, [mt5, strategies, alphaSignals, stream, loopStats])

  const columns: DataColumn<SubsystemHealth>[] = [
    {
      key: "name",
      label: "SUBSYSTEM",
      width: props.compact ? 18 : 22,
      render: (r) => r.name,
      color: (r) => STATUS_CFG[r.status].color,
    },
    {
      key: "stage",
      label: "STAGE",
      width: 10,
      align: "center" as const,
      render: (r) => r.stage,
      color: () => T.muted,
    },
    {
      key: "status",
      label: "REALITY",
      width: 12,
      align: "center" as const,
      render: (r) => `${STATUS_CFG[r.status].icon} ${STATUS_CFG[r.status].label}${r.source === "STATIC-AUDIT" ? "*" : ""}`,
      color: (r) => STATUS_CFG[r.status].color,
    },
    {
      key: "gate",
      label: "GATE",
      width: props.compact ? 18 : 24,
      render: (r) => r.gate,
    },
    {
      key: "wiring",
      label: "WIRING",
      width: props.compact ? 20 : 28,
      render: (r) => r.wiring,
    },
  ]

  const realCount = rows.filter((r) => r.status === "REAL").length
  const unverifiedCount = rows.filter((r) => r.status === "UNVERIFIED").length
  const notWiredCount = rows.filter((r) => r.status === "NOT_WIRED").length
  const total = rows.length
  const verified = realCount
  const healthPct = total > 0 ? verified / total : 0
  const avail = props.width != null ? Math.max(20, props.width - 1) : 999
  const tableW = props.width != null ? Math.max(20, props.width - 1) : undefined

  return (
    <box flexDirection="column" gap={1}>
      {fitRow(avail, [
        { t: "◈ ORCHESTRATION HEALTH MATRIX", fg: T.cyan },
        { t: ` · ${realCount} live-verified · ${unverifiedCount} unverified · ${notWiredCount} not wired · ${total} total`, fg: T.dim },
      ])}
      {(() => {
        const barW = 30
        const filled = Math.round(healthPct * barW)
        const pct = `${Math.round(healthPct * 100)}%`
        const color = healthPct > 0.6 ? T.up : healthPct > 0.4 ? T.warn : T.down
        const segs: Seg[] = [{ t: "LIVE-VERIFIED ", fg: T.dim }]
        if (filled > 0) segs.push({ t: "▰".repeat(filled), fg: color })
        segs.push({ t: "▱".repeat(barW - filled), fg: T.dim })
        segs.push({ t: ` ${pct}`, fg: T.dim })
        segs.push({ t: " · rows marked * are static audit claims, not live status", fg: T.dim })
        return fitRow(avail, segs)
      })()}
      <DataTable columns={columns} data={rows} emptyMessage="no subsystems" headerColor={T.cyan} width={tableW} />
      {!props.compact && (
        <box flexDirection="column" gap={0}>
          <text fg={T.dim}>─ Row sources ─</text>
          <text fg={T.muted}>
            <span fg={T.up}>LIVE rows</span>: recomputed from real deck state (snapshot/WS). No hardcoded verdicts.
          </text>
          <text fg={T.muted}>
            <span fg={T.dim}>* STATIC-AUDIT rows</span>: 2026-09-24 code review findings, NOT re-verified from live data — treat as leads, not status.
          </text>
        </box>
      )}
    </box>
  )
}

export function WiringBadge(props: { status: SubsystemHealth["status"] }) {
  const cfg = STATUS_CFG[props.status]
  return <Badge label={cfg.label} color={cfg.color} icon={cfg.icon} />
}
