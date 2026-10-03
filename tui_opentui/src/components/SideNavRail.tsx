import { useState } from "react"
import { useTerminalDimensions } from "@opentui/react"
import { useDeck, store } from "../store"
import { T, SECTIONS, findSection, incidentSeverityColor, incidentSeverityGlyph } from "../theme"
import { useSidebarWidth, clip } from "../layout"
import { AgentRoster } from "./AgentRoster"
import { AccountRail } from "./AccountRail"

type SidebarTab = "nav" | "agents" | "acct"

interface SideNavRailProps {
  onOpenHelp?: () => void
}

/**
 * Left navigation rail.
 *
 * Refactored as a full-height bordered panel:
 *   · pinned tab row (NAV / OPS / ACCT)
 *   · a scrollable middle region (sections + active screens) so a short
 *     window never clips content — the list scrolls instead
 *   · a pinned ops footer (agents / equity) so account state is always
 *     visible without scrolling
 * Width follows the live terminal width (see useSidebarWidth) and every row
 * label is clipped to the rail's measured inner width, so the panel reflows
 * cleanly as the window changes shape.
 */
export function SideNavRail(props: SideNavRailProps) {
  const [tab, setTab] = useState<SidebarTab>("nav")
  const railWidth = useSidebarWidth()
  const { height } = useTerminalDimensions()
  const effH = height > 0 ? height : 40
  // Short windows: collapse the active-section screen list so the rail never
  // steals vertical space from the main panel.
  const compact = effH < 38

  const currentView = useDeck((s) => s.view)
  const agents = useDeck((s) => s.agents)
  const acct = useDeck((s) => s.account)
  const mode = useDeck((s) => s.mode)
  const incidents = useDeck((s) => s.systemHealth.incidents)

  const activeAgentsCount = Object.values(agents).filter(
    (a) => a.status === "working" || a.status === "idle",
  ).length
  const pnl = acct.equity - acct.dayStartEquity
  // P7 badge: worst active incident severity, visible on every screen. A
  // kill-switch engagement outranks the computed status — the operator must
  // see it even if the journal evaluator is unreachable.
  const ksEngaged = incidents.killSwitch?.active === true
  const worstStatus = ksEngaged ? "CRITICAL" : incidents.status
  const incColor = incidentSeverityColor(worstStatus)
  const incGlyph = incidentSeverityGlyph(worstStatus)
  // inner width = rail width − double border − horizontal padding
  const innerW = Math.max(12, railWidth - 4)
  const currentSection = findSection(currentView)

  const TabBar = (
    <box flexDirection="row" paddingLeft={1} paddingRight={1} marginBottom={1} justifyContent="space-between">
      <TabButton active={tab === "nav"} onMouseDown={() => setTab("nav")} fg={T.bright} label="NAV" />
      <TabButton active={tab === "agents"} onMouseDown={() => setTab("agents")} fg={T.teal} label="OPS" />
      <TabButton active={tab === "acct"} onMouseDown={() => setTab("acct")} fg={T.purple} label="ACCT" />
    </box>
  )

  const OpsFooter = (
    <box
      marginTop={1}
      marginBottom={1}
      marginLeft={1}
      marginRight={1}
      borderStyle="single"
      borderColor={T.border}
      flexDirection="column"
      paddingLeft={0}
      paddingRight={0}
      flexShrink={0}
    >
      <box onMouseDown={() => setTab("agents")} flexDirection="row" justifyContent="space-between">
        <text fg={T.teal}>● {activeAgentsCount} agents</text>
        <text fg={T.dim}>{mode}</text>
      </box>
      <box onMouseDown={() => setTab("acct")} flexDirection="row" justifyContent="space-between">
        <text fg={T.dim}>{clip(`Eq ${acct.equity.toFixed(0)}`, innerW - 9)}</text>
        <text fg={pnl >= 0 ? T.up : T.down}>{`${pnl >= 0 ? "+" : ""}${pnl.toFixed(0)}`}</text>
      </box>
      <box
        onMouseDown={() => store.set({ view: "health" })}
        flexDirection="row"
        justifyContent="space-between"
      >
        <text fg={incColor}>{clip(`${incGlyph} incidents`, innerW - 8)}</text>
        <text fg={incColor}>
          {ksEngaged ? "KILL" : incidents.cards.length > 0 ? `×${incidents.cards.length}` : incidents.status === "OK" ? "clear" : "?"}
        </text>
      </box>
    </box>
  )

  if (tab === "agents") {
    return (
      <box flexDirection="column" flexGrow={1}>
        <BackRow onClick={() => setTab("nav")} />
        <scrollbox
          flexGrow={1}
          viewportCulling
          scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
        >
          <AgentRoster />
        </scrollbox>
        {OpsFooter}
      </box>
    )
  }

  if (tab === "acct") {
    return (
      <box flexDirection="column" flexGrow={1}>
        <BackRow onClick={() => setTab("nav")} />
        <scrollbox
          flexGrow={1}
          viewportCulling
          scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
        >
          <AccountRail />
        </scrollbox>
        {OpsFooter}
      </box>
    )
  }

  return (
    <box
      title=" NAVIGATION "
      titleColor={T.cyan}
      borderStyle="single"
      borderColor={T.border}
      backgroundColor={T.panel}
      flexDirection="column"
      flexGrow={1}
      paddingLeft={0}
      paddingRight={0}
      paddingTop={0}
      paddingBottom={0}
    >
      {TabBar}

      {/* Scrollable middle: sections + active screens. Nothing clips on short
          windows — the list scrolls. */}
      <scrollbox
        flexGrow={1}
        viewportCulling
        paddingLeft={1}
        paddingRight={1}
        scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
      >
        <text fg={T.dim}>SECTIONS (click):</text>
        {SECTIONS.map((s) => {
          const isCurrent = s.id === currentSection?.id
          const label = `${isCurrent ? "●" : " "} [${s.key}] ${s.icon} ${s.label}`
          return (
            <box
              key={s.id}
              onMouseDown={() => {
                const first = s.views[0]?.id
                if (first) store.set({ view: first })
              }}
              paddingLeft={0}
              paddingRight={0}
              flexDirection="row"
              justifyContent="space-between"
              backgroundColor={isCurrent ? T.raised : undefined}
            >
              <text fg={isCurrent ? T.cyan : T.fg}>{clip(label, innerW - 5)}</text>
              <text fg={isCurrent ? T.teal : T.dim}>{s.views.length}</text>
            </box>
          )
        })}

        {!compact && (
          <>
            <box marginTop={1} marginBottom={0}>
              <text fg={T.dim}>{clip(`── ${currentSection?.label ?? "SCREENS"} ──`, innerW)}</text>
            </box>
            <box flexDirection="column" gap={0}>
              {currentSection?.views.map((v) => {
                const isActive = v.id === currentView
                return (
                  <box
                    key={v.id}
                    onMouseDown={() => store.set({ view: v.id })}
                    paddingLeft={0}
                    paddingRight={0}
                    flexDirection="row"
                    justifyContent="space-between"
                    backgroundColor={isActive ? T.borderBright : undefined}
                  >
                    <text fg={isActive ? T.bright : T.muted}>{clip(`${isActive ? "›" : " "} ${v.label}`, innerW - 5)}</text>
                    <text fg={isActive ? T.cyan : T.dim}>{v.key}</text>
                  </box>
                )
              })}
            </box>
          </>
        )}
      </scrollbox>

      {/* Quick actions */}
      <box flexDirection="column" marginLeft={1} marginRight={1} flexShrink={0}>
        <box
          onMouseDown={() => store.set({ paletteOpen: true })}
          backgroundColor={T.raised}
          paddingLeft={1}
          paddingRight={1}
        >
          <text fg={T.cyan}>{clip("🔍 Search Screens (/)", innerW)}</text>
        </box>
        {props.onOpenHelp && (
          <box
            onMouseDown={props.onOpenHelp}
            backgroundColor={T.panel}
            paddingLeft={1}
            paddingRight={1}
          >
            <text fg={T.orange}>{clip("❓ Help & Keys (?)", innerW)}</text>
          </box>
        )}
      </box>

      {OpsFooter}
    </box>
  )
}

function TabButton(props: {
  active: boolean
  label: string
  fg: string
  onMouseDown: () => void
}) {
  return (
    <box
      onMouseDown={props.onMouseDown}
      backgroundColor={props.active ? T.raised : undefined}
      paddingLeft={1}
      paddingRight={1}
    >
      <text fg={props.active ? props.fg : T.dim}>
        {props.active ? `[${props.label}]` : ` ${props.label} `}
      </text>
    </box>
  )
}

function BackRow(props: { onClick: () => void }) {
  return (
    <box
      onMouseDown={props.onClick}
      backgroundColor={T.raised}
      paddingLeft={1}
      paddingRight={1}
      flexDirection="row"
      justifyContent="space-between"
      marginBottom={1}
    >
      <text fg={T.cyan}>
        <b>‹ BACK TO NAV</b>
      </text>
      <text fg={T.dim}>[Esc / Click]</text>
    </box>
  )
}
