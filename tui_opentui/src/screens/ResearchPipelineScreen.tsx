import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import { clip, useContentWidth } from "../layout"
import type { ResearchPipelineEntry, ResearchStage } from "../types"

const STAGE_ORDER: ResearchStage[] = [
  "INGEST", "EXTRACT", "VALIDATE", "BACKTEST", "CPCV",
  "WALK_FORWARD", "MONTE_CARLO", "PAPER", "PROMOTE", "DEPLOY",
]

const STAGE_COLOR: Record<ResearchStage, string> = {
  INGEST: T.muted,
  EXTRACT: T.warn,
  VALIDATE: T.cyan,
  BACKTEST: T.teal,
  CPCV: T.purple,
  WALK_FORWARD: T.orange,
  MONTE_CARLO: T.warn,
  PAPER: T.up,
  PROMOTE: T.bright,
  DEPLOY: T.up,
}

const STAGE_DESC: Record<ResearchStage, string> = {
  INGEST: "Import from ForexFactory, FRED, CFTC, SSRN, articles",
  EXTRACT: "Parse, deduplicate, extract signals & hypotheses",
  VALIDATE: "Source policy, statistical significance, edge lifecycle",
  BACKTEST: "VBT vectorized, parameter sweeps, overfitting checks",
  CPCV: "Combinatorial Purged CV, Deflated Sharpe, PBO",
  WALK_FORWARD: "Out-of-sample rolling window validation",
  MONTE_CARLO: "Bootstrap simulation, confidence intervals",
  PAPER: "Live paper shadow, reconciliation, forward test",
  PROMOTE: "Promotion package: gates evidence aggregate",
  DEPLOY: "Code fingerprint, registry, lifecycle transition",
}

export function researchMove(delta: number): void {
  const n = store.get().researchPipeline.length
  if (n === 0) return
  store.set({ selectedResearch: Math.max(0, Math.min(n - 1, store.get().selectedResearch + delta)) })
}

export function ResearchPipelineScreen() {
  const pipeline = useDeck((s) => s.researchPipeline)
  const winProb = useDeck((s) => s.winProbResult)
  const selected = Math.min(useDeck((s) => s.selectedResearch), Math.max(0, pipeline.length - 1))
  const entry = pipeline[selected]
  const currentStageIdx = entry ? STAGE_ORDER.indexOf(entry.stage) : -1
  const contentW = useContentWidth()

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" RESEARCH PIPELINE " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" width={44} flexShrink={0} paddingLeft={1}>
        <box flexDirection="row">
          <text fg={T.dim}>{"NAME".padEnd(20)}{"SOURCE".padEnd(12)}{"STAGE"}</text>
        </box>
        {pipeline.length === 0 && <text fg={T.dim}>no research entries</text>}
        {pipeline.map((x, i) => {
          const active = i === selected
          return (
            <box key={x.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
              <text fg={active ? T.bright : T.fg}>{`${active ? "▸" : " "}${x.name}`.padEnd(20).slice(0, 20)}</text>
              <text fg={T.muted}>{clip(x.source, 12)}</text>
              <text fg={STAGE_COLOR[x.stage]}>{x.stage}</text>
            </box>
          )
        })}
        <text fg={T.borderBright}>──────────────────────────────────────────────</text>
        <text fg={T.dim}>PIPELINE STAGES (10)</text>
        {STAGE_ORDER.map((s, i) => (
          <text key={s} fg={i <= currentStageIdx ? STAGE_COLOR[s] : T.dim}>
            {`  ${i <= currentStageIdx ? "●" : "○"} ${i + 1}. ${s}`}
          </text>
        ))}
        <text fg={T.borderBright}>──────────────────────────────────────────────</text>
        <text fg={T.dim}>↑↓ select · detail right</text>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title={entry ? ` ${entry.name} ` : " — "} titleColor={entry ? T.purple : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={2} paddingLeft={1}>
          {entry ? (
            <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
              <box flexDirection="row" gap={2}>
                <text fg={T.bg} bg={STAGE_COLOR[entry.stage]}> {entry.stage} </text>
                <text fg={T.muted}>source: {entry.source}</text>
                <text fg={T.dim}>{fmtClock(entry.startedTs)}</text>
              </box>
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>STAGE DESCRIPTION</text>
              <text fg={T.muted}>{STAGE_DESC[entry.stage]}</text>
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>STAGE PROGRESSION</text>
              {STAGE_ORDER.map((s, i) => {
                const isCurrent = s === entry.stage
                const isPast = i < currentStageIdx
                const isFuture = i > currentStageIdx
                return (
                  <box key={s} flexDirection="row" paddingLeft={2}>
                    <text fg={isPast ? T.up : isCurrent ? STAGE_COLOR[s] : T.dim}>
                      {isPast ? "✓" : isCurrent ? "▸" : "○"}
                    </text>
                    <text fg={isPast ? T.up : isCurrent ? T.bright : T.dim}> {s}</text>
                    {isCurrent && <text fg={T.warn}> ← current ({(entry.stageProgress * 100).toFixed(0)}%)</text>}
                    {isPast && <text fg={T.dim}> done</text>}
                    {isFuture && <text fg={T.dim}> pending</text>}
                  </box>
                )
              })}
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>METRICS</text>
              {Object.entries(entry.metrics).map(([k, v]) => (
                <text key={k} fg={T.muted}>  {k}: {typeof v === "number" ? v.toFixed(3) : v}</text>
              ))}
              {entry.notes && (
                <box flexDirection="column">
                  <text fg={T.dim}>NOTES</text>
                  <text fg={T.muted}>{clip(entry.notes, contentW - 8)}</text>
                </box>
              )}
            </scrollbox>
          ) : (
            <text fg={T.dim}>no research entry selected</text>
          )}
        </box>

        <box title=" WIN PROBABILITY MODEL " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {winProb ? (
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="row" gap={2}>
                <text fg={T.bg} bg={winProb.winProbability > 0.6 ? T.up : winProb.winProbability > 0.5 ? T.warn : T.down}>
                  {(winProb.winProbability * 100).toFixed(1)}%
                </text>
                <text fg={T.fg}>win probability</text>
                <text fg={T.dim}>conf {(winProb.confidence * 100).toFixed(0)}%</text>
                <text fg={T.muted}>Kelly {(winProb.kellyFraction * 100).toFixed(1)}%</text>
              </box>
              <box flexDirection="row" gap={2}>
                <text fg={T.dim}>recommendation:</text>
                <text fg={winProb.recommendation === "SKIP" ? T.down : winProb.recommendation === "QUARTER_SIZE" ? T.warn : T.up}>
                  {winProb.recommendation}
                </text>
              </box>
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>FEATURE INPUTS (L2 Logistic Regression)</text>
              {winProb.features.map((f) => (
                <box key={f.name} flexDirection="row" paddingLeft={2}>
                  <text fg={T.muted}>{f.name.padEnd(18)}</text>
                  <text fg={T.fg}>{f.value.toFixed(3)}</text>
                  <text fg={T.dim}> importance: {(f.importance * 100).toFixed(0)}%</text>
                  <text fg={T.borderBright}>{"█".repeat(Math.round(f.importance * 20))}</text>
                </box>
              ))}
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>CONSERVATIVE SCALING RULE</text>
              <text fg={T.muted}>  win_prob {">"} 0.60 {"→"} FULL_SIZE</text>
              <text fg={T.muted}>  win_prob 0.55-0.60 {"→"} HALF_SIZE</text>
              <text fg={T.muted}>  win_prob 0.50-0.55 {"→"} QUARTER_SIZE</text>
              <text fg={T.muted}>  win_prob {"<"} 0.50 {"→"} SKIP</text>
              <text fg={T.muted}>  consecutive_losses {">"} 3 {"→"} auto HALVE</text>
            </scrollbox>
          ) : (
            <text fg={T.dim}>win probability model not initialized</text>
          )}
        </box>
      </box>
    </box>
  )
}
