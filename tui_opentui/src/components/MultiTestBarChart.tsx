import { useState } from "react"
import { T } from "../theme"

// Contract with the screen: scores are null until a real engine/API source
// reports them. This chart NEVER invents numbers — phases without a source
// render as empty bars with explicit "NO DATA".
export interface ComponentTestRecord {
  id: string
  name: string
  kind: "STRAT" | "ALPHA" | "AGENT" | "MODEL"
  category: string
  syntheticScore: number | null
  historicalScore: number | null
  oosScore: number | null
  brokerReplayScore: number | null
  forwardScore: number | null
  sharpe: number | null
  evR: number | null
  maxDd: number | null
  stabilityGrade: "A" | "B" | "C" | "D" | "F"
  generalizationGap: number | null
  lastRetrained: string
  nextRevalidation: string
  trainingWindow?: string
  retrainingCadence?: string
  winRate?: string
  profitFactor?: number
  syntheticPermutations?: string
  syntheticDetail?: string
  cpcvDetail?: string
  oosDetail?: string
  replayDetail?: string
  forwardDetail?: string
  status: "HEALTHY" | "VALIDATING" | "DECAYING" | "FROZEN" | "RETIRED"
}

export interface MultiTestBarChartProps {
  components: ComponentTestRecord[]
  selectedId: string
  onSelect: (id: string) => void
  activeTestPhase: "ALL" | "SYNTHETIC" | "CPCV" | "OOS" | "REPLAY" | "FORWARD"
  compactMode?: boolean
}

const PHASE_COLORS: Record<string, string> = {
  SYNTHETIC: T.cyan,
  CPCV: T.purple,
  OOS: T.orange,
  REPLAY: T.teal,
  FORWARD: T.up,
}

const KIND_COLORS: Record<string, string> = {
  STRAT: T.purple,
  ALPHA: T.teal,
  AGENT: T.cyan,
  MODEL: T.orange,
}

const STATUS_COLORS: Record<string, string> = {
  HEALTHY: T.up,
  VALIDATING: T.cyan,
  DECAYING: T.warn,
  FROZEN: T.down,
  RETIRED: T.dim,
}

export function MultiTestBarChart(props: MultiTestBarChartProps) {
  const { components, selectedId, onSelect, activeTestPhase, compactMode } = props
  // Expanded drawer dynamically follows selectedId so navigating with up/down arrows immediately opens the full 5-stage telemetry for the active strategy
  const [collapsedId, setCollapsedId] = useState<string | null>(null)

  const handleToggle = (id: string) => {
    onSelect(id)
    setCollapsedId(collapsedId === id ? null : id)
  }

  return (
    <box flexDirection="column" flexGrow={1}>
      {components.map((c) => {
        const isSelected = c.id === selectedId
        const isExpanded = !compactMode && isSelected && collapsedId !== c.id

        // Determine which score to show based on activeTestPhase.
        // null score = no real source reported this phase → honest empty bar.
        let score: number | null = c.historicalScore
        let phaseLabel = "CPCV"
        let barColor: string = T.purple

        if (activeTestPhase === "SYNTHETIC") {
          score = c.syntheticScore
          phaseLabel = "SYNTH"
          barColor = T.cyan
        } else if (activeTestPhase === "OOS") {
          score = c.oosScore
          phaseLabel = "OOS"
          barColor = T.orange
        } else if (activeTestPhase === "REPLAY") {
          score = c.brokerReplayScore
          phaseLabel = "REPLAY"
          barColor = T.teal
        } else if (activeTestPhase === "FORWARD") {
          score = c.forwardScore
          phaseLabel = "LIVE"
          barColor = T.up
        }

        const hasScore = score != null

        // Calculate visual bar width (max 48 chars); no data → empty track
        const maxBarWidth = 46
        const filledWidth = hasScore ? Math.max(4, Math.round((score! / 100) * maxBarWidth)) : 0
        const remainingWidth = Math.max(0, maxBarWidth - filledWidth)

        // Text overlay on the bar (no invented EV — show "—" when null)
        const evText = c.evR != null ? `EV +${c.evR.toFixed(2)}R` : "EV —"
        const overlayText = `[${c.kind}] ${c.name.padEnd(16)} ${hasScore ? `${score}%` : "NO DATA"} (${phaseLabel}) · ${evText}`
        const inBarText = hasScore ? overlayText.slice(0, filledWidth) : overlayText.slice(0, maxBarWidth)

        return (
          <box key={c.id} flexDirection="column" marginBottom={compactMode ? 0 : 1}>
            {/* Clickable Bar Row */}
            <box
              flexDirection="row"
              alignItems="center"
              backgroundColor={isSelected ? T.raised : undefined}
              onMouseDown={() => handleToggle(c.id)}
            >
              {/* Expand Indicator & Badge */}
              <text fg={isSelected ? T.bright : T.dim}>
                {isExpanded ? "▼" : "▶"}{" "}
              </text>
              <text fg={T.bg} bg={KIND_COLORS[c.kind] ?? T.dim}>
                {` ${c.kind} `}
              </text>
              <text fg={T.dim}> </text>

              {/* Text-Over-Block Colored Bar */}
              <box flexDirection="row" flexGrow={1}>
                {/* Colored background block with text overlay (only when a real score exists) */}
                {hasScore && (
                  <text fg={T.bg} bg={barColor}>
                    {` ${inBarText.padEnd(filledWidth - 1, " ")} `}
                  </text>
                )}
                {/* Remaining un-filled track */}
                <text fg={T.dim} bg={T.panel}>
                  {" ".repeat(remainingWidth)}
                </text>
                {!hasScore && (
                  <text fg={T.dim}> {inBarText}</text>
                )}
              </box>

              {/* Status Badge */}
              <text fg={STATUS_COLORS[c.status] ?? T.dim}>
                {` ${c.status.padEnd(10)} `}
              </text>
              <text fg={T.fg}>
                {c.sharpe != null ? `SR ${c.sharpe.toFixed(2)}` : "SR —"}
              </text>
            </box>

            {/* Expandable Drill-Down Details Drawer */}
            {isExpanded && (
              <box
                flexDirection="column"
                borderStyle="single"
                borderColor={barColor}
                backgroundColor={T.panel}
                marginLeft={2}
                marginTop={1}
                paddingLeft={1}
                paddingRight={1}
              >
                <box flexDirection="row" gap={2}>
                  <text fg={T.bright}>
                    {c.name}
                  </text>
                  <text fg={T.dim}>{`Category: ${c.category.slice(0, 24)}`}</text>
                  <text fg={T.cyan}>{`Stability: Grade ${c.stabilityGrade}`}</text>
                  <text fg={c.generalizationGap != null && c.generalizationGap < 15 ? T.up : T.warn}>
                    {c.generalizationGap != null ? `Gen Gap: ${c.generalizationGap.toFixed(1)}%` : "Gen Gap: —"}
                  </text>
                </box>

                <box borderStyle="single" borderColor={T.border} height={1} />

                {/* 5-Stage Multi-Test Progression - Nested List of Text-Over-Block Colored Bars */}
                <box flexDirection="column" marginTop={1} marginBottom={1}>
                  <text fg={T.bright} marginBottom={1}>
                    MULTI-TEST CONTINUUM (5 TEST PHASES WITH TEXT-OVER-BLOCK COLOR BARS):
                  </text>

                  {/* 1. Synthetic Stress Bar */}
                  <box flexDirection="row" alignItems="center" marginBottom={1}>
                    <text fg={T.cyan} width={14}>
                      1. SYNTHETIC:
                    </text>
                    {c.syntheticScore != null ? (
                      <>
                        <box flexDirection="row" flexGrow={1}>
                          <text fg={T.bg} bg={T.cyan}>
                            {` ${(c.syntheticDetail ?? `SYNTHETIC STRESS · ${c.syntheticScore}% PASS`).slice(0, Math.max(4, Math.round((c.syntheticScore / 100) * 44)))
                              .padEnd(Math.max(4, Math.round((c.syntheticScore / 100) * 44)) - 1, " ")} `}
                          </text>
                          <text fg={T.dim} bg={T.panel}>
                            {" ".repeat(Math.max(0, 44 - Math.round((c.syntheticScore / 100) * 44)))}
                          </text>
                        </box>
                        <text fg={c.syntheticScore >= 70 ? T.up : T.down} marginLeft={1}>
                          {` ${c.syntheticScore}% PASS `}
                        </text>
                      </>
                    ) : (
                      <text fg={T.dim}>no synthetic-stress result reported by engine/API</text>
                    )}
                  </box>

                  {/* 2. CPCV In-Sample Bar */}
                  <box flexDirection="row" alignItems="center" marginBottom={1}>
                    <text fg={T.purple} width={14}>
                      2. CPCV:
                    </text>
                    {c.historicalScore != null ? (
                      <>
                        <box flexDirection="row" flexGrow={1}>
                          <text fg={T.bg} bg={T.purple}>
                            {` ${(c.cpcvDetail ?? `PURGED CPCV · ${c.historicalScore}% PASS`).slice(0, Math.max(4, Math.round((c.historicalScore / 100) * 44)))
                              .padEnd(Math.max(4, Math.round((c.historicalScore / 100) * 44)) - 1, " ")} `}
                          </text>
                          <text fg={T.dim} bg={T.panel}>
                            {" ".repeat(Math.max(0, 44 - Math.round((c.historicalScore / 100) * 44)))}
                          </text>
                        </box>
                        <text fg={c.historicalScore >= 70 ? T.up : T.down} marginLeft={1}>
                          {` ${c.historicalScore}% PASS `}
                        </text>
                      </>
                    ) : (
                      <text fg={T.dim}>no CPCV result reported by engine/API</text>
                    )}
                  </box>

                  {/* 3. OOS Embargo Bar */}
                  <box flexDirection="row" alignItems="center" marginBottom={1}>
                    <text fg={T.orange} width={14}>
                      3. OOS EMBARGO:
                    </text>
                    {c.oosScore != null ? (
                      <>
                        <box flexDirection="row" flexGrow={1}>
                          <text fg={T.bg} bg={T.orange}>
                            {` ${(c.oosDetail ?? `OOS EMBARGO · ${c.oosScore}% PASS`).slice(0, Math.max(4, Math.round((c.oosScore / 100) * 44)))
                              .padEnd(Math.max(4, Math.round((c.oosScore / 100) * 44)) - 1, " ")} `}
                          </text>
                          <text fg={T.dim} bg={T.panel}>
                            {" ".repeat(Math.max(0, 44 - Math.round((c.oosScore / 100) * 44)))}
                          </text>
                        </box>
                        <text fg={c.oosScore >= 70 ? T.up : T.down} marginLeft={1}>
                          {` ${c.oosScore}% PASS `}
                        </text>
                      </>
                    ) : (
                      <text fg={T.dim}>no OOS result reported by engine/API</text>
                    )}
                  </box>

                  {/* 4. Broker Replay Bar */}
                  <box flexDirection="row" alignItems="center" marginBottom={1}>
                    <text fg={T.teal} width={14}>
                      4. REPLAY:
                    </text>
                    {c.brokerReplayScore != null ? (
                      <>
                        <box flexDirection="row" flexGrow={1}>
                          <text fg={T.bg} bg={T.teal}>
                            {` ${(c.replayDetail ?? `BROKER REPLAY · ${c.brokerReplayScore}% PASS`).slice(0, Math.max(4, Math.round((c.brokerReplayScore / 100) * 44)))
                              .padEnd(Math.max(4, Math.round((c.brokerReplayScore / 100) * 44)) - 1, " ")} `}
                          </text>
                          <text fg={T.dim} bg={T.panel}>
                            {" ".repeat(Math.max(0, 44 - Math.round((c.brokerReplayScore / 100) * 44)))}
                          </text>
                        </box>
                        <text fg={c.brokerReplayScore >= 70 ? T.up : T.down} marginLeft={1}>
                          {` ${c.brokerReplayScore}% PASS `}
                        </text>
                      </>
                    ) : (
                      <text fg={T.dim}>no broker-replay TCA reported by engine/API</text>
                    )}
                  </box>

                  {/* 5. Live Forward Bar */}
                  <box flexDirection="row" alignItems="center">
                    <text fg={T.up} width={14}>
                      5. FORWARD LIVE:
                    </text>
                    {c.forwardScore != null ? (
                      <>
                        <box flexDirection="row" flexGrow={1}>
                          <text fg={T.bg} bg={T.up}>
                            {` ${(c.forwardDetail ?? `FORWARD · ${c.forwardScore}% PASS`).slice(0, Math.max(4, Math.round((c.forwardScore / 100) * 44)))
                              .padEnd(Math.max(4, Math.round((c.forwardScore / 100) * 44)) - 1, " ")} `}
                          </text>
                          <text fg={T.dim} bg={T.panel}>
                            {" ".repeat(Math.max(0, 44 - Math.round((c.forwardScore / 100) * 44)))}
                          </text>
                        </box>
                        <text fg={c.forwardScore >= 70 ? T.up : T.down} marginLeft={1}>
                          {` ${c.forwardScore}% PASS `}
                        </text>
                      </>
                    ) : (
                      <text fg={T.dim}>no forward/journal metrics reported yet</text>
                    )}
                  </box>
                </box>

                {/* Continuous Backtesting & Retraining Telemetry */}
                <box flexDirection="row" gap={2} marginTop={1}>
                  <text fg={T.muted}>{`Retrained: ${c.lastRetrained.slice(0, 16)}`}</text>
                  {c.retrainingCadence && <text fg={T.cyan}>{`Cadence: ${c.retrainingCadence.slice(0, 18)}`}</text>}
                  <text fg={T.muted}>{`Next: ${c.nextRevalidation.slice(0, 18)}`}</text>
                </box>
                <box flexDirection="row" gap={2}>
                  {c.trainingWindow && <text fg={T.dim}>{`Window: ${c.trainingWindow.slice(0, 18)}`}</text>}
                  <text fg={T.up}>{`Win: ${c.winRate ?? "—"}`}</text>
                  <text fg={T.teal}>{`PF: ${c.profitFactor != null ? c.profitFactor.toFixed(2) : "—"}`}</text>
                  {c.syntheticPermutations && <text fg={T.dim}>{`Seeds: ${c.syntheticPermutations.slice(0, 14)}`}</text>}
                </box>
              </box>
            )}
          </box>
        )
      })}
    </box>
  )
}
