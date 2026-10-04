"""PU-0156 gauntlet: does agent_regime_supervisor@sprt_confirm survive the
full validation hierarchy (CPCV + DSR + walk-forward)?

Runs the wrapped variant AND its base label through run_full_validation on
the same live EURUSD/H1 5000-bar fetch (same-process pair => apples-to-apples),
mirroring the deep-sweep stage extraction. Writes
artifacts/backtest_results/sprt_confirm_gauntlet.json and prints one line
per run for tailing.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

RUNS = [
    "agent_regime_supervisor@sprt_confirm",
    "agent_regime_supervisor",
]
OUT = ROOT / "artifacts" / "backtest_results" / "sprt_confirm_gauntlet.json"
DEEP_SWEEP = ROOT / "artifacts" / "backtest_results" / "deep_sweep_5000.json"
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

    ref = {}
    if DEEP_SWEEP.exists():
        try:
            prev = json.loads(DEEP_SWEEP.read_text(encoding="utf-8"))
            ref = {r["strategy"]: r for r in prev.get("runs", [])
                   if r.get("strategy") in RUNS}
        except Exception:
            ref = {}

    pipeline = ContinuousBacktestPipeline()
    out = {"symbol": "EURUSD", "timeframe": "H1", "bars": BARS,
           "deep_sweep_reference": ref, "runs": []}
    for name in RUNS:
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
        print(f"[{len(out['runs'])}/{len(RUNS)}] {name}: "
              f"bt={entry.get('backtest', {}).get('status')} "
              f"trades={entry.get('backtest', {}).get('total_trades')} "
              f"sharpe={entry.get('backtest', {}).get('sharpe')} "
              f"cpcv={cpcv.get('status')}/n={cpcv.get('samples')} "
              f"pbo={cpcv.get('pbo')} dsr_stage={entry.get('dsr')} "
              f"wf={entry.get('walkforward')} "
              f"verdict={v.get('status')} blockers={v.get('blockers')} "
              f"({entry['seconds']}s)", flush=True)
        OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("GAUNTLET-DONE", flush=True)


if __name__ == "__main__":
    main()
