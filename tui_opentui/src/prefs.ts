// Deck preference persistence — survives restarts.
//
// Layout choices that are pure operator taste (e.g. the CAUSAL DAG panel
// split) should not reset every time the deck restarts. We persist a small,
// versioned JSON file under the user's home directory:
//
//   ~/.quantnexus/deck-prefs.json      (override: QUANTNEXUS_PREFS_PATH)
//
// Reads and writes are best-effort: an unreadable/missing file yields
// defaults and a failed save never crashes the deck.

import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

export interface DeckPrefs {
  version: 1
  dagShare: number
}

// Single source of truth for the CAUSAL DAG split bounds (mirrors the
// hard clamps previously inline in store.ts, so they cannot drift).
export const DAG_SHARE_DEFAULT = 0.42
export const DAG_SHARE_MIN = 0.26
export const DAG_SHARE_MAX = 0.62

const SAVE_DEBOUNCE_MS = 250

export function prefsPath(): string {
  const override = process.env.QUANTNEXUS_PREFS_PATH
  if (override) return override
  return join(homedir(), ".quantnexus", "deck-prefs.json")
}

export function clampDagShare(share: number): number {
  return Math.min(DAG_SHARE_MAX, Math.max(DAG_SHARE_MIN, share))
}

/** Read prefs from disk. Missing/corrupt file → canonical defaults. */
export function loadDeckPrefs(): DeckPrefs {
  const prefs: DeckPrefs = { version: 1, dagShare: DAG_SHARE_DEFAULT }
  try {
    const raw = readFileSync(prefsPath(), "utf8")
    const parsed = JSON.parse(raw) as Partial<DeckPrefs>
    if (typeof parsed.dagShare === "number" && Number.isFinite(parsed.dagShare)) {
      prefs.dagShare = clampDagShare(parsed.dagShare)
    }
  } catch {
    // No prefs yet (first boot), unreadable, or malformed — defaults are fine.
  }
  return prefs
}

let pending: DeckPrefs | null = null
let timer: ReturnType<typeof setTimeout> | null = null

/** Schedule a save. Rapid nudges coalesce into one disk write. */
export function queuePrefsSave(prefs: DeckPrefs): void {
  pending = prefs
  if (timer) return
  timer = setTimeout(() => {
    timer = null
    writePrefs(pending)
  }, SAVE_DEBOUNCE_MS)
}

/** Immediately persist any pending prefs (e.g. on graceful exit). */
export function flushPrefsSave(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  if (pending) {
    writePrefs(pending)
    pending = null
  }
}

function writePrefs(prefs: DeckPrefs | null): void {
  if (!prefs) return
  try {
    const path = prefsPath()
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, `${JSON.stringify(prefs, null, 2)}\n`, "utf8")
  } catch {
    // Preferences are best-effort — never crash the deck over a save.
  }
}

/** Delete persisted prefs so the next boot starts from canonical defaults. */
export function resetDeckPrefs(): void {
  // Cancel any pending debounced save first, or it would resurrect the file.
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  pending = null
  try {
    rmSync(prefsPath(), { force: true })
  } catch {
    // Best-effort — nothing to crash over.
  }
}
