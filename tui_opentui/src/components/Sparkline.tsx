import { sparkline } from "../format"
import { T } from "../theme"

const GLOW_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

export function Sparkline(props: {
  data: number[]
  width?: number
  color?: string
  height?: number
  glow?: boolean
}) {
  const line = sparkline(props.data, props.width ?? 24)
  const color = props.color ?? T.cyan

  if (props.glow) {
    return (
      <text>
        <span fg={T.dim}>{"["}</span>
        <span fg={color}>{line}</span>
        <span fg={T.dim}>{"]"}</span>
      </text>
    )
  }

  return (
    <text fg={color}>{line}</text>
  )
}

export function NeonBar(props: {
  value: number
  width?: number
  color?: string
  label?: string
}) {
  const w = props.width ?? 20
  const v = Math.max(0, Math.min(1, props.value))
  const filled = Math.round(v * w)
  const bar = "█".repeat(filled) + "░".repeat(w - filled)
  const color = props.color ?? T.cyan

  return (
    <box flexDirection="row" gap={1}>
      {props.label && <text fg={T.dim}>{props.label}</text>}
      <text fg={T.dim}>[</text>
      <text fg={color}>{bar}</text>
      <text fg={T.dim}>]</text>
    </box>
  )
}
