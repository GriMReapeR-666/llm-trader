"""ResearchAgent persona: candidate sprints and L3 rollout proposals.

Executes strategy-factory sweeps over injected metric providers. Stage changes
remain impossible from here — promotions only ever become PENDING approvals.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


def sweep(factory: Any, metric_provider: Callable[[str], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Evaluate every registry strategy through the factory gates.

    metric_provider(strategy_id) -> metrics dict | None (no new evidence).
    Quarantines/promotion requests are decided inside the factory.
    """
    evaluated = 0
    transitions: List[Dict[str, Any]] = []
    for record in factory.all():
        metrics = metric_provider(record["id"])
        if not metrics:
            continue
        result = factory.evaluate(record["id"], metrics)
        evaluated += 1
        if result.get("transition") or result.get("gate_progress") == 1.0:
            transitions.append({"id": record["id"], **{k: result[k] for k in
                                                       ("stage", "gate_progress", "transition")
                                                       if k in result}})
    return {"evaluated": evaluated, "transitions": transitions}


def request_rollout(book: str, manifest_id: str) -> Dict[str, Any]:
    from operations.strategy_factory import get_factory

    approval = get_factory().request_l3_rollout(book, manifest_id)
    logger.info("L3 rollout approval requested: %s", approval.get("id"))
    return approval


def _fetch_real_ohlcv(context: Any = None, symbol: str = "EURUSD", bars: int = 300) -> Any:
    """Real OHLCV for alpha mining, or None when the feed cannot supply it.

    There is deliberately NO synthetic or random-walk fallback here. This frame
    is the sole input to AlphaMiningPipeline, which registers whatever it
    discovers into StrategyFactory. Mining a fabricated series manufactures
    candidates carrying real-looking IC scores that no market ever produced --
    and the old fallback reached that path precisely when the feed was thin or
    degraded, which is exactly when invented candidates are least welcome.
    A missing feed must read as a missing feed. Call
    ``KronosMT5Adapter.generate_synthetic_mt5_rates`` directly if a simulation
    genuinely needs synthetic bars; never as an implicit production default.
    """
    import pandas as pd

    provider = getattr(context, "data_provider", None) if context is not None else None
    if provider is None or not hasattr(provider, "fetch_data"):
        logger.warning("Alpha mining skipped: no real data provider on context")
        return None
    try:
        df = provider.fetch_data(symbol, count=bars)
    except Exception as exc:
        logger.warning("Alpha mining data fetch failed for %s: %s", symbol, exc)
        return None
    if not isinstance(df, pd.DataFrame) or len(df) < 50:
        rows = len(df) if isinstance(df, pd.DataFrame) else 0
        logger.warning(
            "Alpha mining skipped: %s returned %d usable rows (need 50)", symbol, rows)
        return None
    return df


def handle_alpha_mining_sprint(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Executor entry for schedule 'alpha_mining_sprint'.

    Mines alpha expressions using Hierarchical GA + PCA Grid QD selection
    and registers discovered candidates into StrategyFactory.
    """
    import pandas as pd
    from operations.event_bridge import emit
    from operations.strategy_factory import get_factory
    from research.alpha_miner import AlphaMiningConfig, AlphaMiningPipeline
    from research.alpha_miner.ga_engine import GAConfig

    factory = get_factory()
    df = (payload or {}).get("ohlcv")
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 50:
        df = _fetch_real_ohlcv(context)
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 50:
        # Fail closed. Mining without real bars would register fabricated
        # candidates into the factory; reporting the gap keeps the absence
        # visible to the operator instead of silently producing noise.
        summary = {
            "mined": False,
            "reason": "no real market data available; refused to mine synthetic prices",
            "discovered": 0,
            "significant": 0,
            "best_ic": None,
            "avg_ic": None,
            "execution_authority": False,
        }
        emit("ALPHA_MINING_SPRINT", summary, actor="ResearchAgent")
        return summary

    config = AlphaMiningConfig(
        ga_config=GAConfig(
            population_size=(payload or {}).get("population_size", 40),
            generations=(payload or {}).get("generations", 2),
            max_depth=3,
        ),
        use_llm_agents=False,
        use_failure_repair=True,
        use_rag_specification=False,
        use_exploration_grid=True,
        strategy_factory=factory,
    )

    pipeline = AlphaMiningPipeline(config=config)
    result = pipeline.run(df, verbose=False)

    summary = {
        "mined": True,
        "discovered": len(result.discovered_alphas),
        "significant": result.significant_alphas_count,
        "best_ic": round(float(result.best_ic), 4),
        "avg_ic": round(float(result.avg_ic), 4),
    }
    emit("ALPHA_MINING_SPRINT", summary, actor="ResearchAgent")
    return summary


def _evidence_only_provider(
        metrics: Dict[str, Any]) -> Callable[[str], Optional[Dict[str, Any]]]:
    """Build a provider that only yields freshly supplied evidence.

    It must never fall back to a record's own stored metrics. Doing so lets the
    factory re-evaluate its own prior claims indefinitely, refreshing
    ``last_eval_ts`` and re-deriving ``gate_progress`` from numbers that no
    backtest or journal ever produced.
    """

    def provider(sid: str) -> Optional[Dict[str, Any]]:
        value = metrics.get(sid)
        return value if isinstance(value, dict) else None

    return provider


def handle_sprint(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Executor entry for schedule 'candidate_sprint'."""
    import json
    import os
    from operations.event_bridge import emit
    from operations.strategy_factory import ROOT, get_factory

    factory = get_factory()
    metrics = (payload or {}).get("metrics") or {}
    if not metrics:
        m_path = os.path.join(ROOT, "artifacts", "strategy_metrics.json")
        try:
            if os.path.exists(m_path):
                with open(m_path, "r", encoding="utf-8") as fh:
                    metrics = json.load(fh)
        except Exception:
            pass

    result = sweep(factory, _evidence_only_provider(metrics))

    # Evaluate passing transitions with GateChainingOrchestrator
    try:
        from research.gate_chaining_orchestrator import GateChainingOrchestrator
        orch = GateChainingOrchestrator()
        for t in result["transitions"]:
            rec = factory.get(t["id"])
            if rec:
                verdict = orch.run_pipeline(t["id"], rec)
                t["gate_recommendation"] = verdict.recommendation
                t["quality_score"] = verdict.quality_score
    except Exception as exc:
        logger.debug("Gate chaining in sprint skipped: %s", exc)

    emit("RESEARCH_SPRINT", {"evaluated": result["evaluated"],
                             "transitions": len(result["transitions"])},
         actor="ResearchAgent")
    return result
