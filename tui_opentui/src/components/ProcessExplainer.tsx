import { useState } from "react"
import { T } from "../theme"

export interface ProcessExplanationStep {
  stepNumber: number
  toolName: string
  codedCall: string // e.g. "SafeAlphaEvaluator(expr='decay_linear(delta(close, 2), 5)', ic=0.042)"
  naturalExplanation: string // plain English explanation
  status: "PASSED" | "FAILED" | "WARNING" | "RUNNING"
  latencyMs: number
  engine?: "LLM" | "KERNEL" // Distinguish LLM-dependent processes vs deterministic kernel
  category?: string
}

export interface ProcessExplainerProps {
  strategyName: string
  steps: ProcessExplanationStep[]
}

const STATUS_ICONS: Record<string, string> = {
  PASSED: "✓",
  FAILED: "✗",
  WARNING: "⚠",
  RUNNING: "●",
}

const STATUS_COLORS: Record<string, string> = {
  PASSED: T.up,
  FAILED: T.down,
  WARNING: T.warn,
  RUNNING: T.cyan,
}

export function ProcessExplainer(props: ProcessExplainerProps) {
  const { strategyName, steps } = props
  const [viewMode, setViewMode] = useState<"EXPLAINED" | "CODED" | "BOTH">("BOTH")

  return (
    <box
      flexDirection="column"
      borderStyle="single"
      borderColor={T.border}
      backgroundColor={T.panel}
      paddingLeft={1}
      paddingRight={1}
      flexGrow={1}
    >
      {/* Header & Mode Toggle */}
      <box flexDirection="row" justifyContent="space-between" marginBottom={1}>
        <box flexDirection="row" gap={1}>
          <text fg={T.teal}>
            PIPELINE EXPLAINER:
          </text>
          <text fg={T.bright}>
            {strategyName}
          </text>
          <text fg={T.dim}>({steps.length} Stages)</text>
        </box>
        <box flexDirection="row" gap={1}>
          <text fg={T.dim}>View:</text>
          <box onMouseDown={() => setViewMode("BOTH")}>
            <text fg={viewMode === "BOTH" ? T.cyan : T.dim}>
              [1:Both]
            </text>
          </box>
          <box onMouseDown={() => setViewMode("EXPLAINED")}>
            <text fg={viewMode === "EXPLAINED" ? T.cyan : T.dim}>
              [2:English]
            </text>
          </box>
          <box onMouseDown={() => setViewMode("CODED")}>
            <text fg={viewMode === "CODED" ? T.cyan : T.dim}>
              [3:Code]
            </text>
          </box>
        </box>
      </box>

      <box borderStyle="single" borderColor={T.border} height={1} marginBottom={1} />

      {/* Step by step list */}
      <scrollbox flexGrow={1} viewportCulling>
        {steps.map((step) => (
          <box key={step.stepNumber} flexDirection="column" marginBottom={1}>
            {/* Step Status, Engine Badge & Tool Name */}
            <box flexDirection="row" gap={1} alignItems="center">
              <text fg={STATUS_COLORS[step.status] ?? T.dim}>
                {STATUS_ICONS[step.status] ?? "•"}
              </text>
              {step.engine && (
                <text
                  fg={T.bg}
                  bg={step.engine === "LLM" ? T.purple : T.teal}
                >
                  {` ${step.engine === "LLM" ? "LLM REASONING" : "DETERMINISTIC"} `}
                </text>
              )}
              <text fg={T.bright}>
                Stage {step.stepNumber}: {step.toolName}
              </text>
              <text fg={T.dim}>({step.latencyMs}ms)</text>
              <text fg={STATUS_COLORS[step.status]}>
                [{step.status}]
              </text>
            </box>

            {/* Coded Tool Call */}
            {(viewMode === "CODED" || viewMode === "BOTH") && (
              <box marginLeft={3} backgroundColor={T.raised} paddingLeft={1} paddingRight={1}>
                <text fg={T.cyan}>{`> call_tool: ${step.codedCall}`}</text>
              </box>
            )}

            {/* Plain English Explanation */}
            {(viewMode === "EXPLAINED" || viewMode === "BOTH") && (
              <box marginLeft={3} marginTop={viewMode === "BOTH" ? 0 : 1}>
                <text fg={T.fg}>{step.naturalExplanation}</text>
              </box>
            )}
          </box>
        ))}
      </scrollbox>
    </box>
  )
}
