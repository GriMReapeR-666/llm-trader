"""ALFRED point-in-time release harvesting.

ALFRED is the only viable source of release outcomes in this repo: the economic
calendar publishes forward-looking rows only and never populates ``actual``
(see docs/CALENDAR_ACTUAL_GAP.md), so ``MacroGraph.schedule_event_reaction``
cannot start a tracker and the causal validator accumulates nothing.

The properties that matter, and which these tests pin:

* point-in-time integrity -- the *first print* is used, not the revision
* a release is never dated outside the window it was harvested for
* FRED's '.' gap placeholder is never read as a zero-valued observation
* no consensus forecast is invented; ``forecast`` stays ``None``
* ``realtime_end`` is clamped into the past, because FRED rejects today with
  HTTP 400 and a local clock running ahead of FRED's silently harvests nothing
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from data.alfred_provider import (
    ALFREDProvider,
    DEFAULT_SERIES,
    SERIES_TITLES,
    _finite,
    _safe_realtime_end,
    default_realtime_window,
    vintage_key,
)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _StubHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params or {})
        return self.responses.pop(0) if self.responses else _Resp(200, {"observations": []})


@pytest.fixture()
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    return ALFREDProvider(cache_path=str(tmp_path / "alfred.json"))


# --- value parsing ----------------------------------------------------------

@pytest.mark.parametrize("raw", [".", "", "-", "NaN", None, "not-a-number", float("nan")])
def test_fred_gap_tokens_are_not_observations(raw):
    assert _finite(raw) is None


@pytest.mark.parametrize("raw,expected", [("1.25", 1.25), ("-0.04", -0.04), (3, 3.0)])
def test_real_values_parse(raw, expected):
    assert _finite(raw) == pytest.approx(expected)


# --- realtime window clamping ----------------------------------------------

def test_default_window_ends_in_the_past():
    _, end = default_realtime_window(3)
    assert end < datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_future_realtime_end_is_clamped():
    """FRED 400s on realtime_end >= today; a late local clock must not zero output."""
    far = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    clamped = _safe_realtime_end(far)
    assert clamped < datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_today_is_clamped_back_one_day():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert _safe_realtime_end(today) < today


def test_past_value_is_preserved():
    assert _safe_realtime_end("2024-03-01") == "2024-03-01"


def test_malformed_end_yields_none():
    assert _safe_realtime_end("not-a-date") is None
    assert _safe_realtime_end("") is None
    assert _safe_realtime_end(None) is None


# --- transport --------------------------------------------------------------

def test_no_credentials_returns_empty_rather_than_raising(provider, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    provider.last_source = "x"
    assert provider.fetch_vintages("CPIAUCSL", realtime_start="2024-01-01") == []
    assert provider.last_source == "no_credentials"


def test_http_error_is_reported_not_raised(provider, monkeypatch):
    monkeypatch.setattr("data.alfred_provider._requests",
                        _StubHTTP([_Resp(400, {"error_message": "bad"})]))
    assert provider.fetch_vintages("CPIAUCSL", realtime_start="2024-01-01") == []
    assert provider.last_source == "http_400"


def test_bad_json_is_reported_not_raised(provider, monkeypatch):
    class Boom:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr("data.alfred_provider._requests", _StubHTTP([Boom()]))
    assert provider.fetch_vintages("CPIAUCSL", realtime_start="2024-01-01") == []
    assert provider.last_source == "json_failed"


def test_observation_window_is_sent(provider, monkeypatch):
    """A bare vintage window returns every period since 1947."""
    stub = _StubHTTP([_Resp(200, {"observations": []})])
    monkeypatch.setattr("data.alfred_provider._requests", stub)
    provider.fetch_vintages("CPIAUCSL", realtime_start="2024-01-01",
                            realtime_end="2024-06-01",
                            observation_start="2024-01-01", observation_end="2024-06-01")
    assert stub.calls[0]["observation_start"] == "2024-01-01"
    assert stub.calls[0]["observation_end"] == "2024-06-01"


# --- point-in-time integrity ------------------------------------------------

def _obs(period, published, value):
    return {"series_id": "X", "period": period, "published_at": published, "value": value}


def test_first_publication_wins_over_revision(provider):
    rows = [
        _obs("2024-01-01", "2024-02-14", 300.5),   # first print
        _obs("2024-01-01", "2024-03-14", 301.0),   # revision
        _obs("2024-02-01", "2024-03-14", 302.0),
    ]
    firsts = provider.first_publication(rows)
    by_period = {r["period"]: r["value"] for r in firsts}
    assert by_period["2024-01-01"] == 300.5
    assert by_period["2024-02-01"] == 302.0


def test_first_publication_is_order_independent(provider):
    rows = [
        _obs("2024-01-01", "2024-03-14", 301.0),
        _obs("2024-01-01", "2024-02-14", 300.5),
    ]
    assert provider.first_publication(rows)[0]["value"] == 300.5


def test_releases_never_dated_outside_the_window(provider, monkeypatch):
    monkeypatch.setattr(
        "data.alfred_provider._requests",
        _StubHTTP([_Resp(200, {"observations": [
            {"date": "2024-01-01", "realtime_start": "2024-02-14", "value": "300.5"},
            {"date": "2019-01-01", "realtime_start": "2019-02-15", "value": "250.0"},
        ]})]),
    )
    releases = provider.build_releases(
        "CPIAUCSL", entity_class="CPI_RELEASE",
        realtime_start="2024-01-01", realtime_end="2024-12-31")
    assert len(releases) == 1
    assert releases[0]["period"] == "2024-01-01"


# --- honesty about what is and is not observed -----------------------------

def test_forecast_is_never_invented(provider, monkeypatch):
    monkeypatch.setattr(
        "data.alfred_provider._requests",
        _StubHTTP([_Resp(200, {"observations": [
            {"date": "2024-01-01", "realtime_start": "2024-02-14", "value": "300.5"},
            {"date": "2024-02-01", "realtime_start": "2024-03-14", "value": "301.6"},
        ]})]),
    )
    releases = provider.build_releases(
        "CPIAUCSL", entity_class="CPI_RELEASE",
        realtime_start="2024-01-01", realtime_end="2024-12-31")
    assert all(r["forecast"] is None for r in releases)
    assert all(r["execution_authority"] is False for r in releases)


def test_previous_chains_from_the_prior_release(provider, monkeypatch):
    monkeypatch.setattr(
        "data.alfred_provider._requests",
        _StubHTTP([_Resp(200, {"observations": [
            {"date": "2024-01-01", "realtime_start": "2024-02-14", "value": "300.5"},
            {"date": "2024-02-01", "realtime_start": "2024-03-14", "value": "301.6"},
        ]})]),
    )
    releases = provider.build_releases(
        "CPIAUCSL", entity_class="CPI_RELEASE",
        realtime_start="2024-01-01", realtime_end="2024-12-31")
    assert releases[0]["previous"] is None
    assert releases[1]["previous"] == pytest.approx(300.5)


def test_harvest_declares_no_forecast_source(provider, monkeypatch):
    monkeypatch.setattr("data.alfred_provider._requests",
                        _StubHTTP([_Resp(200, {"observations": []})]))
    report = provider.harvest(realtime_start="2024-01-01")
    assert report["forecast_source"] is None
    assert report["execution_authority"] is False
    assert "ALFRED carries no consensus" in report["note"]


def test_one_failing_series_does_not_stop_the_rest(provider, monkeypatch):
    calls = {"n": 0}

    class Flaky:
        status_code = 200

        def json(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return {"observations": [
                {"date": "2024-01-01", "realtime_start": "2024-02-14", "value": "1.0"}]}

    monkeypatch.setattr("data.alfred_provider._requests",
                        _StubHTTP([Flaky() for _ in range(10)]))
    report = provider.harvest(realtime_start="2024-01-01",
                              series={"CPIAUCSL": "CPI_RELEASE",
                                      "PAYEMS": "NFP_RELEASE"})
    assert report["errors"], "the failing series should be recorded, not swallowed"
    assert report["release_count"] >= 1


# --- classification bridge --------------------------------------------------

def test_every_default_series_has_a_classifiable_title():
    from intelligence.economic_dynamics_measure import classify_runtime_event

    for series_id, entity_class in DEFAULT_SERIES.items():
        title = SERIES_TITLES.get(series_id, series_id)
        assert classify_runtime_event({"event": title}) == entity_class, (
            f"{series_id} title {title!r} does not route to {entity_class}")


def test_release_identity_is_stable():
    a = {"series_id": "X", "period": "2024-01-01", "published_at": "2024-02-14"}
    b = {"series_id": "X", "period": "2024-01-01", "published_at": "2024-02-14"}
    c = {"series_id": "X", "period": "2024-02-01", "published_at": "2024-03-14"}
    assert vintage_key(a) == vintage_key(b)
    assert vintage_key(a) != vintage_key(c)


# --- cache ------------------------------------------------------------------

def test_cache_roundtrip(provider, monkeypatch, tmp_path):
    monkeypatch.setattr("data.alfred_provider._requests",
                        _StubHTTP([_Resp(200, {"observations": []})]))
    provider.harvest(realtime_start="2024-01-01")
    loaded = provider.load_cache()
    assert loaded is not None
    assert loaded["realtime_start"] == "2024-01-01"


def test_unreadable_cache_returns_none(provider):
    assert provider.load_cache() is None
    with open(provider.cache_path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert provider.load_cache() is None
