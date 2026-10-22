"""Quantitative Metrics Engine implementing the 6 Core Trading Pillars.

Covers the complete 6-stage quantitative lifecycle:
Stage 1: Trade-Level Standardization (1R Unit, Stop Distance, Lot Sizing, Realized R)
Stage 2: Strategy Validation & Proof Engine (EV, Win Rate, Payoff, SQN, WFE, DSR, IC)
Stage 3: Survival & Drawdown Physics (MaxDD, Streak Probability P(k), Expected L_max, RoR, Calmar)
Stage 4: Capital Allocation & Sizing (Volatility ATR Sizing, Kelly K*, Quarter-Kelly, Heat Cap)
Stage 5: Regime Detection & Routing (Hurst H, ADX, ATR Percentiles, Master Expectancy EV_Master)
Stage 6: Compounding & Macro Performance (CAGR, Sharpe, Downside Deviation Sortino)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np


# ---------------------------------------------------------------------------
# Stage 1: Trade-Level Mathematical Standardization (Micro)
# ---------------------------------------------------------------------------

def calculate_stop_distance(entry_price: float, invalidation_price: float) -> float:
    """Quantifies structural invalidation risk: Delta P_stop = |P_entry - P_invalidation|."""
    entry = float(entry_price)
    invalidation = float(invalidation_price)
    if not math.isfinite(entry) or not math.isfinite(invalidation) or entry <= 0 or invalidation <= 0:
        return 0.0
    return float(abs(entry - invalidation))


def calculate_1r_dollar_risk(equity: float, risk_fraction: float = 0.01) -> float:
    """Establishes uniform dollar risk unit per trade: R_$ = Equity * Risk %."""
    eq = float(equity)
    rf = float(risk_fraction)
    if not math.isfinite(eq) or not math.isfinite(rf) or eq <= 0 or rf <= 0:
        return 0.0
    # Hard safety cap: never exceed 5% risk in single trade
    rf_capped = min(max(rf, 0.0), 0.05)
    return float(eq * rf_capped)


def calculate_normalized_lot_size(
    risk_usd: float,
    stop_distance: float,
    tick_size: float = 0.00001,
    tick_value: float = 1.0,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
) -> float:
    """Normalizes trade exposure across different volatilities:
    Size = Risk_$ / (Delta P_stop * Tick_Value / Tick_Size)
    Enforces integer/lot step boundaries and prevents rounding-up over-risk.
    """
    for val in (risk_usd, stop_distance, tick_size, tick_value, volume_min, volume_max, volume_step):
        if not math.isfinite(float(val)) or float(val) <= 0:
            return 0.0
    
    loss_per_lot = (stop_distance / tick_size) * tick_value
    if loss_per_lot <= 0:
        return 0.0
    
    raw_volume = risk_usd / loss_per_lot
    
    # Quantize to broker volume step (avoid float truncation issues)
    if volume_step > 0:
        raw_volume = math.floor((raw_volume / volume_step) + 1e-9) * volume_step
    
    if raw_volume < volume_min:
        return 0.0
    
    return float(min(round(raw_volume, 4), volume_max))


def calculate_realized_r_multiple(
    entry_price: float,
    exit_price: float,
    stop_loss_price: float,
    side: str = "BUY",
) -> float:
    """Standardizes P&L as a dimensionless multiple of initial planned risk:
    R = (P_exit - P_entry) / (P_entry - P_stop)  [for BUY]
    R = (P_entry - P_exit) / (P_stop - P_entry)  [for SELL]
    """
    entry = float(entry_price)
    exit_p = float(exit_price)
    stop = float(stop_loss_price)
    
    if not all(math.isfinite(x) for x in (entry, exit_p, stop)) or entry <= 0 or exit_p <= 0 or stop <= 0:
        return 0.0
    
    is_buy = str(side).strip().upper() in ("BUY", "LONG", "B")
    
    if is_buy:
        risk_dist = entry - stop
        if risk_dist <= 0:
            return 0.0
        return float((exit_p - entry) / risk_dist)
    else:
        risk_dist = stop - entry
        if risk_dist <= 0:
            return 0.0
        return float((entry - exit_p) / risk_dist)


# ---------------------------------------------------------------------------
# Stage 2: Strategy Validation & Proof Engine
# ---------------------------------------------------------------------------

def calculate_expected_value(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float = 1.0,
    friction_r: float = 0.0,
) -> float:
    """Expected Value in R-units:
    EV = (P_win * W) - (P_loss * L) - Friction
    """
    p_win = float(win_rate)
    w = float(avg_win_r)
    l = float(avg_loss_r)
    f = float(friction_r)
    
    if not all(math.isfinite(x) for x in (p_win, w, l, f)):
        return 0.0
    
    p_win = max(0.0, min(1.0, p_win))
    p_loss = 1.0 - p_win
    return float((p_win * w) - (p_loss * l) - f)


def calculate_sqn(r_multiples: Sequence[float]) -> float:
    """System Quality Number:
    SQN = sqrt(N) * (mu_R / sigma_R)
    Target: SQN >= 2.5 (Excellent >= 3.0)
    """
    rs = np.asarray([float(r) for r in r_multiples if math.isfinite(float(r))], dtype=float)
    n = len(rs)
    if n < 10:
        return 0.0
    mean_r = float(np.mean(rs))
    std_r = float(np.std(rs, ddof=1))
    if std_r <= 1e-12:
        return 0.0
    return float(np.sqrt(n) * (mean_r / std_r))


def calculate_wfe(annualized_return_oos: float, annualized_return_is: float) -> float:
    """Walk-Forward Efficiency:
    WFE = Annualized Return OOS / Annualized Return IS
    Target: WFE >= 60% (0.60)
    """
    ret_oos = float(annualized_return_oos)
    ret_is = float(annualized_return_is)
    if not math.isfinite(ret_oos) or not math.isfinite(ret_is) or ret_is <= 0:
        return 0.0
    return float(ret_oos / ret_is)


# ---------------------------------------------------------------------------
# DSR UNITS CONTRACT (resolved 2026-10-20)
#
# Every function in this module that consumes a Sharpe ratio follows one rule:
#
#   * ``estimated_sharpe`` and ``benchmark_sharpe`` are ANNUALIZED by default;
#     pass ``annualized=False`` when you hold the raw estimator (mean / std).
#   * ``periods_per_year`` MUST be the annualization that actually produced
#     the Sharpe (252 for daily equity, 8760 for hourly FX, or the trades per
#     year when Sharpe is computed per trade). It is never guessed from
#     ``sample_size``.
#   * ``sample_size`` is the number of observations behind the Sharpe at that
#     same frequency. A per-trade Sharpe with ``sample_size=trades`` is
#     legitimate; a per-bar Sharpe with ``sample_size=bars`` is legitimate;
#     the two must never be mixed.
#
# Why the rule exists: ``overfit_gate`` computed ``mean / std`` (per-period)
# but called the ledger, which assumed annualized-daily. That de-annualized a
# value that was already per-period, understating z by sqrt(252) ~ 15.9x and
# rejecting every healthy strategy.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def _expected_max_standard_normal(k: int) -> float:
    """E[max of ``k`` iid standard normals], in standard-deviation units.

    Exact order-statistic expectation::

        E[max] = ∫₀^∞ (1 - Φ(x)^k) dx  −  ∫₀^∞ Φ(−x)^k dx

    The closed-form EVT/Gumbel approximation used previously is biased HIGH,
    and worst exactly where a new strategy is judged: +50% at k=2, +15% at
    k=5, +3.5% at k=100, +1.5% at k=2811.  (Verified against the exact
    integral; k=2 must equal 1/sqrt(pi) = 0.56419.)  A too-high bar is a
    type-II error factory -- it rejects real edges.
    """
    if k <= 1:
        return 0.0
    try:
        from scipy.integrate import quad
        from scipy.stats import norm

        upper = max(8.0, math.sqrt(2.0 * math.log(k)) + 6.0)
        positive, _ = quad(
            lambda x: 1.0 - norm.cdf(x) ** k, 0.0, upper, epsabs=1e-9, limit=200
        )
        negative, _ = quad(
            lambda x: norm.cdf(-x) ** k, 0.0, upper, epsabs=1e-9, limit=200
        )
        return float(positive - negative)
    except Exception:  # pragma: no cover - scipy missing or integral failure
        return (
            math.sqrt(2.0 * math.log(k))
            - (math.log(math.pi) + math.log(math.log(k)))
            / (2.0 * math.sqrt(2.0 * math.log(k)))
        )


def expected_max_sharpe(
    num_trials: int,
    sample_size: int,
    benchmark_sharpe: float = 0.0,
    annualized: bool = True,
    periods_per_year: float = 252.0,
) -> float:
    """Multiple-testing benchmark E[max Sharpe] under the null.

    Returns a value in the SAME units as ``benchmark_sharpe``: annualized when
    ``annualized=True``, so it can be compared directly with a reported Sharpe.

    The bar is ``se_null * E[max of k standard normals]``.  The standard error
    belongs to the Sharpe *estimator* (1/sqrt(n)), not to the standard normal,
    so omitting the scale -- as ``overfit_detector`` and both trial ledgers did
    -- reports a bar in sigma units as if it were a Sharpe ratio (4x too high
    at k=2811).
    """
    sr_0 = float(benchmark_sharpe)
    scale = math.sqrt(periods_per_year) if (annualized and periods_per_year > 1.0) else 1.0
    sr_0_period = sr_0 / scale

    k = int(num_trials)
    if k <= 1 or sample_size < 10:
        return float(sr_0)

    n = float(sample_size)
    # SE of the Sharpe estimator at the null (Gaussian moments: skew 0, kurt 3),
    # evaluated at the benchmark's own scale.
    se_null = math.sqrt(max(1.0 - sr_0_period * sr_0_period / 2.0, 1e-12) / max(n - 1.0, 1.0))
    exp_max_period = sr_0_period + se_null * _expected_max_standard_normal(k)
    return float(exp_max_period * scale)


def calculate_deflated_sharpe_ratio(
    estimated_sharpe: float,
    num_trials: int,
    sample_size: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
    annualized: bool = True,
    periods_per_year: float = 252.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014):
    Adjusts Sharpe ratio for multiple testing and selection bias.
    Returns p-value of rejecting the null hypothesis (true Sharpe <= benchmark).

    Units: see the module contract above. ``estimated_sharpe`` is annualized
    unless ``annualized=False``; ``periods_per_year`` must match how it was
    annualized. De-annualization happens internally so the comparison is made
    in per-period units against ``se_null`` and ``sr_variance``.
    """
    if num_trials < 1 or sample_size < 10:
        return 1.0
    
    sr = float(estimated_sharpe)
    sr_0 = float(benchmark_sharpe)
    
    if annualized and periods_per_year > 1.0:
        scale = math.sqrt(periods_per_year)
        sr = sr / scale
        sr_0 = sr_0 / scale
    n = float(sample_size)
    k = float(num_trials)
    gamma_3 = float(skewness)
    gamma_4 = float(kurtosis)
    
    # Expected maximum Sharpe under null of zero Sharpe across k independent
    # trials, in per-period units (the units of ``sr`` and ``sr_0`` here).
    # SE uses Gaussian moments at the null (skew 0, kurtosis 3) -- the null
    # distribution is defined there -- evaluated at sr_0's own scale.
    if k > 1:
        se_null = math.sqrt(
            max(1.0 - sr_0 * sr_0 / 2.0, 1e-12) / max(n - 1.0, 1.0)
        )
        exp_max_sr = sr_0 + se_null * _expected_max_standard_normal(int(k))
    else:
        exp_max_sr = sr_0
    
    # Variance of the Sharpe ratio estimator. If the supplied moments make
    # this non-positive (extreme sample skew/kurtosis — typical for series
    # dominated by zero bars), fall back to Gaussian moments instead of
    # bailing to p=1.0: a bail-out here masquerades as "not significant"
    # and auto-fails honest strategies (same confusion class as the
    # kurtosis-convention inversion).
    sr_variance = (1.0 - gamma_3 * sr + ((gamma_4 - 1.0) / 4.0) * (sr ** 2)) / (n - 1.0)
    if sr_variance <= 1e-12:
        gamma_3, gamma_4 = 0.0, 3.0
        sr_variance = (1.0 - gamma_3 * sr + ((gamma_4 - 1.0) / 4.0) * (sr ** 2)) / (n - 1.0)
        if sr_variance <= 1e-12:
            return 1.0  # unreachable for n > 1 — kept as a hard guard
    
    sr_std = np.sqrt(sr_variance)
    # Z-statistic
    z = (sr - exp_max_sr) / sr_std
    
    # Standard normal CDF using error function
    p_value = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return float(max(0.0, min(1.0, p_value)))


def calculate_information_coefficient(forecasts: Sequence[float], realized_returns: Sequence[float]) -> float:
    """Information Coefficient:
    IC = Pearson correlation between alpha forecast and realized forward returns.
    Target: IC >= 0.03 - 0.05

    Pairs are dropped jointly when either side is non-finite so alignment
    between forecast i and realized return i is never silently shifted.
    """
    if len(forecasts) != len(realized_returns):
        raise ValueError("forecasts and realized_returns must have equal length")
    pairs = [
        (float(f), float(r))
        for f, r in zip(forecasts, realized_returns)
        if math.isfinite(float(f)) and math.isfinite(float(r))
    ]
    if len(pairs) < 5:
        return 0.0
    f = np.asarray([p[0] for p in pairs], dtype=float)
    r = np.asarray([p[1] for p in pairs], dtype=float)
    std_f = np.std(f)
    std_r = np.std(r)
    if std_f <= 1e-12 or std_r <= 1e-12:
        return 0.0
    corr = np.corrcoef(f, r)[0, 1]
    return float(corr) if math.isfinite(corr) else 0.0


# ---------------------------------------------------------------------------
# Stage 3: Survival, Run Theory & Drawdown Physics
# ---------------------------------------------------------------------------

def calculate_risk_of_ruin(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float = 1.0,
    risk_fraction: float = 0.01,
) -> float:
    """Risk of Ruin calculation:
    RoR = ((1 - Edge) / (1 + Edge))^Units
    Where Edge = EV / L, Units = 1 / Risk%
    """
    p_win = float(win_rate)
    w = float(avg_win_r)
    l = float(avg_loss_r)
    rf = float(risk_fraction)
    
    if not all(math.isfinite(x) for x in (p_win, w, l, rf)) or rf <= 0 or l <= 0:
        return 1.0
    
    ev = (p_win * w) - ((1.0 - p_win) * l)
    edge = ev / l
    
    if edge <= 0:
        return 1.0  # Zero or negative edge guarantees ruin over infinite horizon
    if edge >= 1.0:
        return 0.0
    
    units = 1.0 / rf
    ratio = (1.0 - edge) / (1.0 + edge)
    try:
        ror = ratio ** units
        return float(max(0.0, min(1.0, ror)))
    except (OverflowError, ValueError):
        return 0.0


def calculate_streak_probability(loss_rate: float, streak_length: int) -> float:
    """Probability of experiencing k consecutive losses: P(k) = (P_loss)^k."""
    p_loss = float(loss_rate)
    k = int(streak_length)
    if not math.isfinite(p_loss) or p_loss < 0 or p_loss > 1 or k < 1:
        return 0.0
    return float(p_loss ** k)


def calculate_expected_max_losing_streak(loss_rate: float, total_trades: int) -> float:
    """Expected Maximum Losing Streak:
    L_max approx ln(N) / ln(1 / P_loss)
    """
    p_loss = float(loss_rate)
    n = int(total_trades)
    if not math.isfinite(p_loss) or p_loss <= 0 or p_loss >= 1 or n <= 1:
        return 0.0
    denom = math.log(1.0 / p_loss)
    if denom <= 1e-12:
        return 0.0
    return float(math.log(n) / denom)


def calculate_survival_edge_audit(
    r_multiples: Sequence[float],
    risk_fraction: float = 0.01,
    horizon_trades: int = 100,
    min_sample: int = 20,
) -> Dict[str, Any]:
    """Survival & edge audit composed from realised labelled R-multiples.

    Unites the Module-01 pillars (Expected Value, Ralph Vince risk-of-ruin,
    quarter-Kelly, run-theory L_max) over one durable journal sample so a
    caller can attach a single bounded advisory evidence object to a decision
    cycle.  Fail-closed: fewer than ``min_sample`` finite R-multiples returns
    ``INSUFFICIENT_DATA`` with null metrics; malformed values never raise.

    Advisory only: the output must never grant execution authority, raise a
    deterministic risk cap, or bypass validation gates.
    """
    rs: List[float] = []
    for raw in r_multiples:
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value):
            rs.append(value)
    n = len(rs)
    base = {
        "status": "INSUFFICIENT_DATA",
        "sample_count": n,
        "minimum_sample": int(min_sample),
        "win_rate": None,
        "avg_win_r": None,
        "avg_loss_r": None,
        "payoff_ratio": None,
        "expected_value_r": None,
        "risk_of_ruin": None,
        "quarter_kelly_fraction": None,
        "run_theory_l_max": None,
        "risk_fraction_assumed": float(risk_fraction),
        "kind": "advisory",
        "execution_authority": False,
    }
    if n < max(2, int(min_sample)):
        return base

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    win_rate = len(wins) / n
    avg_win_r = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss_r = (abs(sum(losses) / len(losses))) if losses else 1.0
    payoff_ratio = (avg_win_r / avg_loss_r) if avg_loss_r > 1e-12 else 0.0

    base.update({
        "status": "OK",
        "win_rate": round(win_rate, 4),
        "avg_win_r": round(avg_win_r, 4),
        "avg_loss_r": round(avg_loss_r, 4),
        "payoff_ratio": round(payoff_ratio, 4),
        "expected_value_r": round(
            calculate_expected_value(win_rate, avg_win_r, avg_loss_r), 4),
        "risk_of_ruin": round(
            calculate_risk_of_ruin(win_rate, avg_win_r, avg_loss_r, risk_fraction), 6),
        "quarter_kelly_fraction": round(
            calculate_quarter_kelly_sizing(win_rate, payoff_ratio), 6),
        "run_theory_l_max": round(
            calculate_expected_max_losing_streak(1.0 - win_rate, horizon_trades), 2),
    })
    return base


def calculate_calmar_ratio(cagr: float, max_drawdown: float) -> float:
    """Calmar / MAR Ratio:
    Calmar = CAGR / |MaxDD|
    Target: >= 1.5
    """
    c = float(cagr)
    mdd = abs(float(max_drawdown))
    if not math.isfinite(c) or not math.isfinite(mdd) or mdd <= 1e-6:
        return 0.0
    return float(c / mdd)


def calculate_recovery_factor(net_profit: float, max_drawdown: float) -> float:
    """Recovery Factor (P07 Anexo 1: Net Profit / Maximum Drawdown).

    Ability to recover from losses; P07 system reports 6.48 full-sample.
    Returns 0.0 for non-positive profit or non-finite inputs (fail-closed);
    infinite (zero-drawdown profit) is capped at 999.0 to stay JSON-safe.
    """
    try:
        pnl = float(net_profit)
        mdd = abs(float(max_drawdown))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(pnl) or not math.isfinite(mdd) or pnl <= 0:
        return 0.0
    if mdd <= 1e-12:
        return 999.0
    return float(pnl / mdd)


def calculate_asymmetric_loss_recovery_fraction(loss_fraction: float) -> float:
    """Calculates required gain to recover from a given capital drawdown fraction:
    Required Return = Loss / (1 - Loss)
    Examples:
      -10% (0.10) -> +11.1% (0.1111)
      -30% (0.30) -> +42.9% (0.4286)
      -50% (0.50) -> +100.0% (1.0000)
      -80% (0.80) -> +400.0% (4.0000)
      -90% (0.90) -> +900.0% (9.0000)
    """
    loss = float(loss_fraction)
    if not math.isfinite(loss) or loss < 0.0:
        return 0.0
    if loss >= 1.0:
        return float("inf")
    return float(loss / (1.0 - loss))


def calculate_discrete_lottery_expectancy(
    prize_payout: float = 43_000_000.0,
    probability_odds: float = 175_000_000.0,
    ticket_cost: float = 3.0,
) -> float:
    """Calculates discrete mathematical expectancy of a lottery ticket:
    EV = (1 / Odds) * Prize - Cost
    Mega Millions Proof: (1 / 175M) * $43M - $3.00 = -$2.7543 (Net Negative EV)
    """
    prize = float(prize_payout)
    odds = float(probability_odds)
    cost = float(ticket_cost)
    if not all(math.isfinite(x) for x in (prize, odds, cost)) or odds <= 0.0:
        return -cost
    return float((1.0 / odds) * prize - cost)



# ---------------------------------------------------------------------------
# Stage 4: Capital Allocation & Sizing
# ---------------------------------------------------------------------------

def calculate_kelly_criterion(win_rate: float, payoff_ratio: float) -> float:
    """Theoretical Kelly Criterion:
    K* = (P_win * R - (1 - P_win)) / R
    """
    p = float(win_rate)
    r = float(payoff_ratio)
    if not math.isfinite(p) or not math.isfinite(r) or r <= 0 or p < 0 or p > 1:
        return 0.0
    raw_k = (p * r - (1.0 - p)) / r
    return float(max(0.0, raw_k))


def calculate_quarter_kelly_sizing(win_rate: float, payoff_ratio: float, max_cap: float = 0.02) -> float:
    """Quarter-Kelly Sizing (0.25 K*):
    Captures ~75% growth with ~80% less drawdown. Capped at safety max (e.g. 2%).
    """
    k = calculate_kelly_criterion(win_rate, payoff_ratio)
    qk = 0.25 * k
    return float(min(qk, max_cap))


def calculate_cost_adjusted_payoff_ratio(
    reward_risk_ratio: float,
    stop_distance_points: float,
    spread_points: float = 0.0,
    commission_per_lot_dollar: float = 0.0,
    point_value_dollar: float = 1.0,
) -> float:
    """Cost-adjusted payoff ratio rho = W_unit(RR) / L_unit (Masoumian, 2026 Eq 6).
    
    Frictions:
    C_unit = C_commission + C_spread
    L_unit = D_stop * V + C_unit
    W_unit(RR) = D_stop * RR * V - C_unit
    rho = W_unit(RR) / L_unit
    """
    rr = float(reward_risk_ratio)
    d_stop = float(stop_distance_points)
    spread = float(spread_points)
    comm = float(commission_per_lot_dollar)
    v = float(point_value_dollar)

    if not all(math.isfinite(x) for x in (rr, d_stop, spread, comm, v)) or d_stop <= 0 or v <= 0:
        return 0.0

    c_unit = (spread * v) + comm
    l_unit = (d_stop * v) + c_unit
    w_unit = (d_stop * rr * v) - c_unit

    if l_unit <= 1e-12 or w_unit <= 0:
        return 0.0
    return float(w_unit / l_unit)


def calculate_cost_adjusted_kelly(
    win_rate: float,
    reward_risk_ratio: float,
    stop_distance_points: float,
    spread_points: float = 0.0,
    commission_per_lot_dollar: float = 0.0,
    point_value_dollar: float = 1.0,
) -> float:
    """Cost-adjusted Kelly Fraction f* = WR - (1 - WR) / rho (Masoumian, 2026 Eq 13).
    
    If rho <= 0 (gross payoff does not cover execution frictions), returns 0.0 (no-trade zone).
    """
    p = float(win_rate)
    if not math.isfinite(p) or p <= 0 or p >= 1:
        return 0.0
    rho = calculate_cost_adjusted_payoff_ratio(
        reward_risk_ratio, stop_distance_points, spread_points,
        commission_per_lot_dollar, point_value_dollar
    )
    if rho <= 1e-12:
        return 0.0
    f_star = p - ((1.0 - p) / rho)
    return float(max(0.0, f_star))


def calculate_cost_adjusted_quarter_kelly(
    win_rate: float,
    reward_risk_ratio: float,
    stop_distance_points: float,
    spread_points: float = 0.0,
    commission_per_lot_dollar: float = 0.0,
    point_value_dollar: float = 1.0,
    max_cap: float = 0.02,
) -> float:
    """Cost-adjusted Quarter-Kelly Sizing (0.25 f*) (Masoumian, 2026)."""
    f_star = calculate_cost_adjusted_kelly(
        win_rate, reward_risk_ratio, stop_distance_points, spread_points,
        commission_per_lot_dollar, point_value_dollar
    )
    return float(min(0.25 * f_star, max_cap))


def calculate_compound_growth_rate(
    win_rate: float,
    reward_risk_ratio: float,
    risk_fraction: float,
    friction_r: float = 0.0,
) -> float:
    """Expected geometric compound growth rate per trade under frictions (Masoumian 2026 §3.3).
    
    g(f) = (1 + f * (W - c))^p * (1 - f * (L + c))^(1 - p) - 1
    where W is reward_risk_ratio, L is 1.0, and c is friction_r.
    """
    p = float(win_rate)
    f = float(risk_fraction)
    w = float(reward_risk_ratio)
    c = float(friction_r)

    if not all(math.isfinite(x) for x in (p, f, w, c)) or p <= 0.0 or p >= 1.0 or f <= 0.0:
        return 0.0

    net_win = w - c
    net_loss = 1.0 + c

    gain_factor = 1.0 + f * net_win
    loss_factor = 1.0 - f * net_loss

    if gain_factor <= 0.0 or loss_factor <= 0.0:
        return -1.0  # Ruin

    g = (gain_factor ** p) * (loss_factor ** (1.0 - p)) - 1.0
    return float(g)


def check_opportunity_cost_hurdle(
    win_rate: float,
    reward_risk_ratio: float,
    friction_r: float = 0.0,
    r_opp: float = 0.05,
) -> Tuple[bool, float, str]:
    """Validate whether cost-adjusted expectancy clears the opportunity cost hurdle of capital (Masoumian 2026).
    
    EV_net = p * W - (1 - p) * 1.0 - friction_r
    A trade is only permitted if EV_net >= r_opp.
    """
    p = float(win_rate)
    w = float(reward_risk_ratio)
    f = float(friction_r)
    hurdle = float(r_opp)

    if not all(math.isfinite(x) for x in (p, w, f, hurdle)):
        return False, 0.0, "invalid_parameters"

    p = max(0.0, min(1.0, p))
    ev_net = (p * w) - ((1.0 - p) * 1.0) - f

    if ev_net < hurdle:
        return False, ev_net, f"expectancy_{ev_net:.4f}_below_opportunity_hurdle_{hurdle:.4f}"
    return True, ev_net, "admissible"


def calculate_statistical_kill_switch(win_rate: float, alpha: float = 0.01) -> int:
    """Analytical Kill-Switch threshold for anomalous consecutive losses (Masoumian, 2026 Eq 11).
    
    n_loss^(alpha) = ceil( ln(alpha) / ln(1 - WR) )
    
    At confidence level 1 - alpha (e.g. 99% with alpha=0.01), a streak of consecutive
    losses of this length is statistically anomalous and triggers an emergency halt.
    """
    p = float(win_rate)
    a = float(alpha)
    if not math.isfinite(p) or not math.isfinite(a) or p <= 0 or p >= 1 or a <= 0 or a >= 1:
        return 10
    denom = math.log(1.0 - p)
    if abs(denom) < 1e-12:
        return 10
    streak_float = math.log(a) / denom
    return int(math.ceil(streak_float))


def calculate_volatility_parity_size(
    account_equity: float,
    target_volatility: float,
    atr_20: float,
    price: float,
    contract_multiplier: float = 1.0,
) -> float:
    """Volatility Parity Allocation:
    Size_i = (Account Equity * Target Vol) / (ATR_20 * Contract Multiplier)
    """
    eq = float(account_equity)
    t_vol = float(target_volatility)
    atr = float(atr_20)
    p = float(price)
    mult = float(contract_multiplier)
    
    if not all(math.isfinite(x) for x in (eq, t_vol, atr, p, mult)) or atr <= 0 or p <= 0 or eq <= 0:
        return 0.0
    
    dollar_risk = eq * t_vol
    loss_unit = atr * mult
    if loss_unit <= 0:
        return 0.0
    return float(dollar_risk / loss_unit)


# ---------------------------------------------------------------------------
# Stage 5: Regime Detection & Master Expectancy
# ---------------------------------------------------------------------------

def calculate_hurst_exponent(price_series: Sequence[float], min_window: int = 10, max_window: Optional[int] = None) -> float:
    """Hurst Exponent (H) via Rescaled Range (R/S) Analysis:
    H > 0.55: Trending / Persistent
    H < 0.45: Mean-Reverting / Anti-persistent
    H ~ 0.50: Random Walk / Geometric Brownian Motion
    """
    prices = np.asarray([float(p) for p in price_series if math.isfinite(float(p))], dtype=float)
    n = len(prices)
    if n < 50:
        return 0.50  # Default neutral random walk
    
    # Calculate log returns
    returns = np.diff(np.log(prices))
    n_ret = len(returns)
    
    max_w = max_window or (n_ret // 2)
    min_w = max(min_window, 8)
    
    if max_w <= min_w:
        return 0.50
    
    # Generate lag window sizes in log space
    lags = np.unique(np.logspace(np.log10(min_w), np.log10(max_w), num=12).astype(int))
    rs_values = []
    
    for lag in lags:
        # Split into non-overlapping sub-series
        num_chunks = n_ret // lag
        if num_chunks < 1:
            continue
        
        rs_chunk = []
        for i in range(num_chunks):
            chunk = returns[i * lag : (i + 1) * lag]
            m = np.mean(chunk)
            # Cumulative deviations from mean
            y = np.cumsum(chunk - m)
            # Range R
            r_range = np.max(y) - np.min(y)
            # Sample standard deviation S
            s = np.std(chunk, ddof=1)
            if s > 1e-12:
                rs_chunk.append(r_range / s)
        
        if rs_chunk:
            rs_values.append((lag, np.mean(rs_chunk)))
    
    if len(rs_values) < 4:
        return 0.50
    
    log_lags = np.log([x[0] for x in rs_values])
    log_rs = np.log([x[1] for x in rs_values])
    
    # Linear regression slope is Hurst exponent
    slope, _ = np.polyfit(log_lags, log_rs, 1)
    return float(max(0.0, min(1.0, slope)))


def calculate_atr_percentile(atr_history: Sequence[float], current_atr: float) -> float:
    """Calculates percentile rank of current ATR over rolling historical window (e.g. 252 bars)."""
    history = [float(x) for x in atr_history if math.isfinite(float(x))]
    cur = float(current_atr)
    if not history or not math.isfinite(cur):
        return 50.0
    count_below = sum(1 for x in history if x < cur)
    return float((count_below / len(history)) * 100.0)


def calculate_master_expectancy(
    regime_probabilities: Dict[str, float],
    strategy_weights: Dict[str, Dict[str, float]],
    strategy_expectancies: Dict[str, Dict[str, float]],
) -> float:
    """Regime-Conditioned Master Expectancy:
    EV_Master = sum_k P(Regime_k) * sum_i (w_{i,k} * EV_{i,k})
    Target: EV_Master >= +0.20R
    """
    total_ev = 0.0
    for regime, p_regime in regime_probabilities.items():
        if not math.isfinite(p_regime) or p_regime <= 0:
            continue
        weights = strategy_weights.get(regime, {})
        expectancies = strategy_expectancies.get(regime, {})
        regime_ev = 0.0
        for strat, w in weights.items():
            ev = expectancies.get(strat, 0.0)
            if math.isfinite(w) and math.isfinite(ev):
                regime_ev += w * ev
        total_ev += p_regime * regime_ev
    return float(total_ev)


# ---------------------------------------------------------------------------
# Stage 6: Compounding & Macro Performance
# ---------------------------------------------------------------------------

def calculate_cagr(start_equity: float, end_equity: float, duration_years: float) -> float:
    """Compound Annual Growth Rate:
    CAGR = (End / Start)^(1/n) - 1
    """
    start = float(start_equity)
    end = float(end_equity)
    years = float(duration_years)
    if not all(math.isfinite(x) for x in (start, end, years)) or start <= 0 or years <= 0:
        return 0.0
    # Total loss must be visible as -100% growth, never silently reported as 0%.
    if end <= 0:
        return -1.0
    try:
        return float((end / start) ** (1.0 / years) - 1.0)
    except (OverflowError, ValueError):
        return 0.0


def calculate_sharpe_ratio(returns: Sequence[float], risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
    """Annualized Sharpe Ratio: Sharpe = (R_p - R_f) / sigma_p * sqrt(N)."""
    rets = np.asarray([float(r) for r in returns if math.isfinite(float(r))], dtype=float)
    if len(rets) < 5:
        return 0.0
    excess_rets = rets - (risk_free_rate / periods_per_year)
    mean_excess = np.mean(excess_rets)
    # Sharpe uses the dispersion of EXCESS returns so a changing risk-free
    # path cannot deflate/inflate the denominator inconsistently.
    std_excess = np.std(excess_rets, ddof=1)
    if std_excess <= 1e-12:
        return 0.0
    return float((mean_excess / std_excess) * np.sqrt(periods_per_year))


def calculate_sortino_ratio(
    returns: Sequence[float],
    target_return: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualized Sortino Ratio:
    Sortino = (R_p - R_target) / sigma_downside * sqrt(N)
    where sigma_downside = sqrt(mean(min(0, R_i - target)^2))
    """
    rets = np.asarray([float(r) for r in returns if math.isfinite(float(r))], dtype=float)
    if len(rets) < 5:
        return 0.0
    excess_rets = rets - (target_return / periods_per_year)
    mean_excess = np.mean(excess_rets)
    
    downside = np.minimum(0.0, excess_rets)
    downside_var = np.mean(downside ** 2)
    if downside_var <= 1e-12:
        return 0.0
    downside_std = np.sqrt(downside_var)
    return float((mean_excess / downside_std) * np.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Master Integrated Metrics Tracker
# ---------------------------------------------------------------------------

@dataclass
class QuantitativeMetricsEngine:
    """Institutional-grade metrics engine tracking real-time trade performance."""
    initial_balance: float = 0.0
    risk_free_rate: float = 0.04
    equity_curve: List[float] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)
    r_multiples: List[float] = field(default_factory=list)
    trade_pnls: List[float] = field(default_factory=list)
    peak_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0

    def __post_init__(self):
        if self.initial_balance > 0 and not self.equity_curve:
            self.equity_curve.append(self.initial_balance)
            self.peak_equity = self.initial_balance

    def update_equity(self, current_equity: float) -> None:
        eq = float(current_equity)
        if not math.isfinite(eq) or eq <= 0:
            return
        if self.peak_equity <= 0:
            self.peak_equity = eq
        if not self.equity_curve:
            self.equity_curve.append(eq)
        
        # Calculate return from prior point
        if self.equity_curve:
            prior = self.equity_curve[-1]
            if prior > 0:
                self.returns.append((eq - prior) / prior)
        
        self.equity_curve.append(eq)
        self.peak_equity = max(self.peak_equity, eq)
        dd = (self.peak_equity - eq) / self.peak_equity * 100.0 if self.peak_equity > 0 else 0.0
        self.max_drawdown_pct = max(self.max_drawdown_pct, dd)

    def record_trade(self, pnl: float, r_multiple: Optional[float] = None,
                     risk_amount: Optional[float] = None) -> None:
        pnl_val = float(pnl)
        if not math.isfinite(pnl_val):
            return
        self.trade_pnls.append(pnl_val)

        r_val: Optional[float] = None
        if r_multiple is not None and math.isfinite(float(r_multiple)):
            r_val = float(r_multiple)
        elif risk_amount is not None and math.isfinite(float(risk_amount)) and float(risk_amount) > 0:
            # Prefer the ACTUAL planned dollar risk captured at entry.
            r_val = pnl_val / float(risk_amount)
        if r_val is None:
            # Legacy fallback assumes 1% of initial balance; callers should
            # pass the true planned risk so EV/SQN/Kelly inputs stay honest.
            r_val = pnl_val / max(1.0, self.initial_balance * 0.01)
        self.r_multiples.append(r_val)
        
        if r_val < 0:
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)
        else:
            self.consecutive_losses = 0

    def to_state(self) -> Dict[str, Any]:
        """Serialize engine state so EV/SQN/streak counters survive restarts."""
        def tail(seq: List[float], n: int = 1000) -> List[float]:
            return [float(x) for x in list(seq)[-n:]]
        return {
            "initial_balance": float(self.initial_balance),
            "risk_free_rate": float(self.risk_free_rate),
            "equity_curve": tail(self.equity_curve),
            "returns": tail(self.returns),
            "r_multiples": tail(self.r_multiples),
            "trade_pnls": tail(self.trade_pnls),
            "peak_equity": float(self.peak_equity),
            "max_drawdown_pct": float(self.max_drawdown_pct),
            "consecutive_losses": int(self.consecutive_losses),
            "max_consecutive_losses": int(self.max_consecutive_losses),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        data = dict(state or {})
        try:
            self.risk_free_rate = float(data.get("risk_free_rate", self.risk_free_rate))
            self.equity_curve = [float(x) for x in data.get("equity_curve", [])]
            self.returns = [float(x) for x in data.get("returns", [])]
            self.r_multiples = [float(x) for x in data.get("r_multiples", [])]
            self.trade_pnls = [float(x) for x in data.get("trade_pnls", [])]
            peak = float(data.get("peak_equity") or 0.0)
            self.peak_equity = peak if peak > 0 else (
                self.equity_curve[-1] if self.equity_curve else self.initial_balance)
            self.max_drawdown_pct = float(data.get("max_drawdown_pct", 0.0))
            self.consecutive_losses = int(data.get("consecutive_losses", 0))
            self.max_consecutive_losses = int(data.get("max_consecutive_losses", 0))
        except (TypeError, ValueError, OverflowError):
            return

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "QuantitativeMetricsEngine":
        data = dict(state or {})
        engine = cls(initial_balance=float(data.get("initial_balance") or 0.0))
        engine.restore_state(data)
        return engine

    def compute_summary(self, backtest_trials: int = 1) -> Dict[str, Any]:
        total_trades = len(self.r_multiples)
        wins = [r for r in self.r_multiples if r > 0]
        losses = [r for r in self.r_multiples if r < 0]
        
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        avg_win_r = float(np.mean(wins)) if wins else 0.0
        avg_loss_r = abs(float(np.mean(losses))) if losses else 0.0
        
        payoff_ratio = (avg_win_r / avg_loss_r) if avg_loss_r > 0 else 0.0
        ev_r = calculate_expected_value(win_rate, avg_win_r, avg_loss_r)
        sqn = calculate_sqn(self.r_multiples)
        
        # Risk of ruin & streaks
        ror = calculate_risk_of_ruin(win_rate, avg_win_r, avg_loss_r, risk_fraction=0.01)
        expected_l_max = calculate_expected_max_losing_streak(1.0 - win_rate, total_trades=max(10, total_trades))
        
        # Returns metrics
        sharpe = calculate_sharpe_ratio(self.returns, risk_free_rate=self.risk_free_rate)
        sortino = calculate_sortino_ratio(self.returns, target_return=self.risk_free_rate)
        
        # CAGR
        current_eq = self.equity_curve[-1] if self.equity_curve else self.initial_balance
        duration_years = max(1.0 / 252.0, len(self.returns) / 252.0)
        cagr = calculate_cagr(self.initial_balance, current_eq, duration_years)
        calmar = calculate_calmar_ratio(cagr, self.max_drawdown_pct / 100.0)
        
        # DSR
        dsr_pvalue = calculate_deflated_sharpe_ratio(
            estimated_sharpe=sharpe,
            num_trials=max(1, backtest_trials),
            sample_size=max(10, len(self.returns)),
        )
        
        # Quarter-Kelly recommendation
        qk = calculate_quarter_kelly_sizing(win_rate, payoff_ratio)

        # Time under water
        tuw = calculate_time_under_water(self.equity_curve) if self.equity_curve else {
            "max_underwater_bars": 0, "avg_underwater_bars": 0.0, "current_underwater_bars": 0, "underwater_periods": []
        }

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
            "payoff_ratio": round(payoff_ratio, 4),
            "expected_value_r": round(ev_r, 4),
            "sqn": round(sqn, 2),
            "risk_of_ruin": round(ror, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "calmar_ratio": round(calmar, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "cagr": round(cagr, 4),
            "expected_max_loss_streak": round(expected_l_max, 1),
            "current_consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses_observed": self.max_consecutive_losses,
            "deflated_sharpe_pvalue": round(dsr_pvalue, 4),
            "advisory_quarter_kelly": round(qk, 4),
            "current_equity": round(current_eq, 2),
            "total_pnl": round(current_eq - self.initial_balance, 2),
            "max_underwater_bars": tuw["max_underwater_bars"],
            "avg_underwater_bars": tuw["avg_underwater_bars"],
            "current_underwater_bars": tuw["current_underwater_bars"],
        }


# Backward-compatible MetricsTracker facade
class MetricsTracker(QuantitativeMetricsEngine):
    def update(self, balance: float, equity: float):
        if self.initial_balance == 0:
            self.initial_balance = float(balance)
            self.peak_equity = float(equity)
        self.update_equity(equity)

    def add_trade(self, profit: float, r_multiple: Optional[float] = None):
        self.record_trade(profit, r_multiple)

    def get_metrics(self) -> Dict[str, Any]:
        summary = self.compute_summary()
        return {
            "winrate": summary["win_rate"],
            "sharpe": summary["sharpe_ratio"],
            "sortino": summary["sortino_ratio"],
            "drawdown": summary["max_drawdown_pct"],
            "daily_pnl": summary["total_pnl"],
            "expected_value_r": summary["expected_value_r"],
            "sqn": summary["sqn"],
            "calmar": summary["calmar_ratio"],
            "risk_of_ruin": summary["risk_of_ruin"],
            "cagr": summary["cagr"],
        }


# ---------------------------------------------------------------------------
# Stage 6: Multiple Testing & Snooping Control (FDR, Stationary Bootstrap, White's Reality Check, Hansen's SPA)
# ---------------------------------------------------------------------------

def calculate_benjamini_hochberg_fdr(
    p_values: Sequence[float],
    fdr_target: float = 0.05,
) -> Tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg (1995) False Discovery Rate (FDR) control.

    Given M hypothesis test p-values, adjusts for multiple testing to control
    the expected false discovery rate at `fdr_target`.

    Returns:
        (adjusted_q_values, is_significant_mask)
    """
    if len(p_values) == 0:
        return np.array([], dtype=float), np.array([], dtype=bool)

    raw_p = np.asarray(p_values, dtype=float)
    raw_p = np.where(np.isnan(raw_p) | ~np.isfinite(raw_p), 1.0, raw_p)
    raw_p = np.clip(raw_p, 0.0, 1.0)

    m = len(raw_p)
    sorted_indices = np.argsort(raw_p)
    sorted_p = raw_p[sorted_indices]
    ranks = np.arange(1, m + 1, dtype=float)

    # Benjamini-Hochberg critical thresholds: (i / m) * q
    thresholds = (ranks / float(m)) * float(fdr_target)
    significant_in_sorted = sorted_p <= thresholds

    is_significant_sorted = np.zeros(m, dtype=bool)
    if np.any(significant_in_sorted):
        max_k = int(np.max(np.where(significant_in_sorted)[0]))
        is_significant_sorted[: max_k + 1] = True

    is_significant = np.zeros(m, dtype=bool)
    is_significant[sorted_indices] = is_significant_sorted

    # Adjusted q-values: enforce monotonicity from right to left
    # q_(i) = min_{j >= i} (m / j * p_(j))
    unadjusted_q = (sorted_p * float(m)) / ranks
    q_sorted = np.minimum.accumulate(unadjusted_q[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0.0, 1.0)

    q_values = np.empty_like(q_sorted)
    q_values[sorted_indices] = q_sorted

    return q_values, is_significant


def calculate_bonferroni_fwer(p_values: Sequence[float]) -> np.ndarray:
    """Bonferroni Family-Wise Error Rate (FWER) adjusted p-values.

    p_adj = min(1.0, M * p)
    """
    if len(p_values) == 0:
        return np.array([], dtype=float)
    raw_p = np.asarray(p_values, dtype=float)
    raw_p = np.where(np.isnan(raw_p) | ~np.isfinite(raw_p), 1.0, raw_p)
    m = float(len(raw_p))
    return np.clip(raw_p * m, 0.0, 1.0)


def generate_stationary_bootstrap_indices(
    n_samples: int,
    mean_block_length: float = 10.0,
    num_bootstraps: int = 1000,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Politis & Romano (1994) Stationary Bootstrap index generator.

    Generates (num_bootstraps, n_samples) resampled time-series indices using
    geometric block lengths to preserve serial correlation and volatility clustering.
    """
    if n_samples <= 0:
        return np.empty((num_bootstraps, 0), dtype=int)
    if num_bootstraps <= 0:
        return np.empty((0, n_samples), dtype=int)

    rng = np.random.default_rng(seed)
    p_geom = 1.0 / max(1.0, float(mean_block_length))
    p_geom = min(1.0, max(1e-6, p_geom))

    indices = np.empty((num_bootstraps, n_samples), dtype=int)
    for b in range(num_bootstraps):
        current_idx = rng.integers(0, n_samples)
        indices[b, 0] = current_idx
        # Random transitions for the remaining steps
        new_block_flags = rng.random(n_samples - 1) < p_geom
        new_starts = rng.integers(0, n_samples, size=n_samples - 1)
        for t in range(1, n_samples):
            if new_block_flags[t - 1]:
                current_idx = new_starts[t - 1]
            else:
                current_idx = (current_idx + 1) % n_samples
            indices[b, t] = current_idx

    return indices


def calculate_whites_reality_check(
    strategy_returns: np.ndarray,
    benchmark_returns: Optional[np.ndarray] = None,
    mean_block_length: float = 10.0,
    num_bootstraps: int = 1000,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """White's Reality Check (White 2000) for data-snooping bias across K strategies.

    Tests null hypothesis H0: max_k E[d_k] <= 0 against H1: max_k E[d_k] > 0
    where d_k = strategy_k - benchmark.
    """
    strat = np.asarray(strategy_returns, dtype=float)
    if strat.ndim == 1:
        strat = strat[:, np.newaxis]

    t_obs, k_models = strat.shape
    if t_obs < 10 or k_models == 0:
        return {
            "p_value": 1.0,
            "white_stat": 0.0,
            "best_strategy_index": 0,
            "best_strategy_mean": 0.0,
            "is_significant": False,
            "num_strategies": k_models,
            "sample_size": t_obs,
            "advisory_only": True,
        }

    if benchmark_returns is not None:
        bench = np.asarray(benchmark_returns, dtype=float).flatten()
        if len(bench) != t_obs:
            raise ValueError("benchmark_returns length must match strategy_returns length")
        differentials = strat - bench[:, np.newaxis]
    else:
        differentials = strat.copy()

    # Replace non-finite values with 0
    differentials = np.where(np.isfinite(differentials), differentials, 0.0)

    mean_diffs = np.mean(differentials, axis=0)
    best_idx = int(np.argmax(mean_diffs))
    best_mean = float(mean_diffs[best_idx])

    # White's observed test statistic: V_bar = max_k sqrt(T) * d_bar_k
    sqrt_t = math.sqrt(float(t_obs))
    white_stat = float(np.max(sqrt_t * mean_diffs))

    if white_stat <= 0.0:
        # None of the models even beat the benchmark on average
        return {
            "p_value": 1.0,
            "white_stat": round(white_stat, 4),
            "best_strategy_index": best_idx,
            "best_strategy_mean": round(best_mean, 6),
            "is_significant": False,
            "num_strategies": k_models,
            "sample_size": t_obs,
            "advisory_only": True,
        }

    boot_indices = generate_stationary_bootstrap_indices(
        n_samples=t_obs, mean_block_length=mean_block_length, num_bootstraps=num_bootstraps, seed=seed
    )

    # Compute bootstrap means for each replication
    count_exceedances = 0
    for b in range(num_bootstraps):
        b_idx = boot_indices[b]
        b_diffs = differentials[b_idx, :]
        b_mean = np.mean(b_diffs, axis=0)
        # Centering under least-favorable configuration: (b_mean - mean_diffs)
        b_stat = float(np.max(sqrt_t * (b_mean - mean_diffs)))
        if b_stat >= white_stat:
            count_exceedances += 1

    p_value = float(count_exceedances / float(num_bootstraps))

    return {
        "p_value": round(p_value, 4),
        "white_stat": round(white_stat, 4),
        "best_strategy_index": best_idx,
        "best_strategy_mean": round(best_mean, 6),
        "is_significant": p_value < 0.05,
        "num_strategies": k_models,
        "sample_size": t_obs,
        "advisory_only": True,
    }


def calculate_hansens_spa(
    strategy_returns: np.ndarray,
    benchmark_returns: Optional[np.ndarray] = None,
    mean_block_length: float = 10.0,
    num_bootstraps: int = 1000,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Hansen's (2005) Test for Superior Predictive Ability (SPA).

    Refines White's Reality Check by studentizing test statistics and applying
    adaptive recentering mu^c to eliminate asymptotic distortion from poor models.
    """
    strat = np.asarray(strategy_returns, dtype=float)
    if strat.ndim == 1:
        strat = strat[:, np.newaxis]

    t_obs, k_models = strat.shape
    if t_obs < 10 or k_models == 0:
        return {
            "p_value_spa": 1.0,
            "spa_stat": 0.0,
            "best_strategy_index": 0,
            "best_strategy_mean": 0.0,
            "is_significant": False,
            "num_strategies": k_models,
            "sample_size": t_obs,
            "advisory_only": True,
        }

    if benchmark_returns is not None:
        bench = np.asarray(benchmark_returns, dtype=float).flatten()
        differentials = strat - bench[:, np.newaxis]
    else:
        differentials = strat.copy()

    differentials = np.where(np.isfinite(differentials), differentials, 0.0)
    sqrt_t = math.sqrt(float(t_obs))
    mean_diffs = np.mean(differentials, axis=0)

    boot_indices = generate_stationary_bootstrap_indices(
        n_samples=t_obs, mean_block_length=mean_block_length, num_bootstraps=num_bootstraps, seed=seed
    )

    # Bootstrap estimates of variance for studentization
    b_means = np.empty((num_bootstraps, k_models), dtype=float)
    for b in range(num_bootstraps):
        b_idx = boot_indices[b]
        b_means[b, :] = np.mean(differentials[b_idx, :], axis=0)

    # Standard deviation of sqrt(T) * d_bar under bootstrap
    omega_hat = np.std(sqrt_t * b_means, axis=0, ddof=1)
    omega_hat = np.where(omega_hat > 1e-8, omega_hat, 1.0)

    # Studentized test statistic
    studentized_t = (sqrt_t * mean_diffs) / omega_hat
    best_idx = int(np.argmax(studentized_t))
    spa_stat = float(max(0.0, np.max(studentized_t)))

    if spa_stat <= 0.0:
        return {
            "p_value_spa": 1.0,
            "spa_stat": 0.0,
            "best_strategy_index": best_idx,
            "best_strategy_mean": round(float(mean_diffs[best_idx]), 6),
            "is_significant": False,
            "num_strategies": k_models,
            "sample_size": t_obs,
            "advisory_only": True,
        }

    # Hansen's adaptive recentering threshold: -sqrt(2 * ln(ln(T))) * omega_k
    c_threshold = -math.sqrt(2.0 * math.log(max(1.1, math.log(float(t_obs))))) * omega_hat
    mu_c = np.where(sqrt_t * mean_diffs >= c_threshold, mean_diffs, 0.0)

    # Bootstrap exceedance rate for studentized SPA
    count_exceedances = 0
    for b in range(num_bootstraps):
        b_stat_k = (sqrt_t * (b_means[b, :] - mu_c)) / omega_hat
        b_spa = float(max(0.0, np.max(b_stat_k)))
        if b_spa >= spa_stat:
            count_exceedances += 1

    p_value_spa = float(count_exceedances / float(num_bootstraps))

    return {
        "p_value_spa": round(p_value_spa, 4),
        "spa_stat": round(spa_stat, 4),
        "best_strategy_index": best_idx,
        "best_strategy_mean": round(float(mean_diffs[best_idx]), 6),
        "best_studentized_t": round(float(studentized_t[best_idx]), 4),
        "is_significant": p_value_spa < 0.05,
        "num_strategies": k_models,
        "sample_size": t_obs,
        "advisory_only": True,
    }


def screen_candidate_setups_fdr(
    setup_pvalues: Dict[str, float],
    fdr_target: float = 0.05,
) -> Dict[str, Any]:
    """High-level orchestrator helper for screening candidate setup families.

    Filters candidate trade setups using Benjamini-Hochberg FDR control to purge
    false discoveries while retaining statistically robust setups.
    """
    if not setup_pvalues:
        return {"total_setups": 0, "discoveries_count": 0, "fdr_target": fdr_target, "results": {}}

    setup_ids = list(setup_pvalues.keys())
    raw_p = [float(setup_pvalues[sid]) for sid in setup_ids]

    q_vals, sig_mask = calculate_benjamini_hochberg_fdr(raw_p, fdr_target=fdr_target)
    bonf = calculate_bonferroni_fwer(raw_p)

    results: Dict[str, Any] = {}
    for idx, sid in enumerate(setup_ids):
        results[sid] = {
            "raw_p": round(raw_p[idx], 5),
            "fdr_q_value": round(float(q_vals[idx]), 5),
            "bonferroni_p": round(float(bonf[idx]), 5),
            "is_significant": bool(sig_mask[idx]),
        }

    discoveries = [sid for sid, res in results.items() if res["is_significant"]]

    return {
        "total_setups": len(setup_ids),
        "discoveries_count": len(discoveries),
        "fdr_target": fdr_target,
        "discoveries": discoveries,
        "results": results,
    }

def calculate_time_under_water(
    equity_curve: Sequence[float],
) -> Dict[str, Any]:
    """Calculate time-under-water metrics (drawdown duration in bars).

    Tracks consecutive periods where equity is strictly below previous all-time-high.

    Args:
        equity_curve: Sequential series of account equity values.

    Returns:
        Dict with max_underwater_bars, avg_underwater_bars, current_underwater_bars, underwater_periods.
    """
    arr = np.asarray(equity_curve, dtype=float)
    if len(arr) < 2 or not np.all(np.isfinite(arr)):
        return {
            "max_underwater_bars": 0,
            "avg_underwater_bars": 0.0,
            "current_underwater_bars": 0,
            "underwater_periods": [],
        }

    running_max = np.maximum.accumulate(arr)
    is_underwater = arr < running_max

    completed_periods: List[int] = []
    current_run = 0

    for under in is_underwater:
        if under:
            current_run += 1
        else:
            if current_run > 0:
                completed_periods.append(current_run)
                current_run = 0

    max_period = max(completed_periods + [current_run]) if (completed_periods or current_run > 0) else 0
    avg_period = float(np.mean(completed_periods)) if completed_periods else 0.0

    return {
        "max_underwater_bars": int(max_period),
        "avg_underwater_bars": round(avg_period, 2),
        "current_underwater_bars": int(current_run),
        "underwater_periods": completed_periods,
    }


# ---------------------------------------------------------------------------
# Exact Mathematical & Econometric Canonical Aliases
# ---------------------------------------------------------------------------
calculate_structural_invalidation_distance = calculate_stop_distance
calculate_fixed_fractional_dollar_risk = calculate_1r_dollar_risk
calculate_discrete_lot_allocation = calculate_normalized_lot_size
calculate_dimensionless_realized_r_multiple = calculate_realized_r_multiple
calculate_mathematical_expectancy = calculate_expected_value
calculate_system_quality_number = calculate_sqn
calculate_walk_forward_efficiency = calculate_wfe
calculate_ruin_probability_balsara = calculate_risk_of_ruin
calculate_asymmetric_loss_recovery = calculate_asymmetric_loss_recovery_fraction
calculate_lottery_expected_value = calculate_discrete_lottery_expectancy
calculate_run_theory_streak_probability = calculate_streak_probability
calculate_run_theory_expected_max_streak = calculate_expected_max_losing_streak
calculate_calmar_drawdown_ratio = calculate_calmar_ratio
calculate_kelly_optimal_fraction = calculate_kelly_criterion
calculate_fractional_kelly_allocation = calculate_quarter_kelly_sizing
calculate_volatility_parity_allocation = calculate_volatility_parity_size
calculate_rescaled_range_hurst_exponent = calculate_hurst_exponent
calculate_empirical_atr_percentile = calculate_atr_percentile
calculate_regime_conditioned_master_expectancy = calculate_master_expectancy
calculate_compound_annual_growth_rate = calculate_cagr
calculate_annualized_sharpe_ratio = calculate_sharpe_ratio
calculate_downside_deviation_sortino_ratio = calculate_sortino_ratio
calculate_false_discovery_rate_benjamini_hochberg = calculate_benjamini_hochberg_fdr
calculate_data_snooping_reality_check_white = calculate_whites_reality_check
calculate_superior_predictive_ability_hansen = calculate_hansens_spa


