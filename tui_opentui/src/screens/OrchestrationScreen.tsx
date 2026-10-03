import { T } from "../theme"
import { useContentWidth, RES, clip, fitRow } from "../layout"
import { useDeck } from "../store"
import { OrchestrationHealthMatrix } from "../components/OrchestrationHealthMatrix"
import { MetaLearningTracker } from "../components/MetaLearningTracker"
import { DataTable, type DataColumn } from "../components/DataTable"

type PipelineRow = { stage: string; status: string; gate: string; output: string; next: string }

const PIPELINE: PipelineRow[] = [
  { stage: "ALPHA", status: "HEURISTIC", gate: "IC>0.05", output: "candidate alpha", next: "─▶ BACKTEST" },
  { stage: "BACKTEST", status: "HEURISTIC", gate: "vectorbt fallback", output: "Sharpe/Sortino", next: "─▶ CPCV" },
  { stage: "CPCV", status: "REAL", gate: "PBO<0.25 DSR>0.70", output: "PBO/dsr", next: "─▶ WALK-FORWARD" },
  { stage: "WALK-FWD", status: "REAL", gate: "DEAD->HOLD", output: "health", next: "─▶ PAPER" },
  { stage: "PAPER", status: "REAL", gate: "50 trades PF>1.5", output: "paper proven", next: "─▶ DEPLOY" },
  { stage: "LIVE", status: "REAL", gate: "DeterministicValidator", output: "order", next: "↻" },
]

const ORN_TOP = "╔══ ORCHESTRATION · ALPHA ▶ BACKTEST ▶ VALIDATE ▶ PAPER ▶ LIVE "

/** top/bottom box-art borders sized to the measured width (single line). */
function Orn(props: { bottom?: boolean; width: number }) {
  const start = props.bottom ? "╚═" : ORN_TOP
  const end = props.bottom ? "╝" : "╗"
  const fill = Math.max(2, props.width - start.length - end.length)
  return <text fg={T.cyan}>{(start + "═".repeat(fill) + end).slice(0, props.width)}</text>
}

function sepRow(avail: number) {
  return <text fg={T.borderBright}>{"─".repeat(Math.max(4, avail))}</text>
}

export function OrchestrationScreen() {
  const quant = useDeck((s) => s.quantMonitor)
  const lifecycle = useDeck((s) => s.lifecycleEntries)
  const validation = useDeck((s) => s.validationPipeline)
  const research = useDeck((s) => s.researchPipeline)
  const account = useDeck((s) => s.account)

  const pbo = quant.pbo ?? null
  const dsr = quant.dsr ?? null
  const pboPass = pbo !== null && pbo < 0.25
  const dsrPass = dsr !== null && dsr > 0.7
  const pipelineGate = pboPass && dsrPass

  const columns: DataColumn<PipelineRow>[] = [
    { key: "stage", label: "STAGE", width: 12, render: (r) => r.stage, color: (r) => (r.status === "REAL" ? T.up : T.warn) },
    { key: "status", label: "AUDIT*", width: 10, align: "center", render: (r) => r.status, color: (r) => (r.status === "REAL" ? T.up : T.warn) },
    { key: "gate", label: "GATE", width: 20, render: (r) => r.gate },
    { key: "output", label: "OUTPUT", width: 18, render: (r) => r.output },
    { key: "next", label: "NEXT", width: 14, render: (r) => r.next, color: () => T.cyan },
  ]

  // ── measured geometry: every budget derives from the live terminal width ──
  const contentW = useContentWidth()
  const avail = Math.max(20, contentW - 2 - RES) // scrollbox inner (padding L/R 1) − scrollbar
  const boxAvail = Math.max(12, avail - 4) // bordered box padding 1: border 2 + padding 2

  const pboBar = (pbo !== null ? Math.min(1, pbo) : 0) * 14
  const dsrBar = (dsr !== null ? Math.min(1, dsr / 3) : 0) * 14

  return (
    <scrollbox flexDirection="column" flexGrow={1} paddingLeft={1} paddingRight={1} gap={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
      <Orn width={avail} />

      {/* Serial Position #9 essentials first + Von Restorff #7 primary pipeline */}
      <box borderStyle="single" borderColor={T.cyan} flexDirection="column" padding={1} gap={1}>
        <text fg={T.cyan}>{clip("◈ PRIMARY FLOW · THE MONEY PATH (Hick #1: one purpose per screen)", boxAvail)}</text>
        {sepRow(boxAvail)}
        <DataTable columns={columns} data={PIPELINE} headerColor={T.cyan} width={boxAvail} />
        <text fg={T.dim}>* AUDIT column = static 2026-09-24 code review verdicts, not live re-verification. Stages/gates are the designed pipeline; live CPCV/PBO evidence appears below when the engine reports it.</text>
        {fitRow(boxAvail, [
          { t: "Uniform Connectedness #17: ", fg: T.dim },
          { t: pipelineGate ? "● CPCV gate PASS → paper→live allowed" : "○ CPCV gate HOLD → live blocked", fg: pipelineGate ? T.up : T.warn },
          { t: ` · ${lifecycle.length} strategies · ${validation.length} validation runs · ${research.length} research pipelines`, fg: T.dim },
        ])}
        {fitRow(boxAvail, [
          { t: "PBO ", fg: T.dim },
          { t: "▰".repeat(Math.round(pboBar)), fg: pboPass ? T.up : T.warn },
          { t: "▱".repeat(Math.max(0, 14 - Math.round(pboBar))), fg: T.dim },
          { t: pbo !== null ? ` ${pbo.toFixed(3)}` : " —", fg: pboPass ? T.up : pbo !== null ? T.down : T.dim },
          { t: pboPass ? " PASS" : pbo !== null ? " HOLD" : " STANDBY", fg: pboPass ? T.up : pbo !== null ? T.down : T.dim },
          { t: "  DSR ", fg: T.dim },
          { t: "▰".repeat(Math.round(dsrBar)), fg: dsrPass ? T.up : T.warn },
          { t: "▱".repeat(Math.max(0, 14 - Math.round(dsrBar))), fg: T.dim },
          { t: dsr !== null ? ` ${dsr.toFixed(2)}` : " —", fg: dsrPass ? T.up : dsr !== null ? T.down : T.dim },
          { t: dsrPass ? " PASS" : dsr !== null ? " HOLD" : " STANDBY", fg: dsrPass ? T.up : dsr !== null ? T.down : T.dim },
        ])}
      </box>

      {/* Doherty #6 immediate + Postel #14 error prevention */}
      <box borderStyle="single" borderColor={T.border} flexDirection="column" padding={1}>
        <text fg={T.warn}>{clip("◈ GATES · Prevent Errors Before They Occur (Postel #14)", boxAvail)}</text>
        {sepRow(boxAvail)}
        <box flexDirection="column" gap={0}>
          <text fg={T.muted}>  walk-forward DEAD ─▶ <span fg={T.down}>DeterministicValidator HOLD</span> <span fg={T.dim}>· RiskManager 0 → zero size (tests/test_p0_gates 10 passed)</span></text>
          <text fg={T.muted}>  overfit CRITICAL ─▶ <span fg={T.down}>HOLD</span> <span fg={T.dim}>· HIGH ─▶ 75% reduction</span></text>
          <text fg={T.muted}>  CPCV PBO&gt;0.35 DSR&lt;0.50 ─▶ <span fg={T.down}>ESCALATION streak 3 → HOLD</span></text>
          <text fg={T.muted}>  cost divergence ─▶ <span fg={T.warn}>3 models: vbt 0.0002 / TCE Almgren / assess_costs</span> <span fg={T.dim}>· unified TCE TODO</span></text>
          <text fg={T.muted}>  GRPO/PPO promotion_safe False ─▶ <span fg={T.purple}>DEFERRED until PBO&lt;0.25 DSR&gt;0.70</span> + human approve_live_rollout</text>
        </box>
      </box>

      {/* Miller #5 chunk + Prägnanz simplest + Goal-Gradient #20 */}
      <box borderStyle="single" borderColor={T.purple} flexDirection="column" padding={1}>
        <MetaLearningTracker width={boxAvail} />
      </box>

      {/* Hick #1 reduce choices + Proximity #4 grouped */}
      <box borderStyle="single" borderColor={T.border} flexDirection="column" padding={1}>
        <OrchestrationHealthMatrix width={boxAvail} />
      </box>

      {/* Peak-End #10 memorable completion + Zeigarnik #11 visible progress */}
      <box borderStyle="single" borderColor={T.teal} flexDirection="column" padding={1}>
        <text fg={T.teal}>{clip("◈ SYSTEM LEARN · How It Uses Everything When (Tesler #19 progressive disclosure)", boxAvail)}</text>
        {sepRow(boxAvail)}
        <text fg={T.muted}>  1. AutoQuantTools every N cycles: CPCV 2016 kalman 5 bocpd 1 tca 20 walk 500 evo 2000 — enriches toon</text>
        <text fg={T.muted}>  2. LiveAdvisoryHub per-cycle: hierarchical/copula/meta_labeling/bocpd/fracDiff/kalman (advisory_only)</text>
        <text fg={T.muted}>  3. LLMOrchestrator injects toon_lite + TradingMemory APPROVED lessons → JSON action → DeterministicValidator sole authority</text>
        <text fg={T.muted}>  4. PositionManager replaces LLM for exits (R thresholds) + GrowthController reduces risk only</text>
        <text fg={T.muted}>  5. Close → PerformanceMonitor → meta_loop.log_outcome (fixed 42b194d) → EWC snapshot on regime shift → loop</text>
        <text fg={T.dim}>  Parkinson #18: shortest clear path to HOLD/EXECUTE shown above. Implementation req 1-8: goal=EXECUTE, next action highlighted in pipeline table, feedback 400ms via LiveModeBanner, errors recoverable via /flatten, HALT confirm.</text>
        {fitRow(boxAvail, [
          { t: `account ${account.balance.toFixed(0)}→${account.equity.toFixed(0)}`, fg: T.dim },
          { t: ` · equity curve ${account.equityCurve.length} pts`, fg: T.dim },
          { t: ` · drawdown ${account.drawdownPct.toFixed(2)}%`, fg: T.dim },
          { t: ` · ${account.openPositions} open`, fg: T.dim },
        ])}
      </box>

      <Orn bottom width={avail} />
    </scrollbox>
  )
}
