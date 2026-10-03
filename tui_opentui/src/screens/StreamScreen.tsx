import { useEffect, useMemo, useRef, useState } from "react"
import { useDeck, store } from "../store"
import { T } from "../theme"
import { StreamItemView, streamItemExpanded, KIND_META, KIND_ORDER } from "../components/cards"
import type { StreamItem, StreamKind } from "../types"
import { executeCommand } from "../components/CommandPalette"
import { useContentWidth, RES } from "../layout"

const KIND_LABEL: Record<StreamKind, string> = Object.fromEntries(
  KIND_ORDER.map((k) => [k, KIND_META[k].label]),
) as Record<StreamKind, string>

// Small component that subscribes to the fast tick so the whole feed does not
// re-render on every animation frame — only this pulse dot does.
function LiveDot(props: { active: boolean }) {
  const tick = useDeck((s) => s.tick)
  const on = Math.floor(tick / 2) % 2 === 0
  if (!props.active) {
    return <text fg={T.warn}>⏸</text>
  }
  return <text fg={on ? T.teal : T.dim}>{on ? "● LIVE" : "○ LIVE"}</text>
}

function Chip(props: {
  active?: boolean
  color?: string
  label: string
  onMouseDown?: () => void
  hint?: string
}) {
  const active = props.active ?? false
  return (
    <box
      onMouseDown={props.onMouseDown}
      backgroundColor={active ? T.borderBright : T.panel}
      paddingLeft={1}
      paddingRight={1}
    >
      <text fg={active ? T.bright : (props.color ?? T.dim)}>
        {props.label}
      </text>
    </box>
  )
}

export function StreamScreen() {
  const stream = useDeck((s) => s.stream)
  const follow = useDeck((s) => s.streamFollow)
  const kinds = useDeck((s) => s.streamKinds)
  const agentFilter = useDeck((s) => s.streamAgent)
  const query = useDeck((s) => s.streamQuery)
  const unseen = useDeck((s) => s.streamUnseen)
  const expandedMap = useDeck((s) => s.streamExpanded)
  const promptFocused = useDeck((s) => s.promptFocused)
  const [draft, setDraft] = useState("")
  const scrollRef = useRef<any>(null)
  // measured feed width: content area minus scrollbar reserve minus the
  // scrollbox's own padding (2+2) — cards hard-wrap reasoning/body to this so
  // long lines never bleed across panel borders. The floor is clamped to the
  // measured width so a narrow terminal can never hand cards a budget wider
  // than the real inner columns.
  const contentW = useContentWidth()
  const feedWidth = Math.max(Math.min(56, contentW), contentW - RES - 4)

  // Leaving the REPL always returns the deck to live-follow on the way back.
  useEffect(() => {
    return () => {
      store.setStreamFollow(true)
    }
  }, [])

  const counts = useMemo(() => {
    const c: Partial<Record<StreamKind, number>> = {}
    for (const it of stream) c[it.kind] = (c[it.kind] ?? 0) + 1
    return c
  }, [stream])

  const presentKinds = KIND_ORDER.filter((k) => (counts[k] ?? 0) > 0)
  const q = query.trim().toLowerCase()

  const visible = useMemo(() => {
    const res: StreamItem[] = []
    for (const it of stream) {
      if (kinds.length > 0 && !kinds.includes(it.kind)) continue
      if (agentFilter && it.agent !== agentFilter) continue
      if (q) {
        const hay = (it.title + " " + it.lines.join(" ")).toLowerCase()
        if (!hay.includes(q)) continue
      }
      res.push(it)
    }
    return res
  }, [stream, kinds, agentFilter, q])

  const agentsPresent = useMemo(() => {
    const set = new Set<string>()
    for (const it of stream) set.add(it.agent)
    return [...set]
  }, [stream])

  const cycleAgent = () => {
    const list = agentsPresent
    if (list.length === 0) return
    const cur = store.get().streamAgent
    if (!cur) store.setStreamAgent(list[0])
    else {
      const idx = list.indexOf(cur)
      store.setStreamAgent(list[(idx + 1) % list.length])
    }
  }

  const suggestions = draft.startsWith("/")
    ? [
        "stream live", "stream pause", "stream clear", "stream all", "stream agent", "stream q ", "stream expand", "stream fold",
        "help", "cycle", "pause", "resume", "flatten", "kill",
        "cockpit", "brain", "causal", "macro", "positions", "tasks", "strategies", "analytics", "journal", "map",
      ].filter((c) => ("/" + c).startsWith(draft.toLowerCase()))
    : []

  const headerHint = follow
    ? "p pause · c clear · a agents · e expand newest"
    : "p resume live · c clear · a agents · e expand newest · ⤓ unseen"

  return (
    <box flexDirection="column" flexGrow={1} gap={0}>
      {/* ── REPL toolbar: live state + counts ─────────────────────────── */}
      <box flexDirection="row" gap={1} alignItems="center" paddingLeft={1} paddingRight={1} flexShrink={0}>
        <box
          onMouseDown={() => store.setStreamFollow(!store.get().streamFollow)}
          backgroundColor={T.panel}
          paddingLeft={1}
          paddingRight={1}
        >
          <LiveDot active={follow} />
        </box>
        {!follow && unseen > 0 && (
          <box
            onMouseDown={() => store.setStreamFollow(true)}
            backgroundColor={T.raised}
            paddingLeft={1}
            paddingRight={1}
          >
            <text fg={T.cyan}>⤓ {unseen} new</text>
          </box>
        )}
        <text fg={T.muted}>
          feed · {visible.length}/{stream.length} shown
        </text>
        <text fg={T.dim}>{headerHint}</text>
      </box>

      {/* ── REPL toolbar: kind / agent / query chips ──────────────────── */}
      <box flexDirection="row" gap={1} alignItems="center" paddingLeft={2} paddingRight={2} flexShrink={0} marginTop={1}>
        <text fg={T.dim}>filter</text>
        <Chip
          active={kinds.length === 0 && !agentFilter && !q}
          label="ALL"
          color={T.cyan}
          onMouseDown={() => {
            store.setStreamKinds([])
            store.setStreamAgent("")
            store.setStreamQuery("")
          }}
        />
        {presentKinds.map((k) => (
          <Chip
            key={k}
            active={kinds.includes(k) && !agentFilter && !q}
            label={`${KIND_LABEL[k]} ${counts[k]}`}
            color={KIND_META[k].color}
            onMouseDown={() => store.toggleStreamKind(k)}
          />
        ))}
        <Chip
          active={!!agentFilter}
          label={agentFilter ? `AGENT:${agentFilter}` : "AGENT"}
          color={T.purple}
          onMouseDown={cycleAgent}
        />
        {query && (
          <Chip
            active
            label={`q:"${query}" ✕`}
            color={T.warn}
            onMouseDown={() => store.setStreamQuery("")}
          />
        )}
        <Chip label="CLEAR" color={T.down} onMouseDown={() => store.clearStream()} />
      </box>

      {/* ── REPL feed ─────────────────────────────────────────────────── */}
      <scrollbox
        ref={scrollRef}
        flexGrow={1}
        stickyScroll={follow}
        stickyStart="bottom"
        viewportCulling
        scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}
        paddingLeft={2}
        paddingRight={2}
      >
        <box flexDirection="row" gap={1} marginBottom={1}>
          <text fg={T.muted}>Feed</text>
          <text fg={T.dim}>
            {stream.length} events · {follow ? "live" : "frozen"}
            {kinds.length > 0 ? ` · kinds: ${kinds.map((k) => KIND_LABEL[k]).join(",")}` : ""}
            {agentFilter ? ` · agent: ${agentFilter}` : ""}
          </text>
        </box>
        {visible.length === 0 && stream.length > 0 && (
          <text fg={T.dim}>— no events match the current filters —</text>
        )}
        {visible.map((item) => (
          <StreamItemView
            key={item.id}
            item={item}
            expanded={streamItemExpanded(item, expandedMap)}
            onToggle={() => store.toggleStreamExpanded(item.id)}
            width={feedWidth}
          />
        ))}
        <text fg={T.bg}> </text>
      </scrollbox>

      {suggestions.length > 0 && (
        <box flexDirection="row" paddingLeft={2} gap={1} flexShrink={0}>
          <text fg={T.neon}>▸</text>
          <text fg={T.dim}>suggestions:</text>
          {suggestions.slice(0, 6).map((s) => (
            <text key={s} fg={T.cyan}>
              /{s}
            </text>
          ))}
        </box>
      )}

      <box
        flexDirection="row"
        backgroundColor={T.panel}
        paddingLeft={2}
        paddingRight={2}
        marginLeft={2}
        marginRight={2}
        marginTop={0}
        marginBottom={0}
        flexShrink={0}
        borderStyle="single"
        borderColor={promptFocused ? T.cyan : T.border}
      >
        <text fg={T.muted}>› </text>
        <input
          flexGrow={1}
          focused={promptFocused}
          placeholder={promptFocused ? "command or note… (/ for palette, /stream for feed ops)" : "press i to command"}
          placeholderColor={T.borderBright}
          onInput={(v) => setDraft(typeof v === "string" ? v : "")}
          onSubmit={(v) => {
            const text = (typeof v === "string" ? v : "").trim()
            if (!text) return
            store.pushStream({ ts: Date.now(), kind: "user", agent: "OPERATOR", title: text, lines: [text] })
            if (text.startsWith("/")) executeCommand(text)
            setDraft("")
          }}
          backgroundColor={T.panel}
          focusedBackgroundColor={T.panel}
          textColor={T.bright}
          cursorColor={T.cyan}
        />
      </box>
    </box>
  )
}
