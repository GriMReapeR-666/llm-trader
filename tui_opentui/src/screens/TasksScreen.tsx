import { useState } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { fmtClock } from "../format"
import { T, AGENT_COLORS, SEPARATOR, sciPulse } from "../theme"
import { TabBar } from "../components/TabBar"

const STATUS_ICON: Record<string, string> = {
  queued: "○",
  running: "▶",
  done: "✔",
  error: "✘",
  skipped: "⊘",
}

function statusColor(status: string, pulse: boolean): string {
  switch (status) {
    case "running":
      return pulse ? T.warn : "#a5843f"
    case "done":
      return T.up
    case "error":
      return T.down
    case "skipped":
      return T.dim
    default:
      return T.borderBright
  }
}

export function tasksMove(delta: number): void {
  const n = store.get().tasks.length
  if (n === 0) return
  const cur = store.get().selectedTask
  store.set({ selectedTask: Math.max(0, Math.min(n - 1, cur + delta)) })
}

const STATUS_TABS = ["all", "queued", "running", "done", "error", "skipped"]

export function TasksScreen() {
  const tasks = useDeck((s) => s.tasks)
  const selected = Math.min(useDeck((s) => s.selectedTask), Math.max(0, tasks.length - 1))
  const toolCalls = useDeck((s) => s.toolCalls)
  const loop = useDeck((s) => s.loopStats)
  const now = useDeck((s) => s.now)
  const pulse = Math.floor(now / 700) % 2 === 0
  const currentTask = tasks[selected]
  const stageCalls = toolCalls.filter((c) => currentTask && c.taskId === currentTask.id)
  const shown = stageCalls.length > 0 ? stageCalls : toolCalls.slice(-14).reverse()
  const lastAge = loop.lastCycleTs ? Math.round((now - loop.lastCycleTs) / 1000) : null
  const [statusFilter, setStatusFilter] = useState(0)

  const filtered = statusFilter === 0 ? tasks : tasks.filter((t) => t.status === STATUS_TABS[statusFilter])
  const running = tasks.find((t) => t.status === "running")
  const dagLine = tasks.length
    ? tasks.slice(-6).map((t) => `${t.status === "running" ? (pulse ? "▶" : "▷") : t.status === "done" ? "✔" : t.status === "error" ? "✘" : "○"} ${t.stage.slice(0, 10)}`).join(` ─▶ `)
    : "○ tech_net ─▶ ○ macro ─▶ ○ memory ─▶ ○ market ─▶ ○ arbitrate ─▶ ○ debate"

  useKeyboard((key) => {
    if (key.name === "up") {
      tasksMove(-1)
      return
    }
    if (key.name === "down") {
      tasksMove(1)
      return
    }
    if (key.name === "1") { setStatusFilter(0); return }
    if (key.name === "2") { setStatusFilter(1); return }
    if (key.name === "3") { setStatusFilter(2); return }
    if (key.name === "4") { setStatusFilter(3); return }
    if (key.name === "5") { setStatusFilter(4); return }
    if (key.name === "6") { setStatusFilter(5); return }
    if (key.name === "left") {
      setStatusFilter((f) => (f - 1 + STATUS_TABS.length) % STATUS_TABS.length)
      return
    }
    if (key.name === "right") {
      setStatusFilter((f) => (f + 1) % STATUS_TABS.length)
      return
    }
  })

  const tabOptions = STATUS_TABS.map((s) => ({
    name: s,
    badge: s === "all" ? tasks.length : tasks.filter((t) => t.status === s).length,
  }))

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title={` ◈ MAIN LOOP ${running ? `· ${sciPulse(pulse, "◉ LIVE", "○ SYNC")} ${running.stage}` : ""} `}
        titleColor={running ? T.cyan : T.dim}
        borderStyle="single"
        borderColor={running ? T.cyan : T.border}
        flexDirection="column"
        width={46}
        flexShrink={0}
        paddingLeft={1}
      >
        <box flexDirection="column" paddingBottom={0}>
          <text fg={T.muted}>
            cycles {loop.cyclesCompleted}
            {loop.avgCycleMs > 0 ? ` · avg ${(loop.avgCycleMs / 1000).toFixed(1)}s` : ""}
            {lastAge !== null ? ` · last ${lastAge}s ago` : ""}
          </text>
          <text fg={T.dim}>{currentTask ? `cycle trace · ${fmtClock(currentTask.startedTs ?? Date.now())}` : "waiting for cycle…"}</text>
        </box>
        <TabBar
          tabs={tabOptions}
          selected={statusFilter}
          onChange={setStatusFilter}
          activeColor={T.cyan}
        />
        <scrollbox
          flexGrow={1}
          viewportCulling
          paddingRight={1}
          scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
        >
          {filtered.length === 0 ? (
            <box paddingLeft={1} paddingTop={1}>
              <text fg={T.dim}>no {STATUS_TABS[statusFilter]} stages in current trace</text>
            </box>
          ) : (
            filtered.map((t) => {
              const origIdx = tasks.indexOf(t)
              const isSelected = origIdx === selected
              const statusCol = statusColor(t.status, pulse)
              return (
                <box
                  key={t.id}
                  onMouseDown={() => store.set({ selectedTask: origIdx })}
                  backgroundColor={isSelected ? T.raised : undefined}
                  borderStyle={isSelected ? "single" : undefined}
                  borderColor={isSelected ? T.cyan : undefined}
                  flexDirection="column"
                  paddingLeft={1}
                  paddingRight={1}
                  marginBottom={0}
                >
                  <box flexDirection="row" gap={1} alignItems="center">
                    <text fg={isSelected ? T.cyan : T.dim}>{isSelected ? "▶" : " "}</text>
                    <text fg={statusCol}>{STATUS_ICON[t.status]}</text>
                    <text fg={isSelected ? T.bright : T.fg}><b>{t.stage}</b></text>
                    {t.durationMs !== undefined && (
                      <text fg={T.muted}>({(t.durationMs / 1000).toFixed(1)}s)</text>
                    )}
                  </box>
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.teal}>{t.agent.slice(0, 11)}</text>
                    <text fg={T.dim}> · </text>
                    <text fg={isSelected ? T.bright : T.muted}>{t.label}</text>
                  </box>
                </box>
              )
            })
          )}
        </scrollbox>
        <text fg={T.dim}>select stage · right pane shows its tool calls</text>
        <text fg={T.borderBright}>{SEPARATOR.thin}</text>
        <text fg={T.cyan}>◈ LANGFLOW DAG (live)</text>
        <text fg={running ? (pulse ? T.cyan : T.warn) : T.dim}>{dagLine}</text>
        <text fg={T.muted}>{running ? `executing ${running.agent} · ${running.durationMs ?? 0}ms` : "idle — waiting for cycle"}</text>
      </box>

      <box
        title=" ◈ TOOL CALLS & SPANS "
        titleColor={T.teal}
        borderStyle="single"
        borderColor={T.teal}
        flexDirection="column"
        flexGrow={1}
      >
        <scrollbox
          flexGrow={1}
          stickyScroll
          stickyStart="bottom"
          viewportCulling
          paddingLeft={1}
          scrollbarOptions={{
            showArrows: false,
            trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
          }}
        >
          {shown.length === 0 && <text fg={T.dim}>no tool calls yet…</text>}
          {shown.map((c) => (
            <box key={`${c.id}-${c.ts}`} flexDirection="column" marginBottom={0}>
              <box flexDirection="row">
                <text fg={c.ok ? T.teal : T.down}>⚙ </text>
                <text fg={T.bright}>{c.tool}</text>
                <text fg={T.dim}> ({c.latencyMs}ms{c.tokens ? ` · ${c.tokens}tok` : ""})</text>
              </box>
              <box flexDirection="row" paddingLeft={2}>
                <text fg={T.dim}>in: </text>
                <text fg={T.muted}>{c.argsSummary}</text>
              </box>
              <box flexDirection="row" paddingLeft={2}>
                <text fg={T.dim}>→ </text>
                <text fg={T.fg}>{c.resultSummary}</text>
              </box>
              <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            </box>
          ))}
        </scrollbox>
      </box>
    </box>
  )
}
