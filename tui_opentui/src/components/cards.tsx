import type { StreamItem, StreamKind } from "../types"
import { fmtClock } from "../format"
import { T, AGENT_COLORS } from "../theme"
import { chunkLines, clip, strWidth } from "../layout"
// ---------------------------------------------------------------------------
// Kind metadata — single source of truth for card chips and toolbar filters
// ---------------------------------------------------------------------------
export const KIND_META: Record<StreamKind, { label: string; color: string }> = {
  system:      { label: "SYS",     color: T.muted },
  agent_note:  { label: "NOTE",    color: T.purple },
  tool:        { label: "TOOL",    color: T.orange },
  thinking:    { label: "THINK",   color: T.dim },
  decision:    { label: "DECIDE",  color: T.neon },
  order:       { label: "ORDER",   color: T.orange },
  validation:  { label: "GATE",    color: T.teal },
  fill:        { label: "FILL",    color: T.up },
  alert:       { label: "ALERT",   color: T.warn },
  user:        { label: "YOU",     color: T.bright },
}

export const KIND_ORDER: StreamKind[] = [
  "decision", "order", "fill", "validation", "alert", "system", "tool", "thinking", "agent_note", "user",
]

// ---------------------------------------------------------------------------
// Expand / collapse — an event is collapsible when it carries > 1 body line,
// counting the plain body plus any structured reasoning / intent diff rows.
// Events absent from the override map follow the default: multi-line events
// arrive folded to a single row and expand on demand.
// ---------------------------------------------------------------------------
export function bodyLineCount(item: StreamItem): number {
  return item.lines.length + (item.reasoning?.length ?? 0) + (item.intentDiff?.length ?? 0)
}

export function isCollapsible(item: StreamItem): boolean {
  return bodyLineCount(item) > 1
}

export function streamItemExpanded(item: StreamItem, overrides: Record<number, boolean>): boolean {
  if (overrides[item.id] !== undefined) return overrides[item.id]
  // Decisions carry reasoning + intent diff — open them by default so the
  // agent's rationale is visible in the feed (operator can still fold).
  if (item.kind === "decision") return true
  return !isCollapsible(item)
}

function FoldChip(props: { expanded: boolean; hidden: number; onToggle: () => void }) {
  return (
    <box
      onMouseDown={props.onToggle}
      backgroundColor={T.raised}
      paddingLeft={1}
      paddingRight={1}
      flexShrink={0}
    >
      <text fg={T.cyan}>{props.expanded ? "▾ fold" : `▸ ${props.hidden} more`}</text>
    </box>
  )
}

function KindChip(props: { kind: StreamKind; severity?: "info" | "warn" | "critical" }) {
  const meta = KIND_META[props.kind]
  const color =
    props.kind === "alert" && props.severity === "critical"
      ? T.critical
      : props.kind === "alert" && props.severity === "warn"
        ? T.warn
        : meta.color
  return (
    <text bg={T.raised} fg={color}>
      {` ${meta.label} `}
    </text>
  )
}

function AgentLine(props: { agent: string; ts?: number; title?: string }) {
  const color = AGENT_COLORS[props.agent] ?? T.fg
  return (
    <box flexDirection="row" gap={1}>
      <text fg={color}>● {props.agent}</text>
      {props.ts !== undefined && <text fg={T.dim}>{fmtClock(props.ts)}</text>}
      {props.title && <text fg={T.dim}>{props.title}</text>}
    </box>
  )
}

// ---------------------------------------------------------------------------
// Reasoning / diff rendering — decision cards carry the agent's reasoning
// trail and the proposed→validated intent diff; both are sliced into safe
// width chunks so long lines never bleed across panel borders.
// ---------------------------------------------------------------------------
interface StyledLine {
  text: string
  fg: string
}

function reasonLines(lines: string[], width: number): StyledLine[] {
  const w = Math.max(8, Math.floor(width))
  const out: StyledLine[] = []
  for (const raw of lines) {
    const line = raw.trimEnd()
    const chunks = chunkLines([line], w)
    for (let i = 0; i < chunks.length; i++) {
      const c = chunks[i]
      const t = c.trimStart()
      let text = c
      let fg: string = T.dim
      if (t.startsWith("### ")) {
        text = c.replace(/^(\s*)(###)\s+/, "$1▌ ")
        fg = T.bright
      } else if (t.startsWith("## ")) {
        text = c.replace(/^(\s*)(##)\s+/, "$1▌ ")
        fg = T.cyan
      } else if (t.startsWith("# ")) {
        text = c.replace(/^(\s*)(#)\s+/, "$1▌ ")
        fg = T.bright
      } else if (/^[-•*] /.test(t)) {
        text = c.replace(/^(\s*)[-•*] /, "$1· ")
        fg = T.fg
      } else if (t === "") {
        text = " "
      } else {
        text = c.replace(/\*\*/g, "")
      }
      out.push({ text, fg })
    }
  }
  return out
}

function diffLines(lines: string[], width: number): StyledLine[] {
  const w = Math.max(8, Math.floor(width))
  const out: StyledLine[] = []
  for (const raw of lines) {
    for (const c of chunkLines([raw.trimEnd()], w)) {
      if (c.startsWith("+")) out.push({ text: c, fg: T.up })
      else if (c.startsWith("-")) out.push({ text: c, fg: T.down })
      else if (c.startsWith("@@")) out.push({ text: c, fg: T.warn })
      else out.push({ text: c, fg: T.muted })
    }
  }
  return out
}

export function StreamItemView(props: {
  item: StreamItem
  expanded: boolean
  onToggle: () => void
  /** Measured content width — hard-wraps long lines so nothing overflows. */
  width?: number
}) {
  const { item, expanded, onToggle, width } = props
  const agentColor = AGENT_COLORS[item.agent] ?? T.fg

  const collapsible = isCollapsible(item)
  const showBody = !collapsible || expanded
  const foldChip = collapsible ? (
    <FoldChip expanded={expanded} hidden={bodyLineCount(item)} onToggle={onToggle} />
  ) : null

  // max render columns for bare lines (2-col safety margin), for lines inside
  // bordered cards (card border 2 + padding 2 → inner width-4, minus 4 more
  // for breathing room) and for lines inside the *nested* reasoning / intent
  // diff panels (card chrome 4 + panel border 2 + panel padding 4 → real text
  // area is width-10; chunk to width-12 so nothing can ever touch the panel
  // border).
  const plainW = width ? width - 2 : width
  const boxedW = width ? width - 8 : width
  const panelW = width ? width - 12 : width

  // Fold chip footprint (text + 1 col padding each side) — reserved from every
  // header-row budget so the chip can never push the title across a border.
  const foldW = collapsible
    ? (expanded ? strWidth("▾ fold") : strWidth(`▸ ${bodyLineCount(item)} more`)) + 2
    : 0
  const chipW = (k: StreamKind) => strWidth(` ${KIND_META[k].label} `)
  // Title budget inside a bordered card row (inner = width-4, 1 col safety).
  const borderedTitle = (k: StreamKind, extra = 0): number | undefined =>
    width ? Math.max(6, width - 5 - chipW(k) - extra - foldW - 2) : undefined
  // Title budget inside an unbordered feed row (plainW minus the row's other
  // segments and gaps).
  const plainTitle = (k: StreamKind, extra = 0): number | undefined =>
    width ? Math.max(6, width - 4 - chipW(k) - extra - foldW - 2) : undefined

  const bodyLines = (fg: string, prefix = "", w?: number) => {
    if (!showBody || item.lines.length === 0) return null
    const avail = w ?? plainW
    const effW = prefix.length > 0 && avail ? avail - prefix.length : avail
    const lines = effW && effW > 0 ? chunkLines(item.lines, effW) : item.lines
    return lines.map((l, i) => (
      <text key={i} fg={fg}>
        {prefix}
        {l}
      </text>
    ))
  }

  const bodyBlock = (fg: string, prefix = "") => (
    <box flexDirection="column">{bodyLines(fg, prefix)}</box>
  )
  void bodyBlock

  const reasonPanel = showBody && item.reasoning && item.reasoning.length > 0 ? (
    <box
      flexDirection="column"
      marginTop={1}
      marginBottom={1}
      borderStyle="single"
      borderColor={T.border}
      backgroundColor={T.panel}
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
    >
      <box flexDirection="row" gap={1} alignItems="center" marginBottom={1}>
        <text fg={T.cyan}>▸ reasoning</text>
        <text fg={T.border}>
          {"─".repeat(Math.max(0, (width ? width - 10 : 20) - strWidth("▸ reasoning") - 1))}
        </text>
      </box>
      {reasonLines(item.reasoning, panelW ?? 0).map((r, i) => (
        <text key={i} fg={r.fg}>
          {r.text}
        </text>
      ))}
    </box>
  ) : null

  const intentPanel = showBody && item.intentDiff && item.intentDiff.length > 0 ? (
    <box
      flexDirection="column"
      marginTop={0}
      marginBottom={1}
      borderStyle="single"
      borderColor={T.borderBright}
      backgroundColor={T.panel}
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
    >
      <box flexDirection="row" gap={1} alignItems="center" marginBottom={1}>
        <text fg={T.orange}>▸ intent diff · proposed → validated</text>
        <text fg={T.border}>
          {"─".repeat(
            Math.max(
              0,
              (width ? width - 10 : 40) -
                strWidth("▸ intent diff · proposed → validated") -
                1,
            ),
          )}
        </text>
      </box>
      {diffLines(item.intentDiff, panelW ?? 0).map((r, i) => (
        <text key={i} fg={r.fg}>
          {r.text}
        </text>
      ))}
    </box>
  ) : null

  switch (item.kind) {
    case "system":
      return (
        <box
          flexDirection="column"
          marginBottom={2}
          marginTop={1}
          borderStyle="single"
          borderColor={T.border}
          backgroundColor={T.panel}
          paddingLeft={1}
          paddingRight={1}
        >
          <box flexDirection="row" gap={1} alignItems="center">
            <KindChip kind="system" />
            <text fg={T.muted}>{borderedTitle("system") ? clip(item.title, borderedTitle("system")!) : item.title}</text>
            {foldChip}
          </box>
          {bodyLines(T.dim, "", boxedW)}
        </box>
      )

    case "decision": {
      const sell = /SELL/i.test(item.title)
      const buy = /BUY/i.test(item.title)
      const accent = buy ? T.up : sell ? T.down : T.warn
      const decisionLabel = buy ? "BUY" : sell ? "SELL" : "HOLD"
      return (
        <box flexDirection="column" marginBottom={2}>
          <AgentLine agent={item.agent} ts={item.ts} />
          <box
            borderStyle="single"
            borderColor={accent === T.warn ? T.border : accent}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
            flexDirection="column"
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <KindChip kind="decision" />
              <text fg={accent}>{decisionLabel}</text>
              <text fg={T.bright}>
                {borderedTitle("decision", strWidth(decisionLabel) + 1)
                  ? clip(item.title, borderedTitle("decision", strWidth(decisionLabel) + 1)!)
                  : item.title}
              </text>
              {foldChip}
            </box>
            {bodyLines(T.muted, "  ", boxedW)}
            {reasonPanel}
            {intentPanel}
          </box>
        </box>
      )
    }

    case "order":
    case "fill": {
      const isFill = item.kind === "fill"
      return (
        <box flexDirection="column" marginBottom={2}>
          <AgentLine agent={item.agent} ts={item.ts} />
          <box
            borderStyle="single"
            borderColor={isFill ? T.up : T.teal}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
            flexDirection="column"
          >
            <box flexDirection="row" gap={1} alignItems="center">
              <KindChip kind={item.kind} />
              <text fg={isFill ? T.up : T.teal}>{isFill ? "FILLED" : "ROUTED"}</text>
              <text fg={T.bright}>
                {borderedTitle(item.kind, strWidth(isFill ? "FILLED" : "ROUTED") + 1)
                  ? clip(item.title, borderedTitle(item.kind, strWidth(isFill ? "FILLED" : "ROUTED") + 1)!)
                  : item.title}
              </text>
              {foldChip}
            </box>
            {bodyLines(T.muted, "  ", boxedW)}
          </box>
        </box>
      )
    }

    case "validation": {
      const pass = !/REJECT|FAIL/i.test(item.title)
      return (
        <box flexDirection="column" marginBottom={2}>
          <AgentLine agent={item.agent} ts={item.ts} />
          <box flexDirection="row" gap={1} alignItems="center" paddingLeft={1}>
            <KindChip kind="validation" />
            <text fg={pass ? T.up : T.down}>{pass ? "PASS" : "REJECT"}</text>
            <text fg={T.fg}>
              {plainTitle("validation", strWidth(pass ? "PASS" : "REJECT") + 1)
                ? clip(item.title, plainTitle("validation", strWidth(pass ? "PASS" : "REJECT") + 1)!)
                : item.title}
            </text>
            {foldChip}
          </box>
          {bodyLines(T.dim, "  ")}
        </box>
      )
    }

    case "tool":
      return (
        <box flexDirection="column" marginBottom={2}>
          <box flexDirection="row" gap={1} alignItems="center">
            <KindChip kind="tool" />
            <text fg={agentColor}>● {item.agent}</text>
            <text fg={T.bright}>
              {plainTitle("tool", strWidth(item.agent) + 1 + 9)
                ? clip(item.title, plainTitle("tool", strWidth(item.agent) + 1 + 9)!)
                : item.title}
            </text>
            <text fg={T.dim}>{item.ts ? fmtClock(item.ts) : ""}</text>
            {foldChip}
          </box>
          {bodyLines(T.muted, "  ")}
        </box>
      )

    case "thinking":
      return (
        <box flexDirection="column" marginBottom={2} paddingLeft={1}>
          <box flexDirection="row" gap={1} alignItems="center">
            <KindChip kind="thinking" />
            <text fg={agentColor}>● {item.agent}</text>
            <text fg={T.dim}>{item.ts ? fmtClock(item.ts) : ""}</text>
            {item.title !== "reasoning…" && item.title ? (
              <text fg={T.dim}>
                {plainTitle("thinking", strWidth(item.agent) + 1 + 9)
                  ? clip(item.title, plainTitle("thinking", strWidth(item.agent) + 1 + 9)!)
                  : item.title}
              </text>
            ) : (
              <text fg={T.muted}>reasoning</text>
            )}
            {foldChip}
          </box>
          <box flexDirection="row" paddingLeft={2}>
            <text fg={T.borderBright}>│ </text>
            <box flexDirection="column">{bodyLines(T.dim, "", plainW ? plainW - 6 : plainW)}</box>
          </box>
        </box>
      )

    case "agent_note":
      return (
        <box flexDirection="column" marginBottom={2}>
          <box flexDirection="row" gap={1} alignItems="center">
            <KindChip kind="agent_note" />
            <text fg={agentColor}>● {item.agent}</text>
            <text fg={T.dim}>
              {plainTitle("agent_note", strWidth(item.agent) + 1)
                ? clip(item.title, plainTitle("agent_note", strWidth(item.agent) + 1)!)
                : item.title}
            </text>
            {foldChip}
          </box>
          {bodyLines(T.dim, "  ")}
        </box>
      )

    case "alert":
      return (
        <box
          flexDirection="column"
          marginBottom={2}
          borderStyle="single"
          borderColor={item.severity === "critical" ? T.critical : T.warn}
          backgroundColor={T.panel}
          paddingLeft={1}
          paddingRight={1}
        >
          <box flexDirection="row" gap={1} alignItems="center">
            <KindChip kind="alert" severity={item.severity} />
            <text fg={item.severity === "critical" ? T.critical : T.warn}>
              {borderedTitle("alert", strWidth(item.severity === "critical" ? "Critical" : "Warning") + 3)
                ? clip(
                    `${item.severity === "critical" ? "Critical" : "Warning"} · ${item.title}`,
                    borderedTitle("alert", strWidth(item.severity === "critical" ? "Critical" : "Warning") + 3)!,
                  )
                : `${item.severity === "critical" ? "Critical" : "Warning"} · ${item.title}`}
            </text>
            {foldChip}
          </box>
          {bodyLines(T.muted, "", boxedW)}
        </box>
      )

    case "user":
      return (
        <box flexDirection="row" marginBottom={0} gap={1} alignItems="center">
          <KindChip kind="user" />
          <text fg={T.cyan}>Operator</text>
          <text fg={T.dim}>›</text>
          <text fg={T.bright}>
            {plainTitle("user", strWidth("Operator") + 1)
              ? clip(item.lines[0] ?? item.title, plainTitle("user", strWidth("Operator") + 1)!)
              : item.lines[0] ?? item.title}
          </text>
          {foldChip}
        </box>
      )

    default:
      return (
        <box flexDirection="column" marginBottom={0}>
          <box flexDirection="row" gap={1} alignItems="center">
            <AgentLine agent={item.agent} ts={item.ts} />
            <text fg={T.muted}>
              {plainTitle("system", strWidth(item.agent) + 1 + 9)
                ? clip(item.title, plainTitle("system", strWidth(item.agent) + 1 + 9)!)
                : item.title}
            </text>
            {foldChip}
          </box>
          {bodyLines(T.dim, "  ")}
        </box>
      )
  }
}
