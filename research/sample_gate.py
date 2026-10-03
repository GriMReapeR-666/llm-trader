"""Per-setup labelled-sample qualification for research promotion.

The total journal count is not enough: every strategy/setup class needs a
large, point-in-time, broker-reconciled sample spanning more than one regime.
This module reports that requirement explicitly and never changes a setup or
grants execution authority.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

try:
    from research_validation import labelled_dataset
except ImportError:
    from research.research_validation import labelled_dataset


def _regime(row: Dict[str, Any]) -> str:
    features = row.get("features") or {}
    values = features.get("feature_values") or {}
    for candidate in (features.get("regime"), features.get("regime_label"),
                      values.get("regime"), values.get("regime_label")):
        if candidate not in (None, "", "UNKNOWN"):
            return str(candidate)
    return "UNKNOWN"


def _date(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        return parsed.date().isoformat()
    except (TypeError, ValueError):
        return "UNKNOWN"


def evaluate(rows: Iterable[Dict[str, Any]], *, minimum_samples: int = 100,
             minimum_regimes: int = 2) -> Dict[str, Any]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("strategy") or "UNKNOWN"), str(row.get("symbol") or "UNKNOWN"),
               str(row.get("setup_id") or "UNKNOWN"))
        groups[key].append(row)
    qualifications: List[Dict[str, Any]] = []
    for (strategy, symbol, setup_id), group in sorted(groups.items()):
        regimes = sorted({_regime(row) for row in group})
        dates = sorted({_date(row.get("closed_at", row.get("opened_at"))) for row in group})
        unknown_regime = "UNKNOWN" in regimes
        reasons: List[str] = []
        if len(group) < minimum_samples:
            reasons.append(f"need {minimum_samples} closed labelled rows, have {len(group)}")
        if len(regimes) < minimum_regimes:
            reasons.append(f"need {minimum_regimes} distinct regimes, have {len(regimes)}")
        if unknown_regime:
            reasons.append("regime provenance is unknown for at least one row")
        qualifications.append({
            "strategy": strategy, "symbol": symbol, "setup_id": setup_id,
            "sample_size": len(group), "regimes": regimes,
            "regime_count": len(regimes), "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None, "eligible": not reasons,
            "reasons": reasons,
        })
    eligible = [item for item in qualifications if item["eligible"]]
    return {
        "status": "READY_FOR_RESEARCH" if qualifications and len(eligible) == len(qualifications) else "INSUFFICIENT_DATA",
        "minimum_samples": minimum_samples, "minimum_regimes": minimum_regimes,
        "setup_classes": qualifications, "eligible_setup_classes": eligible,
        "sample_size": sum(item["sample_size"] for item in qualifications),
        "execution_authority": False,
        "reason": "Every setup class must meet both sample and regime gates before learned promotion.",
    }


def report(path: str = "trading_state.db", *, minimum_samples: int = 100,
           minimum_regimes: int = 2) -> Dict[str, Any]:
    dataset = labelled_dataset(path)
    result = evaluate(dataset.get("rows", []), minimum_samples=minimum_samples,
                      minimum_regimes=minimum_regimes)
    result["dataset"] = {
        "sample_size": dataset.get("sample_size", 0),
        "excluded": dataset.get("excluded", {}),
        "point_in_time": dataset.get("point_in_time", False),
        "cost_model": dataset.get("cost_model"),
    }
    if not dataset.get("point_in_time", False):
        result["status"] = "INSUFFICIENT_DATA"
        result["reason"] = "point-in-time dataset gate failed"
    return result
