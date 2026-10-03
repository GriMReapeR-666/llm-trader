import { useDeck } from "../store"
import { fmtNum, fmtMoney, sparkline, bar, sciBar } from "../format"
import { T, SEPARATOR, sciPulse } from "../theme"
import { clip, useContentWidth } from "../layout"
import { BrailleChart } from "../components/BrailleChart"
import { Sparkline } from "../components/Sparkline"

function Histogram(props: { values: number[]; buckets?: number }) {
  const n = props.buckets ?? 7
  const wins = props.values.filter((v) => v > 0)
  const losses = props.values.filter((v) => v <= 0)
  const maxR = Math.max(3, ...props.values.map((v) => Math.abs(v)))
  const rows: { label: string; count: number; win: boolean }[] = []
  for (let b = 0; b < 3; b++) {
    const lo = (maxR / 3) * b
    const hi = (maxR / 3) * (b + 1)
    rows.push({
      label: `+${lo.toFixed(1)}…${hi.toFixed(1)}R`,
      count: wins.filter((v) => v > lo && v <= hi).length,
      win: true,
    })
  }
  for (let b = 0; b < 2; b++) {
    const lo = -(maxR / 2) * (b + 1)
    const hi = -(maxR / 2) * b
    rows.push({ label: `${lo.toFixed(1)}…${hi.toFixed(1)}R`, count: losses.filter((v) => v >= lo && v < hi).length || (b === 0 ? losses.length : 0), win: false })
  }
  const maxCount = Math.max(1, ...rows.map((r) => r.count))
  return (
    <box flexDirection="column">
      {rows.map((r, i) => (
        <box key={i} flexDirection="row">
          <text fg={T.dim}>{clip(r.label, 13)}</text>
          <text fg={r.win ? T.up : T.down}>{sciBar(r.count / maxCount, 20)}</text>
          <text fg={T.muted}> {r.count}</text>
        </box>
      ))}
    </box>
  )
}

export function AnalyticsScreen() {
  const acct = useDeck((s) => s.account)
  const strategies = useDeck((s) => s.strategies)
  const rMultiples = useDeck((s) => s.rMultiples)
  const monthly = useDeck((s) => s.monthlyR)
  const qm = useDeck((s) => s.quantMonitor)
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0
  const contentW = useContentWidth()
  const curve = acct.equityCurve.slice(-80)
  const peakSeries: number[] = []
  let peak = -Infinity
  for (const v of curve) {
    peak = Math.max(peak, v)
    peakSeries.push(peak)
  }
  const ddSeries = curve.map((v, i) => ((peakSeries[i] - v) / peakSeries[i]) * 100)

  const totalWins = rMultiples.filter((r) => r > 0)
  const avgWin = totalWins.length ? totalWins.reduce((a, b) => a + b, 0) / totalWins.length : 0
  const grossW = totalWins.reduce((a, b) => a + b, 0)
  const grossL = Math.abs(rMultiples.filter((r) => r <= 0).reduce((a, b) => a + b, 0))
  const pf = grossL > 0 ? grossW / grossL : grossW > 0 ? 9.99 : 0

  const pbo = qm?.pbo != null ? Number(qm.pbo) : null
  const dsr = qm?.dsr != null ? Number(qm.dsr) : null
  const bocpdProb = qm?.bocpd_prob != null ? Number(qm.bocpd_prob) : null
  const kalmanZ = qm?.kalman_z != null ? Number(qm.kalman_z) : null
  const tcaBps = qm?.tca_spread_bps != null ? Number(qm.tca_spread_bps) : null
  const wfe = qm?.wfe != null ? Number(qm.wfe) : null
  const wfePassed = qm?.wfe_passed == null ? null : Boolean(qm.wfe_passed)
  const wfeHistory = (qm?.wfe_history ?? []).map((h) => Number(h.wfe))
  const driftStatus = qm?.drift_status ?? null
  const driftFrac = qm?.drift_fraction != null ? Number(qm.drift_fraction) : null
  const hasQm = pbo != null || dsr != null || bocpdProb != null || kalmanZ != null || tcaBps != null || wfe != null || driftStatus != null

  return (
    <scrollbox flexGrow={1} paddingLeft={1} paddingRight={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
      <text fg={T.cyan}>╔══ QUANT MONITOR · ANALYTICS ═══════════════════════════════════╗</text>

      <box borderStyle="single" borderColor={hasQm ? T.cyan : T.border} flexDirection="column" padding={1}>
        <box flexDirection="row" gap={2}>
          <text fg={hasQm ? T.cyan : T.dim}>◈ LIVE QUANT MONITOR</text>
          <text fg={hasQm ? (pulse ? T.up : T.dim) : T.dim}>{hasQm ? (pulse ? "◉ ACTIVE" : "○ SYNC") : "○ STANDBY"}</text>
        </box>
        <text fg={T.borderBright}>{SEPARATOR.thin}</text>
        <box flexDirection="row" gap={2}>
          <QuantMetric label="PBO" value={pbo} format={(v) => v.toFixed(3)} warn={0.5} />
          <QuantMetric label="DSR" value={dsr} format={(v) => v.toFixed(3)} warn={0.5} inverted />
          <QuantMetric label="BOCPD" value={bocpdProb} format={(v) => v.toFixed(3)} warn={0.3} />
          <QuantMetric label="KALMAN" value={kalmanZ} format={(v) => `z ${v.toFixed(2)}`} warn={2} inverted />
          <QuantMetric label="TCA" value={tcaBps} format={(v) => `${v.toFixed(0)}bps`} warn={50} />
          {driftStatus != null && (
            <text fg={driftStatus === "DRIFT" ? T.down : driftStatus === "SHIFT" ? T.warn : T.up}>
              {`DRIFT ${driftStatus}${driftFrac != null ? ` ${(driftFrac * 100).toFixed(0)}%` : ""}`}
            </text>
          )}
        </box>
        <text fg={T.muted}>{clip("CPCV 6-fold purge 1% embargo · BOCPD hazard 30 threshold 0.15 (×5) · TCA ATR·√288 spread DOM", contentW - 6)}</text>
        <box flexDirection="row" gap={1}>
          <text fg={wfe == null ? T.dim : wfePassed ? T.up : T.down}>
            {wfe == null ? "WFE —" : `WFE ${wfe.toFixed(3)} ${wfePassed ? "✓" : "✗"}`}
          </text>
          {wfeHistory.length >= 2 ? (
            <Sparkline
              data={wfeHistory}
              width={Math.max(10, Math.min(20, wfeHistory.length))}
              color={wfePassed ? T.up : T.warn}
              glow
            />
          ) : (
            <text fg={T.dim}>WFE trend needs ≥2 gate runs</text>
          )}
        </box>
      </box>

      <text fg={T.borderBright}>{SEPARATOR.thin}</text>

      <box flexDirection="row" gap={1}>
        <box borderStyle="single" borderColor={T.up} flexDirection="column" width="58%" padding={1}>
          <box flexDirection="row" justifyContent="space-between">
            <text fg={T.up}>◈ OVERALL EQUITY</text>
            <text fg={T.muted}>now {fmtMoney(acct.equity)} · peak {fmtMoney(acct.peakEquity)}</text>
          </box>
          <BrailleChart
            data={curve}
            width={Math.max(28, Math.round(contentW * 0.58) - 4)}
            height={5}
            color={T.up}
            fill
            baseline="bottom"
            formatY={(v) => fmtMoney(v, 0)}
          />
          <text fg={T.dim}>{curve.length > 0 ? `${curve.length} bars · current DD -${acct.drawdownPct.toFixed(2)}%` : "no trade history recorded yet · awaiting fills"}</text>
        </box>
        <box borderStyle="single" borderColor={T.down} flexDirection="column" width="41%" padding={1}>
          <box flexDirection="row" justifyContent="space-between">
            <text fg={T.down}>◈ DRAWDOWN %</text>
            <text fg={T.muted}>now -{acct.drawdownPct.toFixed(2)}%</text>
          </box>
          <BrailleChart
            data={ddSeries.map((d) => -d)}
            width={Math.max(20, Math.round(contentW * 0.41) - 4)}
            height={5}
            color={T.down}
            fill
            baseline="top"
            formatY={(v) => `${v.toFixed(1)}%`}
          />
          <text fg={T.dim}>{ddSeries.length > 0 ? "underwater curve · baseline 0.0%" : "no drawdown recorded · capital intact"}</text>
        </box>
      </box>

      <text fg={T.borderBright}>{SEPARATOR.thin}</text>

      <box flexDirection="row" gap={1}>
        <box borderStyle="single" borderColor={T.purple} flexDirection="column" width="52%" padding={0} paddingLeft={1}>
          <text fg={T.purple}>◈ R-MULTIPLE DISTRIBUTION (last {rMultiples.length})</text>
          <Histogram values={rMultiples} />
        </box>
        <box borderStyle="single" borderColor={T.teal} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <text fg={T.teal}>◈ SESSION RATIOS</text>
          <Row label="profit factor" value={fmtNum(pf, 2)} color={pf >= 1.5 ? T.up : pf >= 1.1 ? T.warn : T.down} icon="◆" />
          <Row label="avg win" value={`+${fmtNum(avgWin, 2)}R`} color={T.up} icon="▲" />
          <Row label="win rate" value={`${rMultiples.length ? ((totalWins.length / rMultiples.length) * 100).toFixed(0) : "—"}%`} color={T.fg} icon="◇" />
          <Row label="net R" value={`${grossW - grossL >= 0 ? "+" : ""}${fmtNum(grossW - grossL, 2)}R`} color={grossW - grossL >= 0 ? T.up : T.down} icon={grossW - grossL >= 0 ? "▲" : "▼"} />
        </box>
      </box>

      <text fg={T.borderBright}>{SEPARATOR.thin}</text>

      <box borderStyle="single" borderColor={T.warn} flexDirection="column" padding={0} paddingLeft={1}>
        <text fg={T.warn}>◈ MONTHLY R (calendar)</text>
        <box flexDirection="row" gap={0}>
          {monthly.length > 0 && !monthly.every((m) => m === 0) ? (
            monthly.map((m, i) => (
              <box key={i} flexDirection="column" alignItems="center" width={6}>
                <text fg={m >= 0 ? T.up : T.down}>{m >= 0 ? "▲" : "▼"}</text>
                <text fg={m >= 0 ? T.up : T.down}>{sciBar(Math.abs(m) / 4, 4)}</text>
                <text fg={T.dim}>{["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"][i]}</text>
              </box>
            ))
          ) : (
            <text fg={T.dim} padding={1}>no monthly R-multiples accumulated · trades will record here monthly</text>
          )}
          {monthly.length > 0 && (
            <text fg={T.muted}> sum {monthly.reduce((a, b) => a + b, 0).toFixed(1)}R</text>
          )}
        </box>
      </box>

      <text fg={T.borderBright}>{SEPARATOR.thin}</text>

      <box borderStyle="single" borderColor={T.cyan} flexDirection="column" padding={0} paddingLeft={1}>
        <text fg={T.cyan}>◈ STRATEGY COMPARISON</text>
        <text fg={T.borderBright}>{SEPARATOR.thin}</text>
        <box flexDirection="row">
          <text fg={T.dim}>{"NAME".padEnd(24)}{"EQUITY".padStart(8)}{"TRADES".padStart(7)}{"PF".padStart(6)}{"SHRP".padStart(6)}{"MAXDD%".padStart(7)}</text>
        </box>
        {strategies.length === 0 ? (
          <text fg={T.dim} paddingLeft={1} paddingTop={1}>no strategies registered</text>
        ) : (
          strategies.map((x) => {
            const rising = x.equityCurve.length > 0 ? (x.equityCurve.at(-1)! >= x.equityCurve[0]) : true
            return (
              <box key={x.id} flexDirection="row">
                <text fg={T.fg}>{x.name.padEnd(24).slice(0, 24)}</text>
                <text fg={rising ? T.up : T.down}>{x.equityCurve.length > 0 ? sparkline(x.equityCurve, 7).padStart(8) : "—".padStart(8)}</text>
                <text fg={T.muted}>{String(x.trades).padStart(7)}</text>
                <text fg={x.profitFactor >= 1.5 ? T.up : x.profitFactor >= 1.1 ? T.warn : T.down}>{fmtNum(x.profitFactor, 2).padStart(6)}</text>
                <text fg={x.sharpe >= 1 ? T.up : x.sharpe >= 0.5 ? T.warn : T.down}>{fmtNum(x.sharpe, 2).padStart(6)}</text>
                <text fg={x.maxDD <= 10 ? T.up : x.maxDD <= 25 ? T.warn : T.down}>{fmtNum(x.maxDD, 1).padStart(7)}</text>
              </box>
            )
          })
        )}
      </box>

      <text fg={T.cyan}>╚══════════════════════════════════════════════════════════════╝</text>
    </scrollbox>
  )
}

function QuantMetric(props: {
  label: string
  value: number | null
  format: (v: number) => string
  warn: number
  inverted?: boolean
}) {
  const { label, value, format, warn, inverted } = props
  if (value == null) {
    return (
      <text fg={T.dim}>{label} —</text>
    )
  }
  const color = inverted
    ? (Math.abs(value) > warn ? T.down : T.up)
    : (value > warn ? T.down : T.up)
  return (
    <text fg={color}>{label} {format(value)}</text>
  )
}

function Row(props: { label: string; value: string; color: string; icon?: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.icon ?? "│"} {props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
