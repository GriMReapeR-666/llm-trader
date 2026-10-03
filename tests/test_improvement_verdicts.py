"""Regression: audit gap #3 — improvement proposals must reach explicit gate verdicts.

Pins the ATM GRPO kwarg fix (n_contexts → contexts) so the training leg can
never silently zero again, and that GRPO reports stay honest about authority.
"""
from __future__ import annotations

import math


class TestATMGrpoLeg:
    def test_train_grpo_uses_valid_signature(self):
        """The old call used n_contexts= (nonexistent kwarg) → TypeError →
        {'reward': 0} every boot. It must now actually train and return the
        real mean_group_reward."""
        from core.automated_training_manager import AutomatedTrainingManager
        mgr = AutomatedTrainingManager()
        out = mgr._train_grpo("EURUSD")
        assert "reward" in out
        assert isinstance(out["reward"], float)
        assert math.isfinite(out["reward"])

    def test_train_grpo_report_is_training_only(self):
        from research.grpo_proposer import train_grpo_offline
        report = train_grpo_offline(contexts=4, group_size=3, updates=1, epochs=1, seed=3, artifact_path="")
        # Honest authority flags unchanged: training never grants execution.
        assert report["execution_authority"] is False
        assert report["risk_controller_frozen"] is True
        assert "mean_group_reward" in report
