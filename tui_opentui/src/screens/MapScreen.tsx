import { T } from "../theme"

const DIAGRAM = [
  [" DATA FEEDS ", " INTELLIGENCE ", " DECISION ", " EXECUTION "],
  ["MT5 ticks ───┐  ┌─ TechnicalNet scan ──┐   MarketModel propose    RiskController"],
  ["news/RSS ────┼─▶│ MacroAnalyst causal  ├─▶ validate (det.) ─────▶ route orders"],
  ["calendar ────┘  └─ opportunity bus ───┘   reject sink           attach SL/TP"],
  ["                     │                                              │"],
  ["                     ▼                                              ▼"],
  ["              memory L0–L3 ◀── reflection ◀── journal ◀──── fills / reconcile"],
  ["                                                                     │"],
  ["     research lane (QuantitativeCoach): labels · backtests · gates ◀─┘"],
] as const

const DIRECTORY: { key: string; name: string; answers: string }[] = [
  { key: "1", name: "FEED", answers: "what is happening right now? (repl-style reasoning feed)" },
  { key: "2", name: "COCKPIT", answers: "am I safe and making money? (risk + equity)" },
  { key: "3", name: "BRAIN", answers: "why did it decide that? (cycle briefs + diffs)" },
  { key: "4", name: "CAUSAL", answers: "what drives what? (macro DAG edges)" },
  { key: "5", name: "MACRO", answers: "what is the narrative? (news + nowcasts)" },
  { key: "6", name: "POSITIONS", answers: "what am I holding? (blotter)" },
  { key: "7", name: "JOURNAL", answers: "is the record intact? (audit + events)" },
  { key: "8", name: "TASKS", answers: "what is the loop doing? (pipeline spans + tool calls)" },
  { key: "9", name: "STRATEGIES", answers: "what can it trade and how well? (library + gates)" },
  { key: "0", name: "ANALYTICS", answers: "how has it performed? (overall + per strategy metrics)" },
  { key: "a", name: "APPROVALS", answers: "what needs my signature? (promotions, L3 rollouts, config)" },
]

export function MapScreen() {
  return (
    <scrollbox flexGrow={1} paddingLeft={1} paddingRight={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
      <text fg={T.dim}>SYSTEM MAP — one loop, two velocities</text>
      {DIAGRAM.map((row, i) => (
        <box key={i} flexDirection="column">
          {i === 0 ? (
            <box flexDirection="row">
              {row.map((h) => (
                <text key={h} fg={T.bg} bg={T.cyan}>
                  {" "}
                  {h}{" "}
                </text>
              ))}
            </box>
          ) : (
            <text fg={T.fg}>{row[0]}</text>
          )}
        </box>
      ))}
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.dim}>FAST LANE (every completed candle): poll → reconcile → features → causal → decide → validate → execute → manage</text>
      <text fg={T.dim}>SLOW LANE (scheduled): memory distillation · labeling · backtests · promotion gates · audits</text>
      <text fg={T.dim}>AUTHORITY: only RiskController submits orders; validator is fail-closed deterministic.</text>
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.cyan}>SCREEN DIRECTORY</text>
      {DIRECTORY.map((d) => (
        <box key={d.key} flexDirection="row">
          <text fg={T.bg} bg={T.borderBright}>{` ${d.key} `}</text>
          <text fg={T.bright}>{` ${d.name}`.padEnd(13)}</text>
          <text fg={T.muted}>{d.answers}</text>
        </box>
      ))}
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.cyan}>KEYBINDINGS</text>
      <Key k="1-9,0" v="switch screens" />
      <Key k="/" v="command palette (also type /… in prompt)" />
      <Key k="i" v="focus prompt on stream" />
      <Key k="ESC" v="release prompt · close palette · dismiss alert" />
      <Key k="↑↓" v="select item in Brain/Tasks/Strategies" />
      <Key k="x" v="dismiss top alert" />
      <Key k="/pause" v="stop new cycles (positions managed)" />
      <Key k="/flatten" v="close everything at market (confirm)" />
      <Key k="/kill" v="permanent halt — red banner until /resume (confirm)" />
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.cyan}>AUTONOMY DOCTRINE</text>
      <Key k="L0 SHADOW" v="observe only · no orders" />
      <Key k="L1 PAPER" v="paper trading, full pipeline autonomy" />
      <Key k="L2 MICRO" v="live with 0.25% risk cap — autonomous ceiling" />
      <Key k="L3 FULL" v="requires operator-signed rollout manifest (inbox a)" />
      <Key k="circuits" v="drawdown/recon → HALT · slippage → demote · LLM outage → HOLD-only" />
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.cyan}>DATA SOURCES (backend → panel)</text>
      <Key k="cycle_brief_v1" v="Brain reasoning + intent diff" />
      <Key k="agent_operations.summary()" v="Tasks pipeline, schedules, queue health" />
      <Key k="brain_observability.full()" v="Macro nowcasts, causal edges, regime" />
      <Key k="opportunity_bus.bus_snapshot()" v="Stream candidate cards" />
      <Key k="setup_registry + research_validation" v="Strategies library stages & gates" />
      <Key k="trading_state.db + risk_state.json" v="Positions, account rail, analytics" />
      <Key k="journal_audit.json (14 rules)" v="Journal integrity checklist" />
      <text fg={T.borderBright}>{"─".repeat(80)}</text>

      <text fg={T.cyan}>NEW SCREENS (36 total)</text>
      <Key k="q" v="RL Training — Q-learning + PPO metrics" />
      <Key k="v" v="Validation Pipeline — CPCV backtesting" />
      <Key k="n" v="Memory System — 5-tier retrieval architecture" />
      <Key k="z" v="Deliberation — 7-node graph + Meta-Reflector" />
      <Key k="h" v="System Health — kernel services + circuit breakers" />
      <Key k="c" v="Settings — autonomy levels + configuration" />
      <Key k="y" v="Autonomy Control — levels, halt/pause, rollout gate, circuit breakers" />
      <Key k="p" v="Rewards — reward ledger, per-agent attribution, CVaR sustainability" />
      <Key k="k" v="Security — credential scans, secrets hygiene, journal scope, git scrub" />
      <Key k="u" v="Growth — Monte Carlo sims, projections, ruin probability, growth rate" />
      <Key k="t" v="Models — model registry, versioning, metrics, promotion candidates" />
      <Key k="o" v="Metrics — Prometheus metrics, principal heartbeats" />
      <Key k="j" v="Drills — incident drills, restart recovery, drill steps" />
      <Key k="`" v="MT5 — live MT5 MCP bridge (ticks/positions/orders/history + MCP log)" />
    </scrollbox>
  )
}

function Key(props: { k: string; v: string }) {
  return (
    <box flexDirection="row">
      <text fg={T.warn}>{props.k.padEnd(14)}</text>
      <text fg={T.muted}>{props.v}</text>
    </box>
  )
}
