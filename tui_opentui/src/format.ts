export function fmtMoney(v: number, digits = 2): string {
  const sign = v < 0 ? "-" : ""
  const abs = Math.abs(v)
  const s = abs.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return `${sign}$${s}`
}

export function fmtNum(v: number, digits = 2): string {
  return v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtPct(v: number, digits = 2): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`
}

export function fmtClock(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function pad(s: string, n: number): string {
  if (s.length >= n) return s.slice(0, n)
  return s + " ".repeat(n - s.length)
}

export function padLeft(s: string, n: number): string {
  if (s.length >= n) return s.slice(0, n)
  return " ".repeat(n - s.length) + s
}

const SPARK_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

export function sparkline(data: number[], width = 24): string {
  if (data.length < 2) return ""
  const step = Math.max(1, Math.floor(data.length / width))
  const pts: number[] = []
  for (let i = 0; i < data.length; i += step) pts.push(data[i])
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const range = max - min || 1
  return pts
    .slice(-width)
    .map((v) => SPARK_CHARS[Math.min(7, Math.floor(((v - min) / range) * 7.99))])
    .join("")
}

export function sciBar(value: number, width: number, filled = "█", empty = "░"): string {
  const clamped = Math.max(0, Math.min(1, value))
  const n = Math.round(clamped * width)
  return filled.repeat(n) + empty.repeat(width - n)
}

export const bar = sciBar
