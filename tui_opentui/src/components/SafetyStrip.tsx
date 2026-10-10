import { useEffect, useState } from "react"
import { useDeck } from "../store"
import { T } from "../theme"
import { apiFetch } from "../transport/live"
import type { SafetyData } from "../types"

export function isStaleOrNull(
  field: { as_of?: string | null } | null | undefined,
  nowMs: number,
): boolean {
  if (!field || !field.as_of) return true
  const t = Date.parse(field.as_of)
  if (Number.isNaN(t)) return true
  return nowMs - t > 10000 // > 10s is stale -> UNKNOWN
}

export function SafetyStrip({ data: propData }: { data?: SafetyData | null }) {
  const [fetchData, setFetchData] = useState<SafetyData | null>(null)
  const now = useDeck((s) => s.now)

  useEffect(() => {
    if (propData !== undefined) return
    let active = true
    const poll = async () => {
      try {
        const res = await apiFetch("/api/safety")
        if (res.ok && active) {
          const json = (await res.json()) as SafetyData
          setFetchData(json)
        } else if (active) {
          setFetchData(null)
        }
      } catch {
        if (active) setFetchData(null)
      }
    }
    void poll()
    const timer = setInterval(poll, 2000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [propData])

  const data = propData !== undefined ? propData : fetchData
  const nowMs = now || Date.now()

  // 1. Engine
  const engineUnknown = isStaleOrNull(data?.engine, nowMs)
  const engineRunning = data?.engine?.is_running
  const engineText = engineUnknown
    ? "ENGINE UNKNOWN"
    : engineRunning
      ? "ENGINE UP"
      : "ENGINE DOWN"
  const engineColor = engineUnknown ? T.warn : engineRunning ? T.muted : T.critical

  // 2. Mode
  const modeUnknown = isStaleOrNull(data?.mode, nowMs)
  const modeVal = data?.mode?.value?.toUpperCase()
  const modeText = modeUnknown ? "MODE UNKNOWN" : modeVal || "UNKNOWN"
  const modeColor = modeUnknown ? T.warn : modeVal === "LIVE" ? T.down : T.cyan

  // 3. Autonomy
  const autoUnknown = isStaleOrNull(data?.autonomy, nowMs)
  const autoText = autoUnknown
    ? "AUTONOMY UNKNOWN"
    : data?.autonomy?.halted
      ? "AUTONOMY HALTED"
      : data?.autonomy?.paused
        ? "AUTONOMY PAUSED"
        : data?.autonomy?.level?.replace("_", " ") || "UNKNOWN"
  const autoColor = autoUnknown
    ? T.warn
    : data?.autonomy?.halted
      ? T.critical
      : data?.autonomy?.paused
        ? T.warn
        : T.muted

  // 4. Kill Switch
  const ksUnknown = isStaleOrNull(data?.kill_switch, nowMs)
  const ksActive = data?.kill_switch?.active
  const ksText = ksUnknown
    ? "KS UNKNOWN"
    : ksActive
      ? "KILLSWITCH ACTIVE"
      : "KS OFF"
  const ksColor = ksUnknown ? T.warn : ksActive ? T.critical : T.muted

  // 5. News Latch
  const latchUnknown = isStaleOrNull(data?.news_latch, nowMs)
  const isLatched = data?.news_latch?.latched
  const latchText = latchUnknown
    ? "LATCH UNKNOWN"
    : isLatched
      ? "NEWS LATCHED"
      : "LATCH CLEAR"
  const latchColor = latchUnknown ? T.warn : isLatched ? T.critical : T.muted

  // 6. Daily P&L
  const pnlUnknown = isStaleOrNull(data?.daily_pnl, nowMs)
  const pnlVal = data?.daily_pnl?.pnl ?? 0
  const limitVal = data?.daily_pnl?.limit ?? 0
  const breakerTripped = data?.daily_pnl?.breaker_tripped
  const pnlText = pnlUnknown
    ? "P&L UNKNOWN"
    : breakerTripped
      ? "BREAKER TRIPPED"
      : `P&L $${pnlVal >= 0 ? "+" : ""}${pnlVal.toFixed(0)}/$${limitVal.toFixed(0)}`
  const pnlColor = pnlUnknown
    ? T.warn
    : breakerTripped
      ? T.critical
      : pnlVal < 0
        ? T.down
        : T.muted

  // 7. Feed Age
  const feedUnknown = isStaleOrNull(data?.tick_age, nowMs)
  let feedText = "FEED UNKNOWN"
  let feedColor: string = T.warn
  if (!feedUnknown) {
    const syms = data?.tick_age?.symbols || {}
    const ages = Object.values(syms)
    if (ages.length === 0) {
      feedText = "FEED IDLE"
      feedColor = T.dim
    } else {
      const maxAge = Math.max(...ages)
      feedText =
        maxAge < 1000
          ? `FEED ${Math.round(maxAge)}ms`
          : `FEED ${(maxAge / 1000).toFixed(1)}s`
      feedColor = maxAge > 5000 ? T.warn : T.muted
    }
  }

  // 8. Bridge
  const bridgeUnknown = isStaleOrNull(data?.bridge, nowMs)
  const bridgeConn = data?.bridge?.connected
  const bridgeText = bridgeUnknown
    ? "BRIDGE UNKNOWN"
    : bridgeConn
      ? "BRIDGE OK"
      : "BRIDGE DOWN"
  const bridgeColor = bridgeUnknown ? T.warn : bridgeConn ? T.muted : T.critical

  // 9. LLM Circuit
  const llmUnknown = isStaleOrNull(data?.llm_circuit, nowMs)
  const circuitState = data?.llm_circuit?.state || "UNKNOWN"
  const llmText = llmUnknown
    ? "LLM UNKNOWN"
    : circuitState === "CLOSED"
      ? "LLM OK"
      : `LLM ${circuitState}`
  const llmColor =
    llmUnknown ? T.warn : circuitState === "CLOSED" ? T.muted : T.critical

  // 10. Open controls
  const ctrlUnknown = isStaleOrNull(data?.open_controls, nowMs)
  const ctrlCount = data?.open_controls?.count ?? 0
  const ctrlText = ctrlUnknown ? "CTRLS UNKNOWN" : `CTRLS ${ctrlCount}`
  const ctrlColor = ctrlUnknown ? T.warn : ctrlCount > 0 ? T.warn : T.dim

  return (
    <box flexDirection="column" backgroundColor={T.raised} flexShrink={0} width="100%">
      <box flexDirection="row" gap={1} paddingLeft={1} paddingRight={1} height={1}>
        <text fg={engineColor}>● {engineText}</text>
        <text fg={T.dim}>|</text>
        <text fg={modeColor}>{modeText}</text>
        <text fg={T.dim}>|</text>
        <text fg={autoColor}>{autoText}</text>
        <text fg={T.dim}>|</text>
        <text fg={ksColor}>{ksText}</text>
        <text fg={T.dim}>|</text>
        <text fg={latchColor}>{latchText}</text>
        <text fg={T.dim}>|</text>
        <text fg={pnlColor}>{pnlText}</text>
        <text fg={T.dim}>|</text>
        <text fg={feedColor}>{feedText}</text>
        <text fg={T.dim}>|</text>
        <text fg={bridgeColor}>{bridgeText}</text>
        <text fg={T.dim}>|</text>
        <text fg={llmColor}>{llmText}</text>
        <text fg={T.dim}>|</text>
        <text fg={ctrlColor}>{ctrlText}</text>
      </box>
    </box>
  )
}
