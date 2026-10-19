# Q-learning persistence: starved, not broken — plus a durability fix

## Investigating the empty Q-table

Runtime logs show `Q-Table: 0` at `llm_orchestrator.py:9292`, which suggested
`rl_model.json` was never written and learning restarted from zero each boot.
Traced the whole chain before concluding anything:

| Link | Status |
|---|---|
| `RLTrainer.__init__` calls `load_model()` | `research/rl_trainer.py:57` — present |
| `update()` increments `total_updates` | `:280` — before the save check |
| `% 10 == 0` triggers `save_model()` | `llm_orchestrator.py:4712` — present |
| `save_model()` writes JSON | `:349` — worked, but **non-atomically** |
| `update_rl_from_trade` called on close | `:5605` and the `perf.rl_update_callback` at `:849` |

The `%10` rule lives in the *orchestrator*, not the trainer, which is why a
naive probe of `RLTrainer.update()` alone appears to never save.

Verified the full round-trip: 20 updates → save at 10 and 20 → new `RLTrainer`
reloads the state key, its Q-values, and `total_updates` intact. A real
`_build_rl_state`-shaped input produces a stable key
(`vol:bin_0|rsi:bin_2|dir:1|...|sess:LONDON`), not the `default_state` fallback.

**The Q-table is empty for the same reason the contextual bandit was:**
`update_rl_from_trade()` requires `broker_reconciled=True` (`:4654`), and no
real broker-reconciled close has ever occurred. All 151 journal trades are
unbrokered fixtures. No wiring is needed.

## The one real defect: non-atomic save

`save_model()` used a plain `open(path, 'w')`, which truncates in place. A
crash, kill, or full disk partway through `json.dump` left a torn JSON file.
Demonstrated:

```
states persisted: 1
--- crash mid-dump ---
file size now: 33 bytes
next boot: "Failed to load RL model: Unterminated string starting at: line 1 column 14"
           q_table size = 0
```

The exception is caught, so the process survives — but the entire learned
Q-table is **permanently lost** and learning silently restarts from zero. On a
system whose whole point is compounding knowledge across sessions, that is the
worst possible failure mode: it looks like a clean start rather than data loss.

Fixed with temp-file + `os.replace()`, which is atomic on both POSIX and
Windows:

```python
fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".rl_model-", suffix=".tmp")
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
except Exception:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    raise
```

`os.fsync` matters: without it the rename can be durable while the contents are
not, which reproduces the same torn-read problem after a power loss. The
directory is also created if missing, so a configured nested path persists
instead of failing silently into `log.error`.

A reader now only ever observes a complete document.

## Tests

`tests/test_rl_model_persistence.py` — 6 tests:

- state, Q-values and update counter survive a restart
- no `.rl_model-*.tmp` left behind
- a missing parent directory is created
- **a mid-dump failure leaves the previous checkpoint intact** (the regression)
- a corrupt file starts empty rather than crashing the boot
- the production state schema yields a stable, non-fallback key

Verified meaningful: reverting the fix fails exactly
`test_save_creates_missing_directory` and
`test_existing_file_is_never_truncated_by_a_failed_write`; restoring it passes
all 6.

1341 passed in the RL/alpha/research/safety batch.