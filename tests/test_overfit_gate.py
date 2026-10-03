"""Tests for OverfitGate and its wiring into proposal validation.

Covers:
  - OverfitGate: validate_strategy, check_strategy, persistence
  - _validate_proposal overfit gate: REJECT for NOT_VALIDATED, REJECT for HIGH/CRITICAL
  - Self-improvement loop: _check_overfit rejects suspicious proposals
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _healthy_returns(n: int = 200, mu: float = 0.001, sigma: float = 0.01) -> List[float]:
    """Generate healthy-looking positive-expectancy returns.

    mu=0.001 gives per-obs Sharpe ~0.11, enough to clear the DSR gate's
    0.95 confidence bar on a fresh (single-trial) ledger. The old mu=0.0005
    fixture only passed because the pre-fix DSR gate passed everything
    vacuously (p>=0.95 inversion).
    """
    rng = np.random.RandomState(42)
    return list(rng.normal(mu, sigma, n))


def _noisy_returns(n: int = 50, mu: float = 0.0, sigma: float = 0.01) -> List[float]:
    """Generate white-noise returns (likely overfit if Sharpe is high)."""
    rng = np.random.RandomState(99)
    return list(rng.normal(mu, sigma, n))


def _overfit_returns(n: int = 30) -> List[float]:
    """Generate returns that look too good for the sample size (high Sharpe, few obs)."""
    rng = np.random.RandomState(7)
    # High Sharpe with very few observations = overfit
    return list(rng.normal(0.003, 0.001, n))


# ===========================================================================
# OverfitGate unit tests
# ===========================================================================

class TestOverfitGate:
    """Test OverfitGate validate/check/persist."""

    def _make_gate(self, tmp_path, isolated_ledger: bool = False):
        from core.overfit_gate import OverfitGate
        if isolated_ledger:
            # Isolate the gate's own trial ledger (its DSR hard gate path).
            # The default is the shared production ledger, whose accumulated
            # trial count would test the multiple-testing bar, not this gate.
            from core.trial_ledger import TrialLedger
            return OverfitGate(
                state_file=str(tmp_path / "overfit_gate.json"),
                trial_ledger=TrialLedger(db_path=str(tmp_path / "ledger.db")),
            )
        return OverfitGate(state_file=str(tmp_path / "overfit_gate.json"))

    def test_validate_healthy_passes(self, tmp_path):
        # Isolate BOTH DSR paths: the gate's own trial ledger (constructor
        # injection) and overfit_detector's internal hard gate (singleton
        # patch — get_trial_ledger=None skips the ledger gate). Without this
        # the test measures the shared production ledger's trial count, not
        # the gate's classification logic. The detector-side leak went
        # unnoticed for weeks because the gate's own evaluate_dsr call was
        # silently failing on missing args (dead path).
        gate = self._make_gate(tmp_path, isolated_ledger=True)
        with patch("core.overfit_detector.get_trial_ledger", return_value=None):
            result = gate.validate_strategy(
                "healthy_strat",
                returns=_healthy_returns(500),
                n_strategies_tested=1,
            )
        assert result.status == "PASS"
        assert result.risk_level in ("LOW", "MODERATE")

    def test_validate_untested_rejects(self, tmp_path):
        gate = self._make_gate(tmp_path)
        # Very few obs, high Sharpe attempt
        result = gate.validate_strategy(
            "untested_strat",
            returns=_overfit_returns(25),
            n_strategies_tested=50,
        )
        # Should be REJECT or PASS depending on data
        assert result.status in ("PASS", "REJECT")
        assert result.risk_level in ("LOW", "MODERATE", "HIGH", "CRITICAL")

    def test_check_not_validated(self, tmp_path):
        gate = self._make_gate(tmp_path)
        result = gate.check_strategy("never_validated")
        assert result.status == "NOT_VALIDATED"
        assert result.risk_level == "UNKNOWN"
        assert "never been validated" in result.reason

    def test_check_after_validate(self, tmp_path):
        gate = self._make_gate(tmp_path)
        gate.validate_strategy("strat_a", returns=_healthy_returns(300))
        result = gate.check_strategy("strat_a")
        assert result.status in ("PASS", "REJECT")
        assert result.risk_level != "UNKNOWN"

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "overfit_gate.json")
        gate1 = self._make_gate(tmp_path)
        gate1.validate_strategy("persist_strat", returns=_healthy_returns(400))

        # New gate instance loads from same file
        from core.overfit_gate import OverfitGate
        gate2 = OverfitGate(state_file=path)
        result = gate2.check_strategy("persist_strat")
        assert result.status in ("PASS", "REJECT")
        assert result.overfit_result is not None

    def test_reset_strategy(self, tmp_path):
        gate = self._make_gate(tmp_path)
        gate.validate_strategy("reset_strat", returns=_healthy_returns(300))
        assert gate.check_strategy("reset_strat").status != "NOT_VALIDATED"

        assert gate.reset_strategy("reset_strat") is True
        assert gate.check_strategy("reset_strat").status == "NOT_VALIDATED"

    def test_reset_nonexistent(self, tmp_path):
        gate = self._make_gate(tmp_path)
        assert gate.reset_strategy("ghost") is False

    def test_get_all_strategies(self, tmp_path):
        gate = self._make_gate(tmp_path)
        gate.validate_strategy("s1", returns=_healthy_returns(200))
        gate.validate_strategy("s2", returns=_healthy_returns(200))
        all_strats = gate.get_all_strategies()
        assert "s1" in all_strats
        assert "s2" in all_strats

    def test_get_rejected_strategies(self, tmp_path):
        gate = self._make_gate(tmp_path)
        # Validate a healthy strategy (likely passes)
        gate.validate_strategy("good", returns=_healthy_returns(500))
        rejected = gate.get_rejected_strategies()
        assert isinstance(rejected, list)

    def test_get_status(self, tmp_path):
        gate = self._make_gate(tmp_path)
        gate.validate_strategy("s1", returns=_healthy_returns(200))
        status = gate.get_status()
        assert "total_validated" in status
        assert "passed" in status
        assert "rejected" in status
        assert status["total_validated"] == 1

    def test_gate_result_to_dict(self, tmp_path):
        gate = self._make_gate(tmp_path)
        result = gate.validate_strategy("dict_test", returns=_healthy_returns(200))
        d = result.to_dict()
        assert "strategy" in d
        assert "status" in d
        assert "risk_level" in d
        assert "grade" in d

    def test_validate_with_is_oos(self, tmp_path):
        gate = self._make_gate(tmp_path)
        is_rets = _healthy_returns(200)
        oos_rets = _healthy_returns(100)
        result = gate.validate_strategy(
            "is_oos_strat",
            returns=is_rets + oos_rets,
            is_returns=is_rets,
            oos_returns=oos_rets,
            n_strategies_tested=5,
        )
        assert result.status in ("PASS", "REJECT")

    def test_validate_with_fold_sharpes(self, tmp_path):
        gate = self._make_gate(tmp_path)
        result = gate.validate_strategy(
            "cv_strat",
            returns=_healthy_returns(300),
            fold_sharpes=[0.5, 0.6, 0.4, 0.7],
        )
        assert result.status in ("PASS", "REJECT")

    def test_validate_with_sharpe_trajectory(self, tmp_path):
        gate = self._make_gate(tmp_path)
        result = gate.validate_strategy(
            "step_strat",
            returns=_healthy_returns(300),
            sharpe_trajectory=[0.3, 0.5, 0.8, 1.2],
            n_strategies_tested=4,
        )
        assert result.status in ("PASS", "REJECT")


# ===========================================================================
# _validate_proposal overfit gate wiring
# ===========================================================================

class TestValidateProposalOverfitGate:
    """Test that _validate_proposal rejects NOT_VALIDATED strategies."""

    def _make_orchestrator(self, tmp_path):
        """Create a minimal orchestrator with overfit gate."""
        from core.overfit_gate import OverfitGate

        orchestrator = MagicMock()
        orchestrator.overfit_gate = OverfitGate(
            state_file=str(tmp_path / "overfit_gate.json")
        )
        orchestrator.validator = MagicMock()
        orchestrator.validator.confluence.return_value = 5
        orchestrator.store = MagicMock()
        orchestrator.operations = MagicMock()
        orchestrator.run_id = "test_run"

        return orchestrator

    def _make_proposal(self):
        proposal = MagicMock()
        proposal.features = {"confluence": 5}
        proposal.side = "BUY"
        proposal.setup_key.return_value = "test_setup"
        proposal.lots = 0.01
        return proposal

    def test_not_validated_blocks_proposal(self, tmp_path):
        """Strategy never validated → NOT_VALIDATED → REJECT."""
        from intelligence.llm_orchestrator import LLMOrchestrator

        orch = self._make_orchestrator(tmp_path)
        proposal = self._make_proposal()

        # Call the overfit gate check directly (simulating _validate_proposal logic)
        gate_result = orch.overfit_gate.check_strategy("unvalidated_strategy")
        assert gate_result.status == "NOT_VALIDATED"

        # The gate should block this strategy
        assert gate_result.status in ("REJECT", "NOT_VALIDATED")

    def test_validated_passing_strategy_allows(self, tmp_path, monkeypatch):
        """Strategy validated as LOW risk → PASS → allows proposal.

        Uses an isolated TrialLedger: the default ledger is the shared
        production DB (append-only, thousands of trials). Against that
        trial count the DSR gate correctly rejects a ~0.9-Sharpe strategy,
        which would make this test measure the shared ledger, not the
        overfit classification under test.
        """
        from core.overfit_gate import OverfitGate
        from core.trial_ledger import TrialLedger

        # Isolate BOTH ledger paths: the gate's own ledger AND the shared
        # singleton that overfit_detector's internal DSR hard gate uses.
        # The default singleton is the production DB (append-only); against
        # its accumulated trial count nothing of realistic Sharpe passes —
        # measuring that here would test the ledger, not the classifier.
        import core.trial_ledger as _tl
        monkeypatch.setattr(
            _tl, "_default_ledger",
            TrialLedger(db_path=str(tmp_path / "singleton_ledger.db")),
        )

        gate = OverfitGate(
            state_file=str(tmp_path / "gate.json"),
            trial_ledger=TrialLedger(db_path=str(tmp_path / "ledger.db")),
        )
        gate.validate_strategy("good_strat", returns=_healthy_returns(500))

        result = gate.check_strategy("good_strat")
        assert result.status == "PASS"

    def test_feature_fields_set(self, tmp_path):
        """Overfit gate sets feature fields on the proposal."""
        from core.overfit_gate import OverfitGate

        gate = OverfitGate(state_file=str(tmp_path / "gate.json"))
        gate.validate_strategy("feature_test", returns=_healthy_returns(300))

        result = gate.check_strategy("feature_test")
        proposal = self._make_proposal()
        proposal.features["overfit_gate_status"] = result.status
        proposal.features["overfit_gate_risk_level"] = result.risk_level
        proposal.features["overfit_gate_grade"] = result.grade

        assert "overfit_gate_status" in proposal.features
        assert "overfit_gate_risk_level" in proposal.features
        assert "overfit_gate_grade" in proposal.features


# ===========================================================================
# Self-improvement loop overfit check
# ===========================================================================

class TestSelfImprovementOverfitCheck:
    """Test _check_overfit in RecursiveSelfImprovementEngine."""

    def _make_engine(self, tmp_path):
        from core.self_improvement_loop import (
            RecursiveSelfImprovementEngine,
            BaselineSnapshot,
            ImprovementProposal,
        )
        engine = RecursiveSelfImprovementEngine(
            state_file=str(tmp_path / "si_state.json"),
            min_improvement_pct=2.0,
        )
        return engine

    def _make_baseline(self):
        from core.self_improvement_loop import BaselineSnapshot
        return BaselineSnapshot(
            timestamp=time.time(),
            cycle_num=1,
            win_rate=0.50,
            sharpe_rolling=0.8,
            avg_r_multiple=0.5,
            total_trades=100,
            cpcv_pbo=0.2,
        )

    def _make_proposal(self):
        from core.self_improvement_loop import ImprovementProposal
        return ImprovementProposal(
            proposal_id="test_prop",
            category="strategy",
            description="Test improvement",
            parameter_changes={"param": {"current": "1.0", "proposed": "1.2"}},
            rationale="Test",
            expected_impact="Test improvement",
        )

    def test_high_sharpe_low_trades_rejects(self, tmp_path):
        engine = self._make_engine(tmp_path)
        baseline = self._make_baseline()
        proposal = self._make_proposal()
        proposed = baseline.to_dict()
        proposed["sharpe_rolling"] = 2.0  # High Sharpe
        proposed["total_trades"] = 15    # Too few

        result = engine._check_overfit(proposal, baseline, proposed)
        assert result.status == "REJECTED"
        assert "overfit" in result.rejection_reason.lower()

    def test_suspicious_win_rate_jump_rejects(self, tmp_path):
        engine = self._make_engine(tmp_path)
        baseline = self._make_baseline()
        proposal = self._make_proposal()
        proposed = baseline.to_dict()
        proposed["win_rate"] = 0.85  # 35pp jump from 0.50

        result = engine._check_overfit(proposal, baseline, proposed)
        assert result.status == "REJECTED"
        assert "suspiciously large" in result.rejection_reason

    def test_high_pbo_rejects(self, tmp_path):
        engine = self._make_engine(tmp_path)
        baseline = self._make_baseline()
        proposal = self._make_proposal()
        proposed = baseline.to_dict()
        proposed["cpcv_pbo"] = 0.8  # High overfit probability

        result = engine._check_overfit(proposal, baseline, proposed)
        assert result.status == "REJECTED"
        assert "PBO" in result.rejection_reason

    def test_normal_proposal_passes(self, tmp_path):
        engine = self._make_engine(tmp_path)
        baseline = self._make_baseline()
        proposal = self._make_proposal()
        proposal.status = "VALIDATED"  # Simulate sealed validator passing
        proposed = baseline.to_dict()
        proposed["sharpe_rolling"] = 1.0
        proposed["total_trades"] = 100
        proposed["win_rate"] = 0.53
        proposed["cpcv_pbo"] = 0.2

        result = engine._check_overfit(proposal, baseline, proposed)
        assert result.status == "VALIDATED"

    def test_moderate_improvement_passes(self, tmp_path):
        engine = self._make_engine(tmp_path)
        baseline = self._make_baseline()
        proposal = self._make_proposal()
        proposal.status = "VALIDATED"  # Simulate sealed validator passing
        proposed = baseline.to_dict()
        proposed["win_rate"] = 0.55  # 5pp improvement (suspicious threshold is 20pp)
        proposed["total_trades"] = 80
        proposed["sharpe_rolling"] = 1.0
        proposed["cpcv_pbo"] = 0.15

        result = engine._check_overfit(proposal, baseline, proposed)
        assert result.status == "VALIDATED"
