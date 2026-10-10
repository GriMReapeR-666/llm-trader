"""SOCIAL_EVIDENCE_ONLY tier + social entity ingest (audit remediation 9a).

Regression contract — this is a SAFETY tier, so the tests are written as
tripwires rather than feature checks:

- the causal verdict for every social host is BIT-FOR-BIT what it was before
  the tier existed (same five keys, same values). The tier is a separate
  entrypoint, so ``assess_source`` cannot grow a key that a causal consumer
  might interpret.
- no input combination produces ``causal_authority=True`` or an
  ``allowed_ingest`` outside the configured social domain set.
- publisher strings can never grant admission (host-only gate, same ordering
  as the causal block).
- the ingest path is fail-closed on policy, age, and malformed input, and can
  only write to the non-authoritative local memory sidecar.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from core._import_root import install as install_import_root
install_import_root()
for _pkg in ("core", "intelligence", "operations", "research", "data", "ui"):
    _p = os.path.join(ROOT, _pkg)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _pkg)

from intelligence.macro_source_policy import (  # noqa: E402
    BLOCKED_SOCIAL_DOMAINS, SOCIAL_EVIDENCE_ONLY_DOMAINS,
    assess_source, assess_social_source,
)

SOCIAL_URLS = [
    "https://x.com/POTUS/status/123",
    "https://twitter.com/POTUS/status/123",
    "https://truthsocial.com/@realDonaldTrump",
    "https://www.x.com/POTUS/status/1",
    "https://mobile.twitter.com/POTUS",
    "https://t.co/abc",
]

# The exact pre-tier causal verdict. Any extra or missing key is a regression.
CAUSAL_SOCIAL_VERDICT_KEYS = {"allowed", "tier", "domain", "category", "retention"}
SOCIAL_RETENTION = ("social posts are not causal authority; "
                    "store alert only after operator review")


class TestCausalPathUnchanged:
    @pytest.mark.parametrize("url", SOCIAL_URLS)
    def test_social_verdict_is_bit_for_bit_pre_tier(self, url):
        v = assess_source(url)
        assert set(v) == CAUSAL_SOCIAL_VERDICT_KEYS, f"causal verdict shape drifted: {v}"
        assert v["allowed"] is False
        assert v["tier"] == "UNVERIFIED"
        assert v["category"] == "social_unverified"
        assert v["retention"] == SOCIAL_RETENTION

    @pytest.mark.parametrize("url", SOCIAL_URLS)
    def test_causal_verdict_never_leaks_the_evidence_tier(self, url):
        v = assess_source(url)
        blob = repr(v)
        assert "evidence_only_ingest" not in blob
        assert "allowed_ingest" not in blob
        assert "SOCIAL_EVIDENCE_ONLY" not in blob
        assert "causal_authority" not in blob

    def test_spoofed_publisher_cannot_unblock(self):
        """The causal block fires on host before publisher is consulted."""
        for pub in ("Federal Reserve", "Reuters", "Bloomberg"):
            assert assess_source("https://x.com/POTUS/status/1", pub)["allowed"] is False

    def test_tier_string_is_distinct_from_every_causal_comparison(self):
        """No causal consumer can confuse the evidence tier with PRIMARY /
        RUNTIME_FEED / UNVERIFIED."""
        causal_tiers = {
            assess_source(u)["tier"] for u in SOCIAL_URLS
        } | {"PRIMARY", "RUNTIME_FEED"}
        assert assess_social_source("https://x.com/a/status/1")["tier"] not in causal_tiers
        assert "SOCIAL_EVIDENCE_ONLY" not in causal_tiers

    def test_domain_sets_stay_in_step(self):
        assert BLOCKED_SOCIAL_DOMAINS == SOCIAL_EVIDENCE_ONLY_DOMAINS

    def test_primary_and_runtime_feeds_still_pass(self):
        assert assess_source("https://www.federalreserve.gov/release", "")["allowed"] is True
        assert assess_source("https://www.reuters.com/world/", "Reuters")["allowed"] is True
        assert assess_source("", "runtime calendar")["tier"] == "RUNTIME_FEED"

    def test_non_social_verdicts_untouched(self):
        assert assess_source("https://example.invalid/news", "unknown")["allowed"] is False
        assert assess_source(None, publisher=None, rights_note=None)["allowed"] is False
        assert assess_source("http://[malformed", publisher="Federal Reserve")["allowed"] is False


class TestEvidenceOnlyTier:
    @pytest.mark.parametrize("url", SOCIAL_URLS)
    def test_social_domains_admitted_for_ingest_only(self, url):
        v = assess_social_source(url)
        assert v["allowed_ingest"] is True
        assert v["causal_authority"] is False
        assert v["tier"] == "SOCIAL_EVIDENCE_ONLY"

    def test_spoofed_publisher_cannot_grant_admission(self):
        assert assess_social_source("https://example.invalid/a", "Federal Reserve")["allowed_ingest"] is False
        assert assess_social_source("", "Federal Reserve")["allowed_ingest"] is False

    def test_causal_authority_never_true(self):
        for url, pub in [("https://x.com/POTUS/status/123", "X"),
                         ("https://example.invalid/x", "Federal Reserve"),
                         (None, None), ("http://[malformed", "Reuters"),
                         ("https://www.federalreserve.gov/", "Federal Reserve")]:
            assert assess_social_source(url, publisher=pub)["causal_authority"] is False

    def test_evidence_tier_never_reports_causal_allowed(self):
        assert "allowed" not in assess_social_source("https://x.com/a/status/1")

    @pytest.mark.parametrize("url", [
        "https://example.invalid/article", "", "http://[malformed", None, 12345,
        "https://www.federalreserve.gov/release", "https://twitter.com.evil.invalid/x",
    ])
    def test_non_social_fails_closed(self, url):
        v = assess_social_source(url)
        assert v["allowed_ingest"] is False
        assert v["causal_authority"] is False
        assert v["tier"] == "UNVERIFIED"


# ---------------------------------------------------------------------------
# data.social_entity_ingest behavior
# ---------------------------------------------------------------------------
from data.social_entity_ingest import (  # noqa: E402
    DEFAULT_WATCHLIST, MAX_AGE_HOURS, MAX_POST_TEXT_CHARS,
    classify_velocity, ingest_posts, load_watchlist, sanitize_post,
)


def _hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


_RAW_POTUS = {
    "handle": "@POTUS", "entity": "us_president", "category": "political",
    "url": "https://x.com/POTUS/status/999",
    "text": "We will be imposing a new tariff on steel imports.",
    "posted_at": _hours_ago(0.5), "publisher": "x.com",
}


class TestSanitize:
    def test_valid_post_sanitized_with_invariants(self):
        clean = sanitize_post(_RAW_POTUS)
        assert clean is not None
        assert clean["handle"] == "@POTUS"
        assert clean["causal_authority"] is False
        assert clean["execution_authority"] is False
        assert clean["policy_tier"] == "SOCIAL_EVIDENCE_ONLY"

    def test_sanitize_is_itself_the_gate(self):
        """sanitize_post is the only path into store_post, so it must reject
        anything the policy would not admit."""
        for bad in ({"url": "https://example.invalid/1"}, {"url": ""},
                    {"url": None}, {"url": "http://[malformed"}):
            assert sanitize_post(dict(_RAW_POTUS, **bad)) is None

    def test_non_dict_raw_rejected(self):
        for raw in (None, "text", 42, [], {}):
            assert sanitize_post(raw) is None

    def test_empty_text_rejected(self):
        assert sanitize_post(dict(_RAW_POTUS, text="")) is None

    def test_missing_handle_rejected(self):
        assert sanitize_post(dict(_RAW_POTUS, handle="")) is None

    def test_text_hard_capped(self):
        clean = sanitize_post(dict(_RAW_POTUS, text="x" * 10_000))
        assert clean is not None and len(clean["text"]) == MAX_POST_TEXT_CHARS

    def test_no_eval_exec_surface(self):
        """Untrusted text is stored inert: a code-shaped payload survives as
        text and is never interpreted."""
        payload = "__import__('os').system('echo pwned')"
        clean = sanitize_post(dict(_RAW_POTUS, text=payload))
        assert clean["text"] == payload

    def test_truthsocial_post_also_admissible(self):
        clean = sanitize_post(dict(_RAW_POTUS, url="https://truthsocial.com/@user/posts/5"))
        assert clean is not None and clean["causal_authority"] is False


class TestVelocityAndAgeGate:
    def test_classify_velocity_buckets(self):
        assert classify_velocity({}, 0) == "NONE"
        assert classify_velocity({"a": 1}, 10) == "NOMINAL"
        assert classify_velocity({"a": 3, "b": 9}, 12) == "BURST"      # top 9, share .75
        assert classify_velocity({"a": 4, "b": 8}, 12) == "BURST"      # share .67 >= .5
        assert classify_velocity({"a": 3, "b": 3}, 12) == "ELEVATED"   # top 3, spread out
        assert classify_velocity({"a": 6}, 6) == "BURST"

    @pytest.fixture
    def mem_env(self, tmp_path, monkeypatch):
        env = {"LOCAL_MEMORY_ENABLED": "1",
               "LOCAL_MEMORY_PATH": str(tmp_path / "social_mem.db")}
        monkeypatch.setenv("LOCAL_MEMORY_ENABLED", "1")
        monkeypatch.setenv("LOCAL_MEMORY_PATH", env["LOCAL_MEMORY_PATH"])
        return env

    def test_batch_reports_entity_counts_and_velocity(self, mem_env):
        raws = [
            _RAW_POTUS,
            dict(_RAW_POTUS, url="https://x.com/POTUS/status/1001", text="Second."),
            dict(_RAW_POTUS, url="https://x.com/federalreserve/status/1",
                 entity="fed", handle="@federalreserve", text="Rates held."),
        ]
        r = ingest_posts(raws, env=mem_env)
        assert r["stored"] == 3 and r["rejected"] == 0
        assert r["entity_counts"] == {"us_president": 2, "fed": 1}
        assert r["social_velocity"] in ("NOMINAL", "ELEVATED", "BURST")
        assert all(p["causal_authority"] is False for p in r["posts"])
        assert all(p["entity_mention_count"] >= 1 for p in r["posts"])

    def test_stale_post_dropped_by_max_age(self, mem_env):
        old = dict(_RAW_POTUS, posted_at=_hours_ago(MAX_AGE_HOURS + 5))
        r = ingest_posts([old], env=mem_env)
        assert r["stored"] == 0 and r["rejected"] == 1

    def test_unparseable_or_naive_timestamp_dropped(self, mem_env):
        for ts in ("not-a-date", "", "2026-10-09T12:00:00", None):
            r = ingest_posts([dict(_RAW_POTUS, posted_at=ts)], env=mem_env)
            assert r["stored"] == 0, f"{ts!r} must fail closed"

    def test_rfc2822_pubdate_from_the_rss_bridge_is_accepted(self, mem_env):
        """fetch_via_rss_bridge emits <pubDate>, not ISO. Without this the
        whole fetch -> ingest path silently drops every post."""
        from email.utils import format_datetime
        stamp = format_datetime(datetime.now(timezone.utc) - timedelta(hours=1))
        assert sanitize_post(dict(_RAW_POTUS, posted_at=stamp)) is not None
        r = ingest_posts([dict(_RAW_POTUS, posted_at=stamp)], env=mem_env)
        assert r["stored"] == 1 and r["posts"][0]["age_hours"] < 2

    def test_malformed_rejects_never_raise(self, mem_env):
        r = ingest_posts([None, "not a dict", 42, {}], env=mem_env)
        assert r["stored"] == 0 and r["posts"] == []
        assert r["causal_authority"] is False and r["execution_authority"] is False

    def test_never_writes_a_promotion_path(self, mem_env):
        """The only side effect is the non-authoritative local memory sidecar:
        no setup_labels / verified_memories / patterns writes."""
        import sqlite3
        ingest_posts([_RAW_POTUS], env=mem_env)
        con = sqlite3.connect(mem_env["LOCAL_MEMORY_PATH"])
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        assert "memory_documents" in tables
        assert not (tables & {"setup_labels", "trade_labels",
                              "verified_memories", "patterns", "core_memories"})

    def test_stored_row_carries_the_invariants(self, mem_env):
        import json
        import sqlite3
        ingest_posts([_RAW_POTUS], env=mem_env)
        con = sqlite3.connect(mem_env["LOCAL_MEMORY_PATH"])
        try:
            row = con.execute(
                "SELECT payload_json FROM memory_documents").fetchone()[0]
        finally:
            con.close()
        payload = json.loads(row)
        assert payload["payload"]["causal_authority"] is False
        assert payload["payload"]["execution_authority"] is False
        assert payload["payload"]["policy_tier"] == "SOCIAL_EVIDENCE_ONLY"
        assert payload["doc_key"] if "doc_key" in payload else True


class TestWatchlist:
    def test_watchlist_defaults_present(self):
        watch = load_watchlist(path="nonexistent/watchlist.json")
        handles = {e["handle"] for e in watch}
        assert "@POTUS" in handles
        assert "@federalreserve" in handles
        assert len(watch) >= 5

    def test_defaults_are_well_formed(self):
        for entry in DEFAULT_WATCHLIST:
            assert entry["handle"].startswith("@")
            assert entry["entity"] and entry["category"]

    def test_corrupt_watchlist_falls_back_to_defaults(self, tmp_path):
        bad = tmp_path / "w.json"
        bad.write_text("{not json", encoding="utf-8")
        assert len(load_watchlist(path=str(bad))) == len(DEFAULT_WATCHLIST)