import { useDeck } from "../store"
import { useContentWidth, clip } from "../layout"
import { T } from "../theme"
import { SectionPanel } from "../components/SectionPanel"
import { DataTable, type DataColumn } from "../components/DataTable"
import type { ScannerResult } from "../types"

const INTRO = "Hick #1 one purpose: scan opportunity · Prägnanz simplest table (DataTable) · Jakob familiar scan pattern"

export function ScannerScreen() {
  const results = useDeck((s) => s.scannerResults)
  const cols: DataColumn<ScannerResult>[] = [
    { key: "symbol", label: "SYMBOL", width: 10, render: (r) => r.symbol, color: () => T.cyan },
    { key: "score", label: "SCORE", width: 8, align: "right", render: (r) => r.score.toFixed(2), color: (r) => (r.score > 0.7 ? T.up : r.score > 0.4 ? T.warn : T.dim) },
    { key: "atr", label: "ATR", width: 10, align: "right", render: (r) => r.atr.toFixed(4) },
    { key: "price", label: "PRICE", width: 12, align: "right", render: (r) => r.price.toFixed(5) },
  ]
  // SectionPanel: border (2) + paddingLeft (1); reserve one col for safety.
  const tableW = Math.max(20, useContentWidth() - 2 - 1 - 1)
  return (
    <SectionPanel title="◈ SCANNER · OPPORTUNITY SCAN" titleColor={T.cyan} borderStyle="single" borderColor={T.cyan} padding={1}>
      <text fg={T.dim}>{clip(INTRO, tableW)}</text>
      {results.length === 0 ? <text fg={T.dim}>no scan results yet — connect to live engine (:8000) or trigger a scan via API</text> : <DataTable columns={cols} data={results} headerColor={T.dim} width={tableW} />}
    </SectionPanel>
  )
}
