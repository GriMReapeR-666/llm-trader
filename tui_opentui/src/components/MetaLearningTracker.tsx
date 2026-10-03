import { fitRow } from "../layout"
import type { Seg } from "../layout"
import { T } from "../theme"
import { useDeck } from "../store"

const META_STAGES = [
  { id: "collect", label: "COLLECT", desc: "live trades -> hash-chain corpus", input: "trades+labels", output: "meta_corpus.db" },
  { id: "mine", label: "MINE", desc: "pattern freq+corr min N=10", input: "corpus", output: "patterns" },
  { id: "propose", label: "PROPOSE", desc: "Δpriors {Δα,Δβ,Δρ}", input: "patterns", output: "proposals" },
  { id: "validate", label: "VALIDATE", desc: "ShadowDeploy DSR/CPCV OOS", input: "proposals", output: "PBO<0.25 DSR>0.70" },
  { id: "promote", label: "PROMOTE", desc: "research_promotion_gate + human approve", input: "evidence", output: "promotion_safe" },
  { id: "deploy", label: "DEPLOY", desc: "atomic RiskManager/alpha_ensemble", input: "artifact", output: "live weights" },
  { id: "ewc", label: "EWC", desc: "Fisher snapshot per regime", input: "regime shift", output: "L_EWC penalty" },
]

export function MetaLearningTracker(props: { compact?: boolean; width?: number }) {
  const rlTrainer = useDeck((s) => s.rlTrainer)
  const memory = useDeck((s) => s.memorySystem)
  const research = useDeck((s) => s.researchPipeline)
  const validation = useDeck((s) => s.validationPipeline)

  // Derive pipeline health from live store - immediate feedback Doherty 400ms
  const corpusRows = 0 // Would come from meta_corpus.db via API - 0 verified live
  const patternsCount = 0
  const proposalsCount = 0
  const qTableSize = rlTrainer.qLearning.qTableSize
  const staleFacts = memory.staleAtomicFacts
  const totalTokens = memory.totalPromptTokens

  const stagesTotal = META_STAGES.length
  const stagesComplete = corpusRows > 0 ? 2 : 0 // collect+mine need corpus
  const progress = stagesComplete / stagesTotal

  const avail = props.width != null ? Math.max(20, props.width - 1) : 999
  // each stage chip gets ~width/7 columns — only render the description line
  // when a chip is wide enough to hold words instead of mid-word wrap breaks
  const chipCol = Math.floor((avail - 1) / META_STAGES.length)
  const showDesc = props.width != null ? chipCol >= 11 : !props.compact

  return (
    <box flexDirection="column" gap={1}>
      {fitRow(avail, [
        { t: "◈ META-LEARNING LOOP · Loop 4", fg: T.purple },
        { t: ` · PBO 0.25 DSR 0.70 gate · corpus ${corpusRows} · patterns ${patternsCount} · proposals ${proposalsCount}`, fg: T.dim },
      ])}

      {/* Goal-Gradient #20 + Zeigarnik #11: show progress toward deploy */}
      {(() => {
        const barW = 28
        const filled = Math.round(progress * barW)
        const pct = `${Math.round(progress * 100)}%`
        const color = progress > 0.5 ? T.up : T.warn
        const segs: Seg[] = [{ t: "PIPELINE ", fg: T.dim }]
        if (filled > 0) segs.push({ t: "▰".repeat(filled), fg: color })
        segs.push({ t: "▱".repeat(barW - filled), fg: T.dim })
        segs.push({ t: ` ${pct}`, fg: T.dim })
        segs.push({
          t: ` q_table ${qTableSize} · stale ${staleFacts} · tokens ${totalTokens} · ${progress === 0 ? "INSUFFICIENT_DATA (needs 10 rows)" : `${stagesComplete}/${stagesTotal}`}`,
          fg: T.dim,
        })
        return fitRow(avail, segs)
      })()}

      {/* Miller #5 chunk + Prägnanz simplest + Von Restorff primary action */}
      <box flexDirection="row" gap={1}>
        {META_STAGES.map((s, idx) => {
          const active = idx < stagesComplete
          const isGate = s.id === "validate" || s.id === "promote"
          const color = active ? T.up : isGate ? T.warn : T.dim
          const icon = active ? "●" : idx === stagesComplete ? "◐" : "○"
          return (
            <box key={s.id} flexDirection="column" width={`${Math.floor(100 / META_STAGES.length)}%`} paddingLeft={1} borderStyle={isGate ? "single" : undefined} borderColor={isGate ? T.warn : undefined}>
              <text fg={color}>{icon} {s.label}</text>
              {showDesc && <text fg={T.dim}>{s.desc}</text>}
              {isGate && <text fg={T.warn}>GATE</text>}
            </box>
          )
        })}
      </box>

      {/* Uniform Connectedness #17: visually connect with lines */}
      <text fg={T.borderBright}>{"─".repeat(avail)}</text>
      {fitRow(avail, [
        { t: "collect", fg: T.dim },
        { t: " ──▶ ", fg: T.cyan },
        { t: "mine", fg: T.dim },
        { t: " ──▶ ", fg: T.cyan },
        { t: "propose", fg: T.dim },
        { t: " ──▶ GATE ──▶ ", fg: T.warn },
        { t: "promote", fg: T.dim },
        { t: " ──▶ ", fg: T.teal },
        { t: "deploy", fg: T.up },
        { t: " ↻ EWC", fg: T.purple },
      ])}

      {!props.compact && (
        <box flexDirection="column" gap={0}>
          <text fg={T.cyan}>◈ COMPETING LEARNERS (advisory_only=True until gate)</text>
          <text fg={T.muted}>  • MARS: mean_ic*5 clip 0.1-2.0 <span fg={T.warn}>heuristic single-sample</span> → artifact→alpha_ensemble (P0 fixed 42b194d)</text>
          <text fg={T.muted}>  • Evolutionary: Sharpe fitness <span fg={T.up}>simplified CPCV</span> pop10 gen2 (P1 deferred full combin)</text>
          <text fg={T.muted}>  • GRPO/PPO/MetaRL: <span fg={T.purple}>DEFERRED promotion_safe False</span> until gate · artifacts advisory</text>
          <text fg={T.muted}>  • BeyondPrompting: substring align 0.3 → 3 alphas OOS ic*sqrt → validated prompt only</text>
          <text fg={T.dim}>Execution authority: only DeterministicValidator+RiskController submits · LLM prompt advisory · Peak-End: satisfying deploy shows artifact hash</text>
        </box>
      )}
    </box>
  )
}
