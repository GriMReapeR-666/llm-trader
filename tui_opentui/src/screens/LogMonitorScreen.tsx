import { useState, useCallback, useMemo } from "react"
import { useKeyboard } from "@opentui/react"
import { useDeck, store } from "../store"
import { T, SEPARATOR } from "../theme"
import type { LogEntry, LogFilter } from "../types"

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: T.dim,
  INFO: T.cyan,
  WARNING: T.warn,
  ERROR: T.down,
  CRITICAL: T.critical,
}

const LEVEL_PRIORITY: Record<string, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
}

const FILTER_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

export function LogMonitorScreen() {
  const logs = useDeck((s) => s.logs)
  const moduleGroups = useDeck((s) => s.moduleGroups)
  const [searchMode, setSearchMode] = useState(false)
  const [searchInput, setSearchInput] = useState("")
  const [historyIdx, setHistoryIdx] = useState(-1)
  const [history, setHistory] = useState<string[]>([])

  const { entries, filter, paused, totalLines, scrollOffset } = logs

  // Apply level filter
  const levelFiltered = useMemo(() => {
    const minPrio = LEVEL_PRIORITY[filter.level] ?? 2
    return entries.filter((e) => (LEVEL_PRIORITY[e.level] ?? 0) >= minPrio)
  }, [entries, filter.level])

  // Apply text/regex filter
  const filtered = useMemo(() => {
    if (!filter.query) return levelFiltered
    const q = filter.query
    if (filter.regex) {
      try {
        const flags = filter.caseSensitive ? "" : "i"
        const re = new RegExp(q, flags)
        return levelFiltered.filter((e) => {
          const haystack = `${e.ts} ${e.level} ${e.logger} ${e.message}`
          return re.test(haystack)
        })
      } catch {
        return []
      }
    }
    return levelFiltered.filter((e) => {
      const haystack = `${e.ts} ${e.level} ${e.logger} ${e.message}`
      return filter.caseSensitive
        ? haystack.includes(q)
        : haystack.toLowerCase().includes(q.toLowerCase())
    })
  }, [levelFiltered, filter.query, filter.regex, filter.caseSensitive])

  // Search highlights
  const matchSet = useMemo(() => {
    if (!filter.query) return new Set<number>()
    const s = new Set<number>()
    if (filter.regex) {
      try {
        const flags = filter.caseSensitive ? "" : "i"
        const re = new RegExp(filter.query, flags)
        filtered.forEach((e, i) => {
          if (re.test(`${e.ts} ${e.level} ${e.logger} ${e.message}`))
            s.add(i)
        })
      } catch { /* ignore */ }
    } else {
      filtered.forEach((e, i) => {
        const hay = `${e.ts} ${e.level} ${e.logger} ${e.message}`
        const match = filter.caseSensitive
          ? hay.includes(filter.query)
          : hay.toLowerCase().includes(filter.query.toLowerCase())
        if (match) s.add(i)
      })
    }
    return s
  }, [filtered, filter.query, filter.regex, filter.caseSensitive])

  const visibleCount = 30
  const start = Math.max(0, filtered.length - visibleCount - scrollOffset)
  const visible = filtered.slice(start, start + visibleCount)

  const cycleLevel = useCallback(() => {
    const cur = FILTER_LEVELS.indexOf(filter.level)
    const next = (cur + 1) % FILTER_LEVELS.length
    store.set((s) => ({
      logs: {
        ...s.logs,
        filter: { ...s.logs.filter, level: FILTER_LEVELS[next] },
      },
    }))
  }, [filter.level])

  const commitSearch = useCallback(() => {
    setSearchMode(false)
    const q = searchInput
    if (q && (!history.length || history[history.length - 1] !== q)) {
      const newHist = [...history, q].slice(-50)
      setHistory(newHist)
    }
    setHistoryIdx(-1)
    store.set((s) => ({
      logs: {
        ...s.logs,
        filter: { ...s.logs.filter, query: q },
        scrollOffset: 0,
      },
    }))
  }, [searchInput, history])

  const clearSearch = useCallback(() => {
    setSearchMode(false)
    setSearchInput("")
    setHistoryIdx(-1)
    store.set((s) => ({
      logs: {
        ...s.logs,
        filter: { ...s.logs.filter, query: "" },
      },
    }))
  }, [])

  const toggleRegex = useCallback(() => {
    store.set((s) => ({
      logs: {
        ...s.logs,
        filter: { ...s.logs.filter, regex: !s.logs.filter.regex },
      },
    }))
  }, [])

  useKeyboard((key) => {
    if (searchMode) {
      if (key.name === "escape") {
        clearSearch()
        return
      }
      if (key.name === "return" || key.name === "enter") {
        commitSearch()
        return
      }
      if (key.name === "backspace") {
        setSearchInput((p) => p.slice(0, -1))
        return
      }
      if (key.ctrl && key.name === "r") {
        toggleRegex()
        return
      }
      if (key.name === "up") {
        if (history.length && historyIdx < history.length - 1) {
          const newIdx = historyIdx + 1
          setHistoryIdx(newIdx)
          setSearchInput(history[history.length - 1 - newIdx])
        }
        return
      }
      if (key.name === "down") {
        if (historyIdx > 0) {
          const newIdx = historyIdx - 1
          setHistoryIdx(newIdx)
          setSearchInput(history[history.length - 1 - newIdx])
        } else if (historyIdx === 0) {
          setHistoryIdx(-1)
          setSearchInput("")
        }
        return
      }
      if (key.name === "space") {
        setSearchInput((p) => p + " ")
        return
      }
      if (key.name && key.name.length === 1 && !key.ctrl && !key.meta) {
        setSearchInput((p) => p + key.name)
        return
      }
      return
    }

    // Global keys (not in search mode)
    if (key.name === "/") {
      setSearchMode(true)
      setSearchInput("")
      setHistoryIdx(-1)
      return
    }
    if (key.name === "escape") {
      if (filter.query) {
        clearSearch()
      }
      return
    }
    if (key.name === "1") {
      store.set((s) => ({
        logs: { ...s.logs, filter: { ...s.logs.filter, level: "DEBUG" } },
      }))
      return
    }
    if (key.name === "2") {
      store.set((s) => ({
        logs: { ...s.logs, filter: { ...s.logs.filter, level: "INFO" } },
      }))
      return
    }
    if (key.name === "3") {
      store.set((s) => ({
        logs: { ...s.logs, filter: { ...s.logs.filter, level: "WARNING" } },
      }))
      return
    }
    if (key.name === "4") {
      store.set((s) => ({
        logs: { ...s.logs, filter: { ...s.logs.filter, level: "ERROR" } },
      }))
      return
    }
    if (key.name === "5") {
      store.set((s) => ({
        logs: { ...s.logs, filter: { ...s.logs.filter, level: "CRITICAL" } },
      }))
      return
    }
    if (key.name === "space") {
      store.set((s) => ({
        logs: { ...s.logs, paused: !s.logs.paused },
      }))
      return
    }
    if (key.name === "up" && !searchMode) {
      store.set((s) => ({
        logs: {
          ...s.logs,
          scrollOffset: Math.min(
            s.logs.scrollOffset + 3,
            Math.max(0, filtered.length - visibleCount)
          ),
          paused: true,
        },
      }))
      return
    }
    if (key.name === "down" && !searchMode) {
      store.set((s) => ({
        logs: {
          ...s.logs,
          scrollOffset: Math.max(0, s.logs.scrollOffset - 3),
          paused: s.logs.scrollOffset - 3 > 0,
        },
      }))
      return
    }
  })

  // Level stats
  const levelStats = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const e of entries) counts[e.level] = (counts[e.level] || 0) + 1
    return counts
  }, [entries])

  const modeTag = filter.regex ? "regex" : "text"

  return (
    <box flexDirection="column" width="100%" height="100%" gap={0}>
      {/* Search bar */}
      {searchMode && (
        <box
          flexDirection="row"
          height={1}
          paddingLeft={1}
          gap={0}
        >
          <text fg={T.warn}> /</text>
          <text fg={T.bright}>{searchInput}</text>
          <text fg={T.warn}>▌</text>
          <text fg={filter.regex ? T.purple : T.dim}>
            {" "}
            [{filter.regex ? "REGEX" : "text"}]
          </text>
          {searchInput && (
            <text fg={matchSet.size > 0 ? T.up : T.down}>
              {" "}
              {matchSet.size} match{matchSet.size !== 1 ? "es" : ""}
            </text>
          )}
          <text fg={T.dim}>
            {" "}
            Enter=ok Ctrl+R=regex Esc=cancel
          </text>
        </box>
      )}

      {/* Header row */}
      <box flexDirection="row" height={1} gap={0}>
        <text fg={T.bright}> LOGS </text>
        <text fg={T.dim}> | </text>
        <text fg={T.dim}>
          {totalLines} lines | {filtered.length} shown
        </text>
        {filter.query && (
          <text fg={T.warn}>
            {" | "}
            {modeTag}:"{filter.query}" ({matchSet.size})
          </text>
        )}
        {paused && <text fg={T.warn}> | PAUSED</text>}
      </box>

      {/* Main content */}
      <box flexDirection="row" flexGrow={1} gap={0}>
        {/* Sidebar: level stats */}
        <box flexDirection="column" width={18} flexShrink={0} gap={0}>
          <text fg={T.bright}> Levels</text>
          {FILTER_LEVELS.slice().reverse().map((level) => (
            <box
              key={level}
              flexDirection="row"
              height={1}
              gap={0}
            >
              <text
                fg={
                  filter.level === level
                    ? T.bright
                    : T.dim
                }
              >
                {filter.level === level ? "●" : "○"}{" "}
              </text>
              <text fg={LEVEL_COLORS[level]}>
                {level.padEnd(8)}
              </text>
              <text fg={T.dim}>
                {String(levelStats[level] || 0).padStart(4)}
              </text>
              <text fg={T.dim}> [{FILTER_LEVELS.indexOf(level) + 1}]</text>
            </box>
          ))}
          <text fg={T.dim}> </text>
          <text fg={T.dim}> Controls:</text>
          <text fg={T.dim}> / search</text>
          <text fg={T.dim}> 1-5 filter</text>
          <text fg={T.dim}> space pause</text>
          <text fg={T.dim}> up/down scroll</text>
          <text fg={T.borderBright}>{SEPARATOR.thin}</text>
          <text fg={T.bright}> Modules</text>
          {moduleGroups.map((m) => (
            <box key={m.name} flexDirection="row" height={1} gap={0}>
              <text fg={T.cyan}> {m.name.padEnd(14)}</text>
              <text fg={T.dim}>{String(m.count).padStart(5)}</text>
            </box>
          ))}
        </box>

        {/* Log feed */}
        <box flexDirection="column" flexGrow={1} paddingLeft={1} gap={0}>
          {visible.length === 0 ? (
            <text fg={T.dim}>
              {filter.query
                ? `No matches for "${filter.query}"`
                : "Waiting for log entries..."}
            </text>
          ) : (
            visible.map((entry, idx) => {
              const globalIdx = start + idx
              const isActive = filter.query && matchSet.has(globalIdx)
              const color = LEVEL_COLORS[entry.level] || T.fg

              return (
                <box key={entry.id} flexDirection="row" height={1} gap={0}>                  {/* Match marker */}
                  <text fg={isActive ? T.bright : T.dim}>
                    {isActive ? "➤" : " "}
                  </text>
                  {/* Timestamp */}
                  <text fg={T.dim}> {entry.ts.slice(11, 19)} </text>
                  {/* Level badge */}
                  <text fg={isActive ? T.bright : color}>
                    {" "}{entry.level.padEnd(8)}{" "}
                  </text>
                  {/* Logger */}
                  <text fg={T.cyan}>
                    {entry.logger.length > 20
                      ? entry.logger.slice(-20)
                      : entry.logger}{" "}
                  </text>
                  {/* Message with highlighting */}
                  {filter.query && isActive ? (
                    <HighlightedMessage
                      message={entry.message}
                      query={filter.query}
                      regex={filter.regex}
                      caseSensitive={filter.caseSensitive}
                    />
                  ) : (
                    <text fg={color}>{entry.message}</text>
                  )}
                </box>
              )
            })
          )}
        </box>
      </box>
    </box>
  )
}

function HighlightedMessage(props: {
  message: string
  query: string
  regex: boolean
  caseSensitive: boolean
}) {
  const { message, query, regex, caseSensitive } = props

  const spans = useMemo(() => {
    if (!query) return [{ start: 0, end: message.length, match: false }]
    const result: { start: number; end: number; match: boolean }[] = []

    if (regex) {
      try {
        const flags = caseSensitive ? "g" : "gi"
        const re = new RegExp(query, flags)
        let lastEnd = 0
        let m: RegExpExecArray | null
        while ((m = re.exec(message)) !== null) {
          if (m.index > lastEnd)
            result.push({ start: lastEnd, end: m.index, match: false })
          result.push({ start: m.index, end: m.index + m[0].length, match: true })
          lastEnd = m.index + m[0].length
          if (m.index === re.lastIndex) re.lastIndex++
        }
        if (lastEnd < message.length)
          result.push({ start: lastEnd, end: message.length, match: false })
      } catch {
        return [{ start: 0, end: message.length, match: false }]
      }
    } else {
      const hay = caseSensitive ? message : message.toLowerCase()
      const needle = caseSensitive ? query : query.toLowerCase()
      let lastEnd = 0
      let idx = 0
      while (true) {
        const pos = hay.indexOf(needle, idx)
        if (pos === -1) break
        if (pos > lastEnd)
          result.push({ start: lastEnd, end: pos, match: false })
        result.push({ start: pos, end: pos + needle.length, match: true })
        lastEnd = pos + needle.length
        idx = pos + needle.length
      }
      if (lastEnd < message.length)
        result.push({ start: lastEnd, end: message.length, match: false })
    }

    return result.length > 0
      ? result
      : [{ start: 0, end: message.length, match: false }]
  }, [message, query, regex, caseSensitive])

  return (
    <box flexDirection="row">
      {spans.map((span, i) => (
        <text
          key={i}
          fg={span.match ? T.bright : T.dim}
        >
          {message.slice(span.start, span.end)}
        </text>
      ))}
    </box>
  )
}
