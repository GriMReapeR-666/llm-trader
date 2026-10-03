import { useDeck } from "../store"
import { T, AGENT_COLORS } from "../theme"

const ORDER: { id: string; label: string }[] = [
  { id: "TechnicalNet", label: "TechNet" },
  { id: "MacroAnalyst", label: "Macro" },
  { id: "MarketModel", label: "Model" },
  { id: "RiskController", label: "RiskCtrl" },
  { id: "QuantitativeCoach", label: "Coach" },
  { id: "EventAgent", label: "Event" },
  { id: "PortfolioAgent", label: "Portfolio" },
  { id: "IncidentAgent", label: "Incident" },
  { id: "ResearchAgent", label: "Research" },
]

export function AgentRoster() {
  const agents = useDeck((s) => s.agents)
  const now = useDeck((s) => s.now)

  return (
    <box
      title=" Agents "
      titleColor={T.muted}
      borderStyle="single"
      borderColor={T.border}
      backgroundColor={T.panel}
      flexDirection="column"
      paddingLeft={1}
      paddingRight={1}
      paddingTop={0}
      paddingBottom={0}
    >
      {ORDER.map(({ id, label }) => {
        const a = agents[id] ?? { status: "idle" as const, task: null, lastBeat: 0, runsToday: 0 }
        const color = AGENT_COLORS[id] ?? T.fg
        const fresh = now - (a.lastBeat ?? 0) < 60000
        const dot = a.status === "error" ? "×" : a.status === "working" ? "●" : fresh ? "●" : "○"
        const dotColor = a.status === "error" ? T.down : a.status === "working" ? color : fresh ? color : T.dim
        return (
          <box key={id} flexDirection="row">
            <text fg={dotColor}>{dot} </text>
            <text fg={a.status === "working" || fresh ? T.fg : T.dim}>{label}</text>
          </box>
        )
      })}
    </box>
  )
}

export function AgentRosterCompact() {
  const agents = useDeck((s) => s.agents)
  const now = useDeck((s) => s.now)
  const working = ORDER.filter(({ id }) => (agents[id]?.status ?? "idle") === "working").length
  const errors = ORDER.filter(({ id }) => (agents[id]?.status ?? "idle") === "error").length
  void now
  return (
    <box flexDirection="row" paddingLeft={1}>
      <text fg={errors > 0 ? T.down : working > 0 ? T.teal : T.dim}>
        {errors > 0 ? `${errors} err` : working > 0 ? `${working} active` : "idle"} · 9 agents
      </text>
    </box>
  )
}
