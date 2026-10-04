"""PU retail cost-stress sweep (PU-0156 §8.5): all 14 strategy labels through
run_full_validation with the retail cost-stress overlay ON (2x base spread,
x3 event/rollover-hour spread, nightly swap $-2.5/lot, triple on Wednesday),
plus the random-null baseline under identical stress, then a diff against the
unstressed deep_sweep_5000.json and the baseline_sweep_5000.json null.

Framework demand: "re-run with spreads and slippage 1.5-2x worse — if the edge
disappears, it's fragile." This quantifies exactly that per label.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

STRATEGIES = [
    "kronos_h1", "statarb_eur_chf", "macro_nfp_pullback", "meanrev_bollinger",
    "alpha_055_orderflow", "alpha_098_copula", "agent_execution_rl",
    "agent_regime_supervisor", "model_bocpd_changepoint", "model_kalman_filter",
    "gold_momentum_h4", "spx_volatility_skew", "sprt_hypothesis_gate",
    "default",
]
OUT = ROOT / "artifacts" / "backtest_results" / "cost_stress_sweep_5000.json"
DEEP = ROOT / "artifacts" / "backtest_results" / "deep_sweep_5000.json"
NULLS = ROOT / "artifacts" / "backtest_results" / "baseline_sweep_5000.json"
BARS = 5000

STRESS = {
    "cost_stress": True,
    "stress_spread_multiplier": 2.0,
    "stress_event_spread_multiplier": 3.0,
    "stress_swap_per_lot_per_night": -2.5,
    "stress_triple_swap_weekday": 2,
}


def slim(res):
    stages = res.get("stages", {})
    bt = stages.get("backtest", {})
    cpcv = stages.get("cpcv", {})
    return {
        "strategy": res.get("strategy_name"),
        "verdict": res.get("verdict"),
        "backtest": {
            "status": bt.get("status"),
            "ruleset": bt.get("ruleset"),
            "sharpe": bt.get("sharpe"),
            "total_trades": bt.get("total_trades"),
            "expectancy_r": bt.get("expectancy_r"),
            "total_cost": bt.get("total_cost"),
            "total_swap_cost": bt.get("total_swap_cost"),
        },
        "cpcv": {
            "status": cpcv.get("status"),
            "samples": cpcv.get("samples"),
            "mean_cpcv_sharpe": cpcv.get("mean_cpcv_sharpe"),
            "pbo": cpcv.get("probability_of_backtest_overfitting_pbo"),
            "dsr_conf": cpcv.get("deflated_sharpe_ratio"),
        },
    }


def main():
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
    pipeline = ContinuousBacktestPipeline()
    out = {"symbol": "EURUSD", "timeframe": "H1", "bars": BARS,
           "stress": STRESS, "runs": []}
    for name in STRATEGIES:
        t0 = time.time()
        try:
            res = pipeline.run_full_validation(
                symbol="EURUSD", strategy_name=name, timeframe="H1",
                bars=BARS, run_walkforward=True, run_dsr=True, compare_live=True,
                cost_overrides=STRESS)
            entry = slim(res)
        except Exception as exc:
            entry = {"strategy": name, "error": str(exc)[:300]}
        entry["seconds"] = round(time.time() - t0, 1)
        out["runs"].append(entry)
        bt = entry.get("backtest") or {}
        print(f"[{len(out['runs'])}/{len(STRATEGIES)}] {name}: "
              f"trades={bt.get('total_trades')} sharpe={bt.get('sharpe')} "
              f"swap={bt.get('total_swap_cost')} "
              f"({entry['seconds']}s)", flush=True)
        OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    # --- random-null under identical stress (same engine, stress kwargs) ---
    try:
        from research.mt5_backtest_engine import MT5BrokerBacktestEngine
        from research.full_pipeline_backtest import run_full_pipeline_backtest
        df = MT5BrokerBacktestEngine().fetch_market_data("EURUSD", "H1", BARS)
        # Explicit UTC timestamps: the stress overlay parses bar hours/weekdays
        # from the 'time' key, so it must exist regardless of index naming.
        raw = []
        for ts, row in df.iterrows():
            rec = {"time": ts.isoformat() if hasattr(ts, "isoformat") else str(ts)}
            for k, v in row.items():
                try:
                    rec[k] = float(v)
                except (TypeError, ValueError):
                    rec[k] = v
            raw.append(rec)
        rng = np.random.default_rng(42)
        n = len(df)
        t0 = time.time()
        null = run_full_pipeline_backtest(
            bars=raw, symbol="EURUSD", timeframe="H1",
            entry_signal_scores=rng.normal(0, 1, n).astype(np.float64),
            entry_signal_threshold=1.5,
            **STRESS)
        nd = null.to_dict()
        out["random_null_stress"] = {
            "sharpe": nd.get("sharpe_ratio"), "trades": nd.get("total_trades"),
            "pf": nd.get("profit_factor"), "expectancy_r": nd.get("expectancy_r"),
            "total_swap_cost": nd.get("total_swap_cost"),
            "seconds": round(time.time() - t0, 1),
        }
        print(f"[NULL-STRESS] sharpe={nd.get('sharpe_ratio')} "
              f"trades={nd.get('total_trades')} pf={nd.get('profit_factor')}",
              flush=True)
    except Exception as exc:
        out["random_null_stress"] = {"error": str(exc)[:300]}
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    # --- diff vs unstressed deep sweep + unstressed null ---
    try:
        deep = json.loads(DEEP.read_text(encoding="utf-8"))
        base_sharpes = [r.get("backtest", {}).get("sharpe") for r in deep.get("runs", [])]
        out["diff_vs_deep_sweep"] = {
            "unstressed_labels_sharpe": base_sharpes[0],
            "labels_all_identical_unstressed": len(set(map(str, base_sharpes))) <= 1,
        }
    except Exception as exc:
        out["diff_vs_deep_sweep"] = {"error": str(exc)[:300]}
    try:
        nulls = json.loads(NULLS.read_text(encoding="utf-8"))
        rn = next(r for r in nulls.get("runs", [])
                  if r.get("baseline") == "baseline_random_null_seed42")
        out["diff_vs_deep_sweep"]["unstressed_null_sharpe"] = rn.get("sharpe_ratio")
        out["diff_vs_deep_sweep"]["unstressed_null_pf"] = rn.get("profit_factor")
    except Exception as exc:
        out["diff_vs_deep_sweep"]["null_err"] = str(exc)[:200]
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("COST-STRESS-SWEEP-DONE", flush=True)


if __name__ == "__main__":
    main()
