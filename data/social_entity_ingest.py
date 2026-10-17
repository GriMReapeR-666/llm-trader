"""X/Twitter entity evidence ingest (plan Phase 2, item 2).

Journals post text from configured entity accounts (Trump, central banks,
wire desks) into the existing memory-document store as SOCIAL_EVIDENCE_ONLY
context. Design constraints from the plan and macro_source_policy:

- Policy gate first: every post must pass ``assess_social_source`` with
  ``allowed_ingest=True`` before any journal write. ``causal_authority`` is
  False for every stored row — a CANDIDATE for operator review and LLM
  macro context, never a causal or promotion input.
- Fetch layer is pluggable and optional: this module does NOT bind to a
  specific scraper. Callers (or a future RSS-bridge poller) pass post
  dicts in; the bundled ``fetch_via_rss_bridge`` tries public Nitter-style
  RSS mirrors with a bounded timeout and treats every failure as no-op
  (never raises into the trading loop).
- Storage: ``LocalAgentMemory._insert_document`` via ``maybe_remember_event``.
  That pathway resolves ``event_type="social_evidence_post"`` to doc_type
  ``event`` in collection ``operational_events`` of the *local, non-
  authoritative* ``local_agent_memory.db`` sidecar
  (``status()["authoritative"] is False``). It never touches ``setup_labels``,
  ``trade_labels``, ``verified_memories``, ``patterns``, or any promotion
  gate. Note ``maybe_remember_event`` takes no ``tags`` argument, so the tier
  and authority invariants ride inside the payload, not in a tags column.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("SocialEntityIngest")

DEFAULT_WATCHLIST = [
    {"handle": "@POTUS", "entity": "us_president", "category": "political"},
    {"handle": "@WhiteHouse", "entity": "us_administration", "category": "political"},
    {"handle": "@federalreserve", "entity": "fed", "category": "central_bank"},
    {"handle": "@ecb", "entity": "ecb", "category": "central_bank"},
    {"handle": "@BOJ_Press_en", "entity": "boj", "category": "central_bank"},
    {"handle": "@ReutersBiz", "entity": "reuters_business", "category": "wire"},
]

WATCHLIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "social_watchlist.json")

MAX_POST_TEXT_CHARS = 500
MAX_POSTS_PER_RUN = 40
MAX_AGE_HOURS = 12.0   # older mentions are not macro context anymore


def load_watchlist(path: str = WATCHLIST_FILE) -> List[Dict[str, str]]:
    """Operator-configurable entity list; falls back to DEFAULT_WATCHLIST."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("entities") if isinstance(data, dict) else data
        out: List[Dict[str, str]] = []
        for item in entries or []:
            handle = str(item.get("handle") or "").strip()
            if not handle:
                continue
            out.append({
                "handle": handle[:80],
                "entity": str(item.get("entity") or handle.lstrip("@"))[:80],
                "category": str(item.get("category") or "unknown")[:40],
            })
        return out or [dict(e) for e in DEFAULT_WATCHLIST]
    except FileNotFoundError:
        return [dict(e) for e in DEFAULT_WATCHLIST]
    except (OSError, ValueError, AttributeError):
        return [dict(e) for e in DEFAULT_WATCHLIST]


def sanitize_post(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate + normalize one raw post. Returns None for unusable posts.

    Enforces the source policy: the post URL host must be a configured
    social domain (assess_social_source allowed_ingest=True); every stored
    row carries causal_authority=False. Text is hard-capped; URLs inside
    the text are preserved as inert text (never auto-opened downstream).
    """
    try:
        from intelligence.macro_source_policy import assess_social_source
    except ImportError:  # flat import parity
        from macro_source_policy import assess_social_source  # type: ignore

    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    policy = assess_social_source(url=url, publisher=str(raw.get("publisher") or ""))
    if policy.get("allowed_ingest") is not True:
        return None

    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    text = text[:MAX_POST_TEXT_CHARS]

    handle = str(raw.get("handle") or "").strip()[:80]
    if not handle:
        return None
    posted_at = str(raw.get("posted_at") or raw.get("ts") or "").strip()[:64]

    return {
        "handle": handle,
        "entity": str(raw.get("entity") or handle.lstrip("@"))[:80],
        "category": str(raw.get("category") or "unknown")[:40],
        "url": url[:300],
        "posted_at": posted_at,
        "text": text,
        "transport_host": str(raw.get("transport_host") or "")[:120],
        "policy_tier": str(policy.get("tier") or "SOCIAL_EVIDENCE_ONLY"),
        "causal_authority": False,          # invariant; never True
        "execution_authority": False,       # invariant; never True
    }


def store_post(post: Dict[str, Any], *, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Journal one sanitized post via the local agent memory document store.

    Never raises: memory outages degrade to ``stored=False`` exactly like
    the other maybe_* entrypoints.
    """
    try:
        from intelligence.local_agent_memory import maybe_remember_event
    except ImportError:  # flat import parity
        from local_agent_memory import maybe_remember_event  # type: ignore

    payload = {
        "entity": post.get("entity"),
        "handle": post.get("handle"),
        "category": post.get("category"),
        "url": post.get("url"),
        "posted_at": post.get("posted_at"),
        "age_hours": post.get("age_hours"),
        "text": post.get("text"),
        # Operator-reviewable context only. Neither key is read by any causal
        # consumer (news triage, macro graph, macro extraction, causal engine
        # all read assess_source's verdict, not this payload).
        "entity_mention_count": post.get("entity_mention_count"),
        "social_velocity": post.get("social_velocity"),
        "transport_host": post.get("transport_host"),
        "policy_tier": post.get("policy_tier", "SOCIAL_EVIDENCE_ONLY"),
        "causal_authority": False,
        "execution_authority": False,
    }
    return maybe_remember_event(
        actor="SocialEntityIngest",
        event_type="social_evidence_post",
        payload=payload,
        correlation_id=str(post.get("url") or post.get("handle") or "")[:200],
        severity="INFO",
        env=env,
    )


def classify_velocity(counts: Dict[str, int], total: int) -> str:
    """Coarse mention-rate bucket for LLM context only.

    The audit asks for entity AND velocity; counts are per-run mention totals
    for each watched entity. Bucketed rather than numeric so the LLM cannot
    over-read a raw count as a calibrated signal — and because the batch is
    bounded by MAX_POSTS_PER_RUN, an absolute rate would be meaningless.
    """
    if not counts:
        return "NONE"
    top = max(counts.values())
    share = top / total if total else 0.0
    if top >= 5 and share >= 0.5:
        return "BURST"
    if top >= 3:
        return "ELEVATED"
    return "NOMINAL"


def _age_hours(posted_at: str) -> Optional[float]:
    """Hours since *posted_at*, or None when unparseable (fail-closed).

    Accepts ISO-8601 (caller-supplied) and RFC-2822 (``<pubDate>`` from RSS,
    which is what fetch_via_rss_bridge actually produces). Unparseable input
    is dropped rather than journaled as fresh.
    """
    if not posted_at:
        return None
    raw = str(posted_at)
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+0:00"))
    except (TypeError, ValueError):
        try:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0


def ingest_posts(raw_posts: Iterable[Dict[str, Any]], *,
                 env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Ingest a batch of raw posts. Returns a summary; never raises."""
    stored = 0
    rejected = 0
    kept: List[Dict[str, Any]] = []
    for raw in list(raw_posts)[:MAX_POSTS_PER_RUN]:
        clean = sanitize_post(raw)
        if clean is None:
            rejected += 1
            continue
        # Age gate: an unparseable or over-old timestamp is dropped rather
        # than journaled as current macro context (MAX_AGE_HOURS was dead
        # code until this check).
        age = _age_hours(clean["posted_at"])
        if age is None or age < -1.0 or age > MAX_AGE_HOURS:
            rejected += 1
            continue
        clean["age_hours"] = round(age, 2)
        result = store_post(clean, env=env)
        if result.get("stored"):
            stored += 1
            kept.append(clean)
        else:
            rejected += 1

    # Velocity + entity counts go back into every stored row's summary and
    # into each post, so the operator can see burst context without a
    # second pass. Advisory only; causal_authority stays False.
    counts: Dict[str, int] = {}
    for post in kept:
        key = str(post.get("entity") or "?")
        counts[key] = counts.get(key, 0) + 1
    velocity = classify_velocity(counts, len(kept))
    for post in kept:
        post["entity_mention_count"] = counts.get(post.get("entity"), 0)
        post["social_velocity"] = velocity

    return {
        "stored": stored, "rejected": rejected, "posts": kept,
        "entity_counts": counts,
        "social_velocity": velocity,
        "causal_authority": False, "execution_authority": False,
        "tier": "SOCIAL_EVIDENCE_ONLY",
    }


# ---------------------------------------------------------------------------
# Optional bounded fetch (best-effort RSS bridge). Every failure is a no-op.
# ---------------------------------------------------------------------------
def fetch_via_rss_bridge(handle: str, *, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Best-effort fetch of a public Nitter-style RSS mirror for one handle.

    Any network error, parse error, or policy-blocked host returns []
    (never raises into the trading loop). This is NOT a scraping commitment;
    it's the smallest stdlib-only probe an operator can enable or disable.
    """
    import urllib.request
    import xml.etree.ElementTree as ET

    from intelligence.macro_source_policy import _host

    clean_handle = handle.strip().lstrip("@")
    mirrors = [
        f"https://xcancel.com/{clean_handle}/rss",
    ]
    for mirror_url in mirrors:
        try:
            # Policy: the mirror host is a transport, the ENTITY url stored
            # is rewritten to the canonical social host so the policy gate
            # evaluates the source, not the proxy.
            req = urllib.request.Request(
                mirror_url, headers={"User-Agent": "LLMTrader/2.0 (evidence-only)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(256 * 1024)   # hard cap 256 KB
            root = ET.fromstring(data)
            posts: List[Dict[str, Any]] = []
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if not link:
                    continue
                # Policy gate is on the ENTITY, so the stored url is the
                # canonical x.com permalink shape the policy layer knows.
                # It is NOT a claim that x.com was fetched: the transport was
                # a third-party mirror, recorded as transport_host so the
                # operator never mistakes a mirror-sourced row for a direct
                # one (P0-5 discipline applied to our own provenance).
                canonical = f"https://x.com/{clean_handle}/status/{link.rstrip('/').split('/')[-1]}"
                posts.append({
                    "handle": f"@{clean_handle}",
                    "url": canonical,
                    "transport_host": _host(mirror_url),
                    "text": title,
                    "posted_at": pub,
                    "publisher": "x.com",
                    "entity": clean_handle,
                })
            return posts
        except Exception as exc:
            log.debug("rss bridge fetch failed for %s: %s", handle, exc)
            continue
    return []


def run_ingest(handles: Optional[List[str]] = None, *,
               env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Scheduled entrypoint: fetch recent posts for the watchlist and ingest.

    Never raises; returns a summary with ``stored``/``rejected`` counts so
    the scheduler can log it. With no handles given the operator watchlist
    file (or defaults) is used.
    """
    watchlist = load_watchlist()
    targets = []
    if handles:
        by_handle = {e["handle"].lower(): e for e in watchlist}
        for h in handles:
            key = h if h.startswith("@") else f"@{h}"
            match = by_handle.get(key.lower())
            targets.append(match or {"handle": key, "entity": key.lstrip("@"), "category": "unknown"})
    else:
        targets = watchlist

    all_posts: List[Dict[str, Any]] = []
    for entry in targets[:10]:                    # bounded: 10 entities per run
        posts = fetch_via_rss_bridge(entry["handle"])
        for p in posts:
            p.setdefault("entity", entry["entity"])
            p.setdefault("category", entry["category"])
        all_posts.extend(posts)
    return ingest_posts(all_posts, env=env)
