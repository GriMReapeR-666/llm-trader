import { useDeck, store } from "../store"
import { T } from "../theme"
import { fmtClock } from "../format"
import type { AutonomyLevelId } from "../types"

const LEVEL_COLOR: Record<AutonomyLevelId, string> = {
  L0_SHADOW: T.dim,
  L1_PAPER: T.cyan,
  L2_MICRO: T.warn,
  L3_FULL: T.up,
}

const LEVEL_DESC: Record<AutonomyLevelId, string> = {
  L0_SHADOW: "Observe only. No trades executed. Data collection + analysis.",
  L1_PAPER: "Paper trading. Real broker demo execution for strategy validation.",
  L2_MICRO: "Micro-live. Tiny positions, tight risk, operator-signed manifests.",
  L3_FULL: "Full autonomy. All strategies active, max position sizing.",
}

export function autonomyMove(delta: number): void {
  const s = store.get()
  const items = s.autonomyControl.actions
  if (items.length === 0) return
  store.set({ autonomyControl: { ...s.autonomyControl, actions: items } })
}

export function AutonomyScreen() {
  const ac = useDeck((s) => s.autonomyControl)
  const levels: AutonomyLevelId[] = ["L0_SHADOW", "L1_PAPER", "L2_MICRO", "L3_FULL"]

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" AUTONOMY LEVELS " titleColor={T.dim} borderStyle="single" borderColor={T.border} flexDirection="column" width="30%" paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling>
          {levels.map((lvl) => {
            const active = lvl === ac.currentLevel
            const halted = ac.halted && lvl === ac.currentLevel
            return (
              <box key={lvl} flexDirection="column" paddingLeft={1} backgroundColor={active ? T.raised : undefined}>
                <box flexDirection="row" gap={1}>
                  <text fg={active ? LEVEL_COLOR[lvl] : T.dim}>{active ? "▸" : " "}</text>
                  <text fg={active ? T.bright : T.muted}>{lvl}</text>
                  {active && <text fg={T.up}> ●</text>}
                  {halted && <text fg={T.down}> ⏸</text>}
                </box>
                <text fg={T.dim} paddingLeft={2}>{LEVEL_DESC[lvl]}</text>
              </box>
            )
          })}
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>HALT STATE</text>
          <Row label="halted" value={ac.halted ? "YES" : "no"} color={ac.halted ? T.down : T.up} />
          <Row label="paused" value={ac.paused ? "YES" : "no"} color={ac.paused ? T.warn : T.up} />
          <Row label="permanent" value={ac.permanentHalt ? "YES" : "no"} color={ac.permanentHalt ? T.critical : T.up} />
          {ac.haltReason && <Row label="halt reason" value={ac.haltReason} color={T.down} />}
          {ac.pauseReason && <Row label="pause reason" value={ac.pauseReason} color={T.warn} />}
          {ac.flattenReason && <Row label="flatten" value={ac.flattenReason} color={T.critical} />}
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title=" ROLLOUT GATE " titleColor={T.warn} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <Row label="status" value={ac.rolloutGate.status} color={ac.rolloutGate.status === "active" ? T.up : ac.rolloutGate.status === "pending" ? T.warn : T.dim} />
          <Row label="target" value={ac.rolloutGate.targetLevel ?? "—"} color={T.muted} />
          <Row label="manifest" value={ac.rolloutGate.manifestHash?.slice(0, 16) ?? "—"} color={T.muted} />
          {ac.rolloutGate.requestedTs ? <Row label="requested" value={fmtClock(ac.rolloutGate.requestedTs)} color={T.dim} /> : null}
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>RESEARCH INFLUENCE GATE</text>
          <Row label="status" value={ac.researchInfluenceGate.status} color={ac.researchInfluenceGate.status === "approved" ? T.up : ac.researchInfluenceGate.status === "blocked" ? T.down : T.dim} />
          <Row label="model" value={ac.researchInfluenceGate.modelId ?? "—"} color={T.muted} />
        </box>

        <box title=" CONTROL PLANE ACTIONS " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling>
            {ac.actions.length === 0 && <text fg={T.dim}>no actions recorded</text>}
            {ac.actions.map((a) => (
              <box key={a.id} flexDirection="row" gap={1}>
                <text fg={T.dim}>{fmtClock(a.ts)}</text>
                <text fg={a.result === "applied" ? T.up : a.result === "rejected" ? T.down : T.warn}>{a.action.toUpperCase()}</text>
                <text fg={T.muted}>by {a.actor}</text>
                <text fg={T.dim}>— {a.reason}</text>
              </box>
            ))}
          </scrollbox>
          <text fg={T.borderBright}>↑↓ scroll · / palette for actions</text>
        </box>
      </box>
    </box>
  )
}

function Row(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" justifyContent="space-between" paddingRight={2}>
      <text fg={T.dim}>{props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  )
}
