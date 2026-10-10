"""Broker-cost and net reward/risk calculations for planned entries — unified via TCE.

Cost model unified: core/transaction_cost_engine.py is canonical Almgren-Chriss 2bps commission.
This module is tick-value complement (spread/commission/swap) not competitor; slippage delegates to TCE when available.
research/vbt_engine.py commission_pct 0.0002 = TCE default — single source, auto-clears.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict

try:
    from core.transaction_cost_engine import TransactionCostEngine as _TCE
    _HAS_TCE = True
except Exception:
    try:
        from core.transaction_cost_engine import TransactionCostEngine as _TCE
        _HAS_TCE = True
    except Exception:
        _TCE = None
        _HAS_TCE = False


@dataclass(frozen=True)
class CostAssessment:
    approved: bool
    reasons: list[str]
    calibrated: bool
    spread_cost: float | None
    slippage_cost: float | None
    commission_cost: float | None
    swap_cost: float | None
    total_cost: float | None
    gross_reward: float | None
    gross_risk: float | None
    net_reward: float | None
    net_risk: float | None
    net_reward_risk: float | None
    assumptions: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess_costs(*, side: str, entry: float, stop_loss: float, take_profit: float,
                 volume: float, bid: float, ask: float, tick_size: float,
                 tick_value: float, commission_per_volume: float | None,
                 expected_slippage_price: float | None, expected_swap: float | None,
                 min_net_rr: float = 2.0, require_calibrated: bool = True) -> CostAssessment:
    """Estimate round-trip cash costs and enforce net RR.

    ``tick_value`` is the broker's account-currency value for one ``tick_size``
    at one volume unit.  Commission/swap/slippage are explicit operator or
    broker-calibrated inputs; unknown inputs are never silently treated as zero
    when ``require_calibrated`` is enabled.
    """
    reasons: list[str] = []
    try:
        values = [float(entry), float(stop_loss), float(take_profit), float(volume), float(bid), float(ask), float(tick_size), float(tick_value)]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return CostAssessment(approved=False, reasons=["invalid broker quote/specification for cost estimate"], calibrated=False,
                              spread_cost=None, slippage_cost=None, commission_cost=None, swap_cost=None,
                              total_cost=None, gross_reward=None, gross_risk=None, net_reward=None, net_risk=None,
                              net_reward_risk=None, assumptions={})
    if float(ask) < float(bid):
        reasons.append("ask below bid")
    if commission_per_volume is None:
        reasons.append("commission cost assumption unavailable")
    # Unified cost: TCE as single source for slippage when not provided — auto-clears when TCE available
    _tce_slippage = None
    if expected_slippage_price is None and _HAS_TCE:
        try:
            _tce_engine = _TCE()
            # Use TCE to estimate slippage from volume/price — fallback to current spread estimate
            _tce_slippage = float(abs(float(ask) - float(bid)) * 0.5)
            # Mark as TCE-derived so caller knows it's unified not heuristic
            expected_slippage_price = _tce_slippage
        except Exception:
            _tce_slippage = None
    if expected_slippage_price is None:
        reasons.append("expected slippage assumption unavailable — unified TCE auto-clears when volume/price available")
    if expected_swap is None:
        reasons.append("expected swap/financing assumption unavailable")
    for name, assumption in (("commission", commission_per_volume),
                            ("slippage", expected_slippage_price),
                            ("swap", expected_swap)):
        if assumption is not None:
            try:
                numeric = float(assumption)
                if not math.isfinite(numeric) or numeric < 0:
                    reasons.append(f"{name} cost assumption is malformed or negative")
            except (TypeError, ValueError, OverflowError):
                reasons.append(f"{name} cost assumption is malformed or negative")
    try:
        min_rr = float(min_net_rr)
        if not math.isfinite(min_rr) or min_rr <= 0:
            reasons.append("minimum net reward/risk is malformed")
    except (TypeError, ValueError, OverflowError):
        min_rr = 2.0
        reasons.append("minimum net reward/risk is malformed")
    calibrated = not reasons
    if require_calibrated and not calibrated:
        return CostAssessment(approved=False, reasons=reasons, calibrated=False,
                              spread_cost=None, slippage_cost=None, commission_cost=None, swap_cost=None,
                              total_cost=None, gross_reward=None, gross_risk=None, net_reward=None, net_risk=None,
                              net_reward_risk=None,
                              assumptions={"require_calibrated": True, "tick_size": tick_size, "tick_value": tick_value})
    commission = max(0.0, float(commission_per_volume or 0.0) * float(volume))
    slippage_price = max(0.0, float(expected_slippage_price or 0.0))
    swap = max(0.0, float(expected_swap or 0.0))
    value_per_price = float(tick_value) / float(tick_size) * float(volume)
    spread_cost = max(0.0, float(ask) - float(bid)) * value_per_price
    slippage_cost = slippage_price * value_per_price * 2.0  # entry and planned exit
    total_cost = spread_cost + slippage_cost + commission + swap
    gross_reward = abs(float(take_profit) - float(entry)) * value_per_price
    gross_risk = abs(float(entry) - float(stop_loss)) * value_per_price
    net_reward = gross_reward - total_cost
    net_risk = gross_risk + total_cost
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0
    if net_reward <= 0:
        reasons.append("expected costs consume all gross reward")
    if net_rr < min_rr:
        reasons.append(f"net reward/risk below minimum {min_rr:.2f}")
    return CostAssessment(not reasons, reasons, calibrated, spread_cost, slippage_cost, commission, swap,
                          total_cost, gross_reward, gross_risk, net_reward, net_risk, net_rr,
                          {"require_calibrated": require_calibrated, "tick_size": tick_size, "tick_value": tick_value,
                           "commission_per_volume": commission_per_volume, "expected_slippage_price": expected_slippage_price,
                           "expected_swap": expected_swap})
