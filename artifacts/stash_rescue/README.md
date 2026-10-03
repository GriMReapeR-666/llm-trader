# Stash rescue archive (dropped stash 2098d220)

Provenance snapshot: 24 files whose only surviving copy lived in
`stash@{0}` — "On feat/ui-pipeline-compact-tabs: foreign+out-of-set snapshot
during payload-fix commits" — dropped on 2026-09-30 after the 11-commit
payload-fix series landed. Rescued pre-drop to `artifacts/stash_rescue/`
and committed here so recovery does not depend on the dangling commit
`2098d22041860e2f3d8067119cf52e0ad16201d0` surviving the next `git gc`.

Contents:

- `artifacts/commit_work/*.diff` (10) — filtered working-diffs used to stage
  the session's commits (apimgr, router, memory, memoutage, orch, trader,
  bootstrap, bats, runbook) plus `stage_tool.py`, the hunk-splitting helper.
  `runbook.diff` is empty because the runbook diff was staged without a
  filtered-patch round-trip.
- `artifacts/scheduler_tuning_report_2026*.json` (14) — tuning reports from
  2026-09-27 to 2026-09-28T07:06Z, predating the earliest copy that survives
  elsewhere in the worktree.

Notes:

- The doubled `artifacts/` path prefix (`stash_rescue/artifacts/...`) is
  intentional: files were restored under the rescue root exactly as they sat
  in the stash, relative to the repo root.
- Tracked siblings missing from the worktree (test_engine2-8.py,
  test_cycle.py, etc.) were not rescued: tracked files always exist in the
  stash's first parent, recoverable via `git show 2098d220^1:<path>` while
  the dangling commit remains reachable.
- 23 of the 24 files are historical evidence only; none are imported by
  project code. `stage_tool.py` documents the hunk-staging method.
