import { useDeck, store } from "../store"
import { T, SEPARATOR } from "../theme"
import { fmtClock } from "../format"
import { clip, useContentWidth } from "../layout"
import type { Skill } from "../types"

const CAT_COLOR: Record<Skill["category"], string> = {
  analysis: T.cyan,
  execution: T.up,
  risk: T.down,
  data: T.warn,
  research: T.purple,
  memory: T.teal,
}

export function skillsMove(delta: number): void {
  const n = store.get().skills.length
  if (n === 0) return
  store.set({ selectedSkill: Math.max(0, Math.min(n - 1, store.get().selectedSkill + delta)) })
}

export function SkillsScreen() {
  const skills = useDeck((s) => s.skills)
  const execs = useDeck((s) => s.skillExecutions)
  const selected = Math.min(useDeck((s) => s.selectedSkill), Math.max(0, skills.length - 1))
  const s = skills[selected]
  const recentExecs = s ? execs.filter((e) => e.skillId === s.id).slice(-30) : []
  const contentW = useContentWidth()

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title="SKILL REGISTRY"
        titleColor={T.cyan}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        width={34}
        flexShrink={0}
        paddingLeft={1}
      >
        <select
          flexGrow={1}
          focused
          options={skills.map((x) => ({
            name: x.name,
            description: `${x.category} · ${x.status} · ${x.executions} runs · ${x.successRate.toFixed(0)}%`,
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
        <text fg={T.dim}>{skills.filter((x) => x.embedded).length} embedded · {skills.filter((x) => !x.embedded).length} orchestrated</text>
      </box>

      <box
        title={s ? `╔══ ${clip(s.name.toUpperCase(), 30)} ══╗` : "╔══ — ══╗"}
        titleColor={s ? T.cyan : T.cyan}
        borderStyle="single"
        borderColor={T.border}
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
              <text fg={T.bg} bg={CAT_COLOR[s.category]}> {s.category.toUpperCase()} </text>
              <text fg={T.bg} bg={s.embedded ? T.teal : T.purple}> {s.embedded ? "EMBEDDED" : "ORCHESTRATED"} </text>
              <text fg={s.status === "active" ? T.up : s.status === "error" ? T.down : T.dim}>◉ {s.status}</text>
            </box>
            <text fg={T.muted}>{s.description}</text>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <Stat label="total executions" value={String(s.executions)} color={T.fg} />
            <Stat label="avg latency" value={`${s.avgLatencyMs}ms`} color={s.avgLatencyMs < 100 ? T.up : s.avgLatencyMs < 500 ? T.warn : T.down} />
            <Stat label="success rate" value={`${s.successRate.toFixed(1)}%`} color={s.successRate >= 95 ? T.up : s.successRate >= 85 ? T.warn : T.down} />
            <Stat label="last run" value={s.lastRunTs ? fmtClock(s.lastRunTs) : "never"} color={T.muted} />
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <box flexDirection="row" justifyContent="space-between">
              <text fg={T.cyan}>◈ RECENT EXECUTION LOGS ({recentExecs.length})</text>
              <text fg={T.dim}>STATUS | TIME | INPUT | LATENCY | DETAIL</text>
            </box>
            {recentExecs.length === 0 && <text fg={T.dim}>  no executions yet</text>}
            {recentExecs.map((e) => (
              <box key={e.id} flexDirection="row" gap={1}>
                <text fg={e.ok ? T.up : T.down}>{e.ok ? "✓" : "✗"}</text>
                <text fg={T.dim}>{fmtClock(e.ts)}</text>
                <text fg={T.fg}>{e.input.slice(0, 32)}</text>
                <text fg={T.teal}>{e.latencyMs}ms</text>
                {e.error ? <text fg={T.down}>{e.error.slice(0, 36)}</text> : <text fg={T.dim}>OK</text>}
              </box>
            ))}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            <text fg={T.cyan}>◈ CAPABILITIES</text>
            <text fg={T.muted}>· {clip(s.category === "analysis" ? "regime detection, causal propagation, fact checking, win probability" : s.category === "execution" ? "order routing, trailing stops, partial exits, emergency flatten" : s.category === "risk" ? "deterministic validation, portfolio risk, drawdown breaker, cooldown" : s.category === "data" ? "feature building, quality gates, snapshot capture, schema validation" : s.category === "research" ? "backtesting, RL training, edge lifecycle, creator engine" : "L0-L3 memory retrieval, semantic search, compaction, sync", contentW - 6)}</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no skill selected</text>
        )}
      </box>
    </box>
  )
}

function Stat(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
