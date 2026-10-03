import { useState } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck } from "../store"
import { fmtClock, fmtMoney } from "../format"
import { T, SEPARATOR } from "../theme"
import { clip, useContentWidth } from "../layout"
import type { ComplianceEvent, TradeHistoryEntry } from "../types"

const SEV_COLOR: Record<ComplianceEvent["severity"], string> = {
  SUCCESS: T.up,
  WARNING: T.warn,
  CRITICAL: T.down,
  INFO: T.cyan,
}

const SEV_ICON: Record<ComplianceEvent["severity"], string> = {
  SUCCESS: "✔",
  WARNING: "⚠",
  CRITICAL: "✕",
  INFO: "·",
}

const RIGHT_TABS = ["events", "trades"]

export function JournalScreen() {
  const checks = useDeck((s) => s.journalChecks)
  const events = useDeck((s) => s.opEvents)
  const compliance = useDeck((s) => s.complianceEvents)
  const chain = useDeck((s) => s.chainIntegrity)
  const tradeHistory = useDeck((s) => s.tradeHistory)
  const [rightTab, setRightTab] = useState(0)
  const failing = checks.filter((c) => !c.pass)
  const contentW = useContentWidth()

  useKeyboard((key) => {
    if (key.name === "tab" || key.name === "right") {
      setRightTab((p) => (p + 1) % RIGHT_TABS.length)
    }
  })

  return (
    <box flexDirection="row" flexGrow={1}>
      <box flexDirection="column" width={38} flexShrink={0}>
        <box title=" ◈ AUDIT " titleColor={T.cyan} borderStyle="single" borderColor={T.cyan} flexDirection="column" paddingLeft={1}>
          <text fg={failing.length === 0 ? T.up : T.critical}>
            ◉ {checks.length - failing.length}/{checks.length} rules passing
          </text>
          <text fg={T.borderBright}>{SEPARATOR.thin}</text>
          {checks.map((c) => (
            <box key={c.rule} flexDirection="column">
              <box flexDirection="row">
                <text fg={c.pass ? T.up : T.critical}>{c.pass ? "✔ " : "✘ "}</text>
                <text fg={c.pass ? T.muted : T.bright}>{c.rule}</text>
              </box>
              {!c.pass && (
                <text fg={T.warn} paddingLeft={2}>
                  ↳ {c.detail}
                </text>
              )}
            </box>
          ))}
          <text fg={T.borderBright}>{SEPARATOR.thin}</text>
          <text fg={T.cyan}>◈ CHAIN INTEGRITY</text>
          <text fg={chain.valid ? T.up : T.critical}>
            ◉ {chain.valid ? "VALID" : "BROKEN"}
          </text>
          <text fg={T.muted}>entries: {chain.entriesVerified} · {chain.durationMs.toFixed(1)}ms</text>
        </box>

        <box title=" ◈ COMPLIANCE EVENTS " titleColor={T.down} borderStyle="single" borderColor={T.borderBright} flexDirection="column" flexGrow={1} marginTop={0} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling
            scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
          >
            {compliance.length === 0 && <text fg={T.dim}>no compliance events</text>}
            {compliance.map((e) => (
              <box key={e.id} flexDirection="column">
                <box flexDirection="row">
                  <text fg={SEV_COLOR[e.severity]}>{SEV_ICON[e.severity]} </text>
                  <text fg={T.bright}>{e.type.padEnd(18)}</text>
                  <text fg={T.muted}>{fmtClock(e.ts)}</text>
                </box>
                <text fg={T.dim} paddingLeft={2}>{e.agent} {e.symbol ? `· ${e.symbol}` : ""}</text>
                <text fg={T.muted} paddingLeft={2}>{clip(e.details, 34)}</text>
              </box>
            ))}
          </scrollbox>
        </box>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box flexDirection="row" height={1} gap={1} paddingLeft={1}>
          {RIGHT_TABS.map((t, i) => (
            <text key={t} fg={rightTab === i ? T.bright : T.dim}>
              {rightTab === i ? "▸" : " "} {t.toUpperCase()}
            </text>
          ))}
          <text fg={T.dim}>{" "}tab=switch</text>
        </box>

        {rightTab === 0 && (
          <box title=" ◈ OPERATIONAL EVENTS " titleColor={T.teal} borderStyle="single" borderColor={T.teal} flexDirection="column" flexGrow={1}>
            <scrollbox flexGrow={1} viewportCulling paddingLeft={1}
              scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
            >
              {events.length === 0 && <text fg={T.dim}>no events yet…</text>}
              {events.map((e) => {
                const color =
                  e.severity === "critical" ? T.down : e.severity === "warn" ? T.warn : T.dim
                const icon = e.severity === "critical" ? "✕" : e.severity === "warn" ? "⚠" : "·"
                return (
                  <box key={`${e.id}-${e.ts}`} flexDirection="row">
                    <text fg={T.dim}>{fmtClock(e.ts)} </text>
                    <text fg={color}>{icon} {[`[${e.type}]`].join("").padEnd(16)}</text>
                    <text fg={T.muted}>{e.actor.padEnd(17)}</text>
                    <text fg={T.fg}>{clip(e.payload, contentW - 40)}</text>
                  </box>
                )
              })}
            </scrollbox>
          </box>
        )}

        {rightTab === 1 && (
          <box title=" ◈ CLOSED TRADES " titleColor={T.purple} borderStyle="single" borderColor={T.purple} flexDirection="column" flexGrow={1}>
            <scrollbox flexGrow={1} viewportCulling paddingLeft={1}
              scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
            >
              {tradeHistory.length === 0 && <text fg={T.dim}>no closed trades yet…</text>}
              {tradeHistory.length > 0 && (
                <box flexDirection="row">
                  <text fg={T.cyan}>{"TICKET".padEnd(10)}{"SYMBOL".padEnd(8)}{"SIDE".padEnd(6)}{"ENTRY".padStart(10)}{"EXIT".padStart(10)}{"PNL".padStart(10)}{"REASON".padEnd(12)}</text>
                </box>
              )}
              {tradeHistory.length > 0 && <text fg={T.borderBright}>{SEPARATOR.thin}</text>}
              {tradeHistory.map((t: TradeHistoryEntry) => (
                <box key={t.ticket} flexDirection="row">
                  <text fg={T.muted}>{String(t.ticket).padEnd(10)}</text>
                  <text fg={T.cyan}>{t.symbol.padEnd(8)}</text>
                  <text fg={t.side === "BUY" ? T.up : T.down}>{t.side.padEnd(6)}</text>
                  <text fg={T.dim}>{t.entry.toFixed(t.entry > 10 ? 2 : 4).padStart(10)}</text>
                  <text fg={T.dim}>{t.exitPrice.toFixed(t.exitPrice > 10 ? 2 : 4).padStart(10)}</text>
                  <text fg={t.pnl >= 0 ? T.up : T.down}>{fmtMoney(t.pnl).padStart(10)}</text>
                  <text fg={T.muted}>{t.exitReason.padEnd(12)}</text>
                </box>
              ))}
              {tradeHistory.length > 0 && (
                <>
                  <text fg={T.borderBright}>{SEPARATOR.thin}</text>
                  <text fg={T.muted}>
                    {tradeHistory.length} trades · wins: {tradeHistory.filter((t) => t.pnl > 0).length} · 
                    net: {fmtMoney(tradeHistory.reduce((a, t) => a + t.pnl, 0))}
                  </text>
                </>
              )}
            </scrollbox>
          </box>
        )}
      </box>
    </box>
  )
}
