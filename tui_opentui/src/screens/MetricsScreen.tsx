import { useDeck } from "../store"
import { T } from "../theme"
import { fmtClock, fmtNum } from "../format"

export function metricsMove(delta: number): void {
  // metrics screen is read-only, no selection needed
}

export function MetricsScreen() {
  const m = useDeck((s) => s.metricsExporter)

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" PROMETHEUS METRICS " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" width="50%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"METRIC".padEnd(36)}{"TYPE".padEnd(10)}{"VALUE".padStart(12)}</text>
          </box>
          {m.prometheusMetrics.length === 0 && <text fg={T.dim}>no metrics exported</text>}
          {m.prometheusMetrics.map((p, i) => (
            <box key={i} flexDirection="row">
              <text fg={T.fg}>{p.name.padEnd(36)}</text>
              <text fg={T.dim}>{p.type.padEnd(10)}</text>
              <text fg={T.muted}>{fmtNum(p.value, 2).padStart(12)}</text>
            </box>
          ))}
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>EXPORT STATUS</text>
          <text fg={m.exportStatus === "active" ? T.up : m.exportStatus === "paused" ? T.warn : T.down}>{m.exportStatus}</text>
          <text fg={T.dim}>last export: {fmtClock(m.lastExportTs)}</text>
        </scrollbox>
      </box>

      <box title=" PRINCIPAL HEARTBEATS " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"AGENT".padEnd(22)}{"STATUS".padEnd(10)}{"LATENCY".padStart(10)}  {"LAST BEAT".padEnd(12)}</text>
          </box>
          {m.heartbeats.length === 0 && <text fg={T.dim}>no heartbeats</text>}
          {m.heartbeats.map((h) => (
            <box key={h.agent} flexDirection="row">
              <text fg={T.fg}>{h.agent.padEnd(22)}</text>
              <text fg={h.status === "alive" ? T.up : h.status === "stale" ? T.warn : T.down}>{h.status.padEnd(10)}</text>
              <text fg={T.muted}>{`${h.latencyMs}ms`.padStart(10)}</text>
              <text fg={T.dim}>  {fmtClock(h.lastBeat)}</text>
            </box>
          ))}
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>LEGEND</text>
          <text fg={T.up}>● alive</text>
          <text fg={T.warn}>{`● stale (>30s)`}</text>
          <text fg={T.down}>{`● dead (>120s)`}</text>
        </scrollbox>
      </box>
    </box>
  )
}
