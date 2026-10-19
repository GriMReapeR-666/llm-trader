# The contextual bandit is fed, not broken

## The question

`bandit_report` was imported at `intelligence/llm_orchestrator.py:178` and
never called — the same shape as the PPO/GRPO write-only defects fixed in
`9ce10e0f` and `32a3cc1a`. Is it the third instance of the same disease?

## Answer: no, and it must stay that way

Three findings, in order.

### 1. On the live journal it correctly reports nothing

```
labelled_dataset("trading_state.db")
  sample_size : 0
  excluded    : {"unbrokered_trade": 151}

bandit_report("trading_state.db")
  status              : INSUFFICIENT_DATA
  out_of_sample_status: NOT_AVAILABLE
  folds               : 0
```

All 151 trades are excluded because none has independent broker evidence. That
is the guard working, not a failure: `research/research_validation.py:93`
(`_broker_confirmed_tickets`) accepts only the journal's own submission record
(`order_intents.mt5_ticket` or a `broker_execution_reports` ticket). A row that
merely *claims* `source="broker"` proves nothing, because the same party that
would forge it wrote every field on the row.

### 2. With evidence present it learns correctly

Built a fixture satisfying the full evidence contract (200 trades,
`order_intents.mt5_ticket`, `trade_labels`, valid `feature_snapshot_v1`
schema). Two arms were profitable (`fvg_fill` +1.4R, `breakout_retest` +0.9R),
two were not (`mean_rev` -1.2R, `news_fade` -0.8R):

```
status              : READY_FOR_REVIEW
out_of_sample_status: EVALUATED_PURGED_WALK_FORWARD
folds               : 2
  fold 1: taken=20 skipped=20 coverage=0.50 sel_exp=1.15
  fold 2: taken=20 skipped=20 coverage=0.50 sel_exp=1.1125

setups TAKEN in final fold: ['breakout_retest', 'fvg_fill']   <- exactly the profitable pair
```

The bandit isolates the profitable arms and rejects the losing ones. The
machinery is sound; it is starved, not broken.

### 3. It will feed itself once real trading runs

The evidence tables are written by the live path:
- `core/order_gateway.py:1261` — `UPDATE order_intents SET mt5_ticket=...`
- `intelligence/llm_orchestrator.py` — 7 `record_execution_report` call sites

So the bandit needs no new wiring. It starts producing folds on the first
journal that contains real broker-reconciled closes.

## Why the dead import was removed, not wired up

`bandit_report` returns `execution_authority: False` and `promotion_safe: False`
by construction (lines 61, 94-96). Its own docstring calls it "a research
report, not a permission gate". It is designed to be run by an operator:

```
python mt5_trader.py journal bandit-status
```

Importing it into the live pulse would imply an influence on trading decisions
that it is explicitly forbidden from having, and would invite exactly the wrong
"fix" later — treating an advisory ranking as a confidence multiplier. The
import is removed with a comment recording the decision.

Note the online update inside the loop is honest (line 86-88): a test row is
only folded into the context *after* its outcome is revealed, so no future
value can leak into its own selection.

## Verification

`tests/test_bandit_evidence_contract.py` — 6 tests:
- broker evidence present → rows admitted, zero exclusions
- evidence absent → **all** rows excluded (fail-closed preserved)
- bandit learns and takes exactly the profitable arms, positive expectancy
- authority flags stay False even when fully evaluated
- too few rows → `INSUFFICIENT_DATA`, never a fabricated ranking
- the orchestrator never imports the bandit

13 passed across the bandit/research suites.