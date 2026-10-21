"""Carver (2015) forecast-to-position chain — opt-in, standalone.

The chain, as prescribed in "Systematic Trading" (Harriman House, 2015):

    trading rule -> raw forecast
      -> rescale so mean |forecast| ~= 10          (Appendix D, p.297)
      -> combine rules, each weight capped at 2x    (Chapter 8)
         equal weight
      -> cap the combined forecast at +/-20         (Chapter 12, p.201)
      -> volatility scalar = daily cash vol target  (Chapter 10)
         / instrument value volatility
      -> subsystem position = (forecast / 10) * scalar
      -> portfolio position = subsystem * instrument weight
         * diversification multiplier               (Chapter 11)
      -> round, and skip trades inside 10% of the   (Chapter 11, p.174)
         current position (position inertia)

Nothing in the live trading path imports this module. It exists so the
sizing chain can be unit-tested and compared against the repo's existing
multiplier-based sizing paths (see docs/CARVER_FRAMEWORK_MAP.md).

Units: price volatility and returns are FRACTIONS (0.012 == 1.2%).

References:
    Carver, R. (2015). "Systematic Trading." Harriman House.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class CarverConfig:
    """Tunable thresholds for the sizing chain (book defaults)."""

    target_vol: float = 0.10
    """Annualised cash volatility target, as a fraction of equity (Ch. 9)."""

    periods_per_year: int = 252
    """Annualisation period. The book's worked example uses 256 business
    days (annualised -> daily is a divide by 16)."""

    forecast_scale: float = 10.0
    """A combined forecast of 10 means one volatility scalar (Ch. 10)."""

    max_forecast: float = 20.0
    """Recommended maximum absolute combined forecast (p.201)."""

    max_weight_multiple: float = 2.0
    """Cap on a single rule's weight, as a multiple of equal weight (Ch. 8)."""

    inertia_band: float = 0.10
    """Skip trades while the target is within 10% of the current (p.174)."""

    ewma_lookback: int = 36
    """EWMA lookback for the volatility estimate; A = 2 / (1 + L) (App. D)."""

    min_volatility: float = 1e-9


DEFAULT = CarverConfig()


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def forecast_scalar(forecasts: Sequence[float], target: float = 10.0) -> float:
    """Scalar that lifts mean |forecast| to `target` (Appendix D, p.297).

    Returns 0.0 when there is nothing to scale.
    """
    values = [abs(float(f)) for f in forecasts if math.isfinite(float(f))]
    if not values:
        return 0.0
    mean_abs = sum(values) / len(values)
    if mean_abs <= 0.0:
        return 0.0
    return target / mean_abs


def rescale_forecasts(forecasts: Sequence[float], target: float = 10.0) -> List[float]:
    """Rescale forecasts so the average absolute value is `target`."""
    scalar = forecast_scalar(forecasts, target)
    return [float(f) * scalar for f in forecasts]


def cap_forecast(forecast: float, cfg: CarverConfig = DEFAULT) -> float:
    """Clamp a combined forecast to +/- `cfg.max_forecast` (p.201)."""
    value = float(forecast)
    if not math.isfinite(value):
        return 0.0
    return max(-cfg.max_forecast, min(cfg.max_forecast, value))


def combine_forecasts(
    rule_forecasts: Mapping[str, Sequence[float]],
    *,
    weights: Optional[Mapping[str, float]] = None,
    cfg: CarverConfig = DEFAULT,
) -> List[float]:
    """Combine per-rule forecast series into one (Chapter 8).

    Equal weight by default. Explicit weights are capped at
    `cfg.max_weight_multiple` times the equal weight. Capping shrinks the
    total weight rather than renormalising it, so a capped combination
    produces a smaller forecast — the intended effect, not a bug.
    """
    names = list(rule_forecasts)
    if not names:
        return []
    equal_weight = 1.0 / len(names)
    cap = cfg.max_weight_multiple * equal_weight
    if weights is None:
        used = {name: equal_weight for name in names}
    else:
        used = {name: min(max(float(weights.get(name, 0.0)), 0.0), cap) for name in names}
    length = min(len(rule_forecasts[name]) for name in names)
    combined: List[float] = []
    for i in range(length):
        combined.append(sum(used[name] * rule_forecasts[name][i] for name in names))
    return combined


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

def price_volatility(
    returns: Sequence[float],
    lookback: int = 25,
    method: str = "sma",
    cfg: CarverConfig = DEFAULT,
) -> float:
    """Daily price volatility from fractional returns (Appendix D, p.298).

    "sma": sample standard deviation of the last `lookback` returns.
    "ewma": recursive variance with A = 2 / (1 + cfg.ewma_lookback).
    """
    values = [float(r) for r in returns if math.isfinite(float(r))]
    if method == "sma":
        window = values[-max(2, lookback):]
        if len(window) < 2:
            return 0.0
        return float(np.std(window, ddof=1))
    if method == "ewma":
        if len(values) < 2:
            return 0.0
        alpha = 2.0 / (1.0 + cfg.ewma_lookback)
        variance = values[0] ** 2
        for value in values[1:]:
            variance = value * value * alpha + (1.0 - alpha) * variance
        return float(math.sqrt(variance))
    raise ValueError(f"unknown volatility method: {method!r}")


def instrument_value_volatility(
    price_vol: float,
    block_value: float,
    fx_rate: float = 1.0,
) -> float:
    """Instrument value volatility (Table 30, p.172).

    The book computes price volatility in PERCENT x block value x FX
    (0.52 x $1500 x 0.88 = EUR 686). This takes the volatility as a
    fraction and applies the same arithmetic, so the published tables
    reproduce exactly. The unit only ever cancels inside the volatility
    scalar, which is a ratio against a cash target.
    """
    return float(price_vol) * 100.0 * float(block_value) * float(fx_rate)


def daily_cash_volatility_target(
    equity: float,
    cfg: CarverConfig = DEFAULT,
) -> float:
    """Daily cash volatility target = equity x target vol / sqrt(periods)."""
    return float(equity) * cfg.target_vol / math.sqrt(cfg.periods_per_year)


def volatility_scalar(
    daily_cash_vol_target: float,
    instrument_value_vol: float,
    cfg: CarverConfig = DEFAULT,
) -> float:
    """Volatility scalar = daily cash vol target / instrument value vol."""
    if instrument_value_vol <= cfg.min_volatility:
        return 0.0
    return float(daily_cash_vol_target) / float(instrument_value_vol)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def diversification_multiplier(
    weights: Sequence[float],
    correlation: Sequence[Sequence[float]],
) -> float:
    """1 / sqrt(W H W'), negative correlations floored at zero (App. D).

    Flooring prevents a negative correlation from inflating the multiplier
    and implying less risk than the portfolio actually carries.
    """
    w = np.asarray(weights, dtype=float)
    h = np.asarray(correlation, dtype=float)
    if w.size == 0 or h.size == 0:
        return 1.0
    if h.shape != (w.size, w.size):
        raise ValueError("correlation matrix must be square and match weights")
    h = np.where(h < 0.0, 0.0, h)
    variance = float(w @ h @ w)
    if variance <= 0.0:
        return 1.0
    return float(1.0 / math.sqrt(variance))


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

def subsystem_position(
    combined_forecast: float,
    vol_scalar: float,
    cfg: CarverConfig = DEFAULT,
) -> float:
    """Subsystem position in instrument blocks (Chapter 10)."""
    capped = cap_forecast(combined_forecast, cfg)
    return (capped / cfg.forecast_scale) * float(vol_scalar)


def portfolio_position(
    subsys_position: float,
    instrument_weight: float,
    div_multiplier: float,
) -> float:
    """Final portfolio position in instrument blocks (Chapter 11)."""
    return float(subsys_position) * float(instrument_weight) * float(div_multiplier)


@dataclass(frozen=True)
class PositionPlan:
    """Every intermediate value of the chain, for audit and testing."""

    combined_forecast: float
    capped_forecast: float
    daily_cash_vol_target: float
    instrument_value_vol: float
    volatility_scalar: float
    subsystem_position: float
    instrument_weight: float
    diversification_multiplier: float
    portfolio_position: float
    max_position: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "combined_forecast": self.combined_forecast,
            "capped_forecast": self.capped_forecast,
            "daily_cash_vol_target": self.daily_cash_vol_target,
            "instrument_value_vol": self.instrument_value_vol,
            "volatility_scalar": self.volatility_scalar,
            "subsystem_position": self.subsystem_position,
            "instrument_weight": self.instrument_weight,
            "diversification_multiplier": self.diversification_multiplier,
            "portfolio_position": self.portfolio_position,
            "max_position": self.max_position,
        }


def size_position(
    combined_forecast: float,
    equity: float,
    price_vol: float,
    block_value: float,
    *,
    fx_rate: float = 1.0,
    instrument_weight: float = 1.0,
    div_multiplier: float = 1.0,
    cfg: CarverConfig = DEFAULT,
) -> PositionPlan:
    """Run the sizing chain for one instrument and return the full plan."""
    capped = cap_forecast(combined_forecast, cfg)
    daily_target = daily_cash_volatility_target(equity, cfg)
    instrument_vol = instrument_value_volatility(price_vol, block_value, fx_rate)
    scalar = volatility_scalar(daily_target, instrument_vol, cfg)
    subsys = subsystem_position(capped, scalar, cfg)
    portfolio = portfolio_position(subsys, instrument_weight, div_multiplier)
    max_position = (
        (cfg.max_forecast / cfg.forecast_scale) * scalar
        * float(instrument_weight) * float(div_multiplier)
    )
    return PositionPlan(
        combined_forecast=float(combined_forecast),
        capped_forecast=capped,
        daily_cash_vol_target=daily_target,
        instrument_value_vol=instrument_vol,
        volatility_scalar=scalar,
        subsystem_position=subsys,
        instrument_weight=float(instrument_weight),
        diversification_multiplier=float(div_multiplier),
        portfolio_position=portfolio,
        max_position=max_position,
    )


def target_trade(
    current_position: float,
    target_position: float,
    band: float = 0.10,
) -> float:
    """Trade needed to reach the rounded target, with position inertia.

    Carver avoids churn by not trading until the target is more than `band`
    away from the current position (p.174).
    """
    current = float(current_position)
    target = float(target_position)
    if not math.isfinite(target):
        return 0.0
    if abs(target - current) <= band * max(abs(current), 1.0):
        return 0.0
    return round(target) - current
