import { useDeck } from "../store"
import { T } from "../theme"
import { AgentRosterCompact } from "./AgentRoster"
import { sciBar } from "../format"

export function StatusBar() {
  const budgetUsed = useDeck((s) => s.llmBudgetUsed)
  const budgetCap = useDeck((s) => s.llmBudgetCap)
  const schedulesDue = useDeck((s) => s.schedulesDue)
  const queueHealth = useDeck((s) => s.queueHealth)
  const view = useDeck((s) => s.view)
  const promptFocused = useDeck((s) => s.promptFocused)
  const autonomyLevel = useDeck((s) => s.autonomyLevel)
  const pendingApprovals = useDeck((s) => s.approvals.filter((a) => a.status === "PENDING").length)
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 600) % 2 === 0

  const pct = budgetCap === 0 ? 0 : budgetUsed / budgetCap
  const qColor = queueHealth === "OK" ? T.up : T.warn
  const levelColor =
    autonomyLevel === "L3_FULL" ? T.down : autonomyLevel === "L2_MICRO" ? T.warn : T.teal
  const focusHint =
    view === "stream" && promptFocused ? "ESC release prompt" : promptFocused ? "" : "1-6 sections · / palette"

  void now
  void pulse
  const budgetBar = sciBar(pct, 6, "■", "─")

  return (
    <box flexDirection="row" backgroundColor={T.panel} paddingLeft={1} paddingRight={1} gap={1} height={1}>
      <AgentRosterCompact />
      <text fg={T.dim}>|</text>
      <text fg={levelColor}>{autonomyLevel.replace("_", " ")}</text>
      <text fg={T.dim}>|</text>
      <text fg={pendingApprovals > 0 ? T.warn : T.dim}>
        appr {pendingApprovals}
      </text>
      <text fg={T.dim}>|</text>
      <text fg={T.muted}>
        LLM {budgetUsed}/{budgetCap} <span fg={pct > 0.8 ? T.down : T.muted}>{budgetBar}</span>
      </text>
      <text fg={T.dim}>|</text>
      <text fg={schedulesDue > 0 ? T.warn : T.dim}>sched {schedulesDue}</text>
      <text fg={T.dim}>|</text>
      <text fg={qColor}>queue {queueHealth}</text>
      <box flexGrow={1} />
      <text fg={T.dim}>{focusHint}</text>
    </box>
  )
}
