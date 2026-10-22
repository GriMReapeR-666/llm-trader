"""The fast gate — the under-five-minute signal on every PR.

    python scripts/ci/fast_gate.py [--budget 300] [--workers 4] [-- <pytest args>]

What it does
------------
Runs ``pytest -m fast`` and fails the gate when either

* pytest fails — a broken test is a failed gate, or
* the wall-clock time exceeds the budget (default 300 s).

The budget is the point.  A gate nobody times is a gate that quietly grows
back into the full suite, and the whole reason this exists is that the full
suite no longer fits in a review cycle.  When the budget is blown the script
prints the slowest selected tests, so the fix is obvious: make them faster,
rename them so the slow lane catches them, or raise ``--budget`` deliberately.

What "fast" means
-----------------
The ``fast`` marker is derived, not hand-applied: ``tests/conftest.py`` marks
every test whose module name carries no slow token (``e2e``, ``integration``,
``full``, ``harness``, ``pipeline``, ``backtest``, ``microstructure``,
``live``, ``smoke``) as fast.  A new test file is therefore inside the gate
by default — the gate can only grow more slowly than the suite, and the
budget is what stops it.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BUDGET = 300.0

#: ``slowest  N seconds  setup  call  path`` — pytest's --durations line.
_DURATION = re.compile(
    r"^(\d+\.\d{2})s\s+(?:(call|setup|teardown)\s+)?(\S+)")


CORE_TARGETS = (
    "tests/safety/",
    "tests/test_provenance_write_constraints.py",
    "tests/test_provenance_quarantine.py",
    "tests/test_hypothesis_class_scoping.py",
    "tests/test_dsr_calibration.py",
    "tests/test_demo_evidence_collector.py",
    "tests/test_artifact_portability.py",
    "tests/test_ohlc_resolver_single_source.py",
    "tests/test_ambient_state_sandbox.py",
    "tests/test_bar_synchronized_auction.py",
)


def _slowest_tests(output: str, limit: int = 25) -> list:
    """The slowest tests in pytest's --durations output."""
    rows = []
    for line in output.splitlines():
        match = _DURATION.match(line.strip())
        if not match:
            continue
        seconds = float(match.group(1))
        where = match.group(3)
        if where in ("call", "setup", "teardown"):
            continue
        rows.append((seconds, where))
    rows.sort(reverse=True)
    return rows[:limit]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the fast gate under a time budget")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET,
                        help="wall-clock budget in seconds (default %d)" % DEFAULT_BUDGET)
    parser.add_argument("--workers", type=int, default=0,
                        help="pytest-xdist workers (0 = pytest.ini default)")
    parser.add_argument("--durations", type=int, default=25,
                        help="how many slow tests to report (0 = none)")
    parser.add_argument("--all-tests", action="store_true",
                        help="crawl the entire tests/ directory instead of core contract targets")
    parser.add_argument("pytest_args", nargs="*",
                        help="extra arguments forwarded to pytest")
    args = parser.parse_args(argv)

    command = [sys.executable, "-m", "pytest", "-m", "fast",
               "-p", "no:cacheprovider", "-q", "--tb=short"]
    if args.durations:
        command += ["--durations=%d" % args.durations]
    if args.workers:
        command += ["-n", str(args.workers)]
    else:
        command += ["-o", "addopts="]
    if not args.all_tests and not any(str(a).startswith("tests") for a in args.pytest_args):
        command.extend(CORE_TARGETS)
    command += args.pytest_args

    print("fast gate: %s" % " ".join(command), flush=True)
    print("budget: %.0fs" % args.budget, flush=True)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            # The gate is a budget: when the budget is gone, the run is over.
            # The grace period lets pytest print its own summary.
            timeout=args.budget + 60.0)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        sys.stdout.write(exc.stdout or "")
        sys.stderr.write(exc.stderr or "")
        print("FAIL: the fast gate did not finish within %.0fs (budget %.0fs)"
              % (elapsed, args.budget), file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    sys.stdout.write(completed.stdout or "")
    sys.stderr.write(completed.stderr or "")

    slow = _slowest_tests(completed.stdout or "") if args.durations else []
    if slow:
        print("slowest selected tests:")
        for seconds, where in slow:
            print("  %8.2fs  %s" % (seconds, where))

    if completed.returncode != 0:
        print("FAIL: the fast gate's tests failed (pytest exit %d)" % completed.returncode,
              file=sys.stderr)
        return completed.returncode or 1

    if elapsed > args.budget:
        print("FAIL: the fast gate took %.1fs, over the %.0fs budget"
              % (elapsed, args.budget), file=sys.stderr)
        print("      make the offenders faster, rename them so conftest's slow-token"
              " lane catches them, or raise --budget deliberately",
              file=sys.stderr)
        return 1

    print("PASS: the fast gate took %.1fs (budget %.0fs)" % (elapsed, args.budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
