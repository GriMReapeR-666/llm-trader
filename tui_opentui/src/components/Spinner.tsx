import { T, sciPulse } from "../theme"

const SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
const DOTS_FRAMES = ["●○○○", "○●○○", "○○●○", "○○○●"]
const PULSE_FRAMES = ["▪", "▫", "▪", "▫"]

export function Spinner(props: {
  variant?: "dots" | "pulse" | "spin"
  color?: string
  now?: number
}) {
  const color = props.color ?? T.cyan
  const now = props.now ?? Date.now()
  const variant = props.variant ?? "dots"

  if (variant === "spin") {
    const idx = Math.floor(now / 80) % SPIN_FRAMES.length
    return <text fg={color}>{SPIN_FRAMES[idx]}</text>
  }

  if (variant === "pulse") {
    const idx = Math.floor(now / 400) % PULSE_FRAMES.length
    return <text fg={color}>{PULSE_FRAMES[idx]}</text>
  }

  // dots variant
  const idx = Math.floor(now / 300) % DOTS_FRAMES.length
  return <text fg={color}>{DOTS_FRAMES[idx]}</text>
}

export function PulseDot(props: {
  active: boolean
  now?: number
  color?: string
  interval?: number
}) {
  const now = props.now ?? Date.now()
  const interval = props.interval ?? 500
  const on = Math.floor(now / interval) % 2 === 0
  const icon = props.active ? (on ? "●" : "○") : "○"
  const color = props.active ? (props.color ?? T.up) : T.dim
  return <text fg={color}>{icon}</text>
}

export function PulseText(props: {
  text: string
  active: boolean
  now?: number
  color?: string
  interval?: number
}) {
  const now = props.now ?? Date.now()
  const interval = props.interval ?? 500
  const on = Math.floor(now / interval) % 2 === 0
  const color = props.active ? (props.color ?? T.cyan) : T.dim
  const display = props.active ? (on ? props.text : props.text) : props.text
  return <text fg={color}>{display}</text>
}
