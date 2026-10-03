import { useDeck } from "../store"
import { useContentWidth, RES, clip, pad, fit, fitRow } from "../layout"
import type { Seg } from "../layout"
import { T } from "../theme"
import type { EnhancedCausalEdge, CausalChainPath } from "../types"

const LIFECYCLE_COLOR: Record<string, string> = {
  CANDIDATE: T.muted,
  MEASURED: T.warn,
  STAT_PASS: T.cyan,
  REPLAY_PASS: T.teal,
  DEMO_MATCH: T.up,
  TINY_LIVE: T.orange,
  FULL: T.bright,
  RETIRING: T.down,
  RETIRED: T.dim,
  STANDBY: T.muted,
}

const TIER_COLOR: Record<string, string> = {
  PRIMARY: T.up,
  RUNTIME_FEED: T.warn,
  UNVERIFIED: T.down,
}

const SIGN_COLOR: Record<string, string> = {
  POSITIVE: T.up,
  NEGATIVE: T.down,
  CONDITIONAL: T.warn,
}

function formatDecay(ageSec: number, halfLifeSec: number): number {
  return Math.exp(-ageSec / halfLifeSec)
}

export function CausalScreen() {
  const edges = useDeck((s) => s.enhancedCausalEdges)
  const chains = useDeck((s) => s.causalChainPaths)
  const share = useDeck((s) => s.dagShare)
  const sources = Array.from(new Set(edges.map((e) => e.from)))

  // ── measured geometry: every budget below derives from the live terminal width ──
  const contentW = useContentWidth()
  // share of the width given to the DAG tree — operator-adjustable with - / =
  const dagW = Math.max(24, Math.min(110, Math.round(contentW * (share ?? 0.42))))
  const rightW = Math.max(28, contentW - dagW)
  const dagInner = Math.max(18, dagW - 4) // 2 border + 2 padding
  const rightInner = Math.max(24, rightW - 3) // 2 border + 1 paddingLeft

  const headAvail = Math.max(6, dagInner - RES - 1)
  const rowAvail = Math.max(8, dagInner - RES - 2) // edge rows are indented 1 col + scrollbar
  const metaAvail = Math.max(6, dagInner - RES - 5) // edge meta indented 1+3 cols + scrollbar
  const chainAvail = Math.max(20, rightInner - RES - 2) // hop rows are indented 2 cols

  return (
    <box flexDirection="row" flexGrow={1}>
      {/* ── CAUSAL DAG (tree) ── */}
      <box title=" CAUSAL DAG " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" width={dagW} padding={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          {edges.length === 0 && <text fg={T.dim}>no edges yet…</text>}
          {sources.map((src, si) => {
            const outs = edges.filter((e) => e.from === src)
            return (
              <box key={src} flexDirection="column">
                {si > 0 && <text fg={T.borderBright}> </text>}
                {fitRow(headAvail, [
                  { t: clip(src, headAvail - 4), fg: T.bright },
                  { t: ` (${outs.length})`, fg: T.dim },
                ])}
                {outs.map((e) => {
                  const bMax = Math.max(2, Math.min(10, Math.round(rowAvail * 0.22)))
                  const bars = "─".repeat(Math.max(1, Math.min(bMax, Math.round(e.strength * bMax))))
                  const arrowColor = e.direction === 1 ? T.up : T.down
                  const tgtMax = Math.max(2, rowAvail - bars.length - 4)
                  const pct = (formatDecay(e.ageSec, e.decayHalfLifeSec) * 100).toFixed(0)
                  const metaParts = [`${pct}%`, `lag ${e.lag}`, `n=${e.samples}`]
                  if (e.lifecycle) metaParts.push(e.lifecycle)
                  if (e.sourceTier) metaParts.push(e.sourceTier)
                  if (e.relation) metaParts.push(`rel ${e.relation}`)
                  return (
                    <box key={`${e.from}->${e.to}`} flexDirection="column" paddingLeft={1}>
                      {fitRow(rowAvail, [
                        { t: "│", fg: T.borderBright },
                        { t: bars, fg: arrowColor },
                        { t: e.direction === 1 ? "▶" : "▷", fg: arrowColor },
                        { t: " " },
                        { t: clip(e.to, tgtMax), fg: T.fg },
                      ])}
                      <box flexDirection="row" paddingLeft={4}>
                        <text fg={T.dim}>{clip(metaParts.join(" · "), metaAvail)}</text>
                      </box>
                    </box>
                  )
                })}
              </box>
            )
          })}
          <text fg={T.borderBright}> </text>
          <text fg={T.dim}>{clip("▶ ▷ dir · ─ = strength", headAvail)}</text>
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1}>
        {/* ── EDGE TABLE — columns sized from the measured width ── */}
        <EdgeTablePanel inner={rightInner - RES} edges={edges} />
        {/* ── MULTI-HOP CHAINS ── */}
        <box title=" MULTI-HOP CHAINS " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {chains.length === 0 && <text fg={T.dim}>no active propagation paths</text>}
            {chains.map((path) => (
              <ChainBlock key={path.pathId} path={path} avail={chainAvail} />
            ))}
            <text fg={T.borderBright}> </text>
            <text fg={T.dim}>{clip("BFS propagation: max 3 hops · half-life decay", rightInner - RES - 1)}</text>
            <text fg={T.dim}>{clip("path score = sign_product × accumulated confidence", rightInner - RES - 1)}</text>
          </scrollbox>
        </box>
      </box>
    </box>
  )
}

// ── EDGE TABLE ────────────────────────────────────────────────────────────
function tableCols(inner: number) {
  const fixed = 5 + 5 + 5 + 4 // STR LAG N DIR
  const rest = Math.max(0, inner - fixed)
  let edge = Math.max(8, Math.min(26, Math.round(rest * 0.46)))
  let life = Math.max(6, Math.min(16, Math.round(rest * 0.3)))
  let tier = Math.max(4, rest - edge - life)
  let total = fixed + edge + life + tier
  while (total > inner && (tier > 4 || life > 6 || edge > 8)) {
    if (tier > 4) tier--
    else if (life > 6) life--
    else if (edge > 8) edge--
    total--
  }
  return { edge, str: 5, lag: 5, n: 5, dir: 4, life, tier }
}

function tableCell(s: string, w: number, last: boolean): string {
  // reserve one char so every non-last column always ends with a visible space
  return last ? pad(clip(s, w), w) : pad(clip(s, w - 1), w)
}

function EdgeTablePanel(props: { inner: number; edges: EnhancedCausalEdge[] }) {
  const { inner, edges } = props
  const c = tableCols(inner)
  const widths = [
    { key: "edge", label: "EDGE" },
    { key: "str", label: "STR" },
    { key: "lag", label: "LAG" },
    { key: "n", label: "N" },
    { key: "dir", label: "DIR" },
    { key: "life", label: "LIFECYCLE" },
  ] as const
  const header =
    widths.map((col) => tableCell(col.label, c[col.key], false)).join("") +
    tableCell("TIER", c.tier, true)

  return (
        <box title=" EDGE TABLE " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
      <text fg={T.dim}>{clip(header, inner)}</text>
      <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
        {edges.map((e) => {
          const segs: Seg[] = [
            { t: tableCell(`${e.from}→${e.to}`, c.edge, false), fg: T.muted },
            { t: tableCell(e.strength.toFixed(2), c.str, false), fg: e.strength > 0.7 ? T.bright : T.muted },
            { t: tableCell(e.lag, c.lag, false), fg: T.dim },
            { t: tableCell(String(e.samples), c.n, false), fg: T.dim },
            { t: tableCell(e.direction === 1 ? "POS" : "NEG", c.dir, false), fg: e.direction === 1 ? T.up : T.down },
            { t: tableCell(e.lifecycle, c.life, false), fg: LIFECYCLE_COLOR[e.lifecycle] },
            { t: tableCell(e.sourceTier, c.tier, true), fg: TIER_COLOR[e.sourceTier] },
          ]
          return fitRow(inner, segs)
        })}
      </scrollbox>
      <text fg={T.borderBright}> </text>
      <text fg={T.dim}>{clip("agreement gate: technical + causal must concur before MarketModel", inner - 1)}</text>
      <text fg={T.dim}>{clip("proposals reach RiskController", inner - 1)}</text>
    </box>
  )
}

// ── MULTI-HOP CHAINS ──────────────────────────────────────────────────────
function ChainBlock(props: { path: CausalChainPath; avail: number }) {
  const { path, avail } = props
  const last = path.hops.length - 1
  const biasColor = path.bias === "BUY" ? T.up : path.bias === "SELL" ? T.down : T.dim
  return (
    <box flexDirection="column">
      {fitRow(avail, [
        { t: ` ${path.bias} `, bg: biasColor, fg: T.bg },
        { t: ` score ${path.score.toFixed(3)}`, fg: T.fg },
        { t: ` → ${path.terminal}`, fg: T.muted },
        { t: ` · ${Math.round(path.horizonSec / 3600)}h`, fg: T.dim },
      ])}
      {path.hops.map((hop, hi) => {
        const sign = hop.sign === "POSITIVE" ? "→" : "↛"
        return fitRow(avail, [
          { t: hi < last ? "├─" : "└─", fg: T.borderBright },
          { t: hop.sourceName, fg: T.dim },
          { t: ` ${sign} `, fg: SIGN_COLOR[hop.sign] },
          { t: hop.targetName, fg: T.fg },
          { t: ` [${hop.relation}]`, fg: T.muted },
        ])
      })}
      <text fg={T.borderBright}>{"─".repeat(Math.max(4, avail - 1))}</text>
    </box>
  )
}
