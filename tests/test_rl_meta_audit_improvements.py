"""Tests verifying the RL and Meta-Learning Audit improvements.

Covers:
1. Meta-loop alpha weights persistence & alpha_ensemble consumption
2. MT5FinRLEnv 100,000x contract multiplier and intra-bar SL/TP
3. PPO / Meta-RL GAE boundary isolation (dones[-1] = True)
4. MetaAdaptiveEngine empty slice division-by-zero protection & get_transition_summary delegation
5. RLTrainer logger and EWC catastrophic forgetting snapshot wiring
6. AutoQuantTools regime policy bar iteration & EWC snapshot trigger
"""

import os
import json
import warnings
import pytest
import numpy as np
import pandas as pd
from pathlib import Path


class TestMetaAlphaWeightsWiring:
    """Test Loop 4 Meta-Learning weights plumbing into alpha_ensemble."""

    def test_combine_alphas_applies_meta_weights(self, tmp_path, monkeypatch):
        from core.alpha_ensemble import combine_alphas

        # Create mock artifacts dir
        art_dir = tmp_path / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        meta_file = art_dir / "meta_alpha_weights.json"
        
        # Suppose meta loop found trend alpha should be heavily weighted
        meta_weights = {
            "trend": 0.80,
            "mean_reversion": 0.20
        }
        meta_file.write_text(json.dumps(meta_weights), encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        # Generate sample alpha series
        idx = pd.date_range("2026-01-01", periods=20, freq="h")
        df = pd.DataFrame({
            "trend": [1.0] * 20,
            "mean_reversion": [-1.0] * 20,
            "close": [100.0 + i for i in range(20)],
        }, index=idx)

        res = combine_alphas(df, ["trend", "mean_reversion"], weighting="equal")
        
        # Meta weights (0.80 vs 0.20) should override equal weights (0.50 vs 0.50)
        assert res.alpha_weights["trend"] > 0.70
        assert res.alpha_weights["mean_reversion"] < 0.30
        assert np.isclose(sum(res.alpha_weights.values()), 1.0)

    def test_run_meta_loop_persists_alpha_weights(self, tmp_path, monkeypatch):
        from core.auto_quant_tools import AutoQuantTools

        art_dir = tmp_path / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(tmp_path)

        tools = AutoQuantTools()

        # Mock get_meta_loop to return applied changes
        class MockMetaLoop:
            def run_meta_analysis(self, **kwargs):
                return {
                    "status": "COMPLETED",
                    "applied_changes": {
                        "alpha_weights": {
                            "trend": {"proposed": 0.75},
                            "carry": 0.25
                        }
                    }
                }

        monkeypatch.setattr(
            "core.meta_loop.get_meta_loop",
            lambda: MockMetaLoop()
        )

        res = tools._run_meta_loop({"ohlc": []})
        assert res["status"] == "COMPLETED"

        meta_file = tmp_path / "artifacts" / "meta_alpha_weights.json"
        assert meta_file.exists()
        saved = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "trend" in saved
        assert saved["trend"] == {"proposed": 0.75}
        assert saved["carry"] == 0.25


class TestMT5FinRLEnvFxContractMultiplierAndSLTP:
    """Verify MT5FinRLEnv contract size multiplier and intra-bar SL/TP."""

    def test_fx_winning_trade_positive_pnl(self):
        from research.mt5_finrl_env import MT5FinRLEnv, MT5EnvConfig

        # Create synthetic OHLC data for EURUSD around 1.1000
        n_bars = 20
        df = pd.DataFrame({
            "open": [1.1000] * n_bars,
            "high": [1.1010] * n_bars,
            "low": [1.0990] * n_bars,
            "close": [1.1000] * n_bars,
            "volume": [1000] * n_bars,
            "atr_14": [0.0010] * n_bars,
        })
        # At step 6, price jumps up to 1.1050 (+50 pips gain)
        df.loc[6, "open"] = 1.1010
        df.loc[6, "high"] = 1.1060
        df.loc[6, "low"] = 1.1005
        df.loc[6, "close"] = 1.1050

        config = MT5EnvConfig(lookback=5, initial_balance=10000.0)
        env = MT5FinRLEnv(df, config=config)
        env.reset()

        # Step 5: Action: BUY
        obs, reward, done, info = env.step(1)
        assert env.position is not None
        assert env.position["side"] == "BUY"

        # Step 6: price jumped to 1.1050 (+50 pips), action=SELL (closes BUY position, reverses)
        obs, reward, done, info = env.step(2)
        assert info.get("closed_pnl") is not None
        assert info["closed_pnl"] > 0
        
        # Realized PnL should be positive: (1.1050 - 1.1000) * 100,000 * vol
        # With the 100,000 multiplier fix, balance is > initial_balance, reward > 0
        assert env.balance > 10000.0
        assert reward > 0

    def test_intra_bar_stop_loss_trigger(self):
        from research.mt5_finrl_env import MT5FinRLEnv, MT5EnvConfig

        # Synthetic EURUSD
        n_bars = 20
        df = pd.DataFrame({
            "open": [1.1000] * n_bars,
            "high": [1.1010] * n_bars,
            "low": [1.0990] * n_bars,
            "close": [1.1000] * n_bars,
            "volume": [1000] * n_bars,
            "atr_14": [0.0010] * n_bars,
        })
        # Bar 6 has a severe low that penetrates SL (entry ~1.1000, atr=0.0010 -> SL ~1.0990)
        df.loc[6, "low"] = 1.0800
        df.loc[6, "close"] = 1.0950

        config = MT5EnvConfig(lookback=5, initial_balance=10000.0)
        env = MT5FinRLEnv(df, config=config)
        env.reset()

        # BUY at step 5
        env.step(1)
        assert env.position is not None
        assert env.position["sl"] > 0

        # Bar 6 should trigger intra-bar SL exit even on HOLD action (0)
        obs, reward, done, info = env.step(0)
        assert env.position is None
        assert info.get("stop_hit") is True


class TestPPOMetaRLBoundarySealing:
    """Verify dones[-1] = True isolates episode and task trajectories."""

    def test_ppo_rollouts_mark_boundary_done(self):
        from research.ppo_trainer import PPOTrainer, collect_rollouts

        trainer = PPOTrainer(obs_dim=9)

        batch, summaries = collect_rollouts(
            trainer,
            episodes=1,
            symbol="EURUSD",
            regime="TREND",
            max_steps=5,
            seed=42,
        )
        # Episode rollout boundary must be sealed
        assert batch["dones"][-1] == True

    def test_meta_rl_rollout_episode_marks_done(self):
        from research.meta_rl_trainer import MetaRLTrainer, _rollout_episode
        from research.kronos_trading_integrator import KronosRLMarketSimulator

        trainer = MetaRLTrainer(obs_dim=9)
        sim = KronosRLMarketSimulator(symbol="EURUSD", regime="TREND", seed=42)

        steps_data, summary = _rollout_episode(
            trainer,
            sim,
            max_steps=5,
            direction=1.0,
            seed=42,
            update_context=False,
            explore=False,
        )
        assert len(steps_data) > 0
        assert steps_data[-1]["done"] is True


class TestMetaAdaptiveEngineEmptySliceProtection:
    """Verify zero warnings and clean default handling on empty perf history."""

    def test_compute_adaptation_alpha_empty_history(self):
        from core.meta_adaptive_engine import FewShotAdapter

        adapter = FewShotAdapter()
        # Catch any RuntimeWarning
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            alpha = adapter.compute_adaptation_alpha("LOW_VOL_TREND")
            
            # Must not emit 'Mean of empty slice' or 'invalid value'
            for w in recorded:
                assert "empty slice" not in str(w.message)
                assert "invalid value" not in str(w.message)

        assert 0.0 <= alpha <= 1.0

    def test_engine_delegates_transition_summary(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine

        engine = MetaAdaptiveEngine()
        summary = engine.get_transition_summary()
        assert isinstance(summary, dict)
        assert "total_transitions" in summary


class TestRLTrainerInitAndEWC:
    """Verify RLTrainer logger instantiation and EWC snapshotting."""

    def test_rl_trainer_init_no_name_error(self):
        from research.rl_trainer import RLTrainer

        trainer = RLTrainer()
        assert hasattr(trainer, "log")
        assert trainer.log.name == "RLTrainer"

    def test_rl_trainer_snapshot_for_ewc(self):
        from research.rl_trainer import RLTrainer

        trainer = RLTrainer()
        # Put some values in q_table
        trainer.q_table["test_state"]["BUY"] = 2.5
        trainer.q_table["test_state"]["SELL"] = -1.0
        trainer.total_updates = 15

        # Call snapshot_for_ewc
        trainer.snapshot_for_ewc("HIGH_VOL_TREND")

        status = trainer.get_ewc_status()
        assert status["available"] is True
        assert status["num_snapshots"] >= 1
        assert "HIGH_VOL_TREND" in status["regimes_stored"]


class TestAutoQuantToolsRegimeEWCWiring:
    """Verify AutoQuantTools forwards regime transitions to RLTrainer EWC."""

    def test_run_meta_adaptive_calls_ewc_on_transition(self, monkeypatch):
        from core.auto_quant_tools import AutoQuantTools

        tools = AutoQuantTools()
        ewc_called = []

        def mock_snapshot(self, regime):
            ewc_called.append(regime)

        monkeypatch.setattr("research.rl_trainer.RLTrainer.snapshot_for_ewc", mock_snapshot)

        mock_transition_res = {
            "transition_detected": True,
            "from_regime": "LOW_VOL_TREND",
            "to_regime": "HIGH_VOL_CRISIS",
            "confidence": 0.95,
        }
        monkeypatch.setattr("core.meta_adaptive_engine.MetaAdaptiveEngine.update", lambda *args, **kwargs: mock_transition_res)

        toon = {
            "regime_allocation": {"regime": {"regime": "HIGH_VOL_CRISIS"}},
            "ohlc": [{"close": 100.0, "open": 99.0}],
        }
        res = tools._run_meta_adaptive(toon)
        assert res["status"] == "OK"
        assert "LOW_VOL_TREND" in ewc_called
