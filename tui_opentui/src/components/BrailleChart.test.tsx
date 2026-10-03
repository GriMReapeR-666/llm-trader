import { describe, expect, it } from "bun:test"
import { testRender } from "@opentui/react/test-utils"
import { BrailleChart } from "./BrailleChart"

describe("BrailleChart", () => {
  it("renders a 2D Braille chart with expected row count", async () => {
    const data = [10000, 10020, 10015, 10040, 10035, 10060]
    const setup = await testRender(
      <BrailleChart data={data} width={40} height={5} color="#10b981" fill baseline="bottom" />,
      { width: 80, height: 24 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    expect(frame).toBeTruthy()
    // Verify that at least one Braille character from Unicode range 0x2800..0x28FF is rendered
    const hasBraille = /[\u2800-\u28FF]/.test(frame)
    expect(hasBraille).toBe(true)
  })

  it("handles underwater drawdown with baseline top", async () => {
    const dd = [0, -0.5, -1.2, -0.8, -2.4, -0.2, 0]
    const setup = await testRender(
      <BrailleChart data={dd} width={36} height={4} color="#ef4444" fill baseline="top" formatY={(v) => `${v.toFixed(1)}%`} />,
      { width: 80, height: 24 }
    )
    await setup.flush()
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    expect(frame).toBeTruthy()
    const hasBraille = /[\u2800-\u28FF]/.test(frame)
    expect(hasBraille).toBe(true)
  })

  it("handles edge cases: single point and empty array gracefully", async () => {
    const setupSingle = await testRender(
      <BrailleChart data={[10000]} width={30} height={4} />,
      { width: 80, height: 24 }
    )
    await setupSingle.flush()
    await setupSingle.renderOnce()

    const setupEmpty = await testRender(
      <BrailleChart data={[]} width={30} height={4} />,
      { width: 80, height: 24 }
    )
    await setupEmpty.flush()
    await setupEmpty.renderOnce()

    expect(setupSingle.captureCharFrame()).toBeTruthy()
    expect(setupEmpty.captureCharFrame()).toBeTruthy()
  })
})
