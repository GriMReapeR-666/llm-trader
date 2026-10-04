"""Run the remaining module mutation jobs sequentially (after trading_safety finishes)."""
import os, subprocess, sys, time
B = "/Users/dankmagician/Documents/New project/.claude/worktrees/agent-a94544446716ddf7c"
M = os.path.join(B, "mut")
PYD = os.path.join(M, "py")

# wait for the trading_safety job
while subprocess.run(["pgrep", "-f", "mutate.py --file core/trading_safety.py"], capture_output=True).returncode == 0:
    time.sleep(20)

L = lambda name: "@" + os.path.join(M, "lists", name)
open(os.path.join(M, "lists", "rm_focus.txt"), "w").write(
    "tests/test_risk_manager_safety.py tests/test_risk_management_hardening.py tests/test_risk_persistence.py "
    "tests/test_p0_gates.py tests/test_edge_gates.py tests/test_section_e_hardening.py tests/test_p11_frictional_growth.py")
open(os.path.join(M, "lists", "oe_focus.txt"), "w").write(
    "tests/test_execution_engine_hardening.py tests/test_phase1_remediation.py tests/test_phase3_remediation.py "
    "tests/test_paper_quote_safety.py tests/test_bocpd_and_sor.py")
RM_FUNCS = ",".join("RiskManager." + f for f in [
    "check_daily_drawdown", "check_weekly_circuit_breaker", "get_circuit_breaker_size_multiplier",
    "record_trade_outcome", "check_consecutive_loss_breaker", "check_equity_floor", "get_drawdown_multiplier",
    "get_streak_multiplier", "quarter_kelly_scalar", "check_max_drawdown", "check_active_trade_limit",
    "check_drawdown", "remaining_daily_risk", "check_portfolio_heat", "calc_position_size"])
jobs = [
    ("margin_safety", ["--file", "core/margin_safety.py", "--funcs", "ALL", "--tests", L("margin_safety.txt"),
                       "--timeout", "90"]),
    ("kill_switch_latch", ["--file", "data/kill_switch_latch.py", "--funcs", "ALL",
                           "--tests", "tests/test_d9_kill_switch_latch.py tests/test_d9_kill_switch_triage.py",
                           "--timeout", "90"]),
    ("autonomy", ["--file", "operations/autonomy.py", "--funcs", "ALL", "--tests", L("autonomy.txt"),
                  "--timeout", "90"]),
    ("order_execution", ["--file", "core/order_execution.py", "--funcs",
                         "validate_order_params,validate_pending_entry_geometry,place_pending_order,emergency_market_exit", "--line-sample", "706-861=25", "--sample", "emergency_market_exit=30",
                         "--tests", L("oe_focus.txt"), "--wide", L("order_execution_wide.txt"), "--timeout", "90"]),
    ("risk_manager", ["--file", "core/risk_manager.py", "--funcs", RM_FUNCS, "--line-sample", "950-1065=20", "--sample", "RiskManager.record_trade_outcome=30,RiskManager.quarter_kelly_scalar=15",
                      "--tests", L("rm_focus.txt"), "--wide", L("risk_manager_wide.txt"), "--timeout", "90"]),
]
only = sys.argv[1:]
for name, args in jobs:
    if only and name not in only:
        continue
    with open(os.path.join(M, f"log_{name}.txt"), "w") as log:
        subprocess.run([PYD, os.path.join(M, "mutate.py"), *args, "--out", os.path.join(M, f"res_{name}.jsonl")],
                       stdout=log, stderr=subprocess.STDOUT, cwd=B)
print("chain done")
