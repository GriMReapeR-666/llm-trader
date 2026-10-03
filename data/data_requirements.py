"""Declared, fail-closed market-data dependencies for registered setups."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass(frozen=True)
class SetupDataRequirements:
    calendar: bool = True
    dom: bool = False
    cross_market: bool = False


REQUIREMENTS: Dict[str, SetupDataRequirements] = {
    "trend_pullback_v1": SetupDataRequirements(calendar=True),
    "range_reversal_v1": SetupDataRequirements(calendar=True),
    "liquidity_sweep_reversal_v1": SetupDataRequirements(calendar=True, dom=True),
    "asian_range_breakout_v1": SetupDataRequirements(calendar=True),
    "macro_continuation_v1": SetupDataRequirements(calendar=True, cross_market=True),
}


def evaluate(setup_id: str, snapshot: Dict[str, Any], *, calendar_available: bool) -> Dict[str, Any]:
    """Return machine-readable availability; no empty input is interpreted safe."""
    reasons: List[str] = []
    if not isinstance(setup_id, str) or not setup_id.strip():
        setup_id = "unclassified"
        reasons.append("setup identifier is missing or malformed")
    requirements = REQUIREMENTS.get(setup_id, SetupDataRequirements(calendar=True))
    if not isinstance(snapshot, dict):
        snapshot = {}
        reasons.append("market-data snapshot is missing or malformed")
    if not isinstance(calendar_available, bool):
        calendar_available = False
        reasons.append("calendar availability flag is malformed")
    dom = snapshot.get("dom") or {}
    if not isinstance(dom, dict):
        dom = {}
        if requirements.dom:
            reasons.append("DOM payload is malformed for DOM-required setup")
    if requirements.calendar and not calendar_available:
        reasons.append("calendar/news source unavailable for this setup")
    if requirements.dom:
        available = dom.get("available", False)
        depth = dom.get("dom_depth", 0)
        valid_depth = isinstance(depth, int) and not isinstance(depth, bool) and depth > 0
        if available is not True or not valid_depth:
            reasons.append("DOM unavailable for DOM-required setup")
        if not isinstance(dom.get("captured_at"), str) or not dom.get("captured_at", "").strip():
            reasons.append("DOM capture timestamp missing for DOM-required setup")
    if requirements.cross_market:
        macro = snapshot.get("macro_data") or {}
        if not isinstance(macro, dict) or not macro:
            reasons.append("cross-market/macro data unavailable for macro-required setup")
    return {"setup_id": setup_id, "requirements": asdict(requirements), "available": not reasons, "reasons": reasons}
