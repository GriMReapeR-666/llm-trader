"""PortfolioAgent persona: deterministic size caps from policy + correlation.

Pure functions over injected state (positions, correlations, autonomy level) —
no journal writes, no orders. RiskController consults cap_size() between
validate and execute; a hang here can never hide inside Risk's span because
the executor runs under PortfolioAgent's own lease.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

LEVEL_CAP: Dict[str, float] = {
    "L0_SHADOW": 0.0,
    "L1_PAPER": 1.0,
    "L2_MICRO": 0.25,
    "L3_FULL": 1.0,
}

RHO_OVERLAP_LIMIT = 0.40   # max combined notional share among |rho|>0.7 pairs
HEAT_HEADROOM_FLOOR = 0.10  # always keep this fraction of heat budget free


def level_cap(level_key: str) -> float:
    return LEVEL_CAP.get(level_key, 0.0)


def _open_exposure(positions: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    per_symbol: Dict[str, float] = {}
    for p in positions or []:
        sym = str(p.get("symbol", ""))
        notional = abs(float(p.get("notional", p.get("volume", 0)) or 0))
        per_symbol[sym] = per_symbol.get(sym, 0.0) + notional
    return per_symbol


def correlation_headroom(symbol: str, proposed: float,
                         positions: List[Dict[str, Any]],
                         correlations: Dict[Tuple[str, str], float]) -> Tuple[float, str]:
    """Shrink the proposal so correlated exposure stays within the overlap limit."""
    total_book = sum(abs(float(p.get("notional", p.get("volume", 0)) or 0))
                     for p in positions or [])
    if total_book <= 0:
        return proposed, ""
    correlated_notional = 0.0
    for p in positions or []:
        rho = correlations.get((symbol, p.get("symbol")),
                               correlations.get((p.get("symbol"), symbol)))
        if rho is not None and abs(float(rho)) > 0.7:
            correlated_notional += abs(float(p.get("notional", p.get("volume", 0)) or 0))
    projected = correlated_notional + proposed
    if projected <= RHO_OVERLAP_LIMIT * (total_book + proposed):
        return proposed, ""
    allowed = max(0.0, RHO_OVERLAP_LIMIT * (total_book + proposed) - correlated_notional)
    return min(proposed, allowed), (
        f"correlation overlap cap: {symbol} vs book "
        f"(corr_notional={correlated_notional:.2f})"
    )


def cap_size(request: Dict[str, Any]) -> Dict[str, Any]:
    """request: {level, volume_risk_pct, symbol, positions, correlations,
                 heat_used_r, heat_cap_r}
    Returns the capped risk pct plus an audit trail of applied constraints."""
    level = str(request.get("level", "L1_PAPER"))
    requested = float(request.get("volume_risk_pct", 0.0))
    trail: List[str] = []

    cap = level_cap(level)
    sized = min(requested, cap)
    if cap < requested:
        trail.append(f"autonomy level {level} cap {cap}%")

    heat_cap = float(request.get("heat_cap_r", 6.0))
    heat_used = float(request.get("heat_used_r", 0.0))
    headroom = max(0.0, (heat_cap * (1 - HEAT_HEADROOM_FLOOR)) - heat_used)
    heat_share = headroom * (100.0 / max(heat_cap, 1e-9)) * 0.25  # ≤25% of free heat
    if heat_share < sized:
        sized = max(0.0, heat_share)
        trail.append(f"heat headroom {headroom:.2f}R")

    corr_capped, why = correlation_headroom(
        str(request.get("symbol", "")), sized,
        request.get("positions") or [],
        {tuple(k): v for k, v in (request.get("correlations") or {}).items()},
    )
    if corr_capped < sized:
        sized = corr_capped
        trail.append(why)

    return {
        "requested_pct": round(requested, 4),
        "capped_pct": round(sized, 4),
        "constraints": trail,
        "authority": "PortfolioAgent",
        "order_authority": False,
    }


def handle_rebalance(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Executor entry for schedule 'alloc_rebalance' — audit only, advisory."""
    from operations.event_bridge import emit

    audit = {
        "symbols": sorted({str(p.get("symbol")) for p in payload.get("positions") or []}),
        "heat_used_r": payload.get("heat_used_r"),
        "level": payload.get("level"),
    }
    emit("PORTFOLIO_AUDIT", audit, actor="PortfolioAgent")
    return audit
