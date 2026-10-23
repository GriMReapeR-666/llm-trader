"""CRITICAL #6 - a promotion gate that cannot run must never report a pass.

Every exception handler in ``research/auto_promotion.py`` used to answer
``{"passed": True}``. The aggregator in ``promote_strategy`` already treats a
*missing* gate as a failure, so those six handlers were the only remaining way
a strategy could be promoted on the strength of a crash: a module that failed
to import, an empty trial ledger, or an absent monitor each read as a green
light.

These tests pin the refusal at the gate level, so the shape stays enforced even
if a future refactor wraps a new gate in a ``try``.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "data", "intelligence", "operations", "research"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.auto_promotion import AutoPromoter  # noqa: E402


@pytest.fixture()
def promoter(tmp_path):
    return AutoPromoter(base_dir=str(tmp_path))


def _block(monkeypatch, needle):
    """Make ``import needle...`` raise, without disturbing anything else."""
    import builtins

    real_import = builtins.__import__

    def guarded(name, *a, **kw):
        if needle in name:
            raise ImportError("simulated: %s absent" % needle)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded)


# --- the six sites that used to fail open -------------------------------

def test_dsr_gate_fails_closed_when_module_unavailable(promoter, monkeypatch):
    _block(monkeypatch, "backtest_dsr_integrator")
    res = promoter._check_dsr("alpha_test_1")
    assert res["passed"] is False
    assert res["reason"] == "dsr_unavailable"


def test_dsr_gate_fails_closed_on_empty_trial_ledger(promoter, monkeypatch):
    class FakeIntegrator:
        def __init__(self, **kw):
            pass

        def get_all_gate_results(self, limit=100):
            return []

    mod = type(sys)("research.backtest_dsr_integrator")
    mod.BacktestDSRIntegrator = FakeIntegrator
    monkeypatch.setitem(sys.modules, "research.backtest_dsr_integrator", mod)

    res = promoter._check_dsr("alpha_test_1")
    assert res["passed"] is False
    assert res["reason"] == "trial_ledger_empty"


def test_dsr_gate_does_not_stamp_the_strategy_key_as_a_class(promoter, monkeypatch):
    """A journal strategy key is an id, not a class.

    Passing it as ``strategy_class`` used to scope the DSR family to the
    strategy's OWN trials (~1) -- a bar that invents significance.  The class
    must be derived from the identity instead (canonical taxonomy, global
    family when the id cannot be attributed).
    """
    seen = {}

    class FakeIntegrator:
        def __init__(self, **kw):
            seen.update(kw)

        def get_all_gate_results(self, limit=100):
            return [object()]  # non-empty ledger, so the gate proceeds

    mod = type(sys)("research.backtest_dsr_integrator")
    mod.BacktestDSRIntegrator = FakeIntegrator
    monkeypatch.setitem(sys.modules, "research.backtest_dsr_integrator", mod)

    promoter._check_dsr("alpha_test_1")
    assert "strategy_class" not in seen or seen["strategy_class"] is None, (
        "the promotion DSR gate must not stamp the strategy key as a class tag"
    )


def test_walkforward_gate_fails_closed_when_module_unavailable(promoter, monkeypatch):
    _block(monkeypatch, "bayesian_walk_forward_sandbox")
    res = promoter._check_walkforward("alpha_test_1")
    assert res["passed"] is False
    assert res["reason"] == "walkforward_unavailable"


def test_divergence_gate_fails_closed_when_monitor_unavailable(promoter, monkeypatch):
    _block(monkeypatch, "divergence_monitor")
    res = promoter._check_no_divergence("alpha_test_1")
    assert res["passed"] is False
    assert res["reason"] == "divergence_unavailable"


def test_judge_rubric_gate_fails_closed_when_judge_raises(promoter, monkeypatch):
    mod = type(sys)("research.alpha_miner.alpha_judge")

    def boom(_formula):
        raise RuntimeError("simulated: judge crashed")

    mod.judge_candidate = boom
    monkeypatch.setitem(sys.modules, "research.alpha_miner.alpha_judge", mod)

    res = promoter._check_judge_rubric(
        "alpha_test_1", {"metrics": {"formula": "close.pct_change(5)"}})
    assert res["passed"] is False
    assert res["reason"] == "judge_rubric_unavailable"


def test_openquant_gate_fails_closed_when_module_unavailable(promoter, monkeypatch):
    _block(monkeypatch, "openquant_eval")
    res = promoter._check_openquant_admissibility(
        "alpha_test_1", {"metrics": {"sharpe": 1.4}})
    assert res["passed"] is False
    assert res["reason"] == "openquant_unavailable"


# --- structural guard: no gate may hard-code a pass ---------------------

def test_no_gate_returns_passed_true_from_a_literal(promoter):
    """A hard-coded ``"passed": True`` anywhere in a gate is the fail-open shape."""
    import inspect
    import re

    source = inspect.getsource(type(promoter))
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r'"passed"\s*:\s*True', line)
        and not line.lstrip().startswith(("#", "Every exception"))
    ]
    assert offenders == [], offenders


def test_gate_unavailable_helper_always_refuses(promoter):
    res = promoter._gate_unavailable("some_gate", "because")
    assert res["passed"] is False
    assert res["reason"] == "some_gate"
    assert res["note"] == "because"
    # default detail falls back to the reason, never to a pass
    assert promoter._gate_unavailable("x")["note"] == "x"
