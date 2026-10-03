import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock, fmtNum, sparkline } from "../format"

export function rewardsMove(delta: number): void {
  const n = store.get().rewardSystem.entries.length
  if (n === 0) return
  store.set((s) => ({ rewardSystem: { ...s.rewardSystem, selectedEntry: Math.max(0, Math.min(n - 1, s.rewardSystem.selectedEntry + delta)) } }))
}

export function RewardsScreen() {
  const rs = useDeck((s) => s.rewardSystem)
  const selected = Math.min(rs.selectedEntry, Math.max(0, rs.entries.length - 1))
  const entry = rs.entries[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" REWARD LEDGER " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" width="40%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"TRADE".padEnd(14)}{"SYMBOL".padEnd(10)}{"STRATEGY".padEnd(18)}{"R-NET".padStart(7)}</text>
          </box>
          {rs.entries.length === 0 && <text fg={T.dim}>no reward entries</text>}
          {rs.entries.map((e, i) => {
            const active = i === selected
            return (
              <box key={e.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                <text fg={active ? T.bright : T.muted}>{`${active ? "▸" : " "}${e.tradeId}`.padEnd(14)}</text>
                <text fg={T.fg}>{e.symbol.padEnd(10)}</text>
                <text fg={T.dim}>{e.strategy.slice(0, 18).padEnd(18)}</text>
                <text fg={e.rNet > 0 ? T.up : T.down}>{fmtNum(e.rNet, 2).padStart(7)}</text>
              </box>
            )
          })}
        </scrollbox>
        <text fg={T.borderBright}>↑↓ select · {rs.entries.length} entries</text>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title=" AGENT ATTRIBUTION " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling>
            <box flexDirection="row">
              <text fg={T.dim}>{"AGENT".padEnd(22)}{"REWARD".padStart(9)}{"TRADES".padStart(8)}{"AVG CREDIT".padStart(11)}{"SHARPE".padStart(8)}</text>
            </box>
            {rs.agentSummaries.map((a) => (
              <box key={a.agent} flexDirection="row">
                <text fg={T.fg}>{a.agent.padEnd(22)}</text>
                <text fg={a.totalReward > 0 ? T.up : T.down}>{fmtNum(a.totalReward, 2).padStart(9)}</text>
                <text fg={T.muted}>{String(a.tradeCount).padStart(8)}</text>
                <text fg={T.dim}>{fmtNum(a.avgCreditShare, 3).padStart(11)}</text>
                <text fg={a.sharpeContribution > 1 ? T.up : a.sharpeContribution > 0 ? T.warn : T.down}>{fmtNum(a.sharpeContribution, 2).padStart(8)}</text>
              </box>
            ))}
          </scrollbox>
        </box>

        <box title=" SUSTAINABILITY " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling>
            <Row label="total R-net" value={fmtNum(rs.totalRNet, 2)} color={rs.totalRNet > 0 ? T.up : T.down} />
            <Row label="avg R-net" value={fmtNum(rs.avgRNet, 3)} color={rs.avgRNet > 0 ? T.up : T.down} />
            <Row label="sustainability" value={`${(rs.sustainabilityScore * 100).toFixed(0)}%`} color={rs.sustainabilityScore > 0.7 ? T.up : rs.sustainabilityScore > 0.4 ? T.warn : T.down} />
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>CVaR METRICS</text>
            {rs.cvarMetrics.map((m) => (
              <Row key={m.name} label={m.name} value={`${fmtNum(m.value, 3)} / ${fmtNum(m.threshold, 3)}`} color={m.status === "pass" ? T.up : m.status === "warn" ? T.warn : T.down} />
            ))}
            {entry && (
              <>
                <text fg={T.borderBright}>──────────────────────────────</text>
                <text fg={T.dim}>SELECTED ENTRY</text>
                <Row label="trade" value={entry.tradeId} color={T.fg} />
                <Row label="R-net" value={fmtNum(entry.rNet, 2)} color={entry.rNet > 0 ? T.up : T.down} />
                {entry.attribution.map((a) => (
                  <Row key={a.agent} label={a.agent} value={`credit ${(a.creditShare * 100).toFixed(0)}% · R ${fmtNum(a.contribution, 2)}`} color={T.muted} />
                ))}
              </>
            )}
          </scrollbox>
        </box>
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
