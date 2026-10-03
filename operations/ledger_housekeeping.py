"""Ledger housekeeping: plan cleanup of old reports/artifacts, apply deletions."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = "artifacts"


def plan(base_dir: str = ARTIFACTS_DIR) -> Dict[str, Any]:
    """Scan artifacts dir and plan which files are eligible for cleanup.

    Returns a dict with keys: candidates (list of paths), total_size_bytes, count.
    """
    candidates: List[str] = []
    total_size = 0
    if not os.path.isdir(base_dir):
        return {"candidates": [], "total_size_bytes": 0, "count": 0}
    now = time.time()
    for root, _dirs, files in os.walk(base_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                age_days = (now - os.path.getmtime(fpath)) / 86400.0
                size = os.path.getsize(fpath)
                if age_days > 30 and fname.endswith((".json", ".log", ".csv", ".txt")):
                    candidates.append(fpath)
                    total_size += size
            except OSError:
                continue
    return {
        "candidates": candidates,
        "total_size_bytes": total_size,
        "count": len(candidates),
    }


def apply(
    cleanup_plan: Dict[str, Any],
    reports_older_than_days: int = 30,
) -> Dict[str, Any]:
    """Apply the cleanup plan: delete candidate files older than threshold.

    Returns a dict with keys: deleted, failed, freed_bytes.
    """
    deleted = 0
    failed = 0
    freed = 0
    now = time.time()
    for fpath in cleanup_plan.get("candidates", []):
        try:
            age_days = (now - os.path.getmtime(fpath)) / 86400.0
            if age_days >= reports_older_than_days:
                size = os.path.getsize(fpath)
                os.remove(fpath)
                deleted += 1
                freed += size
        except OSError as exc:
            logger.debug("Failed to delete %s: %s", fpath, exc)
            failed += 1
    return {
        "deleted": deleted,
        "failed": failed,
        "freed_bytes": freed,
    }
