import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtNum, fmtMoney, sparkline } from "../format"
import { BrailleChart } from "../components/BrailleChart"

export function growthMove(delta: number): void {
  const n = store.get().growth.monteCarloSims.length
  if (n === 0) return
  store.set((s) => ({ growth: { ...s.growth, selectedSim: Math.max(0, Math.min(n - 1, s.growth.selectedSim + delta)) } }))
}

export function GrowthScreen() {
  const g = useDeck((s) => s.growth)
  const selected = Math.min(g.selectedSim, Math.max(0, g.monteCarloSims.length - 1))
  const sim = g.monteCarloSims[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" MONTE CARLO SIMS " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" width="40%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"NAME".padEnd(20)}{"CAGR".padStart(8)}{"SHARPE".padStart(8)}{"RUIN%".padStart(8)}</text>
          </box>
          {g.monteCarloSims.length === 0 && <text fg={T.dim}>no simulations · awaiting closed trade history</text>}
          {g.monteCarloSims.map((s, i) => {
            const active = i === selected
            return (
              <box key={s.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                <text fg={active ? T.bright : T.muted}>{`${active ? "▸" : " "}${s.name}`.padEnd(20)}</text>
                <text fg={s.cagr > 0 ? T.up : T.down}>{fmtNum(s.cagr, 1).padStart(8)}</text>
                <text fg={s.sharpe > 1 ? T.up : s.sharpe > 0 ? T.warn : T.down}>{fmtNum(s.sharpe, 2).padStart(8)}</text>
                <text fg={s.ruinProbability < 0.05 ? T.up : s.ruinProbability < 0.15 ? T.warn : T.down}>{(s.ruinProbability * 100).toFixed(1).padStart(8)}</text>
              </box>
            )
          })}
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title=" SIM DETAIL " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {sim ? (
            <scrollbox flexGrow={1} viewportCulling>
              <text fg={T.bright}>{sim.name}</text>
              <Row label="final equity" value={`$${fmtNum(sim.finalEquity, 0)}`} color={sim.finalEquity > 10000 ? T.up : T.down} />
              <Row label="CAGR" value={`${fmtNum(sim.cagr, 1)}%`} color={sim.cagr > 10 ? T.up : sim.cagr > 0 ? T.warn : T.down} />
              <Row label="sharpe" value={fmtNum(sim.sharpe, 2)} color={sim.sharpe > 1 ? T.up : sim.sharpe > 0 ? T.warn : T.down} />
              <Row label="max drawdown" value={`${fmtNum(sim.maxDrawdown, 1)}%`} color={sim.maxDrawdown < 10 ? T.up : sim.maxDrawdown < 20 ? T.warn : T.down} />
              <Row label="ruin probability" value={`${(sim.ruinProbability * 100).toFixed(1)}%`} color={sim.ruinProbability < 0.05 ? T.up : sim.ruinProbability < 0.15 ? T.warn : T.down} />
              <text fg={T.borderBright}>──────────────────────────────</text>
              <text fg={T.dim}>EQUITY PATH</text>
              <BrailleChart
                data={sim.path}
                width={36}
                height={4}
                color={sim.path.at(-1)! >= sim.path[0] ? T.up : T.down}
                fill
                formatY={(v) => fmtMoney(v, 0)}
              />
            </scrollbox>
          ) : (
            <text fg={T.dim}>no simulations active · awaiting closed trade history</text>
          )}
        </box>

        <box title=" PROJECTIONS " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling>
            <box flexDirection="row">
              <text fg={T.dim}>{"HORIZON".padEnd(10)}{"EXPECTED".padStart(12)}{"P5".padStart(12)}{"P95".padStart(12)}{"RUIN%".padStart(8)}</text>
            </box>
            {g.projections.length === 0 && <text fg={T.dim}>no projections · awaiting closed trade history</text>}
            {g.projections.map((p) => (
              <box key={p.horizon} flexDirection="row">
                <text fg={T.fg}>{p.horizon.padEnd(10)}</text>
                <text fg={T.muted}>{`$${fmtNum(p.expectedEquity, 0)}`.padStart(12)}</text>
                <text fg={T.down}>{`$${fmtNum(p.p5Equity, 0)}`.padStart(12)}</text>
                <text fg={T.up}>{`$${fmtNum(p.p95Equity, 0)}`.padStart(12)}</text>
                <text fg={p.ruinProb < 0.05 ? T.up : T.warn}>{(p.ruinProb * 100).toFixed(1).padStart(8)}</text>
              </box>
            ))}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <Row label="rolling expectancy" value={fmtNum(g.rollingExpectancy, 3)} color={g.rollingExpectancy > 0 ? T.up : T.down} />
            <Row label="rolling sharpe" value={fmtNum(g.rollingSharpe, 2)} color={g.rollingSharpe > 1 ? T.up : g.rollingSharpe > 0 ? T.warn : T.down} />
            <Row label="growth rate" value={`${fmtNum(g.currentGrowthRate, 2)}%`} color={g.currentGrowthRate > 0 ? T.up : T.down} />
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
