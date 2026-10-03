# quant_knowledge_framework — End-to-End Validation Report

**Date:** 2026-09-29 · **Verdict: GREEN (38 PASS / 0 WARN / 0 FAIL)**
**Method:** read-only live validation against the real knowledge base, via marker-verified probe `artifacts/pu_qkf_e2e.txt` (END-MARKER-PU-0079; path-normalization diff in `artifacts/pu_qkf_diff.txt`, PU-0080). Full test suite: `artifacts/pu_qkf_pytest9.txt` (PU-0078).

## Scope

- **CLI:** real subprocess invocations of the user entry points.
- **Agent:** in-process battery over every tool class on the real 922-document KB.
- **Data integrity:** cross-checks between `index.json`, `search_index.json`, and disk.
- **Mutation paths:** classify-stage idempotence and CLI `reclassify` in isolated sandboxes (real data untouched).
- **Test suite:** full pytest run.

Excluded by design: network-dependent `download`, and live `build`/`transcribe` against the real KB (both mutate; their stages were validated in sandboxes and by unit tests instead).

## 1. CLI (real subprocesses)

| Check | Result |
|---|---|
| `stats` exit 0, per-topic table | PASS (0.41s) |
| `search "kelly criterion"` returns hits | PASS (3 hits, 0.37s) |
| `search` no-match exits 0 with "Found 0 results" | PASS |
| `--help` lists all 8 commands | PASS |
| Piped `agent` shell session (tools → exec → chain → exit) | PASS (4.90s) |
| agent shell lists 18 tools | PASS |
| agent shell exec returns EV 0.65 | PASS |
| agent shell chain succeeds | PASS |

## 2. Agent battery (in-process, real KB)

Discovery: **18 tools** (5 knowledge/pipeline + 13 quant-module tools).

| Tool class | Check | Result |
|---|---|---|
| knowledge.search | "volatility" | PASS — 158 hits |
| knowledge.search | AND query "kelly criterion" | PASS — 3 hits |
| knowledge.classify | HMM/regime text | PASS — `01_Regime_Detection` |
| knowledge.list_topics | 6 topics | PASS |
| knowledge.get_content | real KB file (540 chars) | PASS |
| hurst.calculate | trending AR(1) φ=0.8 | PASS — H=0.787, trending |
| hurst.calculate | random walk | PASS — H=0.500 |
| hmm.classify | auto-fit on two-volatility blocks | PASS — `high_vol`, conf 1.00 |
| kelly.calculate | 0.6 / 1.5 / 0.25 | PASS — f*=0.3333, P(≥50% DD)=0.00781 (Thorp) |
| hrp.optimize | 3 iid assets | PASS — [0.354, 0.325, 0.321] ≈ ⅓ |
| expectancy.r_multiple | 100→110, stop 95 | PASS — R=2.0, dollar_profit=10.0 |
| drawdown.analyze | golden curve | PASS — max-DD = 40/120 |
| almgren_chriss.calculate | 10k vs 1M ADV | PASS — positive impact |
| dsr.calculate | SR 0.10, n=252, 1000 trials | PASS — DSR=0.048, SR₀=0.205 (rejected) |
| triple_barrier.label | 6%/5% path | PASS — label 1, exit bar 5 |
| wfo.optimize | pure noise | PASS — robust=False |

## 3. Data integrity

| Check | Result |
|---|---|
| `index.json` structure (6 topics) | PASS |
| Every `index.json` path exists on disk (0 missing) | PASS |
| `index.json` counts ≡ disk counts (922 docs: 41/83/55/39/190/514) | PASS |
| `search_index.json` doc_list ≡ disk `.txt/.md` set (922) | PASS (after comparing resolved paths; the index stores config-relative path strings, same convention as `index.json`) |
| CLI `stats` total ≡ `index.json` total (922 = 922) | PASS |
| `transcriptions/` drained (post-classify idempotence state) | PASS |
| Classify-stage re-run flagged `idempotent_noop` + fresh index (sandbox) | PASS |
| CLI `reclassify` on a sandbox copy of the real KB exits 0 (514 archive files, 0 movable — correct: v2 keyword rules already matched v1 placements) | PASS |

## 4. Test suite

**143 passed** in ~9s (`PYTHONDONTWRITEBYTECODE=1 python -X utf8 -m pytest tests -p no:warnings -n0 -q`).

## Context: gap-report items validated live

This session's fixes, previously verified by unit tests, now also verified end-to-end on the real system: Hurst lag-difference math (H=0.787 trending / 0.500 walk), HMM fit+auto-fit flow, module-registry auto-population and QuantAgent bridge (18 tools), real LdP HRP, Bailey/LdP DSR (probability semantics, multiple-testing rejection), Thorp Kelly drawdown risk, honest `dollar_profit`, idempotent classify stage, lazy agent singleton, and the tokenized search index (warm query ~99ms vs full-file-scan previously).

## Known limitations (not failures)

1. **`download` untested live** — requires network access to YouTube; out of scope for a read-only validation.
2. **`build` untested against the real KB** — mutating; validated in sandboxes + unit tests.
3. ~~**Transcribe-stage overlap** — stage 2 re-parses `.srt/.vtt` that the downloader already converted to `transcript.txt`.~~ **RESOLVED after this report (2026-09-29, PU-0081/0082):** stage 2 now harvests the downloader's `transcript.txt` directly, parses only missing/stale subtitles, runs whisper only when real audio/video files exist, and skips transcripts already collected or already classified — full suite now **158 passed** (PU-0085), verified by the one-command smoke runner below.
4. **Search index path convention** — paths stored config-relative; resolution-aware consumers must normalize (as the E2E diff initially demonstrated the hard way).
5. **Search is lexical only** — prefix/token matching, no semantic ranking.

## Artifacts

- `artifacts/pu_qkf_e2e.py` / `artifacts/pu_qkf_e2e.txt` (PU-0079) — E2E probe + verified output
- `artifacts/pu_qkf_diff.py` / `artifacts/pu_qkf_diff.txt` (PU-0080) — path-form diff
- `artifacts/pu_qkf_pytest9.txt` (PU-0078) — test suite
- `quant_knowledge_framework/smoke_test.py` — one-command rerun of this validation (pytest + E2E + transcribe + download probes); writes `artifacts/qkf_smoke_report.txt` (final run verified PU-0085/0086: GREEN — 158 tests, 38 E2E checks over 19 tools, 17 transcribe, 12 download checks)
- `artifacts/pu_qkf_download_probe.py` — download-stage probe (stubbed yt-dlp, no network)
- `knowledge.smoke_test` agent tool — the agent shell can re-run the smoke suite itself (verified via agent path, PU-0086)
- Session probes: PU-0063/0064 (gap report), 0065/0066 (Hurst/HMM), 0067/0068 (registry bridge), 0069/0070 (HRP/DSR), 0071 (numeric suite), 0072 (Kelly/README), 0073/0074 (classify idempotence), 0075/0076 (lazy singleton), 0077 (search index)
