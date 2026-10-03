import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtNum, bar } from "../format"
import { useKeyboard } from "@opentui/react"
import type { KernelService, CircuitBreakerState, IncidentCard } from "../types"

const HEALTH_ICONS: Record<string, string> = {
  true: "✓",
  false: "✗",
}

const HEALTH_COLORS: Record<string, string> = {
  true: T.up,
  false: T.down,
}

function CircuitBreakerRow(props: { cb: CircuitBreakerState }) {
  const { cb } = props
  return (
    <box flexDirection="column">
      <box flexDirection="row" gap={1}>
        <text fg={cb.active ? T.down : T.up}>{cb.active ? "⚠" : "✓"}</text>
        <text fg={cb.active ? T.down : T.fg}>Level {cb.level}: {cb.name}</text>
      </box>
      <box flexDirection="row" paddingLeft={3}>
        <text fg={T.dim}>trigger: {cb.trigger} · threshold: {cb.threshold}</text>
      </box>
      <box flexDirection="row" paddingLeft={3}>
        <text fg={T.dim}>current: {cb.current} · effect: {cb.effect}</text>
      </box>
    </box>
  )
}

const INCIDENT_SEV: Record<string, { icon: string; color: string }> = {
  critical: { icon: "⛔", color: T.critical },
  warn: { icon: "⚠", color: T.warn },
  info: { icon: "ℹ", color: T.cyan ?? T.teal },
}

function IncidentRow(props: { card: IncidentCard; selected: boolean }) {
  const { card, selected } = props
  const sev = INCIDENT_SEV[card.severity] ?? INCIDENT_SEV.warn
  return (
    <box flexDirection="column">
      <box flexDirection="row" gap={1}>
        <text fg={selected ? T.bright : sev.color}>{selected ? "▸" : sev.icon}</text>
        <text fg={selected ? T.bright : sev.color}>{card.code}</text>
        {card.count > 1 && <text fg={T.muted}>×{card.count}</text>}
      </box>
      {selected && (
        <box flexDirection="column" paddingLeft={3}>
          <text fg={T.warn}>▸ {card.runbook}</text>
          <text fg={T.dim}>1st: {card.first_response}</text>
        </box>
      )}
    </box>
  )
}

function IncidentsPane() {
  const incidents = useDeck((s) => s.systemHealth.incidents)
  const cards = incidents.cards

  useKeyboard((key) => {
    if (cards.length === 0) return
    if (key.name === "up") {
      const next = (incidents.selected - 1 + cards.length) % cards.length
      store.set((st) => ({ systemHealth: { ...st.systemHealth, incidents: { ...st.systemHealth.incidents, selected: next } } }))
    } else if (key.name === "down") {
      const next = (incidents.selected + 1) % cards.length
      store.set((st) => ({ systemHealth: { ...st.systemHealth, incidents: { ...st.systemHealth.incidents, selected: next } } }))
    }
  })

  const statusColor =
    incidents.status === "CRITICAL" ? T.critical
    : incidents.status === "WARNING" ? T.warn
    : incidents.status === "OK" ? T.up
    : T.dim
  const ks = incidents.killSwitch

  return (
    <box
      title={` INCIDENTS ${incidents.status} `}
      titleColor={statusColor}
      borderStyle="single"
      borderColor={statusColor}
      flexDirection="column"
      flexGrow={2}
      paddingLeft={1}
    >
      <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
        {ks && (
          <box flexDirection="row" gap={1}>
            <text fg={ks.active ? T.critical : T.up}>{ks.active ? "⛔ KILL SWITCH: ENGAGED" : "✓ kill switch: clear"}</text>
            <text fg={ks.trading_enabled ? T.dim : T.warn}>{ks.trading_enabled ? "trading enabled" : "trading disabled"}</text>
          </box>
        )}
        {cards.length === 0 ? (
          <text fg={statusColor === T.dim ? T.dim : T.up}>
            {incidents.status === "UNKNOWN" ? "no health telemetry — engine offline?" : "✓ no active incidents"}
          </text>
        ) : (
          cards.map((card, i) => (
            <IncidentRow key={`${card.code}-${i}`} card={card} selected={i === incidents.selected} />
          ))
        )}
        {cards.length > 0 && <text fg={T.dim}>↑/↓ select · expand shows runbook + 1st response</text>}
      </scrollbox>
    </box>
  )
}

export function SystemHealthScreen() {
  const health = useDeck((s) => s.systemHealth)
  const rm = health.riskManager

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={36} flexShrink={0}>
        <box title=" MICROKERNEL SERVICES " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={2} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={T.dim}>context: {health.contextName}</text>
              <text fg={health.kernelDisposed ? T.down : T.up}>{health.kernelDisposed ? "DISPOSED" : "ACTIVE"}</text>
            </box>
            <text fg={T.dim}>plugins: {health.pluginCount}</text>
            <text fg={T.borderBright}>──────────────────────────</text>
            {health.services.map((svc, i) => (
              <box key={i} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={HEALTH_COLORS[String(svc.healthy)]}>{HEALTH_ICONS[String(svc.healthy)]}</text>
                  <text fg={svc.healthy ? T.fg : T.down}>{svc.name}</text>
                  <text fg={T.dim}>v{svc.version}</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.muted}>uptime: {(svc.uptime / 3600).toFixed(1)}h</text>
                </box>
                {svc.dependencies.length > 0 && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.dim}>deps: {svc.dependencies.join(", ")}</text>
                  </box>
                )}
              </box>
            ))}
          </scrollbox>
        </box>

        <box title=" CIRCUIT BREAKERS " titleColor={T.critical} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {rm.circuitBreakers.map((cb, i) => (
              <CircuitBreakerRow key={i} cb={cb} />
            ))}
          </scrollbox>
        </box>

        <IncidentsPane />
      </box>

      <box title=" RISK MANAGER STATE " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          <text fg={T.dim}>ACCOUNT STATE</text>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <box flexDirection="row"><text fg={T.muted}>{"balance:".padEnd(18)}</text><text fg={T.bright}>${fmtNum(rm.balance, 2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"initial:".padEnd(18)}</text><text fg={T.fg}>${fmtNum(rm.initialBalance, 2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"peak equity:".padEnd(18)}</text><text fg={T.up}>${fmtNum(rm.peakEquity, 2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"drawdown:".padEnd(18)}</text><text fg={rm.drawdownPct > 10 ? T.down : rm.drawdownPct > 5 ? T.warn : T.up}>{rm.drawdownPct.toFixed(1)}%</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"daily loss:".padEnd(18)}</text><text fg={rm.dailyLossPct > 3 ? T.down : T.up}>{rm.dailyLossPct.toFixed(1)}%</text></box>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>POSITION SIZING MULTIPLIERS</text>
          <box flexDirection="row"><text fg={T.muted}>{"DD multiplier:".padEnd(18)}</text><text fg={rm.ddMultiplier < 1 ? T.warn : T.up}>{rm.ddMultiplier.toFixed(2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"streak multiplier:".padEnd(18)}</text><text fg={rm.streakMultiplier < 1 ? T.warn : T.up}>{rm.streakMultiplier.toFixed(2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"portfolio heat:".padEnd(18)}</text><text fg={rm.portfolioHeatR > rm.maxPortfolioHeatR * 0.8 ? T.down : T.fg}>{rm.portfolioHeatR.toFixed(1)}R / {rm.maxPortfolioHeatR}R</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"heat budget:".padEnd(18)}</text><text fg={T.muted}>{bar(rm.portfolioHeatR / rm.maxPortfolioHeatR, 20)}</text></box>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>STREAK STATE</text>
          <box flexDirection="row"><text fg={T.muted}>{"consecutive losses:".padEnd(20)}</text><text fg={rm.consecutiveLosses >= 3 ? T.down : T.fg}>{rm.consecutiveLosses}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"consecutive wins:".padEnd(20)}</text><text fg={rm.consecutiveWins > 0 ? T.up : T.dim}>{rm.consecutiveWins}</text></box>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>R-MULTIPLE HISTORY (last 50)</text>
          <box flexDirection="row" flexWrap="wrap">
            {rm.tradeROfLast50.map((r, i) => (
              <text key={i} fg={r > 0 ? T.up : r < 0 ? T.down : T.dim}>{r > 0 ? "+" : ""}{r.toFixed(1)} </text>
            ))}
          </box>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>BREAKER TRIGGERS</text>
          <box flexDirection="row"><text fg={T.muted}>{"weekly circuit:".padEnd(18)}</text><text fg={rm.weeklyCircuitActive ? T.down : T.up}>{rm.weeklyCircuitActive ? "ACTIVE (50% sizing)" : "inactive"}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"permanent halt:".padEnd(18)}</text><text fg={rm.permanentHalt ? T.critical : T.up}>{rm.permanentHalt ? "HALTED" : "clear"}</text></box>
          {rm.flattenReason && (
            <box flexDirection="row"><text fg={T.critical}>FLATTEN: {rm.flattenReason}</text></box>
          )}
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>SIZE MULTIPLIER CHAIN</text>
          <text fg={T.muted}>  base_risk × confidence_gate × circuit × dd × streak × kelly × atr</text>
          <text fg={T.muted}>  confidence: &lt;0.6→0, &lt;0.75→0.5, ≥0.75→1.0</text>
          <text fg={T.muted}>  DD ladder: &lt;5%→1.0, &lt;10%→0.75, &lt;15%→0.5, &lt;20%→0.25, ≥20%→0</text>
          <text fg={T.muted}>  streak: loss1→1.0, loss2→0.5, loss3→0.25, loss4→0.125, ≥5→0</text>
        </scrollbox>
      </box>
    </box>
  )
}
