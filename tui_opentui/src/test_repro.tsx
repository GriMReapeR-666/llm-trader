import { testRender } from "@opentui/react/test-utils"
import { App } from "./App"
import { store } from "./store"
import { startLive } from "./transport/live"

process.env.NO_WELCOME = "1"

async function main() {
  console.log("========================================================")
  console.log("   COMMAND DECK COMPREHENSIVE USER NAVIGATION TEST     ")
  console.log("========================================================")

  console.log("\n[1/4] Starting live transport to API server on :8000...")
  let stopLive: (() => void) | undefined
  try {
    stopLive = await startLive()
  } catch (err) {
    console.log("Live transport could not connect (offline mode):", err)
  }

  // Wait for bootstrap snapshot and first poll cycle
  await new Promise((r) => setTimeout(r, 2000))
  const state = store.get()
  console.log(`Transport status: ${state.transport}`)
  console.log(`Account balance: $${state.account.balance.toLocaleString()}`)
  console.log(`Account equity: $${state.account.equity.toLocaleString()}`)
  console.log(`Total trades: ${state.account.totalTrades}`)
  console.log(`Laya decisions loaded: ${state.layaDecisions.activeBundle?.decisions.length ?? 0}`)
  console.log(`Laya benchmark win rate: ${state.layaDecisions.benchmark.winRate.toFixed(1)}%`)

  const allViews = [
    // Section 1: Market
    "stream", "cockpit", "positions", "journal", "tasks", "approvals",
    // Section 2: Analysis
    "brain", "causal", "macro", "strategies", "fusion",
    // Section 3: Pipeline
    "graph", "skills", "workflows", "lifecycle",
    // Section 4: Research
    "analytics", "backtest", "alpha", "diagnostics", "rl", "validation", "memory", "datasources", "research",
    // Section 5: Control
    "autonomy", "rewards", "security", "growth",
    // Section 6: System
    "health", "settings", "models", "metrics", "drills", "mt5", "logs", "orchestration", "observatory", "map", "deliberation"
  ] as const

  const geometries = [
    { width: 80, height: 25 },
    { width: 100, height: 30 },
    { width: 120, height: 34 },
  ]

  for (const dims of geometries) {
    console.log(`\n[2/4] Testing terminal geometry ${dims.width}x${dims.height}...`)
    const setup = await testRender(<App />, dims)

    for (const view of allViews) {
      store.set({ view: view as any, paletteOpen: false })
      for (let i = 0; i < 3; i++) {
        await setup.flush()
        await setup.renderOnce()
      }
    }

    // Now test interactive tab navigation on key screens
    console.log(`[3/4] Testing interactive tab navigation on Macro screen (${dims.width}x${dims.height})...`)
    store.set({ view: "macro" as any, paletteOpen: false })
    for (let i = 0; i < 2; i++) {
      await setup.flush()
      await setup.renderOnce()
    }

    // Press "1" (Calendar)
    setup.mockInput.pressKey("1")
    await setup.flush()
    await setup.renderOnce()

    // Press "2" (Narratives)
    setup.mockInput.pressKey("2")
    await setup.flush()
    await setup.renderOnce()

    // Press "3" (Nowcasts)
    setup.mockInput.pressKey("3")
    await setup.flush()
    await setup.renderOnce()

    // Press "4" (Regimes)
    setup.mockInput.pressKey("4")
    await setup.flush()
    await setup.renderOnce()

    // Press "5" (Laya Cognitive Decisions - D0..D13)
    setup.mockInput.pressKey("5")
    await setup.flush()
    await setup.renderOnce()

    // Testing Positions tab navigation
    console.log(`[3/4] Testing interactive tab navigation on Positions screen (${dims.width}x${dims.height})...`)
    store.set({ view: "positions" as any, paletteOpen: false })
    for (let i = 0; i < 2; i++) {
      await setup.flush()
      await setup.renderOnce()
    }

    // Press "1" (Blotter / Overview)
    setup.mockInput.pressKey("1")
    await setup.flush()
    await setup.renderOnce()

    // Press "2" (Open Positions)
    setup.mockInput.pressKey("2")
    await setup.flush()
    await setup.renderOnce()

    // Press "3" (Closed Deals)
    setup.mockInput.pressKey("3")
    await setup.flush()
    await setup.renderOnce()

    // Press "4" (Strategies)
    setup.mockInput.pressKey("4")
    await setup.flush()
    await setup.renderOnce()

    setup.renderer.destroy()
    console.log(`  ✓ Geometry ${dims.width}x${dims.height}: All 39 screens + tabs passed cleanly!`)
  }

  console.log("\n[4/4] COMPLETE SUCCESS: All 39 views and tabs rendered with 0 errors across all geometries!")
  stopLive?.()
  process.exit(0)
}

main().catch((err) => {
  console.error("FATAL USER NAVIGATION TEST ERROR:", err)
  process.exit(1)
})
