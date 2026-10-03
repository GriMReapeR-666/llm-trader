import os, sys

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import numpy as np
from quant_framework.modules.portfolio_management import HRPOptimizer
from quant_framework.modules.strategy_development import DeflatedSharpeRatio

rs = np.random.RandomState(42)
hrp = HRPOptimizer()

print("=== HRP: correlated pair + independent third ===")
r1 = rs.randn(500) * 0.01
r2 = 0.98 * r1 + 0.02 * rs.randn(500) * 0.01
r3 = rs.randn(500) * 0.01
out = hrp.optimize([r1.tolist(), r2.tolist(), r3.tolist()])
print("weights:", [round(w, 4) for w in out["weights"]], "sum:", round(sum(out["weights"]), 6))
print("port_vol:", round(out["portfolio_volatility"], 5))

print("\n=== HRP: near-identical pair + independent same-vol third (analytic ~ [.25,.25,.50]) ===")
r1 = rs.randn(500) * 0.01
r2 = r1 + 0.001 * rs.randn(500) * 0.01
r3 = rs.randn(500) * 0.01
out = hrp.optimize([r1.tolist(), r2.tolist(), r3.tolist()])
print("weights:", [round(w, 4) for w in out["weights"]])

print("\n=== HRP: vol matters (asset A vol 1%, B vol 5%, independent) ===")
ra = rs.randn(500) * 0.01
rb = rs.randn(500) * 0.05
out = hrp.optimize([ra.tolist(), rb.tolist()])
print("weights:", [round(w, 4) for w in out["weights"]], "(old equal-weight would be [0.5, 0.5])")

print("\n=== HRP: 3 iid assets -> near equal ===")
out = hrp.optimize([(rs.randn(500) * 0.01).tolist() for _ in range(3)])
print("weights:", [round(w, 4) for w in out["weights"]])

print("\n=== HRP: guards ===")
print("1 asset:", hrp.optimize([[0.01, 0.02, 0.01]]))
print("2 obs:", hrp.optimize([[0.01, 0.02], [0.01, 0.02]]))

dsr = DeflatedSharpeRatio()
print("\n=== DSR: report's overconfidence case SR=1.5, n=252, trials=100 (old printed dsr=7.83) ===")
out = dsr.calculate(sharpe_ratio=1.5, n=252, trials=100)
print({k: round(v, 6) if isinstance(v, float) else v for k, v in out.items()})

print("\n=== DSR: small edge, many trials: SR=0.10, n=252, trials=1000 ===")
out = dsr.calculate(sharpe_ratio=0.10, n=252, trials=1000)
print({k: round(v, 6) if isinstance(v, float) else v for k, v in out.items()})

print("\n=== DSR: skew/kurtosis penalty (SR=0.30, n=252, trials=10000) ===")
base = dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000)
skewd = dsr.calculate(sharpe_ratio=0.30, n=252, trials=10000, skew=-1.0, kurtosis=8.0)
print("gaussian moments:", round(base["dsr"], 6), " sig:", base["significant"])
print("skew -1, kurt 8 :", round(skewd["dsr"], 6), " sig:", skewd["significant"])

print("\n=== DSR: trials monotonicity (SR=0.30, n=252) ===")
for t in (10, 100, 10000):
    o = dsr.calculate(sharpe_ratio=0.30, n=252, trials=t)
    print(f"trials={t:>6} dsr={o['dsr']:.6f} sr0={o['expected_max_sr']:.6f}")

print("\n=== DSR: sr_variance override ===")
d1 = dsr.calculate(sharpe_ratio=0.30, n=252, trials=100)
d2 = dsr.calculate(sharpe_ratio=0.30, n=252, trials=100, sr_variance=4.0 / 251)
print("default var:", round(d1["dsr"], 6), "4x var:", round(d2["dsr"], 6))

print("\n=== DSR: scipy cross-check of SR=0.10 case ===")
from scipy.stats import norm
g = 0.5772156649015329
var_sr = 1.0 / 251.0
sr0 = np.sqrt(var_sr) * ((1 - g) * norm.ppf(1 - 1.0 / 1000) + g * norm.ppf(1 - 1.0 / (1000 * np.e)))
expected = float(norm.cdf((0.10 - sr0) * np.sqrt(251)))
got = dsr.calculate(sharpe_ratio=0.10, n=252, trials=1000)["dsr"]
print("scipy expected:", round(expected, 8), "got:", round(got, 8), "diff:", abs(expected - got))

print("\n=== DSR: guards ===")
print("n=2:", dsr.calculate(sharpe_ratio=1.0, n=2))
print("kurtosis=0:", dsr.calculate(sharpe_ratio=1.0, n=100, kurtosis=0))
print("trials=0:", dsr.calculate(sharpe_ratio=1.0, n=100, trials=0))

print("MATH-DONE")
