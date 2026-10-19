# MT5 attach retry — production boot log scan

Target: `artifacts/watch_guarded_boot_session.log`

- **file**: `artifacts/watch_guarded_boot_session.log`
- **bytes**: 1,074,849
- **lines**: 10,966

## Retry signatures searched

Each signature below was searched case-insensitively across the entire log; all counts are **0**.

- `_ATTACH_SLEEP`
- `sleep(0.5)`
- `sleep(1.0)`
- `0.5, 1.0`
- `mt5.initialize()`
- `initialize()`
- `terminal_info()`
- `_ensure_mt5_attached`
- word `attach`
- `MarketData`
- `backoff`
- `back_off`

Only **2** lines in the whole log match the generic pattern `retry in <s>`, and both are **SeekingAlpha HTTP retries**, not MT5 attach:

- `2026-10-18 12:57:09,677 WARNING SeekingAlphaProvider: ... retry in 0.7s`
- `2026-10-18 14:14:18,728 WARNING SeekingAlphaProvider: ... retry in 0.5s`

## Observed result

- **MT5 attach retry fires 0 times in this boot log**, observable or otherwise.
- The only retries in this log are the two SeekingAlpha network retries above.

## Why 0 here

This is a **paper-mode autonomous run on 2026-10-18** that never reached the real MT5 attach path. Relevant log evidence:

- `2026-10-18 01:14:20,942 INFO main: Paper market-data session established (terminal-only, no credential login): MetaTrader 5`
- `2026-10-18 01:14:24,176 INFO root: Enhanced calendar unavailable; no calendar fallback is trusted: Could not find MT5 terminal DLL. Please specify dll_path parameter.`
- `2026-10-18 01:20:04,723 WARNING core.autonomous_bootstrap: Enhanced MT5 Calendar init skipped: Could not find MT5 terminal DLL. Please specify dll_path parameter.`

So the 0 count is **an artifact of this log's paper-mode / no-terminal-DLL execution**, not a measurement of real-world retry frequency.

## True bound (source of truth, not from this log)

From `data/market_data_provider.py:198-239`:

- `_ATTACH_MAX_ATTEMPTS = 3`
- `_ATTACH_BACKOFF_S = (0.5, 1.0)`
- `_ATTACH_SLEEP = time.sleep`
- `_ensure_mt5_attached` loops `for attempt in range(_ATTACH_MAX_ATTEMPTS)...` with structural fast-fail guards.

Per single `_ensure_mt5_attached(...)` call:

- Already attached: 1 `terminal_info()` check, **0 initializes, 0 sleeps**, returns True.
- 1 transient refusal then success: **2 init calls, 1 sleep (0.5 s)**, succeeds.
- 2 transient refusals then success: **3 init calls, 2 sleeps (up to 1.5 s cumulative)**, succeeds.
- Genuinely absent terminal: **3 init calls, 2 sleeps (up to 1.5 s cumulative)**, then **fails closed with False**.
- No `initialize` attribute or raising `initialize()` on first attempt: **0 sleeps**, fast-fail.

So the retry **cannot exceed 3 init calls, 2 sleeps, or ~1.5 s per attach call**, regardless of behavior of the terminal.

## Test evidence of that bound

`tests/test_mt5_attach_retry.py` pins the contract:

- `test_attach_retries_until_terminal_boots`: `_FakeTerminal(boot_attempts=2)` → `term.init_calls == 3`, `no_sleep == [0.5, 1.0]`
- `test_attach_succeeds_first_try_without_sleeping`: `term.init_calls == 1`, `no_sleep == []`
- `test_persistent_failure_is_bounded_and_fails_closed`: `term.init_calls == _ATTACH_MAX_ATTEMPTS`, `no_sleep == [0.5, 1.0]`, returns False
- `test_no_initialize_attribute_keeps_fast_fail`, `test_raising_initialize_fails_fast_without_retry`, `test_already_attached_never_calls_initialize`: 0 sleeps

Ran now:

- `python -m pytest tests/test_mt5_attach_retry.py -q` → **7 passed**, RC 0.

## Checks run

- Log scan of `artifacts/watch_guarded_boot_session.log`: RC 0, results above.
- Source read of `data/market_data_provider.py:198-239`: retry ladder and loop confirmed.
- `python -m pytest tests/test_mt5_attach_retry.py -q`: 7 passed, RC 0.

No files were edited for this task.

## Limitations

- This is a **paper-mode** boot log that did not exercise the MT5 attach path; the 0 observed count is specific to this execution, not a general production frequency.
- No live MT5 terminal was attached in this environment, so I could not produce a live empirical retry count either.
- Strongest available evidence is the **code bound + passing retry tests**; the log confirms the absence only for this particular run.
