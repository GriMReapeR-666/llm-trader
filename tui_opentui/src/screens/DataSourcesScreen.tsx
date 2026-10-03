import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { DataSource, LLMProvider } from "../types"

const HEALTH_COLOR: Record<string, string> = {
  healthy: T.up,
  degraded: T.warn,
  down: T.down,
  circuit_open: T.critical,
}

const TIER_COLOR: Record<string, string> = {
  REALTIME: T.up,
  DAILY: T.cyan,
  WEEKLY: T.purple,
  FREE_STACK: T.muted,
}

export function datasourcesMove(delta: number): void {
  const n = store.get().dataSources.length
  if (n === 0) return
  store.set({ selectedDataSource: Math.max(0, Math.min(n - 1, store.get().selectedDataSource + delta)) })
}

export function DataSourcesScreen() {
  const sources = useDeck((s) => s.dataSources)
  const providers = useDeck((s) => s.llmProviders)
  const selected = Math.min(useDeck((s) => s.selectedDataSource), Math.max(0, sources.length - 1))
  const src = sources[selected]

  const tierGroups = {
    REALTIME: sources.filter((s) => s.tier === "REALTIME"),
    DAILY: sources.filter((s) => s.tier === "DAILY"),
    WEEKLY: sources.filter((s) => s.tier === "WEEKLY"),
    FREE_STACK: sources.filter((s) => s.tier === "FREE_STACK"),
  }

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={36} flexShrink={0}>
        <box title=" DATA SOURCES " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={2} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {(["REALTIME", "DAILY", "WEEKLY", "FREE_STACK"] as const).map((tier) => {
              const group = tierGroups[tier]
              if (group.length === 0) return null
              return (
                <box key={tier} flexDirection="column">
                  <text fg={TIER_COLOR[tier]}>{tier} ({group.length})</text>
                  {group.map((s, i) => {
                    const idx = sources.indexOf(s)
                    const active = idx === selected
                    return (
                      <box key={s.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                        <text fg={HEALTH_COLOR[s.health]}>{active ? "▸" : " "} </text>
                        <text fg={active ? T.bright : T.fg}>{s.name.slice(0, 20).padEnd(20)}</text>
                        <text fg={HEALTH_COLOR[s.health]}>{s.health === "healthy" ? "●" : s.health === "degraded" ? "◐" : "○"}</text>
                      </box>
                    )
                  })}
                  <text fg={T.borderBright}> </text>
                </box>
              )
            })}
          </scrollbox>
          <text fg={T.dim}>↑↓ select</text>
        </box>

        <box title=" LLM PROVIDERS " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {providers.map((p) => (
            <box key={p.id} flexDirection="column">
              <box flexDirection="row" gap={1}>
                <text fg={HEALTH_COLOR[p.health]}>{p.health === "healthy" ? "●" : p.health === "degraded" ? "◐" : "○"}</text>
                <text fg={T.fg}>{p.name}</text>
                <text fg={T.dim}>{p.rpm} RPM</text>
                <text fg={T.muted}>{p.requestsToday} reqs</text>
              </box>
              {p.models.map((m) => (
                <text key={m.id} fg={m.isDefault ? T.bright : T.dim}>
                  {"  "}{m.isDefault ? "▸" : " "}{m.name}
                  {m.latencyMs > 0 && <span fg={T.dim}> {m.latencyMs}ms</span>}
                  {m.tokensUsed > 0 && <span fg={T.dim}> {(m.tokensUsed / 1000).toFixed(0)}k tok</span>}
                </text>
              ))}
              <text fg={T.borderBright}>──────────────────────────</text>
            </box>
          ))}
        </box>
      </box>

      <box title={src ? ` ${src.name} ` : " — "} titleColor={src ? T.cyan : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {src ? (
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={HEALTH_COLOR[src.health]}> {src.health.toUpperCase()} </text>
              <text fg={TIER_COLOR[src.tier]}>{src.tier}</text>
              <text fg={T.muted}>{src.category}</text>
            </box>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>DESCRIPTION</text>
            <text fg={T.muted}>{src.description}</text>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>HEALTH METRICS</text>
            <text fg={T.muted}>  last fetch: {fmtClock(src.lastFetchTs)}</text>
            <text fg={T.muted}>  fetch count: {src.fetchCount}</text>
            <text fg={T.muted}>  error count: {src.errorCount}</text>
            <text fg={T.muted}>  error rate: {src.fetchCount > 0 ? ((src.errorCount / src.fetchCount) * 100).toFixed(1) : 0}%</text>
            <text fg={T.muted}>  avg latency: {src.avgLatencyMs}ms</text>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>CIRCUIT BREAKER</text>
            <text fg={T.muted}>  tier 1: normal · tier 2: degraded</text>
            <text fg={T.muted}>  tier 3: failing · tier 4: circuit_open</text>
            <text fg={T.muted}>  window: 10min · cooldown: 1hr</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no data source selected</text>
        )}
      </box>
    </box>
  )
}
