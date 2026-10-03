import { useDeck, store } from "../store"
import { fmtClock } from "../format"
import { T } from "../theme"

export function AlertBand() {
  const alerts = useDeck((s) => s.alerts)
  if (alerts.length === 0) return null
  const top = alerts[0]
  const isCritical = top.severity === "critical"
  const bg = isCritical ? T.critical : "#8a6d1c"
  const fg = "#ffffff"

  return (
    <box flexDirection="row" backgroundColor={bg} paddingLeft={1} paddingRight={1} height={1}>
      <text fg={fg} bg={bg}>
        {isCritical ? "! " : ""}{top.text.length > 110 ? top.text.slice(0, 110) + "…" : top.text}
      </text>
      <box flexGrow={1} />
      <text fg={fg} bg={bg}>{fmtClock(top.ts)} · x dismiss</text>
    </box>
  )
}

export function useTopAlert() {
  return useDeck((s) => s.alerts[0] ?? null)
}

export function dismissTopAlert(): void {
  const a = store.get().alerts[0]
  if (a) store.dismissAlert(a.id)
}
