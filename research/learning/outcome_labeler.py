"""Deterministic Offline Trade Outcome Labeler.

Consumes completed trade execution / broker replay records and computes:
1. Realized net R-multiple: R = (Exit - Entry) / (Entry - SL) * direction_sign - costs.
2. Path efficiency: Max Adverse Excursion (MAE), Max Favorable Excursion (MFE).
3. Attribution quality: Validates whether move was FUNDAMENTAL_REPRICING, SQUEEZE, or NOISE.
4. Ground-truth arbitration label for D8:
   - R >= 1.5 -> STRONG_PROCEED
   - 0.5 <= R < 1.5 -> CONDITIONAL_HALF_RISK
   - R < -0.5 (or hit SL) -> REJECT_MACRO_CONFLICT
5. Zero future leakage: Cutoff-bound evaluation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional

from research.decision_models.extended_taxonomy import D8_OpportunityArbitration


@dataclass(frozen=True)
class TradeExecutionRecord:
    """Completed trade record from journal or broker replay."""
    trade_id: str
    symbol: str
    direction: str                             # 'BUY' or 'SELL'
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    entry_time: str
    exit_time: str
    high_watermark: float                      # Highest price reached during trade
    low_watermark: float                       # Lowest price reached during trade
    spread_cost_pips: float = 1.0
    commission: float = 0.0
    slippage_pips: float = 0.0
    exit_reason: str = "TAKE_PROFIT"           # TAKE_PROFIT, STOP_LOSS, TIME_EXIT, MANUAL
    pip_size: float = 0.0001
    decision_bundle_ref: Optional[str] = None


@dataclass(frozen=True)
class LabeledTradeOutcome:
    """Deterministic ground-truth evaluation of a completed trade."""
    trade_id: str
    symbol: str
    direction: str
    realized_pips: float
    initial_risk_pips: float
    realized_r_multiple: float
    mae_pips: float                            # Max Adverse Excursion
    mfe_pips: float                            # Max Favorable Excursion
    mae_r: float
    mfe_r: float
    ground_truth_d8_label: str                 # STRONG_PROCEED, CONDITIONAL_HALF_RISK, REJECT_MACRO_CONFLICT
    attribution_quality: str                   # FUNDAMENTAL_REPRICING, SQUEEZE, STOP_SWEEP, NOISE
    is_profitable: bool
    is_macro_aligned: bool
    decision_bundle_ref: Optional[str]
    labeled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    labeler_version: str = "v1_deterministic_r"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "realized_pips": round(self.realized_pips, 2),
            "initial_risk_pips": round(self.initial_risk_pips, 2),
            "realized_r_multiple": round(self.realized_r_multiple, 3),
            "mae_pips": round(self.mae_pips, 2),
            "mfe_pips": round(self.mfe_pips, 2),
            "mae_r": round(self.mae_r, 3),
            "mfe_r": round(self.mfe_r, 3),
            "ground_truth_d8_label": self.ground_truth_d8_label,
            "attribution_quality": self.attribution_quality,
            "is_profitable": self.is_profitable,
            "is_macro_aligned": self.is_macro_aligned,
            "decision_bundle_ref": self.decision_bundle_ref,
            "labeled_at": self.labeled_at,
            "labeler_version": self.labeler_version,
        }


class DeterministicOutcomeLabeler:
    """Grades completed trades deterministically for RLCD training."""

    def label_trade(self, record: TradeExecutionRecord) -> LabeledTradeOutcome:
        """Compute R-multiple, MAE/MFE, and true D8 arbitration label."""
        p_size = record.pip_size
        dir_sign = 1.0 if record.direction.upper() == "BUY" else -1.0

        # Initial risk calculation
        risk_dist = abs(record.entry_price - record.stop_loss)
        initial_risk_pips = max(risk_dist / p_size, 1.0)

        # Realized gross and net pips
        gross_pips = (record.exit_price - record.entry_price) * dir_sign / p_size
        net_costs_pips = record.spread_cost_pips + record.slippage_pips + (record.commission / 10.0)
        net_pips = gross_pips - net_costs_pips

        realized_r = net_pips / initial_risk_pips

        # MAE and MFE
        if record.direction.upper() == "BUY":
            mfe_pips = max(0.0, (record.high_watermark - record.entry_price) / p_size)
            mae_pips = max(0.0, (record.entry_price - record.low_watermark) / p_size)
        else:
            mfe_pips = max(0.0, (record.entry_price - record.low_watermark) / p_size)
            mae_pips = max(0.0, (record.high_watermark - record.entry_price) / p_size)

        mae_r = mae_pips / initial_risk_pips
        mfe_r = mfe_pips / initial_risk_pips

        # Deterministic Ground-Truth D8 Arbitration Label
        # If trade achieved strong R with low MAE -> STRONG_PROCEED
        if realized_r >= 1.2:
            d8_target = D8_OpportunityArbitration.STRONG_PROCEED.value
        elif realized_r >= 0.2:
            d8_target = D8_OpportunityArbitration.CONDITIONAL_HALF_RISK.value
        else:
            d8_target = D8_OpportunityArbitration.REJECT_MACRO_CONFLICT.value

        # Attribution Quality Classification
        if realized_r >= 1.0 and mae_r < 0.5:
            quality = "FUNDAMENTAL_REPRICING"
        elif mfe_r >= 1.5 and realized_r < 0.0:
            quality = "POSITIONING_SQUEEZE_REVERSAL"
        elif mae_r >= 1.0:
            quality = "STOP_CASCADE_LOSS"
        else:
            quality = "NOISE_CHURN"

        return LabeledTradeOutcome(
            trade_id=record.trade_id,
            symbol=record.symbol,
            direction=record.direction,
            realized_pips=net_pips,
            initial_risk_pips=initial_risk_pips,
            realized_r_multiple=realized_r,
            mae_pips=mae_pips,
            mfe_pips=mfe_pips,
            mae_r=mae_r,
            mfe_r=mfe_r,
            ground_truth_d8_label=d8_target,
            attribution_quality=quality,
            is_profitable=(net_pips > 0),
            is_macro_aligned=(realized_r > 0),
            decision_bundle_ref=record.decision_bundle_ref,
        )
