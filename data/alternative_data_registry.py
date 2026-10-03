"""Alternative / nowcast data source registry — Tier 1/2/3 + Golden 8.

Fast alternative sources for macrofundamental nowcasting. Official government
releases (CPI, GDP, NFP, etc.) are lagging benchmarks — not primary model inputs.

Doctrine (from project docs + operator hierarchy):
- Model CPI via daily price basket + energy ticks + ZORI; reconcile to CPI at back end.
- Model GDP via grid load + rail velocity + credit-card flows; reconcile quarterly.
- ``execution_authority`` is always False for every binding.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


TIER_REALTIME = "T1_REALTIME"      # 0–5 min
TIER_DAILY = "T2_DAILY"            # 1–24 hours
TIER_WEEKLY = "T3_WEEKLY"          # 3–7 days

TIER_LABELS: Dict[str, str] = {
    TIER_REALTIME: "Real-Time / Sub-Minute (0–5 min)",
    TIER_DAILY: "Daily (1–24 hours)",
    TIER_WEEKLY: "Weekly (3–7 days)",
}

NOWCAST_MODELING_PRINCIPLES: Tuple[str, ...] = (
    "Never model CPI directly — model daily scraped price basket + energy ticks + ZORI, "
    "then bridge to CPI for benchmarking.",
    "Never model GDP directly — model electricity consumption + rail velocity + credit-card flows, "
    "then reconcile to GDP quarterly.",
    "Government reports are lagging benchmarks, not primary alphas.",
    "Tier 1 and Tier 2 sources are primary nowcast inputs; Tier 3 confirms.",
)

GOLDEN_EIGHT_IDS: Tuple[str, ...] = (
    "grid_load_mw",
    "sku_price_scrape",
    "zillow_zori",
    "credit_card_spending",
    "linkup_job_postings",
    "ais_tanker_tracking",
    "sentinel_ndvi",
    "fed_sofr_futures",
)


@dataclass(frozen=True)
class AlternativeDataSource:
    source_id: str
    entity: str
    tier: str
    category: str
    name: str
    provider: str
    module: str
    latency: str
    beats_official: str
    symbols: Tuple[str, ...] = ()
    series: Tuple[str, ...] = ()
    requires_api_key: bool = False
    env_key: str = ""
    golden_eight: bool = False
    execution_authority: bool = False
    notes: str = ""

    def to_binding(self) -> Dict[str, Any]:
        row = asdict(self)
        row["execution_authority"] = False
        return row


def _src(
    source_id: str,
    entity: str,
    tier: str,
    category: str,
    name: str,
    provider: str,
    module: str,
    latency: str,
    beats_official: str,
    *,
    symbols: Tuple[str, ...] = (),
    series: Tuple[str, ...] = (),
    requires_api_key: bool = False,
    env_key: str = "",
    golden_eight: bool = False,
    notes: str = "",
) -> AlternativeDataSource:
    return AlternativeDataSource(
        source_id=source_id,
        entity=entity,
        tier=tier,
        category=category,
        name=name,
        provider=provider,
        module=module,
        latency=latency,
        beats_official=beats_official,
        symbols=symbols,
        series=series,
        requires_api_key=requires_api_key,
        env_key=env_key,
        golden_eight=golden_eight,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Tier 1 — Real-Time / Sub-Minute
# ---------------------------------------------------------------------------

_TIER1: Tuple[AlternativeDataSource, ...] = (
    _src(
        "energy_futures_ticks", "INFLATION", TIER_REALTIME, "inflation_prices",
        "Tick-level energy futures (crude, natgas, gasoline)",
        "MARKET", "alternative_data_provider", "0–5 min", "CPI (15–45 days)",
        symbols=("CL=F", "NG=F", "RB=F"),
    ),
    _src(
        "realtime_fx", "DXY", TIER_REALTIME, "inflation_prices",
        "Real-time FX / dollar index",
        "MARKET", "yahoo_finance_provider", "0–5 min", "CPI (15–45 days)",
        symbols=("DX-Y.NYB", "EURUSD=X"),
    ),
    _src(
        "grid_load_mw", "GRID_LOAD", TIER_REALTIME, "economic_activity",
        "Power grid load telemetry (PJM, ERCOT, CAISO) — 5-min MW consumption",
        "GRID_ISO", "alternative_data_provider", "5 min", "GDP (30–90 days)",
        requires_api_key=True, env_key="GRID_ISO_API_KEY", golden_eight=True,
        notes="Proxy: EIA electricity ops when ISO key absent",
    ),
    _src(
        "fed_press_finbert", "EMPLOYMENT", TIER_REALTIME, "labor_market",
        "FinBERT NLP on Fed Chair live press conferences",
        "FINBERT_NLP", "macro_extraction", "live", "FOMC Minutes (21 days)",
        requires_api_key=False,
        notes="Candidate NLP only; never order authority",
    ),
    _src(
        "ais_vessel_tracking", "SHIPPING_FLOW", TIER_REALTIME, "trade_shipping",
        "AIS vessel transponders (Hormuz, Malacca, Suez, Panama)",
        "AIS", "alternative_data_provider", "real-time", "Export reports (7–14 days)",
        requires_api_key=True, env_key="AIS_API_KEY",
    ),
    _src(
        "options_vol_surface", "OPTIONS_SKEW", TIER_REALTIME, "market_stress",
        "Options vol surface (VIX, skew), order book DOM imbalance",
        "CBOE", "openbb_data_bridge|mt5_trader", "intraday", "COT reports (3–6 days)",
        symbols=("^VIX", "^SKEW"), series=("VIXCLS",),
    ),
    _src(
        "fed_sofr_futures", "FUTURES_LIQUIDITY", TIER_REALTIME, "liquidity",
        "Fed Funds futures (ZQ), SOFR futures (SR3) intraday",
        "MARKET", "alternative_data_provider", "intraday", "SOFR/EFFR (12–24 hrs)",
        symbols=("ZQ=F", "SR3=F"), series=("SOFR", "EFFR"), golden_eight=True,
    ),
)

# ---------------------------------------------------------------------------
# Tier 2 — Daily
# ---------------------------------------------------------------------------

_TIER2: Tuple[AlternativeDataSource, ...] = (
    _src(
        "sku_price_scrape", "CPI_NOWCAST", TIER_DAILY, "consumer_inflation",
        "500k+ SKU web scraping (e-commerce, grocery, auto) + AAA gas ticks",
        "SKU_SCRAPER", "alternative_data_provider", "daily", "CPI (15–45 days)",
        requires_api_key=True, env_key="SKU_SCRAPE_API_KEY", golden_eight=True,
    ),
    _src(
        "zillow_zori", "SHELTER_RENT", TIER_DAILY, "rent_shelter",
        "Zillow Observed Rent Index (ZORI)",
        "ZILLOW", "alternative_data_provider", "daily", "CPI shelter (~9 month lead)",
        requires_api_key=True, env_key="ZILLOW_RESEARCH_API_KEY", golden_eight=True,
    ),
    _src(
        "credit_card_spending", "CONSUMER_SPENDING", TIER_DAILY, "consumer_spending",
        "Anonymized credit card aggregates (Facteus, Second Measure)",
        "CREDIT_CARD", "alternative_data_provider", "daily (~2-day lag)", "Retail Sales (15–45 days)",
        requires_api_key=True, env_key="CREDIT_CARD_API_KEY", golden_eight=True,
    ),
    _src(
        "linkup_job_postings", "LABOR_DEMAND", TIER_DAILY, "labor_demand",
        "LinkUp / Revelio daily job postings + corporate portal scrapes",
        "LINKUP", "alternative_data_provider", "daily (0-day lag)", "JOLTS (35–65 days)",
        requires_api_key=True, env_key="LINKUP_API_KEY", golden_eight=True,
    ),
    _src(
        "shift_hours_worked", "LABOR_SUPPLY", TIER_DAILY, "labor_supply",
        "Homebase / Kronos shift hours worked",
        "WORKFORCE", "alternative_data_provider", "daily", "NFP (7–35 days)",
        requires_api_key=True, env_key="WORKFORCE_API_KEY",
    ),
    _src(
        "sentinel_ndvi", "CROP_HEALTH", TIER_DAILY, "crop_health",
        "Sentinel-2 / Planet Labs NDVI + SMAP soil moisture",
        "SATELLITE", "alternative_data_provider", "daily", "WASDE (30–60 days)",
        requires_api_key=True, env_key="SATELLITE_API_KEY", golden_eight=True,
    ),
    _src(
        "oil_storage_satellite", "OIL_STORAGE", TIER_DAILY, "oil_storage",
        "Satellite optical tank shadow tracking (Cushing, Singapore)",
        "SATELLITE", "alternative_data_provider", "daily", "EIA Petroleum (5–12 days)",
        requires_api_key=True, env_key="SATELLITE_API_KEY",
        notes="Fallback: EIA weekly petroleum when satellite unavailable",
    ),
    _src(
        "gas_storage_pipeline", "GAS_STORAGE", TIER_DAILY, "gas_storage",
        "Pipeline flow meter scrapers + degree-day models",
        "PIPELINE", "alternative_data_provider", "daily", "EIA NatGas (6–13 days)",
        requires_api_key=True, env_key="PIPELINE_FLOW_API_KEY",
    ),
    _src(
        "housing_excavation", "HOUSING_ACTIVITY", TIER_DAILY, "housing_activity",
        "Satellite foundation excavation detection + lumber futures volume",
        "SATELLITE|MARKET", "alternative_data_provider", "daily", "Housing Starts (20–50 days)",
        symbols=("LBS=F",), requires_api_key=True, env_key="SATELLITE_API_KEY",
    ),
    _src(
        "corporate_web_scrape", "CORPORATE_HEALTH", TIER_DAILY, "corporate_health",
        "Daily web scraping (pricing, inventory, reviews) + hiring velocity",
        "WEB_INTEL", "alternative_data_provider", "daily", "10-Q / 10-K (45–90 days)",
        requires_api_key=True, env_key="WEB_INTEL_API_KEY",
    ),
)

# ---------------------------------------------------------------------------
# Tier 3 — Weekly
# ---------------------------------------------------------------------------

_TIER3: Tuple[AlternativeDataSource, ...] = (
    _src(
        "grain_barge_freight", "GRAIN_EXPORTS", TIER_WEEKLY, "grain_exports",
        "Mississippi barge freight rates + port vessel grain queues",
        "FREIGHT", "alternative_data_provider", "weekly", "USDA Export Sales (7–14 days)",
        requires_api_key=True, env_key="FREIGHT_API_KEY",
    ),
    _src(
        "dat_cass_freight", "FREIGHT_VOLUME", TIER_WEEKLY, "freight_volume",
        "DAT load-to-truck ratio + Cass Freight Index",
        "FREIGHT", "alternative_data_provider", "weekly", "Retail Sales / GDP",
        requires_api_key=True, env_key="FREIGHT_API_KEY",
    ),
    _src(
        "rail_velocity", "RAIL_ACTIVITY", TIER_WEEKLY, "rail_activity",
        "Union Pacific / BNSF daily train velocity (or AAR weekly)",
        "RAIL", "alternative_data_provider", "weekly", "GDP / Industrial Production",
        requires_api_key=True, env_key="RAIL_DATA_API_KEY",
    ),
    _src(
        "cme_positioning", "FUTURES_POSITIONING", TIER_WEEKLY, "futures_positioning",
        "CME liquidity provider tick delta & order book imbalance",
        "CME", "alternative_data_provider", "intraday-weekly", "CFTC COT (3–6 days)",
        notes="Fallback: CFTC COT weekly via cftc_cot_provider",
    ),
    _src(
        "money_market_repo", "MONEY_SUPPLY", TIER_WEEKLY, "money_market",
        "Daily primary dealer repo + ON RRP + discount window",
        "FEDERAL_RESERVE", "openbb_data_bridge|fredapi", "daily-weekly", "Fed H.4.1 (1–6 days)",
        series=("WALCL", "RRPONTSYD", "SOFR"),
    ),
)

# ---------------------------------------------------------------------------
# Doc-aligned additions (04_MACHINE_LEARNING_NLP_AND_ALTERNATIVE_DATA.md)
# ---------------------------------------------------------------------------

_DOC_ALIGNED: Tuple[AlternativeDataSource, ...] = (
    _src(
        "ais_tanker_tracking", "SHIPPING_FLOW", TIER_REALTIME, "trade_shipping",
        "AIS tanker tracking — physical oil/commodity flows",
        "AIS", "alternative_data_provider", "real-time", "Export reports (7–14 days)",
        requires_api_key=True, env_key="AIS_API_KEY", golden_eight=True,
    ),
    _src(
        "eia_petroleum_weekly", "OIL_STORAGE", TIER_WEEKLY, "oil_storage",
        "EIA weekly petroleum status (inventory draws/builds)",
        "EIA", "eia_provider", "weekly", "EIA Petroleum (5–12 days)",
        notes="Free proxy when satellite tank tracking unavailable",
    ),
    _src(
        "cftc_cot_weekly", "FUTURES_POSITIONING", TIER_WEEKLY, "futures_positioning",
        "CFTC Commitments of Traders positioning",
        "CFTC", "cftc_cot_provider", "weekly (Tue)", "COT (3–6 days lag vs positioning)",
    ),
    _src(
        "imf_maritime_chokepoints", "SHIPPING_FLOW", TIER_WEEKLY, "trade_shipping",
        "IMF maritime chokepoint throughput (Suez, Panama, Hormuz)",
        "IMF", "openbb_data_bridge", "monthly-weekly", "Trade reports (weeks)",
    ),
    _src(
        "usda_commodity_psd", "CROP_HEALTH", TIER_WEEKLY, "crop_health",
        "USDA commodity production/supply/demand",
        "USDA", "openbb_data_bridge", "weekly-monthly", "WASDE (30–60 days)",
    ),
    _src(
        "finbert_transcripts", "CORPORATE_HEALTH", TIER_DAILY, "corporate_health",
        "FinBERT on earnings transcripts (Seeking Alpha / SEC)",
        "SEEKING_ALPHA|SEC", "seeking_alpha_provider|sec_edgar_provider", "daily",
        "10-Q / 10-K (45–90 days)",
    ),
    _src(
        "growth_grid_rail_cards", "GROWTH", TIER_DAILY, "economic_activity",
        "Composite GDP nowcast: grid load + rail velocity + credit-card flows",
        "COMPOSITE", "alternative_data_provider", "daily", "GDP (30–90 days)",
        notes="Modeling principle: never model GDP directly from BEA print",
    ),
)

# ---------------------------------------------------------------------------
# Free Nowcasting Stack & 10 Macro Pillars
# (FRED, Satellite, Grid ISOs, NOAA, AIS, Zillow, Redfin, NASS, SEC, Google Trends)
# ---------------------------------------------------------------------------

_TEN_PILLARS_FREE_STACK: Tuple[AlternativeDataSource, ...] = (
    # 1. Inflation (CPI / PCE)
    _src(
        "fred_tips_breakeven", "INFLATION", TIER_DAILY, "inflation_expectations",
        "FRED 5y/10y TIPS Breakeven Inflation Rates (T5YIE, T10YIE)",
        "FRED", "zero_lag_macro_provider", "daily", "CPI (15–45 days)",
        series=("T5YIE", "T10YIE"),
    ),
    _src(
        "redfin_housing_market", "SHELTER_RENT", TIER_WEEKLY, "housing_demand",
        "Redfin Data Center: Median sale price, price drops, days on market",
        "REDFIN", "zero_lag_macro_provider", "weekly", "CPI Shelter / Existing Home Sales",
    ),
    _src(
        "open_meteo_weather", "ENERGY_DEMAND", TIER_REALTIME, "weather_energy",
        "Open-Meteo / NOAA GFS real-time degree days & extreme temperature alerts",
        "NOAA", "zero_lag_macro_provider", "real-time", "Energy demand surprises",
    ),

    # 2. Economic Output (GDP / Industrial Production)
    _src(
        "entsoe_electricity_grid", "GRID_LOAD", TIER_REALTIME, "economic_activity",
        "ENTSO-E Transparency: European hourly electricity consumption & fuel generation",
        "ENTSO-E", "zero_lag_macro_provider", "hourly", "Eurozone GDP (60–90 days)",
    ),
    _src(
        "aar_rail_carloads", "FREIGHT_VOLUME", TIER_WEEKLY, "rail_freight",
        "AAR Weekly Rail Data: Intermodal containers & carloads by commodity",
        "AAR", "zero_lag_macro_provider", "weekly", "GDP / Industrial Production (30–90 days)",
    ),
    _src(
        "nasa_viirs_nightlights", "GROWTH", TIER_DAILY, "satellite_activity",
        "NASA VIIRS Nighttime Lights satellite-measured economic intensity",
        "NASA", "zero_lag_macro_provider", "daily", "Regional GDP (90 days)",
    ),

    # 3. Labor Market (NFP / JOLTS)
    _src(
        "google_trends_unemployment", "LABOR_DEMAND", TIER_DAILY, "labor_sentiment",
        "Google Trends: Search interest for 'unemployment benefits', 'layoffs', 'jobs near me'",
        "GOOGLE_TRENDS", "zero_lag_macro_provider", "daily", "NFP / JOLTS (7–65 days)",
    ),
    _src(
        "fred_initial_claims", "LABOR_SUPPLY", TIER_WEEKLY, "jobless_claims",
        "FRED: Weekly Initial & Continued Jobless Claims (ICSA, CCSA)",
        "FRED", "zero_lag_macro_provider", "weekly", "NFP (7–35 days)",
        series=("ICSA", "CCSA"),
    ),
    _src(
        "sec_edgar_layoffs", "LABOR_DEMAND", TIER_REALTIME, "corporate_restructuring",
        "SEC EDGAR 8-K Layoff Disclosures & Hiring Freeze Filings",
        "SEC", "sec_edgar_provider", "real-time", "JOLTS (35–65 days)",
    ),

    # 4. Consumer Spending (Retail Sales)
    _src(
        "fred_retail_gasoline", "CONSUMER_SPENDING", TIER_WEEKLY, "energy_spending",
        "FRED: Weekly Retail Gasoline Prices (GASREGW) consumer energy drag",
        "FRED", "zero_lag_macro_provider", "weekly", "Retail Sales (15–45 days)",
        series=("GASREGW",),
    ),
    _src(
        "opentable_reservations", "CONSUMER_SPENDING", TIER_DAILY, "dining_activity",
        "OpenTable State of the Industry: Daily restaurant reservation seated diners",
        "OPENTABLE", "zero_lag_macro_provider", "daily", "Retail Food Services (30 days)",
    ),

    # 5. Housing & Construction
    _src(
        "fred_building_permits", "HOUSING_ACTIVITY", TIER_DAILY, "construction_permits",
        "FRED: US New Private Housing Units Authorized by Building Permits (PERMIT)",
        "FRED", "zero_lag_macro_provider", "monthly", "Housing Starts (20–50 days)",
        series=("PERMIT",),
    ),
    _src(
        "usgs_landsat_urban", "HOUSING_ACTIVITY", TIER_WEEKLY, "earth_observation",
        "USGS Landsat & Copernicus Sentinel-2 Construction Footprint Expansion",
        "USGS", "zero_lag_macro_provider", "5-day", "Construction Spending (45 days)",
    ),

    # 6. Agriculture & Commodities
    _src(
        "usda_nass_quickstats", "CROP_HEALTH", TIER_WEEKLY, "crop_progress",
        "USDA NASS Quick Stats API: Weekly crop condition ratings, silking & harvest progress",
        "USDA_NASS", "zero_lag_macro_provider", "weekly", "WASDE (30–60 days)",
    ),
    _src(
        "nasa_smap_moisture", "CROP_HEALTH", TIER_DAILY, "soil_moisture",
        "NASA SMAP: Root-zone topsoil moisture anomalies for Corn Belt & Cerrado",
        "NASA", "zero_lag_macro_provider", "2-3 day", "WASDE Yield Estimates (30–60 days)",
    ),
    _src(
        "noaa_climate_precip", "CROP_HEALTH", TIER_DAILY, "drought_indices",
        "NOAA Climate Data: Palmer Drought Severity Index (PDSI) & Precipitation Anomaly",
        "NOAA", "zero_lag_macro_provider", "daily", "Commodity supply shocks",
    ),

    # 7. Energy & Petroleum
    _src(
        "noaa_gfs_degree_days", "ENERGY_DEMAND", TIER_DAILY, "degree_days",
        "NOAA GFS/NAM Heating Degree Days (HDD) & Cooling Degree Days (CDD)",
        "NOAA", "zero_lag_macro_provider", "hourly", "EIA Natural Gas Storage (6–13 days)",
    ),
    _src(
        "skyfi_tank_shadows", "OIL_STORAGE", TIER_WEEKLY, "satellite_storage",
        "SkyFi Open Data: Satellite floating-roof oil tank shadow height calculations",
        "SKYFI", "zero_lag_macro_provider", "weekly", "EIA Petroleum (5–12 days)",
    ),

    # 8. Maritime & Global Trade
    _src(
        "marine_cadastre_ais", "SHIPPING_FLOW", TIER_REALTIME, "coastal_ais",
        "NOAA MarineCadastre.gov: US coastal & port vessel traffic density",
        "NOAA_AIS", "zero_lag_macro_provider", "hourly", "Census Trade Balance (35 days)",
    ),
    _src(
        "aishub_vessel_positions", "SHIPPING_FLOW", TIER_REALTIME, "global_ais",
        "AISHub: Near real-time global transponder positions at maritime chokepoints",
        "AISHUB", "zero_lag_macro_provider", "real-time", "Export Volume Reports (14 days)",
    ),
    _src(
        "freightos_fbx_ticker", "SHIPPING_FLOW", TIER_WEEKLY, "container_rates",
        "Freightos Baltic Index (FBX): Global 40ft container spot freight rate ticker",
        "FREIGHTOS", "zero_lag_macro_provider", "weekly", "Import Price Index (30 days)",
    ),

    # 9. Corporate Fundamentals
    _src(
        "fred_corporate_yield_spreads", "MARKET_STRESS", TIER_DAILY, "credit_spreads",
        "FRED: ICE BofA US High Yield & Investment Grade Option-Adjusted Spreads (OAS)",
        "FRED", "zero_lag_macro_provider", "daily", "Corporate default & bankruptcy reports",
        series=("BAMLH0A0HYM2", "BAMLC0A0CM"),
    ),

    # 10. Monetary Policy & Sentiment
    _src(
        "fed_speech_transcripts", "POLICY_SENTIMENT", TIER_DAILY, "central_bank_speeches",
        "Federal Reserve Speech Calendar + Governor Testimony Transcripts",
        "FED", "zero_lag_macro_provider", "same-day", "FOMC Minutes (21 days)",
    ),
    _src(
        "google_trends_macro", "MACRO_SENTIMENT", TIER_DAILY, "search_interest",
        "Google Trends: Search interest for 'inflation', 'recession', 'Fed rate hike'",
        "GOOGLE_TRENDS", "zero_lag_macro_provider", "daily", "UMich Consumer Sentiment (15–30 days)",
    ),

    # 11. Satellite SAR (Synthetic Aperture Radar)
    _src(
        "satellite_sar_storage", "OIL_STORAGE", TIER_DAILY, "satellite_radar",
        "Sentinel-1 SAR Radar: Floating roof oil tank elevation & mining pit volume",
        "SATELLITE", "zero_lag_macro_provider", "daily", "EIA Petroleum Inventories (5–12 days)",
    ),

    # 12. Night Lights (VIIRS DNB)
    _src(
        "viirs_dnb_nightlights", "GROWTH", TIER_DAILY, "satellite_radiance",
        "NASA VIIRS DNB: Industrial manufacturing facility nighttime radiance",
        "NASA", "zero_lag_macro_provider", "daily", "Regional GDP (90 days)",
    ),

    # 13. Atmospheric Chemical Composition
    _src(
        "tropomi_atmospheric_flaring", "ENERGY_DEMAND", TIER_DAILY, "atmospheric_plumes",
        "TROPOMI Sentinel-5P: SO2 flaring, NO2 industrial emissions & methane super-emitters",
        "SATELLITE", "zero_lag_macro_provider", "daily", "Industrial Production",
    ),

    # 14. Groundwater & Gravity Depletion
    _src(
        "grace_groundwater_depletion", "CROP_HEALTH", TIER_DAILY, "aquifer_gravity",
        "NASA GRACE-FO: Ogallala & North China Plain aquifer gravity depletion",
        "NASA", "zero_lag_macro_provider", "monthly", "Agricultural Drought Declarations",
    ),

    # 15. SKU Stockout & Substitution Elasticity
    _src(
        "sku_stockout_elasticity", "CPI_NOWCAST", TIER_DAILY, "ecommerce_elasticity",
        "500k+ SKU Scraper: Substitute good detection, stock-out flags & price elasticity",
        "SKU_SCRAPER", "zero_lag_macro_provider", "daily", "Retail Inventory-to-Sales Ratios",
    ),

    # 16. Freight Rail Intermodal Carloads
    _src(
        "aar_intermodal_carloads", "FREIGHT_VOLUME", TIER_WEEKLY, "rail_intermodal",
        "AAR Weekly Rail Data: Intermodal container loadings & chemical rail velocity",
        "AAR", "zero_lag_macro_provider", "weekly", "Surface Transportation Board Reports",
    ),

    # 17. Credit Card Transaction Velocity
    _src(
        "credit_card_velocity", "CONSUMER_SPENDING", TIER_DAILY, "card_velocity",
        "Facteus / Second Measure: Merchant Category Code (MCC) spending velocity",
        "CREDIT_CARD", "zero_lag_macro_provider", "daily (T+1)", "BEA Personal Consumption Expenditures",
    ),

    # 18. Job Posting NLP & Skill Clustering
    _src(
        "lightcast_job_nlp", "LABOR_DEMAND", TIER_WEEKLY, "job_nlp",
        "Lightcast / LinkUp: Skill cluster hiring intensity & wage pressure",
        "LINKUP", "zero_lag_macro_provider", "weekly", "BLS Occupational Employment",
    ),

    # 19. Dark Fleet & AIS Gap Detection
    _src(
        "dark_vessel_sts_ais", "SHIPPING_FLOW", TIER_REALTIME, "shadow_fleet",
        "AIS Gap & Satellite SAR: Ship-to-Ship (STS) transfer detection in Laconian Gulf/Malacca",
        "AIS", "zero_lag_macro_provider", "real-time", "Sanctions Evasion Supply Reports",
    ),

    # 20. Power Grid Frequency & SCADA Telemetry
    _src(
        "grid_frequency_scada", "GRID_LOAD", TIER_REALTIME, "grid_frequency",
        "High-Voltage Substation Frequency Containment Reserves (FCR) & Grid Inertia",
        "GRID_ISO", "zero_lag_macro_provider", "sub-second", "NERC Grid Stability Alerts",
    ),

    # 21. On-Chain Stablecoin Flows
    _src(
        "onchain_stablecoin_flows", "FUTURES_LIQUIDITY", TIER_REALTIME, "onchain_liquidity",
        "USDT/USDC Cross-Border Settlement Corridors & Mining Hash-Rate Migration",
        "ONCHAIN", "zero_lag_macro_provider", "real-time", "Sovereign Capital Flight Disclosures",
    ),

    # 22. Open-Source Drone & ISR Remote Sensing
    _src(
        "drone_open_isr_port", "SHIPPING_FLOW", TIER_DAILY, "open_isr",
        "Commercial High-Resolution Optical Scans & Port Queue Length Analysis",
        "SATELLITE", "zero_lag_macro_provider", "daily", "Port Logistics Bottlenecks",
    ),
)

ALL_SOURCES: Tuple[AlternativeDataSource, ...] = (
    _TIER1 + _TIER2 + _TIER3 + _DOC_ALIGNED + _TEN_PILLARS_FREE_STACK
)

# Deduplicate by source_id (first wins — tier-specific entry preferred)
_SEEN: Dict[str, AlternativeDataSource] = {}
for _row in ALL_SOURCES:
    _SEEN.setdefault(_row.source_id, _row)
SOURCES: Dict[str, AlternativeDataSource] = _SEEN

ALTERNATIVE_DATA_BINDINGS: List[Dict[str, Any]] = [
    src.to_binding() for src in SOURCES.values()
]


def sources_by_tier(tier: str) -> List[AlternativeDataSource]:
    key = str(tier or "").upper()
    return [s for s in SOURCES.values() if s.tier == key]


def sources_by_entity(entity: str) -> List[AlternativeDataSource]:
    key = str(entity or "").upper()
    return [s for s in SOURCES.values() if s.entity == key]


def golden_eight() -> List[AlternativeDataSource]:
    return [s for s in SOURCES.values() if s.golden_eight]


def registry_summary() -> Dict[str, Any]:
    by_tier: Dict[str, int] = {}
    for src in SOURCES.values():
        by_tier[src.tier] = by_tier.get(src.tier, 0) + 1
    return {
        "total_sources": len(SOURCES),
        "golden_eight": len(golden_eight()),
        "by_tier": by_tier,
        "entities_covered": sorted({s.entity for s in SOURCES.values()}),
        "execution_authority": False,
        "modeling_principles": list(NOWCAST_MODELING_PRINCIPLES),
    }


def coverage_gaps(entity_names: set[str]) -> List[str]:
    """Return entity names referenced by bindings but missing from catalog ENTITIES."""
    return sorted(
        {s.entity for s in SOURCES.values() if s.entity not in entity_names}
    )


def free_proxy_sources() -> List[AlternativeDataSource]:
    """Sources fetchable without paid API keys (may still need FRED/EIA env keys)."""
    return [s for s in SOURCES.values() if not s.requires_api_key]


class AlternativeDataRegistry:
    """Registry class interface for alternative and nowcast data sources."""
    SOURCES = SOURCES
    TIER_REALTIME = TIER_REALTIME
    TIER_DAILY = TIER_DAILY
    TIER_WEEKLY = TIER_WEEKLY

    def __init__(self):
        self.sources = SOURCES

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        return registry_summary()

    @classmethod
    def sources_by_tier(cls, tier: str) -> List[AlternativeDataSource]:
        return sources_by_tier(tier)

    @classmethod
    def golden_eight(cls) -> List[AlternativeDataSource]:
        return golden_eight()

    @classmethod
    def free_sources(cls) -> List[AlternativeDataSource]:
        return free_proxy_sources()
