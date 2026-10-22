"""Test order must not decide the outcome (regression, 2026-10-22).

Measured failure: ``tests/test_news_gate_integration.py`` failed 4/4 when it
ran in the same process after ``tests/test_ablation_harness.py``, and passed
4/4 standalone.  The ablation harness imports ``mt5_trader`` at module
scope; the OHLC resolver then preferred ``sys.modules["mt5_trader"]
.fetch_ohlc``, so the news-gate stub was ignored and the cycle hit the live
broker fetcher ("Unsupported timeframe M15") instead of its own fixture.

The unit-level invariants live in ``tests/test_ohlc_resolver_single_source.py``
(one owner, one answer, no ``sys.modules`` guessing).  This module is the
end-to-end proof: it runs the two files in ONE process, in both orders, and
demands a clean exit each time.

Slow by construction: the news-gate file builds an orchestrator and runs a
cycle (minutes-class on a low-RAM box).  The file name puts it in the slow
lane, so it stays out of the five-minute fast gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Only the cheap ablation test is selected.  That is enough: pytest imports
# the whole ablation MODULE during collection, and the module imports
# ``mt5_trader`` at module scope -- the side effect that used to flip the
# fetcher resolution.  Its two cycle tests are not the subject here and each
# spends ~100s in a real MT5 attach attempt (pre-existing, unrelated).
_ABLATION = "tests/test_ablation_harness.py::test_modes_parse_from_env_drop_unknown"
_NEWS_GATE = "tests/test_news_gate_integration.py"


def _run_together(first: str, second: str) -> subprocess.CompletedProcess:
    """Run both files in a single pytest process, in the given order."""
    cmd = [
        sys.executable, "-m", "pytest",
        first, second,
        "-n", "0",              # one process, not xdist workers
        "-p", "no:cacheprovider",
        "-q",
    ]
    return subprocess.run(
        cmd,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )


def _assert_clean(result: subprocess.CompletedProcess, order: str) -> None:
    assert result.returncode == 0, (
        f"{order} order failed in a single process "
        f"(exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout[-4000:]}\n"
        f"--- stderr ---\n{result.stderr[-4000:]}"
    )


@pytest.mark.integration
def test_ablation_then_news_gate_in_one_process():
    """The reported order: the ablation harness imports mt5_trader first."""
    result = _run_together(_ABLATION, _NEWS_GATE)
    _assert_clean(result, "ablation-then-news-gate")


@pytest.mark.integration
def test_news_gate_then_ablation_in_one_process():
    """The reverse order must be equally clean."""
    result = _run_together(_NEWS_GATE, _ABLATION)
    _assert_clean(result, "news-gate-then-ablation")
