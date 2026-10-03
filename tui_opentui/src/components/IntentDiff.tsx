import * as React from "react"
import { T } from "../theme"
import { chunkLines } from "../layout"

export const IntentDiff = React.memo(function IntentDiff(props: { diff: string; width?: number }) {
  const raw = props.diff.split("\n").filter(Boolean)
  // hard-wrap every line to the measured inner width of the bordered panel
  // that hosts this component so long diff lines never cross its border
  const lines = props.width && props.width > 8 ? chunkLines(raw, props.width) : raw
  return (
    <box flexDirection="column">
      {lines.map((l, i) => {
        if (l.startsWith("+++") || l.startsWith("---")) {
          return (
            <text key={i} fg={T.dim}>
              {l}
            </text>
          )
        }
        if (l.startsWith("@@")) {
          return (
            <text key={i} fg={T.borderBright}>
              {l}
            </text>
          )
        }
        if (l.startsWith("+")) {
          return (
            <text key={i}>
              <span fg={T.up}>+ </span>
              <span fg={T.up}>{l.slice(1)}</span>
            </text>
          )
        }
        if (l.startsWith("-")) {
          return (
            <text key={i}>
              <span fg={T.down}>- </span>
              <span fg={T.down}>{l.slice(1)}</span>
            </text>
          )
        }
        return (
          <text key={i} fg={T.muted}>
            {"  "}
            {l}
          </text>
        )
      })}
    </box>
  )
})
