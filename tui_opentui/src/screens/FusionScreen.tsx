import { useDeck, store } from "../store"
import { useContentWidth, RES, clip, pad, fit, fitRow, wrapSegs } from "../layout"
import type { Seg } from "../layout"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { FusionResult, EventReaction } from "../types"

const STATUS_COLOR: Record<string, string> = {
  PASS: T.up,
  WEAK: T.warn,
  BLOCK: T.down,
}

export function fusionMove(delta: number): void {
  const n = store.get().fusionResults.length
  if (n === 0) return
  store.set({ selectedFusion: Math.max(0, Math.min(n - 1, store.get().selectedFusion + delta)) })
}

// fixed-width cell with one reserved spacer char so columns always separate
function cell(s: string, w: number, last: boolean): string {
  return last ? pad(clip(s, w), w) : pad(clip(s, w - 1), w)
}

// rail columns sized from the measured rail inner width
function railCols(inner: number) {
  const fixed = 5 + 6 // SIDE STATUS-trim + SCORE
  const rest = Math.max(6, inner - fixed)
  const sym = Math.max(4, Math.round(rest * 0.45))
  let status = Math.max(4, rest - sym)
  let total = fixed + sym + status
  while (total > inner && status > 4) {
    status--
    total--
  }
  return { sym, side: 5, status, score: 6 }
}

export function FusionScreen() {
  const fusions = useDeck((s) => s.fusionResults)
  const events = useDeck((s) => s.eventReactions)
  const selected = Math.min(useDeck((s) => s.selectedFusion), Math.max(0, fusions.length - 1))
  const f = fusions[selected]

  // ── measured geometry: every row budget derives from the live terminal width ──
  const contentW = useContentWidth()
  const railW = Math.max(26, Math.min(36, Math.round(contentW * 0.3)))
  const railInner = Math.max(20, railW - 3) // 2 border + 1 paddingLeft
  const rightW = Math.max(32, contentW - railW)
  const rightInner = Math.max(26, rightW - 3) // 2 border + 1 paddingLeft
  const avail = Math.max(10, rightInner - RES)
  const c = railCols(railInner)

  const head = railCellHeader(c)

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" FUSION GATE " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" width={railW} flexShrink={0} paddingLeft={1}>
        <text fg={T.dim}>{head}</text>
        {fusions.length === 0 && <text fg={T.dim}>no fusion results</text>}
        {fusions.map((x, i) => {
          const active = i === selected
          const segs: Seg[] = [
            { t: cell(`${active ? "▸" : " "}${x.symbol}`, c.sym, false), fg: active ? T.bright : T.fg },
            { t: cell(x.side, c.side, false), fg: x.side === "BUY" ? T.up : T.down },
            { t: cell(x.status, c.status, false), fg: STATUS_COLOR[x.status] },
            { t: cell(x.score.toFixed(2), c.score, true), fg: T.dim },
          ]
          return (
            <box key={x.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
              {fit(railInner, segs).map((s, j) => (
                <text key={j} fg={s.fg}>
                  {s.t}
                </text>
              ))}
            </box>
          )
        })}
        <text fg={T.borderBright}>{"─".repeat(Math.max(4, railInner))}</text>
        <text fg={T.dim}>{clip("↑↓ select · detail right", railInner)}</text>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title={f ? ` ${clip(f.symbol, 8)} ${f.side} → ${f.status} ` : " — "} titleColor={f ? STATUS_COLOR[f.status] : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={2} paddingLeft={1}>
          {f ? (
            <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
              {fitRow(avail, [
                { t: ` ${f.status} `, bg: STATUS_COLOR[f.status], fg: T.bg },
                { t: ` fusion score: ${f.score.toFixed(3)}`, fg: T.fg },
                { t: ` ${f.strategy}`, fg: T.muted },
                { t: ` ${fmtClock(f.timestamp)}`, fg: T.dim },
              ])}
              <text fg={T.borderBright}>{"─".repeat(Math.max(4, avail))}</text>
              <text fg={T.dim}>{clip("FUSION DECISION TREE", avail)}</text>
              <text fg={T.borderBright}> </text>
              {fitRow(avail, gateSegs("event_ok", f.eventOk, "no blackout conflict"))}
              <text fg={T.borderBright}>  │</text>
              {fitRow(avail, gateSegs("causal_agree", f.causalAgree, `${f.agreeingEdges.length} agreeing edges`))}
              <text fg={T.borderBright}>  │</text>
              {fitRow(avail, gateSegs("propagation_agree", f.propagationAgree, f.propagationPath ?? "no path"))}
              <text fg={T.borderBright}>  │</text>
              {fitRow(avail, gateSegs("ta_ok", f.taOk, "setup registry validated"))}
              <text fg={T.borderBright}>  │</text>
              {fitRow(avail, [
                { t: "  score = ", fg: T.dim },
                { t: "([event + causal + ta] / 3) × 0.85 + strength × 0.15", fg: T.muted },
              ])}
              {f.propagationAgree &&
                fitRow(avail, [
                  { t: "  + propagation boost: ", fg: T.dim },
                  { t: "+0.05", fg: T.muted },
                ])}
              <text fg={T.borderBright}>{"─".repeat(Math.max(4, avail))}</text>
              <text fg={T.dim}>{clip("REASONING", avail)}</text>
              {f.reasons.map((r, i) => {
                const fg = r.startsWith("BLOCK") ? T.down : r.startsWith("PASS") ? T.up : T.muted
                return fitRow(avail, [{ t: `  ${r}`, fg }])
              })}
              {f.agreeingEdges.length > 0 && (
                <box flexDirection="column">
                  <text fg={T.dim}>{clip("AGREEING EDGES", avail)}</text>
                  {f.agreeingEdges.map((e) => (
                    <text key={e} fg={T.muted}>{clip(`  · ${e}`, avail)}</text>
                  ))}
                </box>
              )}
            </scrollbox>
          ) : (
            <text fg={T.dim}>no fusion result selected</text>
          )}
        </box>

        <box title=" EVENT REACTIONS " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {events.length === 0 && <text fg={T.dim}>no event reactions</text>}
            {events.map((ev) => (
              <box key={ev.id} flexDirection="column">
                {fitRow(avail, [
                  { t: "▪ ", fg: T.borderBright },
                  { t: ev.eventName, fg: T.bright },
                  { t: ` ${ev.symbol}`, fg: T.muted },
                  { t: ` ${fmtClock(ev.timestamp)}`, fg: T.dim },
                ])}
                {fitRow(avail, [
                  { t: "  actual=", fg: T.dim },
                  { t: String(ev.actual), fg: T.fg },
                  { t: " forecast=", fg: T.dim },
                  { t: String(ev.forecast), fg: T.muted },
                  { t: " previous=", fg: T.dim },
                  { t: String(ev.previous), fg: T.muted },
                ])}
                {wrapSegs(avail, eventWindowSegs(ev), 2).map((line, li) => (
                  <box key={li}>{fitRow(avail, line)}</box>
                ))}
                <text fg={T.borderBright}>{"─".repeat(Math.max(4, avail))}</text>
              </box>
            ))}
          </scrollbox>
          <text fg={T.dim}>{clip("surprise = (actual - consensus) / std", rightInner - RES - 1)}</text>
          <text fg={T.dim}>{clip("windows: 1m 5m 15m 1h 4h 1d post-release", rightInner - RES - 1)}</text>
        </box>
      </box>
    </box>
  )
}

function railCellHeader(c: { sym: number; side: number; status: number; score: number }): string {
  return (
    cell("SYM", c.sym, false) + cell("SIDE", c.side, false) + cell("STATUS", c.status, false) + cell("SCORE", c.score, true)
  )
}

function gateSegs(label: string, passed: boolean, detail: string): Seg[] {
  return [
    { t: passed ? "✓ " : "✗ ", fg: passed ? T.up : T.down },
    { t: pad(label, 18), fg: passed ? T.fg : T.muted },
    { t: detail, fg: T.dim },
  ]
}

function eventWindowSegs(ev: EventReaction): Seg[] {
  const segs: Seg[] = [
    { t: "  surprise=", fg: T.dim },
    { t: `${ev.surprise > 0 ? "+" : ""}${ev.surprise.toFixed(2)}`, fg: ev.surprise > 0 ? T.up : ev.surprise < 0 ? T.down : T.dim },
    { t: " · windows: ", fg: T.dim },
  ]
  for (const [k, v] of Object.entries(ev.windows)) {
    segs.push({
      t: `${k}=${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}% `,
      fg: v > 0 ? T.up : v < 0 ? T.down : T.dim,
    })
  }
  return segs
}
