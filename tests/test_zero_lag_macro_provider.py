"""Unit tests for ZeroLagMacroProvider and AlternativeDataProvider integration."""
import pytest
from data.zero_lag_macro_provider import (
    SyntheticCommoditySpreads,
    ZeroLagMacroSnapshot,
    ZeroLagMacroProvider,
    get_zero_lag_provider,
)
from data.alternative_data_provider import AlternativeDataProvider
from data.alternative_data_registry import SOURCES, golden_eight


def test_zero_lag_provider_initialization():
    provider = get_zero_lag_provider()
    assert isinstance(provider, ZeroLagMacroProvider)


def test_synthetic_commodity_spreads_computation():
    provider = ZeroLagMacroProvider()
    spreads = provider.compute_synthetic_commodity_spreads()
    assert isinstance(spreads, SyntheticCommoditySpreads)
    assert spreads.synthetic_ammonia_cost_ton > 0.0
    assert spreads.corn_soy_price_ratio > 0.0
    assert spreads.corn_soy_regime in {"SOY_FAVORED", "EQUILIBRIUM", "CORN_FAVORED"}
    assert isinstance(spreads.crack_spread_321_bbl, float)
    assert isinstance(spreads.soybean_crush_spread_bu, float)
    assert isinstance(spreads.spark_spread_mwh, float)
    assert isinstance(spreads.ethanol_blend_margin_gal, float)


def test_net_liquidity_computation():
    provider = ZeroLagMacroProvider()
    net_liq, bias = provider.compute_net_liquidity()
    assert isinstance(net_liq, float)
    assert net_liq > 0.0
    assert bias in {"EXPANSIONARY", "NEUTRAL", "CONTRACTIONARY"}


def test_zero_lag_macro_snapshot_safety():
    provider = ZeroLagMacroProvider()
    snapshot = provider.get_snapshot()
    assert isinstance(snapshot, ZeroLagMacroSnapshot)
    assert snapshot.execution_authority is False
    
    data_dict = snapshot.to_dict()
    assert data_dict["execution_authority"] is False
    assert data_dict["data_latency_status"] == "ZERO_LAG (0-24h Physical Proxies)"
    assert "synthetic_spreads" in data_dict
    assert data_dict["net_liquidity_usd_billions"] > 0


def test_alternative_data_provider_zero_lag_routing():
    alt_provider = AlternativeDataProvider()
    
    # Test grid_load_mw
    obs_grid = alt_provider.fetch_source("grid_load_mw")
    assert obs_grid.status == "LIVE"
    assert obs_grid.value is not None
    assert obs_grid.execution_authority is False
    assert obs_grid.payload.get("kind") == "zero_lag_physical_proxy"

    # Test sku_price_scrape
    obs_sku = alt_provider.fetch_source("sku_price_scrape")
    assert obs_sku.status == "LIVE"
    assert obs_sku.value is not None
    assert obs_sku.payload.get("kind") == "zero_lag_physical_proxy"

    # Test sentinel_ndvi
    obs_ndvi = alt_provider.fetch_source("sentinel_ndvi")
    assert obs_ndvi.status == "LIVE"
    assert obs_ndvi.value is not None
    assert obs_ndvi.payload.get("kind") == "zero_lag_physical_proxy"

    # Test ais_vessel_tracking
    obs_ais = alt_provider.fetch_source("ais_vessel_tracking")
    assert obs_ais.status == "LIVE"
    assert obs_ais.value is not None
    assert obs_ais.payload.get("kind") == "zero_lag_physical_proxy"


def test_nowcast_snapshot_golden_eight():
    alt_provider = AlternativeDataProvider()
    snapshot = alt_provider.fetch_nowcast_snapshot(include_stubs=True)
    assert snapshot["execution_authority"] is False
    assert snapshot["live_count"] > 0
    
    # Verify golden eight presence
    g8_ids = {s.source_id for s in golden_eight()}
    obs_ids = {o["source_id"] for o in snapshot["observations"]}
    assert g8_ids.issubset(obs_ids)


def test_ten_pillars_bundle_completeness():
    provider = ZeroLagMacroProvider()
    bundle = provider.get_ten_pillars_bundle()
    assert bundle["execution_authority"] is False
    assert bundle["free_nowcasting_stack_status"] == "ONLINE"
    
    # Check all 10 pillars
    assert "1_inflation_cpi_pce" in bundle
    assert "2_economic_output_gdp" in bundle
    assert "3_labor_market_nfp_jolts" in bundle
    assert "4_consumer_spending_retail" in bundle
    assert "5_housing_and_construction" in bundle
    assert "6_agriculture_and_commodities" in bundle
    assert "7_energy_and_petroleum" in bundle
    assert "8_maritime_and_trade" in bundle
    assert "9_corporate_credit_stress" in bundle
    assert "10_monetary_policy_and_sentiment" in bundle


def test_ten_pillars_sources_routing():
    alt_provider = AlternativeDataProvider()
    for test_src in [
        "fred_tips_breakeven", "entsoe_electricity_grid",
        "google_trends_unemployment", "usda_nass_quickstats",
        "marine_cadastre_ais", "fred_corporate_yield_spreads",
        "satellite_sar_storage", "viirs_dnb_nightlights",
        "tropomi_atmospheric_flaring", "grace_groundwater_depletion",
        "sku_stockout_elasticity", "aar_intermodal_carloads",
        "credit_card_velocity", "lightcast_job_nlp",
        "dark_vessel_sts_ais", "grid_frequency_scada",
        "onchain_stablecoin_flows", "drone_open_isr_port",
    ]:
        obs = alt_provider.fetch_source(test_src)
        assert obs.status == "LIVE"
        assert obs.execution_authority is False
        assert obs.payload.get("kind") == "zero_lag_physical_proxy"


