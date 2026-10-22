"""The single signal-to-position route: absolute size first, multipliers last.

Carver (2015) derives a position from the forecast and the instrument's
volatility. This repository grew six multiplier paths on top of a
risk-per-trade base (see `docs/CARVER_FRAMEWORK_MAP.md`). This module is
the consolidation target: one order of operations, one cap, one place to
look.

    forecast
      -> absolute size (Carver chain, `core/forecast_sizing.py`)
      -> post-size adjustments (alpha confidence, regime) — bounded, and
         applied AFTER the absolute size is known
      -> risk amount
      -> capped at the risk limit

The multipliers are demoted to what they actually are: confidence and regime
adjustments that can only scale a size that has already been derived from the
forecast. They are clamped, and they are applied before the cap, so no
combination of them can raise the risk limit.

This module computes sizes. It places no order. The deterministic validator
(`core/trading_safety.py`) and the single order door remain the only routes
to a venue, and the cap here mirrors their default rather than replacing it.

ponytail: the live route still sizes by risk-per-trade; this is the
consolidation target, not the live path — ceiling: switch the live route
over; upgrade path: a contract amendment (docs/PROJECT_SUCCESS_CONTRACT.md)
plus the measured comparison in tests/test_sizing_spine.py.

References:
    Carver, R. (2015). "Systematic Trading." Harriman House, Ch. 9-11.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from core import forecast_sizing as fs

MIN_MULTIPLIER = 0.1
MAX_MULTIPLIER = 2.0
"""Bounds on a post-size adjustment, matching `core/alpha_sizing.py`."""

DEFAULT_RISK_CAP = 0.01
"""Risk limit per trade as a fraction of equity — the same default as
`core/risk_manager.py`, and a limit the caller cannot raise here."""


@dataclass(frozen=True)
class SizingInputs:
    """Everything the route needs. Multipliers are bounded on use."""

    combined_forecast: float
    equity: float
    price_vol: float
    block_value: float
    risk_per_block: float = 1.0
    """Currency at risk per instrument block (stop distance x contract value)."""

    fx_rate: float = 1.0
    instrument_weight: float = 1.0
    div_multiplier: float = 1.0
    confidence_multiplier: float = 1.0
    regime_multiplier: float = 1.0
    risk_cap: float = DEFAULT_RISK_CAP


@dataclass(frozen=True)
class SizingResult:
    """The full order of operations, kept inspectable."""

    carver_position: float
    """Absolute position in instrument blocks, before adjustments."""

    adjusted_position: float
    """Position after the bounded confidence and regime multipliers."""

    risk_amount: float
    """Currency at risk before the cap."""

    capped_risk_amount: float
    """Currency at risk after the cap — the number a caller may use."""

    final_position: float
    """Instrument blocks that the capped risk amount supports."""

    risk_limit: float
    """The cap that was applied (equity x risk_cap)."""

    def to_dict(self) -> Dict[str, float]:
        return {
            "carver_position": self.carver_position,
            "adjusted_position": self.adjusted_position,
            "risk_amount": self.risk_amount,
            "capped_risk_amount": self.capped_risk_amount,
            "final_position": self.final_position,
            "risk_limit": self.risk_limit,
        }


def _clamp(multiplier: float) -> float:
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, float(multiplier)))


def size_position(
    inputs: SizingInputs,
    cfg: fs.CarverConfig = fs.DEFAULT,
) -> SizingResult:
    """Run the route: absolute size, then bounded adjustments, then the cap."""
    plan = fs.size_position(
        combined_forecast=inputs.combined_forecast,
        equity=inputs.equity,
        price_vol=inputs.price_vol,
        block_value=inputs.block_value,
        fx_rate=inputs.fx_rate,
        instrument_weight=inputs.instrument_weight,
        div_multiplier=inputs.div_multiplier,
        cfg=cfg,
    )

    adjustment = _clamp(inputs.confidence_multiplier) * _clamp(inputs.regime_multiplier)
    adjusted = plan.portfolio_position * adjustment

    risk_amount = abs(adjusted) * abs(inputs.risk_per_block)
    risk_limit = abs(inputs.equity) * max(0.0, float(inputs.risk_cap))
    capped = min(risk_amount, risk_limit)

    per_block = abs(inputs.risk_per_block)
    final_position = (capped / per_block) if per_block > 0 else 0.0
    if adjusted < 0:
        final_position = -final_position

    return SizingResult(
        carver_position=plan.portfolio_position,
        adjusted_position=adjusted,
        risk_amount=risk_amount,
        capped_risk_amount=capped,
        final_position=final_position,
        risk_limit=risk_limit,
    )


def legacy_risk_amount(equity: float, risk_per_trade: float = DEFAULT_RISK_CAP) -> float:
    """The rule the live path still uses: a flat fraction of equity.

    Kept so the migration can be measured rather than argued about.
    """
    return abs(float(equity)) * float(risk_per_trade)
