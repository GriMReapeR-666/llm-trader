"""Loop-integration tests for advisory quantitative evidence wiring.

Verifies that the survival/edge audit (EV, risk-of-ruin, quarter-Kelly,
run-theory L_max) is correctly composed from realised journal R-multiples
and attached to each decision cycle as bounded advisory evidence without
ever granting execution authority.

Covers:
1. ``core.metrics.calculate_survival_edge_audit`` composition + fail-closed.
2. ``LLMOrchestrator._journal_closed_r_multiples`` journal read/degradation.
3. ``LLMOrchestrator._attach_quant_survival_audit`` per-cycle attachment.
"""
from __future__ import annotations

import math

import pytest

from core.metrics import calculate_survival_edge_audit


def _synthetic_r_multiples(wins: int, losses: int, win_r: float = 2.0, loss_r: float = -1.0):
    return [win_r] * wins + [loss_r] * losses


# ---------------------------------------------------------------------------
# 1. Pure calculator: composition and fail-closed behaviour
# ---------------------------------------------------------------------------

def test_survival_edge_audit_composes_module01_pillars():
    rs = _synthetic_r_multiples(18, 12)  # 60% win rate, 2:1 payoff
    audit = calculate_survival_edge_audit(rs)
    assert audit["status"] == "OK"
    assert audit["sample_count"] == 30
    assert audit["execution_authority"] is False
    assert audit["kind"] == "advisory"
    assert audit["win_rate"] == pytest.approx(0.6, abs=1e-6)
    assert audit["expected_value_r"] == pytest.approx(0.8, abs=1e-6)
    # Positive edge at 1% risk => ruin bound effectively zero over the horizon.
    assert 0.0 <= audit["risk_of_ruin"] < 0.01
    # Quarter-Kelly is capped by the deterministic 2% safety cap.
    assert 0.0 < audit["quarter_kelly_fraction"] <= 0.02
    assert audit["run_theory_l_max"] > 0.0
    for key in ("avg_win_r", "avg_loss_r", "payoff_ratio"):
        assert isinstance(audit[key], float) and math.isfinite(audit[key])


def test_survival_edge_audit_negative_edge_has_unit_ruin():
    rs = _synthetic_r_multiples(5, 25)  # deeply negative expectancy
    audit = calculate_survival_edge_audit(rs)
    assert audit["status"] == "OK"
    assert audit["expected_value_r"] < 0.0
    assert audit["risk_of_ruin"] == 1.0


def test_survival_edge_audit_insufficient_sample_is_fail_closed():
    audit = calculate_survival_edge_audit([1.0, -1.0, 2.0])
    assert audit["status"] == "INSUFFICIENT_DATA"
    assert audit["sample_count"] == 3
    for key in ("win_rate", "expected_value_r", "risk_of_ruin",
                "quarter_kelly_fraction", "run_theory_l_max"):
        assert audit[key] is None
    assert audit["execution_authority"] is False


def test_survival_edge_audit_ignores_malformed_values_without_raising():
    dirty = [2.0, -1.0] * 15 + [None, "not-a-number", float("nan"), float("inf"), object()]
    audit = calculate_survival_edge_audit(dirty)
    assert audit["status"] == "OK"
    assert audit["sample_count"] == 30


def test_survival_edge_audit_zero_loss_sample_uses_unit_default():
    rs = [1.5] * 20
    audit = calculate_survival_edge_audit(rs)
    assert audit["status"] == "OK"
    assert audit["win_rate"] == 1.0
    assert audit["avg_loss_r"] == 1.0  # unit-R default keeps ruin math defined
    assert audit["risk_of_ruin"] == 0.0

# ---------------------------------------------------------------------------
# 2/3. Orchestrator wiring (lightweight instances, no MT5/broker required)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        return _FakeCursor(self._rows)


class _FakeStore:
    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    def _connect(self):
        if self._error is not None:
            raise self._error
        return _FakeConnection(self._rows)


class _FakeOperations:
    def __init__(self):
        self.agent_events = []
        self.plain_events = []

    def agent_event(self, *args, **kwargs):
        self.agent_events.append({"args": args, "kwargs": kwargs})

    def event(self, *args, **kwargs):
        self.plain_events.append({"args": args, "kwargs": kwargs})


def _make_orchestrator(store, operations=None):
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.store = store
    orch.operations = operations or _FakeOperations()
    orch.run_id = "test-run"
    return orch


def test_journal_closed_r_multiples_reads_newest_first():
    rows = [{"r_multiple": r} for r in (2.0, -1.0, 0.5)]
    orch = _make_orchestrator(_FakeStore(rows=rows))
    assert orch._journal_closed_r_multiples() == [2.0, -1.0, 0.5]


def test_journal_closed_r_multiples_drops_nulls_and_degrades_on_error():
    rows = [{"r_multiple": 1.0}, {"r_multiple": None}]
    orch_ok = _make_orchestrator(_FakeStore(rows=rows))
    assert orch_ok._journal_closed_r_multiples() == [1.0]

    orch_bad = _make_orchestrator(_FakeStore(error=RuntimeError("journal locked")))
    assert orch_bad._journal_closed_r_multiples() == []


def test_attach_quant_survival_audit_populates_toon_advisory_only():
    rows = [{"r_multiple": r} for r in _synthetic_r_multiples(18, 12)]
    ops = _FakeOperations()
    orch = _make_orchestrator(_FakeStore(rows=rows), operations=ops)
    toon = {"ohlc": [{"close": 1.0 + i * 0.01} for i in range(60)]}

    orch._attach_quant_survival_audit(toon)

    audit = toon["quant_audit"]
    assert audit["status"] == "OK"
    assert audit["sample_count"] == 30
    assert audit["execution_authority"] is False
    # Hurst computed on the completed-bar closes (formative bar excluded).
    assert audit["hurst_exponent"] is not None
    assert len(ops.agent_events) == 1
    payload = ops.agent_events[0]["args"][4]
    assert payload["execution_authority"] is False


def test_attach_quant_survival_audit_degrades_without_journal_data():
    ops = _FakeOperations()
    orch = _make_orchestrator(_FakeStore(), operations=ops)
    toon = {"ohlc": []}

    orch._attach_quant_survival_audit(toon)  # must not raise

    assert toon["quant_audit"]["status"] == "INSUFFICIENT_DATA"
    assert toon["quant_audit"]["hurst_exponent"] is None
    assert toon["quant_audit"]["execution_authority"] is False


def test_attach_quant_survival_audit_swallows_broken_journal():
    ops = _FakeOperations()
    orch = _make_orchestrator(_FakeStore(error=RuntimeError("boom")), operations=ops)
    toon = {"ohlc": []}
    orch._attach_quant_survival_audit(toon)
    assert "quant_audit" in toon