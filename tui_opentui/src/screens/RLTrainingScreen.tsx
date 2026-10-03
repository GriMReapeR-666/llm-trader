import { useDeck } from "../store"
import { T } from "../theme"
import { fmtNum } from "../format"
import { bar } from "../format"

export function RLTrainingScreen() {
  const rl = useDeck((s) => s.rlTrainer)
  const ql = rl.qLearning
  const ppo = rl.ppo

  return (
    <box flexDirection="row" flexGrow={1}>
      <box title=" Q-LEARNING " titleColor={T.cyan} borderStyle="single" borderColor={T.border} flexDirection="column" width={34} flexShrink={0} paddingLeft={1}>
        <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
          <text fg={T.dim}>TELEMETRY</text>
          <text fg={T.borderBright}>──────────────────────────────</text>
          <box flexDirection="row"><text fg={T.muted}>{"updates:".padEnd(16)}</text><text fg={T.bright}>{ql.totalUpdates}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"q-table size:".padEnd(16)}</text><text fg={T.bright}>{ql.qTableSize}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"exploration:".padEnd(16)}</text><text fg={T.warn}>{(ql.explorationRate * 100).toFixed(1)}%</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"explore/exploit:".padEnd(16)}</text><text fg={T.dim}>{ql.explorationCount} / {ql.exploitationCount}</text></box>
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>REWARD DISTRIBUTION</text>
          <box flexDirection="row"><text fg={T.muted}>{"avg (100):".padEnd(16)}</text><text fg={ql.avgReward100 > 0 ? T.up : T.down}>{fmtNum(ql.avgReward100, 3)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"total:".padEnd(16)}</text><text fg={T.fg}>{fmtNum(ql.totalReward, 2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"best:".padEnd(16)}</text><text fg={T.up}>{fmtNum(ql.bestReward, 2)}</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"worst:".padEnd(16)}</text><text fg={T.down}>{fmtNum(ql.worstReward, 2)}</text></box>
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>EXPLORATION RATE</text>
          <text fg={T.muted}>{bar(ql.explorationRate, 28)}</text>
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>HYPERPARAMETERS</text>
          <box flexDirection="row"><text fg={T.muted}>{"α (lr):".padEnd(16)}</text><text fg={T.fg}>0.10</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"γ (discount):".padEnd(16)}</text><text fg={T.fg}>0.95</text></box>
          <box flexDirection="row"><text fg={T.muted}>{"actions:".padEnd(16)}</text><text fg={T.fg}>BUY SELL HOLD</text></box>
          <text fg={T.borderBright}>──────────────────────────────</text>
          <text fg={T.dim}>TOP Q-VALUES (by state)</text>
          {rl.bestStates.length === 0 && <text fg={T.dim}>no active states in Q-table</text>}
          {rl.bestStates.map((s, i) => (
            <box key={i} flexDirection="row" gap={1}>
              <text fg={T.dim}>{String(i + 1).padStart(2)}.</text>
              <text fg={T.muted}>{s.key.slice(0, 22).padEnd(22)}</text>
              <text fg={T.up}>{fmtNum(s.buy, 2)}</text>
              <text fg={T.down}>{fmtNum(s.sell, 2)}</text>
              <text fg={T.dim}>{fmtNum(s.hold, 2)}</text>
            </box>
          ))}
        </scrollbox>
      </box>

      <box flexDirection="column" flexGrow={1}>
        <box title=" PPO + GAE " titleColor={T.purple} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <text fg={T.dim}>PPO TRAINING METRICS</text>
            <text fg={T.borderBright}>──────────────────────────────</text>
            <box flexDirection="row"><text fg={T.muted}>{"policy loss:".padEnd(18)}</text><text fg={ppo.policyLoss > 0 ? T.down : T.up}>{fmtNum(ppo.policyLoss, 6)}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"value loss:".padEnd(18)}</text><text fg={T.fg}>{fmtNum(ppo.valueLoss, 6)}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"entropy:".padEnd(18)}</text><text fg={T.fg}>{fmtNum(ppo.entropy, 6)}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"clip ε:".padEnd(18)}</text><text fg={T.fg}>{ppo.clipEps}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"γ (discount):".padEnd(18)}</text><text fg={T.fg}>{ppo.gamma}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"GAE λ:".padEnd(18)}</text><text fg={T.fg}>{ppo.gaeLambda}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"backend:".padEnd(18)}</text><text fg={T.fg}>{ppo.backend}</text></box>
            <box flexDirection="row"><text fg={T.muted}>{"actions:".padEnd(18)}</text><text fg={T.fg}>{ppo.actions.join(", ")}</text></box>
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>POLICY LOSS (latest updates)</text>
            {rl.ppoUpdates.length === 0 && <text fg={T.dim}>no PPO updates · training idle</text>}
            {rl.ppoUpdates.slice(-8).map((u, i) => (
              <box key={i} flexDirection="row" gap={1}>
                <text fg={T.dim}>U{String(u.update).padStart(3)}</text>
                <text fg={T.muted}>t={u.timesteps}</text>
                <text fg={u.policyLoss > 0 ? T.down : T.up}>π={fmtNum(u.policyLoss, 5)}</text>
                <text fg={T.fg}>V={fmtNum(u.valueLoss, 5)}</text>
                <text fg={T.cyan}>H={fmtNum(u.entropy, 4)}</text>
                <text fg={u.meanReward > 0 ? T.up : T.down}>R={fmtNum(u.meanReward, 3)}</text>
              </box>
            ))}
          </scrollbox>
        </box>

        <box title=" EPISODE HISTORY " titleColor={T.teal} borderStyle="single" borderColor={T.border} flexDirection="column" flexGrow={1} paddingLeft={1}>
          <scrollbox flexGrow={1} viewportCulling scrollbarOptions={{ showArrows: false, trackOptions: { backgroundColor: T.panel, foregroundColor: T.border } }}>
            <box flexDirection="row">
              <text fg={T.dim}>{"EP".padEnd(6)}{"STEPS".padEnd(8)}{"REWARD".padEnd(10)}{"P&L"}</text>
            </box>
            {rl.episodes.length === 0 && <text fg={T.dim}>no episodes recorded · awaiting training run</text>}
            {rl.episodes.map((ep, i) => (
              <box key={i} flexDirection="row">
                <text fg={T.dim}>{String(ep.episode).padEnd(6)}</text>
                <text fg={T.muted}>{String(ep.steps).padEnd(8)}</text>
                <text fg={ep.reward > 0 ? T.up : ep.reward < 0 ? T.down : T.dim}>{fmtNum(ep.reward, 2).padEnd(10)}</text>
                <text fg={ep.pnl !== null ? (ep.pnl > 0 ? T.up : T.down) : T.dim}>{ep.pnl !== null ? fmtNum(ep.pnl, 2) : "—"}</text>
              </box>
            ))}
            <text fg={T.borderBright}>──────────────────────────────</text>
            <text fg={T.dim}>completed: {rl.episodesCompleted} · training: {rl.isTraining ? "ACTIVE" : "IDLE"}</text>
          </scrollbox>
        </box>
      </box>
    </box>
  )
}
