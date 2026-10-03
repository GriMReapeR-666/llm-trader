import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { DrillResult } from "../types"

const STATUS_COLOR: Record<DrillResult["status"], string> = {
  passed: T.up,
  failed: T.down,
  running: T.warn,
  pending: T.dim,
}

const STATUS_ICON: Record<DrillResult["status"], string> = {
  passed: "✓",
  failed: "✗",
  running: "●",
  pending: "○",
}

export function drillsMove(delta: number): void {
  const n = store.get().drills.drills.length
  if (n === 0) return
  store.set((s) => ({ drills: { ...s.drills, selectedDrill: Math.max(0, Math.min(n - 1, s.drills.selectedDrill + delta)) } }))
}

export function DrillsScreen() {
  const d = useDeck((s) => s.drills)
  const selected = Math.min(d.selectedDrill, Math.max(0, d.drills.length - 1))
  const drill = d.drills[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" INCIDENT DRILLS " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" width="38%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"DRILL".padEnd(24)}{"TYPE".padEnd(20)}{"STATUS".padEnd(10)}</text>
          </box>
          {d.drills.length === 0 && <text fg={T.dim}>no drills available</text>}
          {d.drills.map((dr, i) => {
            const active = i === selected
            return (
              <box key={dr.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                <text fg={active ? T.bright : T.muted}>{`${active ? "▸" : " "}${dr.name}`.padEnd(24)}</text>
                <text fg={T.dim}>{dr.type.replace(/_/g, " ").padEnd(20)}</text>
                <text fg={STATUS_COLOR[dr.status]}>{`${STATUS_ICON[dr.status]} ${dr.status}`.padEnd(10)}</text>
              </box>
            )
          })}
        </scrollbox>
        <text fg={T.borderBright}>↑↓ select · {d.drills.length} drills</text>
      </box>

      <box title=" DRILL DETAIL " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {drill ? (
          <scrollbox flexGrow={1} viewportCulling>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STATUS_COLOR[drill.status]}> {STATUS_ICON[drill.status]} {drill.status.toUpperCase()} </text>
              <text fg={T.muted}>{drill.type.replace(/_/g, " ")}</text>
            </box>
            <text fg={T.borderBright}>──────────────────────────────</text>
            <Row label="started" value={fmtClock(drill.startedTs)} color={T.muted} />
            {drill.completedTs ? <Row label="completed" value={fmtClock(drill.completedTs)} color={T.muted} /> : null}
            {drill.durationMs != null && drill.durationMs > 0 ? <Row label="duration" value={`${drill.durationMs}ms`} color={T.dim} /> : null}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>STEPS</text>
            {drill.steps.map((step, i) => (
              <box key={i} flexDirection="row" gap={1}>
                <text fg={step.ok ? T.up : T.down}>{step.ok ? "✓" : "✗"}</text>
                <text fg={T.fg}>{step.name}</text>
                <text fg={T.dim}>— {step.detail}</text>
              </box>
            ))}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>DRILL TYPES</text>
            <text fg={T.muted}>· kill_switch — verify emergency halt</text>
            <text fg={T.muted}>· duplicate_block — reject duplicate intents</text>
            <text fg={T.muted}>· provider_circuit — test LLM failover</text>
            <text fg={T.muted}>· data_quality — gate stale/missing data</text>
            <text fg={T.muted}>· restart_recovery — durable state recovery</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>select a drill</text>
        )}
      </box>
    </box>
  )
}

function Row(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
