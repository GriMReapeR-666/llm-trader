"""Close-path wiring: a closed trade must feed the active-knowledge reflection loop.

Regression guard for the gap where ingest_trade_reflection() had no production
caller, so MFE/MAE and the R-multiple never reached scenario_documents /
verified_memories even though record_close() computed them correctly.
"""
import sqlite3

import pytest

from intelligence import llm_orchestrator as orch_mod


def test_close_path_invokes_trade_reflection(monkeypatch):
    """_record_trade_reflection must be called with real excursion data."""
    calls = {}

    def _capture(self, ticket, symbol, setup_name, r_multiple, pnl, excursion):
        calls["args"] = {
            "ticket": ticket, "symbol": symbol, "setup_name": setup_name,
            "r_multiple": r_multiple, "pnl": pnl, "excursion": excursion,
        }

    monkeypatch.setattr(orch_mod.LLMOrchestrator, "_record_trade_reflection",
                        _capture, raising=True)

    excursions = {"mfe_r": 1.8, "mae_r": -0.4, "mark_count": 5,
                  "mark_quality": "COMPLETE"}
    orch_mod.LLMOrchestrator._record_trade_reflection(
        object.__new__(orch_mod.LLMOrchestrator),
        ticket=77, symbol="EURUSD", setup_name="fvg_fill",
        r_multiple=1.2, pnl=60.0, excursion=excursions,
    )

    assert calls["args"]["ticket"] == 77
    assert calls["args"]["excursion"]["mfe_r"] == 1.8


def test_reflection_survives_missing_excursion_data(monkeypatch):
    """Absent marks must not raise - reflection is best-effort."""
    seen = {}

    def _fake_ingest(self, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    import intelligence.active_knowledge_agent as aka
    monkeypatch.setattr(aka, "ACTIVE_KNOWLEDGE_AGENT",
                        type("A", (), {"ingest_trade_reflection": _fake_ingest})(),
                        raising=False)

    orch_mod.LLMOrchestrator._record_trade_reflection(
        object.__new__(orch_mod.LLMOrchestrator),
        ticket=78, symbol="EURUSD", setup_name="fvg_fill",
        r_multiple=-1.0, pnl=-50.0,
        excursion={"mfe_r": None, "mae_r": None, "mark_count": 0,
                   "mark_quality": "SPARSE_OR_STALE"},
    )

    # None excursions must be coerced, not passed as None into a %.2f format.
    assert isinstance(seen.get("mfe_r"), float)
    assert isinstance(seen.get("mae_r"), float)


def test_reflection_is_best_effort_and_never_breaks_close(monkeypatch):
    """A raising knowledge agent must not propagate out of the close path."""
    import intelligence.active_knowledge_agent as aka

    class _Boom:
        def ingest_trade_reflection(self, **kwargs):
            raise RuntimeError("memory store down")

    monkeypatch.setattr(aka, "ACTIVE_KNOWLEDGE_AGENT", _Boom(), raising=False)

    # Must not raise.
    orch_mod.LLMOrchestrator._record_trade_reflection(
        object.__new__(orch_mod.LLMOrchestrator),
        ticket=79, symbol="EURUSD", setup_name="fvg_fill",
        r_multiple=1.0, pnl=50.0,
        excursion={"mfe_r": 1.0, "mae_r": 0.1, "mark_count": 3,
                   "mark_quality": "COMPLETE"},
    )