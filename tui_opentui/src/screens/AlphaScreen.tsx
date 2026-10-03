import { useDeck, store } from "../store"
import { T, SEPARATOR, sciPulse } from "../theme"
import { fmtNum, sparkline, fmtClock, bar } from "../format"
import type { AlphaSignal } from "../types"

const STATUS_COLOR: Record<AlphaSignal["status"], string> = {
  candidate: T.warn,
  validated: T.cyan,
  deployed: T.up,
  retired: T.dim,
}

const HEALTH_COLOR: Record<string, string> = {
  HEALTHY: T.up,
  DEGRADED: T.warn,
  REVERSED: T.down,
  DEAD: T.dim,
}

export function alphaMove(delta: number): void {
  const n = store.get().alphaSignals.length
  if (n === 0) return
  store.set({ selectedAlpha: Math.max(0, Math.min(n - 1, store.get().selectedAlpha + delta)) })
}

export function AlphaScreen() {
  const signals = useDeck((s) => s.alphaSignals)
  const health = useDeck((s) => s.alphaHealth)
  const selected = Math.min(useDeck((s) => s.selectedAlpha), Math.max(0, signals.length - 1))
  const a = signals[selected]
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0

  const healthCounts = { HEALTHY: 0, DEGRADED: 0, REVERSED: 0, DEAD: 0 }
  for (const h of health) healthCounts[h.status]++

  const alerts = health.filter((h) => h.status === "DEGRADED" || h.status === "REVERSED" || h.status === "DEAD")

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title="ALPHA SIGNALS" titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" width={58} flexShrink={0} paddingLeft={1}>
        <box flexDirection="row" gap={2}>
          <text fg={T.up}>{sciPulse(pulse, "●", "○")} {healthCounts.HEALTHY} HEALTHY</text>
          <text fg={T.warn}>{sciPulse(pulse && healthCounts.DEGRADED > 0, "●", "○")} {healthCounts.DEGRADED} DEGRADED</text>
          <text fg={T.down}>{sciPulse(pulse && healthCounts.REVERSED > 0, "●", "○")} {healthCounts.REVERSED} REVERSED</text>
          <text fg={T.dim}>{healthCounts.DEAD} DEAD</text>
        </box>
        <text fg={T.borderBright}>{SEPARATOR.thin}</text>
        <box flexDirection="row">
          <text fg={T.cyan}>{"NAME".padEnd(22)}{"STATUS".padEnd(12)}{"IC".padStart(6)}{"IC TREND".padStart(10)}</text>
        </box>
        {signals.length === 0 && <text fg={T.dim}>no alpha signals discovered</text>}
        {signals.map((x, i) => {
          const active = i === selected
          const h = health.find((hh) => hh.name === x.name)
          const hStatus = h?.status ?? "HEALTHY"
          const icTrend = h?.icTrend ?? 0
          const trendArrow = icTrend > 0.002 ? "▲" : icTrend < -0.002 ? "▼" : "─"
          const trendColor = icTrend > 0.002 ? T.up : icTrend < -0.002 ? T.down : T.dim
          return (
            <box key={x.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
              <text fg={active ? T.bright : T.fg}>{`${active ? "▸" : " "}${x.name}`.padEnd(22).slice(0, 22)}</text>
              <text fg={HEALTH_COLOR[hStatus]}>{sciPulse(pulse && hStatus !== "HEALTHY", "◉", "○")} {hStatus.padEnd(12)}</text>
              <text fg={x.ic >= 0.05 ? T.up : x.ic >= 0.02 ? T.warn : T.down}>{fmtNum(x.ic, 3).padStart(6)}</text>
              <text fg={trendColor}>{" "}{trendArrow} {icTrend > 0 ? "+" : ""}{icTrend.toFixed(3)}</text>
            </box>
          )
        })}
        <text fg={T.borderBright}> </text>
        {alerts.length > 0 && (
          <>
            <text fg={T.warn}>◈ DECAY ALERTS</text>
            {alerts.slice(0, 3).map((h) => (
              <text key={h.name} fg={HEALTH_COLOR[h.status]}>
                {" "}{h.status === "REVERSED" ? "✕" : h.status === "DEAD" ? "✕" : "⚠"} {h.name} ({h.consecutiveLow} bars low)
              </text>
            ))}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
          </>
        )}
        <text fg={T.dim}>↑↓ select · detail right</text>
        <text fg={T.dim}>{signals.filter((x) => x.status === "deployed").length} deployed · {signals.filter((x) => x.status === "candidate").length} candidates</text>
      </box>

      <box title={a ? `╔══ ${a.name.toUpperCase()} ══╗` : "╔══ — ══╗"} titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {a ? (
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STATUS_COLOR[a.status]}> {sciPulse(pulse, "◉", "○")} {a.status.toUpperCase()} </text>
              <text fg={T.muted}>{a.source}</text>
              <text fg={T.dim}>{a.timeframe}</text>
            </box>
            <text fg={T.muted}>{a.symbols.join(" · ")}</text>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ EDGE METRICS</text>
            <Row label="information coefficient" value={fmtNum(a.ic, 3)} color={a.ic >= 0.05 ? T.up : a.ic >= 0.02 ? T.warn : T.down} />
            <Row label="sharpe" value={fmtNum(a.sharpe, 2)} color={a.sharpe >= 1 ? T.up : a.sharpe >= 0.5 ? T.warn : T.down} />
            <Row label="strength" value={bar(a.strength, 20)} color={a.strength >= 0.7 ? T.up : a.strength >= 0.4 ? T.warn : T.down} />
            <Row label="confidence" value={bar(a.confidence, 20)} color={a.confidence >= 0.8 ? T.up : a.confidence >= 0.6 ? T.warn : T.down} />
            <Row label="turnover" value={`${(a.turnover * 100).toFixed(0)}%`} color={a.turnover < 0.3 ? T.up : a.turnover < 0.6 ? T.warn : T.down} />
            <Row label="decay half-life" value={`${a.decay.toFixed(1)}d`} color={a.decay > 20 ? T.up : a.decay > 7 ? T.warn : T.down} />
            {(() => {
              const h = health.find((hh) => hh.name === a.name)
              if (!h) return null
              return (
                <>
                  <text fg={T.borderBright}>{SEPARATOR.thin}</text>
                  <text fg={T.cyan}>◈ HEALTH STATUS</text>
                  <Row label="status" value={h.status} color={HEALTH_COLOR[h.status]} />
                  <Row label="IC mean" value={fmtNum(h.icMean, 4)} color={h.icMean >= 0.05 ? T.up : h.icMean >= 0.02 ? T.warn : T.down} />
                  <Row label="IC current" value={fmtNum(h.icCurrent, 4)} color={h.icCurrent >= 0.05 ? T.up : h.icCurrent >= 0.02 ? T.warn : T.down} />
                  <Row label="IC trend" value={`${h.icTrend > 0 ? "+" : ""}${h.icTrend.toFixed(4)}`} color={h.icTrend > 0 ? T.up : h.icTrend < 0 ? T.down : T.dim} />
                  <Row label="consecutive low" value={`${h.consecutiveLow} bars`} color={h.consecutiveLow > 5 ? T.down : h.consecutiveLow > 2 ? T.warn : T.up} />
                </>
              )
            })()}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ DISCOVERY</text>
            <Row label="discovered" value={fmtClock(a.discoveredTs)} color={T.muted} />
            <Row label="source module" value={a.source} color={T.cyan} />
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ EDGE LIFECYCLE</text>
            <text fg={T.muted}>· candidate → validated → deployed → retired</text>
            <text fg={T.muted}>· IC {">"} 0.05 + Sharpe {">"} 1.0 for validation</text>
            <text fg={T.muted}>· auto-retire on decay half-life {"<"} 5d</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no alpha signal selected</text>
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
