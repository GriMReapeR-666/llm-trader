import { testRender } from "@opentui/react/test-utils"
import { App } from "./App"
import { store } from "./store"

async function main() {
  const setup = await testRender(<App />, { width: 120, height: 34 })
  for (let i = 0; i < 4; i++) {
    await setup.flush()
    await setup.renderOnce()
  }

  console.log("=== VIEW 1 · STREAM (empty real state — no synthetic seed) ===")
  console.log(setup.captureCharFrame())

  for (const [name, view] of [
    ["COCKPIT", "cockpit"],
    ["BRAIN", "brain"],
    ["CAUSAL", "causal"],
    ["MACRO", "macro"],
    ["POSITIONS", "positions"],
    ["JOURNAL", "journal"],
    ["TASKS", "tasks"],
    ["STRATEGIES", "strategies"],
    ["ANALYTICS", "analytics"],
    ["APPROVALS", "approvals"],
    ["MAP", "map"],
    ["GRAPH", "graph"],
    ["SKILLS", "skills"],
    ["BACKTEST", "backtest"],
    ["ALPHA", "alpha"],
    ["DIAGNOSTICS", "diagnostics"],
    ["WORKFLOWS", "workflows"],
    ["LIFECYCLE", "lifecycle"],
    ["FUSION", "fusion"],
    ["RESEARCH", "research"],
    ["DATASOURCES", "datasources"],
    ["RL", "rl"],
    ["VALIDATION", "validation"],
    ["MEMORY", "memory"],
    ["DELIBERATION", "deliberation"],
    ["SYSTEM_HEALTH", "health"],
    ["SETTINGS", "settings"],
    ["AUTONOMY", "autonomy"],
    ["REWARDS", "rewards"],
    ["SECURITY", "security"],
    ["GROWTH", "growth"],
    ["MODELS", "models"],
    ["METRICS", "metrics"],
    ["DRILLS", "drills"],
    ["MT5", "mt5"],
  ] as const) {
    store.set({ view, paletteOpen: false })
    for (let i = 0; i < 6; i++) {
      await setup.flush()
      await setup.renderOnce()
    }
    console.log(`\n=== VIEW · ${name} ===`)
    console.log(setup.captureCharFrame())
  }

  setup.renderer.destroy()
}

main()
  .then(() => {
    console.log("\nSELFTEST OK")
    process.exit(0)
  })
  .catch((err) => {
    console.error("SELFTEST FAILED:", err)
    process.exit(1)
  })