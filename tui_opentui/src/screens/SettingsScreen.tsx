import { useDeck, store } from "../store"
import { T } from "../theme"
import type { AutonomyLevel, ConfigEntry } from "../types"

export function settingsMove(delta: number): void {
  const n = store.get().settings.configEntries.length
  if (n === 0) return
  store.set({
    settings: {
      ...store.get().settings,
      selectedConfig: Math.max(0, Math.min(n - 1, store.get().settings.selectedConfig + delta)),
    },
  })
}

const LEVEL_ICONS: Record<string, string> = {
  true: "▶",
  false: "▷",
}

export function SettingsScreen() {
  const settings = useDeck((s) => s.settings)
  const selected = Math.min(settings.selectedConfig, Math.max(0, settings.configEntries.length - 1))

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={34} flexShrink={0}>
        <box title=" AUTONOMY LEVELS " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            {settings.autonomyLevels.map((level, i) => (
              <box key={level.id} flexDirection="column">
                <box flexDirection="row" gap={1}>
                  <text fg={level.active ? T.up : T.dim}>{LEVEL_ICONS[String(level.active)]}</text>
                  <text fg={level.active ? T.bright : T.muted}>{level.name}</text>
                </box>
                <box flexDirection="row" paddingLeft={3}>
                  <text fg={T.dim}>{level.description}</text>
                </box>
                {Array.isArray(level.capabilities) && level.capabilities.length > 0 && (
                  <box flexDirection="row" paddingLeft={3}>
                    <text fg={T.muted}>{level.capabilities.join(" · ")}</text>
                  </box>
                )}
                {i < settings.autonomyLevels.length - 1 && <text fg={T.borderBright}>──────────────────────────</text>}
              </box>
            ))}
          </scrollbox>
        </box>

        <box title=" SYSTEM OVERVIEW " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <text fg={T.dim}>ARCHITECTURE DOCTRINE</text>
            <text fg={T.borderBright}>──────────────────────────</text>
            <text fg={T.muted}>• LLM proposes, deterministic code decides</text>
            <text fg={T.muted}>• Fail-closed on any validation</text>
            <text fg={T.muted}>• Append-only audit trail</text>
            <text fg={T.muted}>• Capability-based security</text>
            <text fg={T.muted}>• Human-in-the-loop gates</text>
            <text fg={T.borderBright}>──────────────────────────</text>
            <text fg={T.dim}>SCREEN DIRECTORY</text>
            <text fg={T.muted}>1 MARKET · FEED / COCKPIT / POSITIONS</text>
            <text fg={T.muted}>  JOURNAL / TASKS / APPROVALS</text>
            <text fg={T.muted}>2 ANALYSIS · BRAIN / CAUSAL / MACRO</text>
            <text fg={T.muted}>  STRATEGIES / FUSION</text>
            <text fg={T.muted}>3 PIPELINE · PIPELINE GRAPH / SKILLS</text>
            <text fg={T.muted}>  WORKFLOWS / LIFECYCLE</text>
            <text fg={T.muted}>4 RESEARCH · ANALYTICS / BACKTEST / ALPHA</text>
            <text fg={T.muted}>  RESEARCH PIPELINE / RL / VALIDATION</text>
            <text fg={T.muted}>  MEMORY / DELIBERATION / ORCHESTRATION</text>
            <text fg={T.muted}>5 CONTROL · AUTONOMY / REWARDS / SECURITY</text>
            <text fg={T.muted}>  GROWTH</text>
            <text fg={T.muted}>6 SYSTEM · HEALTH / CONFIG / MODELS / METRICS</text>
            <text fg={T.muted}>  DRILLS / DATA / SCANNER / MT5 / LOGS</text>
            <text fg={T.muted}>  DIAGNOSTICS / MAP</text>
            <text fg={T.borderBright}>──────────────────────────</text>
            <text fg={T.dim}>OPERATIONAL COMMANDS</text>
            <text fg={T.muted}>/pause — halt all trading</text>
            <text fg={T.muted}>/flatten — close all positions</text>
            <text fg={T.muted}>/kill — emergency shutdown</text>
            <text fg={T.muted}>/approve — approve pending action</text>
            <text fg={T.muted}>/reject — reject pending action</text>
          </scrollbox>
        </box>
      </box>

      <box title=" CONFIGURATION " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          <box flexDirection="row">
            <text fg={T.dim}>{"CATEGORY".padEnd(16)}{"KEY".padEnd(28)}{"VALUE"}</text>
          </box>
          {settings.configEntries.map((entry, i) => {
            const active = i === selected
            return (
              <box key={entry.key} flexDirection="column" backgroundColor={active ? T.raised : undefined}>
                <box flexDirection="row">
                  <text fg={active ? T.cyan : T.muted}>{`${active ? "▸" : " "}${entry.category}`.padEnd(16)}</text>
                  <text fg={active ? T.bright : T.fg}>{entry.key.padEnd(28)}</text>
                  <text fg={active ? T.warn : T.fg}>{entry.value}</text>
                </box>
                {active && (
                  <box flexDirection="row" paddingLeft={2}>
                    <text fg={T.dim}>{entry.description}</text>
                  </box>
                )}
              </box>
            )
          })}
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>↑↓ navigate · detail on selection</text>
          <text fg={T.dim}>config changes logged to journal</text>
        </scrollbox>
      </box>
    </box>
  )
}
