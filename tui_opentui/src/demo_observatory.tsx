import { createCliRenderer } from "@opentui/core"
import { createRoot, useKeyboard } from "@opentui/react"
import { T } from "./theme"
import { StrategyObservatoryScreen } from "./screens/StrategyObservatoryScreen"

function StandaloneObservatoryApp() {
  const handleQuit = () => {
    console.log("\n  Observatory Demo closed cleanly.\n")
    process.exit(0)
  }

  useKeyboard((key) => {
    if (key.name === "q" || (key.name === "c" && key.ctrl)) {
      handleQuit()
    }
  })

  return (
    <box flexDirection="column" width="100%" height="100%" backgroundColor={T.bg}>
      <StrategyObservatoryScreen isStandalone={true} onQuit={handleQuit} />
    </box>
  )
}

async function main() {
  // Bootstrap live engine transport bridge (polls FastAPI :8000 when active)
  try {
    const { startLive } = await import("./transport/live")
    startLive().catch(() => {})
  } catch {
    /* Standby fallback */
  }

  const renderer = await createCliRenderer({
    exitOnCtrlC: true,
    backgroundColor: "#0b0e14",
    useMouse: true,
    enableMouseMovement: true,
  })

  const root = createRoot(renderer)
  root.render(<StandaloneObservatoryApp />)
}

main().catch((err) => {
  console.error("Failed to launch Strategy Observatory cockpit:", err)
  process.exit(1)
})
