"""Zero-Lag Macroeconomic & Physical Proxy Ingestion Provider.

Bypasses the 30-90 day publication delay of official government reports
(USDA WASDE, monthly CPI, quarterly GDP, JOLTS) by synthesizing real-time
physical proxies, satellite indicators, electrical grid telemetry, and
cross-commodity input-cost models.

Core Principles:
- Computes synthetic commodity input costs (Haber-Bosch ammonia, crack/crush spreads, corn/soy ratio).
- Models real-time US Net Liquidity (WALCL - TGA - RRP).
- Computes high-frequency physical nowcasts (Grid Load MW, Satellite NDVI, AIS Chokepoints, Scraped CPI).
- Strictly non-custodial: execution_authority is ALWAYS False.

P0-5 residual debt (ponytail): the placeholder values are still EMITTED,
now labelled SYNTHETIC_PLACEHOLDER in ``source_quality`` rather than dropped.
Ceiling: a consumer that ignores the label map still reads a constant as a
number (data/alternative_data_provider.py journals these with status="LIVE").
Upgrade path: null out the pure-constant fields and drop the LAUNCHER
entries in this file; that needs the four consumer files
(core/trading_tools.py, data/alternative_data_provider.py,
intelligence/macrofundamental_pipeline.py, data/market_data_provider.py)
updated in the same change, so it is deliberately not done unilaterally here.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ZeroLagMacroProvider")

# P0-5 provenance vocabulary. Every value this module emits carries one of
# these labels in ``source_quality`` so no downstream consumer (LLM prompt,
# nowcast journal, macro diagnostics) can read a hardcoded constant as an
# observation.
LIVE = "LIVE"
SYNTHETIC = "SYNTHETIC_PLACEHOLDER"


@dataclass
class SyntheticCommoditySpreads:
    """Real-time synthetic input-cost and relative value spreads."""
    synthetic_ammonia_cost_ton: float      # 33.5 * NatGas + OpEx ($/ton)
    corn_soy_price_ratio: float            # Soybeans / Corn price ratio
    corn_soy_regime: str                   # 'SOY_FAVORED', 'EQUILIBRIUM', 'CORN_FAVORED'
    crack_spread_321_bbl: float            # 3:2:1 Refinery margin ($/bbl)
    soybean_crush_spread_bu: float         # Crush margin (Meal + Oil - Soybeans) ($/bu)
    spark_spread_mwh: float                # Power price - (HeatRate * NatGas) ($/MWh)
    ethanol_blend_margin_gal: float        # Ethanol price - Net Corn Cost ($/gal)


@dataclass
class UniversalMacroRatios:
    """Master cross-asset macro ratios, physical stoichiometry spreads, and structural barometers."""
    timestamp_utc: str
    
    # 1. Macro Growth & Industrial Activity Ratios
    copper_gold_ratio: float               # Copper (lb) / Gold (oz) * 1000 -> Global Growth & Yield Barometer
    copper_oil_ratio: float                # Copper / WTI Crude -> Industrial Demand vs Supply Friction
    lumber_gold_ratio: float               # Lumber / Gold -> Housing / Risk-On Sentiment
    cyclicals_defensives_ratio: float      # Industrials / Utilities (XLI / XLU proxy)
    move_vix_ratio: float                  # Treasury Bond Volatility / Equity Volatility
    
    # 2. Energy & Refining Stoichiometry Spreads
    brent_wti_spread_usd: float            # Transatlantic Arbitrage & US Export Pull
    brent_dubai_spread_usd: float          # Sweet vs Sour & Asian Demand Premium (EFS)
    gasoil_crack_bbl_usd: float            # European Diesel Refined Margin vs Brent
    natgas_crude_parity_ratio: float       # CL / (6 * NatGas) -> Energy BTU Parity / Substitution
    crack_spread_321_bbl: float            # 3:2:1 Refinery Margin
    spark_spread_mwh: float                # Power - 7.0 * NatGas
    clean_dark_spread_mwh: float           # Power - Coal - Carbon
    
    # 3. Agricultural & Feed Processing Spreads
    haber_bosch_ammonia_cost_ton: float    # 33.5 * NatGas + $65/ton
    corn_soy_price_ratio: float            # Soybeans / Corn (Planting acre rotation)
    corn_soy_regime: str                   # 'SOY_FAVORED', 'EQUILIBRIUM', 'CORN_FAVORED'
    soybean_crush_spread_bu: float         # Meal + Oil - Soybeans
    cattle_crush_feedlot_margin_cwt: float # Live Cattle - Feeder Cattle - Corn Feed Cost
    hog_corn_ratio: float                  # Live Hogs / Corn (Breeding expansion vs liquidation)
    wheat_corn_spread_bu: float            # Wheat - Corn (Feed substitution threshold)
    ethanol_blend_margin_gal: float        # Ethanol - Net Corn Cost
    
    # 4. Metals, Materials & Smelting Margins
    gold_silver_ratio: float               # Gold / Silver -> Monetary vs Industrial Demand
    platinum_gold_ratio: float             # Platinum / Gold -> Autocatalyst vs Safe Haven
    steel_mill_spread_ton: float           # HRC Steel - 1.6*IronOre - 0.77*CokingCoal
    aluminum_smelter_margin_ton: float     # Aluminum - 2*Alumina - 14*Power
    
    # 5. Financial, Liquidity & Credit Stress Barometers
    hy_ig_credit_ratio: float              # US High Yield OAS / Investment Grade OAS
    net_liquidity_usd_billions: float      # WALCL - TGA - RRP
    net_liquidity_bias: str                # 'EXPANSIONARY', 'NEUTRAL', 'CONTRACTIONARY'
    
    execution_authority: bool = False
    
    # P0-5: per-field LIVE / SYNTHETIC_PLACEHOLDER labels. Shipped inside
    # to_dict() (not behind a side-channel getter) because every consumer that
    # feeds an LLM prompt goes through to_dict(): core/trading_tools.py
    # query_zero_lag_macro, intelligence/macrofundamental_pipeline.py, and the
    # on-disk cache. A label only the provider could produce would be read by
    # nobody.
    source_quality: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["execution_authority"] = False
        return d


@dataclass
class ZeroLagMacroSnapshot:
    """Instantaneous point-in-time macro state bypassing government survey delay."""
    timestamp_utc: str
    net_liquidity_usd_billions: float      # Fed Assets - TGA - RRP ($B)
    net_liquidity_bias: str                # 'EXPANSIONARY', 'NEUTRAL', 'CONTRACTIONARY'
    daily_cpi_mom_nowcast: float           # Scraped price + energy + ZORI lead nowcast (%)
    daily_cpi_yoy_nowcast: float           # Rolling 12m instantaneous price nowcast (%)
    industrial_grid_load_index: float      # Power grid MW index vs seasonal baseline (100 = norm)
    maritime_chokepoint_index: float       # AIS vessel throughput (100 = normal flow)
    satellite_crop_health_score: float     # Sentinel-2 / SMAP vegetation & moisture (0-100)
    labor_hiring_velocity: float           # Online job vacancy velocity (% MoM)
    synthetic_spreads: SyntheticCommoditySpreads
    data_latency_status: str               # 'ZERO_LAG (0-24h Physical Proxies)'
    execution_authority: bool = False
    
    # P0-5: see UniversalMacroRatios.source_quality.
    source_quality: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["execution_authority"] = False
        return d


class ZeroLagMacroProvider:
    """Fetches, calculates, and synthesizes zero-lag physical proxies and commodity spreads."""

    # Audit P0-5: fields whose value is a hardcoded constant, or a live quote
    # combined with a hardcoded anchor. A live upstream quote can never make
    # these honest, so they are stamped last and always win (see
    # ``_annotations``).
    _ALWAYS_SYNTHETIC = frozenset({
        "cyclicals_defensives_ratio",      # constant 128.0/72.0 XLI/XLU proxy
        "brent_dubai_spread_usd",          # Dubai leg is a hardcoded 80.20 anchor
        "gasoil_crack_bbl_usd",            # diesel anchor 104.70 - brent
        "spark_spread_mwh",                # power anchor 45.00 - 7.0 * natgas
        "clean_dark_spread_mwh",           # power/coal/carbon 45.00/120/68 anchors
        "steel_mill_spread_ton",           # HRC/iron ore/coking coal 720/108/220
        "aluminum_smelter_margin_ton",     # Al/alumina/power 2450/480/45 anchors
        "hy_ig_credit_ratio",              # constant 342.0 / 105.0 OAS
        "net_liquidity_usd_billions",      # WALCL/TGA baselines (RRP may refine)
        "move_vix_ratio",                  # MOVE numerator is a hardcoded 108.0
        "ethanol_blend_margin_gal",        # ethanol price is a hardcoded 1.65
    })

    # Audit P0-5: hardcoded snapshot proxies (see get_snapshot).
    _SNAPSHOT_PLACEHOLDERS = {
        "daily_cpi_mom_nowcast": SYNTHETIC,
        "daily_cpi_yoy_nowcast": SYNTHETIC,
        "industrial_grid_load_index": SYNTHETIC,
        "maritime_chokepoint_index": SYNTHETIC,
        "satellite_crop_health_score": SYNTHETIC,
        "labor_hiring_velocity": SYNTHETIC,
    }

    def __init__(self, cache_file: str = "artifacts/zero_lag_macro_cache.json") -> None:
        self.cache_file = cache_file
        self._yahoo_provider = None
        # symbol -> True when the cached value came from the live provider.
        # Written by _fetch_quote_safe at the moment the value is cached, so
        # provenance and value cannot drift apart.
        self._quote_live: Dict[str, bool] = {}
        # Provenance for the most recent compute pass; rebuilt (never merged)
        # so a prior cycle's LIVE labels cannot leak into a degraded one.
        self._last_quality: Dict[str, str] = {}

    def _get_yahoo(self):
        if self._yahoo_provider is None:
            try:
                from data.yahoo_finance_provider import YahooFinanceProvider
                self._yahoo_provider = YahooFinanceProvider()
            except Exception:
                try:
                    from yahoo_finance_provider import YahooFinanceProvider
                    self._yahoo_provider = YahooFinanceProvider()
                except Exception:
                    self._yahoo_provider = False
        return self._yahoo_provider if self._yahoo_provider is not False else None

    _QUOTE_TTL_S = 300.0  # cache live quotes for 5 minutes

    def _fetch_quote_safe(self, symbol: str, fallback: float) -> float:
        """Fetch latest price quote with graceful fallback.

        Uses YahooFinanceProvider.get_quote_data (guarded fetch + outage
        circuit breaker). Live quotes are cached for ``_QUOTE_TTL_S`` so the
        many symbols computed per snapshot do not hammer yfinance. When no
        live quote is available the caller's static fallback anchor is used
        and the observation stays non-custodial (execution_authority=False).
        """
        now = time.monotonic()
        cache = getattr(self, "_quote_cache", None)
        if cache is None:
            cache = self._quote_cache = {}
        cached = cache.get(symbol)
        if cached is not None and now - cached[0] < self._QUOTE_TTL_S:
            return cached[1]
        value = fallback
        is_live = False
        yp = self._get_yahoo()
        if yp is not None:
            try:
                quote = yp.get_quote_data(symbol)
                if quote and quote.get("price", 0) > 0:
                    value = float(quote["price"])
                    is_live = True
            except Exception as e:
                log.debug("Quote fetch failed for %s: %s", symbol, e)
        cache[symbol] = (now, value)
        # Provenance is stamped HERE, where "did the provider answer?" is
        # still known. Comparing the price against the fallback constant is
        # unsound (a live quote can legitimately equal the anchor).
        self._quote_live[symbol] = is_live
        return value

    def _quote_is_live(self, symbol: str) -> bool:
        """True when the cached value for *symbol* came from the live provider.

        Fail-closed: any symbol never fetched in this process reads
        SYNTHETIC. Reads only the flag written by _fetch_quote_safe, so it
        never issues a second network call and cannot disagree with the value.
        """
        return self._quote_live.get(symbol) is True

    # P0-5: ratio/spread field -> the upstream quote symbols it is derived
    # from. A field is LIVE only when EVERY input symbol was live, so a
    # partially-failed cycle degrades the field rather than upgrading it.
    # (Mapped field-first, not symbol-first, so no field's label can be
    # decided by dict ordering when several symbols feed it.)
    _RATIO_QUOTE_SYMBOLS = {
        "copper_gold_ratio": ("HG=F", "GC=F"),
        "copper_oil_ratio": ("HG=F", "CL=F"),
        "lumber_gold_ratio": ("LBS=F", "GC=F"),
        "crack_spread_321_bbl": ("CL=F", "RB=F", "HO=F"),
        "brent_wti_spread_usd": ("BZ=F", "CL=F"),
        "natgas_crude_parity_ratio": ("CL=F", "NG=F"),
        "haber_bosch_ammonia_cost_ton": ("NG=F",),
        "corn_soy_price_ratio": ("ZC=F", "ZS=F"),
        "corn_soy_regime": ("ZC=F", "ZS=F"),
        "soybean_crush_spread_bu": ("ZM=F", "ZL=F", "ZS=F"),
        "cattle_crush_feedlot_margin_cwt": ("LE=F", "GF=F", "ZC=F"),
        "hog_corn_ratio": ("HE=F", "ZC=F"),
        "wheat_corn_spread_bu": ("ZW=F", "ZC=F"),
        "gold_silver_ratio": ("GC=F", "SI=F"),
        "platinum_gold_ratio": ("PL=F", "GC=F"),
    }

    def _stamp_provenance(self) -> Dict[str, str]:
        """Rebuild the provenance map from scratch for this compute pass.

        Rebuild (never merge into a previous pass) so a LIVE stamp from an
        earlier cycle cannot survive into a later, fully-degraded one.
        """
        self._last_quality = {
            field: (LIVE if all(self._quote_is_live(s) for s in symbols)
                    else SYNTHETIC)
            for field, symbols in self._RATIO_QUOTE_SYMBOLS.items()
        }
        return self._last_quality

    def _annotations(self, ratio_quality: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Complete source-quality map: constants are applied LAST so they
        override any quote-derived label. The first-pass ordering let a live
        NG=F quote upgrade spark_spread_mwh (whose 45.00 power anchor is a
        constant) to LIVE; that is the exact laundering P0-5 is about.
        """
        quality: Dict[str, str] = dict(ratio_quality or self._last_quality or {})
        quality.update({name: SYNTHETIC for name in self._ALWAYS_SYNTHETIC})
        quality.update(self._SNAPSHOT_PLACEHOLDERS)
        return quality

    def source_quality_annotations(self) -> Dict[str, str]:
        """P0-5 honesty labels for the most recent compute pass.

        Fail-closed before the first pass: every constant field is labelled
        and no quote-derived field is claimed LIVE. Also shipped inside
        ``ZeroLagMacroSnapshot.to_dict()`` / ``UniversalMacroRatios.to_dict()``
        so prompt builders cannot bypass it.
        """
        return self._annotations()

    def compute_synthetic_commodity_spreads(self) -> SyntheticCommoditySpreads:
        """
        Computes real-time commodity input costs and relative value spreads:
        1. Ammonia Cash-Cost = 33.5 * HenryHub_NatGas + $65/ton OpEx
        2. Corn/Soy Ratio = Soybean_Price / Corn_Price (Equilibrium = 2.20 - 2.50)
        3. 3:2:1 Crack Spread = (2 * RBOB_gal * 42 + 1 * HeatingOil_gal * 42 - 3 * WTI_bbl) / 3
        4. Soybean Crush Margin = (Meal_ton * 0.022 + Oil_lb * 11) - Soybeans_bu
        5. Spark Spread = Power_Price - (7.0 HeatRate * NatGas)
        """
        # Fetch current commodity prices (with robust fallback anchors)
        natgas = self._fetch_quote_safe("NG=F", 2.65)
        crude = self._fetch_quote_safe("CL=F", 78.50)
        gasoline = self._fetch_quote_safe("RB=F", 2.45)
        heating_oil = self._fetch_quote_safe("HO=F", 2.60)
        corn = self._fetch_quote_safe("ZC=F", 4.35)
        soybeans = self._fetch_quote_safe("ZS=F", 10.40)
        soy_meal = self._fetch_quote_safe("ZM=F", 315.00)
        soy_oil = self._fetch_quote_safe("ZL=F", 0.44)

        # 1. Haber-Bosch Synthetic Ammonia Cost ($/ton)
        # Stoichiometry: ~33.5 MMBtu natural gas per short ton of NH3
        ammonia_cost = (33.5 * natgas) + 65.00

        # 2. Corn-Soybean Price Ratio
        corn_clean = max(corn, 0.50)
        corn_soy_ratio = soybeans / corn_clean
        if corn_soy_ratio > 2.50:
            corn_soy_regime = "SOY_FAVORED"      # Soybeans expensive -> plant Soybeans
        elif corn_soy_ratio < 2.20:
            corn_soy_regime = "CORN_FAVORED"     # Corn expensive -> plant Corn
        else:
            corn_soy_regime = "EQUILIBRIUM"

        # 3. 3:2:1 Refinery Crack Spread ($/bbl)
        # 3 barrels Crude -> 2 barrels Gasoline (42 gal/bbl) + 1 barrel Distillate (42 gal/bbl)
        gas_bbl = gasoline * 42.0
        ho_bbl = heating_oil * 42.0
        crack_321 = ((2.0 * gas_bbl) + (1.0 * ho_bbl) - (3.0 * crude)) / 3.0

        # 4. Soybean Crush Spread ($/bushel)
        # 1 bu Soybeans (60 lbs) yields ~44 lbs Meal + 11 lbs Oil
        meal_revenue = (soy_meal / 2000.0) * 44.0
        oil_revenue = soy_oil * 11.0
        crush_margin = (meal_revenue + oil_revenue) - soybeans

        # 5. Spark Spread ($/MWh)
        # Base power ~ $45/MWh, Heat rate 7.0 MMBtu/MWh
        spark_spread = 45.00 - (7.0 * natgas)

        # 6. Ethanol Blend Margin ($/gal)
        # 1 bu Corn yields ~2.8 gal Ethanol
        ethanol_price = 1.65
        net_corn_cost_gal = (corn_clean / 2.8) - 0.35  # minus DDGS co-product credit
        ethanol_margin = ethanol_price - net_corn_cost_gal

        return SyntheticCommoditySpreads(
            synthetic_ammonia_cost_ton=round(ammonia_cost, 2),
            corn_soy_price_ratio=round(corn_soy_ratio, 3),
            corn_soy_regime=corn_soy_regime,
            crack_spread_321_bbl=round(crack_321, 2),
            soybean_crush_spread_bu=round(crush_margin, 3),
            spark_spread_mwh=round(spark_spread, 2),
            ethanol_blend_margin_gal=round(ethanol_margin, 3),
        )

    def compute_universal_macro_ratios(self) -> UniversalMacroRatios:
        """
        Computes all master cross-asset macro ratios, physical stoichiometry spreads,
        and structural intermarket barometers.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        
        # 1. Fetch asset prices with resilient fallback anchors
        copper = self._fetch_quote_safe("HG=F", 4.45)
        gold = self._fetch_quote_safe("GC=F", 2450.00)
        silver = self._fetch_quote_safe("SI=F", 28.50)
        platinum = self._fetch_quote_safe("PL=F", 960.00)
        crude_wti = self._fetch_quote_safe("CL=F", 78.50)
        crude_brent = self._fetch_quote_safe("BZ=F", 82.50)
        gasoline = self._fetch_quote_safe("RB=F", 2.45)
        heating_oil = self._fetch_quote_safe("HO=F", 2.60)
        natgas = self._fetch_quote_safe("NG=F", 2.65)
        lumber = self._fetch_quote_safe("LBS=F", 540.00)
        corn = self._fetch_quote_safe("ZC=F", 4.35)
        soybeans = self._fetch_quote_safe("ZS=F", 10.40)
        soy_meal = self._fetch_quote_safe("ZM=F", 315.00)
        soy_oil = self._fetch_quote_safe("ZL=F", 0.44)
        live_cattle = self._fetch_quote_safe("LE=F", 185.00)
        feeder_cattle = self._fetch_quote_safe("GF=F", 250.00)
        live_hogs = self._fetch_quote_safe("HE=F", 88.00)
        wheat = self._fetch_quote_safe("ZW=F", 5.60)
        vix = self._fetch_quote_safe("^VIX", 15.50)
        
        # 2. Macro Growth & Industrial Activity Ratios
        cu_clean = max(copper, 0.01)
        au_clean = max(gold, 1.0)
        wti_clean = max(crude_wti, 1.0)
        copper_gold_ratio = (cu_clean / au_clean) * 1000.0   # e.g., ~1.81 (Historical norm: 1.5 - 2.5)
        copper_oil_ratio = cu_clean / (wti_clean / 100.0)    # Industrial demand vs Energy cost
        lumber_gold_ratio = lumber / au_clean               # Residential builder risk appetite
        cyclicals_defensives = 128.0 / 72.0                 # XLI / XLU proxy
        move_vix_ratio = 108.0 / max(vix, 1.0)              # Bond vol / Equity vol
        
        # 3. Energy & Refining Stoichiometry Spreads
        brent_wti_spread = crude_brent - crude_wti          # Transatlantic export pull
        brent_dubai_spread = crude_brent - 80.20            # Sweet vs Sour EFS premium
        gasoil_crack = 104.70 - crude_brent                 # Refined diesel margin
        natgas_clean = max(natgas, 0.1)
        natgas_crude_parity = crude_wti / (6.0 * natgas_clean) # Energy parity ratio
        gas_bbl = gasoline * 42.0
        ho_bbl = heating_oil * 42.0
        crack_321 = ((2.0 * gas_bbl) + (1.0 * ho_bbl) - (3.0 * crude_wti)) / 3.0
        spark_spread = 45.00 - (7.0 * natgas)
        clean_dark_spread = 45.00 - (0.38 * 120.0) - (0.85 * 68.0) # Power - Coal - Carbon
        
        # 4. Agricultural & Feed Processing Spreads
        ammonia_cost = (33.5 * natgas) + 65.00
        corn_clean = max(corn, 0.5)
        corn_soy_ratio = soybeans / corn_clean
        if corn_soy_ratio > 2.50:
            corn_soy_regime = "SOY_FAVORED"
        elif corn_soy_ratio < 2.20:
            corn_soy_regime = "CORN_FAVORED"
        else:
            corn_soy_regime = "EQUILIBRIUM"
        meal_rev = (soy_meal / 2000.0) * 44.0
        oil_rev = soy_oil * 11.0
        crush_margin = (meal_rev + oil_rev) - soybeans
        cattle_feedlot_margin = live_cattle - (feeder_cattle * 0.65) - (corn_clean * 6.5)
        hog_corn_ratio = live_hogs / corn_clean
        wheat_corn_spread = wheat - corn_clean
        ethanol_margin = 1.65 - ((corn_clean / 2.8) - 0.35)
        
        # 5. Metals, Materials & Smelting Margins
        gold_silver_ratio = au_clean / max(silver, 0.1)
        platinum_gold_ratio = platinum / au_clean
        steel_mill_spread = 720.0 - (1.6 * 108.0) - (0.77 * 220.0) # HRC - IronOre - CokingCoal
        aluminum_smelter_margin = 2450.0 - (2.0 * 480.0) - (14.0 * 45.0) # Al - Alumina - Power
        
        # 6. Financial Liquidity & Credit Stress
        hy_ig_credit_ratio = 342.0 / 105.0  # HY OAS / IG OAS
        net_liq, liq_bias = self.compute_net_liquidity()

        return UniversalMacroRatios(
            timestamp_utc=now_utc,
            copper_gold_ratio=round(copper_gold_ratio, 3),
            copper_oil_ratio=round(copper_oil_ratio, 3),
            lumber_gold_ratio=round(lumber_gold_ratio, 3),
            cyclicals_defensives_ratio=round(cyclicals_defensives, 3),
            move_vix_ratio=round(move_vix_ratio, 3),
            brent_wti_spread_usd=round(brent_wti_spread, 2),
            brent_dubai_spread_usd=round(brent_dubai_spread, 2),
            gasoil_crack_bbl_usd=round(gasoil_crack, 2),
            natgas_crude_parity_ratio=round(natgas_crude_parity, 3),
            crack_spread_321_bbl=round(crack_321, 2),
            spark_spread_mwh=round(spark_spread, 2),
            clean_dark_spread_mwh=round(clean_dark_spread, 2),
            haber_bosch_ammonia_cost_ton=round(ammonia_cost, 2),
            corn_soy_price_ratio=round(corn_soy_ratio, 3),
            corn_soy_regime=corn_soy_regime,
            soybean_crush_spread_bu=round(crush_margin, 3),
            cattle_crush_feedlot_margin_cwt=round(cattle_feedlot_margin, 2),
            hog_corn_ratio=round(hog_corn_ratio, 2),
            wheat_corn_spread_bu=round(wheat_corn_spread, 2),
            ethanol_blend_margin_gal=round(ethanol_margin, 3),
            gold_silver_ratio=round(gold_silver_ratio, 2),
            platinum_gold_ratio=round(platinum_gold_ratio, 3),
            steel_mill_spread_ton=round(steel_mill_spread, 2),
            aluminum_smelter_margin_ton=round(aluminum_smelter_margin, 2),
            hy_ig_credit_ratio=round(hy_ig_credit_ratio, 3),
            net_liquidity_usd_billions=net_liq,
            net_liquidity_bias=liq_bias,
            execution_authority=False,
            source_quality=self._annotations(self._stamp_provenance()),
        )

    def compute_net_liquidity(self) -> Tuple[float, str]:
        """
        Calculates real-time US Net Liquidity:
        Net Liquidity = Fed Total Assets (WALCL) - Treasury General Account (TGA) - Overnight RRP (ON RRP)
        """
        # Baseline institutional proxies (in billions USD)
        walcl = 6950.0   # Fed Total Assets (~$6.95T)
        tga = 780.0      # Treasury General Account (~$780B)
        on_rrp = 320.0   # Overnight Reverse Repo (~$320B)

        # Attempt to refine with live FRED / OpenBB if environment has keys
        fred_key = os.getenv("FRED_API_KEY", "")
        if fred_key:
            try:
                import urllib.request
                # Fast API fetch for RRP and WALCL
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id=RRPONTSYD&api_key={fred_key}&file_type=json&sort_order=desc&limit=1"
                req = urllib.request.Request(url, headers={"User-Agent": "AntigravityTrader/1.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    obs = data.get("observations", [])
                    if obs and obs[0].get("value") not in (".", ""):
                        on_rrp = float(obs[0]["value"])
            except Exception as e:
                log.debug("FRED live liquidity fetch fallback: %s", e)

        net_liq = walcl - tga - on_rrp

        if net_liq > 6000.0:
            bias = "EXPANSIONARY"
        elif net_liq < 5600.0:
            bias = "CONTRACTIONARY"
        else:
            bias = "NEUTRAL"

        return round(net_liq, 1), bias

    def get_snapshot(self) -> ZeroLagMacroSnapshot:
        """Synthesizes all real-time physical proxies into a complete zero-lag snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        spreads = self.compute_synthetic_commodity_spreads()
        net_liq, liq_bias = self.compute_net_liquidity()

        # High-frequency physical nowcasting proxies
        # 1. Daily CPI Nowcast (scraped prices + retail fuel + rent lead)
        daily_cpi_mom = 0.22  # +0.22% MoM nowcast
        daily_cpi_yoy = 2.85  # +2.85% YoY nowcast

        # 2. Industrial Electricity Grid Load Index (100 = seasonal benchmark)
        grid_load_idx = 101.4

        # 3. AIS Maritime Chokepoint Flow (100 = baseline transit count)
        chokepoint_idx = 98.2

        # 4. Satellite Crop Health NDVI / Soil Moisture (0 - 100)
        crop_health = 74.5

        # 5. Labor Demand Hiring Velocity (% MoM change in active vacancies)
        labor_velocity = -1.2

        snapshot = ZeroLagMacroSnapshot(
            timestamp_utc=now_utc,
            net_liquidity_usd_billions=net_liq,
            net_liquidity_bias=liq_bias,
            daily_cpi_mom_nowcast=daily_cpi_mom,
            daily_cpi_yoy_nowcast=daily_cpi_yoy,
            industrial_grid_load_index=grid_load_idx,
            maritime_chokepoint_index=chokepoint_idx,
            satellite_crop_health_score=crop_health,
            labor_hiring_velocity=labor_velocity,
            synthetic_spreads=spreads,
            data_latency_status="ZERO_LAG (0-24h Physical Proxies)",
            execution_authority=False,
            # get_snapshot() does NOT call compute_universal_macro_ratios(),
            # so the provenance map is rebuilt from this pass's own quote
            # fetches. setdefault-merging into a previous pass's map (as the
            # first pass did) let LIVE labels survive a fully-degraded cycle.
            source_quality=self._annotations(self._stamp_provenance()),
        )

        # Cache snapshot to disk for offline resilience
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, indent=2)
        except Exception as e:
            log.debug("Failed caching zero lag snapshot: %s", e)

        return snapshot

    def get_ten_pillars_bundle(self) -> Dict[str, Any]:
        """
        Returns an exhaustive structured bundle across all 10 free nowcasting macro pillars:
        1. Inflation (CPI/PCE)
        2. Economic Output (GDP/IP)
        3. Labor Market (NFP/JOLTS)
        4. Consumer Spending (Retail Sales)
        5. Housing & Construction
        6. Agriculture & Commodities
        7. Energy & Petroleum
        8. Maritime & Global Trade
        9. Corporate Credit Stress
        10. Monetary Policy & Search Sentiment
        """
        snap = self.get_snapshot()
        return {
            "timestamp_utc": snap.timestamp_utc,
            # P0-5: this bundle is almost entirely hardcoded constants, not
            # only the six snapshot proxies copied in below. The map says so
            # explicitly rather than letting "free_nowcasting_stack_status":
            # "ONLINE" imply observed feeds.
            "source_quality": snap.source_quality,
            # Only the fields listed in source_quality carry a label; every
            # other value in this bundle is a literal in get_ten_pillars_bundle
            # and is NOT an observation.
            "pillar_inputs_are_hardcoded_constants": True,
            "1_inflation_cpi_pce": {
                "fred_tips_5y_breakeven": 2.28,
                "fred_tips_10y_breakeven": 2.34,
                "daily_cpi_mom_nowcast": snap.daily_cpi_mom_nowcast,
                "daily_cpi_yoy_nowcast": snap.daily_cpi_yoy_nowcast,
                "redfin_price_drop_pct": 6.8,
                "open_meteo_temp_anomaly_c": +0.8,
                "beats_official": "CPI (15–45 days lag)",
            },
            "2_economic_output_gdp": {
                "us_grid_load_index": snap.industrial_grid_load_index,
                "entsoe_eu_power_load_mw": 284500.0,
                "aar_weekly_rail_carloads_k": 482.4,
                "nasa_viirs_nightlight_index": 102.1,
                "beats_official": "Quarterly GDP (30–90 days lag)",
            },
            "3_labor_market_nfp_jolts": {
                "google_trends_layoff_index": 38.0,
                "fred_initial_claims_k": 218.0,
                "linkup_job_vacancy_velocity_mom": snap.labor_hiring_velocity,
                "sec_edgar_layoff_filings_7d": 4,
                "beats_official": "NFP / JOLTS (7–65 days lag)",
            },
            "4_consumer_spending_retail": {
                "fred_retail_gasoline_gal": 3.42,
                "opentable_seated_diners_yoy_pct": -1.5,
                "credit_card_spending_proxy_index": 104.2,
                "beats_official": "Retail Sales (15–45 days lag)",
            },
            "5_housing_and_construction": {
                "zillow_zori_rent_yoy_pct": 3.1,
                "redfin_median_days_on_market": 34,
                "fred_building_permits_annualized_k": 1460.0,
                "usgs_sentinel_construction_index": 98.6,
                "beats_official": "Housing Starts (20–50 days lag)",
            },
            "6_agriculture_and_commodities": {
                "sentinel_crop_health_ndvi": snap.satellite_crop_health_score,
                "usda_nass_corn_good_excellent_pct": 67.0,
                "nasa_smap_soil_moisture_percentile": 54.0,
                "noaa_drought_pdsi_index": -1.2,
                "synthetic_ammonia_cost_ton": snap.synthetic_spreads.synthetic_ammonia_cost_ton,
                "corn_soy_price_ratio": snap.synthetic_spreads.corn_soy_price_ratio,
                "corn_soy_regime": snap.synthetic_spreads.corn_soy_regime,
                "beats_official": "USDA WASDE (30–60 days lag)",
            },
            "7_energy_and_petroleum": {
                "crack_spread_321_bbl": snap.synthetic_spreads.crack_spread_321_bbl,
                "soybean_crush_spread_bu": snap.synthetic_spreads.soybean_crush_spread_bu,
                "spark_spread_mwh": snap.synthetic_spreads.spark_spread_mwh,
                "noaa_hdd_heating_degree_days": 18.5,
                "skyfi_cushing_storage_utilization_pct": 52.4,
                "beats_official": "EIA Petroleum (5–12 days lag)",
            },
            "8_maritime_and_trade": {
                "chokepoint_ais_transit_index": snap.maritime_chokepoint_index,
                "marine_cadastre_us_port_dwell_hours": 38.2,
                "freightos_fbx_container_index_usd": 2450.0,
                "beats_official": "Trade Balance (35 days lag)",
            },
            "9_corporate_credit_stress": {
                "fred_high_yield_oas_bps": 342.0,
                "fred_ig_oas_bps": 105.0,
                "credit_regime": "BENIGN",
                "beats_official": "Corporate bankruptcy reports",
            },
            "10_monetary_policy_and_sentiment": {
                "net_liquidity_usd_billions": snap.net_liquidity_usd_billions,
                "net_liquidity_bias": snap.net_liquidity_bias,
                "google_trends_recession_search_index": 29.0,
                "google_trends_inflation_search_index": 44.0,
                "beats_official": "FOMC Minutes (21 days lag)",
            },
            "free_nowcasting_stack_status": "ONLINE",
            "execution_authority": False,
        }


# Convenience factory
_GLOBAL_ZERO_LAG_PROVIDER: Optional[ZeroLagMacroProvider] = None


def get_zero_lag_provider() -> ZeroLagMacroProvider:
    global _GLOBAL_ZERO_LAG_PROVIDER
    if _GLOBAL_ZERO_LAG_PROVIDER is None:
        _GLOBAL_ZERO_LAG_PROVIDER = ZeroLagMacroProvider()
    return _GLOBAL_ZERO_LAG_PROVIDER
