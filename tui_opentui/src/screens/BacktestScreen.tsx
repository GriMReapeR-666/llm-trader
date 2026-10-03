import { useDeck, store } from "../store"
import { T, SEPARATOR } from "../theme"
import { fmtNum, fmtMoney, sparkline, bar, fmtClock } from "../format"
import type { BacktestResult } from "../types"
import { BrailleChart } from "../components/BrailleChart"

export function backtestMove(delta: number): void {
  const n = store.get().backtests.length
  if (n === 0) return
  store.set({ selectedBacktest: Math.max(0, Math.min(n - 1, store.get().selectedBacktest + delta)) })
}

export function BacktestScreen() {
  const backtests = useDeck((s) => s.backtests)
  const selected = Math.min(useDeck((s) => s.selectedBacktest), Math.max(0, backtests.length - 1))
  const bt = backtests[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title="BACKTESTS" titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" width={48} flexShrink={0} paddingLeft={1}>
        <box flexDirection="row">
          <text fg={T.cyan}>{"STRATEGY".padEnd(20)}{"PERIOD".padEnd(10)}{"STATUS".padEnd(9)}{"PF".padStart(6)}{"SHRP".padStart(6)}</text>
        </box>
        {backtests.length === 0 && <text fg={T.dim}>no backtests run</text>}
        {backtests.map((x, i) => {
          const active = i === selected
          return (
            <box key={x.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
              <text fg={active ? T.bright : T.fg}>{`${active ? "▸" : " "}${x.strategyId}`.padEnd(20).slice(0, 20)}</text>
              <text fg={T.muted}>{x.period.padEnd(10)}</text>
              <text fg={x.status === "complete" ? T.up : x.status === "running" ? T.warn : T.down}>{x.status.padEnd(9)}</text>
              <text fg={x.profitFactor >= 1.5 ? T.up : x.profitFactor >= 1.1 ? T.warn : T.down}>{fmtNum(x.profitFactor, 2).padStart(6)}</text>
              <text fg={x.sharpe >= 1 ? T.up : x.sharpe >= 0.5 ? T.warn : T.down}>{fmtNum(x.sharpe, 2).padStart(6)}</text>
            </box>
          )
        })}
        <text fg={T.borderBright}> </text>
        <text fg={T.dim}>↑↓ select · detail right</text>
      </box>

      <box title={bt ? `╔══ CPCV · ${bt.strategyId} ══╗` : "╔══ — ══╗"} titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        {bt ? (
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={bt.status === "complete" ? T.up : bt.status === "running" ? T.warn : T.down}> ◉ {bt.status.toUpperCase()} </text>
              <text fg={T.muted}>{bt.period}</text>
              <text fg={T.dim}>{fmtClock(bt.startedTs)}</text>
              {bt.completedTs ? <text fg={T.dim}>→ {fmtClock(bt.completedTs)}</text> : null}
            </box>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ EQUITY CURVE</text>
            {bt.equityCurve.length > 0 ? (
              <BrailleChart
                data={bt.equityCurve}
                width={52}
                height={5}
                color={bt.equityCurve.at(-1)! >= bt.equityCurve[0] ? T.up : T.down}
                fill
                formatY={(v) => fmtMoney(v, 0)}
              />
            ) : (
              <text fg={T.dim}>no equity curve data recorded</text>
            )}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ PERFORMANCE METRICS</text>
            <Row label="total trades" value={String(bt.totalTrades)} color={T.fg} />
            <Row label="win rate" value={`${bt.winRate.toFixed(1)}%`} color={bt.winRate >= 55 ? T.up : bt.winRate >= 45 ? T.warn : T.down} />
            <Row label="profit factor" value={fmtNum(bt.profitFactor, 2)} color={bt.profitFactor >= 1.5 ? T.up : bt.profitFactor >= 1.1 ? T.warn : T.down} />
            <Row label="sharpe ratio" value={fmtNum(bt.sharpe, 2)} color={bt.sharpe >= 1 ? T.up : bt.sharpe >= 0.5 ? T.warn : T.down} />
            <Row label="sortino ratio" value={fmtNum(bt.sortino, 2)} color={bt.sortino >= 1.5 ? T.up : bt.sortino >= 1 ? T.warn : T.down} />
            <Row label="calmar ratio" value={fmtNum(bt.calmar, 2)} color={bt.calmar >= 2 ? T.up : bt.calmar >= 1 ? T.warn : T.down} />
            <Row label="max drawdown" value={`${fmtNum(bt.maxDD, 1)}%`} color={bt.maxDD <= 10 ? T.up : bt.maxDD <= 20 ? T.warn : T.down} />
            <Row label="expectancy" value={`${bt.expectancyR >= 0 ? "+" : ""}${fmtNum(bt.expectancyR, 3)}R`} color={bt.expectancyR > 0 ? T.up : T.down} />
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ MONTHLY RETURNS</text>
            <box flexDirection="row" gap={0}>
              {bt.monthlyReturns.length > 0 ? (
                bt.monthlyReturns.map((m, i) => (
                  <box key={i} flexDirection="column" alignItems="center" width={6}>
                    <text fg={m >= 0 ? T.up : T.down}>{m >= 0 ? "▲" : "▼"}</text>
                    <text fg={m >= 0 ? T.up : T.down}>{bar(Math.abs(m) / 4, 4)}</text>
                    <text fg={T.dim}>{["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"][i]}</text>
                  </box>
                ))
              ) : (
                <text fg={T.dim}>no monthly returns recorded</text>
              )}
            </box>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ RISK ASSESSMENT</text>
            <Row label="max DD / PF" value={bt.profitFactor > 0 ? `${(bt.maxDD / bt.profitFactor).toFixed(1)}x` : "—"} color={bt.maxDD / bt.profitFactor < 10 ? T.up : T.warn} />
            <Row label="calmar / DD" value={bt.maxDD > 0 ? `${(bt.calmar / bt.maxDD * 100).toFixed(2)}` : "—"} color={T.muted} />
            <Row label="trades / month" value={`${(bt.totalTrades / 12).toFixed(0)}`} color={T.muted} />
          </scrollbox>
        ) : (
          <text fg={T.dim}>no backtest selected</text>
        )}
      </box>
    </box>
  )
}

function Row(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color}><b>{props.value}</b></text>
    </box>
  )
}
