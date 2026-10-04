import { describe, expect, it } from "bun:test"
import { renderControlOutcome, applyIntervention } from "./CommandPalette"
import { store } from "../store"

describe("CommandPalette Outcome Rendering", () => {
  it("renders APPLIED with closed and cancelled counts", () => {
    const outcome = renderControlOutcome({
      status: "APPLIED",
      result: { closed: 3, cancelled: 1, failed: 0, remaining: 0, sweeps: 2 },
    })
    expect(outcome).toBe("APPLIED (3 closed, 1 cancelled)")
  })

  it("renders APPLIED with failed count if non-zero", () => {
    const outcome = renderControlOutcome({
      status: "APPLIED",
      result: { closed: 2, cancelled: 0, failed: 1, remaining: 1, sweeps: 5 },
    })
    expect(outcome).toBe("APPLIED (2 closed, 0 cancelled, 1 failed)")
  })

  it("renders FAILED with error reason", () => {
    const outcome = renderControlOutcome({
      status: "FAILED",
      result: { error: "broker unavailable" },
    })
    expect(outcome).toBe("FAILED (broker unavailable)")
  })

  it("renders EXPIRED when engine times out", () => {
    const outcome = renderControlOutcome({
      status: "EXPIRED",
      result: { error: "engine offline: not acknowledged within expiry window" },
    })
    expect(outcome).toBe("EXPIRED")
  })

  it("renders REQUESTED and ACKNOWLEDGED states as-is", () => {
    expect(renderControlOutcome({ status: "REQUESTED" })).toBe("REQUESTED")
    expect(renderControlOutcome({ status: "ACKNOWLEDGED" })).toBe("ACKNOWLEDGED")
  })

  it("fails closed when deck is offline and refuses to simulate local execution", async () => {
    store.set({ transport: "synthetic" })
    const prevAlertCount = store.get().alerts.length
    await applyIntervention("flatten")
    const alerts = store.get().alerts
    expect(alerts.length).toBeGreaterThan(prevAlertCount)
    const latest = alerts[0]
    expect(latest.text).toContain("NOT SENT: deck offline")
  })
})
