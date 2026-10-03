import { useDeck } from "../store"
import { fmtNum, sparkline, sciBar } from "../format"
import { T, SEPARATOR } from "../theme"
import { BrailleChart } from "../components/BrailleChart"
import { syntaxTheme } from "../syntaxStyle"
import type { StrategyState } from "../types"

const STAGE_COLOR: Record<StrategyState["stage"], string> = {
  FULL: T.up,
  EVALUATION: T.warn,
  CANDIDATE: T.cyan,
  RETIRED: T.dim,
  QUARANTINE: T.down,
}

const STAGE_ICON: Record<StrategyState["stage"], string> = {
  FULL: "◈",
  EVALUATION: "◇",
  CANDIDATE: "▣",
  RETIRED: "○",
  QUARANTINE: "✕",
}

function bandColor(value: number, good: number, ok: number, invert = false): string {
  const goodEnough = invert ? value <= good : value >= good
  const okEnough = invert ? value <= ok : value >= ok
  return goodEnough ? T.up : okEnough ? T.warn : T.down
}

export function StrategiesScreen() {
  const strategies = useDeck((s) => s.strategies)
  const selected = Math.min(useDeck((s) => s.selectedStrategy), Math.max(0, strategies.length - 1))
  const s = strategies[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title=" ◈ STRATEGY LIBRARY "
        titleColor={T.cyan}
        borderStyle="single"
        borderColor={T.cyan}
        flexDirection="column"
        width={66}
        flexShrink={0}
        paddingLeft={1}
      >
        <select
          flexGrow={1}
          focused
          options={strategies.map((x) => ({
            name: `${STAGE_ICON[x.stage]} ${x.name}`,
            description: `${x.stage} · ${x.trades}T · PF ${x.profitFactor.toFixed(2)} · Sharpe ${x.sharpe.toFixed(2)}`,
            value: x.id,
          }))}
          showDescription
          showScrollIndicator
          showSelectionIndicator
          wrapSelection
          textColor={T.fg}
          descriptionColor={T.dim}
          selectedBackgroundColor={T.raised}
          selectedTextColor={T.bright}
          selectedDescriptionColor={T.teal}
          backgroundColor={T.bg}
          onChange={(_i) => {}}
        />
        <text fg={T.dim}>bands: PF ≥1.5 good ·≥1.1 ok | SHRP ≥1.0 ·≥0.5 | WIN ≥55 ·≥45</text>
      </box>

      <box
        title={` ◈ ${s ? s.name : "—"} `}
        titleColor={s ? STAGE_COLOR[s.stage] : T.dim}
        borderStyle="single"
        borderColor={s ? STAGE_COLOR[s.stage] : T.border}
        flexDirection="column"
        flexGrow={1}
        paddingLeft={1}
      >
        {s ? (
          <scrollbox
            flexGrow={1}
            viewportCulling
            scrollbarOptions={{
              showArrows: false,
              trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
            }}
          >
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STAGE_COLOR[s.stage]}> {s.activeToday ? "◉" : "○"} {s.stage} </text>
              <text fg={T.muted}>{s.timeframe}</text>
              <text fg={T.dim}>{s.symbols.join(" ")}</text>
              <text fg={s.activeToday ? T.up : T.dim}>{s.activeToday ? "● live" : "○ idle"}</text>
            </box>
            <box flexDirection="row">
              <text fg={T.dim}>gate </text>
              <text fg={STAGE_COLOR[s.stage]}>{sciBar(s.gateProgress, 12)}</text>
              <text fg={T.muted}> {(s.gateProgress * 100).toFixed(0)}%</text>
            </box>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <Stat label="trades" value={String(s.trades)} color={T.fg} icon="◇" />
            <Stat label="win rate" value={`${s.winRate.toFixed(1)}%`} color={bandColor(s.winRate, 55, 45)} icon="◆" />
            <Stat label="profit factor" value={fmtNum(s.profitFactor, 2)} color={bandColor(s.profitFactor, 1.5, 1.1)} icon="▲" />
            <Stat label="sharpe" value={fmtNum(s.sharpe, 2)} color={bandColor(s.sharpe, 1.0, 0.5)} icon="◈" />
            <Stat label="max drawdown" value={`${fmtNum(s.maxDD, 1)}%`} color={bandColor(s.maxDD, 10, 25, true)} icon="▼" />
            <Stat label="expectancy" value={`${s.expectancyR >= 0 ? "+" : ""}${fmtNum(s.expectancyR, 3)}R`} color={s.expectancyR > 0 ? T.up : T.down} icon={s.expectancyR > 0 ? "▲" : "▼"} />
            <Stat label="recovery factor" value={fmtNum(s.recoveryFactor, 2)} color={bandColor(s.recoveryFactor, 2, 1)} icon="◆" />
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ EQUITY (index=100)</text>
            {s.equityCurve.length > 0 ? (
              <BrailleChart
                data={s.equityCurve}
                width={36}
                height={4}
                color={s.equityCurve.at(-1)! >= s.equityCurve[0] ? T.up : T.down}
                fill
                formatY={(v) => v.toFixed(0)}
              />
            ) : (
              <text fg={T.dim}>no equity curve data recorded</text>
            )}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ NOTES</text>
            <code
              content={s.notes}
              filetype="markdown"
              syntaxStyle={syntaxTheme}
              fg={T.muted}
              bg={T.panel}
              drawUnstyledText
              wrapMode="word"
              selectable
            />
          </scrollbox>
        ) : (
          <text fg={T.dim}>no strategy selected</text>
        )}
      </box>
    </box>
  )
}

function Stat(props: { label: string; value: string; color: string; icon?: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.icon ?? "│"} {props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
