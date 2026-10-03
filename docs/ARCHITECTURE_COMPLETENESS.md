# Architecture Completeness Runbook

Short operator guide for the market-surviving, self-improving spine. Code never fakes live readiness clear.

## Doctrine

- LLM / memory / causal / RL / research never get `order_authority`. Only `RiskController` + deterministic gates create intents.
- Profitability is proven offline, then staged via edge lifecycle: `CANDIDATE → MEASURED → STAT_PASS → REPLAY_PASS → DEMO_MATCH → TINY_LIVE → FULL`.
- Survival before growth: `account_growth` may only reduce risk until promotion gates are green.
- Live stays operator-gated: MT5 auth, samples, tiny-live approve, security clear, `LLM_TRADER_ALLOW_LIVE`.

## Pillars shipped

| Pillar | Module / surface | Role |
|---|---|---|
| Profitability foundation | `profitability_foundation.py`, `/api/brain/profitability` | Expectancy, walk-forward, ruin, Kelly cap, sample gates (advisory) |
| Cycle brief | `cycle_brief.py`, `/api/brain/cycle-brief` | One auditable narrative per pulse |
| Opportunity fusion | `opportunity_fusion.py` in `_validate_proposal` | Macro setups fail closed without agreeing causal edges |
| Causal propagate | `causal_propagate.py` | Multi-hop path score + `path_id` (advisory) |
| Macro observations | `macro_observations.py` | FRED/calendar bound to catalog entities |
| Data source pipeline | `data_source_pipeline.py` | FRED/NewsAPI/optional providers → observations + candidate graph sources |
| Realtime news ingest | `realtime_news_ingest.py`, `/api/brain/realtime-news` | Reuters/Fed RSS + NewsAPI/SA → CANDIDATE sources; optional LLM extraction |
| Edge replay floors | `CausalEdgeBacktestOptimizer.apply_replay_floors` | STAT_PASS → REPLAY_PASS only after event-aligned backtest floors |
| Memory lessons | `TradingMemory.format_approved_lessons` + review queue | Journal-backed APPROVED lessons only |

## Data source processing (causal / events)

| Source | Fetched in | Causal / event role |
|---|---|---|
| MT5/configured calendar | `fetch_data`, `event_monitor` | Only source that schedules `event_reaction` measurement + blackout |
| FRED (full catalog series) | `fetch_data`, `event_monitor` | `macro_observations` + `fred_observation` graph sources + entity expansion |
| NewsAPI / hot topics | `fetch_data`, `event_monitor` | Candidate narrative graph sources (never blackout) |
| Reuters / Fed RSS | `realtime_news_ingest.py`, `event_monitor` | Allow-listed RSS → candidate sources + entity watch expansion |
| OpenBB / Seeking Alpha / Portfolio123 | `event_monitor` (optional) | Best-effort candidate ingest when clients online |
| MT5 OHLC/DOM | `fetch_data` | Price anchor for reaction windows + correlation returns |

Doctrine: non-calendar feeds never invent release blackouts or order permission.

## Operator live track (parallel, not code-bypassable)

1. Start/auth MT5; run `broker-preflight`; approve per-symbol `execution_policy`.
2. Paper/demo until `sample_gate` reports `READY_FOR_RESEARCH`.
3. Persist research bars for replay floors: store key `causal_replay_bars` (list of OHLC dicts with timestamps).
4. Confirm readiness report still BLOCKED until MT5 auth, samples, tiny-live, and security evidence exist.
5. Security clear + tiny-live approve + `LLM_TRADER_ALLOW_LIVE=true` only when promoting to live.
6. Review memory candidates: `python mt5_trader.py memory-review ...` — approval does not grant orders.
7. Inspect cockpit: `GET /api/brain/cycle-brief`, `GET /api/brain/profitability`, `GET /api/brain/realtime-news`, `GET /api/brain/observability`.
8. Optional LLM claim extraction from news: set `TRADING_REALTIME_NEWS_EXTRACT=true` (CANDIDATE only; never order authority).

## Self-improvement schedule

`self_improvement` writes `last_self_improvement` with memory quality, optional offline Kronos RL, profitability report, and candidate review queue. All payloads keep `execution_authority: false`.

## What we will not do

- Autopilot from catalog text or web claims.
- Live Kelly upsizing from LLM confidence.
- Silent readiness bypass.
