import { useDeck } from "../store"
import { fmtMoney, bar } from "../format"
import { T } from "../theme"

export function AccountRail() {
  const acct = useDeck((s) => s.account)
  const pnl = acct.equity - acct.dayStartEquity
  const ddColor = acct.drawdownPct > 4 ? T.critical : acct.drawdownPct > 2 ? T.warn : T.muted

  return (
    <box
      title=" Account "
      titleColor={T.muted}
      borderStyle="single"
      borderColor={T.border}
      backgroundColor={T.panel}
      flexDirection="column"
      paddingLeft={1}
      paddingRight={1}
      gap={0}
    >
      <box flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>Equity</text>
        <text fg={T.bright}>{fmtMoney(acct.equity)}</text>
      </box>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>Day P/L</text>
        <text fg={pnl >= 0 ? T.up : T.down}>{fmtMoney(pnl, 0)}</text>
      </box>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>Drawdown</text>
        <text fg={ddColor}>{acct.drawdownPct.toFixed(1)}%</text>
      </box>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>Heat</text>
        <text fg={acct.heatUsedR > 4 ? T.warn : T.teal}>
          {bar(acct.heatUsedR / acct.heatCapR, 6)} {acct.heatUsedR.toFixed(1)}R
        </text>
      </box>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>Win</text>
        <text fg={T.fg}>{acct.winRate.toFixed(0)}%</text>
      </box>
    </box>
  )
}
