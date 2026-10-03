import { useTerminalDimensions } from "@opentui/react"
import { useDeck, store } from "../store"
import { fmtClock } from "../format"
import { T, SECTIONS, findSection } from "../theme"

const TRANSPORT_LABEL: Record<string, string> = {
  connecting: "CONNECTING",
  live: "LIVE",
  mcp: "MCP",
  stale: "STALE",
}

const TRANSPORT_COLOR: Record<string, string> = {
  connecting: T.warn,
  live: T.neon,
  mcp: T.cyan,
  stale: T.down,
}

const MODE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  HALTED: { label: "HALTED", color: "#ffffff", bg: T.critical },
  LIVE: { label: "LIVE", color: "#ffffff", bg: T.down },
  PAPER: { label: "PAPER", color: "#0b0e14", bg: T.teal },
  DEMO: { label: "DEMO", color: "#ffffff", bg: "#5b5bd6" },
}

// ── measured chip fitting ───────────────────────────────────────────────────
// A chip row must never exceed the terminal width — OpenTUI overflows a flex
// row horizontally (mid-glyph wraps) instead of wrapping it. Every tab strip
// is fitted here: chip padding drops first, then the longest labels are
// ellipsized until the whole row fits `avail` columns.
interface ChipSpec {
  label: string
  active?: boolean
  onPress: () => void
}

function measure(specs: ChipSpec[], pad: number): number {
  return specs.reduce((s, c) => s + (c.active ? 2 : 2) + c.label.length + pad * 2, 0)
}

interface Fitted {
  label: string
  active?: boolean
  onPress: () => void
}

function fitChips(specs: ChipSpec[], avail: number): { specs: Fitted[]; pad: number } {
  const A = Math.max(8, Math.floor(avail))
  // padding is per-chip; drop it first when the row is too wide
  let pad = specs.length > 0 && measure(specs, 1) <= A ? 1 : 0
  // core text (no ellipsis) + whether we appended "…" — ellipsis replaces a
  // real column, so each shrink truly reduces measured width by 1.
  const core = specs.map((c) => c.label)
  const trunc = specs.map(() => false)
  const w = () =>
    core.reduce(
      (s, t, i) => s + t.length + (trunc[i] ? 1 : 0) + 2 + pad * 2,
      0,
    )
  let guard = 0
  while (w() > A && guard++ < 500) {
    // shrink the longest (prefer non-active so the current section/screen
    // keeps its full name as long as possible)
    let bi = -1
    let bv = -1
    for (let i = 0; i < core.length; i++) {
      const len = core[i].length + (trunc[i] ? 1 : 0)
      if (len > 1 && !specs[i].active && len > bv) {
        bv = len
        bi = i
      }
    }
    if (bi < 0) {
      // everything left is active or 1-col — fall back to longest overall
      for (let i = 0; i < core.length; i++) {
        const len = core[i].length + (trunc[i] ? 1 : 0)
        if (len > 1 && len > bv) {
          bv = len
          bi = i
        }
      }
    }
    if (bi < 0) break
    trunc[bi] = true
    core[bi] = core[bi].slice(0, -1)
  }
  const specsOut = specs.map((c, i) => ({
    label: trunc[i] ? core[i] + "…" : core[i],
    active: c.active,
    onPress: c.onPress,
  }))
  return { specs: specsOut, pad }
}

function ChipRow(props: { specs: ChipSpec[]; avail: number; activeColor?: string }) {
  const { specs, avail, activeColor = T.cyan } = props
  const { specs: fitted, pad } = fitChips(specs, avail)
  return (
    <box
      flexDirection="row"
      height={1}
      alignItems="center"
      paddingLeft={1}
      paddingRight={1}
      width="100%"
      flexShrink={0}
    >
      {fitted.map((c, i) => (
        <box
          key={i}
          onMouseDown={c.onPress}
          backgroundColor={c.active ? T.raised : T.panel}
          paddingLeft={pad}
          paddingRight={pad}
          flexShrink={0}
        >
          <text fg={c.active ? activeColor : T.dim}>
            {c.active ? `● ${c.label}` : `  ${c.label}`}
          </text>
        </box>
      ))}
    </box>
  )
}

// ── HeaderBar ───────────────────────────────────────────────────────────────
// Three single-line rows total (masthead / sections / screens) — no spacer,
// tagline, hint, or separator rows, so the content area owns the terminal.
export function HeaderBar(props: { onOpenHelp?: () => void }) {
  const mode = useDeck((s) => s.mode)
  const halted = useDeck((s) => s.halted)
  const now = useDeck((s) => s.now)
  const view = useDeck((s) => s.view)
  const transport = useDeck((s) => s.transport)
  const { width: termWidth } = useTerminalDimensions()
  const effW = termWidth > 0 ? termWidth : 120
  const avail = Math.max(8, effW - 4) // minus row padding L+R

  const modeConfig = halted ? MODE_CONFIG.HALTED : MODE_CONFIG[mode] ?? MODE_CONFIG.DEMO

  // Masthead tokens, measured in columns (emoji count 2 in JS .length here).
  const brand = "LLM TRADER"
  const tag = "· autonomous multi-agent trading"
  const searchT = "🔍 Search (/)"
  const helpT = "❓ Help (?)"
  const transportT = `● ${TRANSPORT_LABEL[transport] ?? "—"}`
  const clockT = `${fmtClock(now)} UTC`

  const W = (s: string) => s.length
  const boxW = (s: string) => s.length + 2 // 1 col padding each side
  // Right-hand cluster measured with gaps (1) between items.
  const coreRight = boxW(searchT) + 1 + boxW(helpT) + 1 + ` ${modeConfig.label} `.length
  const transportRight = boxW(transportT) + 1
  const clockRight = W(clockT) + 1

  // Decide which optional tokens fit: prefer tagline first (brand identity),
  // then transport, then clock — each only when everything stays on one line.
  const brandCore = W(brand) + 1 + coreRight
  let showTag = false
  let showTransport = false
  let showClock = false
  if (brandCore + W(tag) + 1 <= avail) {
    showTag = true
    const withTag = brandCore + W(tag) + 1
    if (withTag + transportRight + clockRight <= avail) {
      showTransport = true
      showClock = true
    } else if (withTag + transportRight <= avail) {
      showTransport = true
    }
  } else {
    // Brand alone already near the cap — transport/clock need their own room.
    const withT = brandCore
    if (withT + transportRight + clockRight <= avail) {
      showTransport = true
      showClock = true
    } else if (withT + transportRight <= avail) {
      showTransport = true
    }
  }

  const leftW =
    W(brand) + 1 + (showTag ? W(tag) + 1 : 0)
  const rightW =
    coreRight + (showTransport ? transportRight : 0) + (showClock ? clockRight : 0)
  const spacer = Math.max(1, avail - leftW - rightW - 2)

  const section = findSection(view)
  const sectionSpecs: ChipSpec[] = SECTIONS.map((s) => ({
    label: `${s.key} ${s.label}`,
    active: s.id === section?.id,
    onPress: () => {
      const first = s.views[0]?.id
      if (first) store.set({ view: first })
    },
  }))

  const screenSpecs: ChipSpec[] = (section?.views ?? []).map((v) => ({
    label: v.label,
    active: v.id === view,
    onPress: () => store.set({ view: v.id }),
  }))

  return (
    <box flexDirection="column" width="100%" backgroundColor={T.bg} flexShrink={0}>
      {/* Row 0 — masthead: brand + tagline left, search/help/mode right */}
      <box
        flexDirection="row"
        alignItems="center"
        paddingLeft={2}
        paddingRight={2}
        width="100%"
        height={1}
        flexShrink={0}
      >
        <text fg={T.bright}>
          <b>{brand}</b>
        </text>
        {showTag && <text fg={T.muted}> {tag}</text>}
        <box flexDirection="row" gap={1} alignItems="center" flexShrink={0} marginLeft={spacer}>
          <box
            onMouseDown={() => store.set({ paletteOpen: true })}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
          >
            <text fg={T.cyan}>{searchT}</text>
          </box>
          {props.onOpenHelp && (
            <box
              onMouseDown={props.onOpenHelp}
              backgroundColor={T.panel}
              paddingLeft={1}
              paddingRight={1}
            >
              <text fg={T.orange}>{helpT}</text>
            </box>
          )}
          <text fg={modeConfig.color} bg={modeConfig.bg}>
            {" "}{modeConfig.label}{" "}
          </text>
          {showTransport && <text fg={TRANSPORT_COLOR[transport]}>{transportT}</text>}
          {showClock && <text fg={T.dim}>| {clockT}</text>}
        </box>
      </box>

      {/* Row 1 — all six section tabs (click to jump to section's first screen) */}
      <ChipRow specs={sectionSpecs} avail={effW - 2} activeColor={T.cyan} />

      {/* Row 2 — screens of the active section (click to open) */}
      {screenSpecs.length > 0 && (
        <ChipRow specs={screenSpecs} avail={effW - 2} activeColor={T.teal} />
      )}
    </box>
  )
}
