"""Per-article R1 event-class verdicts extending the runtime→catalog mirror path.

Model-routed headlines (R1_headline_event_routing from triage) extend
mirror_recent_reactions beyond the lexical title-token map, post-validated
against the lexical router: agreement or lexical abstention accepts,
contradiction fails closed.
"""
from __future__ import annotations

import pytest

from economic_dynamics_measure import (
    _R1_ENTITY_MAP,
    classify_runtime_event,
    mirror_recent_reactions,
    mirror_runtime_reaction_to_catalog,
    post_validate_r1_routing,
)
from economic_dynamics_seed import seed_economic_dynamics
from macro_graph import MacroGraph


def _seeded_graph(tmp_path):
    graph = MacroGraph(str(tmp_path / "mirror.db"))
    seed = seed_economic_dynamics(graph=graph)
    assert seed["created_count"] > 0
    return graph, seed["source_id"]


def _windows():
    return {"15m": {"return_fraction": 0.002}}


# ---------------------------------------------------------------------------
# post_validate_r1_routing: the lexical cross-check doctrine
# ---------------------------------------------------------------------------

def test_lexical_agreement_accepts():
    article = {"title": "US CPI m/m jumps to 3.5%"}
    check = post_validate_r1_routing(article, "EVENT_CPI")
    assert check["accepted"] is True
    assert check["mode"] == "lexical_agreement"


def test_model_extension_accepted_when_lexical_abstains():
    # No title-token map hits, but the model routes it as a CB meeting.
    article = {"title": "Officials deliver surprise 50bp cut after closed-door session"}
    check = post_validate_r1_routing(article, "EVENT_CB_MEETING")
    assert check["accepted"] is True
    assert check["mode"] == "model_extension"


def test_lexical_contradiction_fails_closed():
    article = {"title": "US CPI m/m jumps to 3.5%"}  # lexically EVENT_CPI
    check = post_validate_r1_routing(article, "EVENT_NFP")
    assert check["accepted"] is False
    assert check["mode"] == "lexical_contradiction"


def test_non_event_class_rejected():
    check = post_validate_r1_routing({"title": "Anything"}, "NOT_EVENT_NOISE")
    assert check["accepted"] is False
    assert check["mode"] == "non_event"
    check = post_validate_r1_routing({"title": "Anything"}, "INSUFFICIENT_DATA")
    assert check["accepted"] is False


def test_empty_article_rejected():
    check = post_validate_r1_routing({"title": "  "}, "EVENT_CPI")
    assert check["accepted"] is False
    assert check["mode"] == "no_title"


def test_r1_entity_map_covers_all_concrete_classes():
    from news_triage import R1_HeadlineEventRouting

    concrete = {
        m.value for m in R1_HeadlineEventRouting
        if m.value.startswith("EVENT_")
    }
    assert set(_R1_ENTITY_MAP) == concrete
    # Every mapped entity is a real catalog EVENT_CLASS used by the token map.
    from economic_dynamics_measure import _EVENT_TOKEN_MAP

    catalog_entities = {entity for _, entity in _EVENT_TOKEN_MAP}
    assert set(_R1_ENTITY_MAP.values()) == catalog_entities


# ---------------------------------------------------------------------------
# Single-event mirror: extension path without breaking the lexical contract
# ---------------------------------------------------------------------------

def test_lexical_path_reports_lexical_mode(tmp_path):
    graph, seed_id = _seeded_graph(tmp_path)
    result = mirror_runtime_reaction_to_catalog(
        graph,
        event={"event": "US CPI m/m", "actual": 3.5, "forecast": 3.1},
        symbol="EURUSD",
        windows=_windows(),
        catalog_source_id=seed_id,
    )
    assert result["mirrored"] is True
    assert result["mapped_entity"] == "CPI_RELEASE"
    assert result["routing_mode"] == "lexical_title"


def test_model_r1_extension_mirrors_unmappable_title(tmp_path):
    graph, seed_id = _seeded_graph(tmp_path)
    event = {
        "event": "Dollar surges as officials deliver surprise 50bp cut",
        "actual": 4.50,
        "forecast": 4.25,
    }
    assert classify_runtime_event(event) is None  # lexical router misses
    article = {"title": event["event"]}
    result = mirror_runtime_reaction_to_catalog(
        graph,
        event=event,
        symbol="EURUSD",
        windows=_windows(),
        catalog_source_id=seed_id,
        r1_verdict={"r1": "EVENT_CB_MEETING", "article": article},
    )
    assert result["mirrored"] is True
    assert result["mapped_entity"] == "CB_MEETING"
    assert result["routing_mode"] == "model_r1:model_extension"


def test_r1_contradiction_blocks_mirror(tmp_path):
    graph, seed_id = _seeded_graph(tmp_path)
    event = {"event": "Price shock waves through trading desks",
             "actual": 1.0, "forecast": 0.5}
    assert classify_runtime_event(event) is None
    result = mirror_runtime_reaction_to_catalog(
        graph,
        event=event,
        symbol="EURUSD",
        windows=_windows(),
        catalog_source_id=seed_id,
        r1_verdict={"r1": "EVENT_NFP", "article": {"title": "US CPI m/m spikes"}},
    )
    assert result["mirrored"] is False
    assert "r1 post-validation rejected" in result["reason"]


def test_no_r1_verdict_behaves_exactly_as_before(tmp_path):
    graph, seed_id = _seeded_graph(tmp_path)
    event = {"event": "Unmapped noise headline", "actual": 1.0, "forecast": 0.5}
    result = mirror_runtime_reaction_to_catalog(
        graph,
        event=event,
        symbol="EURUSD",
        windows=_windows(),
        catalog_source_id=seed_id,
    )
    assert result["mirrored"] is False
    assert result["reason"] == "event not mapped to catalog EVENT_CLASS"


# ---------------------------------------------------------------------------
# mirror_recent_reactions: per-article triage verdicts thread through
# ---------------------------------------------------------------------------

def _reaction(actual, forecast, title):
    return {
        "actual": actual,
        "forecast": forecast,
        "previous": None,
        "event": title,
        "windows": _windows(),
    }


def test_recent_reactions_extended_by_model_r1(tmp_path):
    graph, _ = _seeded_graph(tmp_path)
    title = "Dollar surges as officials deliver surprise 50bp cut"
    news = [{
        "actual": 4.50,
        "forecast": 4.25,
        "title": title,
        "triage": {"r1": "EVENT_CB_MEETING", "action": "INGEST"},
    }]
    result = mirror_recent_reactions(
        graph,
        symbol="EURUSD",
        news_events=news,
        recent_reactions=[_reaction(4.50, 4.25, title)],
    )
    assert result["mirrored_count"] == 1
    assert result["r1_model_extended"] == 1
    assert result["r1_rejected"] == 0
    assert result["mirrored"][0]["routing_mode"].startswith("model_r1:")
    assert result["execution_authority"] is False


def test_recent_reactions_lexical_path_still_reports_clean(tmp_path):
    graph, _ = _seeded_graph(tmp_path)
    news = [{
        "actual": 3.5,
        "forecast": 3.1,
        "title": "US CPI m/m",
        "triage": {"r1": "EVENT_CPI", "action": "INGEST"},
    }]
    result = mirror_recent_reactions(
        graph,
        symbol="EURUSD",
        news_events=news,
        recent_reactions=[_reaction(3.5, 3.1, "US CPI m/m")],
    )
    assert result["mirrored_count"] == 1
    assert result["r1_model_extended"] == 0
    assert result["mirrored"][0]["routing_mode"] == "lexical_title"


def test_recent_reactions_contradiction_counts_as_rejected(tmp_path):
    graph, _ = _seeded_graph(tmp_path)
    # 'Jobless' is in the R1 token map but NOT the catalog token map, so the
    # lexical catalog path abstains and the post-validation binds: the
    # article lexically routes NFP while the model head said CB_MEETING.
    title = "Jobless claims spike unexpectedly"
    news = [{
        "actual": 4.50,
        "forecast": 4.25,
        "title": title,
        "triage": {"r1": "EVENT_CB_MEETING", "action": "INGEST"},
    }]
    assert classify_runtime_event({"event": title}) is None  # catalog map abstains
    result = mirror_recent_reactions(
        graph,
        symbol="EURUSD",
        news_events=news,
        recent_reactions=[_reaction(4.50, 4.25, title)],
    )
    assert result["mirrored_count"] == 0
    assert result["r1_rejected"] == 1
    assert result["skipped"] == 1


def test_recent_reactions_without_triage_is_backward_compatible(tmp_path):
    graph, _ = _seeded_graph(tmp_path)
    news = [{"actual": 3.5, "forecast": 3.1, "title": "Unmapped noise headline"}]
    result = mirror_recent_reactions(
        graph,
        symbol="EURUSD",
        news_events=news,
        recent_reactions=[_reaction(3.5, 3.1, "Unmapped noise headline")],
    )
    assert result["mirrored_count"] == 0
    assert result["r1_model_extended"] == 0
    assert result["r1_rejected"] == 0


def test_recent_reactions_r1_insufficient_data_ignored(tmp_path):
    """Abstained R1 routing must not extend or reject anything."""
    graph, _ = _seeded_graph(tmp_path)
    title = "Unmapped noise headline"
    news = [{
        "actual": 3.5, "forecast": 3.1, "title": title,
        "triage": {"r1": "INSUFFICIENT_DATA", "action": "TAG"},
    }]
    result = mirror_recent_reactions(
        graph,
        symbol="EURUSD",
        news_events=news,
        recent_reactions=[_reaction(3.5, 3.1, title)],
    )
    assert result["mirrored_count"] == 0
    assert result["r1_rejected"] == 0
