# Supervised Paper-Trading Accumulation Path — Proposal
*2026-10-01 · branch `feat/ui-pipeline-compact-tabs` @ `50456f4`*

## Problem

The certification pipeline now runs (after the `fetch_ohlc` lazy-attach fix), but the
evidence gates need executed-trade evidence the frozen system can never produce:

| Gate | Threshold | Today | True blocker |
|---|---|---|---|
| CPCV (≥40 net-of-cost trade returns) | 40 | 0 journal / 26 from 500-bar replay | replay depth only |
| Walk-forward (≥20 broker-reconciled r-multiples) | 20 | 0 | **zero executed trades, ever** |
| Live-comparison | ≥10 broker-confirmed closes | 0 | same |
| `patterns` learned rows | — | 0 | every cycle ends HOLD |

`order_intents` has 0 rows: nothing has ever reached submission. The system is honest
but starved — a cold-start deadlock the doctrine never anticipated.

## Key mechanism discovery (changes the design)

`_stage_cpcv` feed priority is **(1) the Stage-1 backtest's own R-multiple stream,
(2) journal trades**. Stage 1 on 500 bars produced 26 R-multiples. So **CPCV is a data
problem, not a trading problem**: raise the replay depth and it runs today, with zero
executed trades. Walk-forward is the only gate that genuinely requires executed,
broker-reconciled trades (with the broker's own tickets — `order_intents.mt5_ticket`
or `broker_execution_reports`; self-declared `source="broker"` proves nothing).

## The path: three phases

### Phase 0 — Immediate: un-freeze CPCV by deepening replay (no trading)
Run the certification sweep with `--bars 5000` (approx. 8–10 months of EURUSD H1).
At the observed 5.2 trades/100-bar rate that yields ~260 replay R-multiples per
strategy — comfortably over the 40 threshold. CPCV runs on **real broker bars** (the
lazy attach returns genuine MT5 data), so no doctrine is bent: it is the sanctioned
backtest stage operating at research depth instead of smoke depth.
- Zero new risk, zero code, immediate.
- Also de-noises Stage 1 (26 trades → ~260, tighter Sharpe/DSR estimates).
- Cost: one CLI run per sweep; slower per strategy (minutes not seconds).

### Phase 1 — Supervised paper accumulation (engine of the proposal)
Let the **existing autonomous loop run in paper mode** under supervision, with these
controls:

1. **Order-intent floor.** Today every decision is HOLD-forced by provider loss.
   Fix provider capacity first (nvidia_nim 403 keys, cerebras 402, openrouter 429).
   Once ≥1 provider is live, the loop resumes producing decisions; each validated
   proposal flows through validator → arbiter → `order_intents` → PaperBroker.
   PaperBroker already journals closes via `journal_callback` and the e2e paper
   pipeline test exercises exactly this path (validator → PaperBroker → TradeManager
   → PerformanceMonitor → MetaLoop).
2. **Supervision surface.** Daily read-only triage: `provider-circuit status`,
   `positions`, `history`, `llm_requests` error breakdown, outbox PENDING trend.
   No operator intervention inside the loop itself — only gate-and-observe.
3. **Hard caps** (already exist in config/risk layer): 1% risk/trade, bounded loops,
   kill-switch available, deterministic validator blocks bad geometry regardless of
   LLM output. Worst case stays bounded-decimal damage to a paper account.
4. **Accumulation math.** At session cadence (~8 cycles/session, several sessions/week,
   ~2–4 trades/session when decisions flow): **60–120 paper trades ≈ 3–4 weeks** to
   clear walk-forward's 20 broker-reconciled r-multiples with margin; CPCV's 40
   within the first week if ≥1 trade per session lands.

Paper fills come from the broker quote plane with 0 synthetic positions by design;
fills carry the broker's own ticket, so closes reconcile exactly as live trades would.

### Phase 2 — Evidence-gated promotion (doctrine unchanged)
When journal evidence clears the thresholds, the existing gates simply begin to pass
on their own — **no gate relaxation anywhere in this proposal**:
- CPCV ≥40 → combinatorial paths run, PBO/DSR computed honestly.
- walk-forward ≥20 r-multiples → WFE computed, DEAD status blocks entries as designed.
- live-comparison ≥10 closes → divergence check activates.
- `labelled_dataset` grows → future RL/meta-loops train on real labeled outcomes.

The validator keeps treating missing evidence as DEAD/no-evidence — trades earned by
accumulation, never by threshold reduction.

## Supervision duties (the "supervised" part)
- **Daily:** provider circuit + key rotation (the one external dependency), kill-switch
  status, outbox PENDING trend (7 stuck now — must drain or be bounded).
- **Weekly:** run the Phase-0 deep sweep; compare journal vs replay R-multiple
  distributions (divergence = slippage model drift, feed back to TCE).
- **Escalation triggers:** ERROR rate >50% sustained → pause and triage providers;
  outbox PENDING monotonic growth → stop accumulation, drain first; any
  non-paper order intent → immediate kill-switch + audit.

## What this deliberately does NOT do
- Does not lower thresholds (40/20/10 stay).
- Does not admit synthetic, fixture, or self-declared evidence.
- Does not touch the foreign workstream's files to implement (Phase 0 is CLI-only;
  Phase 1 needs no code — provider recovery + loop uptime only).
- Does not enable live execution (`execution_authority` stays false).

## Assumptions / risks
- **Provider recovery is the critical dependency** for Phase 1; without it the loop
  stays HOLD-forced and Phase 0 alone still unlocks CPCV (worth doing regardless).
- Paper fills at broker quotes overstate real fills (no queue/rejection realism) —
  acceptable for evidence gates, flagged in any promotion decision that follows.
- Session-6 chassis conventions (bounded windows, RAM bar) still govern any long run.

## Immediate next actions
1. Run the Phase-0 deep sweep: `python -X utf8 research/training_orchestrator_cli.py backtest --symbol EURUSD --timeframe H1 --bars 5000` (or equivalent sweep entry).
2. Triage LLM providers (separate workstream, blocks Phase 1).
3. Drain/bound the 7 stuck outbox items before accumulation starts.
