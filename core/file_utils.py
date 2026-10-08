"""Cross-platform atomic file write utility.

Windows/OneDrive sync clients can lock files during ``os.replace()``, causing
``PermissionError``.  This module centralises the retry-on-PermissionError
pattern so callers stay DRY.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def safe_atomic_write(
    dest: str | Path,
    data: str | bytes,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write *data* to *dest* atomically, retrying once on PermissionError.

    1. Write to a temp file in the same directory (same-filesystem rename).
    2. ``os.replace(tmp, dest)`` for atomicity.
    3. On ``PermissionError`` (Windows/OneDrive sync lock), remove the stale
       target and retry once.

    Raises the second ``PermissionError`` (or any other ``OSError``) so callers
    are never silently broken.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            if isinstance(data, str):
                fh.write(data)
            else:
                fh.write(data.decode(encoding))
        try:
            os.replace(tmp, str(dest))
        except PermissionError:
            # Windows/OneDrive: sync client holds destination lock.
            # Remove stale target and retry once.
            try:
                os.remove(str(dest))
            except OSError:
                pass
            os.replace(tmp, str(dest))
    except BaseException:
        # Clean up temp file on any failure.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def safe_atomic_write_json(
    dest: str | Path,
    data: Any,
    *,
    indent: int = 2,
    encoding: str = "utf-8",
) -> None:
    """Serialise *data* as JSON and write atomically to *dest*."""
    content = json.dumps(data, indent=indent, ensure_ascii=False)
    safe_atomic_write(dest, content, encoding=encoding)


def safe_atomic_write_lines(
    dest: str | Path,
    lines: list[str],
    *,
    encoding: str = "utf-8",
) -> None:
    """Write newline-delimited *lines* atomically to *dest*."""
    content = "\n".join(lines) + "\n" if lines else ""
    safe_atomic_write(dest, content, encoding=encoding)


#: Top-level key holding the CRITICAL #10 invalidation marker.
EVIDENCE_MARKER_KEY = "_EVIDENCE_INVALID"


def heal_strategy_registry_marker(registry: Any) -> Any:
    """Recompute the DERIVED counts inside a strategy-registry evidence marker.

    ``strategy_registry.json`` carries an ``_EVIDENCE_INVALID`` block that says,
    in effect, "these promoted metrics are unbacked evidence".  The block also
    records the counts that verdict was based on (``strategy_count`` and a
    per-``stage`` breakdown).  Those counts go stale the moment anything
    appends a strategy, and a stale count is worse than no marker at all:
    ``tests/test_evidence_artifacts.py`` reads ``strategy_count`` as a live
    claim about the file, and a reviewer looking at "103" above 104 rows has no
    way to tell the number was frozen when the verdict was written.

    Called on every write, so the marker keeps describing the data beside it.

    Deliberately narrow:

    * Only keys that ALREADY exist are refreshed — this never introduces a new
      factual claim into a file someone has not reviewed.
    * The marker is never added, removed, or downgraded.  ``verdict``, ``why``
      and ``action`` are human audit text, and synthesising a verdict for a file
      nobody looked at would be dishonest.  A registry with no marker is left
      exactly as-is.
    * ``strategy_count`` and ``stages`` are derived from the same list by the
      same rule, so they are healed together; letting one track reality while
      the other lies is the same defect one field over.

    Returns *registry* (mutated in place) so it can be used inline in a write.
    """
    if not isinstance(registry, dict):
        return registry
    marker = registry.get(EVIDENCE_MARKER_KEY)
    strategies = registry.get("strategies")
    if not isinstance(marker, dict) or not isinstance(strategies, list):
        return registry

    if "strategy_count" in marker:
        marker["strategy_count"] = len(strategies)

    stages = marker.get("stages")
    if isinstance(stages, dict):
        actual: dict[str, int] = {}
        for entry in strategies:
            if not isinstance(entry, dict):
                continue
            stage = entry.get("stage")
            if isinstance(stage, str):
                actual[stage] = actual.get(stage, 0) + 1
        # Stages the marker already claimed (zero them out if no longer used)
        # plus any stage that appeared since.
        for stage in list(stages):
            stages[stage] = actual.get(stage, 0)
        for stage, count in actual.items():
            stages.setdefault(stage, count)
    return registry
