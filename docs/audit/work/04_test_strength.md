# 04: Test strength (mutation and property-based testing)

**Date:** 2026-10-04 · **Commit:** `3e7baf1` · Raw results, mutator and property tests are in [`mutation/`](mutation/).

## Method
- **Mutator:** a custom AST/line mutator (`mutation/mutate.py`), used because mutmut doesn't handle this repo's `sys.path` layout. It applies one mutation at a time:
  - comparison flips (`<`↔`<=`, `==`↔`!=`, `is`↔`is not`, `in`↔`not in`);
  - `and`↔`or`, `min`↔`max`, `True`↔`False`, numeric constant ×2 or +1;
  - negating `if` conditions and dropping `not`;
  - arithmetic operator swaps;
  - deleting `reasons.append(...)` lines.
- **Workers:** three, each on its own copy of the repo. The target file is restored after every mutant and verified at the end (all `restore-check OK`). Your checkout was never touched.
- **Test selection:** each mutant runs the module's *focused* tests (the files that import it) with a timeout. **Survivors were re-run against a wider set**, so "survived" means survived both sets.
- **Score** = killed ÷ (killed + survived). Errors and timeouts (mutants that fail to import, or hang) are excluded.
- **Property tests:** written by the review agent with `hypothesis` (`mutation/props/test_props.py`) and re-run by me. Every counterexample was checked against the code.

## Results
| Module | Mutants | Killed | Survived | **Score** | Coverage (Part 1) |
|---|---|---|---|---|---|
| `core/trading_safety.py` (validator, arbiter, store) | 313 | 148 | 157 | **49 %** | 83 % |
| `core/margin_safety.py` | 190 | 84 | 106 | **44 %** | 80 % |
| `core/order_execution.py` (5 safety functions) | 194 | 59 | 98 | **38 %** | 40 % |
| `core/risk_manager.py` (15 safety functions + sample) | 488 | 158 | 211 | **43 %** | 72 % |
| `data/kill_switch_latch.py` | 77 | 53 | 24 | **69 %** | 94 % |
| `operations/autonomy.py` | 95 | 65 | 30 | **68 %** | 91 % |
| **Total** | **1,357** | **567** | **626** | **48 %** | |

**Coverage overstates protection by roughly 25-35 points.** The lines run, but in most cases nothing asserts what they do. Fewer than half of the bugs that could be planted in the safety modules would be caught by the 4,300-test suite.

## Weakest functions (score)
| Function | Score | What a surviving mutant means in practice |
|---|---|---|
| `DeterministicValidator.confluence` | **0 %** (0/31) | The confluence score can be computed in any way at all. |
| `exposure_buckets` · `open_bucket_risk` | **0 %** | The correlated-exposure buckets are untested. |
| `RiskManager.check_drawdown` · `check_active_trade_limit` | **0 %** | The drawdown check and the max-positions cap can be inverted. |
| `RiskManager.get_drawdown_multiplier` | 14 % | The drawdown size ladder (1.0/0.75/0.5/0.25/0) can be changed freely. |
| `RiskManager.get_streak_multiplier` · `record_trade_outcome` | 16 % · 18 % | Streak sizing (up to 1.8×) and the loss-streak counter are effectively unpinned. |
| `RiskManager.check_weekly_circuit_breaker` | 20 % | Doubling the 6 % weekly cap, inverting `min`/`max`, or `/`→`*` on the drawdown all survive. |
| `validate_order_params` · `emergency_market_exit` | 25 % · 25 % | See below. |
| `MarginSafetyGate.assess` | 41 % | See below. |
| `PortfolioRiskArbiter.permit` | 49 % | The daily, weekly and open-risk comparisons (`trading_safety.py:1352-1365`) can be flipped or sign-swapped. |
| `DeterministicValidator.validate` | 64 % | The best of the set, but see the single-gate deletions below. |

## Most dangerous survivors
1. **Every individual validator gate can be deleted unnoticed:** `market not open`, `symbol not tradable`, `spread too wide`, `stop too close`, `reward/risk below setup minimum`, `insufficient deterministic confluence`, `price moved beyond planned-entry tolerance`, `no completed-candle trigger`, `setup excludes high-impact event window`, `setup recently considered` (`trading_safety.py:1013-1052`). The tests only exercise markets where several gates fail at once, so no single gate is pinned.
2. **Order geometry isn't pinned** (`order_execution.py:193-212`). You can invert the `side == "buy"` branch, flip the SL/TP side checks (`sl >= price`, `tp <= price`), double the 5 % maximum SL distance, or change the 0.5 ATR minimum stop, and every test still passes.
3. **Risk-limit arithmetic:**
   - `permit()` has surviving mutants on `daily >= equity*…`, `weekly >= …`, `open_risk + new_risk > …` (`>` → `>=`, `+` → `-`, `*` → `/`);
   - `check_daily_drawdown` and `check_weekly_circuit_breaker` survive `>=` → `>` and `return True` → `False` on the zero-equity guard.
4. **Margin thresholds:** doubling `max_margin_to_equity` 0.35 → 0.7, `min_margin_level_percent` 300 → 600, the stop-out buffer 200 → 400 and `min_stop_out_buffer_r` 2 → 4 all survive (`margin_safety.py:21-27`). Malformed-input rejections (symbol, account snapshot, risk, margin) can be deleted (`:124-139`).
5. **Autonomy:** `force_level` with an unknown level isn't tested (`autonomy.py:154`). The kill-switch latch's checkpoint loader survives `and` → `or` (`kill_switch_latch.py:93`).

## Property-based tests: 7 properties, 5 failed (all real)
| Property | Counterexample | Code | Status |
|---|---|---|---|
| P1: a position never risks more than 1 R | 0.03 lot risks $1.125 against a $1.00 cap (equity $100) | lot rounding in `calc_position_size` | **Real.** Matches prior F-RM-1. At the broker minimum lot it should refuse, not round up. |
| P2: the drawdown ladder fails closed | equity NaN, 0 or negative gives multiplier **1.0 (full size)** | `risk_manager.py:454-455` | **Real 🟠.** An unreadable or blown account sizes at 100 %. |
| P3: the validator rejects any failing gate | `changepoint_probability = NaN` is **approved** | `trading_safety.py:1116-1118` | **Real 🟡.** Prior F-TS-8, still open. |
| P5: order parameters accept only sane values | NaN price/SL/TP accepted | `order_execution.py:192-221` | **Real ⚪.** Upstream `v > 0` checks catch it today. |
| P6: the latch only clears on explicit calm | a latched checkpoint that is corrupt after a crash comes back **unlatched** | `kill_switch_latch.py:89-98` (`_load` swallows the error and starts blank) | **Real 🟠.** Combined with 06 F5 (the two latch copies tear the file), a halt can disappear on restart. It should fail closed (latched). |
| P4: arbiter approvals respect limits | (passed) | | |
| P5b: pending orders are never marketable | (passed) | | |

## ~~Flaky test found~~ (corrected)
`tests/test_p0_gates.py::TestOverfitGate::test_overfit_critical_blocks_trade` failed once in the mutation harness's baseline check. **Correction (Phase 1):** the test is deterministic (0 failures in 8 parallel runs). The failure came from a worker copy that still held a mutant from the run aborted by the rate limit, not from the test.

## Tests to add (highest value first)
1. **Validator: one gate at a time.** Parametrize over every reason string: start from an all-pass `market` dict, break exactly one input, and assert `approved is False` and that the specific reason is present. Also assert that the all-pass fixture *is* approved (catches over-rejection). This kills about 40 survivors.
2. **Arbiter boundaries:** for daily, weekly and open-risk limits, check exactly at the limit, at limit − ε and at limit + ε, with pending risk included once 06 F1 is fixed.
3. **Order geometry table:** `{buy, sell} × {SL above, SL below, SL equal} × {TP above, TP below}` plus the 5 % and 0.5 ATR boundaries, and NaN/inf inputs.
4. **Margin thresholds:** one test per constant at its boundary, and one per malformed-input reason.
5. **Risk ladders:** a table test of drawdown multiplier and streak multiplier against expected values, including NaN, 0 and negative equity (expect 0.0, i.e. fail closed).
6. **Latch persistence:** latch, corrupt the file, restart, expect latched; latch from copy A, read from copy B (C1).
7. Turn the property tests in `mutation/props/test_props.py` into permanent tests under `tests/property/` once P1, P2, P3 and P6 are fixed.
8. Run the mutator (or mutmut once the package layout is fixed) in CI on the six safety files, and fail if the score drops below a ratchet.
