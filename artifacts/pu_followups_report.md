# PU-0161 — Four PU-0160 Follow-ups: Prompt Ceiling · Alternating Schedule · Hash-Chain Break · Stop Flag

Date: 2026-10-03 · Branch `feat/ui-pipeline-compact-tabs` · Continues PU-0158 / PU-0160.

All four follow-ups from [pu_ablation_pair_report.md](pu_ablation_pair_report.md) are
implemented and verified. Nothing below re-derives prior work.

---

## 1. Prompt-size ceiling guard (compacts before ~100K chars)

### Root cause (measured, not assumed)

PU-0160 said "parse errors correlate with prompt size" but the threshold was
derived from the wrong metric. Read-only probes over `trading_state.db`
(END-MARKER-PU-0161-PROBE, END-MARKER-PU-0161B-PROBE) separate two numbers that
had been conflated:

| metric | 3 PARSE_ERRORs | 12 successes | boundary |
|---|---|---|---|
| **stored** (JSON-serialized request) | 104.1k–105.9k | ≤102.2k | fails >~103k |
| **content** (sum of message chars) | 93.4k–94.7k | ≤91.8k | — |

The gap is **JSON escaping** (~11%): every quote inside the embedded
market-data JSON is escaped on the wire. `_enforce_request_char_budget` (R2)
budgets *content* at 96,000 — so all three failing requests sat **under** the
96K content budget and R2 passed them through untouched, while the provider
actually received 104K–106K. That is the real failure boundary.

### Fix

`LLMOrchestrator._enforce_request_ceiling()` in
`intelligence/llm_orchestrator.py` — a **serialized-char** guard applied in
`get_decision()` immediately after the R2 content budget:

- ceiling: explicit `max_chars` > `LLM_TRADER_PROMPT_CEILING_CHARS` > **100_000**
  (below the 104K failure boundary, above the ~95K serialized size of a healthy
  s11 request); `<= 0` disables;
- never touches the first system message (role/doctrine) or the final user
  message (live market data) — same contract as R2;
- halves older history turns largest-first with the existing
  `[truncated for request budget]` marker, so a truncated turn can never be
  mistaken for a complete one;
- re-measures per pass so repeated calls never stack markers; logs
  `Prompt ceiling applied: X -> Y chars (cap Z)` on compaction and a WARNING if
  the cap still cannot be met.

### Verification against real s11 prompts

`artifacts/pu_prompt_ceiling_replay.py` (END-MARKER-PU-0161C-REPLAY) replayed
all 15 recorded s11 prompts through the new guard:

- **9/15 over-cap requests compacted**, including all three failure-shaped ones
  (104,131 → 93,310 · 104,766 → 93,939 · 105,898 → 95,077);
- **0 contract violations** — system prompt byte-identical, live payload
  byte-identical, and the current-turn market-data JSON still parses in every
  single case.

### Tests

`tests/test_request_budget.py` — 20/20 green (13 pre-existing R2/R4/R5 + 7 new):
oversized request compacted under cap · current-turn JSON still parses ·
under-cap untouched · env override + disable-on-zero · garbage env falls back to
default · repeated call does not stack markers · degenerate inputs returned as-is ·
**and PU-0160's exact three recorded failure sizes (104,146 / 104,785 / 105,909)
each now compact below 100K with the payload intact.**

---

## 2. Daily alternating ablation/baseline schedule

### Why

PU-0160 ran both arms on the same day **5.5h apart** — its own caveat #1: drift
between windows is confounded with the treatment. The fix is **one arm per UTC
day, alternating**, so the arms run under matched market conditions on adjacent
days.

### Design — `artifacts/pu_ablation_schedule.py` (END-MARKER-PU-0161-SCHEDULE)

- **UTC date, not local** — sessions journal in UTC; a local-date boundary would
  silently skew which arm a run lands on;
- **anchor-based parity**, not `today % 2` — an explicit anchor makes the
  assignment reproducible so a report can classify any historical date. Default
  anchor `2026-10-02` (session-10's ablation date), preserving existing parity;
  negative offsets keep alternating backwards instead of collapsing to day 0;
- **fail-safe direction** — an unresolvable input (garbage anchor, unknown arm)
  resolves to `with_llm`. Shipping a baseline where an ablation was wanted costs
  one data point; the reverse poisons the control arm;
- **opt-in** — disabled unless `PU_ABLATION_SCHEDULE=alternate`, so existing
  single-arm chassis keep their behaviour.

Env: `PU_ABLATION_SCHEDULE` · `PU_ABLATION_ANCHOR` · `PU_ABLATION_ARM`.

### Wiring

Both boot chassis gate on it, **first thing in `main()` — before any slot wait**,
so a mismatched day yields immediately instead of blocking behind another
chassis's gate:

- `artifacts/pu_session10_boot.py` — ablation arm (`no_llm`);
- `artifacts/pu_session11_boot.py` — baseline arm (`with_llm`). Its
  `_session10_done()` additionally accepts "session-10 chose not to run today"
  (schedule says s10 skips, its processes are gone) so the baseline day is not
  deadlocked waiting for a report that will never be written.

### Verification

- schedule selftest **13/13** (strict alternation over 14 days, anchor parity,
  backwards alternation, both skip directions, disabled-never-matches, garbage
  anchor, fail-safe arm, UTC today);
- `artifacts/pu_schedule_gate_harness.py` (END-MARKER-PU-0161-GATE) **4/4** —
  imports each chassis fresh and calls `main()` under a 25s timeout: both yield
  with rc=0 *without spawning a trader* (a regression would fall into the
  multi-hour slot-wait loop and time out), and neither self-skips when the
  schedule is disabled.

---

## 3. Hash-chain break at checkpoint 13907 — investigated, journal verified

### Verdict: the journal data is intact; the alarm was a write-path bug

**Checkpoint 13907 is no longer retained** (window is now 13922..14021, anchor
13921), so the s10-startup break has already rotated out. The same class of
break is present **right now** at `broken_at=13924`.

Read-only probes (`artifacts/pu_chain_probe.py` … `pu_chain_selfheal.py`, all
END-MARKER-footed):

| check | result |
|---|---|
| `payload_json` parses | **100 / 100 — zero corruption** |
| id-walk links valid | 95 / 100 |
| mismatched rows | 5: 13924, 13927, 13942, 13943 (FAILED) · 13947 (COMPLETED) |
| tampering | **none found** — every mismatch is explained by the write path |

### Root causes (two distinct writer bugs)

**A. FAILED rows seeded from the wrong predecessor.** `begin_checkpoint()`
computed a new row's hash from the **last COMPLETED** row, but
`verify_chain_integrity()` walks **every** row in id order. Two consecutive
FAILED rows therefore hashed against the same predecessor — the second row's
stored hash literally duplicated the first's (`745ec5cf… == 745ec5cf…` at
13923/13924), and the id-order walk computed a different expected hash. This is
4 of the 5 breaks, and it is a **false tamper alarm**, not corruption.

**B. Out-of-order completion (row 13947).** `complete_checkpoint()` finalizes a
row's hash, but a row created after it may already have chained off that row's
earlier *placeholder* hash. Candidate search proved it: 13947's stored hash
equals `h(13945's placeholder hash, 13947 payload)` — its predecessor 13945 was
completed *after* it.

### Fixes in `core/graph_state_persistence.py`

1. `begin_checkpoint()` now seeds from the **immediately preceding row by id**
   — the same rule the verifier walks and `complete_checkpoint()` already used;
2. `complete_checkpoint()` now **re-chains successors** after finalizing, from
   their untouched payloads (logged as a WARNING with the successor count).
   Payloads are never rewritten, so tamper detection is unchanged; the common
   path (completing the newest row) has no successors and is a no-op.

### Verification

- `tests/test_graph_state_persistence.py` — **42/42 green** (33 pre-existing +
  9 new): consecutive-FAILED keeps the chain valid · FAILED/COMPLETED interleaved ·
  no duplicate hash values · tamper still detected after the fix · begin seeds
  from immediate predecessor · out-of-order completion stays valid · re-chain
  leaves payloads byte-identical · **re-chain does not launder a tampered payload** ·
  normal completion unaffected;
- **self-heal confirmed on a DB copy** (END-MARKER-PU-0161-SELFHEAL): simulating
  retention advancing past all 5 bad rows and re-anchoring — which is exactly
  what `complete_checkpoint()`'s prune path already does — yields
  `broken_at=None, verified=74/74`. **The live journal recovers on its own as
  retention rotates past id 13947**; no mutation of the tamper-evident log was
  performed or is needed. The copy was discarded; the live journal was opened
  read-only throughout.

### Blast radius

`test_p07_recovery` · `test_graph_checkpointer` · `test_crash_detector` ·
`test_autonomous_bootstrap_paper_gates` — all green.

> Note: this alarm was already downgraded to **advisory in paper mode** by
> `core/autonomous_bootstrap.py` (live still blocks), which is why s10 booted
> despite the CRITICAL. Worth knowing that a **live** boot would have been
> blocked by it.

---

## 4. Trader-side stop-flag (true graceful external shutdown)

### Why

PU-0160 measured that a boot-spawned trader sits in a `CREATE_NEW_PROCESS_GROUP`
with CTRL_C disabled, so the RSS guard's wind-down ladder degrades to
CTRL_BREAK — which the OS honours by killing the group **without running the
trader's cleanup** (exit 0xC000013A). CrashRecovery then replays from the last
checkpoint instead of shutting down cleanly. A stop flag is the missing graceful
path.

### Design — `mt5_trader.py`

- `_stop_file_path()` / `_stop_file_requested()` / `_acknowledge_stop_file()`;
  path from `MT5_TRADER_STOP_FILE` (absolute or relative to repo root), default
  `<repo>/trader_stop.flag`;
- **checked first in the main-loop body**, before the holiday check and any
  cycle work, so a request stops the trader before another order can be placed;
- **peek is side-effect free** — the sleep loops peek every ~1s (both display
  and headless paths) so a request lands within a second rather than after a
  full poll interval, without consuming the flag before the loop body sees it;
- **one-shot and auditable** — on consumption the flag is *renamed* to
  `<path>.consumed` (not deleted) so the request stays on disk, and a restart
  cannot immediately re-trigger a boot→stop→boot loop;
- the reason (first line, ≤200 chars) is journaled in the warning;
- exits via `break`, setting `run_status = "STOP_FLAG"` so the report engine can
  tell an operator stop from a crash — **normal loop exit**, so the `finally`
  block runs in full.

### CrashRecovery cleanup made explicit

`CrashRecovery.shutdown()` was previously reached only via `atexit`. The
`finally` block now calls `_crash_recovery.shutdown()` directly (idempotent —
its `_shutdown_in_progress` guard makes the later atexit call a no-op), so the
shutdown snapshot no longer depends on interpreter teardown ordering.

### Verification

- `tests/test_trader_stop_flag.py` — **12/12 green**: default path · relative
  override · no-flag · **peek has no side effects** · consume reads reason and
  renames to `.consumed` · one-shot · empty/whitespace · first-line-only ·
  consume-without-flag is safe · **structural: check precedes holiday check and
  breaks (not raises)** · distinct `STOP_FLAG` status · both sleep paths poll;
- AST structure probe over the real `try`/`finally`: stop-flag break inside the
  TRY body (lines 4411–5604), check precedes the holiday check, and
  `_crash_recovery.shutdown()` + Guardian stop + supervisor shutdown +
  `finish_run` all present in the FINALLY (lines 5619–5714).

---

## Test summary

Batteries touched by this work, all exit 0:

| battery | result |
|---|---|
| `test_request_budget.py` | 20/20 (7 new) |
| `test_graph_state_persistence.py` | 42/42 (9 new) |
| `test_trader_stop_flag.py` | 12/12 (new file) |
| `test_ablation_harness.py` | 4/4 |
| schedule selftest (`pu_ablation_schedule.py`) | 13/13 |
| schedule gate harness | 4/4 |
| prompt-ceiling replay vs real journal | 9/15 compacted, 0 violations |
| chain self-heal (DB copy) | 74/74 verified after prune |
| combined 12-file regression | 137 passed, exit 0 |

Marker doctrine: every probe artifact is END-MARKER-footed; all journal access
was read-only.

## Artifacts

`artifacts/pu_prompt_size_probe{,2}.py` · `artifacts/pu_prompt_ceiling_replay.py`
· `artifacts/pu_ablation_schedule.py` · `artifacts/pu_schedule_gate_harness.py`
· `artifacts/pu_chain_{probe,rootcause,rootcause2,rootcause3,selfheal}.py`

END-MARKER-PU-0161
