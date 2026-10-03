"""Training Orchestrator CLI

Unified command-line interface for managing all training, backtesting,
validation, and autonomous operations.

Usage:
    # Show status of all systems
    python training_orchestrator_cli.py status

    # Run full training pipeline
    python training_orchestrator_cli.py train --all

    # Run backtest validation
    python training_orchestrator_cli.py backtest --symbol EURUSD

    # Check promotion eligibility
    python training_orchestrator_cli.py promote --check

    # Run continuous backtest
    python training_orchestrator_cli.py backtest-continuous --once

    # Check divergence
    python training_orchestrator_cli.py divergence --symbol EURUSD

    # Show alert rules
    python training_orchestrator_cli.py alerts --rules

    # Show metrics
    python training_orchestrator_cli.py metrics --rolling
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Add parent directories to path
_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

log = logging.getLogger("TrainingOrchestratorCLI")


def cmd_status(args):
    """Show status of all autonomous systems."""
    status = {}

    # RL Trainer status
    try:
        from rl_trainer import RLTrainer
        rl = RLTrainer(model_path="rl_model.json")
        status["rl_trainer"] = {
            "total_updates": rl.total_updates,
            "epsilon": round(rl.epsilon, 4),
            "q_table_size": len(rl.q_table),
        }
    except Exception as exc:
        status["rl_trainer"] = {"error": str(exc)}

    # Backtest pipeline status
    try:
        from continuous_backtest_pipeline import ContinuousBacktestPipeline
        pipeline = ContinuousBacktestPipeline()
        status["backtest_pipeline"] = {
            "last_run": pipeline.state.get("last_run"),
            "total_runs": pipeline.state.get("total_runs", 0),
            "successful_runs": pipeline.state.get("successful_runs", 0),
            "failed_runs": pipeline.state.get("failed_runs", 0),
        }
    except Exception as exc:
        status["backtest_pipeline"] = {"error": str(exc)}

    # Auto-promotion status
    try:
        from auto_promotion import AutoPromoter
        promoter = AutoPromoter()
        status["auto_promotion"] = promoter.get_status()
    except Exception as exc:
        status["auto_promotion"] = {"error": str(exc)}

    # Divergence monitor status
    try:
        from divergence_monitor import DivergenceMonitor
        monitor = DivergenceMonitor()
        status["divergence_monitor"] = {
            "last_check": monitor.state.get("last_check"),
            "alerts_count": len(monitor.state.get("alerts", [])),
        }
    except Exception as exc:
        status["divergence_monitor"] = {"error": str(exc)}

    # Alert rules status
    try:
        from alert_rules import AlertRulesEngine
        engine = AlertRulesEngine()
        status["alert_rules"] = engine.get_status()
    except Exception as exc:
        status["alert_rules"] = {"error": str(exc)}

    # Degradation detector status
    try:
        from degradation_detector import DegradationDetector
        detector = DegradationDetector()
        status["degradation_detector"] = detector.get_status()
    except Exception as exc:
        status["degradation_detector"] = {"error": str(exc)}

    # Metrics persistence status
    try:
        from metrics_persistence import MetricsStore
        store = MetricsStore()
        rolling = store.get_rolling_metrics(window=50)
        status["metrics_persistence"] = {
            "trade_count": rolling.get("trade_count", 0),
            "rolling_sharpe": rolling.get("rolling_sharpe"),
            "rolling_win_rate": rolling.get("win_rate"),
        }
    except Exception as exc:
        status["metrics_persistence"] = {"error": str(exc)}

    # Model registry
    try:
        from model_registry import list_models
        models = list_models()
        status["model_registry"] = {
            "total_models": len(models),
            "approved": len([m for m in models if m.get("status") == "APPROVED"]),
        }
    except Exception as exc:
        status["model_registry"] = {"error": str(exc)}

    print(json.dumps(status, indent=2, default=str))


def cmd_train(args):
    """Run training pipeline."""
    results = {}

    if args.rl or args.all:
        print("Training RL model...")
        try:
            from rl_trainer import RLTrainer
            rl = RLTrainer(model_path="rl_model.json")
            # Simulate a few updates
            for i in range(10):
                state = {"rsi_14": 50 + i, "atr_normalized": 0.005}
                rl.update(state, "BUY", 0.5, state, done=True)
            rl.save_model()
            results["rl"] = {"updates": rl.total_updates, "epsilon": rl.epsilon}
            print(f"  RL: {rl.total_updates} updates, epsilon={rl.epsilon:.4f}")
        except Exception as exc:
            results["rl"] = {"error": str(exc)}
            print(f"  RL failed: {exc}")

    if args.ppo or args.all:
        print("Training PPO model...")
        try:
            from ppo_trainer import train_ppo_offline
            ppo_result = train_ppo_offline(
                episodes=2, updates=1, max_steps=10,
                symbol="EURUSD", seed=42, simulator="kronos",
            )
            results["ppo"] = ppo_result
            print(f"  PPO: mean_reward={ppo_result.get('metrics', {}).get('mean_reward', 0):.3f}")
        except Exception as exc:
            results["ppo"] = {"error": str(exc)}
            print(f"  PPO failed: {exc}")

    print(json.dumps(results, indent=2, default=str))


def cmd_backtest(args):
    """Run backtest validation."""
    try:
        from continuous_backtest_pipeline import ContinuousBacktestPipeline
        pipeline = ContinuousBacktestPipeline()

        if args.once:
            result = pipeline.run_full_validation(
                symbol=args.symbol,
                timeframe=args.timeframe,
                bars=args.bars,
            )
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Running continuous backtest for {args.symbol}...")
            result = pipeline.run_validation_cycle(
                symbol=args.symbol,
                strategy_name="default",
            )
            print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        print(f"Backtest failed: {exc}")
        sys.exit(1)


def cmd_promote(args):
    """Check promotion eligibility."""
    try:
        from auto_promotion import AutoPromoter
        promoter = AutoPromoter()

        if args.check:
            results = promoter.check_all_strategies()
            for r in results:
                status = "PROMOTED" if r["promoted"] else "NO_ACTION"
                print(f"{r['strategy_key']}: {status} - {r['reason']}")
        elif args.strategy:
            result = promoter.promote_strategy(args.strategy)
            print(json.dumps(result, indent=2, default=str))
        else:
            # Show all strategies
            history = promoter.get_promotion_history()
            print(json.dumps(history, indent=2, default=str))
    except Exception as exc:
        print(f"Promotion check failed: {exc}")
        sys.exit(1)


def cmd_divergence(args):
    """Check divergence."""
    try:
        from divergence_monitor import DivergenceMonitor
        monitor = DivergenceMonitor()

        result = monitor.check_divergence(args.symbol)
        print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        print(f"Divergence check failed: {exc}")
        sys.exit(1)


def cmd_alerts(args):
    """Manage alert rules."""
    try:
        from alert_rules import AlertRulesEngine
        engine = AlertRulesEngine()

        if args.rules:
            rules = engine.get_rules()
            print(json.dumps(rules, indent=2, default=str))
        elif args.recent:
            alerts = engine.get_recent_alerts(limit=args.limit)
            print(json.dumps(alerts, indent=2, default=str))
        else:
            status = engine.get_status()
            print(json.dumps(status, indent=2, default=str))
    except Exception as exc:
        print(f"Alert rules failed: {exc}")
        sys.exit(1)


def cmd_metrics(args):
    """Show metrics."""
    try:
        from metrics_persistence import MetricsStore
        store = MetricsStore()

        if args.rolling:
            result = store.get_rolling_metrics(window=args.window)
            print(json.dumps(result, indent=2, default=str))
        elif args.latency:
            result = store.get_latency_stats(args.latency)
            print(json.dumps(result, indent=2, default=str))
        elif args.costs:
            result = store.get_cost_summary()
            print(json.dumps(result, indent=2, default=str))
        elif args.drawdown:
            result = store.get_drawdown_duration()
            print(json.dumps(result, indent=2, default=str))
        else:
            # Show all
            rolling = store.get_rolling_metrics(window=50)
            costs = store.get_cost_summary()
            dd = store.get_drawdown_duration()
            print(json.dumps({
                "rolling_metrics": rolling,
                "cost_summary": costs,
                "drawdown": dd,
            }, indent=2, default=str))
    except Exception as exc:
        print(f"Metrics failed: {exc}")
        sys.exit(1)


def cmd_degradation(args):
    """Check degradation status."""
    try:
        from degradation_detector import DegradationDetector
        detector = DegradationDetector()

        if args.status:
            status = detector.get_status()
            print(json.dumps(status, indent=2, default=str))
        elif args.resume:
            success = detector.resume(operator_override=args.override)
            print("Resumed" if success else "Failed - operator override required")
        else:
            status = detector.get_status()
            print(json.dumps(status, indent=2, default=str))
    except Exception as exc:
        print(f"Degradation check failed: {exc}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Training Orchestrator CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status
    subparsers.add_parser("status", help="Show status of all systems")

    # Train
    train_parser = subparsers.add_parser("train", help="Run training pipeline")
    train_parser.add_argument("--rl", action="store_true", help="Train RL model")
    train_parser.add_argument("--ppo", action="store_true", help="Train PPO model")
    train_parser.add_argument("--all", action="store_true", help="Train all models")

    # Backtest
    bt_parser = subparsers.add_parser("backtest", help="Run backtest validation")
    bt_parser.add_argument("--symbol", default="EURUSD", help="Symbol to backtest")
    bt_parser.add_argument("--timeframe", default="H1", help="Timeframe")
    bt_parser.add_argument("--bars", type=int, default=500, help="Number of bars")
    bt_parser.add_argument("--once", action="store_true", help="Run once")

    # Promote
    promo_parser = subparsers.add_parser("promote", help="Check promotion eligibility")
    promo_parser.add_argument("--check", action="store_true", help="Check all strategies")
    promo_parser.add_argument("--strategy", type=str, help="Specific strategy to promote")

    # Divergence
    div_parser = subparsers.add_parser("divergence", help="Check divergence")
    div_parser.add_argument("--symbol", default="EURUSD", help="Symbol to check")

    # Alerts
    alerts_parser = subparsers.add_parser("alerts", help="Manage alert rules")
    alerts_parser.add_argument("--rules", action="store_true", help="Show all rules")
    alerts_parser.add_argument("--recent", action="store_true", help="Show recent alerts")
    alerts_parser.add_argument("--limit", type=int, default=20, help="Number of alerts")

    # Metrics
    metrics_parser = subparsers.add_parser("metrics", help="Show metrics")
    metrics_parser.add_argument("--rolling", action="store_true", help="Show rolling metrics")
    metrics_parser.add_argument("--latency", type=str, help="Show latency for component")
    metrics_parser.add_argument("--costs", action="store_true", help="Show cost summary")
    metrics_parser.add_argument("--drawdown", action="store_true", help="Show drawdown")
    metrics_parser.add_argument("--window", type=int, default=50, help="Rolling window size")

    # Degradation
    deg_parser = subparsers.add_parser("degradation", help="Check degradation")
    deg_parser.add_argument("--status", action="store_true", help="Show status")
    deg_parser.add_argument("--resume", action="store_true", help="Resume from freeze")
    deg_parser.add_argument("--override", action="store_true", help="Operator override")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    commands = {
        "status": cmd_status,
        "train": cmd_train,
        "backtest": cmd_backtest,
        "promote": cmd_promote,
        "divergence": cmd_divergence,
        "alerts": cmd_alerts,
        "metrics": cmd_metrics,
        "degradation": cmd_degradation,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
