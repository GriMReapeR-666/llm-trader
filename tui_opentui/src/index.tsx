import { createCliRenderer } from "@opentui/core"
import { createRoot } from "@opentui/react"
import { App } from "./App"
import { store } from "./store"
import { DAG_SHARE_DEFAULT, flushPrefsSave, loadDeckPrefs } from "./prefs"
import { requestExit, isExitRequested } from "./components/ExitOverlay"

// Professional entry: user-friendly bootstrap with clear purpose, sensible defaults, immediate feedback (Doherty 400ms)
type TransportMode = "auto" | "mcp" | "mt5" | "live"
const TRANSPORT_HELP: Record<TransportMode, string> = {
  auto: "Auto — live engine on :8000, fail closed if unreachable (recommended)",
  mcp: "MCP — direct MT5 terminal bridge (requires MT5 running)",
  mt5: "MT5 — alias for mcp",
  live: "Live — force live engine, no fallback (for prod)",
}

function resolveTransport(): TransportMode {
  const raw = (process.env.TRANSPORT ?? "auto").toLowerCase() as TransportMode
  const known: TransportMode[] = ["auto", "mcp", "mt5", "live"]
  if (known.includes(raw)) return raw
  // Postel #14: accept variations, warn and default
  const hint = raw ? `unknown TRANSPORT="${raw}"` : "no TRANSPORT"
  store.pushStream({ ts: Date.now(), kind: "system", agent: "SYSTEM", title: `Transport: ${hint} — using auto`, lines: [TRANSPORT_HELP.auto] })
  return "auto"
}

function logBoot(step: string, detail?: string) {
  store.pushStream({ ts: Date.now(), kind: "system", agent: "SYSTEM", title: step, lines: detail ? [detail] : [] })
  store.pushOpEvent({ ts: Date.now(), actor: "SYSTEM", type: "BOOT", severity: "info", payload: `${step}${detail ? ` · ${detail}` : ""}` })
}

async function startTransport(mode: TransportMode) {
  // Hick #1 one clear purpose per branch, sensible default #13
  if (mode === "mcp" || mode === "mt5") {
    logBoot("Transport: MCP", "connecting to MT5 terminal...")
    store.set({ transport: "connecting" as any })
    try {
      const { startMcp } = await import("./transport/mcp")
      const stop = await startMcp()
      ;(globalThis as any).__deckStop = stop
      if (store.get().transport === "stale" || store.get().transport === "connecting") {
        store.pushAlert("critical", "MCP offline — check MT5 terminal open + MT5_LOGIN env.")
        logBoot("MCP unavailable — fail closed", "MT5 terminal not responding. Fix: start MT5 and set MT5_LOGIN/MT5_PASSWORD/MT5_SERVER, then restart deck. No synthetic fallback exists.")
        throw new Error("MCP transport unavailable")
      } else {
        logBoot("Transport: MCP connected", "MT5 bridge live — ticks/positions/orders streaming")
      }
    } catch (err: any) {
      store.pushAlert("critical", `MCP start failed: ${String(err?.message ?? err).slice(0,120)}`)
      logBoot("MCP error — fail closed", String(err?.message ?? err).slice(0, 160))
      throw err
    }
    return
  }
  if (mode === "live") {
    logBoot("Transport: LIVE", "connecting to live engine :8000 (no fallback)")
    const { startLive } = await import("./transport/live")
    try {
      const stop = await startLive()
      ;(globalThis as any).__deckStop = stop
    } catch (err: any) {
      store.pushAlert("critical", `Live engine unreachable: ${String(err?.message ?? err).slice(0,120)} — no fallback in live mode`)
      logBoot("Live failed", "Start gui_api.py on :8000, or use TRANSPORT=auto. Press Ctrl+C to retry with auto.")
      throw err
    }
    return
  }
  // auto (default)
  logBoot("Transport: AUTO", TRANSPORT_HELP.auto)
  const { startLive } = await import("./transport/live")
  let stop: (() => void) | null = null
  try {
    stop = await startLive()
    ;(globalThis as any).__deckStop = stop
  } catch {
    // will fail closed below if still connecting
  }
  // Give live 2.5s to bootstrap (Doherty 400ms + window) — prevent flicker
  await new Promise((r) => setTimeout(r, 2500))
  if (store.get().transport === "connecting") {
    store.pushAlert("critical", "Live engine not on :8000 — deck refuses to start with synthetic data.")
    logBoot("Auto: live not found — fail closed", "Run: python ui/gui_api.py then restart the deck. No synthetic fallback exists.")
    throw new Error("Live engine not reachable on :8000")
  } else if (stop) {
    logBoot("Transport: AUTO → LIVE", "connected to live engine")
  }
  if (process.env.MT5_MCP_AUTOSTART === "1") {
    // Progressive disclosure #19: optional MT5 alongside live
    import("./transport/mcp").then(({ startMcp }) => startMcp().catch(() => {})).then(() => logBoot("MT5 autostart", "MCP bridge attached alongside live"))
  }
}

async function main() {
  const mode = resolveTransport()

  // Peak-End #10 satisfying welcome — clear purpose (UX Req 1)
  store.pushStream({
    ts: Date.now(),
    kind: "system",
    agent: "SYSTEM",
    title: "LLM TRADER — Command Deck booting",
    lines: [`mode ${mode} · ${TRANSPORT_HELP[mode]}`, "shortest path: watch cockpit [2] → stream [/] → tasks [5]"],
  })

  // Preferences restored from disk? Surface it so the operator knows the
  // layout was carried over rather than silently reset (auditable feed).
  const restored = loadDeckPrefs().dagShare
  if (restored !== DAG_SHARE_DEFAULT) {
    store.pushStream({
      ts: Date.now(),
      kind: "system",
      agent: "SYSTEM",
      title: `Layout preference restored — DAG split ${Math.round(restored * 100)}%`,
      lines: ["stored in ~/.quantnexus/deck-prefs.json · tweak on CAUSAL with - / ="],
    })
  }

  // Start transport before renderer so first frame already has state (Doherty)
  const transportPromise = startTransport(mode)

  // Clean render: no scanlines / vignette / bloom — those paint a striped,
  // flickering overlay that hurts readability. Solid background only.
  const renderer = await createCliRenderer({
    exitOnCtrlC: true,
    backgroundColor: "#0b0e14",
    useMouse: true,
    enableMouseMovement: true,
  })

  // Error boundary: preserve work #15
  const root = createRoot(renderer)
  try {
    root.render(<App />)
  } catch (err: any) {
    store.pushAlert("critical", `UI render failed: ${String(err?.message ?? err).slice(0,160)} — restart deck after fixing`)
    throw err
  }

  await transportPromise

  // Accessibility: announce ready (Von Restorff primary action obvious)
  store.pushOpEvent({ ts: Date.now(), actor: "SYSTEM", type: "READY", severity: "info", payload: `Command deck ready · transport ${mode} · press / for palette · 1-6 sections · [ ] screens` })
}

// Global Postel: graceful failure, explain fix, preserve advice
main().catch((err) => {
  // Clean crash screen — no raw stack dumps
  const msg = String(err?.message ?? err).slice(0, 200)
  const isSegfault = msg.toLowerCase().includes("segmentation") || msg.toLowerCase().includes("segfault")
  const isBunBug = msg.includes("bun has crashed") || isSegfault

  console.error("")
  console.error("  ╔══════════════════════════════════════════════════════════╗")
  console.error("  ║  ✕ Command Deck failed to start                          ║")
  console.error("  ╚══════════════════════════════════════════════════════════╝")
  console.error("")

  if (isBunBug) {
    console.error("  This appears to be a Bun runtime issue, not a code problem.")
    console.error("  Try these steps in order:")
    console.error("")
    console.error("    1. Update Bun:    bun upgrade")
    console.error("    2. Clear cache:   rm -rf ~/.bun/install/cache && bun install")
    console.error("    3. Retry boot:    bun run dev")
    console.error("")
  } else {
    console.error("  Error: " + msg)
    console.error("")
    console.error("  Recovery options:")
    console.error("    1. python ui/gui_api.py  — start live engine on :8000, then restart deck")
    console.error("    2. bun run typecheck     — verify installation")
    console.error("    3. bun run selftest      — smoke-test deck rendering")
    console.error("")
  }

  process.exit(1)
})

// Handle Ctrl+C — show exit overlay for a satisfying goodbye, then close
let _exitTimeout: ReturnType<typeof setTimeout> | null = null
process.on("SIGINT", () => {
  // Prevent double-fire
  if (isExitRequested()) return

  // Trigger the exit overlay
  requestExit("Operator initiated shutdown · Ctrl+C")

  // Flush pending writes and stop transport
  flushPrefsSave()
  try { (globalThis as any).__deckStop?.() } catch {}

  // Give the exit overlay time to render its animation, then close
  if (_exitTimeout) clearTimeout(_exitTimeout)
  _exitTimeout = setTimeout(() => {
    console.log("\n  Command Deck closed — positions kept managed by engine\n")
    process.exit(0)
  }, 2800)
})
