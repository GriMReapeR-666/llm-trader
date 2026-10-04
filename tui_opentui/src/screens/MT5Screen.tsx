import { useState } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck } from "../store"
import { T } from "../theme"
import { fmtClock, fmtMoney, fmtNum, bar } from "../format"
import { mcpCallTool } from "../transport/mcp"
import { apiFetch } from "../transport/live"
import { syntaxTheme } from "../syntaxStyle"
import { TabBar } from "../components/TabBar"

const TABS = ["TERMINAL", "MARKET", "POSITIONS", "ORDERS", "HISTORY", "TOOLS", "MCP LOG"] as const

const CONN_COLOR: Record<string, string> = {
  connected: T.up,
  connecting: T.warn,
  disconnected: T.dim,
  error: T.down,
}

function StatRow(props: { k: string; v: string; c?: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={1}>
      <text fg={T.dim}>{props.k}</text>
      <text fg={props.c ?? T.fg}>{props.v}</text>
    </box>
  )
}

export function MT5Screen() {
  const mt5 = useDeck((s) => s.mt5)
  const [tab, setTab] = useState(0)
  const terminal = mt5.terminal
  const acct = mt5.accountLive
  const ticks = mt5.ticks
  const syms = mt5.symbols
  const pending = mt5.pendingOrders
  const history = mt5.history
  const calls = mt5.mcpCalls
  const selectedCall = calls[Math.min(mt5.mcpSelected, Math.max(0, calls.length - 1))]
  const transport = useDeck((s) => s.transport)
  const isLive = transport === "live" || transport === "mcp"

  useKeyboard((key) => {
    if (key.name === "1") { setTab(0); return }
    if (key.name === "2") { setTab(1); return }
    if (key.name === "3") { setTab(2); return }
    if (key.name === "4") { setTab(3); return }
    if (key.name === "5") { setTab(4); return }
    if (key.name === "6") { setTab(5); return }
    if (key.name === "7") { setTab(6); return }
    if (key.name === "left" || (key.name === "tab" && key.shift)) {
      setTab((t) => (t - 1 + TABS.length) % TABS.length)
      return
    }
    if (key.name === "right" || key.name === "tab") {
      setTab((t) => (t + 1) % TABS.length)
      return
    }
  })

  return (
    <box flexDirection="column" flexGrow={1}>
      <box flexDirection="row" gap={1} paddingLeft={1}>
        <text fg={isLive ? T.up : T.warn}>{isLive ? "● MCP LIVE" : "○ MCP offline — start MT5 terminal"}</text>
        <text fg={CONN_COLOR[terminal.state] ?? T.dim}>terminal: {terminal.state.toUpperCase()}</text>
        <text fg={T.dim}>| {terminal.server} #{terminal.login}</text>
        <text fg={terminal.tradeAllowed ? T.up : T.down}>{terminal.tradeAllowed ? "trade allowed" : "trade disabled"}</text>
        {terminal.lastError && <text fg={T.down}>last_error: {terminal.lastError.slice(0, 40)}</text>}
      </box>

      <TabBar
        tabs={TABS}
        selected={tab}
        onChange={setTab}
        activeColor={T.cyan}
      />

       {tab === 0 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          <box flexDirection="column" width={40} flexShrink={0} gap={1}>
            <box title=" TERMINAL " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexShrink={0} paddingLeft={1}>
              <text fg={CONN_COLOR[terminal.state]}>{terminal.state.toUpperCase()} {terminal.connected ? "●" : "○"}</text>
              <text fg={T.borderBright}>{"─".repeat(32)}</text>
              <StatRow k="name" v={terminal.name} />
              <StatRow k="company" v={terminal.company} />
              <StatRow k="build" v={String(terminal.build)} />
              <StatRow k="path" v={terminal.path.slice(-28)} c={T.muted} />
              <StatRow k="dataPath" v={terminal.dataPath.slice(-26)} c={T.muted} />
              <StatRow k="leverage" v={`1:${terminal.leverage}`} />
              <StatRow k="currency" v={terminal.currency} />
              <StatRow k="ping" v={`${terminal.lastPingMs}ms`} c={terminal.lastPingMs < 100 ? T.up : T.warn} />
              <StatRow k="dlls" v={terminal.dllsAllowed ? "allowed" : "blocked"} c={terminal.dllsAllowed ? T.up : T.down} />
              <StatRow k="tradeApi" v={terminal.tradeApiDisabled ? "DISABLED" : "enabled"} c={terminal.tradeApiDisabled ? T.down : T.up} />
              <text fg={T.borderBright}>{"─".repeat(32)}</text>
              <text fg={T.dim}>invocation</text>
              <text fg={T.muted}>python -m metatrader5_mcp.main</text>
              <text fg={T.dim}>env: MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_PATH</text>
              <text fg={T.dim}>transport: stdio → @modelcontextprotocol/sdk</text>
              <text fg={T.dim}>tools: 29 (health, account, terminal, market, trading, positions, history)</text>
              {terminal.lastError && (
                <box flexDirection="column" marginTop={1}>
                  <text fg={T.down}>LAST ERROR</text>
                  <text fg={T.warn}>{terminal.lastError}</text>
                  <text fg={T.dim}>tool: mt5_last_error · mt5_health_check</text>
                </box>
              )}
            </box>
            <box title=" SECURE ACCOUNT — masked, keyring/enc, MCP login " titleColor={T.orange} borderStyle="single" borderColor={T.border} flexDirection="column" height={19} flexShrink={0} paddingLeft={1}>
              <SecureAccountForm />
            </box>
          </box>

          <box title=" ACCOUNT (LIVE) " titleColor={T.up} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
            <box flexDirection="row" gap={3}>
              <box flexDirection="column" width={22}>
                <text fg={T.dim}>BALANCE</text>
                <text fg={T.bright}>{fmtMoney(acct.balance)}</text>
                <text fg={T.dim}>EQUITY</text>
                <text fg={acct.equity >= acct.balance ? T.up : T.down}>{fmtMoney(acct.equity)}</text>
                <text fg={T.dim}>FLOATING</text>
                <text fg={acct.profit >= 0 ? T.up : T.down}>{fmtMoney(acct.profit)}</text>
              </box>
              <box flexDirection="column" flexGrow={1}>
                <StatRow k="margin" v={fmtMoney(acct.margin)} />
                <StatRow k="margin free" v={fmtMoney(acct.marginFree)} />
                <StatRow k="margin level" v={`${acct.marginLevel.toFixed(1)}%`} c={acct.marginLevel > 200 ? T.up : acct.marginLevel > 100 ? T.warn : T.down} />
                <StatRow k="leverage" v={`1:${acct.leverage}`} />
                <StatRow k="currency" v={acct.currency} />
                <text fg={T.borderBright}>{"─".repeat(28)}</text>
                <text fg={T.dim}>health: mt5_health_check + mt5_connection_status + mt5_last_error</text>
                <text fg={T.dim}>poll: mt5_account_info every 1.8s · mt5_version / mt5_server_info every 8s</text>
              </box>
            </box>
            <text fg={T.borderBright}>{"─".repeat(52)}</text>
            <text fg={T.dim}>MARGIN BAR (equity vs margin)</text>
            <text fg={acct.marginLevel > 200 ? T.up : acct.marginLevel > 100 ? T.warn : T.down}>{bar(Math.min(1, acct.marginLevel / 1000), 32)}</text>
            <text fg={T.dim}>Live backtests: mt5_copy_rates_from → BACKTEST screen (mt5_live:* entries)</text>
          </box>
        </box>
      )}

      {tab === 1 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          <box title={` SYMBOLS (${syms.length || 6}) `} titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" width={48} flexShrink={0} paddingLeft={1}>
            <scrollbox flexGrow={1} viewportCulling>
              <box flexDirection="row">
                <text fg={T.dim}>{"SYMBOL".padEnd(10)}{"DIGITS".padStart(7)}{"SPREAD".padStart(8)}{"MODE".padStart(10)}</text>
              </box>
              {syms.length === 0 ? (
                <text fg={T.dim}>symbols_get pending — connect MCP for live catalog (29 tools)</text>
              ) : (
                syms.slice(0, 120).map((s) => (
                  <box key={s.symbol} flexDirection="row">
                    <text fg={s.visible ? T.fg : T.dim}>{s.symbol.padEnd(10)}</text>
                    <text fg={T.muted}>{String(s.digits).padStart(7)}</text>
                    <text fg={T.dim}>{String(s.spread).padStart(8)}</text>
                    <text fg={s.tradeMode === 4 ? T.up : T.warn}>{s.tradeModeLabel.padStart(10)}</text>
                  </box>
                ))
              )}
            </scrollbox>
            <text fg={T.dim}>mt5_symbols_total · mt5_symbols_get(*) · mt5_symbol_info · mt5_symbol_select</text>
          </box>

          <box title=" LIVE TICKS (mt5_symbol_info_tick) " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
            <box flexDirection="row">
              <text fg={T.dim}>{"SYM".padEnd(8)}{"BID".padStart(11)}{"ASK".padStart(11)}{"SPREAD".padStart(9)}{"TIME".padStart(10)}</text>
            </box>
            <text fg={T.borderBright}>{"─".repeat(50)}</text>
            {Object.keys(ticks).length === 0 ? (
              <text fg={T.dim}>waiting for first tick poll… (1.8s interval via mt5_symbol_info_tick ×6)</text>
            ) : (
              Object.values(ticks).map((t) => (
                <box key={t.symbol} flexDirection="row">
                  <text fg={T.cyan}>{t.symbol.padEnd(8)}</text>
                  <text fg={T.fg}>{fmtNum(t.bid, 5).padStart(11)}</text>
                  <text fg={T.fg}>{fmtNum(t.ask, 5).padStart(11)}</text>
                  <text fg={t.spreadPts < 20 ? T.up : t.spreadPts < 50 ? T.warn : T.down}>{`${t.spreadPts}pt`.padStart(9)}</text>
                  <text fg={T.dim}>{fmtClock(t.timeMs).padStart(10)}</text>
                </box>
              ))
            )}
            <text fg={T.borderBright}>{"─".repeat(50)}</text>
            <text fg={T.dim}>also: mt5_copy_ticks_from / mt5_copy_ticks_range for tick history</text>
          </box>
        </box>
      )}

      {tab === 2 && (
        <box title={` POSITIONS (mt5_positions_get / mt5_positions_total) — ${useDeck((s) => s.positions.length)} open `} titleColor={T.up} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <box flexDirection="row">
            <text fg={T.dim}>{"TICKET".padEnd(10)}{"SYM".padEnd(8)}{"SIDE".padEnd(6)}{"VOL".padStart(6)}{"ENTRY".padStart(10)}{"SL".padStart(10)}{"TP".padStart(10)}{"PNL".padStart(10)}{"R".padStart(7)}</text>
          </box>
          <text fg={T.borderBright}>{"─".repeat(86)}</text>
          <scrollbox flexGrow={1} viewportCulling>
            {useDeck((s) => s.positions).length === 0 ? (
              <text fg={T.dim}>flat — no open positions (poll mt5_positions_get + mt5_positions_total every 1.8s)</text>
            ) : (
              useDeck((s) => s.positions).map((p) => (
                <box key={p.ticket} flexDirection="row">
                  <text fg={T.muted}>{String(p.ticket).padEnd(10)}</text>
                  <text fg={T.fg}>{p.symbol.padEnd(8)}</text>
                  <text fg={p.side === "BUY" ? T.up : T.down}>{p.side.padEnd(6)}</text>
                  <text fg={T.fg}>{p.volume.toFixed(2).padStart(6)}</text>
                  <text fg={T.dim}>{fmtNum(p.entry, 4).padStart(10)}</text>
                  <text fg={T.dim}>{fmtNum(p.sl, 4).padStart(10)}</text>
                  <text fg={T.dim}>{fmtNum(p.tp, 4).padStart(10)}</text>
                  <text fg={p.pnl >= 0 ? T.up : T.down}>{fmtMoney(p.pnl).padStart(10)}</text>
                  <text fg={p.unrealizedR >= 1 ? T.up : p.unrealizedR >= 0 ? T.warn : T.down}>{`${p.unrealizedR >= 0 ? "+" : ""}${p.unrealizedR.toFixed(2)}R`.padStart(7)}</text>
                </box>
              ))
            )}
          </scrollbox>
          <text fg={T.dim}>mapped from MT5 Position dict (ticket/type/volume/price_open/sl/tp/profit) · mt5_positions_total for count</text>
        </box>
      )}

      {tab === 3 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          <box title={` PENDING ORDERS (mt5_orders_get / mt5_orders_total) — ${pending.length} `} titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" width="52%" paddingLeft={1}>
            <box flexDirection="row">
              <text fg={T.dim}>{"TICKET".padEnd(10)}{"SYM".padEnd(8)}{"TYPE".padEnd(14)}{"PRICE".padStart(10)}{"VOL".padStart(6)}</text>
            </box>
            <text fg={T.borderBright}>{"─".repeat(50)}</text>
            <scrollbox flexGrow={1} viewportCulling>
              {pending.length === 0 ? (
                <text fg={T.dim}>no pending orders — mt5_orders_get returns 0 rows (1.8s poll)</text>
              ) : (
                pending.map((o) => (
                  <box key={o.ticket} flexDirection="row">
                    <text fg={T.muted}>{String(o.ticket).padEnd(10)}</text>
                    <text fg={T.fg}>{o.symbol.padEnd(8)}</text>
                    <text fg={T.warn}>{o.typeLabel.padEnd(14)}</text>
                    <text fg={T.fg}>{fmtNum(o.price, 4).padStart(10)}</text>
                    <text fg={T.fg}>{o.volume.toFixed(2).padStart(6)}</text>
                  </box>
                ))
              )}
            </scrollbox>
            <text fg={T.dim}>mt5_orders_get(group/symbol/ticket) · modify via TOOLS tab</text>
          </box>

          <box title=" ORDER PAD (mt5_order_check → mt5_order_send) " titleColor={T.down} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
            <OrderPad />
          </box>
        </box>
      )}

      {tab === 4 && (
        <box title=" HISTORY 30d (mt5_history_* + mt5_copy_* backtests) " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <box flexDirection="row" gap={3}>
            <box flexDirection="column" width={28}>
              <text fg={T.dim}>RANGE</text>
              <text fg={T.fg}>{new Date(history.fromTs).toISOString().slice(0, 10)} → {new Date(history.toTs).toISOString().slice(0, 10)}</text>
              <text fg={T.dim}>ORDERS / DEALS</text>
              <text fg={T.bright}>{history.ordersCount} / {history.dealsCount}</text>
              <text fg={T.borderBright}>{"─".repeat(26)}</text>
              <StatRow k="gross profit" v={fmtMoney(history.grossProfit)} c={history.grossProfit >= 0 ? T.up : T.down} />
              <StatRow k="commission" v={fmtMoney(history.commission)} c={T.down} />
              <StatRow k="swap" v={fmtMoney(history.swap)} c={history.swap >= 0 ? T.up : T.down} />
              <text fg={T.borderBright}>{"─".repeat(26)}</text>
              <StatRow k="net profit" v={fmtMoney(history.netProfit)} c={history.netProfit >= 0 ? T.up : T.down} />
              <text fg={T.borderBright}>{"─".repeat(26)}</text>
              <text fg={T.dim}>LIVE BACKTESTS</text>
              {useDeck((s) => s.backtests).filter((b) => b.id.startsWith("mt5_live")).length === 0 ? (
                <text fg={T.dim}>no live bars yet — mt5_copy_rates_from pending</text>
              ) : (
                useDeck((s) => s.backtests).filter((b) => b.id.startsWith("mt5_live")).map((b) => (
                  <box key={b.id} flexDirection="row" justifyContent="space-between">
                    <text fg={T.cyan}>{b.strategyId.slice(9, 19)}</text>
                    <text fg={T.up}>{b.equityCurve.length} bars</text>
                  </box>
                ))
              )}
              <text fg={T.dim}>see BACKTEST screen (b) for equity curves</text>
            </box>
            <box flexDirection="column" flexGrow={1}>
              <text fg={T.dim}>TOOLS</text>
              <text fg={T.muted}>mt5_history_orders_total · mt5_history_orders_get(group/ticket/position)</text>
              <text fg={T.muted}>mt5_history_deals_total · mt5_history_deals_get(group/ticket/position)</text>
              <text fg={T.muted}>mt5_copy_rates_from / _range / _from_pos + mt5_copy_ticks_*</text>
              <text fg={T.borderBright}>{"─".repeat(36)}</text>
              <text fg={T.dim}>QUICK COPY</text>
              <QuickCopyPanel />
            </box>
          </box>
          <text fg={T.dim}>poll history totals every 8s · live bars every 60s → backtests (mt5_live:*)</text>
        </box>
      )}

      {tab === 5 && (
        <box title=" TOOLS — full 29 (mt5_* + paper.*) " titleColor={T.orange} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <ToolPalette />
        </box>
      )}

      {tab === 6 && (
        <box flexDirection="row" flexGrow={1} gap={1}>
          <box title={` MCP CALLS (${calls.length}) `} titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" width={52} flexShrink={0} paddingLeft={1}>
            <select
              flexGrow={1}
              focused
              options={calls.length === 0 ? [{ name: "no calls yet", description: "connect MCP for live traffic", value: "empty" }] : calls.slice(-80).map((c) => ({
                name: `${c.ok ? "✓" : "✗"} ${c.tool}`,
                description: `${c.argsSummary.slice(0, 28)} → ${c.resultSummary.slice(0, 30)} · ${c.latencyMs}ms`,
                value: String(c.id),
              }))}
              showDescription
              showScrollIndicator
              showSelectionIndicator
              wrapSelection
              textColor={T.fg}
              descriptionColor={T.dim}
              selectedBackgroundColor={T.raised}
              selectedTextColor={T.bright}
              selectedDescriptionColor={T.teal}
              backgroundColor={T.bg}
              onChange={(_i) => {}}
            />
            <text fg={T.dim}>every MCP call journalled + mirrored to TASKS toolCalls</text>
          </box>

          <box title={selectedCall ? ` ${selectedCall.tool} ` : " — "} titleColor={selectedCall?.ok ? T.up : selectedCall ? T.down : T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
            {selectedCall ? (
              <scrollbox flexGrow={1} viewportCulling>
                <box flexDirection="row" gap={2}>
                  <text fg={T.bg} bg={selectedCall.ok ? T.up : T.down}> {selectedCall.ok ? "OK" : "ERR"} </text>
                  <text fg={T.dim}>{fmtClock(selectedCall.ts)}</text>
                  <text fg={T.muted}>{selectedCall.latencyMs}ms</text>
                  {selectedCall.retcode !== undefined && <text fg={T.warn}>retcode {selectedCall.retcode}</text>}
                  {selectedCall.ticket ? <text fg={T.teal}>ticket #{selectedCall.ticket}</text> : null}
                </box>
                <text fg={T.borderBright}>{"─".repeat(48)}</text>
                <text fg={T.dim}>TOOL</text>
                <text fg={T.bright}>{selectedCall.tool}</text>
                <text fg={T.dim}>ARGS</text>
                <text fg={T.muted}>{selectedCall.argsSummary || "—"}</text>
                <text fg={T.dim}>RESULT</text>
                <text fg={T.fg}>{selectedCall.resultSummary || "—"}</text>
                {selectedCall.traceId && <text fg={T.dim}>trace {selectedCall.traceId}</text>}
                <text fg={T.borderBright}>{"─".repeat(48)}</text>
                <text fg={T.dim}>MCP SERVER</text>
                <text fg={T.muted}>metatrader5-mcp v1.0.0 · FastMCP · stdio transport</text>
                <text fg={T.muted}>command: python -m metatrader5_mcp.main</text>
                <text fg={T.muted}>29 tools: mt5_* (23) + connection(3) + health(3)</text>
              </scrollbox>
            ) : (
              <text fg={T.dim}>no MCP calls yet — start with TRANSPORT=mcp bun src/index.tsx</text>
            )}
          </box>
        </box>
      )}
    </box>
  )
}

function QuickCopyPanel() {
  const [symbol, setSymbol] = useState("EURUSD")
  const [count, setCount] = useState("100")
  const [out, setOut] = useState<string>("—")
  const [busy, setBusy] = useState(false)

  const run = async (tool: string) => {
    setBusy(true)
    setOut("calling…")
    try {
      const args: any =
        tool === "mt5_copy_rates_from"
          ? { symbol, timeframe: 60, date_from: new Date(Date.now() - 86400000).toISOString().slice(0, 19).replace("T", " "), count: Number(count) || 100 }
          : tool === "mt5_symbol_info"
            ? { symbol }
            : { symbol }
      const r = await mcpCallTool(tool as any, args)
      setOut(r.ok ? JSON.stringify(r.data, null, 2).slice(0, 2000) : `ERR: ${r.error}`)
    } catch (e: any) {
      setOut(String(e?.message ?? e).slice(0, 1000))
    } finally {
      setBusy(false)
    }
  }

  return (
    <box flexDirection="column">
      <box flexDirection="row" gap={1}>
        <input placeholder="symbol" value={symbol} onInput={setSymbol} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <input placeholder="count" value={count} onInput={setCount} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={6} />
        <text fg={T.dim}>{busy ? "…" : ""}</text>
      </box>
      <box flexDirection="row" gap={1} marginTop={1}>
        <select
          width={36}
          height={5}
          focused={false}
          options={[
            { name: "mt5_symbol_info", description: "spec", value: "mt5_symbol_info" },
            { name: "mt5_symbol_info_tick", description: "tick", value: "mt5_symbol_info_tick" },
            { name: "mt5_copy_rates_from", description: "OHLCV", value: "mt5_copy_rates_from" },
          ]}
          showDescription
          showScrollIndicator={false}
          selectedBackgroundColor={T.teal}
          selectedTextColor={T.bg}
          textColor={T.fg}
          onSelect={(_i, opt) => {
            const v = String(opt?.value ?? "mt5_symbol_info")
            void run(v)
          }}
        />
      </box>
      <scrollbox maxHeight={8} viewportCulling>
        <text fg={T.muted}>{out.slice(0, 2000)}</text>
      </scrollbox>
    </box>
  )
}

function SecureAccountForm() {
  const [name, setName] = useState("demo")
  const [login, setLogin] = useState("5055089321")
  const [server, setServer] = useState("MetaQuotes-Demo")
  const [typ, setTyp] = useState("DEMO")
  const [password, setPassword] = useState("")
  const [out, setOut] = useState("masked input → keyring / .secure_store.enc (Fernet) → never plaintext in accounts.json. Use LOGIN via MCP to verify.")
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (!name || !login || !server || !password) { setOut("fill name/login/server/password (all masked, stored securely)"); return }
    setBusy(true)
    setOut("saving securely…")
    try {
      // Prefer local GUI API if running (port 8000), else direct MCP check only
      let saved = false
      try {
        const r = await apiFetch("http://127.0.0.1:8000/api/config/accounts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, login: Number(login), server, type: typ, password }),
        })
        const j: any = await r.json()
        if (j.status === "success") {
          saved = true
          setOut(`✓ saved secure account '${name}' via GUI API (backend ${j.secure ? "keyring/enc" : "env"}, store_key ${j.store_key ?? "—"}). Now LOGIN via MCP…`)
        } else {
          setOut(`API save failed: ${j.message ?? JSON.stringify(j).slice(0,300)}`)
        }
      } catch (e: any) {
        setOut(`GUI API not reachable (${String(e.message).slice(0,80)}), falling back to MCP-only verify. Save via CLI: python mt5_trader.py accounts secure-add --name ${name} --login ${login} --server ${server} --type ${typ} --prompt`)
      }
      // Also fire MCP login test directly (reads from secure_store if env MT5_* set, otherwise via GUI API path may need env injection)
      // We set env via GUI API already; now check via mt5_account_info
      const r2 = await mcpCallTool("mt5_account_info" as any, {} as any)
      if (r2.ok) {
        setOut((s) => s + `\n✓ MCP mt5_account_info: ${JSON.stringify(r2.data).slice(0,600)}`)
      } else {
        setOut((s) => s + `\nMCP login pending: ${r2.error?.slice(0,300) ?? "no terminal"}. If saved via API, restart MT5 terminal + ensure MT5_LOGIN/PASSWORD/SERVER env flows to mt5_mcp (deck will pick up new account as default).`)
      }
      // clear password from memory
      setPassword("")
    } finally { setBusy(false) }
  }

  const testLogin = async () => {
    setBusy(true)
    setOut("testing MCP login → mt5_account_info …")
    try {
      // Prefer GUI API test-login which uses secure_store + MT5Client
      try {
        const r = await apiFetch("http://127.0.0.1:8000/api/config/accounts/test-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        const j: any = await r.json()
        if (j.ok) {
          setOut(`✓ secure login OK via secure_store (${j.backend}) — equity ${JSON.stringify(j.info).slice(0,500)}`)
          if (!j.ok) throw new Error(j.error)
          return
        }
        setOut(`GUI test-login: ${j.error ?? JSON.stringify(j).slice(0,400)} — trying MCP direct…`)
      } catch {}
      const r2 = await mcpCallTool("mt5_account_info" as any, {} as any)
      setOut(r2.ok ? `✓ MCP OK: ${JSON.stringify(r2.data).slice(0,800)}` : `✗ MCP failed: ${r2.error?.slice(0,400)}`)
    } finally { setBusy(false) }
  }

  return (
    <box flexDirection="column" gap={1}>
      <box flexDirection="row" gap={1}>
        <input placeholder="name" value={name} onInput={setName} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <input placeholder="login" value={login} onInput={setLogin} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={12} />
        <input placeholder="server" value={server} onInput={setServer} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={18} />
        <text fg={T.dim}>{typ}</text>
      </box>
      <box flexDirection="row" gap={1}>
        <input placeholder="password (masked, never logged)" value={password} onInput={setPassword} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={24} />
        <select width={10} height={3} options={[{name:"DEMO",description:"demo",value:"DEMO"},{name:"LIVE",description:"live",value:"LIVE"}]} showDescription={false} selectedBackgroundColor={T.borderBright} textColor={T.fg} onSelect={(_i,opt)=> setTyp(String(opt?.value ?? "DEMO"))} />
        <select width={18} height={3} focused={false} options={[{name:"SAVE SECURE",description:"vault+keyring",value:"save"},{name:"LOGIN via MCP",description:"mt5_account_info",value:"login"}]} showDescription textColor={T.fg} selectedBackgroundColor={T.up} selectedTextColor={T.bg} onSelect={(_i,opt)=>{ const v=String(opt?.value); if(v==="save") void save(); else void testLogin(); }} />
        <text fg={T.dim}>{busy ? "●" : ""}</text>
      </box>
      <box flexDirection="row">
        <text fg={T.muted}>{out.slice(0, 1200)}</text>
      </box>
      <text fg={T.dim}>CLI equivalent: python mt5_trader.py accounts secure-add --name {name} --login {login} --server {server} --type {typ} --prompt --test-mcp  ·  settings: python mt5_trader.py settings --section accounts  ·  deck: python mt5_trader.py deck</text>
    </box>
  )
}

// ── full 29-tool palette ─────────────────────────────────────────────
const TOOL_GROUPS: { label: string; tools: { name: string; desc: string }[] }[] = [
  { label: "CONNECTION", tools: [{ name: "mt5_version", desc: "terminal version" }, { name: "mt5_terminal_info", desc: "terminal status/paths" }, { name: "mt5_last_error", desc: "last error code+msg" }, { name: "mt5_account_info", desc: "balance/equity/margin" }] },
  { label: "HEALTH", tools: [{ name: "mt5_health_check", desc: "comprehensive health" }, { name: "mt5_server_info", desc: "python/MT5/runtime" }, { name: "mt5_connection_status", desc: "quick connected/logged_in" }] },
  { label: "MARKET", tools: [{ name: "mt5_symbols_total", desc: "count of symbols" }, { name: "mt5_symbols_get", desc: "list symbols (group filter)" }, { name: "mt5_symbol_info", desc: "spec for one symbol" }, { name: "mt5_symbol_info_tick", desc: "current bid/ask/last" }, { name: "mt5_symbol_select", desc: "add/remove from MarketWatch" }, { name: "mt5_copy_rates_from", desc: "OHLCV from date+count" }, { name: "mt5_copy_rates_range", desc: "OHLCV date_from→date_to" }, { name: "mt5_copy_rates_from_pos", desc: "OHLCV from pos+count" }, { name: "mt5_copy_ticks_from", desc: "ticks from date+count" }, { name: "mt5_copy_ticks_range", desc: "ticks date_from→date_to" }] },
  { label: "POSITIONS", tools: [{ name: "mt5_positions_total", desc: "count open positions" }, { name: "mt5_positions_get", desc: "list open positions" }, { name: "mt5_orders_total", desc: "count pending orders" }, { name: "mt5_orders_get", desc: "list pending orders" }] },
  { label: "HISTORY", tools: [{ name: "mt5_history_orders_total", desc: "count history orders in range" }, { name: "mt5_history_orders_get", desc: "list history orders" }, { name: "mt5_history_deals_total", desc: "count deals in range" }, { name: "mt5_history_deals_get", desc: "list deals" }] },
  { label: "TRADING", tools: [{ name: "mt5_order_check", desc: "validate without sending" }, { name: "mt5_order_calc_margin", desc: "required margin" }, { name: "mt5_order_calc_profit", desc: "potential profit" }] },
]

const ALL_TOOLS = TOOL_GROUPS.flatMap((g) => g.tools)

function ToolPalette() {
  const [tool, setTool] = useState("mt5_account_info")
  const [symbol, setSymbol] = useState("EURUSD")
  const [extraJson, setExtraJson] = useState("{}")
  const [out, setOut] = useState("select a tool → edit args → RUN (↩). 28 tools via metatrader5-mcp.")
  const [busy, setBusy] = useState(false)

  const current = ALL_TOOLS.find((t) => t.name === tool) ?? ALL_TOOLS[0]

  const argHint = (() => {
    switch (tool) {
      case "mt5_symbols_get": return `{ "group": "*EUR*" }  or {} for all`
      case "mt5_symbol_info": case "mt5_symbol_info_tick": return `{ "symbol": "${symbol}" }`
      case "mt5_symbol_select": return `{ "symbol": "${symbol}", "enable": true }`
      case "mt5_copy_rates_from": return `{ "symbol": "${symbol}", "timeframe": 60, "date_from": "2026-08-01 00:00:00", "count": 100 }`
      case "mt5_copy_rates_range": return `{ "symbol": "${symbol}", "timeframe": 60, "date_from": "2026-08-01", "date_to": "2026-08-26" }`
      case "mt5_copy_rates_from_pos": return `{ "symbol": "${symbol}", "timeframe": 60, "start_pos": 0, "count": 100 }`
      case "mt5_copy_ticks_from": return `{ "symbol": "${symbol}", "date_from": "2026-08-25 00:00:00", "count": 100, "flags": 6 }`
      case "mt5_copy_ticks_range": return `{ "symbol": "${symbol}", "date_from": "2026-08-25", "date_to": "2026-08-26", "flags": 6 }`
      case "mt5_positions_get": case "mt5_orders_get": return `{ "symbol": "${symbol}" }  or {} for all · group/ticket also valid`
      case "mt5_history_orders_total": case "mt5_history_deals_total": return `{ "date_from": "2026-07-01", "date_to": "2026-08-26" }`
      case "mt5_history_orders_get": case "mt5_history_deals_get": return `{ "date_from": "2026-07-01", "date_to": "2026-08-26", "group": "${symbol}" }`
      case "mt5_order_calc_margin": return `{ "action": 0, "symbol": "${symbol}", "volume": 0.1, "price": 1.0842 }`
      case "mt5_order_calc_profit": return `{ "action": 0, "symbol": "${symbol}", "volume": 0.1, "price_open": 1.0842, "price_close": 1.09 }`
      case "mt5_order_check": return `{ "action": 1, "symbol": "${symbol}", "volume": 0.1, "type": 0, "price": 1.0842, "sl": 1.08, "tp": 1.09, "deviation": 10 }`
      default: return `{ } — no args or { "symbol": "${symbol}" }`
    }
  })()

  const run = async () => {
    setBusy(true)
    setOut("calling…")
    try {
      let args: any = {}
      const trimmed = extraJson.trim()
      if (trimmed && trimmed !== "{}") {
        try {
          args = JSON.parse(trimmed)
        } catch (e: any) {
          setOut(`ERR: invalid JSON in args: ${String(e.message).slice(0, 120)}\nHint: ${argHint}`)
          setBusy(false)
          return
        }
      } else {
        // auto-fill common symbol param for tools that need it
        if (["mt5_symbol_info", "mt5_symbol_info_tick", "mt5_symbol_select"].includes(tool)) args = { symbol }
        if (tool === "mt5_symbols_get" && symbol) args = { group: `*${symbol}*` }
      }
      const r = await mcpCallTool(tool as any, args)
      const pretty = r.ok ? JSON.stringify(r.data, null, 2) : `ERR ${r.error}`
      setOut(pretty.slice(0, 4000))
    } catch (e: any) {
      setOut(String(e?.message ?? e).slice(0, 2000))
    } finally {
      setBusy(false)
    }
  }

  return (
    <box flexDirection="column" flexGrow={1}>
      <box flexDirection="row" gap={1}>
        <select
          width={34}
          height={10}
          focused
          options={TOOL_GROUPS.flatMap((g) => [{ name: `── ${g.label} ──`, description: "", value: `__hdr_${g.label}` } as any].concat(g.tools.map((t) => ({ name: t.name, description: t.desc, value: t.name }))))}
          showDescription
          showScrollIndicator
          selectedBackgroundColor={T.borderBright}
          selectedTextColor={T.bright}
          textColor={T.fg}
          descriptionColor={T.dim}
          onSelect={(_i, opt) => {
            const v = String(opt?.value ?? "")
            if (v.startsWith("__hdr_")) return
            setTool(v)
          }}
        />
        <box flexDirection="column" flexGrow={1} gap={1}>
          <box flexDirection="row" gap={1}>
            <text fg={T.dim}>tool</text>
            <text fg={T.cyan}>{current.name}</text>
            <text fg={T.dim}>{busy ? "● calling…" : ""}</text>
          </box>
          <text fg={T.muted}>{current.desc}</text>
          <text fg={T.dim}>hint: {argHint.slice(0, 70)}</text>
          <box flexDirection="row" gap={1}>
            <input placeholder="symbol filter" value={symbol} onInput={setSymbol} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={12} />
            <text fg={T.dim}>args JSON</text>
          </box>
          <input placeholder='{} or {"symbol":"EURUSD","count":100}' value={extraJson} onInput={setExtraJson} onSubmit={run} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={44} />
          <select
            width={16}
            height={3}
            focused={false}
            options={[{ name: "RUN ▶", description: tool, value: "run" }]}
            showDescription={false}
            showScrollIndicator={false}
            selectedBackgroundColor={T.up}
            selectedTextColor={T.bg}
            textColor={T.fg}
            onSelect={() => void run()}
          />
          <text fg={T.dim}>↩ in JSON input also runs</text>
        </box>
      </box>
      <box title=" RESULT " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} marginTop={1}>
        <scrollbox flexGrow={1} viewportCulling>
          <code content={out} filetype="json" syntaxStyle={syntaxTheme} fg={T.muted} bg={T.panel} drawUnstyledText wrapMode="word" selectable />
        </scrollbox>
      </box>
    </box>
  )
}

function OrderPad() {
  const [symbol, setSymbol] = useState("EURUSD")
  const [volume, setVolume] = useState("0.10")
  const [price, setPrice] = useState("1.0842")
  const [sl, setSl] = useState("1.0800")
  const [tp, setTp] = useState("1.0900")
  const [checkOut, setCheckOut] = useState("—")
  const [busy, setBusy] = useState(false)
  const mt5Ticks = useDeck((s) => s.mt5.ticks)
  const tick = mt5Ticks[symbol]

  const doCheck = async () => {
    setBusy(true)
    setCheckOut("mt5_order_check …")
    try {
      const p = Number(price) || (tick ? tick.ask : 1.0842)
      const args: any = { action: 1, symbol, volume: Number(volume) || 0.1, type: 0, price: p, sl: Number(sl) || undefined, tp: Number(tp) || undefined, deviation: 10, type_filling: 1 }
      const r = await mcpCallTool("mt5_order_check" as any, args)
      setCheckOut(r.ok ? `✓ CHECK PASS\n${JSON.stringify(r.data, null, 2).slice(0, 1200)}` : `✗ CHECK FAIL\n${r.error}`)
    } catch (e: any) {
      setCheckOut(String(e.message).slice(0, 800))
    } finally {
      setBusy(false)
    }
  }

  const doMargin = async () => {
    setBusy(true)
    try {
      const p = Number(price) || (tick ? tick.ask : 1.0842)
      const r = await mcpCallTool("mt5_order_calc_margin" as any, { action: 0, symbol, volume: Number(volume) || 0.1, price: p })
      setCheckOut(r.ok ? `margin: ${JSON.stringify(r.data).slice(0, 400)}` : `ERR ${r.error}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <box flexDirection="column" flexGrow={1}>
      <box flexDirection="row" gap={1}>
        <input placeholder="symbol" value={symbol} onInput={setSymbol} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <input placeholder="vol" value={volume} onInput={setVolume} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={7} />
        {tick && <text fg={T.dim}>bid {fmtNum(tick.bid, 5)} ask {fmtNum(tick.ask, 5)}</text>}
      </box>
      <box flexDirection="row" gap={1} marginTop={1}>
        <input placeholder="price" value={price} onInput={setPrice} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <input placeholder="sl" value={sl} onInput={setSl} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <input placeholder="tp" value={tp} onInput={setTp} backgroundColor={T.raised} focusedBackgroundColor={T.raised} textColor={T.bright} cursorColor={T.cyan} width={10} />
        <text fg={T.dim}>{busy ? "…" : ""}</text>
      </box>
      <box flexDirection="row" gap={1} marginTop={1}>
        <select
          width={18}
          height={4}
          focused={false}
          options={[{ name: "CHECK", description: "order_check", value: "check" }, { name: "MARGIN", description: "calc_margin", value: "margin" }]}
          showDescription
          showScrollIndicator={false}
          selectedBackgroundColor={T.teal}
          selectedTextColor={T.bg}
          textColor={T.fg}
          onSelect={(_i, opt) => {
            const v = String(opt?.value)
            if (v === "check") void doCheck()
            else void doMargin()
          }}
        />
        <text fg={T.dim}>check validates risk/margin before send; send is gated by RiskController in live engine</text>
      </box>
      <scrollbox flexGrow={1} marginTop={1} viewportCulling>
        <text fg={T.muted}>{checkOut.slice(0, 2000)}</text>
      </scrollbox>
      <text fg={T.down}>order_send is intentionally not exposed as one-click — use order_check + engine validator path</text>
    </box>
  )
}
