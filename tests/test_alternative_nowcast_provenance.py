"""P0-5 / audit item 8: the nowcast journal must not record a placeholder as observed.

``data/alternative_data_provider.py::_fetch_zero_lag`` wrote every zero-lag row
into ``artifacts/alternative_nowcast_cache.json`` with a hardcoded
``status="LIVE"`` — a label it could not have earned, because
``data/zero_lag_macro_provider.py`` computes those numbers from hardcoded
constants (grid 101.4, chokepoint 98.2, crop 74.5, labor -1.2). That journal is
what ``intelligence/macro_observations.py`` persists and what the prompt paths
read, so a constant reached the LLM wearing an observation's clothes.

Pinned here:
- status is DERIVED from the provider's provenance map, never defaulted;
- fail-closed: no map, an unlabelled field, or no label at all -> synthetic;
- one placeholder anywhere in a row demotes the WHOLE row;
- relabelling must not remove a source from any snapshot (no silent drops);
- ``live_count`` stops counting placeholders.

Hermetic: every test injects a fake zero-lag provider, so no network call, no
write to ``artifacts/``, and no dependence on which engine run touched the
shared cache last.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.alternative_data_provider import (  # noqa: E402
    AlternativeDataProvider,
    LIVE_STATUS,
    SYNTHETIC_STATUS,
)
from data.alternative_data_registry import SOURCES, golden_eight  # noqa: E402
from data.zero_lag_macro_provider import (  # noqa: E402
    LIVE,
    SYNTHETIC,
    SyntheticCommoditySpreads,
    ZeroLagMacroSnapshot,
)

# Snapshot field each zero-lag source_id consumes. Mirrors the branch table in
# _fetch_zero_lag. Verified against the live registry: these six are the only
# ones of that table that ``fetch_source`` actually routes to zero-lag.
ROUTED = {
    "grid_load_mw": ("industrial_grid_load_index",),
    "sku_price_scrape": ("daily_cpi_mom_nowcast", "daily_cpi_yoy_nowcast"),
    "sentinel_ndvi": ("satellite_crop_health_score",),
    "ais_vessel_tracking": ("maritime_chokepoint_index",),
    "ais_tanker_tracking": ("maritime_chokepoint_index",),
    "linkup_job_postings": ("labor_hiring_velocity",),
}
# Still real branches in _fetch_zero_lag, but unreachable through fetch_source
# today: money_market_repo's module/provider match nothing, and
# growth_grid_rail_cards needs no key so it never enters the keyless
# zero-lag fallback. Called directly so the branch cannot rot back to LIVE.
DEAD_BRANCH = {
    "money_market_repo": ("net_liquidity_usd_billions",),
    "growth_grid_rail_cards": ("industrial_grid_load_index", "net_liquidity_usd_billions"),
}
# Routes to _fetch_zero_lag and falls through to the whole-snapshot branch.
ELSE_BRANCH = "aar_intermodal_carloads"


def _snapshot(quality):
    return ZeroLagMacroSnapshot(
        timestamp_utc="2026-10-10T00:00:00+00:00",
        net_liquidity_usd_billions=5850.0,
        net_liquidity_bias="NEUTRAL",
        daily_cpi_mom_nowcast=0.22,
        daily_cpi_yoy_nowcast=2.85,
        industrial_grid_load_index=101.4,
        maritime_chokepoint_index=98.2,
        satellite_crop_health_score=74.5,
        labor_hiring_velocity=-1.2,
        synthetic_spreads=SyntheticCommoditySpreads(
            synthetic_ammonia_cost_ton=153.0, corn_soy_price_ratio=2.39,
            corn_soy_regime="EQUILIBRIUM", crack_spread_321_bbl=22.0,
            soybean_crush_spread_bu=1.9, spark_spread_mwh=26.45,
            ethanol_blend_margin_gal=0.2,
        ),
        data_latency_status="ZERO_LAG (0-24h Physical Proxies)",
        execution_authority=False,
        source_quality=dict(quality),
    )


class _FakeZeroLag:
    """Stands in for ZeroLagMacroProvider; ``annotations`` is what it exposes."""

    def __init__(self, quality, *, annotations=None, has_annotations=True):
        self._snapshot = _snapshot(quality)
        if has_annotations:
            self.source_quality_annotations = lambda: dict(self._snapshot.source_quality)
        elif annotations is not None:
            self.source_quality_annotations = lambda: annotations

    def get_snapshot(self):
        return self._snapshot


def _provider(fake, tmp_path):
    alt = AlternativeDataProvider(
        cache_path=str(tmp_path / "alternative_nowcast_cache.json")
    )
    alt._zero_lag = fake
    return alt


def _all_synthetic():
    return {
        "daily_cpi_mom_nowcast": SYNTHETIC,
        "daily_cpi_yoy_nowcast": SYNTHETIC,
        "industrial_grid_load_index": SYNTHETIC,
        "maritime_chokepoint_index": SYNTHETIC,
        "satellite_crop_health_score": SYNTHETIC,
        "labor_hiring_velocity": SYNTHETIC,
        "net_liquidity_usd_billions": SYNTHETIC,
    }


def _all_live():
    return {name: LIVE for name in _all_synthetic()}


class TestStatusIsDerivedFromProvenance:
    def test_placeholder_rows_are_never_journalled_live(self, tmp_path):
        """The audit case: every routed field is a hardcoded constant."""
        alt = _provider(_FakeZeroLag(_all_synthetic()), tmp_path)
        for source_id, fields in ROUTED.items():
            obs = alt.fetch_source(source_id)
            assert obs.payload.get("kind") == "zero_lag_physical_proxy"
            assert obs.status == SYNTHETIC_STATUS, (
                "%s reads a placeholder as an observation" % source_id)
            for name in fields:
                assert obs.payload["source_quality"][name] == SYNTHETIC

    def test_live_rows_still_report_live(self, tmp_path):
        """Relabelling must not blind the provider: a real label is honoured."""
        alt = _provider(_FakeZeroLag(_all_live()), tmp_path)
        for source_id in ROUTED:
            assert alt.fetch_source(source_id).status == LIVE_STATUS

    def test_one_placeholder_demotes_a_mixed_row(self, tmp_path):
        """growth_grid_rail_cards = grid + net_liquidity; one constant is enough."""
        quality = _all_live()
        quality["net_liquidity_usd_billions"] = SYNTHETIC
        alt = _provider(_FakeZeroLag(quality), tmp_path)
        obs = alt._fetch_zero_lag(SOURCES["growth_grid_rail_cards"])
        assert obs.status == SYNTHETIC_STATUS
        assert obs.payload["source_quality"] == {
            "industrial_grid_load_index": LIVE,
            "net_liquidity_usd_billions": SYNTHETIC,
        }

    def test_dead_branches_are_still_honest(self, tmp_path):
        """Unreachable today, but the code stays and must not rot back to LIVE."""
        for source_id, fields in DEAD_BRANCH.items():
            obs = _provider(_FakeZeroLag(_all_synthetic()), tmp_path)._fetch_zero_lag(
                SOURCES[source_id]
            )
            assert obs.status == SYNTHETIC_STATUS, source_id
            assert set(obs.payload["source_quality"]) == set(fields)

    def test_unlabelled_field_is_synthetic(self, tmp_path):
        """A field the map omits is unknown, and unknown is never live."""
        quality = _all_live()
        del quality["industrial_grid_load_index"]
        alt = _provider(_FakeZeroLag(quality), tmp_path)
        obs = alt.fetch_source("grid_load_mw")
        assert obs.status == SYNTHETIC_STATUS
        assert obs.payload["source_quality"]["industrial_grid_load_index"] == SYNTHETIC

    def test_provider_without_a_provenance_map_is_synthetic(self, tmp_path):
        """No map at all -> fail closed, even though the snapshot object exists."""
        fake = _FakeZeroLag({}, has_annotations=False, annotations=None)
        alt = _provider(fake, tmp_path)
        assert alt.fetch_source("grid_load_mw").status == SYNTHETIC_STATUS

    def test_whole_snapshot_branch_needs_every_field_live(self, tmp_path):
        """The else branch ships the entire snapshot, so one constant demotes it."""
        quality = _all_live()
        quality["satellite_crop_health_score"] = SYNTHETIC
        obs = _provider(_FakeZeroLag(quality), tmp_path).fetch_source(ELSE_BRANCH)
        assert obs.status == SYNTHETIC_STATUS
        assert obs.payload["source_quality"]["satellite_crop_health_score"] == SYNTHETIC

        live_obs = _provider(_FakeZeroLag(_all_live()), tmp_path).fetch_source(ELSE_BRANCH)
        assert live_obs.status == LIVE_STATUS


class TestJournalHonesty:
    def test_live_count_stops_counting_placeholders(self, tmp_path):
        alt = _provider(_FakeZeroLag(_all_synthetic()), tmp_path)
        snap = alt.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
        rows = {o["source_id"]: o for o in snap["observations"]}
        for source_id in ROUTED:
            assert rows[source_id]["status"] == SYNTHETIC_STATUS
            assert rows[source_id]["status"] != LIVE_STATUS
        # live_count is computed from the same rows the reader gets, so it can
        # never claim more live observations than the file actually holds.
        assert snap["live_count"] == sum(
            1 for o in snap["observations"] if o["status"] == LIVE_STATUS
        )
        assert snap["stub_count"] == sum(
            1 for o in snap["observations"] if o["status"] == "STUB"
        )

    def test_relabelling_does_not_drop_sources(self, tmp_path):
        """status is not STUB, so include_stubs=False keeps every source row.

        Downstream (core/trading_tools.py, data/global_macro_bridge.py) reads
        these rows with include_stubs=False. Only rows the stub filter removes
        may disappear; a placeholder must still be present, just not live.
        """
        alt = _provider(_FakeZeroLag(_all_synthetic()), tmp_path)
        full = alt.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
        stripped = alt.fetch_nowcast_snapshot(include_stubs=False, force_refresh=True)
        full_ids = {o["source_id"] for o in full["observations"]}
        stripped_ids = {o["source_id"] for o in stripped["observations"]}
        assert set(ROUTED) <= full_ids
        assert set(ROUTED) <= stripped_ids, "relabelling removed a source"
        # Every source that actually reaches _fetch_zero_lag must survive the
        # include_stubs=False filter carrying its (synthetic) label, NOT be
        # dropped. It must NOT be asserted that every golden-eight source
        # survives: api-key-gated sources outside the zero-lag fallback set
        # (e.g. zillow_zori, credit_card_spending) legitimately return STUB and
        # are filtered — that is correct fail-closed behaviour, not a drop
        # caused by relabelling. Only the routed zero-lag proxies are in scope.
        assert ELSE_BRANCH in stripped_ids
        assert stripped["stub_count"] == sum(
            1 for o in stripped["observations"] if o["status"] == "STUB"
        )

    def test_registry_sources_are_still_routable(self, tmp_path):
        """Every registry source that routes to zero-lag gets an honest row."""
        alt = _provider(_FakeZeroLag(_all_synthetic()), tmp_path)
        snap = alt.fetch_nowcast_snapshot(include_stubs=True, force_refresh=True)
        for source_id, row in {o["source_id"]: o for o in snap["observations"]}.items():
            if row["payload"].get("kind") == "zero_lag_physical_proxy":
                assert source_id in SOURCES
                assert row["status"] == SYNTHETIC_STATUS
                assert "source_quality" in row["payload"]