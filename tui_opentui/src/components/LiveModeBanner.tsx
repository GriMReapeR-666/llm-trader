import { useDeck } from "../store"
import { T } from "../theme"

export function LiveModeBanner() {
  const transport = useDeck((s) => s.transport)
  const bootMode = useDeck((s) => s.bootMode)
  const mode = useDeck((s) => s.mode)
  const halted = useDeck((s) => s.halted)
  const queueHealth = useDeck((s) => s.queueHealth)
  const llmUsed = useDeck((s) => s.llmBudgetUsed)
  const llmCap = useDeck((s) => s.llmBudgetCap)
  const agents = useDeck((s) => s.agents)
  const stale = transport === "stale"
  const live = transport === "live"

  const budgetPct = llmCap > 0 ? llmUsed / llmCap : 0
  const agentPulse = Object.values(agents).filter((a) => Date.now() - a.lastBeat < 20000).length
  const totalAgents = Object.keys(agents).length

  const bannerColor = halted ? T.critical : stale ? T.down : live ? T.up : T.warn
  const bannerLabel = halted ? "HALTED" : stale ? "STALE · reconnecting" : live ? `LIVE · ${mode}` : "CONNECTING"
  // Honest boot-mode chip: only rendered once the engine itself reported its
  // triage mode via ENGINE_CONFIG/BOOTSTRAP_COMPLETE — never a guess.
  const bootChip =
    bootMode === "fast" ? <text fg={T.warn}>FAST · triage off</text>
    : bootMode === "full" ? <text fg={T.teal}>FULL · triage auto</text>
    : null
  void budgetPct

  return (
    <box flexDirection="column" backgroundColor={T.bg} flexShrink={0} width="100%">
      <box flexDirection="row" gap={1} paddingLeft={1} paddingRight={1} height={1}>
        <text fg={bannerColor}>● {bannerLabel}</text>
        {bootChip}
        <text fg={T.dim}>agents {agentPulse}/{totalAgents} · queue {queueHealth} · LLM {llmUsed}/{llmCap}</text>
        {halted && <text fg={T.critical}>trading halted · /flatten to recover</text>}
      </box>
    </box>
  )
}
