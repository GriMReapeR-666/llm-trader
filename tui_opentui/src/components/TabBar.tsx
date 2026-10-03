import { T } from "../theme"

export interface TabOption {
  name: string
  description?: string
  badge?: number | string
}

interface TabBarProps {
  tabs: readonly (string | TabOption)[]
  selected: number
  onChange: (index: number) => void
  activeColor?: string
  gap?: number
}

export function TabBar(props: TabBarProps) {
  const { tabs, selected, onChange, activeColor = T.cyan, gap = 1 } = props

  return (
    <box flexDirection="row" gap={gap} marginBottom={0} flexShrink={0} height={1} alignItems="center">
      {tabs.map((tab, i) => {
        const name = typeof tab === "string" ? tab : tab.name
        const badge = typeof tab === "object" ? tab.badge : undefined
        const active = i === selected

        return (
          <box
            key={name}
            onMouseDown={() => onChange(i)}
            backgroundColor={active ? T.raised : T.panel}
            paddingLeft={1}
            paddingRight={1}
            height={1}
            flexDirection="row"
            alignItems="center"
          >
            <text fg={active ? activeColor : T.dim}>
              {active ? "▸ " : "  "}
            </text>
            <text fg={active ? T.bright : T.muted}>
              [{i + 1}] {name}
            </text>
            {badge !== undefined ? (
              <text fg={active ? activeColor : T.dim}>
                {" "}({badge})
              </text>
            ) : null}
          </box>
        )
      })}
    </box>
  )
}
