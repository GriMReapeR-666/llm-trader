import { T } from "../theme"

export interface DataColumn<T> {
  key: string
  label: string
  width: number
  align?: "left" | "right" | "center"
  render: (row: T, index: number) => React.ReactNode
  color?: (row: T) => string
}

interface DataTableProps<T> {
  columns: DataColumn<T>[]
  data: T[]
  selectedIndex?: number
  onSelect?: (index: number) => void
  onUp?: () => void
  onDown?: () => void
  emptyMessage?: string
  headerColor?: string
  showHeader?: boolean
  /** Optional measured width of the panel. Columns are proportionally scaled
   *  (never exceeding it) so rows cannot overflow their container at any
   *  terminal width. */
  width?: number
  /** Active sort column; the header shows ▲ (asc) / ▼ (desc) beside it.
   *  Sortable headers always show a dim ‹› marker so they read as clickable
   *  before the first click. */
  sortKey?: string | null
  sortDir?: "asc" | "desc"
  /** When provided, headers become clickable. Clicking a new column sorts it
   *  ascending; clicking the active column toggles asc/desc. The resolved
   *  direction is passed here so the owner can re-sort its data. */
  onSort?: (key: string, dir: "asc" | "desc") => void
}

/**
 * Per-column widths, scaled to fit `avail` when the declared total exceeds it.
 * The last column absorbs rounding so the fitted total equals `avail` exactly.
 */
function resolveWidths<T>(columns: DataColumn<T>[], avail?: number): number[] {
  const raw = columns.map((c) => c.width)
  if (avail == null || avail <= 0) return raw
  const total = raw.reduce((a, b) => a + b, 0)
  if (total <= avail) return raw
  const scale = avail / total
  const out = raw.map((w) => Math.max(2, Math.floor(w * scale)))
  const used = out.reduce((a, b) => a + b, 0)
  const last = out.length - 1
  out[last] = Math.max(2, out[last] + (avail - used))
  return out
}

/** Clip cell content to the column width (with ellipsis) then align. */
function cellText(s: string, w: number, align?: "left" | "right" | "center"): string {
  const c = s.length > w ? s.slice(0, Math.max(1, w - 1)) + "…" : s
  if (align === "right") return c.padStart(w)
  return c.padEnd(w)
}

export function DataTable<T>(props: DataTableProps<T>) {
  const sel = props.selectedIndex ?? -1
  const headerColor = props.headerColor ?? T.dim
  const widths = resolveWidths(props.columns, props.width)
  const tableWidth = widths.reduce((a, b) => a + b, 0)

  // Header click semantics: a new column starts ascending; clicking the active
  // column toggles asc/desc. The resolved direction is passed to the owner so
  // it can re-sort its data (DataTable stays presentational).
  const cycleSort = (col: DataColumn<T>) => {
    if (!props.onSort) return
    const dir = props.sortKey === col.key && props.sortDir === "asc" ? "desc" : "asc"
    props.onSort(col.key, dir)
  }

  return (
    <box flexDirection="column" flexGrow={1}>
      {props.showHeader !== false && (
        <box flexDirection="row">
          {props.columns.map((col, i) => {
            const canSort = props.onSort != null
            // Sortable headers carry a 2-col marker in every state so the label
            // never shifts when a column becomes active: ▲/▼ when sorted, a dim
            // ‹› affordance (sortable, not yet sorted) on the rest, signalling
            // the header is clickable before the first click.
            const active = canSort && props.sortKey === col.key
            const mark = active
              ? (props.sortDir === "desc" ? "▼ " : "▲ ")
              : (canSort ? "‹›" : "")
            const labelColor = active ? T.bright : headerColor
            const markColor = active ? T.bright : T.muted
            if (!canSort) {
              return (
                <text key={col.key} fg={headerColor}>
                  {cellText(col.label, widths[i], col.align)}
                </text>
              )
            }
            const w = widths[i]
            const labelMax = Math.max(0, w - mark.length)
            const label =
              labelMax <= 0 ? "" :
              col.label.length <= labelMax ? col.label :
              labelMax === 1 ? col.label.slice(0, 1) :
              col.label.slice(0, labelMax - 1) + "…"
            const used = mark.length + label.length
            let pre = 0
            let post = w - used
            if (col.align === "right") {
              pre = w - used
              post = 0
            } else if (col.align === "center") {
              pre = Math.floor((w - used) / 2)
              post = w - used - pre
            }
            return (
              <box key={col.key} flexDirection="row" onMouseDown={() => cycleSort(col)}>
                <text fg={markColor}>{" ".repeat(pre) + mark}</text>
                <text fg={labelColor}>{label + " ".repeat(post)}</text>
              </box>
            )
          })}
        </box>
      )}
      <text fg={T.borderBright}>{"─".repeat(tableWidth)}</text>
      {props.data.length === 0 && <text fg={T.dim}>{props.emptyMessage ?? "no data"}</text>}
      {props.data.map((row: any, i) => {
        const active = i === sel
        return (
          <box
            key={(row as any).id ?? (row as any).ticket ?? i}
            flexDirection="row"
            backgroundColor={active ? T.raised : undefined}
          >
            {props.columns.map((col, j) => (
              <text key={col.key} fg={active ? T.bright : (col.color?.(row) ?? T.fg)}>
                {cellText(String(col.render(row, i)), widths[j], col.align)}
              </text>
            ))}
          </box>
        )
      })}
    </box>
  )
}
