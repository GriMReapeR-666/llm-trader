"""Trader-side stop flag: graceful external wind-down (PU-0161 follow-up 4).

PU-0160 measured that a boot-spawned trader sits in a CREATE_NEW_PROCESS_GROUP
with CTRL_C disabled, so the guard's wind-down ladder degrades to CTRL_BREAK —
which the OS honours by killing the process group WITHOUT running the trader's
cleanup (exit 0xC000013A). CrashRecovery then replays from the last journal
checkpoint instead of shutting down cleanly.

A stop flag is the missing graceful path: an external guard creates a file, the
trader notices it at the top of the next main-loop iteration, and leaves the
loop NORMALLY so the `finally` block runs the full cleanup path.

These tests pin the flag protocol (path resolution, peek vs consume, one-shot
retirement). The loop wiring itself is asserted structurally — see
test_loop_check_precedes_work.
"""
import os

import pytest

from mt5_trader import (
    _acknowledge_stop_file,
    _stop_file_path,
    _stop_file_requested,
)


@pytest.fixture(autouse=True)
def _isolated_flag(tmp_path, monkeypatch):
    """Point the flag at a scratch path for every test in this module."""
    flag = tmp_path / "trader_stop.flag"
    monkeypatch.setenv("MT5_TRADER_STOP_FILE", str(flag))
    yield flag


def test_default_path_is_repo_root(monkeypatch):
    """Unset env -> <repo>/trader_stop.flag (documented default)."""
    monkeypatch.delenv("MT5_TRADER_STOP_FILE", raising=False)
    path = _stop_file_path()
    assert os.path.basename(path) == "trader_stop.flag"
    assert os.path.isabs(path)


def test_relative_override_resolves_against_base_dir(monkeypatch):
    monkeypatch.setenv("MT5_TRADER_STOP_FILE", "custom.stop")
    path = _stop_file_path()
    assert os.path.isabs(path)
    assert os.path.basename(path) == "custom.stop"


def test_no_flag_means_not_requested(_isolated_flag):
    assert not _isolated_flag.exists()
    assert _stop_file_requested() is False


def test_peek_has_no_side_effects(_isolated_flag):
    """_stop_file_requested must NOT consume — the sleep loop peeks every
    second and would otherwise swallow the request before the main loop sees it."""
    _isolated_flag.write_text("reason", encoding="utf-8")
    assert _stop_file_requested() is True
    assert _isolated_flag.exists(), "peek must leave the flag in place"
    assert _stop_file_requested() is True


def test_consume_reads_reason_and_retires_flag(_isolated_flag):
    _isolated_flag.write_text("RSS guard wind-down", encoding="utf-8")
    assert _stop_file_requested() is True

    reason = _acknowledge_stop_file()

    assert reason == "RSS guard wind-down"
    assert not _isolated_flag.exists(), "flag must be retired (one-shot)"
    assert (_isolated_flag.parent / ( _isolated_flag.name + ".consumed")).exists(), (
        "retired flag must be renamed, not deleted, so the request stays auditable"
    )


def test_consume_is_one_shot(_isolated_flag):
    """After acknowledgement the flag must not re-trigger (prevents a
    boot -> stop -> boot loop on restart)."""
    _isolated_flag.write_text("stop", encoding="utf-8")
    _acknowledge_stop_file()
    assert _stop_file_requested() is False
    assert _acknowledge_stop_file() == ""


def test_consume_survives_empty_and_whitespace(_isolated_flag):
    _isolated_flag.write_text("   \n\n", encoding="utf-8")
    assert _acknowledge_stop_file() == ""


def test_only_first_line_is_the_reason(_isolated_flag):
    """Multi-line payloads collapse to line 1 (bounded reason, no log spam)."""
    _isolated_flag.write_text("first line\nsecond line that is much longer\n",
                              encoding="utf-8")
    assert _acknowledge_stop_file() == "first line"


def test_consume_without_flag_is_safe():
    """Calling consume when nothing was requested must not raise."""
    assert _acknowledge_stop_file() == ""


def test_loop_check_precedes_work():
    """Structural guard: the stop-flag check must be the FIRST statement in
    the main loop body, ahead of the holiday check, so a request stops the
    trader before another cycle's work (and before any order placement)."""
    import inspect
    import mt5_trader

    src = inspect.getsource(mt5_trader.main)
    loop_marker = "while args.continuous or cycle_count < args.loops:"
    assert loop_marker in src, "main trading loop not found"

    # Find the llm-trade loop (the last one — earlier loops are demo paths).
    idx = src.rfind(loop_marker)
    body = src[idx:idx + 4000]

    stop_at = body.find("_stop_file_requested()")
    assert stop_at != -1, "stop-flag check missing from main loop"
    holiday_at = body.find("PRE-CYCLE HOLIDAY CHECK")
    if holiday_at != -1:
        assert stop_at < holiday_at, (
            "stop-flag check must precede the holiday/cycle work"
        )
    # It must be inside the loop, not merely mentioned elsewhere.
    assert "break" in body[stop_at:stop_at + 800], (
        "stop-flag check must break out of the loop (graceful, not raise)"
    )


def test_stop_flag_sets_distinct_run_status():
    """A stop-flag exit must not be reported as STOPPED/FAILED — the report
    engine needs to distinguish 'operator asked to stop' from a crash."""
    import inspect
    import mt5_trader

    src = inspect.getsource(mt5_trader.main)
    assert 'run_status = "STOP_FLAG"' in src


def test_sleep_pauses_poll_the_stop_flag():
    """Both sleep paths (display and headless) must break early on a flag, so
    a graceful-stop request lands within ~1s rather than after a full poll."""
    import inspect
    import mt5_trader

    src = inspect.getsource(mt5_trader.main)
    # Count stop-flag polls: one in the loop body, two in the sleep paths.
    assert src.count("_stop_file_requested()") >= 3, (
        "expected stop-flag polls in the loop body and both sleep paths"
    )
