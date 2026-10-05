"""Governed realtime news ingest → candidate macro sources / claims.

Allow-listed RSS (Reuters, Fed), NewsAPI, and Seeking Alpha are normalized and
ingested as research evidence. Calendar releases remain the only event-reaction
schedulers. Never grants execution_authority.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.request import Request, urlopen

try:
    from intelligence.economic_dynamics_catalog import EVENT_TEMPLATES, resolve_entity_alias
except ImportError:
    from intelligence.economic_dynamics_catalog import EVENT_TEMPLATES, resolve_entity_alias

try:
    from intelligence.macro_source_policy import assess_source
except ImportError:
    from intelligence.macro_source_policy import assess_source

try:
    from data.news_triage import TriageGate, project_article, summarize_cycle_evidence
except ImportError:
    from data.news_triage import TriageGate, project_article, summarize_cycle_evidence

try:
    from data.kill_switch_latch import get_kill_switch_latch
except ImportError:
    from data.kill_switch_latch import get_kill_switch_latch


DEFAULT_RSS_FEEDS: List[Dict[str, str]] = [
    {
        "provider": "bloomberg",
        "publisher": "Bloomberg",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
    },
    {
        "provider": "yahoo_finance",
        "publisher": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
    },
    {
        "provider": "seeking_alpha",
        "publisher": "Seeking Alpha",
        "url": "https://seekingalpha.com/feed.xml",
    },
    {
        "provider": "federalreserve",
        "publisher": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
    },
    {
        "provider": "world_gold_council",
        "publisher": "World Gold Council",
        "url": "https://www.gold.org/rss/research",
    },
    {
        "provider": "reuters",
        "publisher": "Reuters",
        "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best",
        "fallback_url": "https://feeds.reuters.com/reuters/businessNews",
    },
]



def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def normalize_article(
    *,
    title: str,
    url: str = "",
    publisher: str = "",
    published_at: str = "",
    text_or_summary: str = "",
    provider: str = "",
) -> Optional[Dict[str, Any]]:
    """Build a validated realtime article row via the shared field contract.

    Realtime additions over ``news_triage.project_article``: reject empty
    titles (validated rows are contractual), default ``published_at`` to now,
    and fall the body back to the title. The field shape itself (policy,
    event_class, dedupe key) is owned by the shared projector.
    """
    title = _text(title, 400)
    if not title:
        return None
    row = project_article(
        title=title,
        url=url,
        publisher=publisher,
        provider=provider,
        published_at=published_at or utcnow(),
        text_or_summary=text_or_summary or title,
    )
    return row


def _parse_rss_bytes(payload: bytes, *, provider: str, publisher: str, source_url: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    items = root.findall(".//item")
    if not items:
        # Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for item in items[:15]:
        title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
        link = item.findtext("link") or ""
        if not link:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.attrib.get("href", "")
        summary = (
            item.findtext("description")
            or item.findtext("{http://www.w3.org/2005/Atom}summary")
            or item.findtext("{http://www.w3.org/2005/Atom}content")
            or ""
        )
        published = (
            item.findtext("pubDate")
            or item.findtext("{http://www.w3.org/2005/Atom}updated")
            or item.findtext("{http://www.w3.org/2005/Atom}published")
            or ""
        )
        # Prefer article link host for policy; fall back to feed URL.
        article = normalize_article(
            title=title,
            url=link or source_url,
            publisher=publisher,
            published_at=published,
            text_or_summary=summary,
            provider=provider,
        )
        if article:
            rows.append(article)
    return rows


def fetch_rss_feed(url: str, *, provider: str, publisher: str, timeout: float = 8.0) -> List[Dict[str, Any]]:
    try:
        req = Request(url, headers={"User-Agent": "llm-trader-research/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
        return _parse_rss_bytes(payload, provider=provider, publisher=publisher, source_url=url)
    except Exception:
        return []


def collect_rss_articles(feeds: Optional[Sequence[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for feed in feeds or DEFAULT_RSS_FEEDS:
        rows = fetch_rss_feed(feed["url"], provider=feed.get("provider", "rss"), publisher=feed.get("publisher", "RSS"))
        if not rows and feed.get("fallback_url"):
            rows = fetch_rss_feed(
                feed["fallback_url"],
                provider=feed.get("provider", "rss"),
                publisher=feed.get("publisher", "RSS"),
            )
        out.extend(rows)
    return out


def collect_newsapi_articles(api_manager: Any, *, keywords: Sequence[str], limit: int = 5) -> List[Dict[str, Any]]:
    if api_manager is None or not hasattr(api_manager, "get_news"):
        return []
    try:
        raw = api_manager.get_news(list(keywords), page_size=limit) or []
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        article = normalize_article(
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            publisher=str(item.get("source") or "NewsAPI"),
            published_at=str(item.get("publishedAt") or item.get("published_at") or ""),
            text_or_summary=str(item.get("description") or item.get("content") or ""),
            provider="newsapi",
        )
        if article:
            rows.append(article)
    return rows


def collect_seeking_alpha_articles(api_manager: Any, *, symbol: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    sa = getattr(api_manager, "seeking_alpha", None) if api_manager is not None else None
    if sa is None or not hasattr(sa, "get_news_articles"):
        return []
    try:
        raw = sa.get_news_articles(symbol=symbol or None, limit=limit) or []
    except TypeError:
        try:
            raw = sa.get_news_articles(symbol) or []
        except Exception:
            return []
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for item in raw:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        article = normalize_article(
            title=str(item.get("title") or ""),
            url=str(item.get("article_url") or item.get("url") or "https://seekingalpha.com/"),
            publisher=str(item.get("source") or "Seeking Alpha"),
            published_at=str(item.get("published_at") or ""),
            text_or_summary=str(item.get("title") or ""),
            provider="seeking_alpha",
        )
        if article:
            rows.append(article)
    return rows


def watch_entities_for_event_class(event_class: Optional[str]) -> List[str]:
    if not event_class:
        return []
    for template in EVENT_TEMPLATES:
        if str(template.get("entity")) == str(event_class):
            watch = [str(x) for x in (template.get("watch") or [])]
            return [event_class, *watch]
    return [str(event_class)]


def ingest_articles(
    graph: Any,
    articles: Sequence[Dict[str, Any]],
    *,
    triage_enabled: bool = True,
    learning_logger: Any = None,
) -> Dict[str, Any]:
    """Ingest allow-listed articles; skip UNVERIFIED domains; apply Layer-0 Laya triage.

    Triage (D0_1 credibility / D0_2 duplication / D0_3 fact-vs-opinion) runs per
    article BEFORE graph ingest. REJECT actions never reach the graph; TAG
    actions are ingested but flagged as commentary/rumor/duplicate in the payload
    so downstream consumers can down-weight them.

    ``learning_logger`` (optional) receives every (verdict, article) pair via
    ``log_verdict`` for active-learning persistence; failures are swallowed.
    """
    if graph is None:
        return {"status": "SKIPPED", "ingested": 0, "skipped": 0, "source_ids": [], "execution_authority": False}
    ingested = 0
    skipped = 0
    source_ids: List[str] = []
    classified: List[Dict[str, Any]] = []
    entities: List[str] = []
    seen = set()
    gate = TriageGate(triage_enabled=triage_enabled, learning_logger=learning_logger)
    gate.apply(list(articles))
    triage_report = gate.report
    for article in articles:
        if not isinstance(article, dict):
            skipped += 1
            continue
        key = article.get("dedupe_key") or article.get("url") or article.get("title")
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        policy = article.get("source_policy") or assess_source(article.get("url", ""), article.get("publisher", ""))
        if policy.get("allowed") is not True:
            skipped += 1
            continue
        if gate.is_rejected(article):
            skipped += 1
            continue
        try:
            source_id = graph.ingest_source(
                title=str(article.get("title") or "")[:240],
                payload={
                    "kind": "realtime_rss" if str(article.get("provider", "")).startswith(("reuters", "federal")) or "rss" in str(article.get("provider", "")) else "realtime_news",
                    "provider": article.get("provider"),
                    "event_class": article.get("event_class"),
                    "summary": article.get("text_or_summary"),
                    "dedupe_key": article.get("dedupe_key"),
                    "triage": article.get("triage") or None,
                    "execution_authority": False,
                },
                source_url=str(article.get("url") or ""),
                publisher=str(article.get("publisher") or article.get("provider") or "news"),
                published_at=str(article.get("published_at") or ""),
                rights_note="realtime news candidate; not a calendar release; no order authority",
            )
        except Exception:
            skipped += 1
            continue
        ingested += 1
        source_ids.append(str(source_id))
        event_class = article.get("event_class")
        if event_class:
            classified.append({
                "source_id": source_id,
                "event_class": event_class,
                "title": article.get("title"),
                "url": article.get("url"),
            })
            for ent in watch_entities_for_event_class(str(event_class)):
                if ent not in entities:
                    entities.append(ent)
        article["source_id"] = source_id
    learning_summary: Dict[str, Any] = {"status": "DISABLED", "execution_authority": False}
    if learning_logger is not None:
        try:
            summary_fn = getattr(learning_logger, "summary", None)
            if callable(summary_fn):
                learning_summary = dict(summary_fn() or {})
        except Exception:
            learning_summary = {"status": "ERROR", "execution_authority": False}
    return {
        "status": "INGESTED" if ingested else "EMPTY",
        "ingested": ingested,
        "skipped": skipped,
        "triage": triage_report or {"status": "DISABLED", "mode": "off", "execution_authority": False},
        "triage_rejected": gate.rejected,
        "learning": learning_summary,
        "source_ids": source_ids[:40],
        "classified": classified[:20],
        "macro_entities_extra": entities,
        "execution_authority": False,
    }


def extract_and_persist_claims(
    *,
    graph: Any,
    memory: Any,
    articles: Sequence[Dict[str, Any]],
    llm: Any = None,
    max_articles: int = 3,
) -> Dict[str, Any]:
    """Run macro_extraction on allow-listed articles with enough text (CANDIDATE only)."""
    if llm is None or graph is None or memory is None:
        return {
            "status": "SKIPPED_NO_LLM",
            "extracted": 0,
            "edge_ids": [],
            "execution_authority": False,
        }
    from intelligence.macro_extraction import extract_candidates, persist_candidates
    from intelligence.economic_dynamics_catalog import resolve_entity_alias as resolve_alias

    extracted = 0
    edge_ids: List[int] = []
    errors: List[str] = []
    for article in list(articles)[: max(0, int(max_articles))]:
        if not isinstance(article, dict):
            continue
        source_id = article.get("source_id")
        text = str(article.get("text_or_summary") or "")
        if not source_id or len(text) < 40:
            continue
        policy = article.get("source_policy") or assess_source(article.get("url", ""), article.get("publisher", ""))
        if policy.get("allowed") is not True:
            continue
        try:
            candidates = extract_candidates(
                llm,
                source_id=str(source_id),
                source_title=str(article.get("title") or ""),
                source_text=text,
                source_url=str(article.get("url") or ""),
                publisher=str(article.get("publisher") or ""),
                rights_note="realtime news candidate extraction",
            )
            # Map entity names onto catalog aliases when possible.
            for rel in candidates.get("causal_relationships") or []:
                if not isinstance(rel, dict):
                    continue
                src = resolve_alias(str(rel.get("source_entity") or ""))
                tgt = resolve_alias(str(rel.get("target_entity") or ""))
                if src:
                    rel["source_entity"] = src
                if tgt:
                    rel["target_entity"] = tgt
            persisted = persist_candidates(graph, memory, candidates)
            extracted += 1
            edge_ids.extend(int(x) for x in (persisted.get("edge_ids") or []) if x is not None)
        except Exception as exc:
            errors.append(str(exc)[:200])
    return {
        "status": "EXTRACTED" if extracted else ("ERROR" if errors else "EMPTY"),
        "extracted": extracted,
        "edge_ids": edge_ids[:40],
        "errors": errors[:5],
        "execution_authority": False,
    }


def run(
    *,
    graph: Any,
    api_manager: Any = None,
    memory: Any = None,
    llm: Any = None,
    symbol: str = "",
    keywords: Optional[Sequence[str]] = None,
    include_rss: bool = True,
    extract_claims: bool = True,
    learning_logger: Any = None,
) -> Dict[str, Any]:
    """Fetch → normalize → triage → ingest → optional CANDIDATE claim extraction.

    ``learning_logger`` is an optional active-learning sink exposing
    ``log_verdict(verdict, article)`` (typically
    ``research.learning.triage_ledger.TriageDisagreementLogger``); when None the
    call is a no-op. Built lazily by callers so a broken learning store never
    blocks news ingestion.
    """
    articles: List[Dict[str, Any]] = []
    if include_rss:
        articles.extend(collect_rss_articles())
    kw = list(keywords or ([symbol] if symbol else ["Federal Reserve", "inflation", "markets"]))
    articles.extend(collect_newsapi_articles(api_manager, keywords=kw, limit=5))
    articles.extend(collect_seeking_alpha_articles(api_manager, symbol=symbol, limit=5))

    ingest = ingest_articles(graph, articles, learning_logger=learning_logger)
    # Attach source_ids onto article copies used for extraction.
    by_key = {a.get("dedupe_key"): a for a in articles if isinstance(a, dict)}
    for row in ingest.get("classified") or []:
        # classified may not include dedupe; extraction uses ingested articles with source_id set
        pass
    extraction = {"status": "SKIPPED", "execution_authority": False}
    if extract_claims:
        triaged_articles = [
            a for a in articles
            if isinstance(a, dict) and a.get("source_id")
            and (a.get("triage") or {}).get("d0_3") not in (
                "ANALYST_OPINION", "RUMOR_UNCONFIRMED",
            )
        ]
        extraction = extract_and_persist_claims(
            graph=graph,
            memory=memory,
            articles=triaged_articles,
            llm=llm,
        )
    evidence_summary = None
    try:
        evidence_summary = summarize_cycle_evidence(articles)
    except Exception:
        evidence_summary = None

    # D9 emergency kill-switch: near-zero-latency lexical tripwire fired per
    # article during triage. Advisory cycle-state signal (halt risk trading);
    # requires market-data confirmation before any risk action.
    kill_headlines: List[str] = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        v = a.get("triage") or {}
        if str(v.get("d9_kill_switch") or "").upper() == "EMERGENCY_HALT_ACTIVE":
            url = str(a.get("url") or a.get("link") or "")
            publisher = str(a.get("publisher") or a.get("source") or "")
            assessment = assess_source(url=url, publisher=publisher)
            # H3: only PRIMARY sources and wire-service hosts count toward
            # the latch — aggregators and publisher-name matches never do.
            try:
                from intelligence.macro_source_policy import counts_toward_latch
                _counts = counts_toward_latch(assessment)
            except Exception:
                _counts = bool(assessment.get("allowed")) and assessment.get("tier") == "PRIMARY"
            if _counts:
                kill_headlines.append(str(a.get("title") or "")[:120])
    # Latch: one batch of tripwire headlines is a *pending confirmation*, not
    # an active kill-switch; the latch requires N batches within its window.
    # Once latched, quiet cycles never clear it — only explicit market data
    # (applied by MacroAnalyst) can, and a re-arm cooldown prevents flapping.
    try:
        latch_snapshot = get_kill_switch_latch().observe(
            len(kill_headlines), triggered_headlines=kill_headlines[:5]
        )
    except Exception:
        latch_snapshot = None
    kill_active = bool((latch_snapshot or {}).get("latched"))
    kill_switch = {
        "active": kill_active,
        "count": len(kill_headlines),
        "triggered_headlines": kill_headlines[:5],
        "question_id": "D9_emergency_kill_switch",
        "latch": latch_snapshot,
        "execution_authority": False,
        "limitation": (
            "Kill-switch state persists across cycles until explicit market "
            "data clears it; news silence never un-kills. Advisory only — "
            "cycle risk state, never order authority."
        ),
    }

    return {
        "status": "PROCESSED",
        "captured_at": utcnow(),
        "article_count": len(articles),
        "ingest": ingest,
        "triage": ingest.get("triage"),
        "learning": ingest.get("learning"),
        "evidence_summary": evidence_summary,
        "kill_switch": kill_switch,
        "extraction": extraction,
        "macro_entities_extra": list(ingest.get("macro_entities_extra") or []),
        "execution_authority": False,
        "limitation": (
            "Realtime news expands candidate graph evidence and watch entities only. "
            "Calendar releases remain the sole event-reaction measurement path."
        ),
    }
