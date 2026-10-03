import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"

const SEV_COLOR: Record<string, string> = {
  clean: T.up,
  info: T.dim,
  warn: T.warn,
  critical: T.down,
}

const SEV_ICON: Record<string, string> = {
  clean: "✓",
  info: "ℹ",
  warn: "⚠",
  critical: "✗",
}

export function securityMove(delta: number): void {
  const n = store.get().security.scans.length
  if (n === 0) return
  store.set((s) => ({ security: { ...s.security, selectedScan: Math.max(0, Math.min(n - 1, s.security.selectedScan + delta)) } }))
}

export function SecurityScreen() {
  const sec = useDeck((s) => s.security)
  const selected = Math.min(sec.selectedScan, Math.max(0, sec.scans.length - 1))
  const scan = sec.scans[selected]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" SECURITY SCANS " titleColor={T.down} borderStyle="single" borderColor={T.border} flexDirection="column" width="45%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <box flexDirection="row">
            <text fg={T.dim}>{"SCANNER".padEnd(24)}{"SEV".padEnd(10)}{"FINDINGS".padStart(9)}  {"TIME".padEnd(10)}</text>
          </box>
          {sec.scans.length === 0 && <text fg={T.dim}>no scans run</text>}
          {sec.scans.map((s, i) => {
            const active = i === selected
            return (
              <box key={s.id} flexDirection="row" backgroundColor={active ? T.raised : undefined}>
                <text fg={active ? T.bright : T.muted}>{`${active ? "▸" : " "}${s.scanner}`.padEnd(24)}</text>
                <text fg={SEV_COLOR[s.severity]}>{`${SEV_ICON[s.severity]} ${s.severity}`.padEnd(10)}</text>
                <text fg={s.findings > 0 ? T.down : T.up}>{String(s.findings).padStart(9)}</text>
                <text fg={T.dim}>  {fmtClock(s.ts)}</text>
              </box>
            )
          })}
          <text fg={T.borderBright}>────────────────────────────────────</text>
          <text fg={T.dim}>JOURNAL SCOPE</text>
          <text fg={sec.journalScope === "LIVE" ? T.up : sec.journalScope === "PAPER" ? T.cyan : T.warn}>{sec.journalScope}</text>
          <text fg={T.dim}>GIT SCRUB</text>
          <text fg={sec.gitScrubStatus === "clean" ? T.up : sec.gitScrubStatus === "dirty" ? T.down : T.dim}>{sec.gitScrubStatus}</text>
          {sec.lastScrubTs ? <text fg={T.dim}>last scrub: {fmtClock(sec.lastScrubTs)}</text> : null}
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title=" SCAN DETAIL " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {scan ? (
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="row" gap={2}>
                <text fg={T.bg} bg={SEV_COLOR[scan.severity]}> {SEV_ICON[scan.severity]} {scan.scanner} </text>
                <text fg={T.muted}>{fmtClock(scan.ts)}</text>
              </box>
              <Row label="findings" value={String(scan.findings)} color={scan.findings > 0 ? T.down : T.up} />
              <Row label="severity" value={scan.severity} color={SEV_COLOR[scan.severity]} />
              <text fg={T.borderBright}>────────────────────────────────────</text>
              <text fg={T.dim}>DETAIL</text>
              <text fg={T.muted}>{scan.detail}</text>
            </scrollbox>
          ) : (
            <text fg={T.dim}>select a scan for detail</text>
          )}
        </box>

        <box title=" SECRETS HYGIENE " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1} marginTop={1}>
          <scrollbox flexGrow={1} viewportCulling>
            {sec.secretsHygiene.length === 0 && <text fg={T.dim}>no checks run</text>}
            {sec.secretsHygiene.map((h, i) => (
              <box key={i} flexDirection="row" gap={1}>
                <text fg={h.status === "clean" ? T.up : h.status === "warning" ? T.warn : T.down}>{h.status === "clean" ? "✓" : h.status === "warning" ? "⚠" : "✗"}</text>
                <text fg={T.muted}>{h.file.slice(-30).padEnd(32)}</text>
                <text fg={T.dim}>{h.detail.slice(0, 40)}</text>
              </box>
            ))}
          </scrollbox>
        </box>
      </box>
    </box>
  )
}

function Row(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
