import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { LifecycleEntry, EdgeEntry } from "../types"

const LIFECYCLE_STATES_ORDER = [
  "CANDIDATE", "VALIDATING", "PAPER_PROVEN", "DEMO_PROVEN",
  "DEPLOYED", "DECAYING", "FROZEN", "RETIRED",
]

const STATE_COLOR: Record<string, string> = {
  CANDIDATE: T.muted,
  VALIDATING: T.warn,
  PAPER_PROVEN: T.cyan,
  DEMO_PROVEN: T.teal,
  DEPLOYED: T.up,
  DECAYING: T.orange,
  FROZEN: T.cyan,
  RETIRED: T.dim,
  MEASURED: T.muted,
  STAT_PASS: T.warn,
  REPLAY_PASS: T.cyan,
  DEMO_MATCH: T.teal,
  TINY_LIVE: T.orange,
  RETIRING: T.down,
  STANDBY: T.dim,
}

export function lifecycleMove(delta: number): void {
  const total = store.get().lifecycleEntries.length + store.get().edgeEntries.length
  if (total === 0) return
  store.set({ selectedLifecycle: Math.max(0, Math.min(total - 1, store.get().selectedLifecycle + delta)) })
}

export function LifecycleScreen() {
  const lifecycles = useDeck((s) => s.lifecycleEntries)
  const edges = useDeck((s) => s.edgeEntries)
  const sel = Math.min(useDeck((s) => s.selectedLifecycle), Math.max(0, lifecycles.length + edges.length - 1))
  const isEdge = sel >= lifecycles.length
  const entry = isEdge ? edges[sel - lifecycles.length] : lifecycles[sel]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={30} flexShrink={0}>
        <box
          title=" STRATEGY LIFECYCLE "
          titleColor={T.cyan}
          borderStyle="single"
          borderColor={T.border}
          flexDirection="column"
          flexGrow={1}
          paddingLeft={1}
        >
          <select
            flexGrow={1}
            focused
            options={lifecycles.map((x) => ({
              name: x.name,
              description: `${x.state}${x.drift ? " ⚠ DRIFT" : ""}`,
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
        </box>
        <box
          title=" EDGE LIFECYCLE "
          titleColor={T.purple}
          borderStyle="single"
          borderColor={T.border}
          flexDirection="column"
          flexGrow={1}
          paddingLeft={1}
        >
          <select
            flexGrow={1}
            focused
            options={edges.map((x) => ({
              name: x.name,
              description: x.state,
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
        </box>
      </box>

      <box
        title={entry ? ` ${entry.name} ` : " — "}
        titleColor={entry ? T.cyan : T.dim}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        flexGrow={1}
        paddingLeft={1}
      >
        {entry ? (
          <scrollbox
            flexGrow={1}
            viewportCulling
            scrollbarOptions={{
              showArrows: false,
              trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
            }}
          >
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={STATE_COLOR[entry.state]}> {entry.state} </text>
              {"strategyId" in entry && <text fg={T.muted}>strategy: {(entry as EdgeEntry).strategyId}</text>}
              {"drift" in entry && entry.drift && <text fg={T.down}>DRIFT: {entry.drift}</text>}
              {entry.blocked && <text fg={T.critical}>BLOCKED</text>}
              <text fg={T.dim}>{fmtClock(entry.updatedAt)}</text>
            </box>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>STATE PROGRESSION</text>
            <text fg={T.borderBright}> </text>
            {"strategyId" in entry ? (
              <text fg={T.muted}>  {(entry as EdgeEntry).state} : {(entry as EdgeEntry).strategyId}</text>
            ) : (
              <text fg={T.muted}>  {LIFECYCLE_STATES_ORDER.map((s) => s === entry.state ? `→${s}←` : s).join(" → ")}</text>
            )}
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>TRANSITION HISTORY ({entry.transitions.length})</text>
            <text fg={T.borderBright}> </text>
            {entry.transitions.map((t, i) => (
              <box key={i} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={T.dim}>{fmtClock(t.timestamp)}</text>
                  <text fg={STATE_COLOR[t.to]}>{(t.from ?? "∅").padEnd(14)}→ {t.to}</text>
                  <text fg={T.muted}>{t.actor}</text>
                </box>
                {t.reason && (
                  <box flexDirection="row" paddingLeft={15}>
                    <text fg={T.dim}>{t.reason}</text>
                  </box>
                )}
              </box>
            ))}
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>GOVERNANCE RULES</text>
            <text fg={T.muted}>• automated: DEPLOYED→DECAYING→FROZEN (demotion only)</text>
            <text fg={T.muted}>• operator:  all promotions, unfreeze, retire</text>
            <text fg={T.muted}>• history:   append-only (SQLite triggers enforce)</text>
            <text fg={T.muted}>• blocking:  FROZEN/RETIRED → no new validation</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no entry selected</text>
        )}
      </box>
    </box>
  )
}
