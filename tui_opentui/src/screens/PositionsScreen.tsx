import { useState } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { useContentWidth, RES, clip } from "../layout"
import { fmtMoney, fmtNum } from "../format"
import { T } from "../theme"
import { SectionPanel } from "../components/SectionPanel"
import { DataTable, type DataColumn } from "../components/DataTable"
import { ProgressBar } from "../components/ProgressBar"
import { Badge } from "../components/Badge"
import { TabBar } from "../components/TabBar"
import type { Position, TradeHistoryEntry, StrategyState } from "../types"

export function PositionsScreen() {
  const positions = useDeck((s) => s.positions)
  const strategies = useDeck((s) => s.strategies)
  const account = useDeck((s) => s.account)
  const tradeHistory = useDeck((s) => s.tradeHistory)
  const symbols = useDeck((s) => s.symbols)
  const totalPnl = positions.reduce((a, p) => a + p.pnl, 0)

  const [activeTab, setActiveTab] = useState(0)

  // ── Sort states for each blotter section ──
  const [posSort, setPosSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null)
  const [histSort, setHistSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null)
  const [stratSort, setStratSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null)

  const contentW = useContentWidth()
  const boxAvail = Math.max(20, contentW - 2 - RES - 4)

  const stratName = (id: string) => strategies.find((s) => s.id === id)?.name ?? id

  // ── Keyboard shortcuts ──
  useKeyboard((key) => {
    if (key.name === "1") { setActiveTab(0); return }
    if (key.name === "2") { setActiveTab(1); return }
    if (key.name === "3") { setActiveTab(2); return }
    if (key.name === "4") { setActiveTab(3); return }
    if (key.name === "tab") {
      setActiveTab((p) => (key.shift ? (p - 1 + 4) % 4 : (p + 1) % 4))
      return
    }
    if (key.name === "f" || key.name === "F") {
      store.set({ confirm: { action: "flatten", label: "FLATTEN all positions at market" } })
      return
    }
  })

  // ── Open Positions Rows & Columns ──
  type PosRow = Position & { strat: string; ageLabel: string; digits: number }
  const posRows: PosRow[] = positions.map((p) => ({
    ...p,
    strat: stratName(p.strategyId),
    ageLabel: `${Math.floor(p.ageMin / 60)}h${p.ageMin % 60}m`,
    digits: p.entry > 100 ? 2 : p.entry > 10 ? 3 : 5,
  }))

  const sortedPosRows: PosRow[] = posSort == null ? posRows : [...posRows].sort((a, b) => {
    let av: string | number = 0
    let bv: string | number = 0
    switch (posSort.key) {
      case "ticket": av = a.ticket; bv = b.ticket; break
      case "symbol": av = a.symbol; bv = b.symbol; break
      case "side": av = a.side; bv = b.side; break
      case "vol": av = a.volume; bv = b.volume; break
      case "entry": av = a.entry; bv = b.entry; break
      case "pnl": av = a.pnl; bv = b.pnl; break
      case "r": av = a.unrealizedR; bv = b.unrealizedR; break
      case "strat": av = a.strat; bv = b.strat; break
      case "age": av = a.ageMin; bv = b.ageMin; break
    }
    let c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv))
    return posSort.dir === "desc" ? -c : c
  })

  const posCols: DataColumn<PosRow>[] = [
    { key: "ticket", label: "TICKET", width: 9, render: (r) => String(r.ticket), color: () => T.muted },
    { key: "symbol", label: "SYMBOL", width: 8, render: (r) => r.symbol, color: () => T.bright },
    { key: "side", label: "SIDE", width: 7, render: (r) => (r.side === "BUY" ? "▲ BUY" : "▼ SELL"), color: (r) => (r.side === "BUY" ? T.up : T.down) },
    { key: "vol", label: "LOTS", width: 6, align: "right", render: (r) => r.volume.toFixed(2) },
    { key: "entry", label: "ENTRY", width: 10, align: "right", render: (r) => fmtNum(r.entry, r.digits) },
    { key: "pnl", label: "P&L", width: 11, align: "right", render: (r) => fmtMoney(r.pnl), color: (r) => (r.pnl >= 0 ? T.up : T.down) },
    { key: "r", label: "R-MULT", width: 8, align: "right", render: (r) => `${r.unrealizedR > 0 ? "+" : ""}${r.unrealizedR.toFixed(2)}R`, color: (r) => (r.unrealizedR > 1 ? T.up : r.unrealizedR < 0 ? T.down : T.warn) },
    { key: "strat", label: "STRATEGY", width: 14, render: (r) => r.strat.slice(0, 14) },
    { key: "age", label: "AGE", width: 7, align: "right", render: (r) => r.ageLabel, color: () => T.dim },
  ]

  // ── Closed Trades Rows & Columns ──
  const sortedHistRows: TradeHistoryEntry[] = histSort == null ? tradeHistory : [...tradeHistory].sort((a, b) => {
    let av: string | number = 0
    let bv: string | number = 0
    switch (histSort.key) {
      case "ticket": av = a.ticket; bv = b.ticket; break
      case "symbol": av = a.symbol; bv = b.symbol; break
      case "side": av = a.side; bv = b.side; break
      case "vol": av = a.volume; bv = b.volume; break
      case "entry": av = a.entry; bv = b.entry; break
      case "exit": av = a.exitPrice; bv = b.exitPrice; break
      case "pnl": av = a.pnl; bv = b.pnl; break
      case "reason": av = a.exitReason; bv = b.exitReason; break
      case "strat": av = a.strategy; bv = b.strategy; break
    }
    let c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv))
    return histSort.dir === "desc" ? -c : c
  })

  const histCols: DataColumn<TradeHistoryEntry>[] = [
    { key: "ticket", label: "TICKET", width: 9, render: (r) => String(r.ticket), color: () => T.muted },
    { key: "symbol", label: "SYMBOL", width: 8, render: (r) => r.symbol, color: () => T.bright },
    { key: "side", label: "SIDE", width: 6, render: (r) => (r.side === "BUY" ? "▲ BUY" : "▼ SELL"), color: (r) => (r.side === "BUY" ? T.up : T.down) },
    { key: "vol", label: "LOTS", width: 6, align: "right", render: (r) => r.volume.toFixed(2) },
    { key: "entry", label: "ENTRY", width: 10, align: "right", render: (r) => fmtNum(r.entry, r.entry > 10 ? 2 : 4) },
    { key: "exit", label: "EXIT", width: 10, align: "right", render: (r) => fmtNum(r.exitPrice, r.exitPrice > 10 ? 2 : 4) },
    { key: "pnl", label: "P&L", width: 11, align: "right", render: (r) => fmtMoney(r.pnl), color: (r) => (r.pnl >= 0 ? T.up : T.down) },
    { key: "reason", label: "REASON", width: 13, render: (r) => r.exitReason.slice(0, 13), color: (r) => (r.exitReason.includes("TP") ? T.up : r.exitReason.includes("SL") ? T.down : T.dim) },
    { key: "strat", label: "STRATEGY", width: 14, render: (r) => r.strategy.slice(0, 14) },
  ]

  // ── Strategies Rows & Columns ──
  const sortedStratRows: StrategyState[] = stratSort == null ? strategies : [...strategies].sort((a, b) => {
    let av: string | number = 0
    let bv: string | number = 0
    switch (stratSort.key) {
      case "name": av = a.name; bv = b.name; break
      case "stage": av = a.stage; bv = b.stage; break
      case "trades": av = a.trades; bv = b.trades; break
      case "winRate": av = a.winRate; bv = b.winRate; break
      case "pf": av = a.profitFactor; bv = b.profitFactor; break
      case "sharpe": av = a.sharpe; bv = b.sharpe; break
      case "maxDD": av = a.maxDD; bv = b.maxDD; break
    }
    let c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv))
    return stratSort.dir === "desc" ? -c : c
  })

  const stratCols: DataColumn<StrategyState>[] = [
    { key: "name", label: "STRATEGY", width: 16, render: (s) => s.name.slice(0, 16), color: () => T.bright },
    { key: "stage", label: "STAGE", width: 12, render: (s) => s.stage, color: (s) => (s.stage === "FULL" ? T.up : s.stage === "EVALUATION" ? T.cyan : s.stage === "CANDIDATE" ? T.warn : T.dim) },
    { key: "trades", label: "TRADES", width: 8, align: "right", render: (s) => String(s.trades) },
    { key: "winRate", label: "WIN %", width: 8, align: "right", render: (s) => `${s.winRate.toFixed(1)}%`, color: (s) => (s.winRate >= 50 ? T.up : T.warn) },
    { key: "pf", label: "PF", width: 7, align: "right", render: (s) => s.profitFactor.toFixed(2), color: (s) => (s.profitFactor >= 1.5 ? T.up : s.profitFactor >= 1.0 ? T.dim : T.down) },
    { key: "sharpe", label: "SHARPE", width: 8, align: "right", render: (s) => s.sharpe.toFixed(2), color: (s) => (s.sharpe >= 1.5 ? T.up : T.dim) },
    { key: "maxDD", label: "MAX DD", width: 8, align: "right", render: (s) => `-${s.maxDD.toFixed(1)}%`, color: (s) => (s.maxDD > 5 ? T.down : T.dim) },
    { key: "symbols", label: "SYMBOLS", width: 16, render: (s) => s.symbols.join(", ") },
  ]

  const realizedPnl = tradeHistory.reduce((a, t) => a + t.pnl, 0)
  const winningTrades = tradeHistory.filter((t) => t.pnl > 0).length
  const winRate = tradeHistory.length > 0 ? (winningTrades / tradeHistory.length) * 100 : 0

  return (
    <box flexDirection="column" flexGrow={1} paddingLeft={1} paddingRight={1}>
      {/* ── Top Header Strip ── */}
      <box flexDirection="row" justifyContent="space-between" marginBottom={0} flexShrink={0}>
        <text fg={T.cyan}>{clip("╔══ POSITIONS & EXECUTION BLOTTER ═════════════════════════════════════════════════════════╗", contentW - 16)}</text>
        <Badge
          label={positions.length > 0 ? `${positions.length} ACTIVE` : "FLAT"}
          color={positions.length > 0 ? T.warn : T.teal}
          icon={positions.length > 0 ? "◉" : "◇"}
        />
      </box>

      {/* ── Compact Portfolio & Heat Risk Summary Strip (Pinned, never scrolls off) ── */}
      <box
        backgroundColor={T.panel}
        borderStyle="single"
        borderColor={positions.length > 0 ? T.warn : T.border}
        paddingLeft={1}
        paddingRight={1}
        marginBottom={1}
        flexDirection="row"
        justifyContent="space-between"
        alignItems="center"
        flexShrink={0}
      >
        <box flexDirection="row" gap={2} alignItems="center">
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>BALANCE:</text>
            <text fg={T.bright}>{fmtMoney(account.balance)}</text>
          </box>
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>EQUITY:</text>
            <text fg={account.equity >= account.balance ? T.up : T.down}>{fmtMoney(account.equity)}</text>
          </box>
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>FLOATING:</text>
            <text fg={totalPnl >= 0 ? T.up : T.down}>{fmtMoney(totalPnl)}</text>
          </box>
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>DRAWDOWN:</text>
            <text fg={account.drawdownPct > 2 ? T.warn : T.dim}>-{account.drawdownPct.toFixed(2)}%</text>
          </box>
        </box>
        <box flexDirection="row" gap={2} alignItems="center">
          <ProgressBar
            value={account.heatCapR > 0 ? account.heatUsedR / account.heatCapR : 0}
            width={16}
            color={account.heatUsedR > 4 ? T.warn : T.teal}
            label="HEAT"
            showPct
          />
          <Badge
            label={`${account.heatUsedR.toFixed(1)}R / ${account.heatCapR.toFixed(0)}R`}
            color={account.heatUsedR > 4 ? T.warn : T.teal}
          />
        </box>
      </box>

      {/* ── Interactive Blotter Navigation Tabs (Clickable + 1-4 / Tab hotkeys) ── */}
      <TabBar
        tabs={[
          { name: "Overview", badge: positions.length + tradeHistory.length },
          { name: "Open Positions", badge: positions.length },
          { name: "Closed History", badge: tradeHistory.length },
          { name: "Strategy Allocations", badge: strategies.length },
        ]}
        selected={activeTab}
        onChange={setActiveTab}
        activeColor={T.cyan}
      />

      {/* ── Tab Content Area (Scrolls cleanly inside when content is tall) ── */}
      <scrollbox
        flexDirection="column"
        flexGrow={1}
        viewportCulling
        scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
      >
        {/* ═══════════ TAB 0: OVERVIEW / ALL BLOTTER ═══════════ */}
        {activeTab === 0 && (
          <box flexDirection="column" gap={1}>
            {/* Open Exposure Section */}
            <SectionPanel title={`◈ ACTIVE MARKET EXPOSURE · ${positions.length} OPEN`} titleColor={positions.length > 0 ? T.warn : T.bright}>
              {positions.length === 0 ? (
                <box
                  borderStyle="single"
                  borderColor={T.border}
                  backgroundColor={T.panel}
                  paddingLeft={1}
                  paddingRight={1}
                  flexDirection="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <box flexDirection="row" gap={2} alignItems="center">
                    <Badge label="FLAT" color={T.teal} icon="●" />
                    <text fg={T.muted}>No open market risk · 100% capital protected · Automated engines standing by</text>
                  </box>
                  <text fg={T.dim}>WATCHING: {symbols.slice(0, 4).join(" · ")}</text>
                </box>
              ) : (
                <DataTable
                  columns={posCols}
                  data={sortedPosRows}
                  width={boxAvail}
                  sortKey={posSort?.key ?? null}
                  sortDir={posSort?.dir ?? "asc"}
                  onSort={(key, dir) => setPosSort({ key, dir })}
                />
              )}
            </SectionPanel>

            {/* Closed History Section */}
            <SectionPanel title={`◈ RECENT CLOSED TRADES · ${tradeHistory.length} FILLS`} titleColor={T.purple}>
              {tradeHistory.length === 0 ? (
                <box paddingLeft={1} paddingTop={0} paddingBottom={0}>
                  <text fg={T.dim}>No closed fills recorded in current session · Orders trigger automatically upon signal confirmation.</text>
                </box>
              ) : (
                <DataTable
                  columns={histCols}
                  data={sortedHistRows.slice(0, 8)}
                  width={boxAvail}
                  sortKey={histSort?.key ?? null}
                  sortDir={histSort?.dir ?? "asc"}
                  onSort={(key, dir) => setHistSort({ key, dir })}
                />
              )}
            </SectionPanel>

            {/* Strategy Allocation Section */}
            <SectionPanel title={`◈ STRATEGY RISK ALLOCATIONS · ${strategies.length} ENGINES ACTIVE`} titleColor={T.cyan}>
              <DataTable
                columns={stratCols}
                data={sortedStratRows.slice(0, 6)}
                width={boxAvail}
                sortKey={stratSort?.key ?? null}
                sortDir={stratSort?.dir ?? "asc"}
                onSort={(key, dir) => setStratSort({ key, dir })}
              />
            </SectionPanel>
          </box>
        )}

        {/* ═══════════ TAB 1: OPEN POSITIONS ═══════════ */}
        {activeTab === 1 && (
          <box flexDirection="column" gap={1}>
            <SectionPanel title={`◈ OPEN POSITIONS · ${positions.length} LIVE`} titleColor={positions.length > 0 ? T.warn : T.bright}>
              {positions.length === 0 ? (
                <box flexDirection="column" gap={1} paddingLeft={1} paddingTop={1} paddingBottom={1}>
                  <box flexDirection="row" gap={2} alignItems="center">
                    <Badge label="FLAT" color={T.teal} icon="●" />
                    <text fg={T.bright}>Account is currently flat with zero open positions.</text>
                  </box>
                  <text fg={T.muted}>Risk Engine Status: ARMED · Maximum Portfolio Heat: {account.heatCapR.toFixed(0)}R · Available Capacity: {(account.heatCapR - account.heatUsedR).toFixed(1)}R</text>
                  <text fg={T.dim}>Monitored Universe: {symbols.join(", ")}</text>
                  <text fg={T.dim}>Press [3] to inspect Closed History or [4] to review Strategy Allocations.</text>
                </box>
              ) : (
                <DataTable
                  columns={posCols}
                  data={sortedPosRows}
                  width={boxAvail}
                  sortKey={posSort?.key ?? null}
                  sortDir={posSort?.dir ?? "asc"}
                  onSort={(key, dir) => setPosSort({ key, dir })}
                />
              )}
            </SectionPanel>

            {positions.length > 0 && (
              <box flexDirection="column" gap={1}>
                {positions.map((p) => (
                  <SectionPanel key={p.ticket} title={`◈ #${p.ticket} ${p.symbol} ${p.side}`} titleColor={p.side === "BUY" ? T.up : T.down}>
                    <box flexDirection="row" gap={3} marginBottom={0}>
                      <text fg={T.dim}>ENTRY: <span fg={T.bright}>{p.entry}</span></text>
                      <text fg={T.dim}>SL: <span fg={T.down}>{p.sl} ({fmtNum(Math.abs((p.entry - p.sl) / p.entry) * 10000, 0)}bp)</span></text>
                      <text fg={T.dim}>TP: <span fg={T.up}>{p.tp} ({fmtNum(Math.abs((p.tp - p.entry) / p.entry) * 10000, 0)}bp)</span></text>
                      <text fg={T.dim}>P&L: <span fg={p.pnl >= 0 ? T.up : T.down}>{fmtMoney(p.pnl)}</span></text>
                    </box>
                    <box flexDirection="row" gap={1} alignItems="center">
                      {[0.5, 1.0, 1.5, 2.0, 2.5].map((rTarget) => {
                        const hit = p.unrealizedR >= rTarget
                        return <text key={rTarget} fg={hit ? T.up : T.dim}> {rTarget.toFixed(1)}R{hit ? "✓" : ""}</text>
                      })}
                      <text fg={p.unrealizedR > 0 ? T.up : T.down}> ◉ {p.unrealizedR > 0 ? "+" : ""}{p.unrealizedR.toFixed(2)}R</text>
                    </box>
                    <ProgressBar value={Math.min(1, Math.max(0, p.unrealizedR / 2.5))} width={24} color={p.unrealizedR > 1 ? T.up : T.warn} label="R-ladder" showPct />
                  </SectionPanel>
                ))}
              </box>
            )}
          </box>
        )}

        {/* ═══════════ TAB 2: CLOSED TRADES HISTORY ═══════════ */}
        {activeTab === 2 && (
          <box flexDirection="column" gap={1}>
            <box
              backgroundColor={T.panel}
              borderStyle="single"
              borderColor={T.border}
              paddingLeft={1}
              paddingRight={1}
              flexDirection="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <box flexDirection="row" gap={3}>
                <text fg={T.dim}>TOTAL CLOSED: <span fg={T.bright}>{tradeHistory.length}</span></text>
                <text fg={T.dim}>WIN RATE: <span fg={winRate >= 50 ? T.up : T.warn}>{winRate.toFixed(1)}%</span> ({winningTrades}W / {tradeHistory.length - winningTrades}L)</text>
                <text fg={T.dim}>NET REALIZED: <span fg={realizedPnl >= 0 ? T.up : T.down}>{fmtMoney(realizedPnl)}</span></text>
              </box>
              <text fg={T.dim}>Click headers to sort</text>
            </box>

            <SectionPanel title={`◈ CLOSED DEALS BLOTTER (${tradeHistory.length})`} titleColor={T.purple}>
              {tradeHistory.length === 0 ? (
                <box paddingLeft={1} paddingTop={1} paddingBottom={1}>
                  <text fg={T.dim}>No closed trades logged in current journal database.</text>
                </box>
              ) : (
                <DataTable
                  columns={histCols}
                  data={sortedHistRows}
                  width={boxAvail}
                  sortKey={histSort?.key ?? null}
                  sortDir={histSort?.dir ?? "asc"}
                  onSort={(key, dir) => setHistSort({ key, dir })}
                />
              )}
            </SectionPanel>
          </box>
        )}

        {/* ═══════════ TAB 3: STRATEGY RISK ALLOCATIONS ═══════════ */}
        {activeTab === 3 && (
          <box flexDirection="column" gap={1}>
            <box
              backgroundColor={T.panel}
              borderStyle="single"
              borderColor={T.border}
              paddingLeft={1}
              paddingRight={1}
              flexDirection="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <box flexDirection="row" gap={3}>
                <text fg={T.dim}>TOTAL STRATEGIES: <span fg={T.bright}>{strategies.length}</span></text>
                <text fg={T.dim}>FULL DEPLOYMENT: <span fg={T.up}>{strategies.filter((s) => s.stage === "FULL").length}</span></text>
                <text fg={T.dim}>EVALUATION: <span fg={T.cyan}>{strategies.filter((s) => s.stage === "EVALUATION").length}</span></text>
                <text fg={T.dim}>CANDIDATE: <span fg={T.warn}>{strategies.filter((s) => s.stage === "CANDIDATE").length}</span></text>
              </box>
              <text fg={T.dim}>Click headers to sort</text>
            </box>

            <SectionPanel title={`◈ STRATEGY PERFORMANCE & ALLOCATION MATRIX (${strategies.length})`} titleColor={T.cyan}>
              <DataTable
                columns={stratCols}
                data={sortedStratRows}
                width={boxAvail}
                sortKey={stratSort?.key ?? null}
                sortDir={stratSort?.dir ?? "asc"}
                onSort={(key, dir) => setStratSort({ key, dir })}
              />
            </SectionPanel>
          </box>
        )}
      </scrollbox>

      {/* ── Persistent Bottom Blotter Action Bar ── */}
      <box
        flexDirection="row"
        justifyContent="space-between"
        borderStyle="single"
        borderColor={T.border}
        paddingLeft={1}
        paddingRight={1}
        flexShrink={0}
      >
        <box flexDirection="row" gap={2} alignItems="center">
          <box onMouseDown={() => store.set({ confirm: { action: "flatten", label: "FLATTEN all positions at market" } })}>
            <text fg={positions.length > 0 ? T.down : T.dim}>[F] Flatten All</text>
          </box>
          <text fg={T.borderBright}>|</text>
          <box onMouseDown={() => setActiveTab(0)}>
            <text fg={activeTab === 0 ? T.cyan : T.muted}>[1] Overview</text>
          </box>
          <box onMouseDown={() => setActiveTab(1)}>
            <text fg={activeTab === 1 ? T.cyan : T.muted}>[2] Open ({positions.length})</text>
          </box>
          <box onMouseDown={() => setActiveTab(2)}>
            <text fg={activeTab === 2 ? T.cyan : T.muted}>[3] History ({tradeHistory.length})</text>
          </box>
          <box onMouseDown={() => setActiveTab(3)}>
            <text fg={activeTab === 3 ? T.cyan : T.muted}>[4] Strategies ({strategies.length})</text>
          </box>
        </box>

        <box flexDirection="row" gap={2} alignItems="center">
          <text fg={T.dim}>Tab/1-4 switch tabs</text>
          <text fg={totalPnl >= 0 ? T.up : T.down}>FLOATING: {fmtMoney(totalPnl)}</text>
        </box>
      </box>
    </box>
  )
}

