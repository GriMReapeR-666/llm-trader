"""Regression tests: AutonomousBootstrap paper-mode gate scoping.

Paper continuous autonomy must never be hard-blocked by live-deployment
evidence gates (MT5 authorization, broker preflight, tiny-live rollout,
research influence, synthetic WFE/overfit probes). Live boots must keep
failing closed on all of them. Deferred/advisory signals are recorded,
never faked as passing.
"""
from __future__ import annotations

import types
from types import SimpleNamespace

import pytest

from core.autonomous_bootstrap import AutonomousBootstrap, _phase_validate


LIVE_AUTH_BLOCKERS = [
    "MT5 terminal/account authorization is not verified",
    "broker preflight is missing, stale, or invalid",
    "tiny-live rollout approval is absent, expired, or evidence-invalid",
    "learned/adaptive research influence is not approved",
]

FAKE_READINESS_BLOCKERS = LIVE_AUTH_BLOCKERS + [
    "journal integrity audit failed",
]


@pytest.fixture()
def fake_crash_halt(monkeypatch):
    """Deterministic crash-recovery HALT (real module reads live state)."""
    import core.crash_recovery as cr

    monkeypatch.setattr(
        cr, "startup_recovery",
        lambda: SimpleNamespace(action="HALT_AND_ALERT", crash_detected=True,
                                reasons=["test halt"], restored_snapshot=None),
        raising=False,
    )


@pytest.fixture()
def fake_readiness_blocked(monkeypatch):
    import operations.readiness_report as rr

    monkeypatch.setattr(
        rr, "build",
        lambda path, **kw: {"status": "BLOCKED", "blockers": list(FAKE_READINESS_BLOCKERS),
                            "execution_authority": False},
        raising=False,
    )


def _validate(store, paper_mode, client=None):
    return _phase_validate(
        store=store, client=client, config=None, orchestrator=None,
        paper_mode=paper_mode,
    )


def test_paper_mode_defers_live_authority_blockers(
    tmp_path, monkeypatch, fake_crash_halt, fake_readiness_blocked
):
    """All live-authority readiness blockers are deferred in paper mode."""
    import core.autonomous_bootstrap as ab

    monkeypatch.setattr(ab, "_load_strategy_registry", lambda: [])

    result = _validate(SimpleNamespace(path=str(tmp_path / "paper.db")),
                       paper_mode=True)

    assert result.passed is True
    assert result.blockers == []
    readiness = result.details["readiness"]
    assert readiness["paper_mode"] is True
    assert readiness["deferred_live_authority_blockers"] == LIVE_AUTH_BLOCKERS
    # Non-authority blockers stay recorded as advisory — never hidden or faked.
    assert readiness["advisory_blockers"] == ["journal integrity audit failed"]
    assert readiness["status"] == "BLOCKED"


def test_live_mode_still_fails_closed(
    tmp_path, monkeypatch, fake_crash_halt, fake_readiness_blocked
):
    """Live boots block on every readiness gate and crash-recovery HALT."""
    import core.autonomous_bootstrap as ab

    monkeypatch.setattr(ab, "_load_strategy_registry", lambda: [])

    result = _validate(SimpleNamespace(path=str(tmp_path / "live.db")),
                       paper_mode=False, client=None)

    assert result.passed is False
    joined = "\n".join(result.blockers)
    for b in LIVE_AUTH_BLOCKERS:
        assert f"Readiness: {b}" in joined
    assert "Crash recovery: hash chain broken, HALT_AND_ALERT" in result.blockers
    assert "journal integrity audit failed" in joined


def test_sharpe_without_measured_returns_is_not_validated_not_probed(
    tmp_path, monkeypatch, fake_crash_halt, fake_readiness_blocked
):
    """A Sharpe with no measured return series must NOT be gated on a synthetic probe.

    The bootstrap used to build np.random.normal(sharpe/252, 0.01, 200) from the
    backtest's own Sharpe claim, so both gates evaluated a random series and
    their REJECT said nothing about the strategy. A summary statistic is not
    evidence: with no measured returns the gates must report NOT_VALIDATED and
    must not raise a blocker in either mode.
    """
    import core.autonomous_bootstrap as ab
    import core.walk_forward_validator as wfv_mod
    import core.overfit_gate as og_mod

    monkeypatch.setattr(
        ab, "_load_strategy_registry",
        lambda: [{"id": "strat_x", "stage": "FULL"}],
    )

    class _FakePipeline:
        def run_validation_cycle(self, **kwargs):
            return {"verdict": {"passed": True},
                    "stages": {"backtest": {"sharpe": 1.5, "max_drawdown_pct": 5.0}}}

    import sys
    fake_mod = types.ModuleType("research.continuous_backtest_pipeline")
    fake_mod.ContinuousBacktestPipeline = _FakePipeline
    monkeypatch.setitem(sys.modules, "research.continuous_backtest_pipeline", fake_mod)

    def _must_not_be_called(*a, **k):
        raise AssertionError("gate must not evaluate an unmeasured series")

    class _FakeWfv:
        def __init__(self, state_file=None):
            pass

        def walk_forward_efficiency_gate(self, *a, **k):
            return _must_not_be_called()

    monkeypatch.setattr(wfv_mod, "WalkForwardValidator", _FakeWfv, raising=False)

    class _FakeOverfit:
        def __init__(self, store=None):
            pass

        def validate_strategy(self, **kwargs):
            return _must_not_be_called()

    monkeypatch.setattr(og_mod, "OverfitGate", _FakeOverfit, raising=False)

    for mode in (True, False):
        result = _validate(SimpleNamespace(path=str(tmp_path / f"j{mode}.db")),
                           paper_mode=mode)
        # The fixture blocks readiness independently, so assert on the gates
        # specifically: neither WFE nor overfit may be probed or reported.
        assert not any("WFE gate" in b for b in result.blockers), f"paper_mode={mode}"
        assert not any("Overfit gate" in b for b in result.blockers), f"paper_mode={mode}"
        assert result.details["walk_forward"]["strat_x"]["status"] == "NOT_VALIDATED"
        assert result.details["overfit_gate"]["strat_x"]["status"] == "NOT_VALIDATED"


def test_measured_returns_reject_is_advisory_in_paper_and_blocking_live(
    tmp_path, monkeypatch, fake_crash_halt, fake_readiness_blocked
):
    """With real measured returns, a gate REJECT is paper-advisory / live-blocking."""
    import core.autonomous_bootstrap as ab
    import core.walk_forward_validator as wfv_mod
    import core.overfit_gate as og_mod

    monkeypatch.setattr(
        ab, "_load_strategy_registry",
        lambda: [{"id": "strat_x", "stage": "FULL"}],
    )

    measured = [0.001 if i % 3 else -0.002 for i in range(300)]

    class _FakePipeline:
        def run_validation_cycle(self, **kwargs):
            return {"verdict": {"passed": True},
                    "stages": {"backtest": {"sharpe": 1.5, "max_drawdown_pct": 5.0,
                                            "returns": measured}}}

    import sys
    fake_mod = types.ModuleType("research.continuous_backtest_pipeline")
    fake_mod.ContinuousBacktestPipeline = _FakePipeline
    monkeypatch.setitem(sys.modules, "research.continuous_backtest_pipeline", fake_mod)

    class _FakeWfv:
        def __init__(self, state_file=None):
            pass

        def walk_forward_efficiency_gate(self, returns, train_size, test_size):
            return SimpleNamespace(wfe=0.1, passed=False, n_folds=2)

    monkeypatch.setattr(wfv_mod, "WalkForwardValidator", _FakeWfv,
                        raising=False)

    class _FakeOverfit:
        def __init__(self, store=None):
            pass

        def validate_strategy(self, **kwargs):
            return SimpleNamespace(status="REJECT", risk_level="HIGH", grade="F")

    monkeypatch.setattr(og_mod, "OverfitGate", _FakeOverfit, raising=False)

    result = _validate(SimpleNamespace(path=str(tmp_path / "paper.db")),
                       paper_mode=True)
    assert result.passed is True
    assert result.blockers == []
    assert result.details["walk_forward"]["strat_x"]["passed"] is False
    assert result.details["overfit_gate"]["strat_x"]["status"] == "REJECT"

    live = _validate(SimpleNamespace(path=str(tmp_path / "live.db")),
                     paper_mode=False)
    assert live.passed is False
    assert any("WFE gate REJECT for strat_x" in b for b in live.blockers)
    assert any("Overfit gate REJECT for strat_x" in b for b in live.blockers)


def test_bootstrap_store_is_none_tolerant(
    tmp_path, monkeypatch, fake_crash_halt, fake_readiness_blocked
):
    """Regression: harness path passes _plugin_store (may be None), never an
    unbound `store` local (UnboundLocalError silently skipped bootstrap)."""
    import core.autonomous_bootstrap as ab

    monkeypatch.setattr(ab, "_load_strategy_registry", lambda: [])

    boot = AutonomousBootstrap(store=None, paper_mode=True)
    assert boot.store is None and boot.paper_mode is True

    result = _validate(None, paper_mode=True)
    assert result.passed is True
    assert result.details["readiness"]["paper_mode"] is True


def test_mt5_trader_wires_plugin_store_into_bootstrap():
    """The construction site must pass the plugin store, not a bare `store`."""
    with open("mt5_trader.py", "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("AutonomousBootstrap(")
    end = src.index("_bootstrap.run()", start)
    call_src = src[start:end]
    assert "store=_plugin_store" in call_src
    assert "store=store," not in call_src
    assert "paper_mode=bool(getattr(args" in call_src
