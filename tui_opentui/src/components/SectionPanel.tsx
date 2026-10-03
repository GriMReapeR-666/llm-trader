import { T } from "../theme"

interface SectionPanelProps {
  title: string
  titleColor?: string
  borderColor?: string
  borderStyle?: "single" | "double" | "rounded" | "heavy"
  children: React.ReactNode
  width?: number | `${number}%`
  height?: number | `${number}%`
  flexGrow?: number
  flexShrink?: number
  padding?: number
  paddingLeft?: number
  flexDirection?: "column" | "row"
  marginBottom?: number
  marginTop?: number
}

export function SectionPanel(props: SectionPanelProps) {
  return (
    <box
      title={` ${props.title} `}
      titleColor={props.titleColor ?? T.dim}
      titleAlignment="left"
      borderStyle={props.borderStyle ?? "single"}
      borderColor={props.borderColor ?? T.border}
      backgroundColor={T.panel}
      flexDirection={props.flexDirection ?? "column"}
      width={props.width}
      height={props.height}
      flexGrow={props.flexGrow}
      flexShrink={props.flexShrink}
      padding={props.padding}
      paddingLeft={props.paddingLeft ?? 1}
      marginBottom={props.marginBottom}
      marginTop={props.marginTop}
    >
      {props.children}
    </box>
  )
}

interface StatRowProps {
  label: string
  value: string
  color?: string
}

export function StatRow(props: StatRowProps) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={1}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color ?? T.fg}>{props.value}</text>
    </box>
  )
}
