"""Boot-stage deadlines must fit the work they bound (2026-10-16).

``bootstrap_macro_seed`` timed out on most boots and the causal graph silently
failed to seed.  The stage was not slow and no provider hung: it makes real
network calls and was measured at 39.2s cold against a 45s deadline -- ~87%
utilisation, so ordinary variance tipped it over.  Because the stage fails
open, boot continued and the only trace was one degraded marker.

Two network-bound stages therefore take a measured per-stage default instead
of the shared 45s, while ``BOOT_STAGE_BUDGET_S`` stays an operator override
that always wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import autonomous_bootstrap as ab  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Capture the budget each stage is actually run under."""
    seen = {}

    def _fake(stage, fn, *, budgets=None, timeout_s=None, emit_event=None):
        seen[stage] = (budgets or {}).get(stage)
        return {"ok": True, "result": {}, "error": None,
                "timed_out": False, "elapsed_s": 0.0}

    monkeypatch.setattr(ab, "run_stage_with_deadline", _fake)
    return seen


def test_network_stages_get_measured_headroom(monkeypatch, captured):
    """The two network-bound stages must exceed the shared 45s default."""
    monkeypatch.delenv("BOOT_STAGE_BUDGET_S", raising=False)

    ab._boot_stage("bootstrap_macro_seed", lambda: None, 45.0)
    ab._boot_stage("bootstrap_global_macro_bridge", lambda: None, 45.0)

    assert captured["bootstrap_macro_seed"] == 90.0
    assert captured["bootstrap_global_macro_bridge"] == 90.0
    assert captured["bootstrap_macro_seed"] > 45.0, (
        "39.2s measured cold against a 45s deadline leaves no headroom; "
        "the seed will time out again")


def test_local_stages_keep_the_shared_default(monkeypatch, captured):
    """Stages that do not fan out to the network are unchanged."""
    monkeypatch.delenv("BOOT_STAGE_BUDGET_S", raising=False)

    for stage in ("bootstrap_alternative_data", "bootstrap_yahoo_finance",
                  "bootstrap_cftc_cot", "bootstrap_eia_petroleum",
                  "bootstrap_forexfactory", "bootstrap_exness"):
        ab._boot_stage(stage, lambda: None, 45.0)
        assert captured[stage] == 45.0, stage


def _call(stage: str, fn=None) -> object:
    """Invoke _boot_stage the way the real call sites do.

    Every call site resolves its budget through ``_stage_budget(default)``,
    which owns the env lookup, so the test goes through it rather than
    reimplementing the read.
    """
    return ab._boot_stage(stage, fn or (lambda: None), ab._stage_budget(45.0))


def test_operator_override_always_wins(monkeypatch, captured):
    """An explicit BOOT_STAGE_BUDGET_S must still govern every stage."""
    monkeypatch.setenv("BOOT_STAGE_BUDGET_S", "12")

    _call("bootstrap_macro_seed")
    _call("bootstrap_alternative_data")

    assert captured["bootstrap_macro_seed"] == 12
    assert captured["bootstrap_alternative_data"] == 12


def test_blank_override_is_not_treated_as_a_pin(monkeypatch, captured):
    """An empty/whitespace override must not collapse the raised budget.

    ``_stage_budget`` falls back to the caller's 45s default for a blank
    value, and since a blank override is not an operator pin, ``_boot_stage``
    then applies its measured network budget on top -- so the seed keeps its
    headroom instead of being silently pinned back to the too-tight default.
    """
    for blank in ("", "   "):
        monkeypatch.setenv("BOOT_STAGE_BUDGET_S", blank)
        assert ab._stage_budget(45.0) == 45.0, repr(blank)
        _call("bootstrap_macro_seed")
        assert captured["bootstrap_macro_seed"] == 90.0, repr(blank)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5"])
def test_bad_override_falls_back_instead_of_crashing(monkeypatch, raw, caplog):
    """A malformed override must never take boot down (2026-10-16).

    The previous inline read, ``float(os.getenv("BOOT_STAGE_BUDGET_S", "45"))``,
    returns the empty string when the variable is set-but-blank, so
    ``BOOT_STAGE_BUDGET_S=`` raised ValueError at all eight call sites and
    crashed boot before any stage ran.
    """
    monkeypatch.setenv("BOOT_STAGE_BUDGET_S", raw)

    with caplog.at_level("WARNING", logger="core.autonomous_bootstrap"):
        assert ab._stage_budget(45.0) == 45.0

    assert "ValueError" not in caplog.text


def test_valid_override_is_honoured(monkeypatch):
    monkeypatch.setenv("BOOT_STAGE_BUDGET_S", "12.5")
    assert ab._stage_budget(45.0) == 12.5


def test_timeout_still_fails_open_with_a_marker(monkeypatch, caplog):
    """The deadline must remain a real bound: over budget -> TIMEOUT marker.

    The fix widens the budget; it must not remove the deadline, or a genuinely
    hung provider would eat minutes of boot with no marker at all.  Asserted on
    the emitted WARNING (which names the budget in force) rather than on the
    watchdog's error text, which _boot_stage passes through verbatim.
    """
    monkeypatch.delenv("BOOT_STAGE_BUDGET_S", raising=False)
    monkeypatch.setattr(
        ab, "run_stage_with_deadline",
        lambda stage, fn, **kw: {"ok": False, "result": None,
                                 "error": "stage exceeded 90s budget; abandoned",
                                 "timed_out": True, "elapsed_s": 90.0})

    with caplog.at_level("WARNING", logger="core.autonomous_bootstrap"):
        result = _call("bootstrap_macro_seed")

    assert result["status"] == "TIMEOUT"
    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "failing open" in warnings
    assert "bootstrap_macro_seed" in warnings
    # The widened budget must be the one reported, or the log lies about
    # which deadline actually tripped.
    assert "90s budget" in warnings