// Throwaway border-integrity check: renders StreamScreen / BrainScreen directly
// at several terminal sizes with adversarial fixtures, then captures char frames.
// Run: bun src/selftest_borders.tsx
import { testRender } from "@opentui/react/test-utils"
import { store } from "./store"
import { StreamScreen } from "./screens/StreamScreen"
import { BrainScreen } from "./screens/BrainScreen"

function seed() {
  store.pushStream({
    ts: Date.now(),
    kind: "system",
    agent: "SYSTEM",
    title: "a very long system banner title that would previously overflow the bordered system card at narrow terminal widths",
    lines: ["heartbeat ok · all gates nominal"],
  })
  store.pushStream({
    ts: Date.now(),
    kind: "alert",
    agent: "RiskGuard",
    severity: "critical",
    title: "drawdown guard tripped — a fairly long alert headline that must stay inside the alert border",
    lines: ["equity drawdown 4.2% exceeds 3.5% soft cap"],
  })
  store.pushStream({
    ts: Date.now(),
    kind: "validation",
    agent: "RiskController",
    title: "DeterministicValidator PASS with an extended suffix description of the checks performed",
    lines: ["risk 0.75% ≤ 1.00% cap"],
  })
  store.pushStream({
    ts: Date.now(),
    kind: "tool",
    agent: "TechnicalNet",
    title: "mt5.copy_rates with an unnecessarily verbose argument summary attached to the tool call row",
    lines: ["XAUUSD H1 bars=300"],
  })
  store.pushStream({
    ts: Date.now(),
    kind: "thinking",
    agent: "MarketModel",
    title: "reasoning…",
    lines: ["weighing macro continuation against session reversal risk…"],
  })
  store.set({
    cycles: [
      {
        id: "XAUUSD-H1-test",
        ts: Date.now(),
        symbol: "XAUUSD",
        timeframe: "H1",
        strategy: "macro_continuation_v1",
        regime: "trending",
        decision: "EXECUTE",
        confidence: 0.84,
        reasoning: "## XAUUSD H1 — STRONG BUY\n\n**Regime**: ADX trending with a very long sentence that must wrap inside the cycle-brief panel rather than crossing its border at any terminal width.",
        intent: {
          proposed: ["volume=0.20", "sl_atr_mult=1.8", "tp_rr=2.5"],
          validated: ["volume=0.15", "sl_atr_mult=1.2", "tp_rr=2.0"],
          diff: "--- proposed\n+++ validated\n@@ -1,3 +1,3 @@\n-volume=0.20 with a very long comment appended that must wrap inside the intent diff panel border\n+volume=0.15",
        },
      },
    ],
    mtf: [
      { tf: "H4", trend: "BULL", adx: 27.1, rsi: 61.2, bias: "long" },
      { tf: "H1", trend: "BULL", adx: 24.8, rsi: 58.0, bias: "long" },
    ],
  })
  // decision LAST so it stays inside the sticky-bottom viewport
  store.pushStream({
    ts: Date.now(),
    kind: "decision",
    agent: "MarketModel",
    title: "[ STRONG BUY XAUUSD — macro continuation regime confirmed across sessions ]",
    lines: ["confidence 84% · strategy macro_continuation_v1 · regime trending"],
    reasoning: [
      "## XAUUSD H1 — STRONG BUY",
      "",
      "**Regime**: ADX(14)=27.3 trending, H1/M15 aligned with the four-hour structure and the weekly bias remains constructive into the London fix window.",
      "**Macro**: DXY momentum negative (−0.31 4h). Causal edge DXY→XAUUSD strength 0.82 at 4h lag supports continuation. 📉 No calendar conflict inside 90m lockout. 中文宽度测试也必须保持在边框之内而不越界。",
      "**Microstructure**: bid-side DOM imbalance +0.38; spread 0.6 pips inside calibrated band.",
      "**Plan**: enter on retest of 2414, stop beyond structure at 1.2×ATR (18.4), first target 2R at 2450, trail remainder via ATR chandelier and reassess at the 1.5R mark for partial profit taking.",
      "**Invalidation**: close beyond session VWAP band flips bias to flat.",
    ],
    intentDiff: [
      "@@ proposed → validated",
      "-volume=0.20  # initial sizing from the proposer before the deterministic risk gate trimmed exposure",
      "+volume=0.15",
      "-sl_atr_mult=1.8",
      "+sl_atr_mult=1.2",
      "-tp_rr=2.5",
      "+tp_rr=2.0",
    ],
  })
}

async function capture(label: string, el: any, width: number, height: number) {
  const setup = await testRender(el, { width, height })
  for (let i = 0; i < 6; i++) {
    await setup.flush()
    await setup.renderOnce()
  }
  const frame: string = setup.captureCharFrame()
  const lines = frame.split("\n")
  const over = lines.map((l, i) => [i, l.length] as const).filter(([, n]) => n > width)
  console.log(`\n=== ${label} @ ${width}x${height} · lines=${lines.length} overflowRows=${over.length} ===`)
  if (over.length > 0) console.log("OVERFLOW ROWS:", JSON.stringify(over))
  console.log(frame)
  setup.renderer.destroy()
}

async function main() {
  seed()
  for (const [w, h] of [[120, 40], [80, 34], [60, 28]] as const) {
    await capture("STREAM", <StreamScreen />, w, h)
    await capture("BRAIN", <BrainScreen />, w, h)
  }
  process.exit(0)
}

main().catch((err) => {
  console.error("BORDER CHECK FAILED:", err)
  process.exit(1)
})
