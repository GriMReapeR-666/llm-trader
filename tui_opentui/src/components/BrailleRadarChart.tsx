import { useMemo } from "react"
import { T } from "../theme"

export interface RadarMetric {
  key: string
  label: string
  value: number // 0.0 to 1.0 (normalized for radar polygon)
  rawValue: string // formatted string (e.g. "+1.84R", "0.98", "0.3 pips")
  benchmark?: number // neutral baseline (typically 0.5)
}

export interface BrailleRadarChartProps {
  title?: string
  subtitle?: string
  metrics: RadarMetric[] // exactly 5 metrics for pentagon
  comparisonMetrics?: RadarMetric[] // optional secondary polygon
  width?: number
  height?: number
  accentColor?: string
  comparisonColor?: string
}

// ---------------------------------------------------------------------------
// Magnitude CLI Pixel-Calibrated Pentagon Radar Architecture
// Exact 56 columns x 15 rows with dynamic vertex-aligned label positioning
// ---------------------------------------------------------------------------
const PENTAGON_RADAR_COLUMNS = 56
const PENTAGON_RADAR_ROWS = 15

const BRAILLE_BITS = [
  [0x01, 0x08], // y=0: dot1, dot4
  [0x02, 0x10], // y=1: dot2, dot5
  [0x04, 0x20], // y=2: dot3, dot6
  [0x40, 0x80], // y=3: dot7, dot8
] as const

type RadarTone = "empty" | "grid" | "profile" | "label" | "detail"

interface RadarRun {
  readonly text: string
  readonly tone: RadarTone
}

interface Point {
  readonly x: number
  readonly y: number
}

type AxisAlignment = "left" | "center" | "right"

const clamp01 = (value: number): number => Math.min(1, Math.max(0, value))

const pointOnAxis = (
  center: Point,
  radiusX: number,
  radiusY: number,
  axis: number,
  axisCount: number = 5,
): Point => {
  const angle = -Math.PI / 2 + (axis * Math.PI * 2) / axisCount
  return {
    x: center.x + Math.cos(angle) * radiusX,
    y: center.y + Math.sin(angle) * radiusY,
  }
}

const setDot = (
  buffer: Uint8Array,
  dotWidth: number,
  dotHeight: number,
  x: number,
  y: number,
): void => {
  const roundedX = Math.round(x)
  const roundedY = Math.round(y)
  if (roundedX < 0 || roundedY < 0 || roundedX >= dotWidth || roundedY >= dotHeight) return
  buffer[roundedY * dotWidth + roundedX] = 1
}

const drawLine = (
  buffer: Uint8Array,
  dotWidth: number,
  dotHeight: number,
  start: Point,
  end: Point,
  dotStep: number = 1,
): void => {
  let x0 = Math.round(start.x)
  let y0 = Math.round(start.y)
  const x1 = Math.round(end.x)
  const y1 = Math.round(end.y)
  const dx = Math.abs(x1 - x0)
  const sx = x0 < x1 ? 1 : -1
  const dy = -Math.abs(y1 - y0)
  const sy = y0 < y1 ? 1 : -1
  let error = dx + dy
  let step = 0
  for (;;) {
    if (step % dotStep === 0) {
      setDot(buffer, dotWidth, dotHeight, x0, y0)
    }
    if (x0 === x1 && y0 === y1) break
    const doubled = error * 2
    if (doubled >= dy) {
      error += dy
      x0 += sx
    }
    if (doubled <= dx) {
      error += dx
      y0 += sy
    }
    step += 1
  }
}

const brailleMask = (
  buffer: Uint8Array,
  dotWidth: number,
  cellX: number,
  cellY: number,
): number => {
  let mask = 0
  for (let y = 0; y < 4; y += 1) {
    for (let x = 0; x < 2; x += 1) {
      if (buffer[(cellY * 4 + y) * dotWidth + cellX * 2 + x] === 1) {
        mask |= BRAILLE_BITS[y]![x]!
      }
    }
  }
  return mask
}

const writeText = (
  characters: string[][],
  tones: RadarTone[][],
  text: string,
  startX: number,
  y: number,
  tone: Extract<RadarTone, "label" | "detail">,
): void => {
  if (y < 0 || y >= characters.length) return
  for (let index = 0; index < text.length; index += 1) {
    const x = startX + index
    if (x < 0 || x >= characters[y]!.length) continue
    characters[y]![x] = text[index]!
    tones[y]![x] = tone
  }
}

const alignedStartX = (
  text: string,
  anchorX: number,
  alignment: AxisAlignment,
): number => {
  if (alignment === "left") return anchorX
  if (alignment === "right") return Math.max(0, anchorX - text.length + 1)
  return Math.max(0, Math.round(anchorX - text.length / 2))
}

export function BrailleRadarChart(props: BrailleRadarChartProps) {
  const {
    metrics,
    accentColor = T.cyan,
  } = props

  // Map input metrics into 5 clockwise axes:
  // Axis 0: Top (Profitability)
  // Axis 1: Top-Right (Execution)
  // Axis 2: Bottom-Right (Tail Risk)
  // Axis 3: Bottom-Left (Generalization)
  // Axis 4: Top-Left (Stability)
  // Honesty contract: axes without a supplied metric render "NO DATA" (value 0
  // collapses the polygon vertex at center) — the chart never invents numbers.
  const axes = useMemo(() => {
    const slot = (i: number, canonical: string): { label: string; detail: string; percent: number; value: number } => {
      const m = metrics[i]
      if (!m) return { label: canonical, detail: "NO DATA", percent: -1, value: 0 }
      const raw = m.rawValue
      return {
        label: m.label ?? canonical,
        detail: raw ?? "NO DATA",
        percent: Math.round((m.value ?? 0) * 100),
        value: m.value ?? 0,
      }
    }
    return [
      slot(0, "PROFITABILITY"),
      slot(1, "EXECUTION"),
      slot(2, "TAIL RISK"),
      slot(3, "GENERALIZATION"),
      slot(4, "STABILITY"),
    ]
  }, [metrics])

  // Compute unified character and tone matrix using Magnitude CLI's exact geometry
  const frame = useMemo((): RadarRun[][] => {
    const columns = PENTAGON_RADAR_COLUMNS
    const rows = PENTAGON_RADAR_ROWS
    const safeColumns = Math.max(44, Math.floor(columns))
    const safeRows = Math.max(13, Math.floor(rows))
    const dotWidth = safeColumns * 2
    const dotHeight = safeRows * 4
    const chartTop = 4
    const radiusX = 27
    const radiusY = 26
    const center: Point = { x: dotWidth / 2, y: chartTop + radiusY }

    const guide = new Uint8Array(dotWidth * dotHeight)
    const profile = new Uint8Array(dotWidth * dotHeight)
    const axisCount = 5

    // 1. Render Outer Pentagon Guide Boundary (Solid, 100% connected straight lines)
    const outerPoints = axes.map((_, axis) => pointOnAxis(center, radiusX, radiusY, axis, axisCount))
    for (let axis = 0; axis < axisCount; axis += 1) {
      drawLine(guide, dotWidth, dotHeight, outerPoints[axis]!, outerPoints[(axis + 1) % axisCount]!, 1)
    }

    // 2. Render 50% Benchmark Reference Pentagon (Solid, 100% connected straight lines)
    const midPoints = axes.map((_, axis) => pointOnAxis(center, radiusX * 0.5, radiusY * 0.5, axis, axisCount))
    for (let axis = 0; axis < axisCount; axis += 1) {
      drawLine(guide, dotWidth, dotHeight, midPoints[axis]!, midPoints[(axis + 1) % axisCount]!, 1)
    }

    // 3. Render Spokes (Clean connected rays from 0.20 to 1.0)
    for (let axis = 0; axis < axisCount; axis += 1) {
      const pStart = pointOnAxis(center, radiusX * 0.20, radiusY * 0.20, axis, axisCount)
      const pEnd = pointOnAxis(center, radiusX, radiusY, axis, axisCount)
      drawLine(guide, dotWidth, dotHeight, pStart, pEnd, 1)
    }
    // Single subtle center reference point
    setDot(guide, dotWidth, dotHeight, center.x, center.y)

    // 4. Render Profile Polygon (Solid, 100% connected straight diagonal lines)
    const profilePoints = axes.map((a, axis) => {
      return pointOnAxis(center, radiusX * clamp01(a.value), radiusY * clamp01(a.value), axis, axisCount)
    })
    for (let axis = 0; axis < axisCount; axis += 1) {
      const pt = profilePoints[axis]!
      const next = profilePoints[(axis + 1) % axisCount]!
      drawLine(profile, dotWidth, dotHeight, pt, next, 1)
      setDot(profile, dotWidth, dotHeight, pt.x, pt.y)
    }

    // 5. Assemble base cells into character matrix
    const characters: string[][] = Array.from({ length: safeRows }, (_, y) =>
      Array.from({ length: safeColumns }, (_, x) => {
        const pMask = brailleMask(profile, dotWidth, x, y)
        const gMask = brailleMask(guide, dotWidth, x, y)
        if (pMask > 0) return String.fromCodePoint(0x2800 + pMask)
        if (gMask > 0) return String.fromCodePoint(0x2800 + gMask)
        return " "
      }),
    )

    const tones: RadarTone[][] = Array.from({ length: safeRows }, (_, y) =>
      Array.from({ length: safeColumns }, (_, x) => {
        const pMask = brailleMask(profile, dotWidth, x, y)
        const gMask = brailleMask(guide, dotWidth, x, y)
        if (pMask > 0) return "profile"
        if (gMask > 0) return "grid"
        return "empty"
      }),
    )

    // 6. Overlay Text Labels with verified institutional layout & clearance
    // Top Axis (Axis 0): Center-aligned over apex
    writeText(characters, tones, axes[0]!.label, Math.floor(safeColumns / 2 - axes[0]!.label.length / 2), 0, "label")
    writeText(characters, tones, axes[0]!.detail, Math.floor(safeColumns / 2 - axes[0]!.detail.length / 2), 1, "detail")

    // Upper and Lower Right Axes (Axes 1 & 2): Left-aligned at dedicated margin
    writeText(characters, tones, axes[1]!.label, 43, 4, "label")
    writeText(characters, tones, axes[1]!.detail, 43, 5, "detail")
    writeText(characters, tones, axes[2]!.label, 42, 12, "label")
    writeText(characters, tones, axes[2]!.detail, 42, 13, "detail")

    // Lower and Upper Left Axes (Axes 3 & 4): Clean left margin placement
    writeText(characters, tones, axes[3]!.label, 2, 12, "label")
    writeText(characters, tones, axes[3]!.detail, 2, 13, "detail")
    writeText(characters, tones, axes[4]!.label, 2, 4, "label")
    writeText(characters, tones, axes[4]!.detail, 2, 5, "detail")

    // 7. Group into color runs per line
    return characters.map((row, y) => {
      const runs: RadarRun[] = []
      for (let x = 0; x < safeColumns; x += 1) {
        const tone = tones[y]![x]!
        const character = row[x]!
        const previous = runs.at(-1)
        if (previous?.tone === tone) {
          runs[runs.length - 1] = { tone, text: `${previous.text}${character}` }
        } else {
          runs.push({ tone, text: character })
        }
      }
      return runs
    })
  }, [axes])

  return (
    <box flexDirection="column" alignItems="center" flexShrink={0}>
      {/* Magnitude Pentagon Radar Canvas with Unified Run Spans */}
      <box
        flexDirection="column"
        width={PENTAGON_RADAR_COLUMNS}
        height={PENTAGON_RADAR_ROWS}
        flexShrink={0}
      >
        {frame.map((row, rowIndex) => (
          <text key={rowIndex} wrapMode="none">
            {row.map((run, runIndex) => (
              <span
                key={runIndex}
                fg={
                  run.tone === "profile"
                    ? accentColor
                    : run.tone === "label"
                      ? T.bright
                      : run.tone === "detail"
                        ? "#94a3b8"
                        : run.tone === "grid"
                          ? "#334155"
                          : undefined
                }
              >
                {run.text}
              </span>
            ))}
          </text>
        ))}
      </box>

      {/* Institutional Bias Metrics Badge Strip - Clean, non-wrapping chip bar */}
      <box
        flexDirection="row"
        justifyContent="space-between"
        width={PENTAGON_RADAR_COLUMNS}
        marginTop={1}
      >
        {axes.map((a, idx) => (
          <box key={idx} flexDirection="row" alignItems="center">
            <text fg={T.muted}>{`[${a.label.slice(0, 4)} `}</text>
            {a.percent >= 0 ? (
              <text fg={a.percent >= 70 ? T.up : a.percent >= 50 ? T.warn : T.down}>
                {`${a.percent}%`}
              </text>
            ) : (
              <text fg={T.dim}>—</text>
            )}
            <text fg={T.muted}>{"]"}</text>
          </box>
        ))}
      </box>
    </box>
  )
}

