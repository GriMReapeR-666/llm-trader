import os
import tempfile
import pytest
from operations.strategy_lifecycle import seed_registry, transition, current_states, block_info
from research.bayesian_walk_forward_sandbox import BayesianWalkForwardSandbox


class TestStrategyGovernanceHardening:
    def test_strategy_unfreeze_and_retire(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            # 1. Seed registry
            seed_registry(db_path, {"test_setup_v1": {}}, fingerprint="fp1", actor="system")
            states = current_states(db_path)
            assert states["test_setup_v1"] == "DEPLOYED"

            # 2. Demote to DECAYING then FROZEN
            transition(db_path, "test_setup_v1", "DECAYING", actor="evaluation", allow_auto=True)
            transition(db_path, "test_setup_v1", "FROZEN", actor="evaluation", allow_auto=True)
            assert block_info(db_path, "test_setup_v1") is not None
            assert block_info(db_path, "test_setup_v1")["state"] == "FROZEN"

            # 3. Explicit operator unfreeze -> DEPLOYED
            res_unfreeze = transition(
                db_path, "test_setup_v1", "DEPLOYED",
                actor="operator:tester", reason="Operator reviewed setup recovery",
            )
            assert res_unfreeze["state"] == "DEPLOYED"
            assert block_info(db_path, "test_setup_v1") is None

            # 4. Operator retire -> RETIRED
            res_retire = transition(
                db_path, "test_setup_v1", "RETIRED",
                actor="operator:tester", reason="Operator retired setup permanently",
            )
            assert res_retire["state"] == "RETIRED"
            assert block_info(db_path, "test_setup_v1")["state"] == "RETIRED"

            # 5. Terminal state guard: cannot unfreeze from RETIRED
            with pytest.raises(Exception):
                transition(db_path, "test_setup_v1", "DEPLOYED", actor="operator:tester")

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_bayesian_walk_forward_sandbox(self):
        sandbox = BayesianWalkForwardSandbox(train_minimum=30, test_size=10, step=10)
        # Sample series of 60 trade R multiples
        returns = [
            1.5, -1.0, 2.0, -0.8, 1.2, -1.0, 0.5, 1.8, -1.0, 2.2,
            -0.9, 1.4, 1.1, -1.0, 1.6, -1.0, 0.9, 2.1, -1.0, 1.3,
        ] * 3

        report = sandbox.tune(
            setup_id="liquidity_sweep_reversal_v1",
            returns=returns,
            candidate_grid=[
                {"min_rr": 1.5, "rr_multiplier": 0.85, "name": "conservative"},
                {"min_rr": 2.0, "rr_multiplier": 1.00, "name": "baseline"},
                {"min_rr": 2.5, "rr_multiplier": 1.20, "name": "aggressive"},
            ]
        )

        assert report["status"] == "COMPLETED"
        assert report["setup_id"] == "liquidity_sweep_reversal_v1"
        assert report["execution_authority"] is False # Safety invariant
        assert len(report["candidates_ranked"]) == 3
        assert "optimal_parameters" in report
        assert "optimal_metrics" in report
        assert report["optimal_metrics"]["posterior_mean_win_rate"] > 0.0
        assert report["optimal_metrics"]["bayesian_kelly_f"] >= 0.0
