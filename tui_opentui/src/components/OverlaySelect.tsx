import { useState, useCallback } from "react"
import { T } from "../theme"

export interface OverlayOption {
  name: string
  description: string
  value?: string
  danger?: boolean
}

interface OverlaySelectProps {
  trigger: React.ReactNode
  options: OverlayOption[]
  onSelect: (index: number, option: OverlayOption) => void
  width?: number
  title?: string
  focused?: boolean
  maxHeight?: number
}

export function OverlaySelect(props: OverlaySelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const filtered = query
    ? props.options.filter(
        (o) =>
          o.name.toLowerCase().includes(query.toLowerCase()) ||
          o.description.toLowerCase().includes(query.toLowerCase()),
      )
    : props.options

  const handleSelect = useCallback(
    (_i: number, opt: { name: string; description: string; value?: unknown } | null) => {
      if (!opt) return
      const realIdx = props.options.findIndex((o) => o.name === opt.name)
      const realOpt = props.options[realIdx]
      setOpen(false)
      setQuery("")
      if (realOpt) props.onSelect(realIdx, realOpt)
    },
    [props],
  )

  return (
    <box flexDirection="column">
      <box
        onMouseDown={() => setOpen((o) => !o)}
        focused={props.focused}
      >
        {props.trigger}
      </box>
      {open && (
        <box
          position="absolute"
          top={1}
          left={0}
          width={props.width ?? 30}
          maxHeight={props.maxHeight ?? 12}
          flexDirection="column"
          borderStyle="single"
          borderColor={T.cyan}
          backgroundColor={T.panel}
          zIndex={100}
          title={props.title ?? " SELECT "}
          titleColor={T.cyan}
          titleAlignment="left"
        >
          <input
            placeholder="filter…"
            onInput={(v) => setQuery(v)}
            onSubmit={() => {
              const first = filtered[0]
              if (first) handleSelect(0, first)
            }}
            backgroundColor={T.raised}
            textColor={T.bright}
            cursorColor={T.cyan}
            marginLeft={1}
            marginRight={1}
            focused
          />
          <select
            flexGrow={1}
            focused={false}
            options={filtered.map((o) => ({
              name: o.danger ? `⚠ ${o.name}` : o.name,
              description: o.description,
              value: o.value,
            }))}
            showDescription
            showScrollIndicator
            showSelectionIndicator
            wrapSelection
            textColor={T.fg}
            descriptionColor={T.dim}
            selectedBackgroundColor={T.borderBright}
            selectedTextColor={T.bright}
            selectedDescriptionColor={T.teal}
            onSelect={handleSelect}
          />
        </box>
      )}
    </box>
  )
}
