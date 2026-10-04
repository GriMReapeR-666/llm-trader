import os, sys

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import numpy as np
from quant_framework.modules.regime_detection import HurstCalculator, HMMRegimeClassifier

rs = np.random.RandomState(42)
n = 1000

# 1) pure random walk
rw = 100.0 + np.cumsum(rs.randn(n))

# 2) trending: price = cumsum of persistent AR(1) steps (phi=0.8)
phi = 0.8
r = np.zeros(n)
eps = rs.randn(n) * 0.01
for t in range(1, n):
    r[t] = phi * r[t - 1] + eps[t]
trend = 100.0 + np.cumsum(r)

# 3) mean-reverting OU process (theta=0.2)
theta = 0.2
p = np.zeros(n)
e = rs.randn(n)
for t in range(1, n):
    p[t] = p[t - 1] - theta * p[t - 1] + e[t]
ou = 100.0 + p

# 4) GBM (geometric random walk)
gbm = 100.0 * np.exp(np.cumsum(rs.randn(n) * 0.01))

print("min values -> rw %.2f trend %.2f ou %.2f gbm %.2f" % (rw.min(), trend.min(), ou.min(), gbm.min()))
hc = HurstCalculator()
for name, series in [("random_walk", rw), ("trending_ar1", trend), ("mean_rev_ou", ou), ("gbm", gbm)]:
    out = hc.calculate([float(x) for x in series])
    print(name, "->", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()})

# max_lag sensitivity on trending/OU
for ml in (20, 50):
    print("trend max_lag=%d ->" % ml, round(hc.calculate([float(x) for x in trend], max_lag=ml)["hurst_exponent"], 4))
    print("ou    max_lag=%d ->" % ml, round(hc.calculate([float(x) for x in ou], max_lag=ml)["hurst_exponent"], 4))

# HMM smoke: two clearly separated volatility blocks
X = np.concatenate([rs.randn(150) * 0.005, rs.randn(150) * 0.05])
c = HMMRegimeClassifier(n_states=2)
fit_out = c.fit([float(x) for x in X])
print("fit ->", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in fit_out.items()})
cls_out = c.classify([float(x) for x in X[-50:]])
print("classify keys ->", sorted(cls_out.keys()))
print("classify -> current_regime=%s label=%s conf=%.3f n_regimes=%d" % (
    cls_out.get("current_regime"), cls_out.get("regime_label"),
    cls_out.get("confidence", float("nan")), len(cls_out.get("regimes", []))))
fresh = HMMRegimeClassifier(n_states=2).classify([float(x) for x in X])
print("autofit fresh -> current_regime=%s label=%s conf=%.3f" % (
    fresh.get("current_regime"), fresh.get("regime_label"), fresh.get("confidence", float("nan"))))
short = HMMRegimeClassifier(n_states=2).classify([0.01, -0.02, 0.01, 0.005])
print("short classify ->", short)
print("3-state fit ->", {k: (round(v, 2) if isinstance(v, float) else v)
                         for k, v in HMMRegimeClassifier(n_states=3).fit([float(x) for x in X * 2]).items()})
print("CALIB-DONE")
