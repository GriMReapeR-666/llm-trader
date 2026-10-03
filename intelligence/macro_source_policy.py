"""Allowlist and retention metadata for macro research sources."""
from __future__ import annotations

from urllib.parse import urlparse
from typing import Any, Dict, Set


PRIMARY_DOMAINS = {
    "federalreserve.gov": "central-bank-release",
    "ecb.europa.eu": "central-bank-release",
    "data.ecb.europa.eu": "central-bank-release",
    "bankofengland.co.uk": "central-bank-release",
    "bankofcanada.ca": "central-bank-release",
    "boj.or.jp": "central-bank-release",
    "fred.stlouisfed.org": "official-statistics",
    "data.imf.org": "official-statistics",
    "bis.org": "official-statistics",
    "ec.europa.eu": "official-statistics",
    "home.treasury.gov": "official-statistics",
    "treasury.gov": "official-statistics",
    "usda.gov": "official-statistics",
    "bls.gov": "official-statistics",
    "ssrn.com": "academic-research",
    "papers.ssrn.com": "academic-research",
    "arxiv.org": "academic-research",
    "export.arxiv.org": "academic-research",
}

# Runtime news / research feeds: candidate evidence only (never order authority).
RUNTIME_DOMAINS = {
    "reuters.com": "news-wire",
    "feeds.reuters.com": "news-wire",
    "www.reuters.com": "news-wire",
    "seekingalpha.com": "research-wire",
    "www.seekingalpha.com": "research-wire",
    "newsapi.org": "news-aggregator",
    "bloomberg.com": "news-wire",
    "www.bloomberg.com": "news-wire",
    "feeds.bloomberg.com": "news-wire",
    "yahoo.com": "news-wire",
    "finance.yahoo.com": "news-wire",
    "feeds.finance.yahoo.com": "news-wire",
}

# Explicitly never auto-trusted for causal promotion.
BLOCKED_SOCIAL_DOMAINS: Set[str] = {
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "truthsocial.com",
    "www.truthsocial.com",
    "t.co",
}

RUNTIME_PUBLISHER_TOKENS = (
    "mt5", "broker", "official calendar", "runtime calendar", "calendar provider",
    "cboe", "finra", "nasdaq", "econdb", "stockgrid", "portfolio123", "seeking_alpha",
    "seeking alpha", "reuters", "newsapi", "fred", "exness", "yahoo", "yahoo finance",
    "bloomberg",
)



def _host(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.hostname.lower().split(":")[0] if parsed.hostname else ""
    except (TypeError, ValueError):
        return ""


def assess_source(url: str = "", publisher: str = "", rights_note: str = "") -> Dict[str, Any]:
    # Provider payloads are untrusted.  Normalize only strings and make every
    # malformed identity unverified rather than allowing urlparse/type errors
    # to interrupt the trading cycle or accidentally approve a source.
    url = url if isinstance(url, str) else ""
    publisher = publisher if isinstance(publisher, str) else ""
    rights_note = rights_note if isinstance(rights_note, str) else ""
    host = _host(url)

    if host in BLOCKED_SOCIAL_DOMAINS or any(
        host == domain or host.endswith("." + domain)
        for domain in BLOCKED_SOCIAL_DOMAINS
    ):
        return {
            "allowed": False,
            "tier": "UNVERIFIED",
            "domain": host,
            "category": "social_unverified",
            "retention": rights_note or "social posts are not causal authority; store alert only after operator review",
        }

    matched = next(
        (domain for domain in PRIMARY_DOMAINS if host == domain or host.endswith("." + domain)),
        None,
    )
    if matched:
        return {
            "allowed": True,
            "tier": "PRIMARY",
            "domain": matched,
            "category": PRIMARY_DOMAINS[matched],
            "retention": rights_note or "store factual payload/hash and citation; do not republish protected text",
        }

    runtime_matched = next(
        (domain for domain in RUNTIME_DOMAINS if host == domain or host.endswith("." + domain)),
        None,
    )
    if runtime_matched:
        return {
            "allowed": True,
            "tier": "RUNTIME_FEED",
            "domain": runtime_matched,
            "category": RUNTIME_DOMAINS[runtime_matched],
            "retention": rights_note or "retain normalized facts, hash, timestamp, and source identifier",
        }

    publisher_l = publisher.lower()
    if publisher and any(token in publisher_l for token in RUNTIME_PUBLISHER_TOKENS):
        # NewsAPI aggregators must still carry an allow-listed article URL when present.
        if "newsapi" in publisher_l and url and not runtime_matched and not matched:
            # Allow NewsAPI rows that cite Reuters/SA/Fed/BLS via URL host above;
            # bare NewsAPI without allow-listed URL stays RUNTIME only if no URL.
            if host and host not in RUNTIME_DOMAINS and not any(
                host == d or host.endswith("." + d) for d in list(PRIMARY_DOMAINS) + list(RUNTIME_DOMAINS)
            ):
                return {
                    "allowed": False,
                    "tier": "UNVERIFIED",
                    "domain": host,
                    "category": "newsapi_unverified_domain",
                    "retention": rights_note or "NewsAPI article domain not allow-listed",
                }
        return {
            "allowed": True,
            "tier": "RUNTIME_FEED",
            "domain": host,
            "category": "broker/calendar-observation",
            "retention": rights_note or "retain normalized facts, hash, timestamp, and source identifier",
        }
    return {
        "allowed": False,
        "tier": "UNVERIFIED",
        "domain": host,
        "category": "unverified",
        "retention": rights_note or "do not use for causal claims until operator review",
    }
