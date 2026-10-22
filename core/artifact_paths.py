"""Portable artifact paths — the operator's directory layout must not leak
into the evidence record.

An artifact that records ``C:\\Users\\Someone\\OneDrive\\Desktop\\...`` cannot
be opened by the next reader, cannot be reproduced on another machine, and
publishes the operator's folder structure to whoever reads the file.  The
contract for Phase 1 requires: *no artifact under ``artifacts/`` contains an
absolute path*, and the writers must be the place where that is guaranteed —
a test can only check what was already written.

One module owns the rules so a writer, the scrubber and the portability test
all agree on what "an absolute path" means:

``find_absolute_paths(text)``
    Every absolute path inside a decoded string.

``sanitize_text(text)``
    The same string with each absolute path replaced by its repo-relative
    form (or the bare file name when it points outside the repository).

``scrub(value)``
    The same treatment applied recursively to a JSON-shaped structure.

``relative_path(path)``
    A repo-relative POSIX path for a file, so an artifact can still say
    *which* journal it read without saying *whose* disk it was on.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Repository root — the parent of the package that holds this module.
REPO_ROOT = Path(__file__).resolve().parent.parent

# A Windows drive path: ``C:\\Users\\me\\file.db``.  The drive letter must not
# be preceded by a word character, otherwise the ``s://`` in ``https://...``
# reads as a drive.  The separator may be a backslash or a forward slash:
# an artifact written on Windows and read on Linux still has to be caught.
_WINDOWS_ABS = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]"
    r"(?:[^\\/:*?\"<>|\r\n()\[\]{},;']+[\\/])*"
    r"[^\\/:*?\"<>|\r\n()\[\]{},;']*"
)

# A POSIX path under a well-known absolute root.  URLs are excluded because
# they never start with a single leading slash followed by one of these roots;
# the lookbehind additionally keeps ``https://host/home/x`` out.
_POSIX_ABS = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home|root|mnt|media|srv|opt|private|tmp|var)/"
    r"(?:[^/\s\"',;)\]}]+/)*[^/\s\"',;)\]}]*"
)

#: A drive-letter prefix, used to recognise a Windows path on a POSIX host.
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _windows_candidate_is_path(text: str) -> bool:
    """True when a Windows-looking match really is a path.

    ``N:\\n{`` — the ``\\n`` of a format string, or the escape of a JSON
    string — is a drive letter followed by a fragment, not a location.  A real
    path either has at least two segments (``C:\\Users\\me``) or a file name
    with an extension (``C:\\x.db``).
    """
    parts = [p for p in text.replace("/", "\\").split("\\") if p]
    segments = parts[1:]  # drop the "C:" drive
    if not segments:
        return False
    if len(segments) >= 2:
        return True
    return "." in segments[0]


def _matches(text: str) -> List[str]:
    found: List[str] = []
    for match in _WINDOWS_ABS.finditer(text):
        candidate = match.group(0)
        if _windows_candidate_is_path(candidate):
            found.append(candidate)
    for match in _POSIX_ABS.finditer(text):
        candidate = match.group(0)
        # A forward-slash Windows path also contains a POSIX-looking tail
        # ("C:/Users/me/x.db" -> "/Users/me/x.db"); report it once.
        if any(candidate in hit for hit in found):
            continue
        found.append(candidate)
    return found


def find_absolute_paths(text: Any) -> List[str]:
    """Absolute paths inside *text* (empty list for a non-string)."""
    if not isinstance(text, str) or not text:
        return []
    return _matches(text)


def is_absolute_path(text: Any) -> bool:
    """True when *text* contains at least one absolute path."""
    return bool(find_absolute_paths(text))


def relative_path(path: Any, base: Optional[str] = None) -> str:
    """A repo-relative POSIX path for *path*.

    Falls back to the bare file name when the target lives outside the
    repository, because "somewhere else on this machine" is not information
    an artifact can carry to another reader.
    """
    if not path:
        return ""
    raw = os.fspath(path)
    # A Windows path stays a Windows path even when the reader is POSIX (CI
    # runs ubuntu), and it cannot be resolved there — so it degrades to the
    # file name instead of leaking the operator's folder layout.
    if _DRIVE.match(raw) and os.sep != "\\":
        return os.path.basename(raw.replace("\\", "/").rstrip("/"))
    root = Path(base).resolve() if base else REPO_ROOT
    try:
        resolved = Path(raw).resolve()
    except (OSError, ValueError):
        return os.path.basename(str(raw).replace("\\", "/"))
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return os.path.basename(str(raw))


def sanitize_text(text: Any, base: Optional[str] = None) -> Any:
    """Replace every absolute path in *text* with a portable form.

    A path inside the repository becomes repo-relative (``data/x.db``); any
    other absolute path becomes the bare file name — the fact that it was
    absolute is preserved, the operator's layout is not.
    """
    if not isinstance(text, str) or not text:
        return text

    def _replace(match: "re.Match[str]") -> str:
        found = match.group(0)
        if not _windows_candidate_is_path(found):
            return found
        rel = relative_path(found, base)
        if rel and rel != found:
            return rel
        return "<abs-path>"

    out = _WINDOWS_ABS.sub(_replace, text)
    out = _POSIX_ABS.sub(_replace, out)
    return out


def scrub(value: Any, base: Optional[str] = None) -> Any:
    """Recursively sanitise a JSON-shaped structure."""
    if isinstance(value, str):
        return sanitize_text(value, base)
    if isinstance(value, dict):
        return {scrub(k, base): scrub(v, base) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, base) for v in value]
    return value


def scan_value(value: Any, _path: str = "$") -> List[Tuple[str, str]]:
    """Every ``(json_path, absolute_path)`` pair inside a JSON-shaped value."""
    found: List[Tuple[str, str]] = []
    if isinstance(value, str):
        for hit in find_absolute_paths(value):
            found.append((_path, hit))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(scan_value(item, f"{_path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(scan_value(item, f"{_path}[{index}]"))
    return found


def _iter_json_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        for name in filenames:
            if name.endswith(".json"):
                yield os.path.join(dirpath, name)


def scrub_tree(root: str = "artifacts", dry_run: bool = False) -> Dict[str, Any]:
    """Rewrite every JSON file under *root* with its absolute paths removed.

    Returns a report of what changed.  Used by the operator to clean a tree
    that was written before the writers were fixed; the writers themselves
    are what keep a clean tree clean.
    """
    import json

    changed: List[str] = []
    scanned = 0
    for path in _iter_json_files(root):
        scanned += 1
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        hits = scan_value(data)
        if not hits:
            continue
        cleaned = scrub(data)
        changed.append(path)
        if not dry_run:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cleaned, handle, indent=2, default=str)
    return {"scanned": scanned, "changed": len(changed), "files": changed}


def main() -> int:
    """CLI: ``python -m core.artifact_paths [--root artifacts] [--check]``."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Strip absolute paths from artifacts")
    parser.add_argument("--root", default="artifacts", help="tree to scrub")
    parser.add_argument("--check", action="store_true",
                        help="report only; do not rewrite")
    args = parser.parse_args()

    if args.check:
        offenders: List[str] = []
        for path in _iter_json_files(args.root):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                continue
            # The raw text of a JSON file escapes every backslash, so a
            # Windows path appears as "C:\\Users\\..." and does not match a
            # path pattern. The DECODED values are what an artifact really
            # carries, so the file is parsed before it is judged.
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if data is not None:
                if scan_value(data):
                    offenders.append(path)
            elif find_absolute_paths(text):
                offenders.append(path)
        print(json.dumps({"root": args.root, "offending_files": len(offenders),
                          "files": offenders}, indent=2))
        return 1 if offenders else 0

    report = scrub_tree(args.root)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
