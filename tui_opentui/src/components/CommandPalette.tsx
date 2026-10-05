import { useState } from "react"
import { useDeck, store } from "../store"
import { T, SECTIONS, VIEWS, findSection } from "../theme"
import type { ViewId } from "../theme"

export interface Command {
  name: string
  description: string
  danger?: boolean
  run: () => void
}

function go(view: ViewId): () => void {
  return () => store.set({ view })
}

export function executeCommand(raw: string): void {
  const [cmd, ...rest] = raw.trim().replace(/^\//, "").split(/\s+/)
  const cleanCmd = cmd.toLowerCase()
  const arg = rest.join(" ")

  // Check all known screens first — but only when the command is not a
  // subcommand (e.g. "/stream pause" must reach the stream switch below,
  // not the view-jump for the "stream" screen)
  const hasArgs = arg.length > 0
  if (!(cleanCmd === "stream" && hasArgs)) {
    const targetView = VIEWS.find(
      (v) => v.id.toLowerCase() === cleanCmd || v.label.toLowerCase() === cleanCmd,
    )?.id
    if (targetView) {
      store.set({ view: targetView })
      return
    }
  }

  // Check section jump
  const targetSection = SECTIONS.find(
    (s) => s.id.toLowerCase() === cleanCmd || s.label.toLowerCase() === cleanCmd,
  )
  if (targetSection && targetSection.views[0]) {
    store.set({ view: targetSection.views[0].id })
    return
  }

  switch (cleanCmd) {
    case "mcp":
      store.set({ view: "mt5" })
      break
    case "incidents": {
      // P7: jump straight to the INCIDENTS pane on the health screen.
      store.set({ view: "health" })
      const inc = store.get().systemHealth.incidents
      const worst = inc.cards[inc.selected]
      store.pushStream({
        ts: Date.now(), kind: "system", agent: "SYSTEM",
        title: `incidents: ${inc.status.toLowerCase()} · ${inc.cards.length} active`,
        lines: inc.cards.length === 0
          ? ["✓ no active incidents — durable health journal is clean"]
          : inc.cards.map((c) => {
              const sev = c.severity === "critical" ? "⛔" : c.severity === "warn" ? "⚠" : "ℹ"
              return `${sev} ${c.code}${c.count > 1 ? ` ×${c.count}` : ""} — ${c.first_response}`
            }).concat(worst ? ["full runbook: SYSTEM HEALTH → INCIDENTS pane"] : []),
      })
      break
    }
    case "pause":
      store.set({ confirm: { action: "pause", label: "PAUSE new cycles (positions keep managed)" } })
      break
    case "flatten":
      store.set({ confirm: { action: "flatten", label: "FLATTEN all positions at market" } })
      break
    case "kill":
    case "killswitch":
      store.set({ confirm: { action: "kill", label: "ENGAGE KILLSWITCH — permanent halt" } })
      break
    case "stop":
      store.set({ confirm: { action: "stop", label: "STOP the trading engine process (open positions stay with the broker)" } })
      break
    case "resume":
      store.set({ halted: false })
      store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "cycles resumed", lines: [] })
      break
    case "cycle": {
      const state = store.get()
      if (state.transport === "live") {
        import("../transport/live").then((m) => m.sendControl("cycle")).catch(() => {})
        store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "manual cycle requested", lines: ["sent to live engine :8000"] })
      } else {
        store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "cycle requires live transport", lines: ["connect to live engine (:8000) — no synthetic preview exists"] })
      }
      break
    }
    case "note":
      if (arg) store.pushStream({ ts: Date.now(), kind: "user", agent: "OPERATOR", title: arg, lines: [arg] })
      break
    case "stream": {
      const [sub, ...subRest] = arg.split(/\s+/)
      const a = (sub ?? "").toLowerCase()
      const val = subRest.join(" ")
      switch (a) {
        case "":
          store.set({ view: "stream" })
          break
        case "pause":
        case "freeze":
          store.setStreamFollow(false)
          store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "stream frozen — new events queued behind badge", lines: [] })
          break
        case "live":
        case "follow":
        case "resume":
          store.setStreamFollow(true)
          store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "stream live — following newest events", lines: [] })
          break
        case "clear":
          store.clearStream()
          break
        case "all":
          store.setStreamKinds([])
          store.setStreamAgent("")
          store.setStreamQuery("")
          store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "stream filters cleared — showing all events", lines: [] })
          break
        case "kind":
        case "only": {
          if (!val) {
            store.pushStream({
              ts: Date.now(), kind: "system", agent: "SYSTEM", title: "stream filter usage",
              lines: ["/stream kind <system|agent_note|tool|thinking|decision|order|validation|fill|alert|user> · toggles a kind", "/stream all · reset"],
            })
            break
          }
          store.toggleStreamKind(val.toLowerCase() as import("../types").StreamKind)
          break
        }
        case "agent": {
          const agents = [...Object.keys(store.get().agents), "SYSTEM", "OPERATOR"]
          const current = store.get().streamAgent
          if (val) {
            store.setStreamAgent(val)
          } else if (!current) {
            store.setStreamAgent(agents[0] ?? "")
          } else {
            const idx = agents.indexOf(current)
            store.setStreamAgent(agents[(idx + 1) % agents.length] ?? "")
          }
          break
        }
        case "q":
        case "search":
          store.setStreamQuery(val)
          break
        case "expand":
        case "open":
          store.expandAllStreamItems()
          store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "stream — every event expanded", lines: [] })
          break
        case "fold":
        case "collapse":
          store.foldAllStreamItems()
          store.pushStream({ ts: Date.now(), kind: "system", agent: "OPERATOR", title: "stream — long events folded (click ▸ on a card to expand one)", lines: [] })
          break
        default:
          store.pushStream({
            ts: Date.now(), kind: "system", agent: "SYSTEM", title: `unknown stream option: ${a}`,
            lines: ["/stream pause|live|clear|all|kind <kind>|agent [name]|q <text>|expand|fold"],
          })
      }
      break
    }
    case "prefs": {
      const [sub] = arg.split(/\s+/)
      const a = (sub ?? "").toLowerCase()
      if (a === "reset") {
        store.resetPrefs()
        store.pushStream({
          ts: Date.now(),
          kind: "system",
          agent: "OPERATOR",
          title: "prefs reset — layout back to defaults",
          lines: ["~/.quantnexus/deck-prefs.json cleared · DAG split restored to default (next boot is clean)"],
        })
        store.pushOpEvent({ ts: Date.now(), actor: "OPERATOR", type: "PREFS_RESET", severity: "info", payload: "deck preferences cleared to defaults" })
      } else {
        store.pushStream({
          ts: Date.now(),
          kind: "system",
          agent: "SYSTEM",
          title: "prefs usage",
          lines: ["/prefs reset · clear persisted layout preferences (~/.quantnexus/deck-prefs.json)"],
        })
      }
      break
    }
    case "help":
      store.pushStream({
        ts: Date.now(),
        kind: "system",
        agent: "SYSTEM",
        title: "commands",
        lines: [
          "/market /analysis /pipeline /research /control /system · jump to section",
          "/feed /cockpit /brain /causal /macro /positions /journal /tasks ...",
          "/stream pause|live · freeze / follow the REPL feed",
          "/stream kind <kind> · toggle a feed filter (decision, order, alert …)",
          "/stream q <text> · search feed · /stream all · reset",
          "/stream expand|fold · expand / collapse every event card (▸ chip toggles one)",
          "/cycle · force one intelligence cycle now",
          "/incidents · active incidents + runbook first responses",
          "/pause /resume /flatten /kill · intervention ladder",
          "/prefs reset · clear persisted layout prefs to defaults",
          "/note <text> · operator note into the stream",
        ],
      })
      break
    default:
      store.pushStream({
        ts: Date.now(),
        kind: "system",
        agent: "SYSTEM",
        title: `unknown command: ${raw}`,
        lines: ["try /help"],
      })
  }
}

const SECTION_COMMANDS: Command[] = SECTIONS.map((s) => ({
  name: `/${s.label.toLowerCase()}`,
  description: `Section: ${s.label} (${s.views.length} screens)`,
  run: () => {
    const first = s.views[0]?.id
    if (first) store.set({ view: first })
  },
}))

const SCREEN_COMMANDS: Command[] = VIEWS.map((v) => ({
  name: `/${v.id}`,
  description: `${v.label} screen [${findSection(v.id)?.label ?? ""}]`,
  run: go(v.id),
}))

const COMMANDS: Command[] = [
  { name: "/help", description: "show command reference", run: () => executeCommand("/help") },
  ...SECTION_COMMANDS,
  ...SCREEN_COMMANDS,
  { name: "/stream pause", description: "freeze feed (keep reading history)", run: () => executeCommand("/stream pause") },
  { name: "/stream live", description: "resume live follow", run: () => executeCommand("/stream live") },
  { name: "/stream clear", description: "clear feed history", danger: true, run: () => executeCommand("/stream clear") },
  { name: "/stream all", description: "reset all feed filters", run: () => executeCommand("/stream all") },
  { name: "/stream q <text>", description: "search events by text", run: () => executeCommand("/stream q") },
  { name: "/stream agent", description: "cycle agent filter", run: () => executeCommand("/stream agent") },
  { name: "/stream expand", description: "expand every event card", run: () => executeCommand("/stream expand") },
  { name: "/stream fold", description: "re-fold long event cards", run: () => executeCommand("/stream fold") },
  { name: "/cycle", description: "force one intelligence cycle", run: () => executeCommand("/cycle") },
  { name: "/incidents", description: "active incidents + runbook first responses", run: () => executeCommand("/incidents") },
  { name: "/pause", description: "stop new cycles", danger: true, run: () => executeCommand("/pause") },
  { name: "/flatten", description: "close all positions", danger: true, run: () => executeCommand("/flatten") },
  { name: "/kill", description: "permanent halt", danger: true, run: () => executeCommand("/kill") },
  { name: "/stop", description: "stop the trading engine process", danger: true, run: () => executeCommand("/stop") },
  { name: "/prefs reset", description: "clear persisted layout prefs (DAG split → default)", danger: true, run: () => executeCommand("/prefs reset") },
]

export function CommandPalette(props: { onClose: () => void }) {
  const [query, setQuery] = useState("")
  const filtered = COMMANDS.filter(
    (c) =>
      c.name.toLowerCase().includes(query.replace(/^\//, "").toLowerCase()) ||
      c.description.toLowerCase().includes(query.replace(/^\//, "").toLowerCase()),
  )

  const options = filtered.map((c) => ({
    name: c.danger ? `⚠ ${c.name}` : c.name,
    description: c.description,
    value: c.name,
  }))

  return (
    <box
      position="absolute"
      top="18%"
      left="25%"
      width="50%"
      height={14}
      borderStyle="rounded"
      borderColor={T.cyan}
      backgroundColor={T.panel}
      title=" COMMAND PALETTE "
      titleColor={T.cyan}
      flexDirection="column"
    >
      <input
        placeholder="type a command…"
        focused
        onInput={(v) => setQuery(v)}
        onSubmit={() => {
          const first = filtered[0]
          props.onClose()
          if (first) first.run()
        }}
        backgroundColor={T.raised}
        focusedBackgroundColor={T.raised}
        textColor={T.bright}
        cursorColor={T.cyan}
        marginLeft={1}
        marginRight={1}
      />
      {options.length > 0 ? (
        <select
          flexGrow={1}
          focused={false}
          options={options.slice(0, 8)}
          showDescription
          showScrollIndicator={false}
          textColor={T.fg}
          descriptionColor={T.dim}
          selectedBackgroundColor={T.borderBright}
          selectedTextColor={T.bright}
          onSelect={(_i, opt) => {
            props.onClose()
            const c = COMMANDS.find((x) => x.name === (opt?.value ?? opt?.name))
            if (c) c.run()
          }}
        />
      ) : (
        <box paddingLeft={1}>
          <text fg={T.dim}>no match</text>
        </box>
      )}
    </box>
  )
}

export function renderControlOutcome(ctrl: {
  status: string
  result?: {
    closed?: number
    cancelled?: number
    failed?: number
    remaining?: number
    sweeps?: number
    error?: string
    [key: string]: any
  }
}): string {
  if (ctrl.status === "APPLIED") {
    const r = ctrl.result || {}
    const parts: string[] = []
    if (typeof r.closed === "number") parts.push(`${r.closed} closed`)
    if (typeof r.cancelled === "number") parts.push(`${r.cancelled} cancelled`)
    if (typeof r.failed === "number" && r.failed > 0) parts.push(`${r.failed} failed`)
    const details = parts.length > 0 ? ` (${parts.join(", ")})` : ""
    return `APPLIED${details}`
  }
  if (ctrl.status === "FAILED") {
    return `FAILED (${ctrl.result?.error || "unknown error"})`
  }
  if (ctrl.status === "EXPIRED") {
    return "EXPIRED"
  }
  return ctrl.status
}

export function ConfirmDialog() {
  const confirm = useDeck((s) => s.confirm)
  const [typedConfirm, setTypedConfirm] = useState("")
  if (!confirm) return null
  const danger = confirm.action !== "pause"
  const isTypedAction = confirm.action === "flatten" || confirm.action === "kill"
  const requiredToken = confirm.action === "flatten" ? "FLATTEN" : confirm.action === "kill" ? "KILL" : ""

  return (
    <box
      position="absolute"
      top="32%"
      left="26%"
      width="48%"
      height={isTypedAction ? 12 : 9}
      borderStyle="single"
      borderColor={danger ? T.down : T.warn}
      backgroundColor={T.panel}
      flexDirection="column"
      padding={1}
    >
      <text fg={danger ? T.down : T.warn}>{danger ? "⚠ CONFIRM INTERVENTION" : "CONFIRM"}</text>
      <text fg={T.bright}>{confirm.label}</text>
      {isTypedAction && (
        <box flexDirection="column" marginTop={1}>
          <text fg={T.dim}>Type {requiredToken} to confirm, or press Esc / select CANCEL:</text>
          <input
            placeholder={`type ${requiredToken}…`}
            focused
            value={typedConfirm}
            onInput={(v) => setTypedConfirm(v)}
            onSubmit={() => {
              if (typedConfirm.trim() === requiredToken) {
                store.set({ confirm: null })
                applyIntervention(confirm.action, requiredToken)
              }
            }}
            backgroundColor={T.raised}
            focusedBackgroundColor={T.raised}
            textColor={T.bright}
            cursorColor={T.cyan}
          />
        </box>
      )}
      <box flexDirection="row" marginTop={1} gap={2}>
        <select
          width="40%"
          height={3}
          focused={!isTypedAction}
          options={[
            { name: "CANCEL", description: "", value: "cancel" },
            { name: danger ? "CONFIRM ⚠" : "CONFIRM", description: "", value: "go" },
          ]}
          showDescription={false}
          showScrollIndicator={false}
          selectedBackgroundColor={danger ? T.down : T.warn}
          selectedTextColor={danger ? T.bg : T.bg}
          onSelect={(_i, opt) => {
            const v = String(opt?.value ?? "cancel")
            store.set({ confirm: null })
            if (v !== "go") return
            if (isTypedAction && typedConfirm.trim() !== requiredToken) {
              store.pushAlert("warn", `Intervention cancelled: must type ${requiredToken}`)
              return
            }
            applyIntervention(confirm.action, isTypedAction ? requiredToken : undefined)
          }}
        />
      </box>
    </box>
  )
}

export async function applyIntervention(
  action: "pause" | "flatten" | "kill" | "stop",
  confirmToken?: string,
): Promise<void> {
  const live = store.get().transport === "live"
  if (!live) {
    store.pushAlert("critical", "NOT SENT: deck offline")
    return
  }

  const { sendControl, getControlStatus } = await import("../transport/live")
  const reqAction = action === "kill" ? "killswitch" : action
  const payload: Record<string, any> = {}
  if (confirmToken) {
    payload.confirm = confirmToken
  }

  const res = await sendControl(reqAction, payload)
  if (!res.ok) {
    store.pushAlert("critical", `${action.toUpperCase()} FAILED — ${res.reason || "control channel refused"}`)
    return
  }

  const controlId = res.control_id
  // A control without a journal id was never issued: FAILED, never poll.
  if (controlId === undefined || controlId === null) {
    store.pushAlert("critical", `${action.toUpperCase()} FAILED — no control_id (engine journal unavailable)`)
    return
  }
  let currentStatus = res.status || "REQUESTED"
  store.pushAlert("warn", `${action.toUpperCase()} ${currentStatus}`)

  if (controlId) {
    let attempts = 0
    const pollInterval = 400
    // Poll until a terminal state (APPLIED/FAILED/EXPIRED). Cap at ~72 s,
    // past the 60 s control expiry: on cap, report presumed-EXPIRED loudly
    // instead of going silent.
    const maxAttempts = 180
    const intervalId = setInterval(async () => {
      attempts++
      const stat = await getControlStatus(controlId)
      if (!stat) {
        if (attempts >= maxAttempts) {
          clearInterval(intervalId)
          store.pushAlert("critical", `${action.toUpperCase()} FAILED — no terminal state after 72 s (treat as EXPIRED, verify via journal)`)
        }
        return
      }
      if (stat.status !== currentStatus) {
        currentStatus = stat.status
        const rendered = renderControlOutcome(stat)
        store.pushAlert(
          stat.status === "FAILED" || stat.status === "EXPIRED" ? "critical" : "warn",
          `${action.toUpperCase()} ${rendered}`,
        )
      }
      if (stat.status === "APPLIED" || stat.status === "FAILED" || stat.status === "EXPIRED" || attempts >= maxAttempts) {
        clearInterval(intervalId)
        if (attempts >= maxAttempts && stat.status !== "APPLIED" && stat.status !== "FAILED" && stat.status !== "EXPIRED") {
          store.pushAlert("critical", `${action.toUpperCase()} FAILED — no terminal state after 72 s (treat as EXPIRED, verify via journal)`)
        }
        // Only reflect halted locally once the engine confirms the outcome.
        if (stat.status === "APPLIED" && (action === "kill" || action === "pause")) {
          store.set({ halted: true })
        }
        const rendered = renderControlOutcome(stat)
        store.pushOpEvent({
          ts: Date.now(),
          actor: "OPERATOR",
          type: action.toUpperCase(),
          severity: stat.status === "APPLIED" ? (action === "pause" ? "warn" : "critical") : "critical",
          payload: rendered,
        })
      }
    }, pollInterval)
  }
}
