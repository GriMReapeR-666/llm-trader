"""PU deep certification sweep: EURUSD/H1 @ 5000 bars (Phase 0 of the
cold-start accumulation plan).

Runs the same strategy set the continuous certification loop sweeps
(13 registry IDs + default) through run_full_validation at replay depth
5000, so the Stage-1 backtest's own R-multiple stream clears CPCV's >=40
sample threshold (the fetch_ohlc lazy-attach fix makes live broker bars
available in this standalone process; ~260 expected trades vs 26 at 500).

Writes artifacts/backtest_results/deep_sweep_5000.json with per-strategy
stages + verdicts, and prints one progress line per strategy for tailing.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

STRATEGIES = [
    "kronos_h1", "statarb_eur_chf", "macro_nfp_pullback", "meanrev_bollinger",
    "alpha_055_orderflow", "alpha_098_copula", "agent_execution_rl",
    "agent_regime_supervisor", "model_bocpd_changepoint", "model_kalman_filter",
    "gold_momentum_h4", "spx_volatility_skew", "sprt_hypothesis_gate",
    "default",
    # PU-0156 sprt-confirm wrapped variants (research hypotheses, honestly
    # labeled; family-check results were heterogeneous — see
    # artifacts/backtest_results/sprt_diagnosis.json).
    "agent_regime_supervisor@sprt_confirm", "kronos_h1@sprt_confirm",
    "gold_momentum_h4@sprt_confirm", "macro_nfp_pullback@sprt_confirm",
    "model_bocpd_changepoint@sprt_confirm",
]
OUT = ROOT / "artifacts" / "backtest_results" / "deep_sweep_5000.json"
BARS = 5000


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
            "bars_processed": bt.get("bars_processed"),
        },
        "cpcv": {
            "status": cpcv.get("status"),
            "source": cpcv.get("source"),
            "samples": cpcv.get("samples"),
            "mean_cpcv_sharpe": cpcv.get("mean_cpcv_sharpe"),
            "pbo": cpcv.get("probability_of_backtest_overfitting_pbo"),
            "dsr_conf": cpcv.get("deflated_sharpe_ratio"),
        },
        "dsr": stages.get("dsr", {}).get("status"),
        "walkforward": stages.get("walkforward", {}).get("status"),
    }


def main():
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline
    pipeline = ContinuousBacktestPipeline()
    out = {"symbol": "EURUSD", "timeframe": "H1", "bars": BARS, "runs": []}
    for name in STRATEGIES:
        t0 = time.time()
        try:
            res = pipeline.run_full_validation(
                symbol="EURUSD", strategy_name=name, timeframe="H1",
                bars=BARS, run_walkforward=True, run_dsr=True, compare_live=True)
            entry = slim(res)
        except Exception as exc:
            entry = {"strategy": name, "error": str(exc)[:300]}
        entry["seconds"] = round(time.time() - t0, 1)
        out["runs"].append(entry)
        v = entry.get("verdict") or {}
        cpcv = entry.get("cpcv") or {}
        print(f"[{len(out['runs'])}/{len(STRATEGIES)}] {name}: "
              f"bt={entry.get('backtest', {}).get('status')} "
              f"trades={entry.get('backtest', {}).get('total_trades')} "
              f"sharpe={entry.get('backtest', {}).get('sharpe')} "
              f"cpcv={cpcv.get('status')}/{cpcv.get('source')}/n={cpcv.get('samples')} "
              f"verdict={v.get('status')} ({entry['seconds']}s)", flush=True)
        OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("SWEEP-DONE", flush=True)


if __name__ == "__main__":
    main()
