import { useMemo } from "react"
import { T } from "../theme"
import { fmtNum } from "../format"

export interface BrailleChartProps {
  data: number[]
  width?: number
  height?: number
  color?: string
  fill?: boolean
  fillMode?: "solid" | "stipple"
  baseline?: "bottom" | "top" // "bottom" for equity growth, "top" for underwater drawdown
  formatY?: (val: number) => string
  showGrid?: boolean
  label?: string
}

const BRAILLE_BITS = [
  [0x01, 0x08], // y=0: dot1, dot4
  [0x02, 0x10], // y=1: dot2, dot5
  [0x04, 0x20], // y=2: dot3, dot6
  [0x40, 0x80], // y=3: dot7, dot8
] as const

const GUTTER_WIDTH = 8

function setDot(
  buf: Uint8Array,
  w: number,
  h: number,
  x: number,
  y: number,
): void {
  const rx = Math.round(x)
  const ry = Math.round(y)
  if (rx < 0 || ry < 0 || rx >= w || ry >= h) return
  buf[ry * w + rx] = 1
}

function drawLine(
  buf: Uint8Array,
  w: number,
  h: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
): void {
  let cx = Math.round(x0)
  let cy = Math.round(y0)
  const tx = Math.round(x1)
  const ty = Math.round(y1)
  const dx = Math.abs(tx - cx)
  const sx = cx < tx ? 1 : -1
  const dy = -Math.abs(ty - cy)
  const sy = cy < ty ? 1 : -1
  let err = dx + dy

  for (;;) {
    setDot(buf, w, h, cx, cy)
    if (cx === tx && cy === ty) break
    const e2 = err * 2
    if (e2 >= dy) {
      err += dy
      cx += sx
    }
    if (e2 <= dx) {
      err += dx
      cy += sy
    }
  }
}

function formatDefault(v: number): string {
  const abs = Math.abs(v)
  if (abs >= 100000) return `${(v / 1000).toFixed(0)}k`
  if (abs >= 10000) return `${(v / 1000).toFixed(1)}k`
  if (abs >= 1000) return `${(v / 1000).toFixed(1)}k`
  if (abs < 0.1 && abs > 0) return v.toFixed(2)
  return fmtNum(v, abs >= 10 ? 0 : 1)
}

export function BrailleChart(props: BrailleChartProps) {
  const {
    data,
    width = 44,
    height = 5,
    color = T.up,
    fill = true,
    fillMode = "solid",
    baseline = "bottom",
    formatY = formatDefault,
    showGrid = true,
  } = props

  const chart = useMemo(() => {
    if (!data || data.length === 0) {
      return {
        rows: Array.from({ length: height }, () => ({
          gutter: " ".repeat(GUTTER_WIDTH - 1) + "│",
          canvas: " ".repeat(Math.max(10, width - GUTTER_WIDTH)),
        })),
        minVal: 0,
        maxVal: 0,
        lastVal: 0,
      }
    }

    const canvasCols = Math.max(10, width - GUTTER_WIDTH)
    const dotW = canvasCols * 2
    const dotH = height * 4

    let minVal = Math.min(...data)
    let maxVal = Math.max(...data)

    if (baseline === "top") {
      // Underwater drawdown: top row is 0%
      maxVal = Math.max(0, maxVal)
      if (minVal >= 0) minVal = -0.01
    }

    if (minVal === maxVal) {
      minVal -= 1
      maxVal += 1
    }

    const range = maxVal - minVal || 1
    const lastVal = data[data.length - 1] ?? 0

    // Virtual bit buffers
    const lineBuf = new Uint8Array(dotW * dotH)
    const fillBuf = new Uint8Array(dotW * dotH)
    const gridBuf = new Uint8Array(dotW * dotH)

    // 1. Draw subtle dotted horizontal grid line at mid-height
    if (showGrid && height >= 3) {
      const midY = Math.round(dotH / 2)
      for (let x = 0; x < dotW; x += 4) {
        setDot(gridBuf, dotW, dotH, x, midY)
      }
    }

    // 2. Map data points to virtual dot coordinates and draw connecting lines
    const n = data.length
    const points: { x: number; y: number }[] = []
    for (let i = 0; i < n; i++) {
      const x = Math.round((i * (dotW - 1)) / Math.max(1, n - 1))
      const norm = (data[i]! - minVal) / range
      const y = Math.round((1 - Math.max(0, Math.min(1, norm))) * (dotH - 1))
      points.push({ x, y })
    }

    // Draw lines between consecutive points
    for (let i = 0; i < points.length - 1; i++) {
      drawLine(
        lineBuf,
        dotW,
        dotH,
        points[i]!.x,
        points[i]!.y,
        points[i + 1]!.x,
        points[i + 1]!.y,
      )
    }
    if (points.length === 1) {
      setDot(lineBuf, dotW, dotH, points[0]!.x, points[0]!.y)
    }

    // 3. Fill area under/above curve
    if (fill) {
      for (let x = 0; x < dotW; x++) {
        let lineY = -1
        if (baseline === "bottom") {
          for (let y = 0; y < dotH; y++) {
            if (lineBuf[y * dotW + x] === 1) {
              lineY = y
              break
            }
          }
          if (lineY !== -1) {
            for (let y = lineY; y < dotH; y++) {
              if (fillMode === "stipple") {
                if ((x + y) % 2 === 0) setDot(fillBuf, dotW, dotH, x, y)
              } else {
                setDot(fillBuf, dotW, dotH, x, y)
              }
            }
          }
        } else {
          // baseline === "top" (e.g. drawdown from y=0 down to curve)
          for (let y = dotH - 1; y >= 0; y--) {
            if (lineBuf[y * dotW + x] === 1) {
              lineY = y
              break
            }
          }
          if (lineY !== -1) {
            for (let y = 0; y <= lineY; y++) {
              if (fillMode === "stipple") {
                if ((x + y) % 2 === 0) setDot(fillBuf, dotW, dotH, x, y)
              } else {
                setDot(fillBuf, dotW, dotH, x, y)
              }
            }
          }
        }
      }
    }

    // 4. Assemble cells into Braille characters
    const rows: { gutter: string; canvas: string }[] = []
    for (let cy = 0; cy < height; cy++) {
      // Compute Gutter label
      let gutter = ""
      const isTop = cy === 0
      const isBottom = cy === height - 1
      const isMid = height >= 5 && cy === Math.floor(height / 2)

      if (isTop) {
        const lbl = formatY(maxVal).slice(0, GUTTER_WIDTH - 1).padStart(GUTTER_WIDTH - 1)
        gutter = `${lbl}┤`
      } else if (isBottom) {
        const lbl = formatY(minVal).slice(0, GUTTER_WIDTH - 1).padStart(GUTTER_WIDTH - 1)
        gutter = `${lbl}┴`
      } else if (isMid) {
        const midVal = (maxVal + minVal) / 2
        const lbl = formatY(midVal).slice(0, GUTTER_WIDTH - 1).padStart(GUTTER_WIDTH - 1)
        gutter = `${lbl}┤`
      } else {
        gutter = `${" ".repeat(GUTTER_WIDTH - 1)}│`
      }

      // Convert dot buffer to Braille characters for this row
      let canvas = ""
      for (let cx = 0; cx < canvasCols; cx++) {
        let mask = 0
        for (let dy = 0; dy < 4; dy++) {
          for (let dx = 0; dx < 2; dx++) {
            const dotX = cx * 2 + dx
            const dotY = cy * 4 + dy
            const idx = dotY * dotW + dotX
            if (lineBuf[idx] === 1 || fillBuf[idx] === 1 || gridBuf[idx] === 1) {
              mask |= BRAILLE_BITS[dy]![dx]!
            }
          }
        }
        canvas += mask === 0 ? " " : String.fromCodePoint(0x2800 + mask)
      }

      rows.push({ gutter, canvas })
    }

    return { rows, minVal, maxVal, lastVal }
  }, [data, width, height, color, fill, fillMode, baseline, formatY, showGrid])

  return (
    <box flexDirection="column" width={width} flexShrink={0}>
      {chart.rows.map((r, i) => (
        <text key={i} wrapMode="none">
          <span fg={T.dim}>{r.gutter}</span>
          <span fg={color}>{r.canvas}</span>
        </text>
      ))}
    </box>
  )
}
