"""Multi-family strategy accuracy scorecard from the authoritative journal.

Descriptive / research only — never grants execution authority.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional


def _safe_mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _calibration(pairs: List[tuple[float, float]], bins: int = 5) -> Dict[str, Any]:
    """Reliability of predicted confidence vs realized win (1 if r>0 else 0)."""
    if len(pairs) < bins:
        return {"status": "INSUFFICIENT_DATA", "bins": []}
    pairs = sorted(pairs, key=lambda item: item[0])
    size = max(1, len(pairs) // bins)
    out = []
    for index in range(bins):
        chunk = pairs[index * size: (index + 1) * size] if index < bins - 1 else pairs[index * size:]
        if not chunk:
            continue
        pred = _safe_mean([p for p, _ in chunk])
        realized = _safe_mean([r for _, r in chunk])
        out.append({"predicted": pred, "realized": realized, "n": len(chunk)})
    return {"status": "OK", "bins": out}


def build_strategy_accuracy_scorecard(path: str = "trading_state.db") -> Dict[str, Any]:
    try:
        from research.research_validation import labelled_dataset
    except ImportError:
        from research.research_validation import labelled_dataset

    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("strategy") or ""),
            str(row.get("setup_id") or ""),
            str(row.get("symbol") or ""),
            str(row.get("side") or ""),
        )
        groups[key].append(row)

    scorecards = []
    for key, group in sorted(groups.items()):
        strategy, setup_id, symbol, side = key
        rs = [float(item["r_multiple"]) for item in group]
        wins = [r for r in rs if r > 0]
        directional_acc = len(wins) / len(rs) if rs else None
        expectancy_r = _safe_mean(rs)
        # Confidence calibration when proposal confidence was persisted in features.
        pairs = []
        for item in group:
            features = item.get("features") or {}
            conf = features.get("confidence")
            if conf is None:
                continue
            try:
                pairs.append((float(conf), 1.0 if float(item["r_multiple"]) > 0 else 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
        net_edge = expectancy_r  # costs already in broker-reconciled R when available
        scorecards.append({
            "strategy": strategy,
            "setup_id": setup_id,
            "symbol": symbol,
            "side": side,
            "sample_size": len(group),
            "directional_accuracy": directional_acc,
            "expectancy_r": expectancy_r,
            "net_edge_r": net_edge,
            "calibration": _calibration(pairs),
            "point_in_time": True,
            "execution_authority": False,
        })

    return {
        "kind": "strategy_accuracy_scorecard",
        "sample_size": len(rows),
        "groups": len(scorecards),
        "scorecards": scorecards,
        "excluded": dataset.get("excluded", {}),
        "execution_authority": False,
        "limitation": "Scorecard is descriptive journal evidence; it cannot approve live trading.",
    }
