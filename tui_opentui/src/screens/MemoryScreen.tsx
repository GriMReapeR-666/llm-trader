import { useDeck } from "../store"
import { T } from "../theme"
import { fmtNum, bar } from "../format"
import type { MemoryTierMetrics, MemoryReviewItem } from "../types"

const TIER_ICONS: Record<string, string> = {
  "L0 Working Context": "◉",
  "L1 Atomic Facts": "◆",
  "L2 Episodic Scenarios": "◈",
  "L2 Verified Memories": "◇",
  "L3 Core Policies": "▣",
}

const TIER_COLORS: Record<string, string> = {
  "L0 Working Context": T.cyan,
  "L1 Atomic Facts": T.teal,
  "L2 Episodic Scenarios": T.purple,
  "L2 Verified Memories": T.orange,
  "L3 Core Policies": T.warn,
}

const KIND_COLOR: Record<string, string> = {
  narrative: T.purple,
  verified: T.orange,
  scenario: T.teal,
  core: T.warn,
}

export function MemoryScreen() {
  const mem = useDeck((s) => s.memorySystem)
  const review = mem.candidateReview

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={36} flexShrink={0}>
        <box title=" MEMORY TIERS " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={2} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {mem.tierMetrics.map((tier, i) => (
              <box key={i} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={TIER_COLORS[tier.name] ?? T.fg}>{TIER_ICONS[tier.name] ?? "•"}</text>
                  <text fg={T.bright}>{tier.name}</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.muted}>retrievals: {tier.retrievals} · avg: {tier.meanResults.toFixed(1)} · zero: {(tier.zeroResultRate * 100).toFixed(0)}%</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.muted}>latency: {tier.meanLatencyMs.toFixed(0)}ms · linked: {tier.outcomeLinked}</text>
                </box>
                {tier.outcomeWinRate !== null && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.dim}>win rate: {tier.outcomeWinRate !== null ? (tier.outcomeWinRate * 100).toFixed(1) : "—"}</text>
                  </box>
                )}
                {i < mem.tierMetrics.length - 1 && <text fg={T.borderBright}>──────────────────────────</text>}
              </box>
            ))}
            <text fg={T.borderBright}>──────────────────────────</text>
            <text fg={T.dim}>WORKING CONTEXT</text>
            <box flexDirection="row"><text fg={T.muted}>{"tokens:".padEnd(14)}</text><text fg={T.fg}>{mem.workingContextTokens} / {mem.workingContextBudget}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"budget:".padEnd(14)}</text><text fg={T.muted}>{bar(mem.workingContextTokens / mem.workingContextBudget, 20)}</text></box>
            <text fg={T.borderBright}>──────────────────────────</text>
            <text fg={T.dim}>AGGREGATE</text>
            <box flexDirection="row"><text fg={T.muted}>{"total retrievals:".padEnd(18)}</text><text fg={T.fg}>{mem.totalRetrievals}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"prompt tokens:".padEnd(18)}</text><text fg={T.fg}>{mem.totalPromptTokens.toLocaleString()}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"zero-result rate:".padEnd(18)}</text><text fg={mem.zeroResultRate > 0.1 ? T.down : T.up}>{(mem.zeroResultRate * 100).toFixed(1)}%</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"stale facts:".padEnd(18)}</text><text fg={mem.staleAtomicFacts > 0 ? T.warn : T.up}>{mem.staleAtomicFacts}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"contradicted:".padEnd(18)}</text><text fg={mem.contradictedAtomicFacts > 0 ? T.down : T.up}>{mem.contradictedAtomicFacts}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"scenarios:".padEnd(18)}</text><text fg={T.fg}>{mem.scenarioDocuments}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"balanced:".padEnd(18)}</text><text fg={T.fg}>{mem.balancedScenarios}</text></box>
          </scrollbox>
        </box>

        <box title=" CANDIDATE REVIEW " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row" gap={2}>
              <text fg={review.status === "CLEAR" ? T.up : T.warn}>{review.status}</text>
              <text fg={T.dim}>{review.totalCandidates} candidates</text>
            </box>
            {Object.entries(review.counts).map(([kind, count]) => (
              <box key={kind} flexDirection="row" gap={1}>
                <text fg={KIND_COLOR[kind] ?? T.fg}>{kind}</text>
                <text fg={T.muted}>×{count}</text>
              </box>
            ))}
            <text fg={T.borderBright}>──────────────────────────</text>
            {review.items.map((item, i) => (
              <box key={i} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={KIND_COLOR[item.kind] ?? T.fg}>{item.kind.slice(0, 3).toUpperCase()}</text>
                  <text fg={T.fg}>{item.summary.slice(0, 30)}</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.dim}>{item.status}{item.createdAt ? ` · ${item.createdAt}` : ""}</text>
                </box>
              </box>
            ))}
            {review.nextActions.length > 0 && (
              <box flexDirection="column">
                <text fg={T.dim}>ACTIONS:</text>
                {review.nextActions.map((a, i) => (
                  <text key={i} fg={T.cyan}>  · {a}</text>
                ))}
              </box>
            )}
          </scrollbox>
        </box>
      </box>

      <box title=" MEMORY ARCHITECTURE " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          <text fg={T.dim}>FIVE-TIER RETRIEVAL PIPELINE</text>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.bright}>Tier 0 · Working Context</text>
          <text fg={T.muted}>  Session-local buffer. MARKET_SNAPSHOT, QUANT_PILLARS,</text>
          <text fg={T.muted}>  TRADE_HYPOTHESIS, ACTIVE_POSITION, RETRIEVED_ANALOG.</text>
          <text fg={T.muted}>  Budget: {mem.workingContextBudget} tokens · Compaction on overflow.</text>
          <text fg={T.borderBright}>  ↓</text>
          <text fg={T.bright}>Tier 1 · Episodic Scenarios</text>
          <text fg={T.muted}>  Scenario documents. Win/loss/rejected/unresolved per</text>
          <text fg={T.muted}>  strategy-symbol-side-setup-regime grouping.</text>
          <text fg={T.muted}>  Balanced sampling prefers win+loss pairs.</text>
          <text fg={T.borderBright}>  ↓</text>
          <text fg={T.bright}>Tier 2 · Atomic Facts & Rule Playbooks</text>
          <text fg={T.muted}>  Observable/inferred facts with provenance, confidence,</text>
          <text fg={T.muted}>  validity windows, supersession chains.</text>
          <text fg={T.muted}>  Namespaces: trend, regime, bias per symbol.</text>
          <text fg={T.borderBright}>  ↓</text>
          <text fg={T.bright}>Tier 3 · Macro Causal DAG + Case Studies</text>
          <text fg={T.muted}>  134-node macro graph edges with approval lifecycle.</text>
          <text fg={T.muted}>  16 case study archetypes (Hormuz, Red Sea, COVID...).</text>
          <text fg={T.muted}>  Historical analog matching with tau_horizon decay.</text>
          <text fg={T.borderBright}>  ↓</text>
          <text fg={T.bright}>Tier 4 · Quant Snapshot History</text>
          <text fg={T.muted}>  Rolling quant state: EV, ruin prob, Kelly, Hurst,</text>
          <text fg={T.muted}>  BOCPD regime, Kalman beta, OU half-life, VaR95.</text>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>MEMORY QUALITY MONITORING</text>
          <text fg={T.muted}>  Every retrieval logged: latency, result count,</text>
          <text fg={T.muted}>  prompt char estimate, outcome linkage.</text>
          <text fg={T.muted}>  Stale/contradicted atomic facts flagged for review.</text>
          <text fg={T.muted}>  Scenario balance monitored (win/loss ratio).</text>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>CANDIDATE REVIEW QUEUE</text>
          <text fg={T.muted}>  Narratives, verified memories, scenarios, core policies</text>
          <text fg={T.muted}>  require operator approval before promotion.</text>
          <text fg={T.muted}>  Commands: approve &lt;id&gt;, retire &lt;id&gt;, review &lt;kind&gt;</text>
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>ACTIVE KNOWLEDGE AGENT</text>
          <text fg={T.muted}>  Triggers retrieval on: new cycle, position open/close,</text>
          <text fg={T.muted}>  regime shift, macro event. Compact working memory</text>
          <text fg={T.muted}>  every N cycles to stay within token budget.</text>
        </scrollbox>
      </box>
    </box>
  )
}
