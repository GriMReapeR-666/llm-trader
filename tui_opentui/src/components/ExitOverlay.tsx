import { useEffect, useState } from "react"
import { T } from "../theme"

let _exitRequested = false
let _exitReason = "Operator initiated shutdown"

export function requestExit(reason?: string) {
  _exitRequested = true
  if (reason) _exitReason = reason
}

export function isExitRequested() {
  return _exitRequested
}

const GOODBYE_LINES = [
  "╔══════════════════════════════════════════════════════════╗",
  "║                                                          ║",
  "║   ████████ ███████ ████████    ██      ███████ ██████████║",
  "║      ██    ██         ██       ██      ██           ██   ║",
  "║      ██    ███████    ██       ██      ███████      ██   ║",
  "║      ██    ██         ██       ██           ██     ██   ║",
  "║      ██    ███████    ██       ██      ███████     ██   ║",
  "║                                                          ║",
  "╚══════════════════════════════════════════════════════════╝",
]

export function ExitOverlay() {
  const [visible, setVisible] = useState(false)
  const [phase, setPhase] = useState(0)

  useEffect(() => {
    if (!_exitRequested) return
    setVisible(true)

    const t1 = setTimeout(() => setPhase(1), 400)
    const t2 = setTimeout(() => setPhase(2), 1200)
    const t3 = setTimeout(() => setPhase(3), 2000)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
      clearTimeout(t3)
    }
  }, [])

  if (!visible) return null

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
      {/* Goodbye ASCII */}
      {phase >= 0 && (
        <box flexDirection="column" alignItems="center" gap={0}>
          {GOODBYE_LINES.map((line, i) => (
            <text key={i} fg={T.dim}>
              {line}
            </text>
          ))}
        </box>
      )}

      {/* Status messages */}
      {phase >= 1 && (
        <box flexDirection="column" alignItems="center" marginTop={1} gap={0}>
          <text fg={T.muted}>{_exitReason}</text>
        </box>
      )}

      {phase >= 2 && (
        <box flexDirection="column" alignItems="center" marginTop={0} gap={0}>
          <text fg={T.borderBright}>{"═".repeat(50)}</text>
          <text fg={T.dim}>positions kept managed by engine</text>
          <text fg={T.dim}>preferences saved to disk</text>
        </box>
      )}

      {phase >= 3 && (
        <box flexDirection="column" alignItems="center" marginTop={1} gap={0}>
          <text fg={T.cyan}>Command Deck closed</text>
          <text fg={T.dim}>see you next session</text>
        </box>
      )}
    </box>
  )
}
