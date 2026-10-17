"""Post-session hook: run the scheduler tuning report and archive it.

Called from the trader's graceful-shutdown path (core/crash_recovery.py).
Runs scripts/scheduler_tuning_report.py (read-only against the journal),
then copies artifacts/scheduler_tuning_report.json to a timestamped
archive so every session leaves its duty-cycle/overrun evidence behind
under artifacts/.

Safety contract:
- Fail-soft by contract: any failure (script error, timeout, missing
  artifact) is logged and returns None; shutdown never depends on it.
- Bounded: the report subprocess gets a hard timeout (default 120s).
- Pruned: only the newest 60 timestamped archives are kept.
- Test-inert: under pytest the hook is a no-op unless
  LLM_TRADER_POST_SESSION_REPORT=1 forces it on (the report's subprocess
  would otherwise run against the real 1.2GB journal on every test
  process exit). Setting LLM_TRADER_POST_SESSION_REPORT=0 disables the
  hook everywhere, including the trader.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("session_report_hook")

ROOT = Path(__file__).resolve().parent.parent
REPORT_SCRIPT = ROOT / "scripts" / "scheduler_tuning_report.py"
DEFAULT_REPORT_JSON = ROOT / "artifacts" / "scheduler_tuning_report.json"
KEEP_ARCHIVES = 60


def _running_under_pytest() -> bool:
    """True when this process is a pytest run.

    ``sys.argv[0]`` is NOT a usable signal on its own: ``python -m pytest``
    sets it to ``.../pytest/__main__.py`` and an xdist worker sets it to
    ``-c``, so neither name contains "pytest" and the test-inert contract
    below silently stopped holding.  The env vars pytest exports are set for
    the whole session (PYTEST_CURRENT_TEST also during each phase), so they
    are the primary signal and argv stays only as a fallback.
    """
    if os.environ.get("PYTEST_VERSION"):
        return True
    return "pytest" in Path(sys.argv[0]).name


def run_and_archive(db_path: str = "trading_state.db",
                    artifacts_dir: str | Path | None = None,
                    timeout_s: int = 120) -> Path | None:
    """Run the report once and archive its JSON; returns the archive path."""
    if os.getenv("LLM_TRADER_POST_SESSION_REPORT") == "0":
        return None
    if _running_under_pytest() and \
            os.getenv("LLM_TRADER_POST_SESSION_REPORT") != "1":
        return None
    artifacts = Path(artifacts_dir) if artifacts_dir else DEFAULT_REPORT_JSON.parent
    script = REPORT_SCRIPT  # the script resolves the journal itself via --db
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(script), "--db", str(db_path)],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            log.warning("scheduler tuning report failed (rc=%d): %s",
                        proc.returncode, (proc.stderr or "")[-300:])
            return None
    except subprocess.TimeoutExpired:
        log.warning("scheduler tuning report timed out after %ss", timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001 - hook must never break shutdown
        log.warning("scheduler tuning report could not run: %s", exc)
        return None

    source = artifacts / "scheduler_tuning_report.json"
    if not source.exists():
        log.warning("report did not produce %s", source)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = artifacts / f"scheduler_tuning_report_{stamp}.json"
    try:
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        log.warning("could not archive tuning report: %s", exc)
        return None

    _prune(artifacts)
    return dest


def _prune(artifacts: Path) -> None:
    """Keep only the newest KEEP_ARCHIVES timestamped report archives."""
    try:
        archives = sorted(artifacts.glob("scheduler_tuning_report_2*.json"))
        for old in archives[:-KEEP_ARCHIVES]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("archive prune skipped: %s", exc)
