import { T, STATUS, type StatusKey } from "../theme"

export function StatusIndicator(props: {
  status: StatusKey
  label?: string
  showIcon?: boolean
  showLabel?: boolean
}) {
  const s = STATUS[props.status]
  const showIcon = props.showIcon ?? true
  const showLabel = props.showLabel ?? true
  const label = props.label ?? s.label
  const icon = showIcon ? `${s.icon} ` : ""
  const text = showLabel ? label : ""

  return (
    <text fg={s.color}>
      {icon}{text}
    </text>
  )
}

export function StatusRow(props: {
  status: StatusKey
  label: string
  value?: string
}) {
  const s = STATUS[props.status]
  return (
    <box flexDirection="row" gap={1}>
      <text fg={s.color}>{s.icon}</text>
      <text fg={T.dim}>{props.label}</text>
      <text fg={s.color}>{props.value ?? s.label}</text>
    </box>
  )
}

export function SeverityBadge(props: {
  severity: "info" | "warning" | "error" | "critical" | "success"
  label?: string
}) {
  const map: Record<string, { icon: string; color: string; label: string }> = {
    success:  { icon: "✔", color: T.up,       label: "SUCCESS" },
    info:     { icon: "ℹ", color: T.dim,      label: "INFO" },
    warning:  { icon: "⚠", color: T.warn,     label: "WARNING" },
    error:    { icon: "✗", color: T.down,      label: "ERROR" },
    critical: { icon: "⛔", color: T.critical,  label: "CRITICAL" },
  }
  const s = map[props.severity]
  const label = props.label ?? s.label

  return (
    <text bg={T.raised} fg={s.color}>
      {" "}{s.icon} {label}{" "}
    </text>
  )
}

export function HealthIndicator(props: {
  healthy: boolean
  label?: string
}) {
  return (
    <text fg={props.healthy ? T.up : T.down}>
      {props.healthy ? "✓" : "✗"} {props.label ?? (props.healthy ? "HEALTHY" : "UNHEALTHY")}
    </text>
  )
}
