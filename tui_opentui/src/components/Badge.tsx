import { T, STATUS, type StatusKey } from "../theme"

export function Badge(props: {
  label: string
  color?: string
  icon?: string
  dim?: boolean
}) {
  const color = props.color ?? T.fg
  const icon = props.icon ? `${props.icon} ` : ""
  return (
    <text bg={T.raised} fg={props.dim ? T.dim : color}>
      {" "}{icon}{props.label}{" "}
    </text>
  )
}

export function StatusBadge(props: {
  status: StatusKey
  label?: string
}) {
  const s = STATUS[props.status]
  const label = props.label ?? s.label
  return (
    <text bg={T.raised} fg={s.color}>
      {" "}{s.icon} {label}{" "}
    </text>
  )
}

export function DotBadge(props: {
  active: boolean
  color?: string
  pulse?: boolean
}) {
  const icon = props.active ? "●" : "○"
  const color = props.active ? (props.color ?? T.up) : T.dim
  return <text fg={color}>{icon}</text>
}
