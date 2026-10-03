"""Historical regime observation batches + broker-cost replay evidence.

Research only: never grants execution authority. Observations must carry an
operator ``source_span`` citation. Replay uses ``broker_replay`` and refuses
to invent bars or event anchors.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from broker_replay import ReplayIntent, ReplaySpec, replay
except ImportError:
    from research.broker_replay import ReplayIntent, ReplaySpec, replay

try:
    from edge_lifecycle import advance_lifecycle, current_lifecycle
except ImportError:
    from research.edge_lifecycle import advance_lifecycle, current_lifecycle

try:
    from historical_regimes import get_template, ingest_cited_observation, research_status, template_registry
except ImportError:
    from research.historical_regimes import get_template, ingest_cited_observation, research_status, template_registry

try:
    from macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph


def ingest_observation_batch(
    graph: MacroGraph,
    regime_id: str,
    observations: Sequence[Dict[str, Any]],
    *,
    default_source_span: str = "",
) -> Dict[str, Any]:
    """Ingest many cited observations for one regime template."""
    template = get_template(regime_id)
    source_ids: List[str] = []
    errors: List[Dict[str, Any]] = []
    for index, raw in enumerate(observations or []):
        if not isinstance(raw, dict):
            errors.append({"index": index, "error": "observation must be an object"})
            continue
        observation = dict(raw)
        source_span = str(
            observation.get("source_span") or default_source_span or ""
        ).strip()
        source_url = str(observation.pop("source_url", "") or "") if "source_url" in observation else ""
        # Keep source_span inside observation for template required_fields.
        if source_span:
            observation["source_span"] = source_span
        published_at = str(observation.pop("published_at", "") or "")
        try:
            source_id = ingest_cited_observation(
                graph,
                regime_id,
                observation,
                source_span=source_span,
                source_url=source_url or None,
                published_at=published_at,
            )
            source_ids.append(source_id)
        except (TypeError, ValueError, KeyError) as exc:
            errors.append({"index": index, "error": str(exc)[:300]})
    status = research_status(graph)
    return {
        "regime_id": regime_id,
        "label": template.get("label"),
        "ingested": len(source_ids),
        "source_ids": source_ids,
        "errors": errors,
        "research_status": status,
        "execution_authority": False,
        "status": "INGESTED" if source_ids and not errors else (
            "PARTIAL" if source_ids else "FAILED"
        ),
    }


def list_regime_observations(graph: MacroGraph, regime_id: str = "") -> List[Dict[str, Any]]:
    """Return cited historical observations (optionally filtered by regime)."""
    import json

    with graph._con() as con:
        rows = con.execute(
            "SELECT source_id, title, source_url, publisher, published_at, payload "
            "FROM macro_sources WHERE payload LIKE '%historical_regime_observation%'"
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("kind") != "historical_regime_observation":
            continue
        if regime_id and str(payload.get("regime_id") or "") != str(regime_id):
            continue
        out.append({
            "source_id": row["source_id"],
            "title": row["title"],
            "source_url": row["source_url"],
            "publisher": row["publisher"],
            "published_at": row["published_at"],
            "regime_id": payload.get("regime_id"),
            "observation": payload.get("observation"),
            "source_span": payload.get("source_span"),
            "execution_authority": False,
        })
    return out


def _observation_timestamp(observation: Dict[str, Any]) -> Optional[str]:
    for key in ("observed_at", "event_time", "timestamp", "time"):
        value = observation.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def replay_regime_with_bars(
    *,
    regime_id: str,
    bars: Sequence[Dict[str, Any]],
    observations: Sequence[Dict[str, Any]],
    side: str = "BUY",
    stop_distance: float = 0.0030,
    reward_multiple: float = 2.0,
    volume: float = 0.1,
    spec: Optional[ReplaySpec] = None,
    edge_id: Optional[int] = None,
    graph: Optional[MacroGraph] = None,
) -> Dict[str, Any]:
    """Replay a setup against cited regime observations aligned to fixture bars.

    Bars must be supplied by the operator/research fixture — this function never
    fabricates OHLC. Missing alignment → INSUFFICIENT_EVENT_ANCHORS.
    """
    template = get_template(regime_id)
    if not bars:
        return {
            "regime_id": regime_id,
            "status": "INSUFFICIENT_BARS",
            "execution_authority": False,
        }
    replay_spec = spec or ReplaySpec(
        point=0.00001,
        contract_size=100000.0,
        commission_per_volume=7.0,
        swap_per_volume_day=2.0,
        expected_slippage_price=0.00002,
        spread_price=0.00010,
    )
    from autonomous_causal_engine import _bar_index_at_or_after, _parse_time

    intents: List[ReplayIntent] = []
    event_indices: List[int] = []
    alignment = {"observations": 0, "aligned": 0, "missing_time": 0, "unmatched": 0}
    direction = 1 if str(side).upper() == "BUY" else -1
    bar_list = list(bars)

    for index, item in enumerate(observations or []):
        alignment["observations"] += 1
        obs = item.get("observation") if isinstance(item, dict) and "observation" in item else item
        if not isinstance(obs, dict):
            alignment["missing_time"] += 1
            continue
        stamp = _observation_timestamp(obs)
        when = _parse_time(stamp)
        if when is None:
            alignment["missing_time"] += 1
            continue
        bar_index = _bar_index_at_or_after(bar_list, when)
        if bar_index is None or bar_index >= len(bar_list) - 2:
            alignment["unmatched"] += 1
            continue
        entry = float(bar_list[bar_index].get("close") or 0.0)
        if entry <= 0:
            alignment["unmatched"] += 1
            continue
        intents.append(ReplayIntent(
            ticket=f"regime_{regime_id}_{index}_{bar_index}",
            side=str(side).upper(),
            entry=entry,
            stop_loss=entry - direction * stop_distance,
            take_profit=entry + direction * reward_multiple * stop_distance,
            volume=volume,
            submit_index=bar_index,
        ))
        event_indices.append(bar_index)
        alignment["aligned"] += 1

    if not intents:
        return {
            "regime_id": regime_id,
            "label": template.get("label"),
            "status": "INSUFFICIENT_EVENT_ANCHORS",
            "alignment": alignment,
            "execution_authority": False,
            "reason": "no cited observations aligned to provided bars",
        }

    result = replay(intents, bar_list, replay_spec, event_indices=event_indices)
    lifecycle = None
    if graph is not None and edge_id is not None:
        # Promote only when replay produced closed trades; still research-only.
        closed = int(result.get("closed_trades", 0) or 0)
        target = "REPLAY_PASS" if closed > 0 else "REPLAY_FAIL"
        # Ensure edge is at least STAT_PASS before REPLAY_* when possible.
        state = current_lifecycle(graph, int(edge_id))
        if state.get("lifecycle") == "STAT_PASS" or target == "REPLAY_FAIL":
            lifecycle = advance_lifecycle(
                graph, int(edge_id), target,
                evidence={
                    "regime_id": regime_id,
                    "closed_trades": closed,
                    "net_r": result.get("net_r"),
                    "alignment": alignment,
                },
            )
        else:
            lifecycle = {
                "advanced": False,
                "reason": f"edge lifecycle {state.get('lifecycle')} not STAT_PASS; refuse REPLAY promotion",
                "execution_authority": False,
            }

    return {
        "regime_id": regime_id,
        "label": template.get("label"),
        "status": "REPLAY_COMPLETE",
        "alignment": alignment,
        "replay": result,
        "lifecycle": lifecycle,
        "templates_available": len(template_registry()),
        "execution_authority": False,
    }


def regime_research_package(graph: MacroGraph, regime_id: str) -> Dict[str, Any]:
    """Bundle template + cited observations for operator review."""
    template = get_template(regime_id)
    observations = list_regime_observations(graph, regime_id)
    return {
        "template": template,
        "observations": observations,
        "observation_count": len(observations),
        "research_status": research_status(graph),
        "execution_authority": False,
        "status": "READY_FOR_REPLAY" if observations else "NEEDS_CITED_OBSERVATIONS",
    }
