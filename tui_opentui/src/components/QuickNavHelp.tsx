import { T } from "../theme"

export function QuickNavHelp(props: { onClose: () => void }) {
  return (
    <box
      position="absolute"
      top="15%"
      left="22%"
      width="56%"
      height={23}
      borderStyle="double"
      borderColor={T.cyan}
      backgroundColor={T.panel}
      flexDirection="column"
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
      zIndex={200}
    >
      <box flexDirection="row" justifyContent="space-between" alignItems="center" marginBottom={1}>
        <text fg={T.cyan}>
          <b>🧭 QUICK NAVIGATION GUIDE</b>
        </text>
        <box
          onMouseDown={props.onClose}
          backgroundColor={T.critical}
          paddingLeft={1}
          paddingRight={1}
        >
          <text fg="#ffffff">✕ Close (Esc)</text>
        </box>
      </box>

      <box flexDirection="column" gap={0} marginBottom={1}>
        <text fg={T.teal}>
          <b>MOUSE INTERACTION (One-Click Control):</b>
        </text>
        <text fg={T.fg}> • Click any Section [1]-[6] in Side Rail or Header to switch section</text>
        <text fg={T.fg}> • Click any Screen tab in Side Rail or Header to open that view</text>
        <text fg={T.fg}> • Click ◀ or ▶ buttons in Header to cycle sections</text>
        <text fg={T.fg}> • Click [NAV] / [OPS] / [ACCT] side tabs to toggle panels</text>
        <text fg={T.fg}> • Click [ 🔍 Search ] in Header or Side Rail to open command palette</text>
        <text fg={T.fg}> • REPL: click LIVE / kind chips / AGENT / CLEAR to filter the feed</text>
        <text fg={T.fg}> • REPL: click ▸ or ▾ on a card to expand / fold its detail lines</text>
        <text fg={T.fg}> • REPL: decision cards open with the full reasoning + intent diff</text>
      </box>

      <box flexDirection="column" gap={0} marginBottom={1}>
        <text fg={T.orange}>
          <b>KEYBOARD SHORTCUTS:</b>
        </text>
        <text fg={T.fg}> • 1 – 6         : Switch directly to Section 1 to 6</text>
        <text fg={T.fg}> • [ / ] or ←/→  : Cycle previous / next section</text>
        <text fg={T.fg}> • Tab / Shift+Tab: Cycle screens within current section</text>
        <text fg={T.fg}> • /            : Open Command Palette (search all 35+ screens)</text>
        <text fg={T.fg}> • Esc          : Close overlays / dismiss top alerts</text>
        <text fg={T.fg}> • ?            : Toggle this Quick Navigation guide</text>
        <text fg={T.fg}> • REPL — P     : pause / resume live feed</text>
        <text fg={T.fg}> • REPL — C     : clear feed · A : cycle agent filter</text>
        <text fg={T.fg}> • REPL — E     : expand / fold the newest event card</text>
        <text fg={T.fg}> • CAUSAL — - / =: shrink / widen the DAG panel split</text>
        <text fg={T.fg}>{" • /stream q <text> : search feed · /stream all : reset filters"}</text>
      </box>

      <box flexDirection="row" justifyContent="flex-end">
        <text fg={T.dim}>Press Esc or click Close to return to deck</text>
      </box>
    </box>
  )
}
