import { T } from "../theme"
import { ComponentTestRecord } from "./MultiTestBarChart"
import type { QuantMonitor } from "../types"

export interface ContinuousRetrainingMatrixProps {
  components: ComponentTestRecord[]
  selectedId: string
  onSelect: (id: string) => void
  quantMonitor?: QuantMonitor
}

export function ContinuousRetrainingMatrix(props: ContinuousRetrainingMatrixProps) {
  const { components, selectedId, onSelect, quantMonitor } = props

  if (components.length === 0) {
    return (
      <box flexDirection="column" flexGrow={1} justifyContent="center" alignItems="center">
        <text fg={T.warn}>◆ CONTINUOUS RETRAINING MATRIX — no engine/API data</text>
        <text fg={T.dim}>No strategy retraining telemetry has been reported by the engine or API yet.</text>
        <text fg={T.dim}>Rows appear here only once real retraining/validation data exists (no mock data is shown).</text>
      </box>
    )
  }

  return (
    <box flexDirection="column" flexGrow={1}>
      {/* Category Section Header */}
      <box
        flexDirection="row"
        justifyContent="space-between"
        backgroundColor={T.raised}
        paddingLeft={1}
        paddingRight={1}
        marginBottom={1}
      >
        <box flexDirection="row" gap={1}>
          <text fg={T.teal}>◆ CONTINUOUS RETRAINING MATRIX</text>
          <text fg={T.dim}>({components.length} Candidates)</text>
        </box>
        <box flexDirection="row" gap={2}>
          <text fg={quantMonitor?.kalman_z != null && Math.abs(quantMonitor.kalman_z) > 2.0 ? T.warn : T.dim}>
            {quantMonitor?.kalman_z != null
              ? `● Kalman: ${quantMonitor.kalman_z > 0 ? "+" : ""}${quantMonitor.kalman_z.toFixed(2)}σ`
              : "● Kalman: NO SIGNAL"}
          </text>
          <text fg={quantMonitor?.bocpd_prob != null && quantMonitor.bocpd_prob > 0.35 ? T.warn : T.dim}>
            {quantMonitor?.bocpd_prob != null
              ? `● Hazard: ${(quantMonitor.bocpd_prob * 100).toFixed(0)}%`
              : "● Hazard: NO SIGNAL"}
          </text>
          <text fg={T.bg} bg={quantMonitor?.kalman_z != null && Math.abs(quantMonitor.kalman_z) > 2.5 ? T.warn : T.dim}>
            {quantMonitor?.kalman_z != null && Math.abs(quantMonitor.kalman_z) > 2.5 ? " DRIFT TRIGGERED " : " DRIFT: NO SIGNAL "}
          </text>
        </box>
      </box>

      {/* Table Column Header */}
      <box
        flexDirection="row"
        backgroundColor={T.panel}
        paddingLeft={1}
        paddingRight={1}
        marginBottom={1}
      >
        <text fg={T.bright} width={18}>STRATEGY / ALPHA</text>
        <text fg={T.dim} width={8}>KIND</text>
        <text fg={T.dim} width={18}>TRAINING WINDOW</text>
        <text fg={T.cyan} width={18}>CADENCE</text>
        <text fg={T.muted} width={16}>NEXT REVAL</text>
        <text fg={T.bright} width={11}>GEN GAP</text>
        <text fg={T.up} width={12}>WIN / PF</text>
        <text fg={T.dim}>STATUS</text>
      </box>

      {/* Rows for all components */}
      <scrollbox flexGrow={1} viewportCulling>
        {components.map((c) => {
          const isSelected = c.id === selectedId
          return (
            <box
              key={c.id}
              flexDirection="column"
              backgroundColor={isSelected ? T.raised : undefined}
              paddingLeft={1}
              paddingRight={1}
              marginBottom={1}
              onMouseDown={() => onSelect(c.id)}
            >
              <box flexDirection="row" alignItems="center">
                {/* Selector & Name */}
                <text fg={isSelected ? T.teal : T.dim}>
                  {isSelected ? "▶ " : "  "}
                </text>
                <text fg={isSelected ? T.bright : T.fg} width={16}>
                  {c.name.slice(0, 15).padEnd(16)}
                </text>

                {/* Kind */}
                <text fg={T.bg} bg={c.kind === "STRAT" ? T.purple : c.kind === "ALPHA" ? T.teal : T.cyan}>
                  {` ${c.kind} `}
                </text>
                <text fg={T.dim} width={2}> </text>

                {/* Training Window (only when engine/API reported one) */}
                <text fg={T.dim} width={18}>
                  {(c.trainingWindow ?? "—").slice(0, 17).padEnd(18)}
                </text>

                {/* Retraining Cadence (only when reported) */}
                <text fg={T.cyan} width={18}>
                  {(c.retrainingCadence ?? "—").slice(0, 17).padEnd(18)}
                </text>

                {/* Next Revalidation */}
                <text fg={T.muted} width={16}>
                  {c.nextRevalidation.slice(0, 15).padEnd(16)}
                </text>

                {/* Gen Gap (null = not measured) */}
                <text fg={T.dim} width={11}>
                  {c.generalizationGap != null
                    ? `${c.generalizationGap.toFixed(1)}% ${c.generalizationGap < 15 ? "OK" : "!!"}`.padEnd(11)
                    : "—".padEnd(11)}
                </text>

                {/* Win / PF (null = not reported) */}
                <text fg={T.fg} width={12}>
                  {`${c.winRate ?? "—"} / ${c.profitFactor != null ? c.profitFactor.toFixed(1) : "—"}`.padEnd(12)}
                </text>

                {/* Status */}
                <text
                  fg={
                    c.status === "HEALTHY"
                      ? T.up
                      : c.status === "VALIDATING"
                      ? T.cyan
                      : T.warn
                  }
                >
                  {c.status}
                </text>
              </box>

              {/* Expanded Sub-Row showing 5-Phase continuum status (null = NO DATA, never fabricated) */}
              {isSelected && (
                <box
                  flexDirection="row"
                  gap={2}
                  marginTop={1}
                  marginBottom={1}
                  paddingLeft={2}
                  backgroundColor={T.panel}
                  borderStyle="single"
                  borderColor={T.border}
                >
                  <text fg={T.dim}>5-Phase Continuum:</text>
                  <text fg={c.syntheticScore != null ? (c.syntheticScore >= 70 ? T.cyan : T.down) : T.dim}>
                    {c.syntheticScore != null ? `[1. SYNTH: ${c.syntheticScore}%]` : "[1. SYNTH: NO DATA]"}
                  </text>
                  <text fg={c.historicalScore != null ? (c.historicalScore >= 70 ? T.purple : T.down) : T.dim}>
                    {c.historicalScore != null ? `[2. CPCV: ${c.historicalScore}%]` : "[2. CPCV: NO DATA]"}
                  </text>
                  <text fg={c.oosScore != null ? (c.oosScore >= 70 ? T.orange : T.down) : T.dim}>
                    {c.oosScore != null ? `[3. OOS: ${c.oosScore}%]` : "[3. OOS: NO DATA]"}
                  </text>
                  <text fg={c.brokerReplayScore != null ? (c.brokerReplayScore >= 70 ? T.teal : T.down) : T.dim}>
                    {c.brokerReplayScore != null ? `[4. REPLAY: ${c.brokerReplayScore}%]` : "[4. REPLAY: NO DATA]"}
                  </text>
                  <text fg={c.forwardScore != null ? (c.forwardScore >= 70 ? T.up : T.down) : T.dim}>
                    {c.forwardScore != null ? `[5. LIVE: ${c.forwardScore}%]` : "[5. LIVE: NO DATA]"}
                  </text>
                  <text fg={T.dim}>|</text>
                  <text fg={T.bright}>{c.evR != null ? `EV: +${c.evR.toFixed(2)}R` : "EV: —"}</text>
                  <text fg={T.dim}>{c.maxDd != null ? `MaxDD: ${c.maxDd.toFixed(1)}%` : "MaxDD: —"}</text>
                  <text fg={T.cyan}>{`Stability: Grade ${c.stabilityGrade}`}</text>
                </box>
              )}
            </box>
          )
        })}
      </scrollbox>
    </box>
  )
}
