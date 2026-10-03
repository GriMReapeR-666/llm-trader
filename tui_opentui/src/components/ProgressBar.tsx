import { T } from "../theme"

const FILLED = "▰"
const EMPTY = "▱"

export function ProgressBar(props: {
  value: number
  width?: number
  color?: string
  showPct?: boolean
  label?: string
}) {
  const w = props.width ?? 20
  const v = Math.max(0, Math.min(1, props.value))
  const filled = Math.round(v * w)
  const color = props.color ?? T.cyan
  const pct = `${Math.round(v * 100)}%`

  return (
    <box flexDirection="row" gap={1}>
      {props.label && <text fg={T.dim}>{props.label}</text>}
      <text fg={color}>{FILLED.repeat(filled)}</text>
      <text fg={T.dim}>{EMPTY.repeat(w - filled)}</text>
      {props.showPct && <text fg={T.dim}>{pct}</text>}
    </box>
  )
}

export function NeonProgressBar(props: {
  value: number
  width?: number
  color?: string
  label?: string
}) {
  const w = props.width ?? 20
  const v = Math.max(0, Math.min(1, props.value))
  const filled = Math.round(v * w)
  const color = props.color ?? T.cyan

  return (
    <box flexDirection="row" gap={1}>
      {props.label && <text fg={T.dim}>{props.label}</text>}
      <text fg={T.dim}>[</text>
      <text fg={color}>{"█".repeat(filled)}</text>
      <text fg={T.dim}>{"░".repeat(w - filled)}</text>
      <text fg={T.dim}>]</text>
    </box>
  )
}

export function AnimatedBar(props: {
  value: number
  width?: number
  color?: string
  now?: number
}) {
  const w = props.width ?? 20
  const v = Math.max(0, Math.min(1, props.value))
  const filled = Math.round(v * w)
  const color = props.color ?? T.cyan
  const now = props.now ?? Date.now()
  const pulse = Math.floor(now / 200) % 2 === 0

  return (
    <text>
      <span fg={color}>{(pulse ? "█" : "▓").repeat(filled)}</span>
      <span fg={T.dim}>{"░".repeat(w - filled)}</span>
    </text>
  )
}
