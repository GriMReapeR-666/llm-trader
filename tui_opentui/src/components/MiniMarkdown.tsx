import * as React from "react"
import { T } from "../theme"

function InlineText(props: { line: string; baseColor: string }) {
  const parts = props.line.split(/(\*\*[^*]+\*\*)/g).filter(Boolean)
  return (
    <text fg={props.baseColor}>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? (
          <span key={i} fg={T.bright}>
            {p.slice(2, -2)}
          </span>
        ) : (
          <span key={i} fg={props.baseColor}>
            {p}
          </span>
        ),
      )}
    </text>
  )
}

export const MiniMarkdown = React.memo(function MiniMarkdown(props: { content: string }) {
  const rows: React.ReactNode[] = []
  const lines = props.content.split("\n")
  let key = 0

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "")
    if (line.startsWith("### ")) {
      rows.push(<text key={key++} fg={T.cyan}>{line.slice(4)}</text>)
    } else if (line.startsWith("## ")) {
      rows.push(<text key={key++} fg={T.bright}>{line.slice(3)}</text>)
      rows.push(<text key={key++} fg={T.borderBright}>{"─".repeat(40)}</text>)
    } else if (line.startsWith("# ")) {
      rows.push(<text key={key++} fg={T.bright}>{line.slice(2)}</text>)
    } else if (/^[-•] /.test(line)) {
      rows.push(
        <box key={key++} flexDirection="row" paddingLeft={1}>
          <text fg={T.cyan}>· </text>
          <InlineText line={line.slice(2)} baseColor={T.fg} />
        </box>,
      )
    } else if (line.trim() === "") {
      rows.push(<text key={key++}> </text>)
    } else {
      rows.push(<InlineText key={key++} line={line} baseColor={T.muted} />)
    }
  }

  return <box flexDirection="column">{rows}</box>
})
