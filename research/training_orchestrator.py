"""Unified Offline Training Orchestrator

Coordinates all training pipelines (RL, PPO, Meta-RL, backtest) into a
single entry point. Can be run standalone or called from the engine loop.

Usage:
    # Run all training pipelines
    python training_orchestrator.py --all

    # Run specific pipelines
    python training_orchestrator.py --rl --ppo

    # Run with custom parameters
    python training_orchestrator.py --rl --ppo --episodes 10 --updates 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger("TrainingOrchestrator")


class TrainingOrchestrator:
    """Coordinates all offline training pipelines."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.artifacts_dir = os.path.join(self.base_dir, "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.results: Dict[str, Any] = {}

    def run_rl_training(
        self,
        episodes: int = 50,
        model_path: str = "",
        register: bool = True,
    ) -> Dict[str, Any]:
        """Run Q-learning RL training on recent trade history."""
        log.info("Starting RL training...")
        start_time = time.time()

        try:
            from research.rl_trainer import RLTrainer

            if not model_path:
                model_path = os.path.join(self.base_dir, "rl_model.json")

            rl = RLTrainer(model_path=model_path)

            # Fetch recent broker-reconciled trades for training; a
            # self-declared journal row must never become a Q-table reward.
            import sqlite3
            _db_path = os.path.join(self.base_dir, "trading_state.db")
            if not os.path.exists(_db_path):
                log.warning("No trading_state.db found, skipping RL training")
                return {"status": "skipped", "reason": "no_trading_db"}

            try:
                from research.research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            _tickets = sorted(broker_reconciled_tickets(_db_path))
            if not _tickets:
                log.info("No broker-reconciled trades for RL training")
                return {"status": "skipped", "reason": "no_broker_reconciled_trades"}

            con = sqlite3.connect(_db_path, timeout=2)
            con.row_factory = sqlite3.Row
            _marks = ",".join("?" for _ in _tickets)
            trades = con.execute(
                "SELECT symbol, side, entry, stop_loss, take_profit, "
                "gross_pnl, risk_amount, volume, opened_at, closed_at "
                "FROM trades WHERE closed_at IS NOT NULL "
                "AND ticket IN (%s) "
                "ORDER BY closed_at DESC LIMIT ?" % _marks,
                (*_tickets, episodes),
            ).fetchall()
            con.close()

            if not trades:
                log.info("No closed trades found for RL training")
                return {"status": "skipped", "reason": "no_trades"}

            # Train on each trade
            train_count = 0
            for trade in trades:
                state = {
                    "direction": 1 if trade["side"] == "BUY" else -1,
                    "rsi_14": 50.0,
                    "atr_normalized": 0.005,
                    "confluence_score": 5,
                    "near_sr": True,
                    "htf_trend": 1 if trade["side"] == "BUY" else -1,
                    "news_sentiment": 0.0,
                    "volatility_regime": "medium",
                    "trend_strength": 0.5,
                    "session": "london",
                }
                reward = 0.0
                if trade["gross_pnl"] and trade["risk_amount"]:
                    reward = trade["gross_pnl"] / max(trade["risk_amount"], 0.01)
                action = "BUY" if trade["side"] == "BUY" else "SELL"
                rl.update(state, action, min(max(reward, -2.0), 2.0), state, done=True)
                train_count += 1

            # Save model
            rl.save_model()

            # Register in model registry
            if register:
                try:
                    from research.model_registry import register as _register_model
                    _hash = hashlib.md5(
                        json.dumps(rl.get_stats(), sort_keys=True).encode()
                    ).hexdigest()[:16]
                    _register_model(
                        model_key="rl_qlearning_v1",
                        version=f"offline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
                        kind="Q-learning",
                        feature_schema_version="v1",
                        training_data_hash=_hash,
                        metrics=rl.get_stats(),
                        artifact_uri=model_path,
                    )
                except Exception as exc:
                    log.debug("Model registry write skipped: %s", exc)

            duration = time.time() - start_time
            result = {
                "status": "completed",
                "backend": "Q-learning",
                "train_count": train_count,
                "q_table_size": len(rl.q_table),
                "epsilon": rl.epsilon,
                "duration_seconds": round(duration, 2),
                "stats": rl.get_stats(),
            }
            log.info(
                "RL training complete: %d updates, %d states, eps=%.3f (%.1fs)",
                train_count, len(rl.q_table), rl.epsilon, duration,
            )
            return result

        except Exception as exc:
            log.error("RL training failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def run_ppo_training(
        self,
        episodes: int = 4,
        updates: int = 2,
        max_steps: int = 20,
        symbol: str = "EURUSD",
        regime: str = "volatile",
        register: bool = True,
    ) -> Dict[str, Any]:
        """Run PPO+GAE training on Kronos simulator."""
        log.info("Starting PPO training...")
        start_time = time.time()

        try:
            from research.ppo_trainer import train_ppo_offline

            artifact_path = os.path.join(self.artifacts_dir, "ppo_position_mgmt.json")

            result = train_ppo_offline(
                episodes=episodes,
                updates=updates,
                max_steps=max_steps,
                symbol=symbol,
                regime=regime,
                seed=7,
                simulator="kronos",
                artifact_path=artifact_path,
                epochs=2,
                batch_size=32,
            )

            # Register in model registry
            if register and result:
                try:
                    from research.model_registry import register as _register_model
                    _hash = hashlib.md5(
                        json.dumps(result.get("config", {}), sort_keys=True).encode()
                    ).hexdigest()[:16]
                    _register_model(
                        model_key="ppo_position_mgmt_v1",
                        version=f"offline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
                        kind="PPO+GAE",
                        feature_schema_version="v1",
                        training_data_hash=_hash,
                        metrics=result.get("metrics", {}),
                        artifact_uri=artifact_path,
                    )
                except Exception as exc:
                    log.debug("PPO model registry write skipped: %s", exc)

            duration = time.time() - start_time
            if result:
                result["duration_seconds"] = round(duration, 2)
                result["status"] = "completed"
            else:
                result = {"status": "skipped", "reason": "no_result"}

            log.info("PPO training complete (%.1fs)", duration)
            return result or {}

        except Exception as exc:
            log.error("PPO training failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def run_backtest(
        self,
        symbol: str = "EURUSD",
        timeframe: str = "H1",
        bars: int = 500,
    ) -> Dict[str, Any]:
        """Run full pipeline backtest."""
        log.info("Starting backtest...")
        start_time = time.time()

        try:
            from research.full_pipeline_backtest import run_backtest as _run_backtest

            result = _run_backtest(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
            )

            duration = time.time() - start_time
            if result:
                result["duration_seconds"] = round(duration, 2)
                result["status"] = "completed"
            else:
                result = {"status": "skipped", "reason": "no_result"}

            log.info("Backtest complete (%.1fs)", duration)
            return result or {}

        except Exception as exc:
            log.error("Backtest failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def run_all(
        self,
        rl_episodes: int = 50,
        ppo_episodes: int = 4,
        ppo_updates: int = 2,
        backtest_bars: int = 500,
    ) -> Dict[str, Any]:
        """Run all training pipelines."""
        log.info("=" * 60)
        log.info("Starting unified training orchestration")
        log.info("=" * 60)

        self.results = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pipelines": {},
        }

        # Run RL training
        self.results["pipelines"]["rl"] = self.run_rl_training(episodes=rl_episodes)

        # Run PPO training
        self.results["pipelines"]["ppo"] = self.run_ppo_training(
            episodes=ppo_episodes,
            updates=ppo_updates,
        )

        # Run backtest
        self.results["pipelines"]["backtest"] = self.run_backtest(bars=backtest_bars)

        # Summary
        completed = sum(
            1 for p in self.results["pipelines"].values()
            if p.get("status") == "completed"
        )
        failed = sum(
            1 for p in self.results["pipelines"].values()
            if p.get("status") == "failed"
        )
        self.results["summary"] = {
            "total_pipelines": 3,
            "completed": completed,
            "failed": failed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save results
        results_path = os.path.join(self.artifacts_dir, "training_results.json")
        try:
            with open(results_path, "w") as f:
                json.dump(self.results, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save training results: %s", exc)

        log.info("=" * 60)
        log.info("Training orchestration complete: %d/%d pipelines succeeded",
                 completed, 3)
        log.info("=" * 60)

        return self.results


def main():
    """CLI entry point for the training orchestrator."""
    parser = argparse.ArgumentParser(description="Unified Offline Training Orchestrator")
    parser.add_argument("--all", action="store_true", help="Run all training pipelines")
    parser.add_argument("--rl", action="store_true", help="Run RL training only")
    parser.add_argument("--ppo", action="store_true", help="Run PPO training only")
    parser.add_argument("--backtest", action="store_true", help="Run backtest only")
    parser.add_argument("--episodes", type=int, default=50, help="RL episodes")
    parser.add_argument("--ppo-episodes", type=int, default=4, help="PPO episodes")
    parser.add_argument("--ppo-updates", type=int, default=2, help="PPO updates")
    parser.add_argument("--bars", type=int, default=500, help="Backtest bars")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol for backtest/PPO")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    orchestrator = TrainingOrchestrator()

    if args.all or (not args.rl and not args.ppo and not args.backtest):
        result = orchestrator.run_all(
            rl_episodes=args.episodes,
            ppo_episodes=args.ppo_episodes,
            ppo_updates=args.ppo_updates,
            backtest_bars=args.bars,
        )
    else:
        result = {"pipelines": {}}
        if args.rl:
            result["pipelines"]["rl"] = orchestrator.run_rl_training(episodes=args.episodes)
        if args.ppo:
            result["pipelines"]["ppo"] = orchestrator.run_ppo_training(
                episodes=args.ppo_episodes,
                updates=args.ppo_updates,
                symbol=args.symbol,
            )
        if args.backtest:
            result["pipelines"]["backtest"] = orchestrator.run_backtest(
                symbol=args.symbol,
                bars=args.bars,
            )

    # Print summary
    print("\n" + "=" * 60)
    print("Training Results:")
    print("=" * 60)
    for name, pipeline in result.get("pipelines", {}).items():
        status = pipeline.get("status", "unknown")
        emoji = "✓" if status == "completed" else "✗" if status == "failed" else "○"
        print(f"  {emoji} {name}: {status}")
        if status == "failed":
            print(f"    Error: {pipeline.get('error', 'unknown')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
