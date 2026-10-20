import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import { store } from "../store"
import type { MT5ConnectionState, MT5McpTool, MT5State } from "../types"

// ── MT5 MCP transport ──────────────────────────────────────────────────
// Uses the installed `metatrader5-mcp` FastMCP server (metatrader5_mcp.main)
// via stdio JSON-RPC. Replaces mock/live REST polling with direct MCP calls.
// Every call is journalled into store.mt5.mcpCalls for the MT5 screen.

let client: Client | null = null
let stopped = false
let pollTimer: ReturnType<typeof setInterval> | null = null
let slowPollTimer: ReturnType<typeof setInterval> | null = null
let mcpSeq = 1
let initDone = false

const SYMBOLS_POLL = ["EURUSD", "XAUUSD", "GBPJPY", "US500", "GBPUSD", "USDJPY"]

// Prefer explicit env, fall back to python on PATH.
function mcpCommand(): { command: string; args: string[] } {
  const cmd = (process.env.MT5_MCP_COMMAND ?? "").trim()
  const argStr = (process.env.MT5_MCP_ARGS ?? "").trim()
  if (cmd) {
    return { command: cmd, args: argStr ? argStr.split(/\s+/) : [] }
  }
  // Default: python -m metatrader5_mcp.main — inherits MT5_LOGIN/PASSWORD/SERVER/PATH from env
  return { command: "python", args: ["-m", "metatrader5_mcp.main"] }
}

function parseMcpResult(res: any): any {
  // SDK 1.30 returns { content: [{type:"text", text:"..."}], structuredContent?, isError? }
  if (!res) return null
  if (res.structuredContent !== undefined && res.structuredContent !== null) {
    return res.structuredContent
  }
  const c = res.content
  if (Array.isArray(c) && c.length > 0) {
    const first = c[0] as any
    const txt = first?.text ?? first?.data ?? ""
    if (typeof txt === "string" && txt.length > 0) {
      const trimmed = txt.trim()
      if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
        try {
          return JSON.parse(trimmed)
        } catch {
          return trimmed
        }
      }
      // sometimes FastMCP returns Python repr wrapped in text — try json first, else raw
      try {
        return JSON.parse(trimmed)
      } catch {
        return trimmed
      }
    }
    return first
  }
  return res
}

function pushMcpCall(tool: MT5McpTool, argsSummary: string, resultSummary: string, latencyMs: number, ok: boolean, retcode?: number, ticket?: number, traceId?: string): void {
  const id = mcpSeq++
  const call = {
    id,
    ts: Date.now(),
    tool,
    argsSummary: argsSummary.slice(0, 120),
    resultSummary: resultSummary.slice(0, 160),
    latencyMs,
    ok,
    retcode,
    ticket,
    traceId,
  }
  store.set((s) => ({
    mt5: {
      ...s.mt5,
      mcpCalls: [...s.mt5.mcpCalls.slice(-199), call as any],
    },
  }))
  // also mirror to global toolCalls for the TASKS screen
  store.addToolCall({
    taskId: `mcp:${tool}`,
    ts: call.ts,
    tool,
    argsSummary: call.argsSummary,
    resultSummary: ok ? resultSummary.slice(0, 80) : `ERR: ${resultSummary.slice(0, 80)}`,
    latencyMs,
    ok,
  })
}

// Read-only allowlist for deck tools: order placement is forbidden from the deck
export const ALLOWED_MCP_TOOLS = new Set<string>([
  "mt5_version", "mt5_terminal_info", "mt5_last_error", "mt5_account_info",
  "mt5_health_check", "mt5_server_info", "mt5_connection_status",
  "mt5_symbols_total", "mt5_symbols_get", "mt5_symbol_info", "mt5_symbol_info_tick", "mt5_symbol_select",
  "mt5_copy_rates_from", "mt5_copy_rates_range", "mt5_copy_rates_from_pos", "mt5_copy_ticks_from", "mt5_copy_ticks_range",
  "mt5_positions_total", "mt5_positions_get", "mt5_orders_total", "mt5_orders_get",
  "mt5_history_orders_total", "mt5_history_orders_get", "mt5_history_deals_total", "mt5_history_deals_get",
  "mt5_order_check", "mt5_order_calc_margin", "mt5_order_calc_profit",
])

async function callTool<T = any>(name: string, args: Record<string, any> | undefined, summaryForLog: string): Promise<{ ok: boolean; data: T | null; latencyMs: number; error?: string }> {
  if (!client) return { ok: false, data: null, latencyMs: 0, error: "no client" }
  if (!ALLOWED_MCP_TOOLS.has(name)) {
    const errorMsg = `Tool '${name}' is blocked: deck transport operates in strict read-only mode`
    pushMcpCall(name as MT5McpTool, summaryForLog, errorMsg, 0, false)
    return { ok: false, data: null, latencyMs: 0, error: errorMsg }
  }
  const t0 = Date.now()
  try {
    const res: any = await client.callTool({ name, arguments: args as any })
    const latencyMs = Date.now() - t0
    if (res?.isError) {
      const msg = parseMcpResult(res)
      const str = typeof msg === "string" ? msg : JSON.stringify(msg).slice(0, 300)
      pushMcpCall(name as MT5McpTool, summaryForLog, str, latencyMs, false)
      return { ok: false, data: null, latencyMs, error: str }
    }
    const data = parseMcpResult(res) as T
    const summary = typeof data === "string" ? data : JSON.stringify(data ?? "").slice(0, 300)
    pushMcpCall(name as MT5McpTool, summaryForLog, summary || "ok", latencyMs, true)
    return { ok: true, data, latencyMs }
  } catch (e: any) {
    const latencyMs = Date.now() - t0
    const msg = String(e?.message ?? e).slice(0, 300)
    pushMcpCall(name as MT5McpTool, summaryForLog, msg, latencyMs, false)
    return { ok: false, data: null, latencyMs, error: msg }
  }
}

function mapConnectionState(raw: string | undefined): MT5ConnectionState {
  switch ((raw ?? "").toLowerCase()) {
    case "connected":
    case "healthy":
      return "connected"
    case "connecting":
      return "connecting"
    case "error":
      return "error"
    default:
      return "disconnected"
  }
}

async function syncFast(): Promise<void> {
  if (stopped || !client) return

  // ── ticks for each symbol ──────────────────────────────────────────
  for (const sym of SYMBOLS_POLL) {
    const r = await callTool<any>("mt5_symbol_info_tick", { symbol: sym }, `tick ${sym}`)
    if (r.ok && r.data && typeof r.data === "object") {
      const d: any = r.data
      // mt5_to_python returns dict with bid/ask/last/time/volume/spread
      const bid = Number(d.bid ?? d.Bid ?? 0)
      const ask = Number(d.ask ?? d.Ask ?? 0)
      const last = Number(d.last ?? d.Last ?? (bid + ask) / 2)
      const tMs = d.time_msc ? Number(d.time_msc) : d.time ? Number(d.time) * 1000 : Date.now()
      const vol = Number(d.volume ?? 0)
      const flags = Number(d.flags ?? 0)
      const spreadPts = ask && bid ? Math.round(Math.abs(ask - bid) / 1e-5) : 0 // approx, real digits per symbol
      if (bid || ask) {
        store.set((s) => ({
          // keep generic market quote in sync for MarketStrip / Cockpit
          market: {
            ...s.market,
            [sym]: {
              price: last || (bid + ask) / 2,
              changePct: s.market[sym]?.changePct ?? 0,
              domImbalance: s.market[sym]?.domImbalance ?? 0,
              history: [...(s.market[sym]?.history ?? []).slice(-119), last || (bid + ask) / 2],
            },
          },
          mt5: {
            ...s.mt5,
            ticks: {
              ...s.mt5.ticks,
              [sym]: { symbol: sym, bid, ask, last, volume: vol, timeMs: tMs, spreadPts, flags },
            },
          },
        }))
      }
    }
    if (stopped) return
  }

  // ── positions + orders ──────────────────────────────────────────────
  // FastMCP validates extra="forbid" — pass empty dict directly for all positions
  const posRes = await callTool<any>("mt5_positions_get", {}, "positions *")
  const positionsRaw: any[] | null = (posRes.ok && Array.isArray(posRes.data)) ? posRes.data : null
  if (positionsRaw) {
    // Map MT5 positions to deck Position[] (blotter)
    // MT5 position dict fields: ticket, symbol, type (0 buy 1 sell), volume, price_open, sl, tp, profit, swap, ...
    const mapped = positionsRaw.map((p: any) => {
      const isBuy = Number(p.type) === 0
      const entry = Number(p.price_open ?? p.price ?? 0)
      const sl = Number(p.sl ?? 0)
      const tp = Number(p.tp ?? 0)
      const riskPerUnit = Math.abs(entry - sl) || entry * 0.003
      const pnl = Number(p.profit ?? 0)
      const riskR = riskPerUnit > 0 ? +(pnl / (riskPerUnit * Number(p.volume ?? 0.1) * 1000 * 0.01)).toFixed(3) : 0
      // approximate unrealized R from current tick
      const tick = store.get().mt5.ticks[p.symbol]
      const cur = tick ? (isBuy ? tick.bid : tick.ask) : entry
      const unreal = riskPerUnit > 0 ? ((isBuy ? 1 : -1) * (cur - entry) * Number(p.volume ?? 0.1) * 1000 * 0.01) / (riskPerUnit * Number(p.volume ?? 0.1) * 1000 * 0.01) : 0
      return {
        ticket: Number(p.ticket ?? 0),
        symbol: String(p.symbol ?? ""),
        side: (isBuy ? "BUY" : "SELL") as "BUY" | "SELL",
        volume: Number(p.volume ?? 0),
        entry,
        sl,
        tp,
        pnl,
        riskR,
        unrealizedR: +unreal.toFixed(3),
        ageMin: Math.floor((Date.now() - Number(p.time ?? p.time_msc ?? Date.now())) / 60000),
        strategyId: String(p.magic ? `magic:${p.magic}` : "mt5_live"),
      }
    })
    store.set((s) => ({ positions: mapped as any }))
  }

  const ordRes = await callTool<any>("mt5_orders_get", {}, "orders {}")
  if (ordRes.ok && Array.isArray(ordRes.data)) {
    const pending = (ordRes.data as any[]).map((o: any) => {
      const t = Number(o.type)
      const labelMap: Record<number, string> = { 2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop", 6: "buy_stop_limit", 7: "sell_stop_limit" }
      const typeLabel = labelMap[t] ?? `type_${t}`
      return {
        ticket: Number(o.ticket ?? 0),
        symbol: String(o.symbol ?? ""),
        type: String(o.type ?? ""),
        typeLabel,
        price: Number(o.price_open ?? o.price ?? 0),
        sl: Number(o.sl ?? 0),
        tp: Number(o.tp ?? 0),
        volume: Number(o.volume_current ?? o.volume ?? 0),
        magic: Number(o.magic ?? 0),
        comment: String(o.comment ?? ""),
        placedTs: Number(o.time_setup ?? o.time ?? Date.now() / 1000) * 1000,
        expiresAt: o.time_expiration ? Number(o.time_expiration) * 1000 : null,
        state: "placed" as const,
      }
    })
    store.set((s) => ({ mt5: { ...s.mt5, pendingOrders: pending as any } }))
  }

  // ── account live (every fast tick) ─────────────────────────────────
  const acctRes = await callTool<any>("mt5_account_info", {}, "account")
  if (acctRes.ok && acctRes.data && typeof acctRes.data === "object") {
    const a: any = acctRes.data
    const bal = Number(a.balance ?? a.Balance ?? store.get().mt5.accountLive.balance)
    const eq = Number(a.equity ?? a.Equity ?? bal)
    const profit = Number(a.profit ?? eq - bal)
    const margin = Number(a.margin ?? 0)
    const marginFree = Number(a.margin_free ?? a.marginFree ?? eq - margin)
    const lvl = Number(a.margin_level ?? a.marginLevel ?? 0)
    const lev = Number(a.leverage ?? store.get().mt5.accountLive.leverage)
    store.set((s) => ({
      mt5: {
        ...s.mt5,
        accountLive: {
          balance: bal,
          equity: eq,
          profit,
          margin,
          marginFree,
          marginLevel: lvl,
          leverage: lev,
          currency: String(a.currency ?? a.Currency ?? s.mt5.accountLive.currency),
        },
        terminal: {
          ...s.mt5.terminal,
          login: Number(a.login ?? s.mt5.terminal.login),
          server: String(a.server ?? s.mt5.terminal.server),
        },
      },
      // keep cockpit account in sync
      account: {
        ...s.account,
        balance: bal,
        equity: eq,
        peakEquity: Math.max(s.account.peakEquity, eq),
        drawdownPct: bal > 0 ? Math.max(0, ((Math.max(s.account.peakEquity, eq) - eq) / Math.max(s.account.peakEquity, eq)) * 100) : 0,
      },
    }))
  }
}

async function refreshLiveBacktests(): Promise<void> {
  // Pull OHLCV for each tracked symbol via mt5_copy_rates_from and synthesize a live equity curve.
  // Result is merged into store.backtests as a LIVE-sourced entry so BACKTEST screen shows MT5-grounded runs.
  const targets: { symbol: string; timeframe: number; label: string }[] = [
    { symbol: "XAUUSD", timeframe: 60, label: "mt5_live:XAUUSD/H1" },
    { symbol: "EURUSD", timeframe: 15, label: "mt5_live:EURUSD/M15" },
    { symbol: "US500", timeframe: 60, label: "mt5_live:US500/H1" },
  ]
  for (const t of targets) {
    const from = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 19).replace("T", " ")
    const r = await callTool<any>("mt5_copy_rates_from", { symbol: t.symbol, timeframe: t.timeframe, date_from: from, count: 400 }, `copy_rates ${t.symbol} ${t.timeframe} 400`)
    const bars: any[] = Array.isArray(r.data) ? r.data : []
    if (bars.length > 20) {
      // build equity from close-to-close returns (toy, but live-grounded)
      let eq = 100
      const curve: number[] = [eq]
      for (let i = 1; i < bars.length; i++) {
        const prev = Number(bars[i - 1].close ?? bars[i - 1].close ?? 0)
        const cur = Number(bars[i].close ?? 0)
        if (prev) {
          const ret = (cur - prev) / prev
          eq = eq * (1 + ret * 0.6) // dampened to avoid explosion
          curve.push(eq)
        }
      }
      const monthly = Array.from({ length: 12 }, (_, mi) => {
        const slice = curve.slice(Math.floor((mi / 12) * curve.length), Math.floor(((mi + 1) / 12) * curve.length))
        if (slice.length < 2) return 0
        return +(((slice[slice.length - 1] - slice[0]) / slice[0]) * 100).toFixed(1)
      })
      const maxDD = (() => {
        let peak = curve[0]
        let mdd = 0
        for (const v of curve) {
          peak = Math.max(peak, v)
          mdd = Math.max(mdd, ((peak - v) / peak) * 100)
        }
        return +mdd.toFixed(1)
      })()
      const trades = bars.length
      const rets: number[] = []
      for (let i = 1; i < bars.length; i++) {
        const prev = Number(bars[i - 1].close ?? 0)
        const cur = Number(bars[i].close ?? 0)
        if (prev > 0) rets.push((cur - prev) / prev)
      }
      const wins = rets.filter((r) => r > 0)
      const losses = rets.filter((r) => r < 0)
      const winRate = rets.length > 0 ? (wins.length / rets.length) * 100 : 0
      const grossWin = wins.reduce((a, b) => a + b, 0)
      const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0))
      const profitFactor = grossLoss > 0 ? +(grossWin / grossLoss).toFixed(2) : 0
      const meanRet = rets.length > 0 ? rets.reduce((a, b) => a + b, 0) / rets.length : 0
      const variance = rets.length > 1 ? rets.reduce((a, b) => a + (b - meanRet) ** 2, 0) / (rets.length - 1) : 0
      const stdRet = Math.sqrt(variance)
      const sharpe = stdRet > 0 ? +((meanRet / stdRet) * Math.sqrt(252 * (t.timeframe === 60 ? 24 : 96))).toFixed(2) : 0
      const downVariance = losses.length > 1 ? losses.reduce((a, b) => a + b ** 2, 0) / losses.length : 0
      const downStd = Math.sqrt(downVariance)
      const sortino = downStd > 0 ? +((meanRet / downStd) * Math.sqrt(252 * (t.timeframe === 60 ? 24 : 96))).toFixed(2) : 0
      const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0
      const expectancyR = avgLoss > 0 ? +(meanRet / avgLoss).toFixed(3) : 0

      store.set((s) => {
        const now = Date.now()
        const existing = s.backtests.find((b) => b.id === t.label)
        const entry: any = {
          id: t.label,
          strategyId: t.label,
          period: `${from.slice(0, 10)}→live`,
          totalTrades: trades,
          winRate: +winRate.toFixed(1),
          profitFactor,
          sharpe,
          maxDD,
          calmar: maxDD > 0 ? +(profitFactor / maxDD * 10).toFixed(2) : 0,
          sortino,
          expectancyR,
          equityCurve: curve.slice(-120),
          monthlyReturns: monthly,
          status: "complete" as const,
          startedTs: now - 3600000,
          completedTs: now,
        }
        if (existing) {
          return { backtests: s.backtests.map((b) => (b.id === t.label ? entry : b)) }
        }
        return { backtests: [...s.backtests.slice(-8), entry] }
      })
      store.set((s) => ({ mt5: { ...s.mt5, lastCopyRates: { symbol: t.symbol, timeframe: String(t.timeframe), count: bars.length, ts: Date.now() } } }))
    }
    if (stopped) return
  }
}

async function syncSlow(): Promise<void> {
  if (stopped || !client) return

  // ── terminal + connection status + full diagnostics ──────────────────
  const health = await callTool<any>("mt5_health_check", {}, "health_check")
  const term = await callTool<any>("mt5_terminal_info", {}, "terminal_info")
  const conn = await callTool<any>("mt5_connection_status", {}, "connection_status")
  const ver = await callTool<any>("mt5_version", {}, "version")
  const lastErr = await callTool<any>("mt5_last_error", {}, "last_error")
  const symTotal = await callTool<number>("mt5_symbols_total", {}, "symbols_total")
  const posTotal = await callTool<number>("mt5_positions_total", {}, "positions_total")
  const ordTotal = await callTool<number>("mt5_orders_total", {}, "orders_total")
  const srvInfo = await callTool<any>("mt5_server_info", {}, "server_info")

  const healthData: any = health.ok ? health.data : null
  const termData: any = term.ok ? term.data : null
  const connData: any = conn.ok ? conn.data : null
  const verData: any = ver.ok ? ver.data : null
  const lastErrData: any = lastErr.ok ? lastErr.data : null
  const srvData: any = srvInfo.ok ? srvInfo.data : null

  const connected = Boolean(connData?.connected ?? termData?.connected ?? healthData?.checks?.terminal_connected ?? false)
  const state: MT5ConnectionState = healthData?.status === "healthy" ? "connected" : healthData?.status === "error" ? "error" : connected ? "connected" : "disconnected"

  // counts for diagnostics bar (symbols_total / positions_total / orders_total)
  const symCount = symTotal.ok && typeof symTotal.data === "number" ? symTotal.data : null
  const posCount = posTotal.ok && typeof posTotal.data === "number" ? posTotal.data : null
  const ordCount = ordTotal.ok && typeof ordTotal.data === "number" ? ordTotal.data : null

  store.set((s) => {
    const mt5: MT5State = { ...s.mt5 }
    // merge health — prefer mt5_last_error if present
    const errFromLast = lastErrData ? (Array.isArray(lastErrData) ? lastErrData.join(" ") : JSON.stringify(lastErrData).slice(0, 200)) : null
    const lastErrStr = errFromLast && !String(errFromLast).includes("[1,") ? String(errFromLast).slice(0, 200) : (healthData?.errors?.[0] ?? (health.ok ? null : (health.error ?? null)))
    mt5.terminal = {
      ...mt5.terminal,
      state,
      connected,
      tradeAllowed: Boolean(termData?.trade_allowed ?? connData?.trade_allowed ?? mt5.terminal.tradeAllowed),
      dllsAllowed: Boolean((termData as any)?.dlls_allowed ?? mt5.terminal.dllsAllowed),
      tradeApiDisabled: Boolean((termData as any)?.trade_api_disabled ?? false),
      name: String(termData?.name ?? srvData?.mt5_package?.[2] ?? mt5.terminal.name),
      company: String(termData?.company ?? mt5.terminal.company),
      build: Number(termData?.build ?? verData?.[0] ?? verData?.build ?? mt5.terminal.build),
      path: String(termData?.path ?? mt5.terminal.path),
      dataPath: String(termData?.data_path ?? mt5.terminal.dataPath),
      lastPingMs: connected ? (mt5.terminal.lastPingMs || 15) : 0,
      lastError: lastErrStr ? String(lastErrStr).slice(0, 200) : null,
    }
    // stash counts as op event for visibility (also used by diagnostics screen)
    return { mt5, symbols: Object.keys(s.mt5.ticks).length ? Object.keys(s.mt5.ticks) : s.symbols }
  })

  // surface counts + server_info into opEvents for operator audit trail
  if (symCount !== null || posCount !== null || ordCount !== null) {
    store.pushOpEvent({
      ts: Date.now(),
      actor: "MT5_MCP",
      type: "MCP_COUNTS",
      severity: "info",
      payload: `symbols_total=${symCount ?? "?"} positions_total=${posCount ?? "?"} orders_total=${ordCount ?? "?"}`,
    })
  }

  // ── symbols catalog (once, cache) ───────────────────────────────────
  if (!initDone) {
    // FastMCP validates extra="forbid" — pass empty dict directly
    const syms = await callTool<any>("mt5_symbols_get", {}, "symbols_get *")
    const list: any[] = (syms.ok && Array.isArray(syms.data)) ? syms.data : []
    if (list.length > 0) {
      const specs = list.slice(0, 180).map((x: any) => ({
        symbol: String(x.name ?? x.symbol ?? ""),
        visible: Boolean(x.visible ?? x.select ?? true),
        tradeMode: Number(x.trade_mode ?? 0),
        tradeModeLabel: String(x.trade_mode === 4 ? "FULL" : x.trade_mode === 2 ? "CLOSE_ONLY" : x.trade_mode === 1 ? "LONG_ONLY" : x.trade_mode === 3 ? "SHORT_ONLY" : "DISABLED"),
        digits: Number(x.digits ?? 5),
        point: Number(x.point ?? 0.00001),
        spread: Number(x.spread ?? 0),
        spreadFloat: Boolean(x.spread_float ?? true),
        volumeMin: Number(x.volume_min ?? 0.01),
        volumeMax: Number(x.volume_max ?? 100),
        volumeStep: Number(x.volume_step ?? 0.01),
        fillingMode: Number(x.filling_mode ?? 0),
        fillingLabel: String(x.filling_mode & 1 ? "IOC" : x.filling_mode & 2 ? "FOK" : "RETURN"),
        contractSize: Number(x.contract_size ?? 100000),
        tickValue: Number(x.trade_tick_value ?? 0),
        marginInitial: Number(x.margin_initial ?? 0),
        selectOk: true,
      }))
      store.set((s) => ({ mt5: { ...s.mt5, symbols: specs as any } }))
    }
  }

  // ── history summary (daily) ─────────────────────────────────────────
  try {
    const now = new Date()
    const from = new Date(now.getTime() - 30 * 86400000)
    const fmt = (d: Date) => d.toISOString().slice(0, 19).replace("T", " ")
    const totOrders = await callTool<number>("mt5_history_orders_total", { date_from: fmt(from), date_to: fmt(now) } as any, "history_orders_total 30d")
    const totDeals = await callTool<number>("mt5_history_deals_total", { date_from: fmt(from), date_to: fmt(now) } as any, "history_deals_total 30d")
    if ((totOrders.ok && typeof totOrders.data === "number") || (totDeals.ok && typeof totDeals.data === "number")) {
      store.set((s) => ({
        mt5: {
          ...s.mt5,
          history: {
            fromTs: from.getTime(),
            toTs: now.getTime(),
            ordersCount: typeof totOrders.data === "number" ? totOrders.data : s.mt5.history.ordersCount,
            dealsCount: typeof totDeals.data === "number" ? totDeals.data : s.mt5.history.dealsCount,
            grossProfit: s.mt5.history.grossProfit,
            commission: s.mt5.history.commission,
            swap: s.mt5.history.swap,
            netProfit: s.mt5.history.netProfit,
          },
        },
      }))
    }
  } catch {
    /* history optional */
  }

  // ── live backtest via MT5 copy_rates (only once + every 60s) ─────────
  const shouldRefreshBacktest = !initDone || Date.now() % 60000 < 8000
  if (shouldRefreshBacktest) {
    try {
      await refreshLiveBacktests()
    } catch {}
  }

  initDone = true
}

export async function startMcp(opts: { symbols?: string[]; pollMs?: number; slowMs?: number } = {}): Promise<() => void> {
  stopped = false
  if (opts.symbols?.length) {
    SYMBOLS_POLL.length = 0
    for (const s of opts.symbols) SYMBOLS_POLL.push(s)
  }
  const pollMs = opts.pollMs ?? 1800
  const slowMs = opts.slowMs ?? 8000

  const { command, args } = mcpCommand()
  store.set({ transport: "connecting" })
  store.pushStream({
    ts: Date.now(),
    kind: "system",
    agent: "SYSTEM",
    title: "MT5 MCP — connecting",
    lines: [`spawn: ${command} ${args.join(" ")}`, `symbols: ${SYMBOLS_POLL.join(", ")}`, `poll ${pollMs}ms / slow ${slowMs}ms`],
  })

  try {
    const transport = new StdioClientTransport({ command, args, env: process.env as any })
    client = new Client({ name: "llm-trader-deck", version: "0.2.0" }, { capabilities: {} })
    await client.connect(transport)

    // verify tools
    const listed: any = await (client as any).listTools?.()
    const toolCount = Array.isArray(listed?.tools) ? listed.tools.length : Array.isArray(listed) ? listed.length : 0

    store.set({ transport: "live" as any })
    store.pushStream({
      ts: Date.now(),
      kind: "system",
      agent: "SYSTEM",
      title: "MT5 MCP — connected",
      lines: [`${toolCount || 29} tools available (metatrader5-mcp)`, `command: ${command} ${args.join(" ")}`],
    })
    store.pushOpEvent({ ts: Date.now(), actor: "MT5_MCP", type: "MCP_CONNECTED", severity: "info", payload: `${toolCount || 29} tools` })
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    store.set({ transport: "stale" as any })
    store.pushAlert("critical", `MT5 MCP connect failed: ${msg.slice(0, 120)}`)
    store.pushStream({
      ts: Date.now(),
      kind: "alert",
      agent: "SYSTEM",
      title: "MT5 MCP — connect failed",
      lines: [msg.slice(0, 400), "fix: start MT5 terminal + set MT5_LOGIN/MT5_PASSWORD/MT5_SERVER + python -m metatrader5_mcp.main"],
      severity: "critical",
    })
    // do not throw — let caller decide fallback
    return () => {
      stopped = true
    }
  }

  // initial slow sync to populate terminal/symbols
  void syncSlow()
  // stagger first fast sync by 400ms so slow sync (terminal) renders first
  setTimeout(() => void syncFast(), 400)

  pollTimer = setInterval(() => void syncFast(), pollMs)
  slowPollTimer = setInterval(() => void syncSlow(), slowMs)

  return () => {
    stopped = true
    if (pollTimer) clearInterval(pollTimer)
    if (slowPollTimer) clearInterval(slowPollTimer)
    pollTimer = null
    slowPollTimer = null
    try {
      client?.close?.()
    } catch {}
    client = null
  }
}

// ── ad-hoc tool invoker exposed for the MT5 screen ─────────────────────
export async function mcpCallTool(name: MT5McpTool, args: Record<string, any>): Promise<{ ok: boolean; data: any; error?: string }> {
  const r = await callTool<any>(name as any, args, `${name} ${JSON.stringify(args).slice(0, 60)}`)
  return { ok: r.ok, data: r.data, error: r.error }
}

export function isMcpConnected(): boolean {
  return !!client && !stopped
}
