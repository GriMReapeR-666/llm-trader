"""Mutation-test the six safety modules and report scores (Phase 1, PR 1.4).

    python scripts/mutation/run_safety.py [--workers 4] [--only trading_safety,...] [--min-score 75]

Focused tests only (no wide re-run), so scores are a lower bound. Results go
to $MUTATION_WORKDIR/results/res_<module>.jsonl; exits 1 when --min-score is
given and the overall score is below it.
"""
import argparse, collections, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("MUTATION_WORKDIR", os.path.join(tempfile.gettempdir(), "llm_trader_mutation"))
PROPS = "tests/property/test_safety_properties.py"

JOBS = {
    "trading_safety": ("core/trading_safety.py",
        "DeterministicValidator.validate,DeterministicValidator.confluence,PortfolioRiskArbiter.permit,"
        "check_selective_activation,exposure_buckets,safe_risk_fraction,TradingStore.open_bucket_risk,"
        "TradingStore.setup_seen_recently,TradingStore.kill_switch_active,TradingStore.is_kill_switch_active",
        "tests/safety/test_validator_single_gate.py tests/safety/test_arbiter_boundaries.py tests/safety/test_scoring_and_limits.py "
        "tests/test_trading_safety.py tests/test_p0_gates.py tests/test_edge_gates.py tests/test_netev_gate.py " + PROPS),
    "margin_safety": ("core/margin_safety.py", "ALL",
        "tests/safety/test_margin_thresholds.py tests/test_margin_safety.py tests/test_section_h_hardening.py"),
    "kill_switch_latch": ("data/kill_switch_latch.py", "ALL",
        "tests/safety/test_latch_persistence.py tests/test_d9_kill_switch_latch.py tests/test_d9_kill_switch_triage.py " + PROPS),
    "autonomy": ("operations/autonomy.py", "ALL",
        "tests/test_supervisor_autonomy.py tests/test_autonomous_gates.py tests/test_strategy_factory.py"),
    "order_execution": ("core/order_execution.py",
        "validate_order_params,validate_pending_entry_geometry,place_pending_order,emergency_market_exit,place_market_order",
        "tests/safety/test_order_geometry.py tests/safety/test_order_path_characterization.py "
        "tests/test_execution_engine_hardening.py tests/test_phase1_remediation.py tests/test_phase3_remediation.py " + PROPS),
    "risk_manager": ("core/risk_manager.py",
        ",".join("RiskManager." + f for f in [
            "check_daily_drawdown", "check_weekly_circuit_breaker", "get_circuit_breaker_size_multiplier",
            "record_trade_outcome", "check_consecutive_loss_breaker", "check_equity_floor", "get_drawdown_multiplier",
            "get_streak_multiplier", "quarter_kelly_scalar", "check_max_drawdown", "check_active_trade_limit",
            "check_drawdown", "remaining_daily_risk", "check_portfolio_heat", "calc_position_size"]),
        "tests/safety/test_risk_ladders.py tests/safety/test_scoring_and_limits.py tests/test_risk_manager_safety.py tests/test_risk_management_hardening.py "
        "tests/test_risk_persistence.py tests/test_edge_gates.py tests/safety/test_sizing_and_limits.py "
        "tests/safety/test_phase4_review_closure.py " + PROPS),
}


def score(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    c = collections.Counter(r["status"] for r in rows)
    return c["killed"], c["survived"], len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    out_dir = os.path.join(BASE, "results")
    os.makedirs(out_dir, exist_ok=True)
    names = [n for n in JOBS if not a.only or n in a.only.split(",")]
    total_k = total_s = 0
    for name in names:
        path, funcs, tests = JOBS[name]
        out = os.path.join(out_dir, f"res_{name}.jsonl")
        if os.path.exists(out):
            os.remove(out)
        rc = subprocess.run([sys.executable, os.path.join(HERE, "mutate.py"), "--file", path, "--funcs", funcs,
                             "--tests", tests, "--out", out, "--workers", str(a.workers),
                             "--timeout", str(a.timeout)]).returncode
        if rc != 0 or not os.path.exists(out):
            print(f"{name}: mutation run failed (rc={rc})", file=sys.stderr)
            return 2
        k, s, n = score(out)
        total_k, total_s = total_k + k, total_s + s
        print(f"SCORE {name:18s} {100 * k / max(1, k + s):5.1f}%  ({k} killed / {s} survived / {n} mutants)", flush=True)
    overall = 100 * total_k / max(1, total_k + total_s)
    print(f"SCORE overall            {overall:5.1f}%", flush=True)
    if a.min_score is not None and overall < a.min_score:
        print(f"mutation score {overall:.1f}% is below the ratchet {a.min_score}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
