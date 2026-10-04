"""Stage selected hunks/lines for the payload-fix commit series.

Usage: python stage_tool.py <commit-number> > patchfile
Then:  git apply --cached patchfile   (+ git add for the commit's new files)

Predicates are content-based (not hunk-number based) because each commit
re-stages against a moving index; per-commit diffs are regenerated with
`git --no-pager diff` at apply time.

Ops per hunk (first match wins):
  whole       — stage the hunk unchanged (when an include marker matches)
  keep_plus   — stage only + lines whose text contains one of the markers
                (all '-' lines dropped: the original line stays committed)
  drop_plus_from
              — stage the hunk but drop the consecutive '+' run starting at
                the marker line (used for out-of-set blocks inside my hunks)
  keep_run    — stage '+' lines starting at the anchor marker, continuing
                while each next + line's exact text is in the given set
                (bat blocks: syntactically-required marker-less lines)

A hunk containing no matching include is left unstaged. A hunk matching an
exclude marker is never staged.
"""
from __future__ import annotations

import re
import subprocess
import sys

GIT = ["git", "--no-pager", "-c", "color.ui=false", "diff", "-U3", "--"]


def git_diff(path: str) -> str:
    proc = subprocess.run(GIT + [path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"git diff failed for {path}: {proc.stderr}")
    return proc.stdout


def parse(diff_text: str):
    lines = diff_text.split("\n")
    header, hunks, cur = [], [], None
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        header.append(lines[i])
        i += 1
    while i < len(lines):
        if lines[i].startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = {"head": lines[i], "body": []}
        elif cur is not None:
            cur["body"].append(lines[i])
        i += 1
    if cur:
        hunks.append(cur)
    return header, hunks


def _counts(body):
    old = sum(1 for ln in body if ln.startswith("-") or ln.startswith(" ") or ln == "")
    new = sum(1 for ln in body if ln.startswith("+") or ln.startswith(" ") or ln == "")
    return old, new


def _rehead(head: str, body) -> str:
    old, new = _counts(body)
    return re.sub(r"^@@ -\d+,\d+ \+\d+,\d+ @@",
                  f"@@ -1,{old} +1,{new} @@", head)


def hunk_text(hunk) -> str:
    return "\n".join(hunk["body"])


def plus_runs(body):
    """Yield (start_index, [lines]) for each maximal run of '+' lines."""
    runs, i = [], 0
    while i < len(body):
        if body[i].startswith("+"):
            j = i
            while j < len(body) and body[j].startswith("+"):
                j += 1
            runs.append((i, body[i:j]))
            i = j
        else:
            i += 1
    return runs


def transform(hunk, op):
    body = list(hunk["body"])
    kind = op["op"]
    if kind == "whole":
        return body
    if kind == "keep_plus":
        markers = op["keep"]
        return [ln for ln in body
                if not ln.startswith("+") and not ln.startswith("-")
                or (ln.startswith("+") and any(m in ln for m in markers))]
    if kind == "drop_plus_from":
        marker = op["marker"]
        runs = plus_runs(body)
        drop_idx = None
        for start, run in runs:
            if any(marker in ln for ln in run):
                drop_idx = start
                break
        if drop_idx is None:
            raise SystemExit(f"drop_plus_from marker not found: {marker}")
        # Drop the '+' run containing the marker through end of its run,
        # then continue dropping any further '+' runs with no context
        # between (tail blocks separated only by blank '+' lines).
        out, i, dropping = [], 0, False
        while i < len(body):
            ln = body[i]
            if i == drop_idx:
                dropping = True
            if dropping and ln.startswith("+"):
                i += 1
                continue
            if dropping and not ln.startswith("+"):
                dropping = False
            out.append(ln)
            i += 1
        return out
    if kind == "keep_run":
        anchor, exact_set = op["anchor"], set(op["lines"])
        out, keeping = [], False
        for ln in body:
            if ln.startswith("+"):
                text = ln[1:].rstrip("\r")
                if not keeping and anchor in text:
                    keeping = True
                    out.append(ln)
                    continue
                if keeping:
                    if text in exact_set or any(m in text for m in op.get("also_keep", [])):
                        out.append(ln)
                        continue
                    keeping = False
            if not keeping:
                out.append(ln)
        # drop all '-' lines in keep_run mode (originals stay)
        return [ln for ln in out if not ln.startswith("-")]
    raise SystemExit(f"unknown op {kind}")


CONFIG = {
    "1": {  # payload R1-R5
        "intelligence/llm_orchestrator.py": {
            "includes": [
                {"op": "whole", "inc": ["R5 (payload-flow audit", "R4 (payload-flow audit",
                                        "_collapse_tail_sections", "_TAIL_PROTECTED_KEYS",
                                        "_enforce_request_char_budget", "Whole-request budget (R2",
                                        "truncated for request budget", "minor_context_summary",
                                        "R1 compact mode: tag deterministic-fallback",
                                        "discard_failed_cycle"]},
            ],
        },
    },
    "3": {  # memory outage
        "intelligence/llm_orchestrator.py": {
            "includes": [
                {"op": "whole", "inc": ["outage_failed", "fallback_saved",
                                        "memory batch blocked by provider outage"]},
            ],
        },
    },
    "5": {  # boot watchdog (orchestrator wiring; datasheet lines dropped)
        "intelligence/llm_orchestrator.py": {
            "includes": [
                {"op": "whole", "inc": ["boot_fetch_watchdog", "run_stage_with_deadline",
                                        "boot_stage_outcomes", "def _emit_boot_event"],
                 "drop_plus_from": [
                     "from boot_datasheet import",
                     "def _emit_provider_scorecard",
                     "# Boot datasheet: fold the watchdog outcomes"]},
            ],
        },
    },
    "4": {  # paper fast-boot default
        "mt5_trader.py": {
            "includes": [
                {"op": "whole", "inc": ["def apply_paper_triage_default",
                                        "Fast-boot default for paper sessions"]},
            ],
        },
        "START_DECK.bat": {
            "includes": [
                {"op": "keep_run", "inc": ["Optional compact-context preflight"],
                 "anchor": "REM -- Optional compact-context preflight",
                 "lines": [
                     "REM    (R1 payload audit: rolling-window history + summaries; verify env first).",
                     "",
                     "if /i \"%LLM_TRADER_COMPACT_CONTEXT%\"==\"1\" (",
                     "  call python -X utf8 artifacts\\ctx_probe.py",
                     "  if errorlevel 1 (",
                     "    echo  [X] PREFLIGHT: compact-context probe RED - aborting launch. See artifacts\\ctx_probe.txt",
                     "    pause",
                     "    exit /b 1",
                     "  )",
                     "  echo  [OK] PREFLIGHT: compact-context probe GREEN.",
                     ")"]},
            ],
        },
        "START_TRADING.bat": {
            "includes": [
                {"op": "keep_run", "inc": ["Compact-context probe running"],
                 "anchor": "if /i \"%LLM_TRADER_COMPACT_CONTEXT%\"==\"1\" (",
                 "lines": [
                     "        echo [PREFLIGHT] Compact-context probe running ^(LLM_TRADER_COMPACT_CONTEXT=1^)...",
                     "        call python -X utf8 artifacts\\ctx_probe.py",
                     "        if errorlevel 1 (",
                     "            echo [PREFLIGHT] Compact-context probe RED - aborting launch. See artifacts\\ctx_probe.txt",
                     "            goto :done",
                     "        )",
                     "        echo [PREFLIGHT] Compact-context probe GREEN.",
                     "    )"]},
            ],
        },
    },
    "6": {  # fetcher degradation
        "data/api_manager.py": {
            "includes": [
                {"op": "whole", "inc": ["def _run_bounded"]},
                {"op": "whole", "inc": ["newsapi.get_news\", \"SKIP\""]},
                {"op": "whole", "inc": ["ok, response, err"]},
                {"op": "keep_plus", "inc": ["_ds_note(\"newsapi.get_news\", \"FRESH\""],
                 "keep": ["_ds_note(\"newsapi.get_news\", \"FRESH\""]},
                {"op": "keep_plus", "inc": ["is_quota", "Hot topics rate limited"],
                 "keep": ["if cached and \"data\" in cached:",
                          "NewsAPI failed (%s); serving stale cache",
                          "Hot topics failed (%s); serving stale cache",
                          "_ds_note(\"newsapi.get_news\", \"STALE\", str(e)",
                          "_ds_note(\"newsapi.get_news\", \"EMPTY\", str(e)",
                          "_ds_note(\"newsapi.get_hot_topics\", \"STALE\", str(e)",
                          "return cached[\"data\"]"]},
                {"op": "keep_plus", "inc": ["_ds_note(\"newsapi.get_hot_topics\", \"FRESH\""],
                 "keep": ["_ds_note(\"newsapi.get_hot_topics\", \"FRESH\""]},
                {"op": "whole", "inc": ["fred.get_fred_data\", \"SKIP\""]},
                {"op": "whole", "inc": ["ok, series, err"]},
                {"op": "keep_plus", "inc": ["_ds_note(\"fred.get_series\", \"FRESH\""],
                 "keep": ["_ds_note(\"fred.get_series\", \"FRESH\""]},
            ],
        },
        "core/autonomous_bootstrap.py": {
            "includes": [
                {"op": "whole", "inc": ["from boot_fetch_watchdog import"]},
                {"op": "drop_plus_from", "inc": ["_PREPARE_STEP_DETAILS"],
                 "marker": "def _has_measured_evidence"},
                {"op": "whole", "inc": ["step_elapsed", "_boot_stage(", "steps=",
                                        "record_prepare_rows(details", "get_datasheet().reset"]},
            ],
        },
    },
}


def main() -> None:
    commit = sys.argv[1]
    cfg = CONFIG[commit]
    patch_parts = []
    for path, rules in cfg.items():
        header, hunks = parse(git_diff(path))
        if not hunks:
            print(f"# WARNING: no diff for {path}", file=sys.stderr)
            continue
        out_body = []
        for hunk in hunks:
            text = hunk_text(hunk)
            staged = None
            for rule in rules["includes"]:
                if not any(m in text for m in rule["inc"]):
                    continue
                staged = transform(hunk, rule)
                break
            if staged is None:
                continue
            staged = [ln for ln in staged]  # keep as-is
            if any(ln.startswith("+") or ln.startswith("-") for ln in staged):
                out_body.append(_rehead(hunk["head"], staged))
                out_body.extend(staged)
        if out_body:
            patch_parts.append("\n".join(header + out_body))
    patch = "\n".join(patch_parts)
    if patch and not patch.endswith("\n"):
        patch += "\n"
    sys.stdout.write(patch)


if __name__ == "__main__":
    main()
