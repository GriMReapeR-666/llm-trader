import { useDeck } from "../store"
import { useContentWidth, RES } from "../layout"
import { fmtMoney, fmtPct } from "../format"
import { T, SEPARATOR, sciPulse } from "../theme"
import { Sparkline } from "../components/Sparkline"
import { BrailleChart } from "../components/BrailleChart"
import { SectionPanel, StatRow } from "../components/SectionPanel"
import { ProgressBar } from "../components/ProgressBar"
import { Badge, StatusBadge } from "../components/Badge"
import { DataTable, type DataColumn } from "../components/DataTable"
import { MetaLearningTracker } from "../components/MetaLearningTracker"

function CockpitPulseText(props: { rising: boolean; hasData: boolean; bars: number }) {
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0
  return (
    <text fg={T.dim}>
      {props.hasData
        ? `equity ${props.bars} bars · ${props.rising ? "▲ rising" : "▼ falling"} · Doherty 400ms pulse ${sciPulse(pulse, "●", "○")}`
        : `awaiting trades · capital intact · pulse ${sciPulse(pulse, "●", "○")}`}
    </text>
  )
}

export function CockpitScreen() {
  const acct = useDeck((s) => s.account)
  const positions = useDeck((s) => s.positions)
  const market = useDeck((s) => s.market)
  const symbols = useDeck((s) => s.symbols)
  const quant = useDeck((s) => s.quantMonitor)
  const mode = useDeck((s) => s.mode)
  const halted = useDeck((s) => s.halted)
  const transport = useDeck((s) => s.transport)
  const tasks = useDeck((s) => s.tasks)
  const loop = useDeck((s) => s.loopStats)
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0
  const runningTask = tasks.find((t) => t.status === "running")
  const dagPipeline = tasks.length > 0
    ? tasks.slice(-7).map((t) => `${t.status === "running" ? (pulse ? "▶" : "▷") : t.status === "done" ? "✔" : t.status === "error" ? "✘" : "○"} ${t.stage.slice(0, 10)}`).join(" ─▶ ")
    : "○ tech_net ─▶ ○ macro ─▶ ○ memory ─▶ ○ market ─▶ ○ arbitrate ─▶ ○ debate ─▶ ○ risk ─▶ ○ broker"


  // ── measured geometry: table width derives from live terminal width ──
  // scrollbox padding L/R 1 (2) − scrollbar reserve (RES) − SectionPanel
  // border 2 + padding 2 = the same budget Orchestration uses for full-width panels.
  const contentW = useContentWidth()
  const boxAvail = Math.max(20, contentW - 2 - RES - 4)

  const curve = acct.equityCurve
  const first = curve[0] ?? acct.equity
  const last = curve[curve.length - 1] ?? acct.equity
  const rising = last >= first
  const pnl = acct.equity - acct.dayStartEquity
  const pnlPct = acct.dayStartEquity > 0 ? (pnl / acct.dayStartEquity) * 100 : 0

  // Serial Position #9 essentials first: balance, equity, mode
  // Von Restorff #7 primary action highlighted
  const primaryActionLabel = halted ? "✕ HALTED — AWAIT RECOVERY" : positions.length > 0 ? `◉ ${positions.length} OPEN · MANAGE RISK` : "◇ PAPER · AWAIT SIGNAL"
  const primaryColor = halted ? T.critical : positions.length > 0 ? T.warn : T.teal

  type SymbolRow = { symbol: string; price: string; change: string; dom: string; spark: number[]; up: boolean }
  const symbolRows: SymbolRow[] = symbols
    .map((sym) => {
      const q = market[sym]
      if (!q) return null
      return {
        symbol: sym,
        price: `$${q.price?.toFixed(q.price > 100 ? 2 : 5) ?? "—"}`,
        change: fmtPct(q.changePct ?? 0),
        dom: `${((q.domImbalance ?? 0) * 100).toFixed(0)}%`,
        spark: q.history ?? [],
        up: (q.changePct ?? 0) >= 0,
      }
    })
    .filter(Boolean) as SymbolRow[]

  const symbolCols: DataColumn<SymbolRow>[] = [
    { key: "symbol", label: "SYMBOL", width: 9, render: (r) => r.symbol, color: () => T.bright },
    { key: "price", label: "PRICE", width: 12, align: "right", render: (r) => r.price },
    { key: "change", label: "24H", width: 9, align: "right", render: (r) => r.change, color: (r) => (r.up ? T.up : T.down) },
    { key: "dom", label: "DOM", width: 8, align: "right", render: (r) => r.dom, color: (r) => (Math.abs(parseInt(r.dom)) > 20 ? T.warn : T.dim) },
    { key: "spark", label: "30BARS", width: 16, render: (r) => r.spark.length > 1 ? r.spark.slice(-16).map((v: number, i: number) => (i % 2 === 0 ? "▁" : "▂")).join("") : "—" },
  ]

  return (
    <scrollbox
      flexGrow={1}
      paddingLeft={1}
      paddingRight={1}
      viewportCulling
      scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
    >
      <text fg={T.cyan}>╔══ COCKPIT · SYSTEM STATUS ═════════════════════════════════════╗</text>

      {/* Serial Position #9 essentials first + Proximity #4 group + Von Restorff #7 primary */}
      <SectionPanel title="◈ PRIMARY · ACCOUNT & MODE" titleColor={T.cyan} borderColor={T.cyan} borderStyle="single" padding={1}>
        <box flexDirection="row" gap={2}>
          <box flexDirection="column" width="60%">
            <box flexDirection="row" gap={2}>
              <text fg={T.bright}>{fmtMoney(acct.equity)}</text>
              <text fg={pnl >= 0 ? T.up : T.down}>{fmtPct(pnlPct)}</text>
              <Badge label={mode} color={mode === "LIVE" ? T.down : mode === "PAPER" ? T.teal : T.dim} icon={mode === "LIVE" ? "◉" : "◇"} />
              <Badge label={transport.toUpperCase()} color={transport === "live" ? T.up : T.warn} />
            </box>
            <text fg={T.muted}>balance {fmtMoney(acct.balance)} · peak {fmtMoney(acct.peakEquity)} · {acct.totalTrades} trades · {positions.length} open</text>
            <box flexDirection="row" gap={1}>
              <text fg={primaryColor} bg={T.raised}> {primaryActionLabel} </text>
              <text fg={T.dim}>— next action visually obvious (Von Restorff #7)</text>
            </box>
          </box>
          <box flexDirection="column" width="40%">
            <BrailleChart
              data={curve.slice(-60)}
              width={Math.max(26, Math.min(46, Math.round(boxAvail * 0.4)))}
              height={4}
              color={rising ? T.up : T.down}
              fill
              formatY={(v) => fmtMoney(v, 0)}
            />
            <CockpitPulseText rising={rising} hasData={curve.length > 0} bars={curve.length} />
          </box>
        </box>
      </SectionPanel>

      {/* Miller #5 chunk + Prägnanz simplest + ProgressBar large target Fitts #2 */}
      <box flexDirection="row" gap={1}>
        <SectionPanel title="◈ DRAWDOWN" titleColor={T.warn} borderStyle="single" borderColor={T.border} width="50%" padding={1}>
          <ProgressBar value={Math.min(1, acct.drawdownPct / 6)} width={22} color={acct.drawdownPct > 4 ? T.critical : acct.drawdownPct > 2 ? T.warn : T.teal} label="DD 6% breaker" showPct />
          <text fg={T.muted}>-{acct.drawdownPct.toFixed(2)}% of peak · circuit L1 3% L2 6% L3 halt</text>
          {acct.drawdownPct > 4 && <text fg={T.critical}>⛔ CRITICAL — PortfolioRiskArbiter will block new risk (Postel #14 prevent error)</text>}
        </SectionPanel>
        <SectionPanel title="◈ PORTFOLIO HEAT" titleColor={T.teal} borderStyle="single" borderColor={T.border} width="50%" padding={1}>
          <ProgressBar value={acct.heatUsedR / acct.heatCapR} width={22} color={acct.heatUsedR > 4 ? T.warn : T.teal} label={`HEAT ${acct.heatCapR.toFixed(0)}R cap`} showPct />
          <text fg={T.muted}>{acct.heatUsedR.toFixed(1)}R / {acct.heatCapR.toFixed(0)}R budget · {acct.openRiskPct.toFixed(2)}% risk</text>
        </SectionPanel>
      </box>

      {/* Multi-Agent Autonomous Execution DAG — live pipeline telemetry */}
      <SectionPanel
        title={`◈ MULTI-AGENT EXECUTION PIPELINE ${runningTask ? `· ▶ LIVE: ${runningTask.stage.toUpperCase()}` : "· STANDBY"}`}
        titleColor={runningTask ? T.cyan : T.teal}
        borderColor={runningTask ? T.cyan : T.border}
        borderStyle="single"
        padding={1}
      >
        <box flexDirection="column">
          <text fg={runningTask ? (pulse ? T.cyan : T.warn) : T.muted}>
            {dagPipeline}
          </text>
          <box flexDirection="row" gap={2} marginTop={1}>
            <text fg={runningTask ? T.bright : T.dim}>
              {runningTask ? `Active: ${runningTask.agent || "SYSTEM"} (${runningTask.durationMs ? `${runningTask.durationMs}ms` : "running..."})` : `Loop: ${loop.cyclesCompleted} completed · avg ${(loop.avgCycleMs / 1000).toFixed(1)}s`}
            </text>
            <text fg={T.borderBright}>|</text>
            <text fg={T.teal}>Deterministic Micro-Signals ─▶ LLM Deliberation ─▶ Risk Arbiter ─▶ Broker Execution</text>
          </box>
        </box>
      </SectionPanel>

      {/* Quant monitor immediate + Jakob familiar pattern + Uniform Connectedness */}
      <SectionPanel title="◈ QUANT GATES · LIVE" titleColor={quant.pbo != null || quant.dsr != null ? T.cyan : T.dim} borderStyle="single" borderColor={T.border} padding={1}>
        <box flexDirection="row" gap={2}>
          <text fg={quant.pbo != null && quant.pbo < 0.25 ? T.up : quant.pbo != null ? T.down : T.dim}>PBO {quant.pbo != null ? quant.pbo.toFixed(3) : "—"} {quant.pbo != null && quant.pbo < 0.25 ? "PASS" : quant.pbo != null ? "HOLD" : "STANDBY"}</text>
          <text fg={quant.dsr != null && quant.dsr > 0.7 ? T.up : quant.dsr != null ? T.down : T.dim}>DSR {quant.dsr != null ? quant.dsr.toFixed(2) : "—"} {quant.dsr != null && quant.dsr > 0.7 ? "PASS" : quant.dsr != null ? "HOLD" : "STANDBY"}</text>
          <text fg={quant.bocpd_prob != null && quant.bocpd_prob > 0.15 ? T.warn : T.dim}>BOCPD {quant.bocpd_prob != null ? quant.bocpd_prob.toFixed(3) : "—"}</text>
          <text fg={quant.kalman_z != null && Math.abs(quant.kalman_z) > 2 ? T.warn : T.dim}>K-Z {quant.kalman_z != null ? quant.kalman_z.toFixed(2) : "—"}</text>
          <text fg={quant.tca_spread_bps != null ? T.muted : T.dim}>TCA {quant.tca_spread_bps != null ? `${quant.tca_spread_bps.toFixed(0)}bps` : "—"}</text>
        </box>
        <text fg={T.dim}>CPCV 6-fold purge 1% embargo · BOCPD hazard 30 threshold 0.15 · connected to Orchestration #17 via ORCH view [9]</text>
      </SectionPanel>

      {/* Familiar pattern Jakob #3 DataTable + Fitts large targets + Proximity */}
      <SectionPanel title={`◈ MARKET · ${symbols.length} WATCHLIST`} titleColor={T.cyan} borderStyle="single" borderColor={T.border} padding={1}>
        {symbolRows.length > 0 ? (
          <DataTable columns={symbolCols} data={symbolRows} headerColor={T.dim} width={boxAvail} />
        ) : (
          <text fg={T.dim}>no market ticks yet — check MT5 view [8] or transport live (Tesler #19 progressive: advanced DOM next)</text>
        )}
        <text fg={T.dim}>Law of Proximity #4: price/change/DOM grouped · Uniform Connectedness #17: symbol → click → Positions [3]</text>
      </SectionPanel>

      {/* Session ledger grouped + sensible defaults Hick #13 */}
      <SectionPanel title="◈ SESSION LEDGER" titleColor={T.teal} borderStyle="single" borderColor={T.border} padding={1}>
        <StatRow label="open positions" value={String(positions.length)} color={T.fg} />
        <StatRow label="open risk" value={`${acct.openRiskPct.toFixed(2)}%`} color={positions.length > 0 ? T.warn : T.muted} />
        <StatRow label="win rate" value={`${acct.winRate.toFixed(1)}%`} color={acct.winRate >= 50 ? T.teal : T.warn} />
        <StatRow label="consecutive losses" value={String(acct.consecutiveLosses)} color={acct.consecutiveLosses > 3 ? T.down : T.muted} />
        <StatRow label="peak equity" value={fmtMoney(acct.peakEquity)} color={T.dim} />
        <text fg={T.dim}>Sensible defaults #13: risk_per_trade 0.01 capped 0.01 · safe min 0.002 · Goal-Gradient #20: progress to flatten visible</text>
      </SectionPanel>

      {/* Progressive disclosure Tesler #19 + Miller chunk */}
      <SectionPanel title="◈ META LEARNING · PROGRESS" titleColor={T.purple} borderStyle="single" borderColor={T.border} padding={1}>
        <MetaLearningTracker compact />
        <text fg={T.dim}>Reveal complexity gradually #19: essentials above, detail in ORCH [9] · shows all data/workings including heuristic flags</text>
      </SectionPanel>

      <text fg={T.cyan}>╚══════════════════════════════════════════════════════════════╝</text>
    </scrollbox>
  )
}

