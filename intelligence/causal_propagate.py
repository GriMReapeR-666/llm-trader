"""Multi-hop causal path propagator with lag/half-life attenuation.

Advisory research helper: scores cause→mechanism→effect paths on approved /
regime-filtered edges. Never grants execution authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MAX_HOPS = 3
DEFAULT_HALF_LIFE = 43200.0  # 12h


def _sign_value(sign: str) -> float:
    key = str(sign or "").upper()
    if key == "POSITIVE":
        return 1.0
    if key == "NEGATIVE":
        return -1.0
    if key == "CONDITIONAL":
        return 0.5
    return 0.0


def _half_life(edge: Dict[str, Any]) -> float:
    for key in ("decay_half_life_seconds", "half_life_seconds"):
        try:
            value = float(edge.get(key))
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    notes = edge.get("notes")
    if isinstance(notes, str) and notes.strip().startswith("{"):
        try:
            payload = json.loads(notes)
            value = float(payload.get("decay_half_life_seconds") or 0)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError, json.JSONDecodeError, OverflowError):
            pass
    return DEFAULT_HALF_LIFE


def _confidence(edge: Dict[str, Any]) -> float:
    try:
        value = float(edge.get("confidence", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _edge_nodes(edge: Dict[str, Any]) -> Tuple[str, str]:
    source = str(edge.get("source_name") or edge.get("source") or "").upper()
    target = str(edge.get("target_name") or edge.get("target") or "").upper()
    return source, target


def _attenuation(age_seconds: float, half_life: float) -> float:
    if half_life <= 0:
        return 0.0
    age = max(0.0, float(age_seconds))
    return math.exp(-age / half_life)


def propagate(
    edges: Sequence[Dict[str, Any]],
    *,
    seed_entities: Iterable[str],
    symbol: str = "",
    max_hops: int = MAX_HOPS,
    age_seconds: float = 0.0,
) -> Dict[str, Any]:
    """Search ≤max_hops paths from seed entities; return best scored path."""
    seeds = [str(item).upper() for item in seed_entities if str(item or "").strip()]
    if symbol:
        sym = str(symbol).upper()
        if len(sym) >= 6:
            seeds.extend([sym[:3], sym[3:6], sym])
        seeds.append(sym)
    seeds = list(dict.fromkeys(seeds))
    if not seeds or not edges:
        return {
            "status": "NO_PATH",
            "path_id": None,
            "bias": "UNKNOWN",
            "score": 0.0,
            "horizon_seconds": None,
            "hops": [],
            "execution_authority": False,
        }

    adjacency: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source, target = _edge_nodes(edge)
        if not source or not target:
            continue
        adjacency.setdefault(source, []).append(edge)

    best: Optional[Dict[str, Any]] = None
    # BFS-style expansion of paths: list of (node, hops, sign_product, conf_product, horizon)
    queue: List[Tuple[str, List[Dict[str, Any]], float, float, float]] = [
        (seed, [], 1.0, 1.0, 0.0) for seed in seeds
    ]
    seen_paths = set()

    while queue:
        node, hops, sign_prod, conf_prod, horizon = queue.pop(0)
        if len(hops) >= max(1, int(max_hops)):
            continue
        for edge in adjacency.get(node, []):
            source, target = _edge_nodes(edge)
            if target == node:
                continue
            edge_id = edge.get("edge_id")
            hop_ids = tuple([*(h.get("edge_id") for h in hops), edge_id])
            if hop_ids in seen_paths:
                continue
            seen_paths.add(hop_ids)
            half = _half_life(edge)
            conf = _confidence(edge)
            sign = _sign_value(str(edge.get("sign")))
            if sign == 0.0 or conf <= 0:
                continue
            new_sign = sign_prod * sign
            new_conf = conf_prod * conf * _attenuation(age_seconds, half)
            new_horizon = max(horizon, half)
            hop = {
                "edge_id": edge_id,
                "source_name": source,
                "target_name": target,
                "relation": edge.get("relation"),
                "sign": edge.get("sign"),
                "confidence": conf,
                "decay_half_life_seconds": half,
            }
            new_hops = [*hops, hop]
            score = abs(new_sign) * new_conf
            candidate = {
                "status": "OK",
                "score": score,
                "signed_score": new_sign * new_conf,
                "bias": "BUY" if new_sign > 0 else "SELL" if new_sign < 0 else "NEUTRAL",
                "horizon_seconds": new_horizon,
                "hops": new_hops,
                "terminal": target,
                "execution_authority": False,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
            queue.append((target, new_hops, new_sign, new_conf, new_horizon))

    if best is None:
        return {
            "status": "NO_PATH",
            "path_id": None,
            "bias": "UNKNOWN",
            "score": 0.0,
            "horizon_seconds": None,
            "hops": [],
            "execution_authority": False,
        }

    digest = hashlib.sha256(
        json.dumps(
            {"hops": [h.get("edge_id") for h in best["hops"]], "bias": best["bias"], "symbol": symbol},
            sort_keys=True, default=str,
        ).encode("utf-8")
    ).hexdigest()
    best["path_id"] = f"cpath:{digest[:20]}"
    best["limitation"] = "Advisory path score only; never grants order permission."
    return best
