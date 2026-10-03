import { useDeck, store } from "../store"
import { fmtClock } from "../format"
import { T, SEPARATOR } from "../theme"
import { syntaxTheme } from "../syntaxStyle"
import { IntentDiff } from "../components/IntentDiff"
import { useContentWidth } from "../layout"
import type { TimeframeTrend } from "../types"

const TREND_COLOR: Record<string, string> = {
  BULL: T.up,
  BEAR: T.down,
  NEUTRAL: T.dim,
}

const trendOf = (t: TimeframeTrend): string => {
  const raw = String(t?.trend ?? "NEUTRAL").toUpperCase()
  return raw.startsWith("BULL") ? "BULL" : raw.startsWith("BEAR") ? "BEAR" : "NEUTRAL"
}
const trendColor = (t: TimeframeTrend): string => TREND_COLOR[trendOf(t)] ?? T.dim
const numOr = (v: unknown, d: number): number => {
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : d
}
const biasOf = (t: TimeframeTrend): string => String(t?.bias ?? "neutral")

export function BrainScreen() {
  const cycles = useDeck((s) => s.cycles)
  const mtf = useDeck((s) => s.mtf)
  const selected = Math.min(useDeck((s) => s.selectedCycle), Math.max(0, cycles.length - 1))
  const brief = cycles[selected]

  const aligned = mtf.length > 0 && (
    mtf.filter((t) => trendOf(t) === "BULL").length === mtf.length ||
    mtf.filter((t) => trendOf(t) === "BEAR").length === mtf.length
  )
  const alignmentColor = aligned ? T.up : mtf.filter((t) => trendOf(t) !== "NEUTRAL").length > 0 ? T.warn : T.dim

  // intent-diff panel text budget: content width − side margins (1+1) −
  // panel border (2) − safety (2) so diff lines never cross the panel border
  const contentW = useContentWidth()
  const diffW = Math.max(12, contentW - 6)

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title=" ◈ CYCLES "
        titleColor={T.cyan}
        borderStyle="single"
        borderColor={T.cyan}
        flexDirection="column"
        width="34%"
      >
        <select
          flexGrow={1}
          focused
          options={cycles.map((c) => {
            const color =
              c.decision === "EXECUTE" ? T.up : c.decision === "HOLD" ? T.warn : T.down
            return {
              name: `${c.decision === "EXECUTE" ? "▶" : "▷"} ${fmtClock(c.ts)} ${c.symbol} ${c.timeframe}`,
              description: `${c.decision} ${(c.confidence * 100).toFixed(0)}% · ${c.strategy}`,
              value: c.id,
            }
          })}
          showDescription
          showScrollIndicator
          showSelectionIndicator
          wrapSelection
          textColor={T.muted}
          descriptionColor={T.dim}
          selectedBackgroundColor={T.raised}
          selectedTextColor={T.bright}
          selectedDescriptionColor={T.teal}
          backgroundColor={T.bg}
          onChange={(_i) => {}}
        />
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box
          title=" ◈ CYCLE BRIEF "
          titleColor={T.cyan}
          borderStyle="single"
          borderColor={T.borderBright}
          flexDirection="column"
          flexGrow={brief ? 1 : 0}
          height={brief ? undefined : 0}
        >
          {brief ? (
            <>
              <scrollbox flexGrow={1} paddingLeft={1} paddingRight={1} viewportCulling>
                <code
                  content={brief.reasoning}
                  filetype="markdown"
                  syntaxStyle={syntaxTheme}
                  fg={T.fg}
                  bg={T.panel}
                  drawUnstyledText
                  wrapMode="word"
                  selectable
                />
                {brief.features && brief.features.length > 0 && (
                  <box flexDirection="column">
                    <text fg={T.cyan}>◈ FEATURE SNAPSHOT</text>
                    <text fg={T.borderBright}>{SEPARATOR.thin}</text>
                    {brief.features.map((f) => (
                      <text key={f.name} fg={T.muted}>
                        ▸ <span fg={T.bright}>{f.name}</span>: {f.value}
                      </text>
                    ))}
                  </box>
                )}
              </scrollbox>
                {brief.intent && (
                <box
                  title=" ◈ INTENT → VALIDATED (diff) "
                  titleColor={T.teal}
                  borderStyle="single"
                  borderColor={T.teal}
                  flexDirection="column"
                  maxHeight={13}
                  marginLeft={1}
                  marginRight={1}
                  marginBottom={0}
                >
                  <IntentDiff diff={brief.intent.diff} width={diffW} />
                </box>
              )}
            </>
          ) : (
            <box padding={1}>
              <text fg={T.dim}>waiting for the first intelligence cycle…</text>
            </box>
          )}
        </box>

        {mtf.length > 0 && (
          <box
            title=" ◈ MULTI-TIMEFRAME ANALYSIS "
            titleColor={T.cyan}
            borderStyle="single"
            borderColor={T.borderBright}
            flexDirection="column"
            maxHeight={10}
            marginLeft={0}
            marginRight={0}
            marginBottom={0}
          >
            <box flexDirection="row" paddingLeft={1}>
              <text fg={T.cyan}>{"TF".padEnd(6)}{"TREND".padEnd(10)}{"ADX".padStart(6)}{"RSI".padStart(7)}{"BIAS".padStart(10)}</text>
            </box>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            {mtf.map((t) => {
              const trend = trendOf(t)
              const adx = numOr(t?.adx, 0)
              const rsi = numOr(t?.rsi, 50)
              const bias = biasOf(t)
              return (
                <box key={String(t?.tf ?? "")} flexDirection="row" paddingLeft={1}>
                  <text fg={T.bright}>{String(t?.tf ?? "").padEnd(6)}</text>
                  <text fg={trendColor(t)}>{trend === "BULL" ? "▲" : trend === "BEAR" ? "▼" : "─"} {trend.padEnd(8)}</text>
                  <text fg={adx > 25 ? T.up : adx > 18 ? T.warn : T.dim}>{String(adx.toFixed(1)).padStart(6)}</text>
                  <text fg={rsi > 60 ? T.up : rsi < 40 ? T.down : T.dim}>{String(rsi.toFixed(1)).padStart(7)}</text>
                  <text fg={trendColor(t)}>{bias.padStart(10)}</text>
                </box>
              )
            })}
            <text fg={alignmentColor} paddingLeft={1}>{aligned ? "◈ ALL TIMEFRAMES ALIGNED" : "◇ TIMEFRAME DIVERGENCE"}</text>
          </box>
        )}
      </box>
    </box>
  )
}

export function brainMove(delta: number): void {
  const n = store.get().cycles.length
  if (n === 0) return
  const cur = store.get().selectedCycle
  store.set({ selectedCycle: Math.max(0, Math.min(n - 1, cur + delta)) })
}

export function brainSelect(): void {
  const c = store.get().cycles[store.get().selectedCycle]
  if (!c) return
  store.pushStream({
    ts: Date.now(),
    kind: "system",
    agent: "SYSTEM",
    title: `cycle brief ${c.id}`,
    lines: [`${c.decision} ${(c.confidence * 100).toFixed(0)}% · ${c.strategy}`],
  })
  store.set({ view: "stream" })
}
