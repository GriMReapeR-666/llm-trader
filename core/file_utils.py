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
