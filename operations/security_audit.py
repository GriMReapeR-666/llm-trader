"""Non-destructive local security checks for trading credentials and traces.

This module never prints a secret value.  It reports only file paths and
redacted finding types so an operator can rotate credentials safely.  It is a
check, not a secret manager: the operator must still rotate values and move
them to an approved environment/secret store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_EXCLUDED_DIRS = {
    ".git", ".pytest_cache", ".pytest_tmp", "__pycache__", ".venv", "venv", "node_modules",
    # Large vendored/third-party subtrees that never contain project credentials.
    "tui_pi", "tui_clawcode", "tui_opentui", "vendor",
    "trading_knowledge_base", "quant_knowledge_framework",
    "backups", "artifacts", ".kilo", "graphify-out",
}
DEFAULT_EXCLUDED_FILES = {
    ".env.example", "security_audit.py", "graph.json",
}

# Values are deliberately not captured in the report.  Assignment scanning is
# line based so values containing ``#`` (a valid password character) are not
# silently truncated.  Code expressions such as ``os.getenv(...)`` are
# filtered below; they are references to a secret, not exposed secret values.
_ASSIGNMENT_KEYS = {
    "MT5_PASSWORD": re.compile(r"^\s*MT5_PASSWORD\s*=\s*(?P<value>.+?)\s*$", re.I),
    "OPENROUTER_API_KEY": re.compile(r"^\s*OPENROUTER_API_KEY\s*=\s*(?P<value>.+?)\s*$", re.I),
    "TOKENROUTER_KEY": re.compile(r"^\s*TOKENROUTER_(?:KEY|API_KEY)\s*=\s*(?P<value>.+?)\s*$", re.I),
    "ROUTESME_KEY": re.compile(r"^\s*ROUTESME_(?:KEY|API_KEY)\s*=\s*(?P<value>.+?)\s*$", re.I),
    "CEREBRAS_API_KEY": re.compile(r"^\s*CEREBRAS_API_KEY\s*=\s*(?P<value>.+?)\s*$", re.I),
    "NVIDIA_API_KEY": re.compile(r"^\s*NVIDIA_(?:API_KEY|KEY_\d+)\s*=\s*(?P<value>.+?)\s*$", re.I),
    "NEWSAPI_KEY": re.compile(r"^\s*NEWSAPI_KEY\s*=\s*(?P<value>.+?)\s*$", re.I),
    "FRED_API_KEY": re.compile(r"^\s*FRED_API_KEY\s*=\s*(?P<value>.+?)\s*$", re.I),
}

_INLINE_PATTERNS = (
    ("OPENAI_LIKE_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("ROUTESME_KEY", re.compile(r"\brm-[A-Za-z0-9_-]{20,}\b")),
    ("BEARER_TOKEN", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)

_PASSWORD_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w.])password\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s\r\n]+)"
)
_PASSWORD_KEY = re.compile(
    r"(?i)(?:\"password\"|'password'|^\s*password)\s*:\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,}\r\n]+)"
)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_placeholder_or_code(value: str) -> bool:
    """Return true for config placeholders and source-code references."""
    value = _unquote(value).strip()
    lowered = value.lower()
    if not value or lowered in {"none", "null", "false", "true", "mock_key", "test_key", "password",
                                "[redacted]", "<redacted>", "redacted", "***"}:
        return True
    # Dataclass/type annotations such as ``password: str = ""`` are code,
    # not assigned credentials.  Keep this narrow so ordinary string values
    # are still scanned.
    if lowered.startswith(("str =", "optional[str]", "secretstr =")):
        return True
    if lowered.startswith(("your_", "change_me", "replace_", "example", "<", "${")):
        return True
    # These are normal ways production code obtains a secret.  Flagging them
    # as leaked credentials makes the audit unusable and masks real literals.
    code_markers = (
        "os.getenv(", "os.environ", "getenv(", "environ.get(", "getpass.",
        "input(", "self.", "args.", "account[", "active_acc[", "config[",
        "password)", "password,", "password if ", " or password",
    )
    if any(marker in lowered for marker in code_markers):
        return True
    if any(char in value for char in ("(", ")")):
        return True
    return False


def _looks_like_secret(value: str, *, minimum_length: int = 8) -> bool:
    value = _unquote(value)
    return len(value) >= minimum_length and not _is_placeholder_or_code(value)


def _git_tracked(root: Path) -> List[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True, capture_output=True, text=False, timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [item.decode("utf-8", "replace") for item in result.stdout.split(b"\0") if item]


def _git_history_risky_files(root: Path) -> List[str]:
    """Return only risky path names ever present in Git history.

    This deliberately inspects path metadata, never blob contents, so a
    historical credential can trigger operator review without being printed
    into a report or test log.
    """
    # ``git -C`` searches parent directories.  Do not attribute the enclosing
    # workspace's history to an arbitrary temporary/subdirectory audit root.
    if not (root / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--all", "--format=",
             "--name-only", "--"],
            check=True, capture_output=True, text=False, timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    paths = set()
    for raw in result.stdout.splitlines():
        relative = raw.decode("utf-8", "replace").strip()
        if not relative:
            continue
        name = Path(relative).name.lower()
        if name in {".env", "accounts.json", "config.ini"} or name.endswith(("secrets.json", "credentials.json")):
            paths.add(relative)
    return sorted(paths)


def _iter_files(root: Path, include_untracked: bool) -> Iterable[Path]:
    seen = set()
    paths = _git_tracked(root) if include_untracked else []
    for relative in paths:
        path = (root / relative).resolve()
        if path.is_file() and path not in seen:
            seen.add(path)
            yield path
    if include_untracked:
        # Use os.walk with top-down pruning so excluded subtrees (tui_pi/,
        # vendor/, etc.) are never entered — rglob("*") would descend into
        # them first and then discard every child, which is O(total_tree).
        skip_exts = {".pyc", ".db", ".log", ".png", ".jpg", ".zip"}
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            # Prune excluded directories in-place so os.walk never descends.
            dirnames[:] = [
                d for d in dirnames if d not in DEFAULT_EXCLUDED_DIRS
            ]
            for fname in filenames:
                if fname in DEFAULT_EXCLUDED_FILES or Path(fname).suffix.lower() in skip_exts:
                    continue
                path = (Path(dirpath) / fname).resolve()
                if path in seen:
                    continue
                try:
                    if path.stat().st_size > 2_000_000:
                        continue
                except OSError:
                    continue
                seen.add(path)
                yield path


def _scan_file(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ["UNREADABLE_FILE"]
    findings: List[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("#", ";", "//")):
            continue
        for name, pattern in _ASSIGNMENT_KEYS.items():
            match = pattern.match(line)
            if match and _looks_like_secret(match.group("value")):
                findings.append(name)
        password_match = _PASSWORD_ASSIGNMENT.search(line)
        if password_match and _looks_like_secret(password_match.group("value")):
            findings.append("PASSWORD_ASSIGNMENT")
        password_key = _PASSWORD_KEY.search(line)
        if password_key and _looks_like_secret(password_key.group("value")):
            findings.append("PASSWORD_ASSIGNMENT")
    for name, pattern in _INLINE_PATTERNS:
        if pattern.search(text):
            findings.append(name)
    return sorted(set(findings))


def _is_non_production_path(relative: str) -> bool:
    """Identify fixtures/build output without hiding local production config."""
    parts = [part.lower() for part in Path(relative).parts]
    name = parts[-1] if parts else ""
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".spec.js"))
        or "dist" in parts
        or "build" in parts
    )


_audit_cache: Optional[Dict[str, Any]] = None
_audit_cache_ts: float = 0.0
_audit_cache_key: Optional[tuple] = None
_AUDIT_CACHE_TTL: float = 60.0  # seconds (in-process L1)

# Cross-process L2: the full tree scan takes minutes on large/OneDrive roots,
# which dominated engine boot VALIDATE. The scan result contains only redacted
# paths and finding types (never secret values), so persisting it is safe.
DISK_CACHE_NAME = ".security_audit_cache.json"


def _disk_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("SECURITY_AUDIT_DISK_TTL_SECONDS", "3600")))
    except (TypeError, ValueError):
        return 3600.0


def _repo_fingerprint(root_path: Path) -> Optional[Dict[str, Any]]:
    """Cheap change signals so a cached report never outlives its repo state.

    Combines the HEAD commit with the tracked-file count: a commit that adds
    or removes files invalidates immediately. Untracked working-tree edits
    remain bounded by the TTL — the report discloses its age either way.
    """
    try:
        head = subprocess.run(
            ["git", "-C", str(root_path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        tracked = subprocess.run(
            ["git", "-C", str(root_path), "ls-files"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.count("\n")
        return {"head": head, "tracked_count": tracked}
    except (OSError, subprocess.SubprocessError):
        return None


def _load_disk_cache(root_path: Path) -> Optional[Dict[str, Any]]:
    path = root_path / DISK_CACHE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("report"), dict):
        return None
    if raw.get("version") != 1:
        return None
    fingerprint = _repo_fingerprint(root_path)
    if raw.get("fingerprint") != fingerprint:
        return None
    if (time.time() - float(raw.get("ts", 0))) >= _disk_cache_ttl():
        return None
    return raw


def _store_disk_cache(root_path: Path, report: Dict[str, Any]) -> None:
    payload = {
        "version": 1,
        "ts": time.time(),
        "fingerprint": _repo_fingerprint(root_path),
        "root": str(root_path),
        "report": report,
    }
    try:
        tmp = root_path / (DISK_CACHE_NAME + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, root_path / DISK_CACHE_NAME)
    except OSError:
        pass  # cache is best-effort; a failed write just means a cold next run


def audit(root: str | os.PathLike[str] = ".", include_untracked: bool = True) -> Dict[str, Any]:
    """Return a redacted security report for *root*.

    ``status`` is ``REVIEW_REQUIRED`` whenever a credential-like value is
    found, a risky file is tracked, or the local L0 trace directory is not
    excluded from the scan.  It never grants execution authority.

    Results are cached for ``_AUDIT_CACHE_TTL`` seconds in-process and, when
    the root is a git checkout, also on disk (``DISK_CACHE_NAME``) with a
    longer TTL so a fresh process (e.g. a trading boot) does not re-pay the
    full tree scan. Disk hits are invalidated by a changed HEAD commit or
    tracked-file count and always disclose their age in ``report["cache"]``.
    """
    global _audit_cache, _audit_cache_ts, _audit_cache_key
    root_path = Path(root).resolve()
    cache_key = (str(root_path), include_untracked)
    now = time.time()
    if _audit_cache is not None and _audit_cache_key == cache_key and (now - _audit_cache_ts) < _AUDIT_CACHE_TTL:
        return _audit_cache
    disk = _load_disk_cache(root_path)
    if disk is not None and bool(disk["report"].get("values_redacted")):
        report = dict(disk["report"])
        age = max(0.0, now - float(disk.get("ts", now)))
        report["cache"] = {"hit": True, "disk": True, "age_seconds": round(age, 1),
                           "ttl_seconds": _disk_cache_ttl()}
        _audit_cache, _audit_cache_ts, _audit_cache_key = report, now, cache_key
        return report
    tracked = set(_git_tracked(root_path))
    historical_risky = _git_history_risky_files(root_path)
    risky_tracked = sorted(
        item for item in tracked
        if Path(item).name.lower() in {".env", "accounts.json", "config.ini"}
        or Path(item).name.lower().endswith(("secrets.json", "credentials.json"))
    )
    findings: List[Dict[str, Any]] = []
    non_production_findings: List[Dict[str, Any]] = []
    for path in _iter_files(root_path, include_untracked=include_untracked):
        types = _scan_file(path)
        if types:
            try:
                relative = str(path.relative_to(root_path))
            except ValueError:
                relative = path.name
            item = {
                "path": relative,
                "types": types,
                "scope": "NON_PRODUCTION" if _is_non_production_path(relative) else "PRODUCTION",
            }
            (non_production_findings if item["scope"] == "NON_PRODUCTION" else findings).append(item)
    l0_candidates = []
    for candidate in (root_path / "l0_traces", root_path / "traces", root_path / "logs"):
        if candidate.exists():
            l0_candidates.append(str(candidate.relative_to(root_path)))
    production_findings = findings
    non_production_findings = non_production_findings
    requires_operator_review = bool(
        production_findings or risky_tracked or historical_risky or l0_candidates
    )
    result = {
        "status": "REVIEW_REQUIRED" if requires_operator_review else "CLEAR_FOR_OPERATOR_REVIEW",
        "credential_findings": production_findings,
        "non_production_findings": non_production_findings,
        "tracked_risky_files": risky_tracked,
        "historical_risky_files": historical_risky,
        "l0_trace_directories": l0_candidates,
        "values_redacted": True,
        "cache": {"hit": False, "disk": False, "age_seconds": None, "ttl_seconds": None},
        "rotation_completed": False,
        "execution_authority": False,
        "next_actions": [
            "Rotate every production finding and any previously exposed value." if production_findings else "No production credential findings require rotation.",
            "Untrack local secret files and keep them only in an approved secret store." if risky_tracked else "No risky files are currently tracked.",
            "Scrub or formally review risky credential files found in Git history." if historical_risky else "No risky files found in Git history.",
            "Verify L0 traces contain hashes/redactions, never raw credentials." if l0_candidates else "No L0 trace directories detected.",
        ],
    }
    _audit_cache = result
    _audit_cache_ts = time.time()
    _audit_cache_key = cache_key
    _store_disk_cache(root_path, result)
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(audit(), indent=2))
