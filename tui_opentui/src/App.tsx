import { useEffect, useState, useCallback } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "./store"
import { T, VIEWS, VIEW_BY_KEY, SECTIONS, findSection, SECTION_FIRST_VIEW, cycleScreenInSection } from "./theme"
import type { ViewId } from "./theme"
import { useSidebarWidth } from "./layout"
import { HeaderBar } from "./components/HeaderBar"
import { StatusBar } from "./components/StatusBar"
import { AlertBand, dismissTopAlert } from "./components/AlertBand"
import { SafetyStrip } from "./components/SafetyStrip"
import { SideNavRail } from "./components/SideNavRail"
import { QuickNavHelp } from "./components/QuickNavHelp"
import { MarketStrip } from "./components/MarketStrip"
import { CommandPalette, ConfirmDialog } from "./components/CommandPalette"
import { StreamScreen } from "./screens/StreamScreen"
import { CockpitScreen } from "./screens/CockpitScreen"
import { BrainScreen, brainSelect } from "./screens/BrainScreen"
import { CausalScreen } from "./screens/CausalScreen"
import { MacroScreen } from "./screens/MacroScreen"
import { PositionsScreen } from "./screens/PositionsScreen"
import { JournalScreen } from "./screens/JournalScreen"
import { TasksScreen } from "./screens/TasksScreen"
import { StrategiesScreen } from "./screens/StrategiesScreen"
import { AnalyticsScreen } from "./screens/AnalyticsScreen"
import { MapScreen } from "./screens/MapScreen"
import { ApprovalsScreen, decideApproval } from "./screens/ApprovalsScreen"
import { GraphScreen } from "./screens/GraphScreen"
import { SkillsScreen } from "./screens/SkillsScreen"
import { BacktestScreen } from "./screens/BacktestScreen"
import { AlphaScreen } from "./screens/AlphaScreen"
import { DiagnosticsScreen } from "./screens/DiagnosticsScreen"
import { WorkflowsScreen } from "./screens/WorkflowsScreen"
import { LifecycleScreen } from "./screens/LifecycleScreen"
import { FusionScreen } from "./screens/FusionScreen"
import { ResearchPipelineScreen } from "./screens/ResearchPipelineScreen"
import { DataSourcesScreen } from "./screens/DataSourcesScreen"
import { ScannerScreen } from "./screens/ScannerScreen"
import { RLTrainingScreen } from "./screens/RLTrainingScreen"
import { ValidationPipelineScreen } from "./screens/ValidationPipelineScreen"
import { MemoryScreen } from "./screens/MemoryScreen"
import { DeliberationScreen } from "./screens/DeliberationScreen"
import { SystemHealthScreen } from "./screens/SystemHealthScreen"
import { SettingsScreen } from "./screens/SettingsScreen"
import { AutonomyScreen } from "./screens/AutonomyScreen"
import { RewardsScreen } from "./screens/RewardsScreen"
import { SecurityScreen } from "./screens/SecurityScreen"
import { GrowthScreen } from "./screens/GrowthScreen"
import { ModelsScreen } from "./screens/ModelsScreen"
import { MetricsScreen } from "./screens/MetricsScreen"
import { DrillsScreen } from "./screens/DrillsScreen"
import { MT5Screen } from "./screens/MT5Screen"
import { LogMonitorScreen } from "./screens/LogMonitorScreen"
import { OrchestrationScreen } from "./screens/OrchestrationScreen"
import { StrategyObservatoryScreen } from "./screens/StrategyObservatoryScreen"
import { WelcomeOverlay } from "./components/WelcomeOverlay"
import { ExitOverlay } from "./components/ExitOverlay"

export function App() {
  const view = useDeck((s) => s.view)
  const paletteOpen = useDeck((s) => s.paletteOpen)
  const confirmOpen = useDeck((s) => s.confirm !== null)
  const promptFocused = useDeck((s) => s.promptFocused)
  const [paletteKey, setPaletteKey] = useState(0)
  const [helpOpen, setHelpOpen] = useState(false)
  const sidebarWidth = useSidebarWidth()

  useEffect(() => {
    const t = setInterval(() => store.set({ now: Date.now() }), 1000)
    return () => clearInterval(t)
  }, [])

  // Frame counter driving lightweight animations (live pulse, spinners).
  // Only components that subscribe to `tick` re-render on this cadence.
  useEffect(() => {
    const t = setInterval(() => store.set({ tick: store.get().tick + 1 }), 200)
    return () => clearInterval(t)
  }, [])

  const cycleStreamAgent = useCallback(() => {
    const s = store.get()
    const list = [...new Set(s.stream.map((it) => it.agent))]
    if (list.length === 0) return
    const cur = s.streamAgent
    if (!cur) {
      store.setStreamAgent(list[0])
    } else {
      const idx = list.indexOf(cur)
      store.setStreamAgent(list[(idx + 1) % list.length])
    }
  }, [])

  const cycleSection = useCallback((dir: 1 | -1) => {
    const cur = findSection(store.get().view)
    const idx = cur ? SECTIONS.indexOf(cur) : 0
    const next = (idx + dir + SECTIONS.length) % SECTIONS.length
    const firstView = SECTIONS[next].views[0]?.id
    if (firstView) store.set({ view: firstView })
  }, [])

  useKeyboard((key) => {
    if (confirmOpen) return
    if (key.name === "escape") {
      if (helpOpen) {
        setHelpOpen(false)
        return
      }
      if (paletteOpen) {
        store.set({ paletteOpen: false })
        return
      }
      if (promptFocused) {
        store.set({ promptFocused: false })
        return
      }
      dismissTopAlert()
      return
    }

    if (view === "brain" && !promptFocused && !paletteOpen && !helpOpen) {
      if (key.name === "return" || key.name === "enter") {
        brainSelect()
        return
      }
    }

    if (view === "approvals" && !promptFocused && !paletteOpen && !helpOpen) {
      if (key.name === "a") {
        decideApproval("APPROVED")
        return
      }
      if (key.name === "r") {
        decideApproval("REJECTED")
        return
      }
    }

    // REPL feed controls — only when the stream screen is in front
    if (view === "stream" && !promptFocused && !paletteOpen && !helpOpen) {
      if (key.name === "p") {
        store.setStreamFollow(!store.get().streamFollow)
        return
      }
      if (key.name === "c") {
        store.clearStream()
        return
      }
      if (key.name === "a") {
        cycleStreamAgent()
        return
      }
      if (key.name === "e") {
        // expand / fold the newest event that matches the current filters
        store.toggleNewestStreamItem()
        return
      }
    }

    // Causal layout — - / = shrink / widen the DAG panel share of the screen
    if (view === "causal" && !promptFocused && !paletteOpen && !helpOpen) {
      if (key.name === "-" || key.name === "_" || key.name === "minus") {
        store.nudgeDagShare(-0.02)
        const v = Math.round((store.get().dagShare ?? 0.42) * 100)
        store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: `causal split ${v}% · DAG narrower (−)` , lines: [] })
        return
      }
      if (key.name === "=" || key.name === "+" || key.name === "equal" || key.name === "equals") {
        store.nudgeDagShare(0.02)
        const v = Math.round((store.get().dagShare ?? 0.42) * 100)
        store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: `causal split ${v}% · DAG wider (=)` , lines: [] })
        return
      }
    }

    // Graph / Architecture layout — preserve 1-5, space, t, g, d, s, r for internal tabs
    if (view === "graph" && !promptFocused && !paletteOpen && !helpOpen) {
      if (["1", "2", "3", "4", "5", "t", "g", "d", "s", "r", "space", " "].includes(key.name ?? "")) {
        return
      }
    }

    if (!promptFocused && !paletteOpen) {
      if (key.name === "?" || (key.name === "/" && key.shift)) {
        setHelpOpen((h) => !h)
        return
      }
      if (key.name === "/") {
        setPaletteKey((k) => k + 1)
        store.set({ paletteOpen: true })
        return
      }
      if (key.name === "tab" && !store.get().tabFocused) {
        const nextScreen = cycleScreenInSection(store.get().view, key.shift ? -1 : 1)
        store.set({ view: nextScreen })
        return
      }
      if (key.name === "i") {
        if (view === "stream") store.set({ promptFocused: true })
        return
      }
      if (key.name === "x") {
        dismissTopAlert()
        return
      }
      if (key.name === "right" || key.name === "]") {
        cycleSection(1)
        return
      }
      if (key.name === "left" || key.name === "[") {
        cycleSection(-1)
        return
      }
      // Direct jump to sections 1 to 6
      if (key.name && SECTION_FIRST_VIEW[key.name]) {
        store.set({ view: SECTION_FIRST_VIEW[key.name] })
        return
      }
      const target = VIEW_BY_KEY[key.name ?? ""]
      if (target) {
        store.set({ view: target })
      }
    }
  })

  return (
    <box flexDirection="column" width="100%" height="100%" backgroundColor={T.bg}>
      <HeaderBar onOpenHelp={() => setHelpOpen(true)} />
      <AlertBand />
      <box flexDirection="row" flexGrow={1} gap={1} paddingLeft={2} paddingRight={2} paddingTop={0} paddingBottom={0}>
        <box flexDirection="column" width={sidebarWidth} flexShrink={0} gap={1}>
          <SideNavRail onOpenHelp={() => setHelpOpen(true)} />
        </box>
        <box flexDirection="column" flexGrow={1} gap={1}>
          <MainView view={view} />
          <MarketStrip />
        </box>
      </box>
      <StatusBar />
      {/* Overlays stack in render order: content < welcome < palette/help < confirm < exit */}
      <ExitOverlay />
      <WelcomeOverlay />
      {paletteOpen && (
        <CommandPalette key={paletteKey} onClose={() => store.set({ paletteOpen: false })} />
      )}
      {helpOpen && (
        <QuickNavHelp onClose={() => setHelpOpen(false)} />
      )}
      <ConfirmDialog />
    </box>
  )
}

function MainView(props: { view: string }) {
  switch (props.view) {
    case "cockpit":
      return <CockpitScreen />
    case "brain":
      return <BrainScreen />
    case "causal":
      return <CausalScreen />
    case "macro":
      return <MacroScreen />
    case "positions":
      return <PositionsScreen />
    case "journal":
      return <JournalScreen />
    case "tasks":
      return <TasksScreen />
    case "strategies":
      return <StrategiesScreen />
    case "analytics":
      return <AnalyticsScreen />
    case "map":
      return <MapScreen />
    case "approvals":
      return <ApprovalsScreen />
    case "graph":
      return <GraphScreen />
    case "skills":
      return <SkillsScreen />
    case "backtest":
      return <BacktestScreen />
    case "alpha":
      return <AlphaScreen />
    case "diagnostics":
      return <DiagnosticsScreen />
    case "workflows":
      return <WorkflowsScreen />
    case "lifecycle":
      return <LifecycleScreen />
    case "fusion":
      return <FusionScreen />
    case "research":
      return <ResearchPipelineScreen />
    case "datasources":
      return <DataSourcesScreen />
    case "scanner":
      return <ScannerScreen />
    case "rl":
      return <RLTrainingScreen />
    case "validation":
      return <ValidationPipelineScreen />
    case "memory":
      return <MemoryScreen />
    case "deliberation":
      return <DeliberationScreen />
    case "health":
      return <SystemHealthScreen />
    case "settings":
      return <SettingsScreen />
    case "autonomy":
      return <AutonomyScreen />
    case "rewards":
      return <RewardsScreen />
    case "security":
      return <SecurityScreen />
    case "growth":
      return <GrowthScreen />
    case "models":
      return <ModelsScreen />
    case "metrics":
      return <MetricsScreen />
    case "drills":
      return <DrillsScreen />
    case "mt5":
      return <MT5Screen />
    case "logs":
      return <LogMonitorScreen />
    case "orchestration":
      return <OrchestrationScreen />
    case "observatory":
      return <StrategyObservatoryScreen />
    default:
      return <StreamScreen />
  }
}
