"""The nowcast cache must only be reused by a caller it can actually answer.

``artifacts/alternative_nowcast_cache.json`` holds ONE entry, so every writer
overwrites every other writer's entry.  The reader could only ever REMOVE rows
(stub rows, non-matching tiers) — never put them back.  So a production caller
fetching with ``include_stubs=False`` wrote a stubs-stripped entry, and the next
stubs-inclusive caller was served it for up to ``max_age_s``: the stub-backed
golden-eight sources (AAR carloads, AIS/AISHUB, CFTC COT, ...) simply vanished
from the snapshot it received.

The entry now records the request shape it was collected under, and is reused
only when that shape covers the caller's.  The unsafe direction is forbidden
and the safe one stays a cache hit.
"""
import json

from data.alternative_data_provider import AlternativeDataProvider
from data.alternative_data_registry import golden_eight

T1 = "T1_REALTIME"
T2 = "T2_DAILY"


def _stub_count(snapshot):
    return sum(1 for o in snapshot["observations"] if o.get("status") == "STUB")


def _provider(tmp_path):
    return AlternativeDataProvider(
        cache_path=str(tmp_path / "alternative_nowcast_cache.json")
    )


def _entry(provider):
    with open(provider.cache_path, encoding="utf-8") as fh:
        return json.load(fh)


def test_stripped_entry_is_never_served_to_a_stubs_inclusive_caller(tmp_path):
    """The exact poisoning scenario: False writes, then True reads."""
    provider = _provider(tmp_path)

    full = provider.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
    assert _stub_count(full) > 0, "premise: the registry does return stub rows"

    # A production-style caller strips stubs and becomes the cached entry.
    stripped = provider.fetch_nowcast_snapshot(include_stubs=False, force_refresh=True)
    assert _stub_count(stripped) == 0
    assert _entry(provider)["request_shape"]["include_stubs"] is False

    # A stubs-inclusive caller must now REFETCH rather than reuse that entry.
    recovered = provider.fetch_nowcast_snapshot(include_stubs=True)
    ids = {o["source_id"] for o in recovered["observations"]}
    assert _stub_count(recovered) > 0, (
        "served a stubs-stripped snapshot to a caller that asked for stubs")
    golden = {s.source_id for s in golden_eight()}
    assert golden.issubset(ids), (
        "golden eight missing from a stubs-inclusive snapshot: %s"
        % sorted(golden - ids))


def test_superset_entry_is_reused_as_a_cache_hit(tmp_path):
    """Keying must not throw the cache away: a superset still answers."""
    provider = _provider(tmp_path)
    provider.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
    stamp = _entry(provider)["captured_at"]

    narrower = provider.fetch_nowcast_snapshot(include_stubs=False)

    assert narrower["observations"], "no observations"
    assert _stub_count(narrower) == 0, "include_stubs=False must still strip"
    assert _entry(provider)["captured_at"] == stamp, (
        "the stubs-inclusive entry should have been reused, not overwritten")
    # Reuse is only safe because it is served in its collected shape.
    assert _entry(provider)["request_shape"]["include_stubs"] is True


def test_narrow_tier_entry_does_not_answer_a_wider_request(tmp_path):
    """Same defect on the tiers axis: a subset entry cannot answer 'all'."""
    provider = _provider(tmp_path)
    provider.fetch_nowcast_snapshot(tiers=[T1], include_stubs=True, force_refresh=True)
    assert all(o["tier"] == T1 for o in _entry(provider)["observations"])

    wider = provider.fetch_nowcast_snapshot(include_stubs=True)
    tiers_seen = {o["tier"] for o in wider["observations"]}
    assert tiers_seen != {T1}, (
        "a T1-only entry was served to a caller asking for every tier")


def test_all_tier_entry_answers_a_subset_request(tmp_path):
    provider = _provider(tmp_path)
    provider.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
    stamp = _entry(provider)["captured_at"]

    subset = provider.fetch_nowcast_snapshot(tiers=[T1, T2], include_stubs=True)

    assert subset["observations"]
    assert {o["tier"] for o in subset["observations"]} <= {T1, T2}
    assert _entry(provider)["captured_at"] == stamp, (
        "an all-tier entry should have served a tier-subset request")


def test_entry_without_a_recorded_shape_is_never_reused(tmp_path):
    """Entries written before the key existed are unknown, so they must miss."""
    provider = _provider(tmp_path)
    legacy = {
        # Far-future stamp: if this were served, the age check alone would let
        # it through, proving the shape check is what rejects it.
        "captured_at": "2999-01-01T00:00:00+00:00",
        "observations": [{"source_id": "legacy", "status": "LIVE", "tier": T1}],
        "live_count": 1,
    }
    with open(provider.cache_path, "w", encoding="utf-8") as fh:
        json.dump(legacy, fh)

    snapshot = provider.fetch_nowcast_snapshot(include_stubs=True)
    ids = {o["source_id"] for o in snapshot["observations"]}
    assert "legacy" not in ids, "an unkeyed legacy entry was served"
    assert _entry(provider)["request_shape"]["include_stubs"] is True