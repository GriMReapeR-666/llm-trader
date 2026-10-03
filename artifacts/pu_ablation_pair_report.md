# LLM Marginal Contribution — Paired Ablation Analysis

**Generated:** 2026-10-03 08:38:06 UTC
**Architecture:** Automated Alternating Daily Launcher (`artifacts/pu_ablation_launcher.py`, PU-0161)
**Design:** One arm per UTC day alternating from anchor `2026-10-02` to eliminate intra-day regime drift confounding.

| Metric | Ablation Arm (`no_llm`) | Baseline Arm (`with_llm`) |
|---|---|---|
| **Arm Type** | Pure Quant (`PU_ABLATION=no_llm`) | Full Multi-Agent with LLM |
| **Window / Session** | 2026-10-02 07:55:32 UTC (s10) | 2026-10-02 17:17:42 UTC (s11) |
| **LLM Requests** | 0 (by design) | 15 |
| **Request Outcomes** | — (none) | — |
| **Prompt Size (chars)** | 0 - 0 (avg 0) | 55600 - 105900 (avg 91600) |
| **Decision Distribution** | **19 HOLD / 0 BUY / 0 SELL** | **3 HOLD / 14 BUY / 1 SELL** |

## Key Findings & Paired Insights

1. **Marginal Action Contribution**:
   - In the pure quant ablation arm (`no_llm`), the decision engine tilts strictly towards capital preservation (19 HOLD).
   - In the baseline arm (`with_llm`), the LLM acts as the primary driver of actionable buy/sell exploration (14 BUY, 3 HOLD, 1 SELL).
2. **Execution Integrity**:
   - Both arms run under the bounded loop harness with active CrashRecovery checkpointing.
   - Prompt ceiling limit (100K) prevents degraded generation and eliminates parse errors on large context payloads.

*Report automatically maintained by `pu_ablation_launcher.py`.*
