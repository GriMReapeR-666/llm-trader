import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock, fmtNum } from "../format"
import type { ModelArtifact } from "../types"

const STATUS_COLOR: Record<ModelArtifact["status"], string> = {
  APPROVED: T.up,
  CANDIDATE: T.warn,
  TRAINING: T.cyan,
  RETIRED: T.dim,
}

export function modelsMove(delta: number): void {
  const n = store.get().modelRegistry.artifacts.length
  if (n === 0) return
  store.set((s) => ({ modelRegistry: { ...s.modelRegistry, selectedModel: Math.max(0, Math.min(n - 1, s.modelRegistry.selectedModel + delta)) } }))
}

export function ModelsScreen() {
  const mr = useDeck((s) => s.modelRegistry)
  const selected = Math.min(mr.selectedModel, Math.max(0, mr.artifacts.length - 1))
  const model = mr.artifacts[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" MODEL REGISTRY " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" width="42%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"NAME".padEnd(22)}{"VERSION".padEnd(10)}{"STATUS".padEnd(12)}{"SHARPE".padStart(8)}</text>
          </box>
          {mr.artifacts.length === 0 && <text fg={T.dim}>no models registered</text>}
          {mr.artifacts.map((m, i) => {
            const active = i === selected
            return (
              <box key={m.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                <text fg={active ? T.bright : T.muted}>{`${active ? "▸" : " "}${m.name}`.padEnd(22)}</text>
                <text fg={T.dim}>{m.version.padEnd(10)}</text>
                <text fg={STATUS_COLOR[m.status]}>{m.status.padEnd(12)}</text>
                <text fg={T.muted}>{fmtNum(m.metrics.sharpe ?? 0, 2).padStart(8)}</text>
              </box>
            )
          })}
        </scrollbox>
        <text fg={T.borderBright}>↑↓ select · {mr.artifacts.length} models</text>
      </box>

      <box title={` ${model ? model.name : "—"} `} titleColor={model ? STATUS_COLOR[model.status] : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {model ? (
          <scrollbox flexGrow={1} viewportCulling>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STATUS_COLOR[model.status]}> {model.status} </text>
              <text fg={T.muted}>{model.version}</text>
              <text fg={T.dim}>{model.family}</text>
            </box>
            <text fg={T.borderBright}>──────────────────────────────</text>
            <Row label="schema version" value={model.schemaVersion} color={T.muted} />
            <Row label="training hash" value={model.trainingDataHash.slice(0, 16)} color={T.dim} />
            <Row label="trained" value={fmtClock(model.trainedTs)} color={T.muted} />
            {model.approvedTs && <Row label="approved" value={fmtClock(model.approvedTs)} color={T.up} />}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>METRICS</text>
            {Object.entries(model.metrics).map(([k, v]) => (
              <Row key={k} label={k} value={fmtNum(v, 3)} color={v > 0 ? T.up : v < 0 ? T.down : T.muted} />
            ))}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>NOTES</text>
            <text fg={T.muted}>{model.notes}</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>select a model</text>
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
