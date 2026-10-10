# Correction: MFE/MAE in the live journal

## What I said earlier (WRONG)

I reported: "`position_observations` = 0 rows → MFE/MAE has never been computed"
and "MFE/MAE is permanently None".

## What is actually true

`core/trading_safety.py:1033` — `record_close()` writes a terminal
`BROKER_EXECUTION` mark *before* computing excursions, precisely so MFE/MAE
cannot be understated. The instrumentation is correct and works.

Verified end-to-end with a realistic open→mark→close sequence:

```
excursions(7) = {'mfe_r': 2.0, 'mae_r': 0.0, 'mark_count': 3,
                 'mark_quality': 'COMPLETE'}
```

## Why the live journal is empty anyway

The 151 `trades` rows were **bulk-inserted fixtures**, not written through the
orchestrator:

| Table | Rows |
|---|---|
| `trades` with `closed_at` | 151 |
| `position_observations` | 0 |
| `trade_lifecycle_events` where `event_type='CLOSED'` | 0 |
| `trade_lifecycle_events` total | 137 (61 PROPOSED, 61 REJECTED, 15 DAILY_RECONCILIATION) |

Zero `CLOSED` lifecycle events for 151 closed trades proves `record_close()`
was never called on them. The payloads self-report
`source: synthetic_fixture_legacy` (150) and `mt5_backtest` (1).

**So the empty table is honest evidence of "no real broker session has run",
not a broken instrument.** The empty journal is the correct signal.

## The one gap that IS real

`ActiveKnowledgeAgent.ingest_trade_reflection()` (`intelligence/active_knowledge_agent.py:520`)
is the only consumer that writes MFE/MAE into the LLM's episodic memory
(`scenario_documents`, `verified_memories`).

It has **no production caller**. Repo-wide search finds only its own definition
plus `tests/test_active_knowledge_agent.py:87`.

`llm_orchestrator.py:5352` calls `record_close()` and then
`self.risk.record_trade_outcome(total_pnl)` — the risk counters update, but
nothing feeds the reflection loop.

Consequence: when a real session does run, excursions will be computed and
journalled correctly, but they will never reach
`scenario_documents` / `verified_memories`, so the retrieval-augmented case
matching that consumes them keeps returning nothing.