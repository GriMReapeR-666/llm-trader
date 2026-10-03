import { syntaxTheme } from "../syntaxStyle"
import { T } from "../theme"

interface CodeBlockProps {
  content: string
  filetype?: string
  maxHeight?: number
}

export function CodeBlock(props: CodeBlockProps) {
  return (
    <scrollbox flexGrow={1} viewportCulling maxHeight={props.maxHeight}>
      <code
        content={props.content}
        filetype={props.filetype ?? "javascript"}
        syntaxStyle={syntaxTheme}
        fg={T.fg}
        bg={T.panel}
        drawUnstyledText
        wrapMode="word"
        selectable
      />
    </scrollbox>
  )
}
