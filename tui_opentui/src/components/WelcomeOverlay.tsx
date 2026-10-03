import { useEffect, useState } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { T } from "../theme"

const LOGO_LINES = [
  "██╗     ███████╗ ██████╗ ██╗  ██╗████████╗██████╗  ██████╗ ██╗   ██╗████████╗███████╗",
  "██║     ██╔════╝██╔═══██╗╚██╗██╔╝╚══██╔══╝██╔══██╗██╔═══██╗██║   ██║╚══██╔══╝██╔════╝",
  "██║     █████╗  ██║   ██║ ╚███╔╝    ██║   ██████╔╝██║   ██║██║   ██║   ██║   ███████╗",
  "██║     ██╔══╝  ██║   ██║ ██╔██╗    ██║   ██╔══██╗██║   ██║██║   ██║   ██║   ╚════██║",
  "███████╗███████╗╚██████╔╝██╔╝ ██╗   ██║   ██║  ██║╚██████╔╝╚██████╔╝   ██║   ███████║",
  "╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝    ╚═╝   ╚══════╝",
]

const TAGLINE = " autonomous multi-agent trading · command deck"

const STEPS = [
  {
    icon: "◈",
    title: "MARKET",
    desc: "Feed [/] · Cockpit [2] · Positions [3]",
    color: T.cyan,
  },
  {
    icon: "◈",
    title: "ANALYSIS",
    desc: "Brain · Causal · Macro · Strategies · Fusion",
    color: T.purple,
  },
  {
    icon: "◈",
    title: "CONTROL",
    desc: "/pause · /flatten · /kill · Approvals",
    color: T.teal,
  },
]

export function WelcomeOverlay() {
  const transport = useDeck((s) => s.transport)
  const promptFocused = useDeck((s) => s.promptFocused)
  const paletteOpen = useDeck((s) => s.paletteOpen)
  const [dismissed, setDismissed] = useState(() => Boolean(process.env.NO_WELCOME === "1" || process.env.CI === "1"))
  const [step, setStep] = useState(-1)
  const [logoRevealed, setLogoRevealed] = useState(0)

  // Unfocus prompt while welcome is showing
  useEffect(() => {
    if (!dismissed && store.get().promptFocused) store.set({ promptFocused: false })
  }, [dismissed])

  // Animated logo reveal — one line at a time
  useEffect(() => {
    if (dismissed) return
    const timers: ReturnType<typeof setTimeout>[] = []
    for (let i = 0; i < LOGO_LINES.length; i++) {
      timers.push(setTimeout(() => setLogoRevealed(i + 1), 120 * (i + 1)))
    }
    // Show first step after logo completes
    timers.push(setTimeout(() => setStep(0), 120 * LOGO_LINES.length + 200))
    return () => timers.forEach(clearTimeout)
  }, [dismissed])

  // Auto-dismiss after 18s or on transport live
  useEffect(() => {
    const t = setTimeout(() => setDismissed(true), 18000)
    return () => clearTimeout(t)
  }, [])
  useEffect(() => {
    if (transport === "live") {
      const t = setTimeout(() => setDismissed(true), 3000)
      return () => clearTimeout(t)
    }
  }, [transport])

  // Keyboard navigation
  useKeyboard((key) => {
    if (dismissed || paletteOpen || promptFocused) return
    if (key.name === "escape") {
      setDismissed(true)
      return
    }
    if (key.name === "left" || key.name === "h") {
      setStep((s) => Math.max(0, s - 1))
      return
    }
    if (key.name === "right" || key.name === "l" || key.name === " ") {
      if (step < 2) setStep((s) => Math.min(2, s + 1))
      else setDismissed(true)
      return
    }
    if (key.name === "return" || key.name === "enter") {
      store.set({ view: "cockpit" as any })
      setDismissed(true)
      return
    }
  })

  if (dismissed) return null

  const current = step >= 0 ? STEPS[step] : null

  return (
    <box
      position="absolute"
      top={0}
      left={0}
      width="100%"
      height="100%"
      backgroundColor={T.bg}
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      padding={0}
    >
      {/* ASCII Logo */}
      <box flexDirection="column" alignItems="center" gap={0}>
        {LOGO_LINES.slice(0, logoRevealed).map((line, i) => (
          <text key={i} fg={i === logoRevealed - 1 ? T.cyan : T.dim}>
            {line}
          </text>
        ))}
      </box>

      {/* Tagline */}
      {logoRevealed >= LOGO_LINES.length && (
        <box flexDirection="column" alignItems="center" marginTop={0}>
          <text fg={T.muted}>{TAGLINE}</text>
        </box>
      )}

      {/* Step cards */}
      {step >= 0 && (
        <box flexDirection="column" alignItems="center" marginTop={1} gap={0}>
          {/* Separator */}
          <text fg={T.borderBright}>
            {"═".repeat(62)}
          </text>

          {/* Navigation dots */}
          <box flexDirection="row" gap={1} marginTop={0} marginBottom={0}>
            {STEPS.map((_, i) => (
              <text key={i} fg={i === step ? T.cyan : T.dim}>
                {i === step ? "●" : "○"}
              </text>
            ))}
          </box>

          {/* Current step */}
          {current && (
            <box flexDirection="column" alignItems="center" gap={0} marginTop={0}>
              <box flexDirection="row" gap={1}>
                <text fg={current.color}>{current.icon}</text>
                <text fg={T.bright}>{current.title}</text>
              </box>
              <text fg={T.muted}>{current.desc}</text>
            </box>
          )}

          {/* Separator */}
          <text fg={T.borderBright}>
            {"═".repeat(62)}
          </text>
        </box>
      )}

      {/* Footer bar */}
      {logoRevealed >= LOGO_LINES.length && (
        <box
          flexDirection="row"
          justifyContent="space-between"
          alignItems="center"
          width={62}
          marginTop={1}
        >
          <text fg={T.dim}>
            {step < 2 ? "→ next · Enter: cockpit · Esc: skip" : "Enter: start · Esc: skip"}
          </text>
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>transport </text>
            <text fg={
              transport === "live" ? T.teal :
              transport === "connecting" ? T.warn :
              T.cyan
            }>
              {transport.toUpperCase()}
            </text>
          </box>
        </box>
      )}
    </box>
  )
}
