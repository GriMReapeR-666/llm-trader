import { describe, expect, it } from "bun:test"
import { testRender } from "@opentui/react/test-utils"
import { SafetyStrip, isStaleOrNull } from "./SafetyStrip"
import type { SafetyData } from "../types"

describe("SafetyStrip Staleness Check", () => {
  const now = 1700000000000

  it("treats null or undefined as stale/unknown", () => {
    expect(isStaleOrNull(null, now)).toBe(true)
    expect(isStaleOrNull(undefined, now)).toBe(true)
    expect(isStaleOrNull({ as_of: null }, now)).toBe(true)
    expect(isStaleOrNull({ as_of: "" }, now)).toBe(true)
    expect(isStaleOrNull({ as_of: "not-a-date" }, now)).toBe(true)
  })

  it("identifies fresh timestamps within 10s SLA", () => {
    const freshIso = new Date(now - 3000).toISOString()
    expect(isStaleOrNull({ as_of: freshIso }, now)).toBe(false)
  })

  it("identifies stale timestamps (>10s old) as unknown", () => {
    const staleIso = new Date(now - 10001).toISOString()
    expect(isStaleOrNull({ as_of: staleIso }, now)).toBe(true)
  })
})

describe("SafetyStrip Rendering", () => {
  it("renders UNKNOWN for all telemetry when data is null (engine down / deck fresh)", async () => {
    const setup = await testRender(
      <SafetyStrip data={null} />,
      { width: 180, height: 4 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()

    expect(frame).toContain("ENGINE UNKNOWN")
    expect(frame).toContain("MODE UNKNOWN")
    expect(frame).toContain("AUTONOMY UNKNOWN")
    expect(frame).toContain("KS UNKNOWN")
    expect(frame).toContain("LATCH UNKNOWN")
    expect(frame).toContain("P&L UNKNOWN")
    expect(frame).toContain("FEED UNKNOWN")
    expect(frame).toContain("BRIDGE UNKNOWN")
    expect(frame).toContain("LLM UNKNOWN")
    expect(frame).toContain("CTRLS UNKNOWN")
  })

  it("renders UNKNOWN for all telemetry when timestamps are stale (>10s)", async () => {
    const staleDate = new Date(Date.now() - 15000).toISOString()
    const staleData: SafetyData = {
      engine: { is_running: true, lock_held: true, heartbeat_age_s: 15, pid: 1234, as_of: staleDate },
      mode: { value: "PAPER", as_of: staleDate },
      autonomy: { level: "L1_PAPER", paused: false, halted: false, pause_reason: "", halt_reason: "", as_of: staleDate },
      kill_switch: { active: false, reason: "", as_of: staleDate },
      news_latch: { latched: false, since: null, source: null, headlines: [], as_of: staleDate },
      daily_pnl: { pnl: 0, limit: -500, breaker_tripped: false, as_of: staleDate },
      tick_age: { symbols: {}, as_of: staleDate },
      bridge: { connected: true, status: "CONNECTED", as_of: staleDate },
      llm_circuit: { state: "CLOSED", available: true, as_of: staleDate },
      open_controls: { count: 0, requests: [], as_of: staleDate },
    }

    const setup = await testRender(
      <SafetyStrip data={staleData} />,
      { width: 180, height: 4 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()

    expect(frame).toContain("ENGINE UNKNOWN")
    expect(frame).toContain("MODE UNKNOWN")
    expect(frame).toContain("AUTONOMY UNKNOWN")
    expect(frame).toContain("KS UNKNOWN")
    expect(frame).toContain("LATCH UNKNOWN")
    expect(frame).toContain("P&L UNKNOWN")
  })

  it("renders operational indicators when fresh and healthy", async () => {
    const freshDate = new Date().toISOString()
    const freshData: SafetyData = {
      engine: { is_running: true, lock_held: true, heartbeat_age_s: 1.2, pid: 1234, as_of: freshDate },
      mode: { value: "PAPER", as_of: freshDate },
      autonomy: { level: "L1_PAPER", paused: false, halted: false, pause_reason: "", halt_reason: "", as_of: freshDate },
      kill_switch: { active: false, reason: "", as_of: freshDate },
      news_latch: { latched: false, since: null, source: null, headlines: [], as_of: freshDate },
      daily_pnl: { pnl: 120, limit: -500, breaker_tripped: false, as_of: freshDate },
      tick_age: { symbols: { EURUSD: 150 }, as_of: freshDate },
      bridge: { connected: true, status: "CONNECTED", as_of: freshDate },
      llm_circuit: { state: "CLOSED", available: true, as_of: freshDate },
      open_controls: { count: 0, requests: [], as_of: freshDate },
    }

    const setup = await testRender(
      <SafetyStrip data={freshData} />,
      { width: 180, height: 4 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()

    expect(frame).toContain("ENGINE UP")
    expect(frame).toContain("PAPER")
    expect(frame).toContain("L1 PAPER")
    expect(frame).toContain("KS OFF")
    expect(frame).toContain("LATCH CLEAR")
    expect(frame).toContain("P&L $+120/$-500")
    expect(frame).toContain("FEED 150ms")
    expect(frame).toContain("BRIDGE OK")
    expect(frame).toContain("LLM OK")
    expect(frame).toContain("CTRLS 0")
  })

  it("renders critical states when kill switch active and news latched", async () => {
    const freshDate = new Date().toISOString()
    const alertData: SafetyData = {
      engine: { is_running: true, lock_held: true, heartbeat_age_s: 0.5, pid: 1234, as_of: freshDate },
      mode: { value: "LIVE", as_of: freshDate },
      autonomy: { level: "L2_MICRO", paused: true, halted: false, pause_reason: "emergency", halt_reason: "", as_of: freshDate },
      kill_switch: { active: true, reason: "Manual kill", as_of: freshDate },
      news_latch: { latched: true, since: freshDate, source: "tripwire", headlines: ["war declared"], as_of: freshDate },
      daily_pnl: { pnl: -600, limit: -500, breaker_tripped: true, as_of: freshDate },
      tick_age: { symbols: { EURUSD: 6000 }, as_of: freshDate },
      bridge: { connected: false, status: "DISCONNECTED", as_of: freshDate },
      llm_circuit: { state: "OPEN", available: false, as_of: freshDate },
      open_controls: { count: 2, requests: [], as_of: freshDate },
    }

    const setup = await testRender(
      <SafetyStrip data={alertData} />,
      { width: 180, height: 4 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()

    expect(frame).toContain("ENGINE UP")
    expect(frame).toContain("LIVE")
    expect(frame).toContain("AUTONOMY PAUSED")
    expect(frame).toContain("KILLSWITCH ACTIVE")
    expect(frame).toContain("NEWS LATCHED")
    expect(frame).toContain("BREAKER TRIPPED")
    expect(frame).toContain("FEED 6.0s")
    expect(frame).toContain("BRIDGE DOWN")
    expect(frame).toContain("LLM OPEN")
    expect(frame).toContain("CTRLS 2")
  })
})
