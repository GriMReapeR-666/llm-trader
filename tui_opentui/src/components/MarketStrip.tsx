import { useDeck } from "../store"
import { fmtNum, fmtPct } from "../format"
import { T } from "../theme"
import { useContentWidth, clip } from "../layout"

export function MarketStrip() {
  const symbols = useDeck((s) => s.symbols)
  const market = useDeck((s) => s.market)
  const avail = useContentWidth()

  // One-line tape: SYMBOL price ±pct · SYMBOL price ±pct …
  const items: { text: string; up: boolean }[] = []
  for (const sym of symbols) {
    const q = market[sym]
    if (!q) {
      items.push({ text: `${sym} —`, up: true })
      continue
    }
    const digits = q.price > 100 ? 1 : q.price > 10 ? 2 : 4
    items.push({ text: `${sym} ${fmtNum(q.price, digits)} ${fmtPct(q.changePct)}`, up: q.changePct >= 0 })
  }
  const sep = " · "
  const label = "MARKET "
  const maxW = Math.max(8, avail)
  let used = label.length
  const shown: typeof items = []
  for (const it of items) {
    const add = it.text.length + (shown.length > 0 ? sep.length : 0)
    if (used + add > maxW) break
    shown.push(it)
    used += add
  }

  return (
    <box
      flexDirection="row"
      height={1}
      flexShrink={0}
      backgroundColor={T.panel}
      paddingLeft={2}
      paddingRight={2}
    >
      <text fg={T.dim}>{label}</text>
      {shown.map((it, i) => (
        <text key={i} fg={it.up ? T.up : T.down}>
          {i > 0 ? sep : ""}
          {it.text}
        </text>
      ))}
      {shown.length < items.length && (
        <text fg={T.dim}>{clip(" · more…", Math.max(3, maxW - used - 2))}</text>
      )}
    </box>
  )
}
