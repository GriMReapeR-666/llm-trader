// Shared measured-width layout helpers.
//
// Screens that render dense rows (DAGs, tables, tree columns) cannot assume a
// fixed terminal width: when a `flexDirection="row"` is longer than its panel,
// OpenTUI overflows it horizontally — wrapping mid-glyph and bleeding across
// borders into neighbouring panels. Every budget below derives from the live
// terminal width and rows are laid out through `fit`/`fitRow` so nothing can
// exceed its measured panel at any size.

import { useTerminalDimensions } from "@opentui/react"

// App-shell chrome to the left of every screen (App.tsx layout):
// responsive sidebar + row gap (1) + page padding (1+1).
export const SIDEBAR_GAP = 1
export const PAGE_PAD = 2
export const MIN_CONTENT = 56
// columns eaten inside a panel by its own border/padding are already removed
// from `inner`; this extra reserve covers the scrollbox scrollbar column that
// overlays the last content column whenever the list overflows.
export const RES = 1

/**
 * Live sidebar width. Narrow terminals get a slimmer rail, wide terminals get
 * more room for the sections/screens list — so the shell follows the window
 * shape instead of overflowing or leaving dead space.
 */
export function useSidebarWidth(): number {
  const { width } = useTerminalDimensions()
  const w = width > 0 ? width : 120
  if (w < 100) return 23
  if (w < 130) return 26
  if (w < 170) return 28
  return 30
}

/** Total horizontal chrome (sidebar + gaps + page padding) a screen loses. */
export function useShellChrome(): number {
  return useSidebarWidth() + SIDEBAR_GAP + PAGE_PAD
}

/** Live terminal width minus app chrome — the content width a screen owns. */
export function useContentWidth(): number {
  const { width } = useTerminalDimensions()
  const eff = width > 0 ? width : 120
  return Math.max(MIN_CONTENT, eff - useShellChrome())
}

// Display width of a string in terminal columns. Counts East Asian wide
// glyphs, emoji and variation-selector sequences as 2 columns so wrapping
// budgets never under-wrap and bleed across panel borders.
const WIDE_RE =
  /[\u1100-\u115F\u2E80-\u303E\u3041-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uA000-\uA4CF\uA960-\uA97F\uAC00-\uD7A3\uF900-\uFAFF\uFE10-\uFE19\uFE30-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6\u{1F300}-\u{1F9FF}\u{1FA70}-\u{1FAFF}\u{1F000}-\u{1F2FF}]/u

export function strWidth(s: string): number {
  let w = 0
  for (const ch of s) w += WIDE_RE.test(ch) ? 2 : 1
  return w
}

/** Hard-wrap a single line to `max` display columns (safe for panels). */
export function chunkText(text: string, max: number): string[] {
  const w = Math.max(8, Math.floor(max))
  if (strWidth(text) <= w) return [text]
  const out: string[] = []
  let rest = text
  let cur = ""
  let curW = 0
  for (const ch of rest) {
    const cw = WIDE_RE.test(ch) ? 2 : 1
    if (curW + cw > w) {
      out.push(cur)
      cur = ch
      curW = cw
    } else {
      cur += ch
      curW += cw
    }
  }
  if (cur.length > 0) out.push(cur)
  return out
}

/** Hard-wrap many lines; long lines are chopped, short lines pass through. */
export function chunkLines(lines: string[], max: number): string[] {
  const out: string[] = []
  for (const l of lines) out.push(...chunkText(l, max))
  return out
}

export function clip(s: string, n: number): string {
  const w = Math.max(1, Math.floor(n))
  if (strWidth(s) <= w) return s
  let out = ""
  let used = 0
  for (const ch of s) {
    const cw = strWidth(ch)
    if (used + cw > w - 1) break
    out += ch
    used += cw
  }
  return out + "…"
}

export function pad(s: string, n: number): string {
  const w = Math.max(0, Math.floor(n))
  return s.length >= w ? s : s + " ".repeat(w - s.length)
}

export interface Seg {
  t: string
  fg?: string
  bg?: string
}

/**
 * Lay tokens on one row up to `avail` columns; the first token that would
 * overflow is truncated with an ellipsis and the rest are dropped. Keeps every
 * row inside its measured panel at any terminal width.
 */
export function fit(avail: number, segs: Seg[]): Seg[] {
  const max = Math.max(4, Math.floor(avail))
  let used = 0
  const out: Seg[] = []
  for (const s of segs) {
    const w = strWidth(s.t)
    if (w === 0) continue
    if (used + w > max) {
      const room = max - used
      if (room >= 1) out.push({ ...s, t: clip(s.t, room) })
      break
    }
    out.push(s)
    used += w
  }
  return out
}

export function fitRow(avail: number, segs: Seg[]) {
  return (
    <box flexDirection="row">
      {fit(avail, segs).map((s, i) => (
        <text key={i} fg={s.fg} bg={s.bg}>
          {s.t}
        </text>
      ))}
    </box>
  )
}

/**
 * Chunk tokens into as many full rows as needed (like fit, but wraps instead
 * of dropping the tail). Each chunk still fits `avail` and is safe to render
 * through fitRow — data is preserved, nothing crosses a panel border.
 */
export function wrapSegs(avail: number, segs: Seg[], indent = 0): Seg[][] {
  const max = Math.max(4, Math.floor(avail))
  const lines: Seg[][] = []
  let cur: Seg[] = []
  let used = 0
  for (const s of segs) {
    const w = strWidth(s.t)
    if (w === 0) continue
    if (used + w > max && cur.length > 0) {
      lines.push(cur)
      cur = indent > 0 ? [{ t: " ".repeat(indent) }] : []
      used = indent
    }
    cur.push(s)
    used += w
  }
  if (cur.length > 0) lines.push(cur)
  return lines
}

export { SafetyStrip } from "./components/SafetyStrip"
