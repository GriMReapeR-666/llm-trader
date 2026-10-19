# Retry discipline review: research/alpha_backtest_harness.py vs data/market_data_provider.py

## What I read
- `research/alpha_backtest_harness.py`: `_initialize_with_env`, `load_mt5_bars` attach/init block.
- `data/market_data_provider.py`: current `_ensure_mt5_attached` and the retry constants we just aligned/pinned.

## Current state of `load_mt5_bars` retry logic
- It does its own `terminal_info()` -> None check, then runs a local retry loop:
  - `for _attempt in range(3):`
  - tries bare `_mt5.initialize()`, then `_initialize_with_env(_mt5)`
  - on failure: `_init_err = _mt5.last_error()`; `time.sleep(5.0)`
- So:
  - retries up to 3 attempts ✅
  - fails closed with `RuntimeError` on final failure ✅
  - but backoff is a flat `time.sleep(5.0)` per attempt ❌ (not the `0.5, 1.0` ladder)
  - no structured exception fast-path matching the same semantics as `_ensure_mt5_attached` ❌ (raises RuntimeError on any final failure, even on raising init)
  - uses `_mt5.last_error()` as the failure payload, which is MT5-binding-specific (not present in the same form in `data/market_data_provider.py`) ❌

## The aligned discipline in `data/market_data_provider.py`
- Constants:
  - `_ATTACH_MAX_ATTEMPTS = 3`
  - `_ATTACH_BACKOFF_S = (0.5, 1.0)`
  - `_ATTACH_SLEEP = time.sleep`
- Retry semantics:
  - transient False/None init outcomes are retried on the ladder
  - raising `initialize()` fails fast with a logged warning and `False` (no retry)
  - missing `initialize` attribute fast-fails
  - final persistent failure logs a single warning and returns `False`
  - already attached: no init, no sleep
- This is the single source-of-truth retry discipline I should align to.

## Alignment plan
1. Centralize the retry constants where both modules can see them, ideally by importing from `data.market_data_provider`:
   - `_ATTACH_MAX_ATTEMPTS`, `_ATTACH_BACKOFF_S`, `_ATTACH_SLEEP`
2. Replace `load_mt5_bars`' local init retry with the same shape and backoff:
   - same attempt bound
   - same backoff ladder
   - same exception-vs-transient distinction (raising init = no retry / fail fast; transient False/None = retry)
   - same final-failure log shape: attempt number + error context
3. Keep `_initialize_with_env` only for the credentialed retry step, and only after a transient bare init failure, not as a parallel first attempt.
4. Do not introduce `_mt5.last_error()`-style behavior into the shared retry contract; prefer the same “attempt N failed with exception: …” / “attempt N refused, retrying in Xs” logging as the attach helper.
5. Preserve the teardown semantics: `initialized_here` + `finally: _mt5.shutdown()` stays, because this CLI path explicitly owns the session lifetime; alignment is about the retry ladder and fail-closed logging, not about session ownership.

## What would change concretely
- `research/alpha_backtest_harness.py`:
  - replace the `time.sleep(5.0)` backoff with the `_ATTACH_BACKOFF_S` ladder
  - replace the ad-hoc local retry loop with the same structured retry/except shape as `_ensure_mt5_attached`
  - pull the retry constants from `data.market_data_provider` instead of hardcoding `range(3)` and the flat sleep
- Expected behavioral result after alignment:
  - cold terminal refuse on attempt 1, then success on retry: 2 init calls, sleep ~0.5s, then attached
  - persistent refusal: 3 init calls, sleeps 0.5s then 1.0s, then fail-closed
  - raising init: fail fast, no sleep, logged warning with exception
  - already attached: 0 init calls, 0 sleeps

## Not changing
- I am not switching `load_mt5_bars` to call `_ensure_mt5_attached` directly as the attach mechanism, because `load_mt5_bars` explicitly owns initialize/shutdown for standalone CLI use and has a credentialed-env retry path. The alignment is on retry discipline, not on merging the two call sites into one.

## Follow-up
- After patching, run any relevant harness/backtest tests that touch `load_mt5_bars`/`_initialize_with_env`, and add/verify at least a unit shape check that the harness retry uses the same ladder constants and fail-closed logging shape as the attach helper.
