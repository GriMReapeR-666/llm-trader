import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { WorkflowExecution } from "../types"

const WORKFLOW_ICONS: Record<string, string> = {
  statistical_arbitrage: "△",
  mean_reversion: "▽",
  macro_causal_momentum: "◈",
  microstructure_breakout: "◇",
  neural_kronos_foundation: "⬡",
  macro_event_alpha: "⊕",
  risk_parity: "⊞",
  regime_monte_carlo: "⊡",
  contextual_bandit: "⊠",
  meta_rl_adaptation: "⊟",
  grpo_proposal: "⊘",
}

const WORKFLOW_SHORT: Record<string, string> = {
  statistical_arbitrage: "STAT-ARB",
  mean_reversion: "MEAN-REV",
  macro_causal_momentum: "MACRO-MOM",
  microstructure_breakout: "MICRO-BRK",
  neural_kronos_foundation: "KRONOS",
  macro_event_alpha: "MACRO-EVT",
  risk_parity: "RISK-PAR",
  regime_monte_carlo: "REGIME-MC",
  contextual_bandit: "CTX-BAND",
  meta_rl_adaptation: "META-RL",
  grpo_proposal: "GRPO",
}

const DECISION_COLOR: Record<string, string> = {
  EXECUTE: T.up,
  HOLD: T.dim,
  REJECT: T.down,
  CIRCUIT_BREAKER: T.critical,
}

export function workflowsMove(delta: number): void {
  const n = store.get().workflowExecutions.length
  if (n === 0) return
  store.set({ selectedWorkflow: Math.max(0, Math.min(n - 1, store.get().selectedWorkflow + delta)) })
}

export function WorkflowsScreen() {
  const workflows = useDeck((s) => s.workflowExecutions)
  const selected = Math.min(useDeck((s) => s.selectedWorkflow), Math.max(0, workflows.length - 1))
  const w = workflows[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title=" WORKFLOWS "
        titleColor={T.purple}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        width={32}
        flexShrink={0}
        paddingLeft={1}
      >
        <select
          flexGrow={1}
          focused
          options={workflows.map((x) => ({
            name: `${WORKFLOW_ICONS[x.workflowName] ?? "•"} ${WORKFLOW_SHORT[x.workflowName] ?? x.workflowName.slice(0, 10)}`,
            description: `${x.symbol} · ${x.decision} · ${x.totalLatencyMs.toFixed(0)}ms`,
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
        <text fg={T.borderBright}>────────────────────────</text>
        <text fg={T.dim}>10 active pipeline safety gates</text>
      </box>

      <box
        title={w ? ` ${WORKFLOW_ICONS[w.workflowName] ?? ""} ${w.workflowName} ` : " — "}
        titleColor={w ? T.purple : T.dim}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        flexGrow={1}
        paddingLeft={1}
      >
        {w ? (
          <scrollbox
            flexGrow={1}
            viewportCulling
            scrollbarOptions={{
              showArrows: false,
              trackOptions: { backgroundColor: T.panel, foregroundColor: T.border },
            }}
          >
            <box flexDirection="row" gap={2}>
              <text fg={T.bg} bg={DECISION_COLOR[w.decision]}> {w.decision} </text>
              <text fg={T.muted}>{w.symbol}</text>
              <text fg={T.dim}>{fmtClock(w.timestamp)}</text>
              {w.ticket ? <text fg={T.up}>ticket #{w.ticket}</text> : null}
              <text fg={T.dim}>{w.totalLatencyMs.toFixed(0)}ms</text>
            </box>
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>WORKFLOW STEPS ({w.steps.length})</text>
            <text fg={T.borderBright}> </text>
            {w.steps.map((step, si) => (
              <box key={si} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={step.passed ? T.up : T.down}>{step.passed ? "✓" : "✗"}</text>
                  <text fg={T.fg}>{step.name}</text>
                  <text fg={T.dim}>{step.durationMs.toFixed(0)}ms</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.dim}>{Object.entries(step.metrics).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(3) : v}`).join("  ")}</text>
                </box>
                {step.reason && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.warn}>{step.reason}</text>
                  </box>
                )}
                {si < w.steps.length - 1 && <text fg={T.borderBright}>  │</text>}
              </box>
            ))}
            <text fg={T.borderBright}>────────────────────────────────────</text>
            <text fg={T.dim}>WORKFLOW PROVENANCE</text>
            <text fg={T.muted}>engine: QuantWorkflowEngine (11 registered)</text>
            <text fg={T.muted}>resolver: statarb, mean_reversion, macro_momentum</text>
            <text fg={T.muted}>         microstructure, neural_kronos, macro_event</text>
            <text fg={T.muted}>         risk_parity, regime_mc, contextual_bandit</text>
            <text fg={T.muted}>         meta_rl, grpo</text>
          </scrollbox>
        ) : (
          <text fg={T.dim}>no workflow selected — run a cycle to see execution</text>
        )}
      </box>
    </box>
  )
}
