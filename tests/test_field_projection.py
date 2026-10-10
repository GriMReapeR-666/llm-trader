"""Field-projection consolidation: one shared contract behind both projectors.

``news_triage.project_article`` owns the Layer-0 field shape; the realtime
``normalize_article`` (validated-row wrapper) and the narrative
``_normalize_narrative_row`` (alias resolution + selective merge) must
produce identical contract fields for identical (url, title), so the triage
gate cannot observe which collection path a row came from.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))

from data.news_triage import TriageGate, project_article  # noqa: E402
from data.realtime_news_ingest import normalize_article  # noqa: E402
from data.data_source_pipeline import _normalize_narrative_row  # noqa: E402

URL = "https://www.reuters.com/world/us/proj-1"
TITLE = "Fed holds rates steady at 4.25 percent"
BODY = "The FOMC held the policy rate at 4.25 percent, matching consensus."

CONTRACT_FIELDS = (
    "title", "url", "publisher", "text_or_summary",
    "dedupe_key", "source_policy", "event_class", "execution_authority",
)


def _realtime_row():
    return normalize_article(
        title=TITLE, url=URL, publisher="Reuters", provider="rss", text_or_summary=BODY,
    )


def _narrative_row():
    row = {
        "event": TITLE, "description": BODY, "url": URL, "source": "Reuters",
        "sentiment": "neutral", "symbol": "SPX",
    }
    _normalize_narrative_row(row, provider="newsapi")
    return row


def test_realtime_and_narrative_rows_share_contract_fields():
    rt, nar = _realtime_row(), _narrative_row()
    for k in CONTRACT_FIELDS:
        assert rt.get(k) == nar.get(k), f"contract field {k} diverges: {rt.get(k)!r} != {nar.get(k)!r}"


def test_dedupe_keys_identical_across_paths_and_shared_owner():
    expected = project_article(title=TITLE, url=URL)["dedupe_key"]
    assert _realtime_row()["dedupe_key"] == expected
    assert _narrative_row()["dedupe_key"] == expected


def test_narrative_merge_preserves_provider_extras():
    nar = _narrative_row()
    assert nar["sentiment"] == "neutral"
    assert nar["symbol"] == "SPX"


def test_narrative_custom_dedupe_key_survives():
    row = {"title": "T", "url": URL, "dedupe_key": "caller-key"}
    _normalize_narrative_row(row, provider="newsapi")
    assert row["dedupe_key"] == "caller-key"


def test_narrative_aliases_resolve_then_project():
    row = {"event": TITLE, "summary": BODY, "article_url": URL, "source": "Reuters"}
    _normalize_narrative_row(row, provider="newsapi")
    assert row["title"] == TITLE
    assert row["text_or_summary"] == BODY
    assert row["publisher"] == "Reuters"
    assert row["dedupe_key"] == project_article(title=TITLE, url=URL)["dedupe_key"]
    assert row["source_policy"]["allowed"] is True


def test_narrative_published_at_passthrough_without_now_default():
    stamped = {"title": TITLE, "url": URL, "published_at": "2026-09-01T00:00:00Z"}
    _normalize_narrative_row(stamped, provider="newsapi")
    assert stamped["published_at"] == "2026-09-01T00:00:00Z"
    unstamped = {"title": TITLE, "url": URL}
    _normalize_narrative_row(unstamped, provider="newsapi")
    assert unstamped.get("published_at", "") == ""  # narrative rows never invent a stamp


def test_realtime_wrapper_rejects_empty_title_and_defaults():
    assert normalize_article(title="   ") is None
    row = normalize_article(title=TITLE, url=URL, provider="rss")
    assert row["text_or_summary"] == TITLE  # body falls back to title
    assert row["published_at"]  # defaulted to now
    assert row["provider"] == "rss"


def test_gate_triages_identical_rows_identically_across_paths():
    """The whole point: the shared gate must treat same-content rows from
    different collection paths as the same article (D0_2 collision) with
    identical quality verdicts."""
    rt, nar = _realtime_row(), _narrative_row()
    gate = TriageGate(triage_enabled=True)
    gate.apply([rt, nar])
    assert gate.rejected == 0
    v1, v2 = rt["triage"], nar["triage"]
    assert v1["d0_2"] == "FIRST_REPORT"
    assert v2["d0_2"] == "STALE_REPEAT"  # cross-path duplicate detected via shared key
    # Quality fields identical; the repeat is TAGged (down-weight) per D0_2
    # doctrine — same gate semantics on both paths, only the repeat differs.
    for k in ("d0_1", "d0_3"):
        assert v1[k] == v2[k], f"verdict field {k} diverges across paths: {v1[k]!r} != {v2[k]!r}"
    assert v1["action"] == "INGEST" and v2["action"] == "TAG"
