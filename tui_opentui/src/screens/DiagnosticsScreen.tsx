import { useDeck, store } from "../store"
import { useContentWidth, RES, clip, pad, fitRow } from "../layout"
import type { Seg } from "../layout"
import { T } from "../theme"
import { fmtClock, bar } from "../format"
import { DataTable, type DataColumn } from "../components/DataTable"
import type { ErrorCode, LintIssue } from "../types"

const SEV_COLOR: Record<string, string> = {
  info: T.dim,
  warning: T.warn,
  warn: T.warn,
  error: T.down,
  critical: T.critical,
}

const SEV_ICON: Record<string, string> = {
  info: "ℹ",
  warning: "⚠",
  warn: "⚠",
  error: "✗",
  critical: "⛔",
}

export function diagnosticsMove(delta: number): void {
  const n = store.get().errorCodes.length
  if (n === 0) return
  store.set({ selectedError: Math.max(0, Math.min(n - 1, store.get().selectedError + delta)) })
}

export function DiagnosticsScreen() {
  const errors = useDeck((s) => s.errorCodes)
  const lints = useDeck((s) => s.lintIssues)
  const selected = Math.min(useDeck((s) => s.selectedError), Math.max(0, errors.length - 1))
  const e = errors[selected]

  // ── measured geometry: every row budget derives from the live terminal width ──
  const contentW = useContentWidth()
  const leftW = Math.max(40, Math.round(contentW / 2))
  const leftInner = Math.max(20, leftW - 3 - RES) // border 2 + paddingLeft 1 + scrollbar
  const rightW = Math.max(28, contentW - leftW - 1) // 1 gap col between panes
  const rightInner = Math.max(22, rightW - 4) // border 2 + paddingLeft 1 + scrollbar
  const sepLen = Math.max(4, rightInner - RES)

  const errorsByModule = new Map<string, number>()
  for (const err of errors) errorsByModule.set(err.module, (errorsByModule.get(err.module) ?? 0) + err.count)
  const topModules = [...errorsByModule.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)

  const lintByFile = new Map<string, { errors: number; warnings: number }>()
  for (const l of lints) {
    const existing = lintByFile.get(l.file) ?? { errors: 0, warnings: 0 }
    if (l.severity === "error") existing.errors++
    else existing.warnings++
    lintByFile.set(l.file, existing)
  }
  const topLintFiles = [...lintByFile.entries()].sort((a, b) => b[1].errors - a[1].errors).slice(0, 6)

  const errCols: DataColumn<ErrorCode>[] = [
    { key: "code", label: "CODE", width: 8, render: (x) => x.code, color: (x) => SEV_COLOR[x.severity] },
    { key: "module", label: "MODULE", width: 16, render: (x) => x.module, color: () => T.muted },
    { key: "sev", label: "SEV", width: 10, render: (x) => `${SEV_ICON[x.severity]} ${x.severity}`, color: (x) => SEV_COLOR[x.severity] },
    { key: "count", label: "COUNT", width: 6, align: "right", render: (x) => String(x.count), color: () => T.fg },
    { key: "message", label: "MESSAGE", width: 18, render: (x) => x.message, color: () => T.dim },
  ]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title="ERROR CODES" titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" width={leftW} flexShrink={0} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          <DataTable columns={errCols} data={errors} selectedIndex={selected} headerColor={T.cyan} width={leftInner} emptyMessage="no errors logged" />
          <text fg={T.borderBright}>{"─".repeat(leftInner)}</text>
          <text fg={T.cyan}>{clip("◈ ERRORS BY MODULE", leftInner)}</text>
          {topModules.map(([mod, count], mi) => {
            const segs: Seg[] = [
              { t: pad(mod, Math.max(8, Math.min(20, Math.round(leftInner * 0.45)))), fg: T.muted },
              { t: bar(count / Math.max(1, topModules[0][1]), Math.max(4, Math.round(leftInner * 0.3))), fg: T.down },
              { t: ` ${count}`, fg: T.dim },
            ]
            return <box key={`${mod}-${mi}`}>{fitRow(leftInner, segs)}</box>
          })}
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1} paddingLeft={1}>
        <box title="ERROR DETAIL" titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          {e ? (
            <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
              {fitRow(rightInner, [
                { t: ` ${SEV_ICON[e.severity]} ${e.code} `, bg: SEV_COLOR[e.severity], fg: T.bg },
                { t: ` ${e.module}`, fg: T.muted },
              ])}
              <text fg={T.fg}>{clip(e.message, sepLen)}</text>
              <text fg={T.borderBright}>{"─".repeat(sepLen)}</text>
              <Row label="severity" value={`${SEV_ICON[e.severity]} ${e.severity}`} color={SEV_COLOR[e.severity]} />
              <Row label="occurrences" value={String(e.count)} color={T.fg} />
              <Row label="last seen" value={fmtClock(e.lastTs)} color={T.muted} />
              <text fg={T.borderBright}>{"─".repeat(sepLen)}</text>
              <text fg={T.cyan}>{clip("◈ TRACE", sepLen)}</text>
              <text fg={T.muted}>{clip(`  module: ${e.module}`, sepLen)}</text>
              <text fg={T.muted}>{clip(`  code: ${e.code}`, sepLen)}</text>
              <text fg={T.muted}>{clip(`  source: ${e.module.toLowerCase().replace(/_/g, "/")}.py`, sepLen)}</text>
            </scrollbox>
          ) : (
            <text fg={T.dim}>select an error for detail</text>
          )}
        </box>

        <box title="LINT ISSUES" titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1} marginTop={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <text fg={T.dim}>{clip(`TOTAL: ${lints.filter((l) => l.severity === "error").length} errors · ${lints.filter((l) => l.severity === "warning").length} warnings`, sepLen)}</text>
            <text fg={T.borderBright}>{"─".repeat(sepLen)}</text>
            <text fg={T.cyan}>{clip("◈ BY FILE", sepLen)}</text>
            {topLintFiles.map(([file, counts], fi) => {
              const segs: Seg[] = [
                { t: counts.errors > 0 ? "✗ " : "⚠ ", fg: counts.errors > 0 ? T.down : T.warn },
                { t: clip(file, Math.max(10, Math.round(sepLen * 0.5))), fg: T.muted },
                { t: "  ", fg: T.dim },
                ...(counts.errors > 0 ? [{ t: `${counts.errors}E`, fg: T.down }] : []),
                ...(counts.warnings > 0 ? [{ t: ` ${counts.warnings}W`, fg: T.warn }] : []),
              ]
              return <box key={`${file}-${fi}`}>{fitRow(sepLen, segs)}</box>
            })}
            <text fg={T.borderBright}>{"─".repeat(sepLen)}</text>
            <text fg={T.cyan}>{clip("◈ RECENT ISSUES", sepLen)}</text>
            {lints.slice(0, 8).map((l, i) => {
              const segs: Seg[] = [
                { t: `${SEV_ICON[l.severity]} `, fg: SEV_COLOR[l.severity] },
                { t: `${l.file.slice(-12)}:${l.line}`, fg: T.dim },
                { t: " ", fg: T.fg },
                { t: pad(clip(l.rule, 10), 10), fg: T.muted },
                { t: l.message, fg: T.dim },
              ]
              return <box key={i}>{fitRow(sepLen, segs)}</box>
            })}
          </scrollbox>
        </box>
      </box>
    </box>
  )
}

function Row(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row">
      <text fg={T.dim}>{props.label}</text>
      <text fg={T.dim}>{" ".repeat(Math.max(1, 18 - props.label.length))}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}

