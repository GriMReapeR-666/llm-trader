import { useState, useCallback } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { bar } from "../format"
import { T, SEPARATOR } from "../theme"
import { clip, useContentWidth } from "../layout"
import { TabBar } from "../components/TabBar"
import type { EconomicEvent, LayaDecision } from "../types"

const TABS = ["calendar", "narratives", "nowcasts", "regimes", "laya_decisions"]

const IMPACT_COLOR: Record<EconomicEvent["impact"], string> = {
  HIGH: T.down,
  MODERATE: T.warn,
  LOW: T.dim,
}

const IMPACT_ICON: Record<EconomicEvent["impact"], string> = {
  HIGH: "▲",
  MODERATE: "●",
  LOW: "○",
}

export function MacroScreen() {
  const narratives = useDeck((s) => s.narratives)
  const nowcasts = useDeck((s) => s.nowcasts)
  const regimes = useDeck((s) => s.intermarketRegimes)
  const events = useDeck((s) => s.economicEvents)
  const fusionResults = useDeck((s) => s.fusionResults)
  const laya = useDeck((s) => s.layaDecisions)
  const [tab, setTab] = useState(0)
  const contentW = useContentWidth()

  useKeyboard((key) => {
    if (key.name === "1") { setTab(0); return }
    if (key.name === "2") { setTab(1); return }
    if (key.name === "3") { setTab(2); return }
    if (key.name === "4") { setTab(3); return }
    if (key.name === "5") { setTab(4); return }
    if (key.name === "left" || (key.name === "tab" && key.shift)) {
      setTab((t) => (t - 1 + TABS.length) % TABS.length)
      return
    }
    if (key.name === "right" || key.name === "tab") {
      setTab((t) => (t + 1) % TABS.length)
      return
    }
  })

  return (
    <box flexDirection="column" flexGrow={1}>
      <TabBar
        tabs={TABS}
        selected={tab}
        onChange={setTab}
        activeColor={T.cyan}
      />
      {tab === 0 && (
        <box title=" ═══ ECONOMIC CALENDAR ═══ " titleColor={T.down} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <box flexDirection="row">
            <text fg={T.cyan}>{clip("IMPACT".padEnd(9) + "EVENT".padEnd(26) + "CURRENCY".padEnd(10) + "TIME".padEnd(12) + "ACTUAL".padStart(9) + "FORECAST".padStart(10) + "PREV".padStart(8), contentW - 4)}</text>
          </box>
          <text fg={T.borderBright}>{SEPARATOR.thin}</text>
          {events.length === 0 && <text fg={T.dim}>no upcoming events</text>}
          {events.map((ev) => {
            const hasActual = ev.actual != null
            const devNum = ev.deviation ? parseFloat(ev.deviation) : 0
            const devColor = devNum > 0 ? T.up : devNum < 0 ? T.down : T.dim
            return (
              <box key={ev.id} flexDirection="column">
                <box flexDirection="row">
                  <text fg={IMPACT_COLOR[ev.impact]}>{IMPACT_ICON[ev.impact]} {ev.impact.padEnd(7)}</text>
                  <text fg={T.bright}>{clip(ev.title, 26)}</text>
                  <text fg={T.cyan}>{ev.currency.padEnd(10)}</text>
                  <text fg={T.muted}>{ev.time.padEnd(12)}</text>
                  <text fg={hasActual ? T.bright : T.dim}>{(ev.actual ?? "—").padStart(9)}</text>
                  <text fg={T.muted}>{(ev.forecast ?? "—").padStart(10)}</text>
                  <text fg={T.dim}>{(ev.previous ?? "—").padStart(8)}</text>
                </box>
                {ev.deviation && (
                  <text fg={devColor} paddingLeft={9}>deviation: {ev.deviation}</text>
                )}
              </box>
            )
          })}
          <text fg={T.dim}>{events.filter((e) => e.impact === "HIGH").length} HIGH impact · lockout ±90m</text>
        </box>
      )}
      {tab === 1 && (
        <box title=" ACTIVE NARRATIVES " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {narratives.length === 0 && <text fg={T.dim}>ingesting feeds…</text>}
          {narratives.map((n) => (
            <box key={n.id} flexDirection="column" marginBottom={0}>
              <box flexDirection="row" gap={1}>
                <text fg={T.bg} bg={T.purple}>
                  {" "}
                  {n.tag}{" "}
                </text>
                <text fg={T.dim}>w={n.weight.toFixed(2)}</text>
              </box>
              <text fg={T.fg}>■ {clip(n.headline, contentW - 8)}</text>
              <text fg={T.borderBright}> </text>
            </box>
          ))}
          <text fg={T.dim}>L0–L4 dynamics · zero-lag physical proxies</text>
        </box>
      )}
      {tab === 2 && (
        <box title=" ALTERNATIVE NOWCASTS " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {nowcasts.length === 0 && <text fg={T.dim}>no nowcasts available · awaiting observations</text>}
          {nowcasts.map((nc) => {
            const norm = Math.max(0, Math.min(1, Math.abs(nc.value) / 100))
            const arrow = nc.trend === 1 ? "▲" : nc.trend === -1 ? "▼" : "═"
            const color = nc.trend === 1 ? T.up : nc.trend === -1 ? T.down : T.muted
            return (
              <box key={nc.name} flexDirection="column">
                <box flexDirection="row" justifyContent="space-between" paddingRight={1}>
                  <text fg={T.muted}>{clip(nc.name, contentW - 30)}</text>
                  <text fg={color}>
                    {arrow} {nc.value.toFixed(1)} {nc.unit}
                  </text>
                </box>
                <box flexDirection="row">
                  <text fg={color}>{bar(norm, 24)}</text>
                </box>
                <text fg={T.borderBright}> </text>
              </box>
            )
          })}
          <text fg={T.dim}>point-in-time FRED observations · CPI/liquidity proxies</text>
        </box>
      )}
      {tab === 3 && (
        <box title=" INTERMARKET REGIME " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <text fg={T.dim}>{"PAIR".padEnd(20)}{"CORR".padStart(6)}{"REGIME".padStart(12)}{"LAG".padStart(7)}{"STR".padStart(5)} SIGNAL</text>
          <text fg={T.borderBright}>{"─".repeat(70)}</text>
          {regimes.length === 0 && <text fg={T.dim}>no correlation pairs available · awaiting returns</text>}
          {regimes.map((r) => {
            const regimeColor = r.regime === "RISK_ON" ? T.up : r.regime === "RISK_OFF" ? T.down : r.regime === "DIVERGENT" ? T.warn : T.dim
            return (
              <box key={r.pair} flexDirection="row">
                <text fg={T.cyan}>{clip(r.pair, 20)}</text>
                <text fg={r.correlation > 0 ? T.up : T.down}>{String(r.correlation.toFixed(2)).padStart(6)}</text>
                <text fg={regimeColor}>{r.regime.padStart(12)}</text>
                <text fg={T.dim}>{r.lag.padStart(7)}</text>
                <text fg={r.strength > 0.6 ? T.up : r.strength > 0.3 ? T.warn : T.dim}>{String(r.strength.toFixed(2)).padStart(5)}</text>
                <text fg={T.muted}> {clip(r.signal, contentW - 60)}</text>
              </box>
            )
          })}
          <text fg={T.dim}>cross-asset correlation regime · Pearson r from trading_state.db</text>
        </box>
      )}
      {tab === 4 && (
        <box title=" LAYA COGNITIVE DECISIONS " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {/* ── Model Status Card ── */}
          <box flexDirection="column" marginBottom={1}>
            <text fg={T.purple}>◈ LAYA MODEL STATUS</text>
            <text fg={T.borderBright}>{"─".repeat(70)}</text>
            <box flexDirection="row" gap={2}>
              <text fg={T.bright}>Status: <span fg={laya.modelStatus.loaded ? T.up : T.down}>{laya.modelStatus.loaded ? "● LOADED" : "○ OFFLINE"}</span></text>
              <text fg={T.bright}>Version: <span fg={T.cyan}>{laya.modelStatus.modelVersion}</span></text>
              <text fg={T.bright}>Latency: <span fg={laya.modelStatus.inferenceLatencyMs < 10 ? T.up : T.warn}>{laya.modelStatus.inferenceLatencyMs.toFixed(1)}ms</span></text>
            </box>
          </box>

          {/* ── Benchmark Metrics ── */}
          <box flexDirection="column" marginBottom={1}>
            <text fg={T.cyan}>◈ MODERNBERT-421M BENCHMARK SCORECARD</text>
            <text fg={T.borderBright}>{"─".repeat(70)}</text>
            <box flexDirection="row" gap={2}>
              <text fg={T.bright}>Validated Accuracy: <span fg={laya.benchmark.accuracy >= 90 ? T.up : T.warn}>{laya.benchmark.accuracy.toFixed(1)}%</span></text>
              <text fg={T.bright}>Raw Model Acc: <span fg={T.warn}>{laya.benchmark.rawAccuracy.toFixed(1)}%</span></text>
              <text fg={T.bright}>Freeze Recall: <span fg={laya.benchmark.freezeRecall >= 90 ? T.up : T.warn}>{laya.benchmark.freezeRecall.toFixed(1)}%</span></text>
            </box>
            <box flexDirection="row" gap={2}>
              <text fg={T.bright}>Empirical Win Rate: <span fg={laya.benchmark.winRate >= 55 ? T.up : T.down}>{laya.benchmark.winRate.toFixed(1)}%</span></text>
              <text fg={T.bright}>Calibration ECE: <span fg={laya.benchmark.ece < 0.1 ? T.up : T.warn}>{laya.benchmark.ece.toFixed(3)}</span></text>
            </box>
          </box>

          {/* ── Decision Counters ── */}
          <box flexDirection="column" marginBottom={1}>
            <text fg={T.orange}>◈ LIVE DECISION COUNTERS</text>
            <text fg={T.borderBright}>{"─".repeat(70)}</text>
            <box flexDirection="row" gap={3}>
              <text fg={T.bright}>Total Decisions: <span fg={T.cyan}>{laya.counters.totalDecisions}</span></text>
              <text fg={T.bright}>Overrides: <span fg={laya.counters.overrides > 0 ? T.warn : T.up}>{laya.counters.overrides}</span></text>
              <text fg={T.bright}>Vetoes: <span fg={laya.counters.vetoes > 0 ? T.down : T.up}>{laya.counters.vetoes}</span></text>
            </box>
          </box>

          {/* ── Active D0-D13 Decision Bundle ── */}
          <box flexDirection="column" flexGrow={1}>
            <text fg={T.purple}>◈ D0-D13 DECISION BUNDLE</text>
            <text fg={T.borderBright}>{"─".repeat(70)}</text>
            {laya.activeBundle && laya.activeBundle.decisions.length > 0 ? (
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="row">
                  <text fg={T.dim}>{"SLOT".padEnd(8)}{"OPTION".padEnd(20)}{"CONF".padStart(6)}{"STATUS".padStart(12)}</text>
                </box>
                {laya.activeBundle.decisions.map((d: LayaDecision, i: number) => {
                  const status = d.isVetoed ? "VETOED" : d.isOverridden ? "OVERRIDDEN" : "ACTIVE"
                  const statusColor = d.isVetoed ? T.down : d.isOverridden ? T.warn : T.up
                  const confColor = d.confidence >= 0.8 ? T.up : d.confidence >= 0.5 ? T.warn : T.down
                  return (
                    <box key={i} flexDirection="column">
                      <box flexDirection="row">
                        <text fg={T.purple}>{d.slot.padEnd(8)}</text>
                        <text fg={T.bright}>{clip(d.selectedOption, 20).padEnd(20)}</text>
                        <text fg={confColor}>{(d.confidence.toFixed(2)).padStart(6)}</text>
                        <text fg={statusColor}>{status.padStart(12)}</text>
                      </box>
                      {d.reasoning && (
                        <text fg={T.dim} paddingLeft={8}>↳ {clip(d.reasoning, contentW - 12)}</text>
                      )}
                    </box>
                  )
                })}
              </scrollbox>
            ) : (
              <text fg={T.dim}>awaiting D0-D13 decision bundle · macro gate armed and active</text>
            )}
          </box>

          <text fg={T.dim}>RiskController remains sole order authority · MacroFusionGate advisory firewall</text>
        </box>
      )}
    </box>
  )
}
