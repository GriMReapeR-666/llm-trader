import os, sys

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import numpy as np
from quant_framework.modules import registry, ensure_builtin_modules_registered

print("registry tools after plain import:", len(registry.list_tools()))
for t in sorted(t.name for t in registry.list_tools()):
    print("  ", t)
print("modules registered:", len(registry._modules))

from quant_framework.agent import QuantAgent
a = QuantAgent("config/sources.yaml")
names = sorted(t["name"] for t in a.discover_tools())
print("agent tool count:", len(names))

print("\n-- execute every module tool via execute_tool --")
rs = np.random.RandomState(42)
returns_300 = (rs.randn(300) * 0.01).tolist()
prices_up = [100.0 + i * 0.5 for i in range(300)]
trend_steps = np.zeros(1200)
e = rs.randn(1200) * 0.01
for i in range(1, 1200):
    trend_steps[i] = 0.8 * trend_steps[i - 1] + e[i]
trending = (100.0 + np.cumsum(trend_steps)).tolist()
two_block = np.concatenate([rs.randn(150) * 0.005, rs.randn(150) * 0.05]).tolist()

calls = [
    ("regime_detection.hmm.classify", {"returns": two_block}),
    ("regime_detection.hmm.fit", {"returns": two_block}),
    ("regime_detection.hurst.calculate", {"prices": trending}),
    ("strategy_development.wfo.optimize", {"returns": returns_300}),
    ("strategy_development.dsr.calculate", {"sharpe_ratio": 1.0, "n": 252, "trials": 10}),
    ("strategy_development.triple_barrier.label", {"prices": [100, 101, 102, 103, 104, 105, 106], "entry_price": 100.0}),
    ("portfolio_management.kelly.calculate", {"win_rate": 0.6, "win_loss_ratio": 1.5, "fraction": 0.25}),
    ("portfolio_management.hrp.optimize", {"returns": [returns_300, returns_300]}),
    ("order_execution.spread.calculate", {"base_spread_bps": 2.0, "volatility_ratio": 2.0}),
    ("order_execution.almgren_chriss.calculate", {"order_size": 10000, "adv": 1_000_000, "price": 100.0, "volatility": 0.02}),
    ("quant_utilities.expectancy.calculate", {"win_rate": 0.55, "avg_win_r": 2.0, "avg_loss_r": 1.0}),
    ("quant_utilities.expectancy.r_multiple", {"entry": 100.0, "exit": 110.0, "knowledge_framework_guard": 0}),
]
# guard fix: correct the r_multiple args (previous line had a stray key)
calls[-1] = ("quant_utilities.expectancy.r_multiple", {"entry": 100.0, "exit": 110.0, "stop": 95.0})
calls.append(("quant_utilities.drawdown.analyze", {"equity_curve": [100, 110, 90, 95, 120, 80, 130]}))

ok = 0
for name, params in calls:
    out = a.execute_tool(name, **params)
    success = out.get("success")
    keys = sorted(out["result"].keys())[:6] if success else out.get("error", "")[:80]
    print(("OK  " if success else "FAIL"), name, "->", keys)
    ok += bool(success)
print("module tools OK:", ok, "/", len(calls))

print("\n-- knowledge tool still works --")
r = a.execute_tool("knowledge.classify", text="hidden markov model regime detection")
print("knowledge.classify:", r["success"], r.get("result", {}).get("topic"))

print("\n-- chained module + knowledge tools --")
ch = a.chain_tools([
    {"tool": "portfolio_management.kelly.calculate", "params": {"win_rate": 0.6, "win_loss_ratio": 1.5}, "save_as": "kelly"},
])
print("chain step1 success:", ch["pipeline_results"][0]["result"]["success"])

print("\n-- no-config agent (module tools must still work) --")
a2 = QuantAgent("does/not/exist.yaml")
r2 = a2.execute_tool("portfolio_management.kelly.calculate", win_rate=0.6, win_loss_ratio=1.5)
print("no-config kelly:", r2["success"], r2["result"].get("kelly_fraction"))

print("\n-- unknown tool --")
r3 = a.execute_tool("no.such.tool")
print("unknown tool:", r3["success"], r3.get("error"))

print("BRIDGE-DONE")
