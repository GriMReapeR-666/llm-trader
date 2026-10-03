"""OpenQuant structured strategy evaluation (P08 Sec. 3): gates + scoring.

Implements Reid et al. (2026) two-stage reasoning (deterministic slice):

  Stage 1 - admissibility gates (Eq. 6). A strategy is admissible only if::
      alpha > 0, excess_return > 0, sharpe > 0, profit_factor > 1, mdd > -0.50
  Fail any gate -> excluded with score zero (no peer-relative rescue).

  Stage 2 - nonlinear composite (Eq. 7-8). Each metric maps through a
  calibrated sigmoid phi(x) = 1/(1+exp(-lam*(x-mu))) centred at a
  decision-relevant threshold, enforcing diminishing marginal reward.
  Twelve components (Table 1) aggregate to Phi in [0, 100], W = 25.

  Population normalisation (Eq. 9): with >=3 same-ticker strategies,
  components are z-scored and re-sigmoidised so rankings reflect relative
  excellence; average maps to 0.5 per component.

  Data quality (Eq. 12): q = clamp(1 - missing - 0.5*dups); fewer than 30
  valid observations is a hard rejection.

NOT ported (platform/LLM scope): Eva fabrication/coding-error agents,
allocation mapping A(s), pitch-intake keyword methodology score.

References:
    Reid, Ramsey, Liu & Jackson-Wade (2026). "OpenQuant: Structured Strategy
    Evaluation under Benchmark Dominance Constraints."
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admissibility gates (Eq. 6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissibilityThresholds:
    jensen_alpha: float = 0.0
    excess_return: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 1.0
    max_drawdown: float = -0.50


@dataclass
class GateVerdict:
    admissible: bool
    failed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"admissible": self.admissible, "failed": list(self.failed)}


def check_admissibility(metrics: Dict[str, float],
                        t: AdmissibilityThresholds = AdmissibilityThresholds()) -> GateVerdict:
    """Stage 1: all five Eq. 6 gates must pass."""
    failed = []
    if not (metrics.get("jensen_alpha", float("-inf")) > t.jensen_alpha):
        failed.append("jensen_alpha<=0")
    if not (metrics.get("excess_return", float("-inf")) > t.excess_return):
        failed.append("excess_return<=0")
    if not (metrics.get("sharpe", float("-inf")) > t.sharpe):
        failed.append("sharpe<=0")
    if not (metrics.get("profit_factor", 0.0) > t.profit_factor):
        failed.append("profit_factor<=1")
    if not (metrics.get("max_drawdown", float("-inf")) > t.max_drawdown):
        failed.append("max_drawdown<=-0.50")
    return GateVerdict(admissible=not failed, failed=failed)


# ---------------------------------------------------------------------------
# Sigmoid components (Table 1): (key, mu, lam, weight)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Component:
    key: str
    mu: float
    lam: float
    weight: float


COMPONENTS: Tuple[Component, ...] = (
    Component("excess_return", 0.05, 20.0, 3.0),
    Component("jensen_alpha", 0.02, 30.0, 3.0),
    Component("information_ratio", 0.50, 3.0, 2.5),
    Component("up_down_capture", 1.00, 3.0, 2.0),
    Component("drawdown_vs_bench", 0.00, 15.0, 2.0),
    Component("sharpe", 1.00, 2.0, 2.5),
    Component("sortino", 1.50, 1.8, 2.0),
    Component("calmar", 1.00, 2.0, 1.5),
    Component("profit_factor", 1.50, 2.0, 1.5),
    Component("win_loss_ratio", 1.50, 1.5, 1.0),
    Component("expectancy", 0.005, 200.0, 1.5),
    Component("trade_consistency", 50.0, 0.04, 0.5),
)

TOTAL_WEIGHT = sum(c.weight for c in COMPONENTS)  # 23.0 from Table 1.
# NOTE: the paper text claims W=25 (Eq. 8) but its own Table 1 weights sum to
# 12.5+6.0+4.5 = 23.0. The denominator MUST equal the configured weight sum,
# otherwise Phi cannot reach 100. Computed here, never hardcoded.


def sigmoid(x: float, mu: float, lam: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-lam * (float(x) - mu)))
    except (OverflowError, ValueError, TypeError):
        return 1.0 if float(x) >= mu else 0.0


def component_scores(metrics: Dict[str, float]) -> Dict[str, float]:
    """phi_k for each Table-1 component (missing metric -> 0 contribution)."""
    out = {}
    for c in COMPONENTS:
        v = metrics.get(c.key)
        out[c.key] = sigmoid(v, c.mu, c.lam) if v is not None and math.isfinite(float(v)) else 0.0
    return out


def composite_score(metrics: Dict[str, float]) -> float:
    """Phi(s) in [0, 100] (Eq. 8). Inadmissible strategies score 0."""
    if not check_admissibility(metrics).admissible:
        return 0.0
    comps = component_scores(metrics)
    total = sum(c.weight * comps[c.key] for c in COMPONENTS)
    return round(100.0 * total / TOTAL_WEIGHT, 2)


def population_normalize(pool: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Eq. 9 double-sigmoid: average strategy -> 0.5 per component (|P| >= 3).

    Returns per-strategy {component: rescored} maps. Pools < 3 pass through
    raw component scores (paper: normalisation only defined for |P|>=3).
    """
    raws = [component_scores(m) for m in pool]
    if len(pool) < 3:
        return raws
    out = []
    for c in COMPONENTS:
        vals = np.array([r[c.key] for r in raws], dtype=float)
        mean, sd = float(vals.mean()), float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        sd = sd if sd > 1e-12 else 1.0
        out.append([1.0 / (1.0 + math.exp(-(v - mean) / sd)) for v in vals])
    return [{c.key: out[j][i] for j, c in enumerate(COMPONENTS)} for i in range(len(pool))]


# ---------------------------------------------------------------------------
# Data quality (Eq. 12)
# ---------------------------------------------------------------------------

MIN_OBSERVATIONS = 30


def data_quality_score(missing_frac: float, dup_frac: float) -> float:
    """q = clamp(1 - m - 0.5*d) in [0, 1]."""
    return max(0.0, min(1.0, 1.0 - float(missing_frac) - 0.5 * float(dup_frac)))


def data_quality_gate(n_valid: int, missing_frac: float, dup_frac: float) -> Tuple[bool, float]:
    """Hard rejection under 30 valid observations; else (ok, q)."""
    if n_valid < MIN_OBSERVATIONS:
        return False, 0.0
    return True, round(data_quality_score(missing_frac, dup_frac), 4)


# ---------------------------------------------------------------------------
# Benchmark-relative inputs from return series
# ---------------------------------------------------------------------------

def benchmark_relative(strategy: List[float], benchmark: List[float],
                       risk_free: float = 0.0, periods_per_year: int = 252) -> Dict[str, float]:
    """Derive the five benchmark-category inputs (Table 1, top block).

    Jensen's alpha via CAPM beta against the benchmark; IR annualised on
    active returns; up/down capture on benchmark up/down subsets.
    """
    s = np.asarray(strategy, dtype=float)
    b = np.asarray(benchmark, dtype=float)
    n = min(len(s), len(b))
    if n < 2:
        return {}
    s, b = s[-n:], b[-n:]
    active = s - b
    excess = float(np.mean(active) * periods_per_year)
    var_b = float(np.var(b))
    beta = float(np.cov(s, b, ddof=1)[0, 1] / var_b) if var_b > 1e-12 else 0.0
    jensen = float((np.mean(s) - risk_free - beta * (np.mean(b) - risk_free)) * periods_per_year)
    sd_active = float(np.std(active, ddof=1))
    ir = float(np.mean(active) / sd_active * math.sqrt(periods_per_year)) if sd_active > 1e-12 else 0.0
    up = b > 0
    down = b < 0
    up_cap = float(np.mean(s[up]) / np.mean(b[up])) if up.any() and abs(np.mean(b[up])) > 1e-12 else 1.0
    down_cap = float(np.mean(s[down]) / np.mean(b[down])) if down.any() and abs(np.mean(b[down])) > 1e-12 else 1.0
    ud = up_cap / down_cap if abs(down_cap) > 1e-12 else up_cap
    cum_s = np.cumprod(1 + s)
    cum_b = np.cumprod(1 + b)
    dd_s = float((cum_s / np.maximum.accumulate(cum_s) - 1).min())
    dd_b = float((cum_b / np.maximum.accumulate(cum_b) - 1).min())
    return {"excess_return": round(excess, 6), "jensen_alpha": round(jensen, 6),
            "information_ratio": round(ir, 4), "up_down_capture": round(float(ud), 4),
            "drawdown_vs_bench": round(dd_s - dd_b, 6)}
