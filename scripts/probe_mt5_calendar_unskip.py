#!/usr/bin/env python3
"""Probe: can this host unskip the 10 MT5 calendar integration tests?

The 10 tests -- the two classes in tests/test_calendar_integration.py (9
tests, class-level skipIf) and tests/test_calendar_wrapper.py::
TestCalendarWrapperIntegration::test_real_mt5_connection -- skip while
find_mt5_dll() is None (data/mt5_calendar_wrapper.py).  Passing that
condition is necessary but not sufficient: the wrapper must also load the
DLL and resolve its MQL5 calendar exports, or setUpClass degrades to a
runtime skip ("MT5 Calendar Wrapper not available").  This probe runs the
REAL code path (find_mt5_dll -> MT5CalendarWrapper -> initialize), not a
re-implementation, so its verdict is what the tests would actually do.

Exit codes:
    0  the 10 tests can run here (skip condition passes AND wrapper inits)
    1  they will still skip (reason printed)
    2  probe itself could not run (calendar wrapper not importable)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TESTS_COVERED = (
    "10 tests: tests/test_calendar_integration.py (9) + "
    "tests/test_calendar_wrapper.py::TestCalendarWrapperIntegration (1)"
)


def main() -> int:
    print(f"Probe for: {TESTS_COVERED}\n")

    try:
        from data.mt5_calendar_wrapper import MT5CalendarWrapper, find_mt5_dll
    except Exception as exc:  # e.g. ctypes.wintypes on non-Windows hosts
        print(f"NOT RUNNABLE: calendar wrapper not importable: {exc}")
        print("VERDICT: the 10 tests will still skip (host cannot load the wrapper)")
        return 2

    # 1) The exact skipIf condition the 10 tests evaluate.
    dll = find_mt5_dll()
    if dll is None:
        print("skip condition find_mt5_dll(): None  (no terminal64.dll candidate exists)")
        print("VERDICT: the 10 tests will still skip (skipIf fires on all 10)")
        return 1
    print(f"skip condition find_mt5_dll(): {dll}")

    # 2) Necessary-but-not-sufficient part: load the DLL and resolve the five
    #    MQL5 calendar exports (what setUpClass does).
    try:
        wrapper = MT5CalendarWrapper()
        wrapper.initialize()
    except Exception as exc:
        print(f"wrapper initialize(): FAILED - {type(exc).__name__}: {exc}")
        print("VERDICT: the 10 tests will still skip "
              "(setUpClass degrades to 'MT5 Calendar Wrapper not available')")
        return 1
    print("wrapper initialize(): OK (DLL loaded, calendar exports resolved)")

    # 3) Informational: a live calendar read, the first thing the tests do.
    try:
        countries = wrapper.get_countries(max_count=5)
        print(f"live get_countries(max_count=5): OK ({len(countries)} returned)")
    except Exception as exc:
        print(f"live get_countries(): FAILED - {type(exc).__name__}: {exc}")
        print("        (tests assert on live calendar data; expect failures)")
    finally:
        try:
            wrapper.shutdown()
        except Exception:
            pass

    print("VERDICT: the 10 MT5 calendar tests CAN run on this host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
