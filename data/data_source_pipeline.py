"""Process all news/macro market data sources into observations + causal graph.

Doctrine:
- Broker/calendar releases alone may schedule event-reaction measurement and
  drive event blackout (caller retains that gate).
- FRED / NewsAPI / OpenBB / Seeking Alpha are ingested as research evidence and
  candidate macro_sources so causal retrieval and catalog measurement can use them.
- Never grants execution_authority.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from intelligence.economic_dynamics_catalog import FREE_DATA_BINDINGS
from intelligence.macro_observations import ingest_calendar_events, ingest_fred_snapshot
from data.news_triage import TriageGate, project_article


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalog_fred_series() -> List[str]:
    """Union of FRED series from FREE_DATA_BINDINGS plus common aliases."""
    series: List[str] = []
    for binding in FREE_DATA_BINDINGS:
        if str(binding.get("provider") or "").upper() != "FRED":
            continue
        for item in binding.get("series") or []:
            key = str(item or "").strip().upper()
            if key and key not in series:
                series.append(key)
    for alias in ("GDP", "CPIAUCSL", "UNRATE", "DFF", "VIXCLS", "FEDFUNDS", "DGS10", "T10Y2Y"):
        if alias not in series:
            series.append(alias)
    return series


def _series_entity_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for binding in FREE_DATA_BINDINGS:
        if str(binding.get("provider") or "").upper() != "FRED":
            continue
        entity = str(binding.get("entity") or "")
        for series in binding.get("series") or []:
            mapping[str(series).upper()] = entity
    mapping.setdefault("GDP", "GROWTH")
    mapping.setdefault("DFF", "POLICY_RATE")
    mapping.setdefault("FEDFUNDS", "POLICY_RATE")
    return mapping


def _safe_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def ingest_fred_into_graph(
    graph: Any,
    macro_data: Dict[str, Any],
    *,
    observed_at: str = "",
) -> Dict[str, Any]:
    """Persist each FRED print as a candidate macro_source bound to a catalog entity."""
    if graph is None or not isinstance(macro_data, dict) or not macro_data:
        return {
            "status": "SKIPPED",
            "ingested": 0,
            "entities": [],
            "source_ids": [],
            "execution_authority": False,
        }
    mapping = _series_entity_map()
    stamp = observed_at or utcnow()
    source_ids: List[str] = []
    entities: List[str] = []
    for series, payload in macro_data.items():
        series_key = str(series).upper()
        entity = mapping.get(series_key, series_key)
        row = payload if isinstance(payload, dict) else {"value": payload}
        try:
            source_id = graph.ingest_source(
                title=f"FRED:{series_key}",
                payload={
                    "kind": "fred_observation",
                    "series": series_key,
                    "entity": entity,
                    "value": row.get("value"),
                    "previous": row.get("previous"),
                    "surprise": (
                        (float(row["value"]) - float(row["previous"]))
                        if row.get("value") is not None and row.get("previous") is not None
                        else None
                    ),
                    "observed_at": row.get("date") or row.get("observed_at") or stamp,
                    "execution_authority": False,
                },
                source_url=f"https://fred.stlouisfed.org/series/{series_key}",
                publisher="FRED",
                published_at=str(row.get("date") or stamp),
                rights_note="FRED observation; research evidence only; not a calendar release blackout trigger",
            )
            source_ids.append(str(source_id))
            if entity and entity not in entities:
                entities.append(entity)
        except Exception:
            continue
    return {
        "status": "INGESTED" if source_ids else "SKIPPED",
        "ingested": len(source_ids),
        "entities": entities,
        "source_ids": source_ids[:40],
        "execution_authority": False,
    }


def _normalize_narrative_row(article: Dict[str, Any], provider: str) -> None:
    """Project a narrative row onto the shared triage field contract, in place.

    Provider rows use different names (``description``/``summary`` for the
    body, ``source`` for the publisher, ``event`` for the title, no dedupe
    key), so aliases are resolved first and then merged through
    ``news_triage.project_article`` — the same contract the realtime path
    builds rows with. Without the body alias the D0_1 injection scan never
    sees the description; without the key D0_2 cannot detect repeats. Merge
    is selective: only contract fields are overwritten, so provider extras
    (sentiment, symbol, ...) and a caller-supplied ``dedupe_key`` survive.
    """
    title = str(article.get("title") or article.get("event") or "").strip()
    body = str(
        article.get("text_or_summary")
        or article.get("description")
        or article.get("summary")
        or ""
    )
    url = str(article.get("url") or article.get("article_url") or "").strip()
    publisher = str(article.get("publisher") or article.get("source") or provider)
    projected = project_article(
        title=title,
        url=url,
        publisher=publisher,
        provider=provider,
        published_at=article.get("published_at"),
        text_or_summary=body,
    )
    for key, value in projected.items():
        if key == "dedupe_key":
            continue  # caller-supplied keys win (same contract as before)
        if value is not None and value != "":
            article[key] = value
    if not article.get("dedupe_key"):
        article["dedupe_key"] = projected["dedupe_key"]


def ingest_narrative_news_into_graph(
    graph: Any,
    articles: Sequence[Dict[str, Any]],
    *,
    provider: str = "newsapi",
    observed_at: str = "",
    triage_enabled: bool = True,
    learning_logger: Any = None,
) -> Dict[str, Any]:
    """Ingest headline/narrative news as candidate sources (never event reactions).

    Every *news-shaped* entry passes the same Layer-0 Laya triage gate as the
    realtime ingest path (``realtime_news_ingest.ingest_articles``): D0_1
    credibility, D0_2 duplication, D0_3 fact-vs-opinion, plus the D5a/D9/R1
    verdicts. Rows are first normalized onto the triage article contract
    (``description`` body → ``text_or_summary``, sha dedupe keys) so the gate
    enforces on this path's real field shapes. REJECT actions (spoofed,
    unverified/allow-list-failing domains) never reach the graph; TAG actions
    (rumor/opinion/stale repeat) ingest with the verdict in ``payload["triage"]``
    for downstream down-weighting — same contract as ``ingest_articles``.
    ``NEWS_TRIAGE_MODE=off`` bypasses the gate (legacy behavior).

    Exempt by design: the pipeline's numeric write sites —
    ``ingest_fred_into_graph``, the Portfolio123 snapshot, and the alternative
    nowcast rows — carry measured numeric observations, not headlines, so
    Layer-0 triage does not apply to them.
    """
    if graph is None:
        return {
            "status": "SKIPPED", "ingested": 0, "source_ids": [],
            "triaged_rejected": 0, "triage_active": False,
            "execution_authority": False,
        }
    stamp = observed_at or utcnow()
    source_ids: List[str] = []
    rows = [a for a in (articles or []) if isinstance(a, dict)]
    for row in rows:
        _normalize_narrative_row(row, provider)
    # Layer-0 gate (shared with realtime_news_ingest.ingest_articles): triages
    # the normalized rows in place; same-batch repeats resolve STALE_REPEAT
    # via the dedupe keys assigned above.
    gate = TriageGate(triage_enabled=triage_enabled, learning_logger=learning_logger)
    gate.apply(rows)
    for article in rows:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        if gate.is_rejected(article):
            continue
        verdict = article.get("triage") or {}
        try:
            source_id = graph.ingest_source(
                title=title[:240],
                payload={
                    "kind": f"{provider}_narrative",
                    "article": {
                        k: article.get(k)
                        for k in (
                            "title", "description", "text_or_summary", "url",
                            "source", "sentiment", "published_at", "author", "symbol",
                        )
                        if article.get(k) is not None
                    },
                    "summary": article.get("text_or_summary"),
                    "dedupe_key": article.get("dedupe_key"),
                    "triage": dict(verdict) if verdict else None,
                    "execution_authority": False,
                },
                source_url=str(article.get("url") or ""),
                publisher=str(article.get("publisher") or article.get("source") or provider),
                published_at=str(article.get("published_at") or article.get("time") or stamp),
                rights_note=(
                    f"{provider} narrative; candidate research source only; "
                    "must not drive release blackout or order permission"
                ),
            )
            source_ids.append(str(source_id))
        except Exception:
            continue
    return {
        "status": "INGESTED" if source_ids else "SKIPPED",
        "ingested": len(source_ids),
        "source_ids": source_ids[:40],
        "provider": provider,
        "triaged_rejected": gate.rejected,
        "triage_active": gate.active,
        "execution_authority": False,
    }


def ingest_optional_providers(
    graph: Any,
    api_manager: Any,
    *,
    symbol: str = "",
    observed_at: str = "",
) -> Dict[str, Any]:
    """Best-effort OpenBB / Seeking Alpha / Portfolio123 ingest when clients exist.

    The OpenBB sweep is bounded by ONE total time budget shared across every
    batch method (default ``OPENBB_SWEEP_BUDGET_S``, 45s).  Without a shared
    budget the six sequential batch sweeps each burn their own per-batch
    allowance — minutes of serial retries against failing providers — which
    starved the Ψ guardian heartbeat and stalled whole cycles (observed
    live: HEARTBEAT trip while the loop was merely deep in enrichment).
    """
    report: Dict[str, Any] = {
        "openbb": {"status": "SKIPPED", "execution_authority": False},
        "seeking_alpha": {"status": "SKIPPED", "execution_authority": False},
        "portfolio123": {"status": "SKIPPED", "execution_authority": False},
        "execution_authority": False,
    }
    if api_manager is None:
        return report
    stamp = observed_at or utcnow()

    try:
        sweep_budget = float(os.environ.get("OPENBB_SWEEP_BUDGET_S", "20"))
    except (TypeError, ValueError):
        sweep_budget = 20.0
    sweep_budget = max(1.0, sweep_budget)
    sweep_deadline = time.monotonic() + sweep_budget

    def _budget_left() -> float:
        return max(0.0, sweep_deadline - time.monotonic())

    openbb = getattr(api_manager, "openbb", None)
    if openbb is not None:
        try:
            if getattr(openbb, "_graph", None) is None and hasattr(openbb, "attach_graph"):
                openbb.attach_graph(graph)
            elif getattr(openbb, "_graph", None) is None:
                try:
                    openbb._graph = graph
                except Exception:
                    pass
            if hasattr(graph, "_openbb_bridge"):
                graph._openbb_bridge = openbb
            results = []
            for method in ("fetch_all_macro", "fetch_all_central_bank", "fetch_all_official_stats", "fetch_all_exchange_data", "fetch_all_fixed_income"):
                left = _budget_left()
                if left <= 0:
                    results.append((method, False, "skipped: sweep budget exhausted"))
                    continue
                fn = getattr(openbb, method, None)
                if callable(fn):
                    try:
                        batch = fn(time_budget_s=left) or []
                        results.extend(batch)
                    except TypeError:
                        # Legacy bridge without budget support — call bare.
                        try:
                            batch = fn() or []
                            results.extend(batch)
                        except Exception:
                            pass
                    except Exception:
                        pass
            if not results and hasattr(openbb, "fetch_and_ingest"):
                for data_type in ("central-bank-release", "official-statistics", "exchange-data", "fixed-income", "macro-indicators", "equity-screening", "regulatory-filing", "news-sentiment"):
                    try:
                        batch = openbb.fetch_by_type(data_type) or []
                        results.extend(batch)
                    except Exception:
                        pass
            ok = sum(1 for item in results if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1])
            report["openbb"] = {
                "status": "INGESTED" if ok else "EMPTY",
                "attempted": len(results),
                "succeeded": ok,
                "execution_authority": False,
            }
        except Exception as exc:
            report["openbb"] = {
                "status": "ERROR",
                "error": str(exc)[:300],
                "execution_authority": False,
            }

    sa = getattr(api_manager, "seeking_alpha", None)
    if sa is not None and symbol:
        try:
            articles: List[Dict[str, Any]] = []
            for method_name in ("get_news_articles", "get_news", "fetch_news", "news_for_symbol"):
                method = getattr(sa, method_name, None)
                if not callable(method):
                    continue
                try:
                    raw = method(symbol)
                except TypeError:
                    try:
                        raw = method(symbol=symbol, limit=5)
                    except TypeError:
                        raw = method(ticker=symbol)
                if isinstance(raw, list):
                    for row in raw[:8]:
                        if hasattr(row, "model_dump"):
                            articles.append(row.model_dump())
                        elif isinstance(row, dict):
                            articles.append(row)
                break
            report["seeking_alpha"] = ingest_narrative_news_into_graph(
                graph, articles, provider="seeking_alpha", observed_at=stamp,
            )
        except Exception as exc:
            report["seeking_alpha"] = {
                "status": "ERROR",
                "error": str(exc)[:300],
                "execution_authority": False,
            }

    p123 = getattr(api_manager, "portfolio123", None)
    if p123 is not None and graph is not None:
        try:
            payload = None
            for method_name in ("snapshot", "get_snapshot", "status"):
                method = getattr(p123, method_name, None)
                if callable(method):
                    payload = method()
                    break
            if payload is not None:
                source_id = graph.ingest_source(
                    title=f"Portfolio123:{symbol or 'MARKET'}",
                    payload={
                        "kind": "portfolio123_snapshot",
                        "symbol": symbol,
                        "data": payload if isinstance(payload, (dict, list)) else {"value": payload},
                        "execution_authority": False,
                    },
                    publisher="Portfolio123",
                    published_at=stamp,
                    rights_note="Portfolio123 research snapshot; candidate only",
                )
                report["portfolio123"] = {
                    "status": "INGESTED",
                    "source_id": source_id,
                    "execution_authority": False,
                }
        except Exception as exc:
            report["portfolio123"] = {
                "status": "ERROR",
                "error": str(exc)[:300],
                "execution_authority": False,
            }
    return report


def ingest_alternative_nowcast(
    graph: Any,
    *,
    journal_path: str = "",
    observed_at: str = "",
    include_stubs: bool = False,
) -> Dict[str, Any]:
    """Ingest Tier 1/2/3 alternative nowcast observations as candidate macro sources."""
    stamp = observed_at or utcnow()
    try:
        from data.alternative_data_provider import AlternativeDataProvider
    except ImportError:
        from data.alternative_data_provider import AlternativeDataProvider

    provider = AlternativeDataProvider()
    snapshot = provider.fetch_nowcast_snapshot(include_stubs=include_stubs)
    observations = snapshot.get("observations") or []
    source_ids: List[str] = []
    entities: List[str] = []

    if journal_path:
        try:
            from intelligence.macro_observations import ingest_alternative_snapshot
        except ImportError:
            ingest_alternative_snapshot = None  # type: ignore[assignment,misc]
        if ingest_alternative_snapshot is not None:
            ingest_alternative_snapshot(journal_path, observations, captured_at=stamp)

    if graph is None:
        return {
            "status": "SKIPPED",
            "ingested": 0,
            "live_count": snapshot.get("live_count", 0),
            "stub_count": snapshot.get("stub_count", 0),
            "source_ids": [],
            "entities": [],
            "execution_authority": False,
        }

    bulk_items: List[Dict[str, Any]] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        if row.get("status") not in {"LIVE", "CACHED"} and not include_stubs:
            continue
        entity = str(row.get("entity") or "")
        source_id_key = str(row.get("source_id") or "alt")
        if entity and entity not in entities:
            entities.append(entity)
        bulk_items.append({
            "title": f"ALT:{source_id_key}:{entity}",
            "payload": {
                "kind": "alternative_nowcast",
                "source_id": source_id_key,
                "entity": entity,
                "tier": row.get("tier"),
                "provider": row.get("provider"),
                "value": row.get("value"),
                "status": row.get("status"),
                "payload": row.get("payload") or {},
                "execution_authority": False,
            },
            "publisher": str(row.get("provider") or "alternative_data"),
            "published_at": str(row.get("observed_at") or stamp),
            "rights_note": (
                "Alternative nowcast observation; research evidence only; "
                "official releases are lagging benchmarks not primary inputs"
            ),
        })

    if hasattr(graph, "ingest_sources_bulk"):
        try:
            source_ids = list(graph.ingest_sources_bulk(bulk_items) or [])
        except Exception:
            source_ids = []
    else:
        for item in bulk_items:
            try:
                sid = graph.ingest_source(
                    title=item["title"],
                    payload=item["payload"],
                    publisher=item["publisher"],
                    published_at=item["published_at"],
                    rights_note=item["rights_note"],
                )
                source_ids.append(str(sid))
            except Exception:
                continue

    return {
        "status": "INGESTED" if source_ids else "EMPTY",
        "ingested": len(source_ids),
        "live_count": snapshot.get("live_count", 0),
        "stub_count": snapshot.get("stub_count", 0),
        "source_ids": source_ids[:60],
        "entities": entities,
        "registry": snapshot.get("registry") or {},
        "execution_authority": False,
    }


def process_cycle_data_sources(
    *,
    macro_graph: Any,
    journal_path: str,
    symbol: str,
    calendar_events: Sequence[Dict[str, Any]] | None = None,
    api_news: Sequence[Dict[str, Any]] | None = None,
    hot_topics: Sequence[Dict[str, Any]] | None = None,
    macro_data: Dict[str, Any] | None = None,
    api_manager: Any = None,
    observed_at: str = "",
    include_optional_providers: bool = False,
    include_alternative_nowcast: bool = True,
) -> Dict[str, Any]:
    """Process every available news/macro feed for one cycle (advisory only)."""
    stamp = observed_at or utcnow()
    calendar_events = _safe_list(calendar_events)
    api_news = _safe_list(api_news)
    hot_topics = _safe_list(hot_topics)
    macro_data = macro_data if isinstance(macro_data, dict) else {}

    observations = {
        "fred": ingest_fred_snapshot(journal_path, macro_data, captured_at=stamp),
        "calendar": ingest_calendar_events(journal_path, calendar_events, captured_at=stamp),
        "execution_authority": False,
    }
    fred_graph = ingest_fred_into_graph(macro_graph, macro_data, observed_at=stamp)
    news_graph = ingest_narrative_news_into_graph(
        macro_graph, api_news, provider="newsapi", observed_at=stamp,
    )
    topics_graph = ingest_narrative_news_into_graph(
        macro_graph, hot_topics, provider="newsapi_hot_topics", observed_at=stamp,
    )
    optional = (
        ingest_optional_providers(macro_graph, api_manager, symbol=symbol, observed_at=stamp)
        if include_optional_providers
        else {"status": "SKIPPED", "execution_authority": False}
    )
    alternative = (
        ingest_alternative_nowcast(
            macro_graph, journal_path=journal_path, observed_at=stamp,
        )
        if include_alternative_nowcast
        else {"status": "SKIPPED", "execution_authority": False}
    )

    entities = list(fred_graph.get("entities") or [])
    entities.extend(alternative.get("entities") or [])
    return {
        "status": "PROCESSED",
        "captured_at": stamp,
        "symbol": str(symbol or "").upper(),
        "observations": observations,
        "fred_graph": fred_graph,
        "news_graph": news_graph,
        "hot_topics_graph": topics_graph,
        "optional_providers": optional,
        "alternative_nowcast": alternative,
        "macro_entities_extra": entities,
        "execution_authority": False,
        "limitation": (
            "Pipeline feeds research/causal retrieval only. Calendar releases remain "
            "the sole event-reaction schedulers; RiskController retains order authority."
        ),
    }
