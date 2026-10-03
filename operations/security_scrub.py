"""Safe Git history scrub utility for risky credential files.

This module removes ``.env``, ``accounts.json``, and ``config.ini`` from
Git history using ``git filter-repo`` (preferred) or ``git filter-branch``
(fallback).  It never prints secret values.  The operator must review the
result and force-push after verification.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


RISKY_PATHS = {".env", "accounts.json", "config.ini"}


def _run(cmd: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _check_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def _find_risky_history(root: Path) -> List[str]:
    result = _run(
        ["git", "-C", str(root), "log", "--all", "--format=", "--name-only", "--"],
        check=False,
    )
    paths: List[str] = []
    if result.returncode != 0:
        return paths
    for raw in result.stdout.splitlines():
        relative = raw.strip()
        if not relative:
            continue
        name = Path(relative).name.lower()
        if name in RISKY_PATHS or name.endswith(("secrets.json", "credentials.json")):
            paths.append(relative)
    return sorted(set(paths))


def _has_filter_repo() -> bool:
    return _run(["git", "filter-repo", "--help"], check=False).returncode == 0


def scrub_history(root: Path, *, dry_run: bool = True) -> Tuple[bool, str]:
    root = root.resolve()
    if not _check_git_repo(root):
        return False, "Not a Git repository"

    risky = _find_risky_history(root)
    if not risky:
        return True, "No risky files found in Git history"

    if dry_run:
        return False, (
            f"DRY RUN: would remove {len(risky)} risky path(s) from history:\n"
            + "\n".join(f"  - {p}" for p in risky)
            + "\nRe-run with --apply to perform the scrub."
        )

    if _has_filter_repo():
        cmd = [
            "git", "filter-repo",
            "--path", ".env",
            "--path", "accounts.json",
            "--path", "config.ini",
            "--invert-paths",
            "--force",
        ]
    else:
        cmd = [
            "git", "filter-branch", "--force", "--index-filter",
            "git rm --cached --ignore-unmatch .env accounts.json config.ini",
            "--prune-empty", "--tag-name-filter", "cat", "--", "--all",
        ]

    result = _run(cmd, cwd=root)
    if result.returncode != 0:
        return False, f"Scrub failed: {result.stderr[-500:]}"

    return True, f"Removed {len(risky)} risky path(s) from history. Review and force-push if correct."


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    dry = "--apply" not in sys.argv
    ok, msg = scrub_history(root, dry_run=dry)
    print(msg)
    sys.exit(0 if ok else 1)
