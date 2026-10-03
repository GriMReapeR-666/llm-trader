export const T = {
  bg: "#0b0e14",
  panel: "#11161f",
  raised: "#1a2230",
  border: "#2a3446",
  borderBright: "#3d4c63",
  fg: "#d3dce8",
  bright: "#f2f6fc",
  dim: "#5b6a85",
  muted: "#8b99b3",
  cyan: "#4cc3ff",
  teal: "#3ddc97",
  purple: "#a78bfa",
  orange: "#f0a35e",
  up: "#3ddc97",
  down: "#f26d7d",
  warn: "#e8b44a",
  critical: "#f05252",
  neon: "#4cc3ff",
  glow: "#4cc3ff",
  accent: "#f0a35e",
  scanline: "#0b0e14",
  grid: "#0b0e14",
} as const

export const GLOW = {
  cyan: "●",
  teal: "●",
  purple: "●",
  orange: "●",
  up: "▲",
  down: "▼",
  warn: "!",
  critical: "×",
  neon: "●",
  pulse: "○",
} as const

export const SEPARATOR = {
  thin: "────────────────────────────────────────",
  thick: "────────────────────────────────────────",
  dotted: "· · · · · · · · · · · · · · · · · · · ·",
  double: "────────────────────────────────────────",
  corner: "────────────────────────────────────────",
  neon: "────────────────────────────────────────",
  glow: "────────────────────────────────────────",
} as const

export function sciBox(title: string, color: string): string {
  return `╔══ ${title} ${"═".repeat(Math.max(0, 50 - title.length))}╗`
}

export function sciBoxEnd(): string {
  return "╚══════════════════════════════════════════════════════╝"
}

export function sciPulse(pulse: boolean, active = "●", inactive = "○"): string {
  return pulse ? active : inactive
}

export function sciIndicator(value: number, thresholds: [number, string, string][]): string {
  for (const [threshold, label, color] of thresholds) {
    if (value >= threshold) return `${label}`
  }
  return thresholds[thresholds.length - 1]?.[1] ?? "—"
}

/** Worst-active-incident severity → deck color (P7 sidebar badge). */
export function incidentSeverityColor(status: string): string {
  switch (status) {
    case "CRITICAL": return T.critical
    case "WARNING": return T.warn
    case "OK": return T.up
    default: return T.dim
  }
}

/** One-glyph incident marker: ⛔ crit · ⚠ warn · ✓ ok · ○ unknown/offline. */
export function incidentSeverityGlyph(status: string): string {
  switch (status) {
    case "CRITICAL": return "⛔"
    case "WARNING": return "⚠"
    case "OK": return "✓"
    default: return "○"
  }
}

export const AGENT_COLORS: Record<string, string> = {
  TechnicalNet: T.cyan,
  MacroAnalyst: T.purple,
  MarketModel: T.warn,
  RiskController: T.teal,
  QuantitativeCoach: T.orange,
  EventAgent: "#89ddff",
  PortfolioAgent: "#c3a6ff",
  IncidentAgent: "#ff5370",
  ResearchAgent: "#7dcfff",
  SYSTEM: T.dim,
  OPERATOR: T.bright,
}

export const STATUS = {
  ok:       { icon: "✔",  label: "OK",       color: T.up },
  pass:     { icon: "✔",  label: "PASS",     color: T.up },
  done:     { icon: "✔",  label: "DONE",     color: T.up },
  running:  { icon: "●",  label: "RUNNING",  color: T.warn },
  active:   { icon: "●",  label: "ACTIVE",   color: T.cyan },
  queued:   { icon: "○",  label: "QUEUED",   color: T.dim },
  pending:  { icon: "○",  label: "PENDING",  color: T.dim },
  error:    { icon: "✗",  label: "ERROR",    color: T.down },
  fail:     { icon: "✗",  label: "FAIL",     color: T.down },
  reject:   { icon: "✘",  label: "REJECT",   color: T.down },
  critical: { icon: "⛔", label: "CRITICAL", color: T.critical },
  warn:     { icon: "⚠",  label: "WARN",     color: T.warn },
  info:     { icon: "ℹ",  label: "INFO",     color: T.dim },
  skipped:  { icon: "—",  label: "SKIPPED",  color: T.dim },
  clean:    { icon: "✓",  label: "CLEAN",    color: T.up },
  halted:   { icon: "✕",  label: "HALTED",   color: T.down },
  live:     { icon: "◉",  label: "LIVE",     color: T.teal },
  paper:    { icon: "◇",  label: "PAPER",    color: T.warn },
  demo:     { icon: "△",  label: "DEMO",     color: T.cyan },
} as const

export type StatusKey = keyof typeof STATUS

export const AGENT_ICONS: Record<string, string> = {
  TechnicalNet: "●",
  MacroAnalyst: "●",
  MarketModel: "●",
  RiskController: "●",
  QuantitativeCoach: "●",
  EventAgent: "●",
  PortfolioAgent: "●",
  IncidentAgent: "●",
  ResearchAgent: "●",
  SYSTEM: "■",
  OPERATOR: "▶",
}

import type { ViewId } from "./types"

export type { ViewId }

export interface Section {
  id: string
  label: string
  key: string
  icon: string
  views: { id: ViewId; label: string; key: string }[]
}

export const SECTIONS: Section[] = [
  {
    id: "watch", label: "MARKET", key: "1", icon: "◉",
    views: [
      { id: "stream", label: "FEED", key: "1" },
      { id: "cockpit", label: "COCKPIT", key: "2" },
      { id: "positions", label: "POSITIONS", key: "3" },
      { id: "journal", label: "JOURNAL", key: "4" },
      { id: "tasks", label: "TASKS", key: "5" },
      { id: "approvals", label: "APPROVALS", key: "6" },
    ],
  },
  {
    id: "think", label: "ANALYSIS", key: "2", icon: "◈",
    views: [
      { id: "brain", label: "BRAIN", key: "1" },
      { id: "causal", label: "CAUSAL", key: "2" },
      { id: "macro", label: "MACRO", key: "3" },
      { id: "strategies", label: "STRATEGIES", key: "4" },
      { id: "fusion", label: "FUSION", key: "5" },
    ],
  },
  {
    id: "pipeline", label: "PIPELINE", key: "3", icon: "△",
    views: [
      { id: "graph", label: "PIPELINE GRAPH", key: "1" },
      { id: "skills", label: "SKILLS", key: "2" },
      { id: "workflows", label: "WORKFLOWS", key: "3" },
      { id: "lifecycle", label: "LIFECYCLE", key: "4" },
    ],
  },
  {
    id: "research", label: "RESEARCH", key: "4", icon: "⊞",
    views: [
      { id: "analytics", label: "ANALYTICS", key: "1" },
      { id: "backtest", label: "BACKTEST", key: "2" },
      { id: "alpha", label: "ALPHA LAB", key: "3" },
      { id: "research", label: "RESEARCH PIPELINE", key: "4" },
      { id: "rl", label: "RL TRAINING", key: "5" },
      { id: "validation", label: "VALIDATION", key: "6" },
      { id: "memory", label: "MEMORY", key: "7" },
      { id: "deliberation", label: "DELIBERATION", key: "8" },
      { id: "orchestration", label: "ORCHESTRATION", key: "9" },
      { id: "observatory", label: "OBSERVATORY", key: "0" },
    ],
  },
  {
    id: "control", label: "CONTROL", key: "5", icon: "⊕",
    views: [
      { id: "autonomy", label: "AUTONOMY", key: "1" },
      { id: "rewards", label: "REWARD CREDITS", key: "2" },
      { id: "security", label: "SECURITY", key: "3" },
      { id: "growth", label: "GROWTH", key: "4" },
    ],
  },
  {
    id: "system", label: "SYSTEM", key: "6", icon: "⊡",
    views: [
      { id: "health", label: "SYSTEM HEALTH", key: "1" },
      { id: "settings", label: "CONFIG", key: "2" },
      { id: "models", label: "MODELS", key: "3" },
      { id: "metrics", label: "METRICS", key: "4" },
      { id: "drills", label: "DRILLS", key: "5" },
      { id: "datasources", label: "DATA SOURCES", key: "6" },
      { id: "scanner", label: "SCANNER", key: "7" },
      { id: "mt5", label: "MT5 BRIDGE", key: "8" },
      { id: "logs", label: "LOG MONITOR", key: "9" },
      { id: "diagnostics", label: "DIAGNOSTICS", key: "0" },
      { id: "map", label: "SYSTEM MAP", key: "-" },
    ],
  },
]

export const VIEWS: { id: ViewId; label: string; key: string }[] =
  SECTIONS.flatMap((s) => s.views)

export const SECTION_FIRST_VIEW: Record<string, ViewId> = {
  "1": "stream",
  "2": "brain",
  "3": "graph",
  "4": "analytics",
  "5": "autonomy",
  "6": "health",
}

export const VIEW_BY_KEY: Record<string, ViewId> = Object.fromEntries(
  VIEWS.map((v) => [v.key, v.id]),
)

export function findSection(viewId: ViewId): Section | undefined {
  return SECTIONS.find((s) => s.views.some((v) => v.id === viewId))
}

export function cycleScreenInSection(currentView: ViewId, dir: 1 | -1): ViewId {
  const section = findSection(currentView)
  if (!section || section.views.length === 0) return currentView
  const idx = section.views.findIndex((v) => v.id === currentView)
  const nextIdx = (idx + dir + section.views.length) % section.views.length
  return section.views[nextIdx].id
}
