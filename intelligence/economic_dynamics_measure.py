"""Measure Economic Dynamics catalog edges from live/runtime event reactions.

Fail-closed research path:
- Mirrors only numeric surprise reactions onto the catalog seed source.
- Never invents returns or grants execution authority.
- Auto-validation still requires the autonomous sample floor.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from intelligence.autonomous_causal_engine import AutonomousCausalValidator
except ImportError:
    from intelligence.autonomous_causal_engine import AutonomousCausalValidator

try:
    from intelligence.economic_dynamics_catalog import CATALOG_VERSION, EVENT_TEMPLATES
except ImportError:
    from intelligence.economic_dynamics_catalog import CATALOG_VERSION, EVENT_TEMPLATES

try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph


# Calendar / news title tokens → catalog event entities.
_EVENT_TOKEN_MAP = (
    (("CPI", "INFLATION", "PCE", "HICP"), "CPI_RELEASE"),
    (("NFP", "NONFARM", "NON-FARM", "PAYROLL", "EMPLOYMENT"), "NFP_RELEASE"),
    (("FOMC", "RATE DECISION", "INTEREST RATE", "ECB", "BOJ", "MPC"), "CB_MEETING"),
    (("WAR", "MISSILE", "STRIKE", "INVASION", "GEOPOLIT"), "WAR_SHOCK"),
    (("BANK", "SVB", "FDIC", "CREDIT SUISSE"), "BANKING_STRESS"),
    (("TARIFF", "TRADE WAR", "SANCTION"), "TRADE_WAR"),
    (("TWEET", "TRUTH SOCIAL", "ELECTION", "IMPEACH", "EXECUTIVE ORDER"), "POLITICAL_SHOCK"),
    (("GOLD RESERVE", "GOLD BUYING", "PBOC GOLD", "CENTRAL BANK GOLD", "GOLD ACCUMULATION", "GOLD STOCKPILE"), "CB_GOLD_ACCUMULATION"),
)


def _event_title(event: Dict[str, Any]) -> str:
    return str(event.get("event") or event.get("title") or event.get("name") or "").upper()


def classify_runtime_event(event: Dict[str, Any]) -> Optional[str]:
    title = _event_title(event)
    if not title:
        return None
    for tokens, entity in _EVENT_TOKEN_MAP:
        if any(token in title for token in tokens):
            return entity
    return None


# R1_headline_event_routing values → catalog EVENT_CLASS entities. Mirrors
# _EVENT_TOKEN_MAP one-to-one so a model-routed headline lands on the same
# catalog seed the lexical router would have used.
_R1_ENTITY_MAP = {
    "EVENT_CPI": "CPI_RELEASE",
    "EVENT_NFP": "NFP_RELEASE",
    "EVENT_CB_MEETING": "CB_MEETING",
    "EVENT_GEO_SHOCK": "WAR_SHOCK",
    "EVENT_BANKING_STRESS": "BANKING_STRESS",
    "EVENT_TRADE_WAR": "TRADE_WAR",
    "EVENT_POLITICAL": "POLITICAL_SHOCK",
    "EVENT_CB_GOLD": "CB_GOLD_ACCUMULATION",
}


def _r1_lexical_router(article: Dict[str, Any]) -> str:
    """Re-derive the lexical R1 router on the article (cheap, no model)."""
    try:
        from data.news_triage import R1_HeadlineEventRouting, _deterministic_r1
    except ImportError:  # package-context imports
        from data.news_triage import R1_HeadlineEventRouting, _deterministic_r1  # type: ignore
    try:
        return _deterministic_r1(article)
    except Exception:
        return R1_HeadlineEventRouting.INSUFFICIENT_DATA.value


def post_validate_r1_routing(
    article: Dict[str, Any],
    r1_value: str,
) -> Dict[str, Any]:
    """Post-validate a model-routed R1 class against the lexical router.

    Doctrine (matches the triage-time R1 floor):
    - lexical agreement → accept ("lexical_agreement");
    - lexical abstention (NOT_EVENT_NOISE / INSUFFICIENT_DATA) → accept as a
      model extension — the triage-time 0.5-probability floor already gated
      this answer when the model ran;
    - lexical names a DIFFERENT concrete class → reject ("contradiction"):
      ambiguous provenance, fail closed rather than guess.
    """
    if r1_value not in _R1_ENTITY_MAP:
        return {
            "accepted": False,
            "mode": "non_event",
            "reason": f"r1 value {r1_value!r} is not a concrete event class",
        }
    title = str(article.get("title") or "")
    if not title.strip():
        return {
            "accepted": False,
            "mode": "no_title",
            "reason": "article has no title to post-validate against",
        }
    lexical = _r1_lexical_router(article)
    if lexical == r1_value:
        return {
            "accepted": True,
            "mode": "lexical_agreement",
            "reason": "model class agrees with lexical router",
        }
    try:
        from data.news_triage import R1_HeadlineEventRouting
    except ImportError:
        from data.news_triage import R1_HeadlineEventRouting  # type: ignore
    abstentions = (
        R1_HeadlineEventRouting.NOT_EVENT_NOISE.value,
        R1_HeadlineEventRouting.INSUFFICIENT_DATA.value,
    )
    if lexical in abstentions:
        return {
            "accepted": True,
            "mode": "model_extension",
            "reason": f"lexical router abstained ({lexical}); model class extends routing",
        }
    return {
        "accepted": False,
        "mode": "lexical_contradiction",
        "reason": f"lexical router says {lexical}, model says {r1_value}",
    }


def find_catalog_seed_source_id(graph: MacroGraph) -> Optional[str]:
    """Newest source whose payload declares the catalog-seed kind.

    Matches on ``payload.kind`` directly. The previous version scanned only the
    200 most-recently-captured sources and JSON-parsed each, which returned
    ``None`` forever once ``macro_sources`` outgrew that window: the seed is a
    long-lived row written once per catalog version, while the table accrues a
    source per article per cycle, so the seed fell ~76k ranks down. Every
    mirroring attempt then bailed at the "seed missing" guard -- which returns a
    normal result rather than raising, so nothing logged it.
    """
    with graph._con() as con:
        row = con.execute(
            "SELECT source_id FROM macro_sources"
            " WHERE json_extract(payload,'$.kind')='economic_dynamics_catalog_seed'"
            " ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
    return str(row["source_id"]) if row else None


def _finite_number(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def mirror_runtime_reaction_to_catalog(
    graph: MacroGraph,
    *,
    event: Dict[str, Any],
    symbol: str,
    windows: Dict[str, Any],
    source_quality: str = "RUNTIME_FEED_MIRROR",
    catalog_source_id: Optional[str] = None,
    r1_verdict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy one measured surprise onto the catalog seed source for validation.

    Requires numeric actual + forecast. Does not invent window returns.

    ``r1_verdict`` (optional) carries a per-article triage R1 routing —
    ``{"r1": <R1 value>, "article": <article dict>}`` — so headlines the
    lexical title-token map misses can still reach their catalog event class.
    The model class is post-validated against the lexical router
    (``post_validate_r1_routing``): agreement or lexical abstention accepts,
    contradiction fails closed.
    """
    actual = _finite_number(event.get("actual"))
    forecast = _finite_number(event.get("forecast"))
    previous = _finite_number(event.get("previous"))
    if actual is None or (forecast is None and previous is None):
        return {
            "mirrored": False,
            "reason": "missing numeric actual/(forecast or previous)",
            "execution_authority": False,
        }
    if not isinstance(windows, dict) or not windows:
        return {
            "mirrored": False,
            "reason": "missing measured windows",
            "execution_authority": False,
        }
    # Entity resolution: lexical title tokens first (existing contract), then
    # the post-validated per-article R1 routing as the extension path.
    entity = classify_runtime_event(event)
    routing_mode = "lexical_title"
    if entity is None and isinstance(r1_verdict, dict):
        check = post_validate_r1_routing(
            r1_verdict.get("article") or {},
            str(r1_verdict.get("r1") or ""),
        )
        if check["accepted"]:
            entity = _R1_ENTITY_MAP[str(r1_verdict.get("r1"))]
            routing_mode = f"model_r1:{check['mode']}"
        else:
            return {
                "mirrored": False,
                "reason": f"r1 post-validation rejected ({check['mode']}): {check['reason']}",
                "execution_authority": False,
            }
    if entity is None:
        return {
            "mirrored": False,
            "reason": "event not mapped to catalog EVENT_CLASS",
            "execution_authority": False,
        }
    seed_id = catalog_source_id or find_catalog_seed_source_id(graph)
    if not seed_id:
        return {
            "mirrored": False,
            "reason": "catalog seed source missing; run economic_dynamics_seed first",
            "execution_authority": False,
        }
    reaction_id = graph.record_event_reaction(
        event_source_id=seed_id,
        symbol=str(symbol).upper(),
        actual=actual,
        forecast=forecast,
        previous=previous,
        windows={
            **windows,
            "_mirror_meta": {
                "catalog_version": CATALOG_VERSION,
                "mapped_entity": entity,
                "routing_mode": routing_mode,
                "runtime_title": _event_title(event)[:200],
                "surprise_method": "CONSENSUS_DELTA" if forecast is not None else "PREVIOUS_DELTA",
                "execution_authority": False,
            },
        },
        source_quality=str(source_quality or "RUNTIME_FEED_MIRROR")[:200],
    )
    return {
        "mirrored": True,
        "reaction_id": int(reaction_id),
        "catalog_source_id": seed_id,
        "mapped_entity": entity,
        "routing_mode": routing_mode,
        "execution_authority": False,
    }


def mirror_recent_reactions(
    graph: MacroGraph,
    *,
    symbol: str,
    news_events: Sequence[Dict[str, Any]],
    recent_reactions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Mirror completed runtime reactions that still carry windows.

    Each news row's per-article triage verdict (``row["triage"]["r1"]``, the
    R1_headline_event_routing class) extends event routing: when the lexical
    title-token map misses, the model-routed class is used after
    post-validation against the lexical router (agreement or abstention
    accepts; contradiction fails closed).
    """
    mirrored: List[Dict[str, Any]] = []
    skipped = 0
    r1_extended = 0
    r1_rejected = 0
    skip_reasons: Dict[str, int] = {}
    seed_id = find_catalog_seed_source_id(graph)

    def _skip(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    # A reaction row carries actual/forecast/windows but no headline, so the
    # synthesized fallback below classified an empty string and missed every
    # time. Its source row already holds a descriptive title.
    source_titles: Dict[str, str] = {}
    _sids = {
        str(r.get("event_source_id") or "")
        for r in (recent_reactions or [])
        if isinstance(r, dict)
    } - {""}
    if _sids:
        marks = ",".join("?" * len(_sids))
        with graph._con() as con:
            for row in con.execute(
                f"SELECT source_id,title FROM macro_sources WHERE source_id IN ({marks})",
                tuple(_sids),
            ):
                source_titles[str(row["source_id"])] = str(row["title"] or "")

    # Index news by rough identity for title classification.
    for reaction in recent_reactions or []:
        if not isinstance(reaction, dict):
            _skip("malformed_row")
            continue
        windows = reaction.get("windows")
        if isinstance(windows, str):
            try:
                windows = json.loads(windows)
            except (TypeError, ValueError, json.JSONDecodeError):
                windows = {}
        if not isinstance(windows, dict):
            _skip("unmeasurable_windows")
            continue
        # Prefer matching news row; else synthesize minimal event from reaction.
        event = {
            "actual": reaction.get("actual"),
            "forecast": reaction.get("forecast"),
            "previous": reaction.get("previous"),
            "event": (
                reaction.get("event")
                or reaction.get("title")
                or source_titles.get(str(reaction.get("event_source_id") or ""), "")
            ),
        }
        matched_news = None
        for news in news_events or []:
            if not isinstance(news, dict):
                continue
            if (
                _finite_number(news.get("actual")) == _finite_number(reaction.get("actual"))
                and _finite_number(news.get("forecast")) == _finite_number(reaction.get("forecast"))
            ):
                event = news
                matched_news = news
                break
        # Per-article R1 routing: the matched news row's triage verdict lets
        # model-routed headlines extend the mirroring path. Only concrete
        # EVENT_* classes are forwarded — an abstained R1 (INSUFFICIENT_DATA /
        # NOT_EVENT_NOISE) is no routing opinion, so there is nothing to
        # post-validate and the row falls back to legacy behavior.
        r1_verdict = None
        if isinstance(matched_news, dict):
            triage = matched_news.get("triage") or {}
            r1_value = str(triage.get("r1") or "").strip().upper()
            if r1_value.startswith("EVENT_"):
                r1_verdict = {"r1": r1_value, "article": matched_news}
        result = mirror_runtime_reaction_to_catalog(
            graph,
            event=event,
            symbol=symbol,
            windows=windows,
            catalog_source_id=seed_id,
            r1_verdict=r1_verdict,
        )
        if result.get("mirrored"):
            mirrored.append(result)
            if str(result.get("routing_mode", "")).startswith("model_r1:"):
                r1_extended += 1
        elif "r1 post-validation rejected" in str(result.get("reason", "")):
            r1_rejected += 1
            _skip("r1_rejected")
        else:
            _skip(str(result.get("reason") or "unclassified"))
    return {
        "mirrored_count": len(mirrored),
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "r1_model_extended": r1_extended,
        "r1_rejected": r1_rejected,
        "mirrored": mirrored[:20],
        "catalog_source_id": seed_id,
        "execution_authority": False,
    }


def run_catalog_measurement_pass(
    graph: MacroGraph,
    *,
    validator: Optional[AutonomousCausalValidator] = None,
) -> Dict[str, Any]:
    """Auto-validate CANDIDATE catalog edges after reaction mirroring."""
    validator = validator or AutonomousCausalValidator(graph)
    validated = validator.auto_validate_all_candidates()
    approved = [row for row in validated if row.get("approved")]
    insufficient = [row for row in validated if row.get("status") == "INSUFFICIENT_SAMPLE"]
    return {
        "validated_count": len(validated),
        "approved_count": len(approved),
        "insufficient_sample_count": len(insufficient),
        "approved_edge_ids": [row.get("edge_id") for row in approved],
        "event_templates": len(EVENT_TEMPLATES),
        "execution_authority": False,
        "status": "MEASUREMENT_PASS_COMPLETE",
    }
