/** Temp proof harness: render Observatory / Graph / Orchestration screens with the
 * real live transport attached (API on :8000) and capture actual char frames. */
import { testRender } from "@opentui/react/test-utils"
import { App } from "./App"
import { store } from "./store"

async function main() {
  // Attach the real live transport (polls API on :8000, boots snapshot + WS)
  const { startLive } = await import("./transport/live")
  void startLive().catch(() => {})

  const setup = await testRender(<App />, { width: 120, height: 34 })
  // Give the transport time to pull the snapshot (can take ~60s cold)
  await new Promise((r) => setTimeout(r, 5000))

  const shots: Array<[string, string]> = [
    ["OBSERVATORY (empty-state honest check)", "observatory"],
    ["GRAPH (no fabricated PASS check)", "graph"],
    ["ORCHESTRATION (live + static-audit rows)", "orchestration"],
  ]

  console.log("TRANSPORT before wait:", store.get().transport)
  // Wait up to 90s for LIVE so the proof uses real data
  for (let i = 0; i < 45; i++) {
    if (store.get().transport === "live") break
    await new Promise((r) => setTimeout(r, 2000))
  }
  console.log("TRANSPORT after wait:", store.get().transport,
    "| strategies:", store.get().strategies.length,
    "| validation:", store.get().validationPipeline.length)

  for (const [label, view] of shots) {
    store.set({ view: view as any, paletteOpen: false })
    for (let i = 0; i < 6; i++) {
      await setup.flush()
      await setup.renderOnce()
    }
    console.log(`\n=== ${label} ===`)
    console.log(setup.captureCharFrame())
  }

  setup.renderer.destroy()
}

main()
  .then(() => {
    console.log("\nSCREEN_PROOF_OK")
    process.exit(0)
  })
  .catch((err) => {
    console.error("SCREEN_PROOF_FAILED:", err)
    process.exit(1)
  })
