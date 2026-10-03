import { testRender } from "@opentui/react/test-utils"
import { App } from "./App"
import { store } from "./store"

async function testWidth(w: number) {
  store.set({
    view: "analytics",
    account: {
      ...store.get().account,
      equity: 9969.03,
      peakEquity: 10026.37,
      drawdownPct: 0.57,
      equityCurve: [10000, 10026, 10010, 9990, 9975, 9969],
    }
  })
  const setup = await testRender(<App />, { width: w, height: 34 })
  for (let i = 0; i < 3; i++) {
    await setup.flush()
    await setup.renderOnce()
  }
  const frame = setup.captureCharFrame()
  const lines = frame.split("\n")
  console.log(`\n=== Terminal width: ${w} ===`)
  for (let i = 0; i < Math.min(22, lines.length); i++) {
    console.log(lines[i])
  }
}

async function main() {
  await testWidth(140)
  await testWidth(120)
  await testWidth(100)
}

main().catch(console.error)

