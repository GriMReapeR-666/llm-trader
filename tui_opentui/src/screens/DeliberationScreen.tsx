import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock, fmtNum } from "../format"
import type { DeliberationNode } from "../types"

const NODE_ICONS: Record<string, string> = {
  pending: "○",
  running: "●",
  done: "✓",
  error: "✗",
  skipped: "—",
}

const NODE_COLORS: Record<string, string> = {
  pending: T.dim,
  running: T.cyan,
  done: T.up,
  error: T.down,
  skipped: T.muted,
}

const ARCH_COLORS: Record<string, string> = {
  HEALTHY: T.up,
  WARNING: T.warn,
  DEGRADED: T.down,
}

export function deliberationMove(delta: number): void {
  const n = store.get().deliberation.graphStates.length
  if (n === 0) return
  store.set({
    deliberation: {
      ...store.get().deliberation,
      selectedGraph: Math.max(0, Math.min(n - 1, store.get().deliberation.selectedGraph + delta)),
    },
  })
}

function GraphFlow(props: { nodes: DeliberationNode[] }) {
  return (
    <box flexDirection="column">
      {props.nodes.map((node, i) => (
        <box key={node.id} flexDirection="column">
          <box flexDirection="row" gap={1}>
            <text fg={NODE_COLORS[node.status]}>{NODE_ICONS[node.status]}</text>
            <text fg={node.status === "done" ? T.fg : T.muted}>{node.label}</text>
            {node.durationMs !== undefined && <text fg={T.dim}>{node.durationMs}ms</text>}
          </box>
          {node.output && (
            <box flexDirection="row" paddingLeft={3}>
              <text fg={T.dim}>{node.output}</text>
            </box>
          )}
          {i < props.nodes.length - 1 && <text fg={T.borderBright}>  ↓</text>}
        </box>
      ))}
    </box>
  )
}

export function DeliberationScreen() {
  const delib = useDeck((s) => s.deliberation)
  const graphs = delib.graphStates
  const selected = Math.min(delib.selectedGraph, Math.max(0, graphs.length - 1))
  const g = graphs[selected]
  const audit = delib.audit

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={32} flexShrink={0}>
        <box
          title=" DELIBERATION GRAPHS "
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
            options={graphs.map((x) => ({
              name: x.cycleId.slice(0, 13),
              description: `${x.symbol} · ${x.status}`,
              value: x.cycleId,
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
          title=" META-REFLECTOR "
          titleColor={T.warn}
          borderStyle="single"
          borderColor={T.border}
          flexDirection="column"
          flexGrow={1}
          paddingLeft={1}
        >
          <scrollbox
            flexGrow={1}
            viewportCulling
            scrollbarOptions={{
              showArrows: false,
              trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
            }}
          >
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={ARCH_COLORS[audit.overallStatus]}> {audit.overallStatus} </text>
            </box>
            <text fg={T.borderBright}>────────────────────────</text>
            <text fg={T.dim}>DIAGNOSIS</text>
            <box flexDirection="row">
              <text fg={T.muted}>{"train-test gap:".padEnd(18)}</text>
              <text fg={audit.trainTestGapPct > 25 ? T.down : T.up}>{audit.trainTestGapPct.toFixed(1)}%</text>
            </box>
            <box flexDirection="row">
              <text fg={T.muted}>{"regime flips/30d:".padEnd(18)}</text>
              <text fg={audit.regimeFlipFrequency30d > 10 ? T.down : T.fg}>{audit.regimeFlipFrequency30d}</text>
            </box>
            <box flexDirection="row">
              <text fg={T.muted}>{"rolling 30E:".padEnd(18)}</text>
              <text fg={audit.rolling30Expectancy > 0.003 ? T.up : T.down}>{fmtNum(audit.rolling30Expectancy, 4)}</text>
            </box>
            <box flexDirection="row">
              <text fg={T.muted}>{"risk multiplier:".padEnd(18)}</text>
              <text fg={audit.riskMultiplier < 1 ? T.warn : T.up}>{audit.riskMultiplier.toFixed(2)}</text>
            </box>
            <box flexDirection="row">
              <text fg={T.muted}>{"architecture:".padEnd(18)}</text>
              <text fg={T.fg}>{audit.recommendedArchitecture}</text>
            </box>
            {audit.remediationActions.length > 0 && (
              <box flexDirection="column">
                <text fg={T.dim}>REMEDIATION:</text>
                {audit.remediationActions.map((a, i) => (
                  <text key={i} fg={T.warn}>  · {a}</text>
                ))}
              </box>
            )}
          </scrollbox>
        </box>
      </box>

      <box
        title={g ? ` ${g.symbol} ${g.timeframe} — ${g.cycleId} ` : " — "}
        titleColor={g ? T.purple : T.dim}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        flexGrow={1}
        paddingLeft={1}
      >
        {g ? (
          <scrollbox
            flexGrow={1}
            viewportCulling
            scrollbarOptions={{
              showArrows: false,
              trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
            }}
          >
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={g.status === "EXECUTED" ? T.up : g.status === "REJECTED" ? T.down : T.cyan}> {g.status} </text>
              <text fg={T.fg}>{g.symbol} {g.timeframe}</text>
              <text fg={T.muted}>{g.strategy}</text>
              <text fg={T.dim}>{fmtClock(g.startedTs)}</text>
            </box>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>7-NODE DELIBERATIVE GRAPH</text>
            <text fg={T.borderBright}> </text>
            <GraphFlow nodes={g.nodes} />
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>GRAPH ARCHITECTURE</text>
            <text fg={T.muted}>  LangGraph-based deliberative reasoning.</text>
            <text fg={T.muted}>  Nodes execute sequentially with typed state.</text>
            <text fg={T.muted}>  Conditional routing after risk_controller:</text>
            <text fg={T.muted}>    validation_passed → order_execution → END</text>
            <text fg={T.muted}>    !validation_passed → rejection_sink → END</text>
            <text fg={T.muted}>  Human-in-the-loop gate on order_execution.</text>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>STATE TRANSITIONS</text>
            <text fg={T.muted}>  INITIALIZED → FEATURES_BUILT → HYPOTHESIS_FORMED</text>
            <text fg={T.muted}>    → VALIDATED → EXECUTED</text>
            <text fg={T.muted}>    → REJECTED (if validation fails)</text>
            <text fg={T.muted}>    → ERROR (if any node fails)</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no deliberation graph selected</text>
        )}
      </box>
    </box>
  )
}
