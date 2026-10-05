"""Source-linked historical macro-regime templates.

These are research templates, not precomputed trading signals.  A template
records the regime hypothesis and the primary source that must be used when
the operator loads observations.  It intentionally contains no fabricated
prices, returns, or inferred trade outcomes.  Ingestion remains a separate,
point-in-time operation and every resulting source is kept non-authoritative
for order permission.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    from intelligence.macro_graph import MacroGraph


HISTORICAL_REGIMES: Dict[str, Dict[str, Any]] = {
    "great_inflation_1965_1982": {
        "label": "Great Inflation",
        "date_window": {"start": "1965-01-01", "end": "1982-12-31"},
        "source_url": "https://www.federalreservehistory.org/essays/great-inflation",
        "publisher": "Federal Reserve History",
        "research_questions": [
            "How did inflation, policy-rate changes, energy shocks, and USD trends co-move?",
            "Which observations were available before each event timestamp?",
        ],
        "required_fields": ["observed_at", "inflation", "policy_rate", "instrument", "source_span"],
    },
    "great_recession_2007_2009": {
        "label": "Great Recession / Global Financial Crisis",
        "date_window": {"start": "2007-12-01", "end": "2009-06-30"},
        "source_url": "https://www.federalreservehistory.org/essays/great-recession-of-200709",
        "publisher": "Federal Reserve History",
        "research_questions": [
            "How did liquidity stress and rate cuts change volatility and correlations?",
            "Did the proposed setup survive spread expansion and gap risk?",
        ],
        "required_fields": ["observed_at", "volatility", "liquidity_proxy", "instrument", "source_span"],
    },
    "pandemic_2020": {
        "label": "COVID-19 shock and emergency policy",
        "date_window": {"start": "2020-02-01", "end": "2020-12-31"},
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323b.htm",
        "publisher": "Board of Governors of the Federal Reserve System",
        "research_questions": [
            "How quickly did spreads, volatility, and cross-asset correlation change?",
            "How should pre-release blackout and post-release cooldown be evaluated?",
        ],
        "required_fields": ["observed_at", "event_time", "spread", "volatility", "instrument", "source_span"],
    },
    "inflation_rates_2022": {
        "label": "2022 inflation and tightening cycle",
        "date_window": {"start": "2022-01-01", "end": "2022-12-31"},
        "source_url": "https://www.federalreserve.gov/monetarypolicy/2022-06-mpr-summary.htm",
        "publisher": "Board of Governors of the Federal Reserve System",
        "research_questions": [
            "How did surprise inflation and policy repricing affect USD, gold, and FX regimes?",
            "Which setup labels remained stable across high-volatility windows?",
        ],
        "required_fields": ["observed_at", "inflation_surprise", "policy_rate", "volatility", "instrument", "source_span"],
    },
    "banking_stress_2023": {
        "label": "US regional-bank stress",
        "date_window": {"start": "2023-03-01", "end": "2023-05-31"},
        "source_url": "https://www.fdic.gov/resources/resolutions/bank-failures/failed-bank-list/silicon-valley.html",
        "publisher": "Federal Deposit Insurance Corporation",
        "research_questions": [
            "Did funding/liquidity stress create a distinct safe-haven and correlation regime?",
            "How often would a spread/news gate have prevented adverse entry?",
        ],
        "required_fields": ["observed_at", "event_time", "liquidity_proxy", "spread", "instrument", "source_span"],
    },
}


def _merged_regimes() -> Dict[str, Dict[str, Any]]:
    """Base historical templates plus decade shocks from Economic Dynamics SoT."""
    merged = dict(HISTORICAL_REGIMES)
    try:
        from intelligence.economic_dynamics_catalog import DECADE_SHOCK_TEMPLATES
    except ImportError:
        return merged
    for key, value in DECADE_SHOCK_TEMPLATES.items():
        if key not in merged:
            # Drop catalog-only helper fields that are not required by ingest.
            merged[key] = {
                "label": value["label"],
                "date_window": value["date_window"],
                "source_url": value["source_url"],
                "publisher": value["publisher"],
                "research_questions": list(value.get("research_questions") or []),
                "required_fields": list(value.get("required_fields") or []),
            }
    return merged


def template_registry() -> List[Dict[str, Any]]:
    """Return immutable-ish copies for UI, reports, and research jobs."""
    return [{"regime_id": key, **dict(value), "execution_authority": False,
             "status": "CANDIDATE_SOURCE_TEMPLATE"} for key, value in _merged_regimes().items()]


def get_template(regime_id: str) -> Dict[str, Any]:
    regimes = _merged_regimes()
    if regime_id not in regimes:
        raise KeyError(f"unknown historical regime: {regime_id}")
    return {"regime_id": regime_id, **dict(regimes[regime_id]),
            "execution_authority": False, "status": "CANDIDATE_SOURCE_TEMPLATE"}


def ingest_cited_observation(graph: MacroGraph, regime_id: str, observation: Dict[str, Any], *,
                             source_span: str, source_url: Optional[str] = None,
                             published_at: str = "", version: str = "") -> str:
    """Ingest one operator-verified, point-in-time observation.

    ``source_span`` is mandatory so a narrative cannot be accepted without a
    citation.  All observations are stored as research evidence only.
    """
    template = get_template(regime_id)
    if not source_span.strip():
        raise ValueError("historical observation requires a source_span citation")
    missing = [field for field in template["required_fields"] if field not in observation]
    if missing:
        raise ValueError(f"historical observation missing fields: {', '.join(missing)}")
    payload = {
        "kind": "historical_regime_observation",
        "regime_id": regime_id,
        "date_window": template["date_window"],
        "observation": observation,
        "source_span": source_span,
        "execution_authority": False,
    }
    return graph.ingest_source(
        title=f"{template['label']} — cited observation",
        payload=payload,
        source_url=source_url or template["source_url"],
        publisher=template["publisher"],
        published_at=published_at,
        version=version or "historical-regime-template-v1",
    )


def research_status(graph: MacroGraph) -> Dict[str, Any]:
    """Report templates and source-backed observation counts without scoring trades."""
    with graph._con() as con:  # read-only connection helper owned by MacroGraph
        rows = con.execute(
            "SELECT payload FROM macro_sources WHERE payload LIKE '%historical_regime_observation%'"
        ).fetchall()
    regimes = _merged_regimes()
    return {
        "templates": len(regimes),
        "cited_observations": len(rows),
        "execution_authority": False,
        "status": "READY_FOR_OPERATOR_DATA" if rows else "TEMPLATES_ONLY",
    }

