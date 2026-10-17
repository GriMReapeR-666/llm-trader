"""P0-5: zero-lag provider source-quality honesty annotations.

Audit finding: the provider returns hardcoded constants (daily_cpi 0.22/2.85,
grid 101.4, chokepoint 98.2, crop 74.5, labor -1.2, dozens of constant
spreads) that flow into LLM context as if observed.

What is pinned here:
- every constant-bearing field reads SYNTHETIC_PLACEHOLDER, and a live quote
  can NEVER upgrade one (the first pass let NG=F upgrade spark_spread_mwh,
  whose 45.00 power anchor is a constant, to LIVE);
- the label map actually REACHES consumers (shipped inside to_dict(), not
  behind a getter nobody calls);
- multi-symbol fields degrade when ANY input symbol is down (no
  ordering-dependent last-writer-wins);
- provenance never leaks across compute passes.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.zero_lag_macro_provider import (  # noqa: E402
    LIVE, SYNTHETIC, ZeroLagMacroProvider, UniversalMacroRatios, ZeroLagMacroSnapshot,
)

# Fields whose formula contains a hardcoded constant, so no live upstream
# quote can make them observed. Mirrors ZeroLagMacroProvider._ALWAYS_SYNTHETIC
# plus the six hardcoded snapshot proxies.
CONSTANT_FIELDS = (
    "cyclicals_defensives_ratio", "brent_dubai_spread_usd", "gasoil_crack_bbl_usd",
    "spark_spread_mwh", "clean_dark_spread_mwh", "steel_mill_spread_ton",
    "aluminum_smelter_margin_ton", "hy_ig_credit_ratio",
    "net_liquidity_usd_billions", "move_vix_ratio", "ethanol_blend_margin_gal",
    "daily_cpi_mom_nowcast", "daily_cpi_yoy_nowcast", "industrial_grid_load_index",
    "maritime_chokepoint_index", "satellite_crop_health_score",
    "labor_hiring_velocity",
)


class _OfflineYahoo:
    """Provider that is present but returns nothing (total outage)."""

    def get_quote_data(self, symbol):
        return None


class _LiveYahoo:
    def get_quote_data(self, symbol):
        return {"price": 123.45}


def _provider(monkeypatch, yahoo):
    prov = ZeroLagMacroProvider(cache_file="artifacts/zero_lag_macro_cache.json")
    monkeypatch.setattr(prov, "_get_yahoo", lambda: yahoo)
    monkeypatch.setenv("FRED_API_KEY", "")   # keep the FRED refinement inert
    return prov


@pytest.fixture
def offline(monkeypatch):
    return _provider(monkeypatch, None)


@pytest.fixture
def live(monkeypatch):
    return _provider(monkeypatch, _LiveYahoo())


class TestConstantsAreNeverUpgraded:
    """The core laundering hole: LIVE must never win over a constant."""

    @pytest.mark.parametrize("field", CONSTANT_FIELDS)
    def test_ratio_constants_stay_synthetic_with_every_quote_live(self, live, field):
        quality = live.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert quality[field] == SYNTHETIC, (
            f"{field} contains a hardcoded constant and must not be labeled LIVE"
        )

    @pytest.mark.parametrize("field", CONSTANT_FIELDS)
    def test_snapshot_constants_stay_synthetic_with_every_quote_live(self, live, field):
        quality = live.get_snapshot().to_dict()["source_quality"]
        assert quality[field] == SYNTHETIC

    def test_spark_spread_not_upgraded_by_natgas(self, live):
        """Regression: spark_spread_mwh = 45.00 - 7.0*natgas. The first pass
        mapped it to NG=F, so a live natgas quote labeled a constant LIVE."""
        quality = live.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert quality["spark_spread_mwh"] == SYNTHETIC
        assert quality["haber_bosch_ammonia_cost_ton"] == LIVE   # genuine NG formula

    def test_gasoil_crack_not_upgraded_by_brent(self, live):
        """gasoil_crack = 104.70 - brent; the 104.70 anchor is a constant."""
        quality = live.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert quality["gasoil_crack_bbl_usd"] == SYNTHETIC


class TestFailClosed:
    def test_annotations_safe_before_any_compute(self, offline):
        quality = offline.source_quality_annotations()
        assert isinstance(quality, dict) and quality
        assert all(v in (LIVE, SYNTHETIC) for v in quality.values())
        for field in CONSTANT_FIELDS:
            assert quality[field] == SYNTHETIC

    def test_total_outage_labels_everything_synthetic(self, offline):
        quality = offline.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert quality and all(v == SYNTHETIC for v in quality.values())

    def test_provider_present_but_returning_nothing_is_synthetic(self, monkeypatch):
        prov = _provider(monkeypatch, _OfflineYahoo())
        quality = prov.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert all(v == SYNTHETIC for v in quality.values())

    def test_never_fetched_symbol_is_synthetic(self, offline):
        assert offline._quote_is_live("HG=F") is False


class TestPartialFailureDegradesNotUpgrades:
    def test_one_dead_symbol_degrades_its_dependents(self, monkeypatch):
        """crack_spread_321_bbl needs CL/RB/HO; if only CL answers it must
        degrade, not inherit the last writer's LIVE."""
        class _PartialYahoo:
            def get_quote_data(self, symbol):
                return {"price": 50.0} if symbol == "CL=F" else None
        prov = _provider(monkeypatch, _PartialYahoo())
        quality = prov.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert quality["crack_spread_321_bbl"] == SYNTHETIC   # RB=F and HO=F dead
        assert quality["brent_wti_spread_usd"] == SYNTHETIC   # BZ=F dead
        assert quality["natgas_crude_parity_ratio"] == SYNTHETIC  # NG=F dead

    def test_multi_symbol_field_is_order_independent(self, monkeypatch):
        """Same upstream availability, different dict ordering => same label."""
        def _run(order):
            class _Y:
                def get_quote_data(self, symbol):
                    return {"price": 7.0} if symbol in order else None
            prov = _provider(monkeypatch, _Y())
            return prov.compute_universal_macro_ratios().to_dict()["source_quality"]

        assert _run({"HG=F", "CL=F"})["copper_oil_ratio"] == LIVE
        assert _run({"HG=F"})["copper_oil_ratio"] == SYNTHETIC


class TestAnnotationsReachConsumers:
    """A label only the provider can produce is a label nobody reads."""

    def test_ratios_to_dict_carries_source_quality(self, live):
        d = live.compute_universal_macro_ratios().to_dict()
        assert isinstance(d["source_quality"], dict) and d["source_quality"]
        assert d["execution_authority"] is False

    def test_snapshot_to_dict_carries_source_quality(self, live):
        d = live.get_snapshot().to_dict()
        assert d["source_quality"]["industrial_grid_load_index"] == SYNTHETIC
        assert d["execution_authority"] is False

    def test_snapshot_to_dict_covers_the_nested_spreads_fields(self, live):
        """core/trading_tools.py ships snap.to_dict() into the LLM tool
        result, so the spread fields must be labelled inside that dict too."""
        q = live.get_snapshot().to_dict()["source_quality"]
        for field in ("corn_soy_price_ratio", "crack_spread_321_bbl",
                      "soybean_crush_spread_bu", "corn_soy_regime"):
            assert field in q

    def test_ten_pillars_bundle_is_labeled(self, live):
        bundle = live.get_ten_pillars_bundle()
        assert bundle["source_quality"]["daily_cpi_mom_nowcast"] == SYNTHETIC
        assert bundle["execution_authority"] is False

    def test_labels_survive_json_serialization(self, live):
        import json
        d = json.loads(json.dumps(live.get_snapshot().to_dict(), default=str))
        assert d["source_quality"]["labor_hiring_velocity"] == SYNTHETIC


class TestProvenanceLifecycle:
    def test_no_live_leak_between_compute_passes(self, monkeypatch):
        """A LIVE stamp from cycle N must not survive a fully-degraded cycle
        N+1 (the first pass merged with setdefault and leaked)."""
        prov = _provider(monkeypatch, _LiveYahoo())
        assert prov.compute_universal_macro_ratios().to_dict()["source_quality"]["gold_silver_ratio"] == LIVE

        prov._get_yahoo = lambda: None
        prov._quote_cache = {}          # force a real refetch, not a TTL hit
        after = prov.compute_universal_macro_ratios().to_dict()["source_quality"]
        assert after["gold_silver_ratio"] == SYNTHETIC
        assert all(v == SYNTHETIC for v in after.values())

    def test_snapshot_pass_does_not_depend_on_ratios_running_first(self, offline):
        """get_snapshot() never calls compute_universal_macro_ratios(); the
        annotation set must still be complete after a snapshot-only pass."""
        quality = offline.get_snapshot().to_dict()["source_quality"]
        assert quality["copper_gold_ratio"] == SYNTHETIC
        assert quality["gold_silver_ratio"] == SYNTHETIC
        assert len(quality) > len(CONSTANT_FIELDS)

    def test_annotation_and_embedded_map_agree(self, live):
        snap = live.get_snapshot()
        assert snap.source_quality == snap.to_dict()["source_quality"]
        assert snap.source_quality == live.source_quality_annotations()

    def test_dataclass_defaults_are_backwards_compatible(self):
        """External constructors keep working; an unstamped object is empty,
        never a claim of LIVE."""
        s = ZeroLagMacroSnapshot(
            timestamp_utc="t", net_liquidity_usd_billions=1.0,
            net_liquidity_bias="NEUTRAL", daily_cpi_mom_nowcast=0.0,
            daily_cpi_yoy_nowcast=0.0, industrial_grid_load_index=100.0,
            maritime_chokepoint_index=100.0, satellite_crop_health_score=50.0,
            labor_hiring_velocity=0.0, synthetic_spreads=None,
            data_latency_status="ZERO_LAG",
        )
        assert s.source_quality == {}
        r = UniversalMacroRatios(
            timestamp_utc="t", copper_gold_ratio=1.0, copper_oil_ratio=1.0,
            lumber_gold_ratio=1.0, cyclicals_defensives_ratio=1.0, move_vix_ratio=1.0,
            brent_wti_spread_usd=1.0, brent_dubai_spread_usd=1.0,
            gasoil_crack_bbl_usd=1.0, natgas_crude_parity_ratio=1.0,
            crack_spread_321_bbl=1.0, spark_spread_mwh=1.0,
            clean_dark_spread_mwh=1.0, haber_bosch_ammonia_cost_ton=1.0,
            corn_soy_price_ratio=1.0, corn_soy_regime="EQUILIBRIUM",
            soybean_crush_spread_bu=1.0, cattle_crush_feedlot_margin_cwt=1.0,
            hog_corn_ratio=1.0, wheat_corn_spread_bu=1.0,
            ethanol_blend_margin_gal=1.0, gold_silver_ratio=1.0,
            platinum_gold_ratio=1.0, steel_mill_spread_ton=1.0,
            aluminum_smelter_margin_ton=1.0, hy_ig_credit_ratio=1.0,
            net_liquidity_usd_billions=1.0, net_liquidity_bias="NEUTRAL",
        )
        assert r.source_quality == {}