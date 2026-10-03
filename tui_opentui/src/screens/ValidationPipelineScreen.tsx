import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock, bar, fmtNum } from "../format"
import type { ValidationPipelineEntry, CPCVSplit } from "../types"

const STAGE_ICONS: Record<string, string> = {
  complete: "✓",
  running: "●",
  queued: "○",
  failed: "✗",
}

const STAGE_COLORS: Record<string, string> = {
  complete: T.up,
  running: T.cyan,
  queued: T.dim,
  failed: T.down,
}

const STATUS_COLOR: Record<string, string> = {
  passing: T.up,
  failing: T.down,
  running: T.cyan,
}

export function validationMove(delta: number): void {
  const n = store.get().validationPipeline.length
  if (n === 0) return
  store.set({ selectedValidation: Math.max(0, Math.min(n - 1, store.get().selectedValidation + delta)) })
}

function CPCVSplitRow(props: { split: CPCVSplit }) {
  const { split } = props
  return (
    <box flexDirection="row" gap={1}>
      <text fg={T.dim}>S{split.split}</text>
      <text fg={split.trainSharpe > 1 ? T.up : T.dim}>{split.trainSharpe.toFixed(2)}</text>
      <text fg={T.muted}>→</text>
      <text fg={split.testSharpe > 1 ? T.up : split.testSharpe < 0 ? T.down : T.dim}>{split.testSharpe.toFixed(2)}</text>
      <text fg={split.overfitRatio > 0.4 ? T.down : T.dim}>{(split.overfitRatio * 100).toFixed(0)}%</text>
      <text fg={split.passed ? T.up : T.down}>{split.passed ? "PASS" : "FAIL"}</text>
    </box>
  )
}

export function ValidationPipelineScreen() {
  const pipelines = useDeck((s) => s.validationPipeline)
  const selected = Math.min(useDeck((s) => s.selectedValidation), Math.max(0, pipelines.length - 1))
  const p = pipelines[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" VALIDATION PIPELINE " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" width={32} flexShrink={0} paddingLeft={1}>
        <box flexDirection="row">
          <text fg={T.dim}>{"STRATEGY".padEnd(16)}{"SYM".padEnd(7)}{"STATUS"}</text>
        </box>
        {pipelines.length === 0 && <text fg={T.dim}>no validation pipelines</text>}
        {pipelines.map((x, i) => {
          const active = i === selected
          return (
            <box key={x.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
              <text fg={active ? T.bright : T.fg}>{`${active ? "▸" : " "}${x.strategyName.slice(0, 15)}`.padEnd(16)}</text>
              <text fg={T.muted}>{x.symbol.padEnd(7)}</text>
              <text fg={STATUS_COLOR[x.overallStatus]}>{x.overallStatus}</text>
            </box>
          )
        })}
        <text fg={T.borderBright}>────────────────────────</text>
        <text fg={T.dim}>↑↓ select · detail right</text>
        <text fg={T.borderBright}>────────────────────────</text>
        <text fg={T.dim}>VALIDATION STAGES:</text>
        <text fg={T.muted}>1. Data Ingestion</text>
        <text fg={T.muted}>2. Feature Engineering</text>
        <text fg={T.muted}>3. CPCV Backtest</text>
        <text fg={T.muted}>4. Walk-Forward</text>
        <text fg={T.muted}>5. Monte Carlo</text>
        <text fg={T.muted}>6. Deflated Sharpe</text>
        <text fg={T.muted}>7. PBO Check</text>
        <text fg={T.muted}>8. Risk Gate</text>
      </box>

      <box title={p ? ` ${p.strategyName} — ${p.symbol} ` : " — "} titleColor={p ? STATUS_COLOR[p.overallStatus] : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {p ? (
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STATUS_COLOR[p.overallStatus]}> {p.overallStatus.toUpperCase()} </text>
              <text fg={T.fg}>{p.strategyName}</text>
              <text fg={T.dim}>{fmtClock(p.startedTs)}</text>
            </box>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>PIPELINE STAGES ({p.stages.length})</text>
            <text fg={T.borderBright}> </text>
            {p.stages.map((stage, si) => (
              <box key={si} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={STAGE_COLORS[stage.status]}>{STAGE_ICONS[stage.status]}</text>
                  <text fg={stage.status === "complete" ? T.fg : T.muted}>{stage.name}</text>
                  {stage.completedTs && stage.startedTs && (
                    <text fg={T.dim}>{stage.completedTs - stage.startedTs}ms</text>
                  )}
                </box>
                {stage.metrics && Object.keys(stage.metrics).length > 0 && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.dim}>{Object.entries(stage.metrics).map(([k, v]) => `${k}=${typeof v === "number" ? fmtNum(v, 3) : v}`).join("  ")}</text>
                  </box>
                )}
                {stage.notes && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.warn}>{stage.notes}</text>
                  </box>
                )}
                {si < p.stages.length - 1 && <text fg={T.borderBright}>  │</text>}
              </box>
            ))}
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>CPCV SPLITS ({p.cpcvSplits.length})</text>
            <box flexDirection="row" gap={1} paddingLeft={1}>
              <text fg={T.dim}>{"#".padEnd(4)}{"TRAIN".padEnd(8)}{"TEST".padEnd(8)}{"GAP".padEnd(7)}</text>
            </box>
            {p.cpcvSplits.map((split, i) => (
              <CPCVSplitRow key={i} split={split} />
            ))}
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>OVERFIT ASSESSMENT</text>
            <box flexDirection="row"><text fg={T.muted}>{"deflated sharpe:".padEnd(20)}</text><text fg={p.deflatedSharpe > 1 ? T.up : T.down}>{fmtNum(p.deflatedSharpe, 2)}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"PBO:".padEnd(20)}</text><text fg={p.pbo < 0.5 ? T.up : T.down}>{fmtNum(p.pbo, 3)}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"overfit risk:".padEnd(20)}</text><text fg={T.muted}>{bar(p.pbo, 20)}</text></box>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no validation pipeline selected</text>
        )}
      </box>
    </box>
  )
}
