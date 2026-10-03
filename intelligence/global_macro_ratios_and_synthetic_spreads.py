"""Module 06: Universal Global Macro Ratios, Synthetic Physical Spreads & Intermarket Barometers.

Implements the Universal Master Compendium of Global Macro Ratios & Synthetic Stoichiometry Spreads:
1. Macro Growth & Risk Appetite:
       Copper/Gold, Copper/Oil, Lumber/Gold, XLI/XLU (Cyclicals/Defensives), MOVE/VIX
2. Energy & Refining Stoichiometry:
       3:2:1 Crack Spread, Brent-WTI Transatlantic, Brent-Dubai EFS, Gasoil Crack,
       BTU Energy Parity Ratio, Clean Spark Spread (Gas), Clean Dark Spread (Coal)
3. Agricultural & Feed Processing:
       Haber-Bosch NH3 Cash-Cost Floor, Corn/Soybean Ratio, Soybean Crush Spread,
       Cattle Crush Feedlot Margin, Hog/Corn Ratio, Wheat/Corn Feed Spread
4. Metals, Materials & Industrial Smelting:
       Gold/Silver Ratio, Platinum/Gold Ratio, Steel Mill HRC Spread, Aluminum Smelter Margin
5. Financial Plumbing, Liquidity & Credit Stress:
       HY/IG OAS Credit Default Ratio, US Net Liquidity (WALCL - TGA - ON_RRP)
6. Master Universal Macro Dashboard Evaluator
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


# ===========================================================================
# 1. Macro Growth, Industrial Activity & Risk Appetite Barometers
# ===========================================================================

def calculate_copper_gold_ratio(
    copper_price_lb: float,
    gold_price_oz: float,
) -> float:
    """Calculates the Copper / Gold Ratio (scaled x1000):
    Ratio = (P_Copper_lb / P_Gold_oz) * 1000
    Canonical Global Growth and 10Y Nominal Treasury Yield Barometer.
    """
    cu = max(1e-6, float(copper_price_lb))
    au = max(1e-6, float(gold_price_oz))
    return float((cu / au) * 1000.0)


def calculate_copper_oil_ratio(
    copper_price_lb: float,
    wti_crude_price_bbl: float,
) -> float:
    """Calculates the Copper / Crude Oil Ratio:
    Ratio = P_Copper / (P_WTI_Crude / 100)
    Industrial Demand vs. Supply-Side Energy Friction Barometer.
    """
    cu = max(1e-6, float(copper_price_lb))
    oil = max(1e-6, float(wti_crude_price_bbl))
    return float(cu / (oil / 100.0))


def calculate_lumber_gold_ratio(
    lumber_price_1000_board_ft: float,
    gold_price_oz: float,
) -> float:
    """Calculates the Lumber / Gold Ratio:
    Ratio = P_Lumber / P_Gold
    Leading Residential Housing & Cyclical Risk-On Barometer.
    """
    lumber = max(1e-6, float(lumber_price_1000_board_ft))
    gold = max(1e-6, float(gold_price_oz))
    return float(lumber / gold)


def calculate_cyclicals_defensives_ratio(
    xli_industrials_price: float,
    xlu_utilities_price: float,
) -> float:
    """Calculates Cyclicals / Defensives Ratio (XLI / XLU):
    Ratio = P_XLI / P_XLU
    Internal Equity Market Risk Appetite Barometer.
    """
    xli = max(1e-6, float(xli_industrials_price))
    xlu = max(1e-6, float(xlu_utilities_price))
    return float(xli / xlu)


def calculate_move_vix_ratio(
    move_index: float,
    vix_index: float,
) -> float:
    """Calculates MOVE / VIX Ratio:
    Ratio = MOVE_Index / VIX_Index
    Bond Market Volatility vs. Equity Volatility Divergence Barometer.
    """
    move = max(1e-6, float(move_index))
    vix = max(1e-6, float(vix_index))
    return float(move / vix)


# ===========================================================================
# 2. Energy, Refining & Power Generation Stoichiometry Spreads
# ===========================================================================

def calculate_three_two_one_crack_spread(
    gasoline_price_gal: float,
    heating_oil_price_gal: float,
    crude_oil_price_bbl: float,
) -> float:
    """Calculates the 3:2:1 Refinery Crack Spread ($/bbl):
    Crack = [2 * (42 * P_Gasoline) + 1 * (42 * P_HeatingOil) - 3 * P_Crude] / 3
    Refinery profitability floor for converting 3 barrels crude into 2 gasoline + 1 distillate.
    """
    gas = float(gasoline_price_gal)
    ho = float(heating_oil_price_gal)
    crude = float(crude_oil_price_bbl)

    prod_rev = 2.0 * (42.0 * gas) + 1.0 * (42.0 * ho)
    crude_cost = 3.0 * crude
    return float((prod_rev - crude_cost) / 3.0)


def calculate_brent_wti_transatlantic_spread(
    brent_price_bbl: float,
    wti_price_bbl: float,
) -> float:
    """Calculates Brent - WTI Transatlantic Spread ($/bbl):
    Spread = P_Brent - P_WTI
    US Crude Export Arbitrage incentive.
    """
    return float(float(brent_price_bbl) - float(wti_price_bbl))


def calculate_brent_dubai_efs_spread(
    brent_price_bbl: float,
    dubai_price_bbl: float,
) -> float:
    """Calculates Brent - Dubai Exchange of Futures for Swaps (EFS) ($/bbl):
    EFS = P_Brent - P_Dubai
    Sweet vs. Sour Arbitrage and Asian crude procurement incentive.
    """
    return float(float(brent_price_bbl) - float(dubai_price_bbl))


def calculate_gasoil_crack_spread(
    gasoil_price_bbl: float,
    brent_price_bbl: float,
) -> float:
    """Calculates Gasoil Crack Spread ($/bbl):
    Spread = P_LowSulfurGasoil - P_Brent
    Global industrial transport, trucking, and maritime diesel margin.
    """
    return float(float(gasoil_price_bbl) - float(brent_price_bbl))


def calculate_btu_energy_parity_ratio(
    crude_oil_price_bbl: float,
    natural_gas_price_mmbtu: float,
) -> float:
    """Calculates BTU Energy Parity Ratio:
    Ratio = P_WTI_Crude / (6.0 * P_HenryHub_NatGas)
    Thermal fuel switching incentive between oil products and natural gas.
    """
    oil = max(1e-6, float(crude_oil_price_bbl))
    gas = max(1e-6, float(natural_gas_price_mmbtu))
    return float(oil / (6.0 * gas))


def calculate_clean_spark_spread(
    power_price_mwh: float,
    natural_gas_price_mmbtu: float,
    carbon_cost_per_ton: float = 0.0,
    heat_rate_mmbtu_per_mwh: float = 7.0,
) -> float:
    """Calculates Clean Spark Spread ($/MWh):
    Spark = P_Power - (HeatRate * P_NatGas) - CarbonCost
    Operating margin of gas-fired combined-cycle power generation plants.
    """
    pwr = float(power_price_mwh)
    gas = float(natural_gas_price_mmbtu)
    carb = float(carbon_cost_per_ton)
    hr = float(heat_rate_mmbtu_per_mwh)
    return float(pwr - (hr * gas) - carb)


def calculate_clean_dark_spread(
    power_price_mwh: float,
    coal_price_ton: float,
    carbon_cost_per_ton: float = 0.0,
    heat_rate_tons_per_mwh: float = 0.38,
    carbon_intensity_per_ton: float = 0.85,
) -> float:
    """Calculates Clean Dark Spread ($/MWh):
    Dark = P_Power - (0.38 * P_Coal) - (0.85 * CarbonCost)
    Operating margin of coal-fired baseload power plants.
    """
    pwr = float(power_price_mwh)
    coal = float(coal_price_ton)
    carb = float(carbon_cost_per_ton)
    hr = float(heat_rate_tons_per_mwh)
    ci = float(carbon_intensity_per_ton)
    return float(pwr - (hr * coal) - (ci * carb))


# ===========================================================================
# 3. Agricultural, Feedlot & Fertilizer Conversion Spreads
# ===========================================================================

def calculate_haber_bosch_ammonia_cash_cost(
    natural_gas_price_mmbtu: float,
    base_operating_cost: float = 65.0,
    mmbtu_per_ton: float = 33.5,
) -> float:
    """Calculates Haber-Bosch Synthetic Nitrogen Ammonia Cash-Cost Floor ($/ton NH3):
    Cost = (33.5 * P_NatGas) + $65.00/ton
    """
    gas = max(0.1, float(natural_gas_price_mmbtu))
    op = float(base_operating_cost)
    stoich = float(mmbtu_per_ton)
    return float(stoich * gas + op)


def calculate_corn_soybean_price_ratio(
    soybean_price_bu: float,
    corn_price_bu: float,
) -> Dict[str, Any]:
    """Calculates the Corn / Soybean Price Ratio:
    Ratio = P_Soybeans / P_Corn
    Determines US Midwestern acreage planting allocation:
    - Ratio > 2.50: Soybean favored
    - 2.20 <= Ratio <= 2.50: Equilibrium
    - Ratio < 2.20: Corn favored
    """
    soy = max(1e-6, float(soybean_price_bu))
    corn = max(1e-6, float(corn_price_bu))
    ratio = soy / corn

    if ratio > 2.50:
        regime = "SOYBEAN_PLANTING_FAVORED"
        acreage_shift = "FARMERS_SHIFT_ACRES_TO_SOY"
    elif ratio < 2.20:
        regime = "CORN_PLANTING_FAVORED"
        acreage_shift = "FARMERS_SHIFT_ACRES_TO_CORN"
    else:
        regime = "BALANCED_ACREAGE_EQUILIBRIUM"
        acreage_shift = "NORMAL_ROTATION"

    return {
        "corn_soy_ratio": round(float(ratio), 4),
        "acreage_regime": regime,
        "implied_acreage_shift": acreage_shift,
    }


def calculate_soybean_crush_spread(
    soybean_meal_price_ton: float,
    soybean_oil_price_lb: float,
    soybean_price_bu: float,
) -> float:
    """Calculates Soybean Crush Spread ($/bushel):
    Crush = [(P_Meal / 2000) * 44] + [P_Oil * 11] - P_Soybean
    Crushing 1 bushel (60 lbs) yields 44 lbs meal + 11 lbs oil.
    """
    meal = float(soybean_meal_price_ton)
    oil = float(soybean_oil_price_lb)
    soy = float(soybean_price_bu)

    meal_val = (meal / 2000.0) * 44.0
    oil_val = oil * 11.0
    return float(meal_val + oil_val - soy)


def calculate_cattle_crush_feedlot_margin(
    live_cattle_price_cwt: float,
    feeder_cattle_price_cwt: float,
    corn_price_bu: float,
) -> float:
    """Calculates Cattle Crush / Feedlot Operating Margin ($/cwt):
    Margin = P_LiveCattle - (0.65 * P_FeederCattle) - (6.5 * P_Corn / 10)
    Fattening a 750 lb feeder steer to a 1,350 lb slaughter live cattle animal.
    """
    live = float(live_cattle_price_cwt)
    feeder = float(feeder_cattle_price_cwt)
    corn = float(corn_price_bu)

    feeder_cost = 0.65 * feeder
    corn_cost = 0.65 * corn  # Scaled feed allocation
    return float(live - feeder_cost - corn_cost)


def calculate_hog_corn_ratio(
    live_hog_price_cwt: float,
    corn_price_bu: float,
) -> float:
    """Calculates Hog / Corn Ratio:
    Ratio = P_LiveHogs_cwt / P_Corn_bu
    Measures profitability of pork production and breeding herd expansion/liquidation.
    """
    hog = max(1e-6, float(live_hog_price_cwt))
    corn = max(1e-6, float(corn_price_bu))
    return float(hog / corn)


def calculate_wheat_corn_feed_spread(
    wheat_price_bu: float,
    corn_price_bu: float,
) -> float:
    """Calculates Wheat - Corn Feed Grain Spread ($/bushel):
    Spread = P_Wheat - P_Corn
    Spread < $0.60/bu triggers livestock feed substitution of wheat for corn.
    """
    return float(float(wheat_price_bu) - float(corn_price_bu))


# ===========================================================================
# 4. Metals, Materials & Industrial Smelting Margins
# ===========================================================================

def calculate_gold_silver_ratio(
    gold_price_oz: float,
    silver_price_oz: float,
) -> float:
    """Calculates Gold / Silver Ratio:
    Ratio = P_Gold / P_Silver
    Monetary debasement vs. Industrial manufacturing demand ratio.
    """
    au = max(1e-6, float(gold_price_oz))
    ag = max(1e-6, float(silver_price_oz))
    return float(au / ag)


def calculate_platinum_gold_ratio(
    platinum_price_oz: float,
    gold_price_oz: float,
) -> float:
    """Calculates Platinum / Gold Ratio:
    Ratio = P_Platinum / P_Gold
    Autocatalyst & Industrial Demand vs. Safe Haven Gold.
    """
    pt = max(1e-6, float(platinum_price_oz))
    au = max(1e-6, float(gold_price_oz))
    return float(pt / au)


def calculate_steel_mill_hrc_spread(
    hrc_steel_price_ton: float,
    iron_ore_price_ton: float,
    coking_coal_price_ton: float,
) -> float:
    """Calculates Steel Mill Blast Furnace Hot-Rolled Coil (HRC) Margin ($/ton):
    Spread = P_HRC - (1.6 * P_IronOre) - (0.77 * P_CokingCoal)
    Producing 1 ton HRC requires ~1.6 tons iron ore + ~0.77 tons metallurgical coking coal.
    """
    hrc = float(hrc_steel_price_ton)
    ore = float(iron_ore_price_ton)
    coal = float(coking_coal_price_ton)
    return float(hrc - (1.6 * ore) - (0.77 * coal))


def calculate_aluminum_smelter_margin(
    aluminum_price_ton: float,
    alumina_price_ton: float,
    power_price_mwh: float,
) -> float:
    """Calculates Aluminum Hall-Heroult Electrolytic Smelter Margin ($/ton):
    Margin = P_Aluminum - (2.0 * P_Alumina) - (14.0 * P_Power)
    Converting alumina to aluminum requires ~2 tons alumina + ~14 MWh electricity.
    """
    al = float(aluminum_price_ton)
    alumina = float(alumina_price_ton)
    pwr = float(power_price_mwh)
    return float(al - (2.0 * alumina) - (14.0 * pwr))


# ===========================================================================
# 5. Financial Plumbing, Liquidity & Credit Stress
# ===========================================================================

def calculate_high_yield_investment_grade_oas_ratio(
    hy_oas_bps: float,
    ig_oas_bps: float,
) -> float:
    """Calculates US High Yield / Investment Grade OAS Credit Spread Ratio:
    Ratio = US_HY_OAS_bps / US_IG_OAS_bps
    Default risk acceleration and credit distress contagion barometer.
    """
    hy = max(1.0, float(hy_oas_bps))
    ig = max(1.0, float(ig_oas_bps))
    return float(hy / ig)


def calculate_us_net_liquidity(
    fed_total_assets_walcl_billion: float,
    treasury_general_account_tga_billion: float,
    overnight_reverse_repo_onrrp_billion: float,
) -> float:
    """Calculates US Net Liquidity ($ Billion):
    NetLiquidity = Fed_Assets (WALCL) - Treasury_Cash (TGA) - Reverse_Repo (ON_RRP)
    True financial plumbing dollar liquidity driving risk asset cycles.
    """
    walcl = float(fed_total_assets_walcl_billion)
    tga = float(treasury_general_account_tga_billion)
    onrrp = float(overnight_reverse_repo_onrrp_billion)
    return float(walcl - tga - onrrp)


# ===========================================================================
# 6. Master Universal Macro Dashboard Evaluator
# ===========================================================================

def evaluate_universal_macro_dashboard(
    macro_inputs: Dict[str, float],
) -> Dict[str, Any]:
    """Evaluates the complete 23-barometer multi-sector global macro dashboard."""
    # 1. Growth & Risk
    cu_au = calculate_copper_gold_ratio(
        macro_inputs.get("copper_price_lb", 4.20),
        macro_inputs.get("gold_price_oz", 2400.0),
    )
    cu_oil = calculate_copper_oil_ratio(
        macro_inputs.get("copper_price_lb", 4.20),
        macro_inputs.get("wti_crude_price_bbl", 75.0),
    )
    lumber_au = calculate_lumber_gold_ratio(
        macro_inputs.get("lumber_price_1000_board_ft", 550.0),
        macro_inputs.get("gold_price_oz", 2400.0),
    )
    xli_xlu = calculate_cyclicals_defensives_ratio(
        macro_inputs.get("xli_price", 125.0),
        macro_inputs.get("xlu_price", 70.0),
    )
    move_vix = calculate_move_vix_ratio(
        macro_inputs.get("move_index", 110.0),
        macro_inputs.get("vix_index", 16.0),
    )

    # 2. Energy
    crack_321 = calculate_three_two_one_crack_spread(
        macro_inputs.get("gasoline_price_gal", 2.40),
        macro_inputs.get("heating_oil_price_gal", 2.60),
        macro_inputs.get("wti_crude_price_bbl", 75.0),
    )
    brent_wti = calculate_brent_wti_transatlantic_spread(
        macro_inputs.get("brent_price_bbl", 80.0),
        macro_inputs.get("wti_crude_price_bbl", 75.0),
    )
    brent_dubai = calculate_brent_dubai_efs_spread(
        macro_inputs.get("brent_price_bbl", 80.0),
        macro_inputs.get("dubai_price_bbl", 78.0),
    )
    btu_parity = calculate_btu_energy_parity_ratio(
        macro_inputs.get("wti_crude_price_bbl", 75.0),
        macro_inputs.get("natgas_price_mmbtu", 2.50),
    )

    # 3. Agriculture & Ammonia
    ammonia_cost = calculate_haber_bosch_ammonia_cash_cost(
        macro_inputs.get("natgas_price_mmbtu", 2.50),
    )
    corn_soy = calculate_corn_soybean_price_ratio(
        macro_inputs.get("soybean_price_bu", 11.50),
        macro_inputs.get("corn_price_bu", 4.50),
    )
    soy_crush = calculate_soybean_crush_spread(
        macro_inputs.get("soybean_meal_price_ton", 350.0),
        macro_inputs.get("soybean_oil_price_lb", 0.45),
        macro_inputs.get("soybean_price_bu", 11.50),
    )

    # 4. Metals
    au_ag = calculate_gold_silver_ratio(
        macro_inputs.get("gold_price_oz", 2400.0),
        macro_inputs.get("silver_price_oz", 28.0),
    )
    steel_hrc = calculate_steel_mill_hrc_spread(
        macro_inputs.get("hrc_steel_price_ton", 850.0),
        macro_inputs.get("iron_ore_price_ton", 110.0),
        macro_inputs.get("coking_coal_price_ton", 240.0),
    )

    # 5. Financial Plumbing
    hy_ig = calculate_high_yield_investment_grade_oas_ratio(
        macro_inputs.get("hy_oas_bps", 320.0),
        macro_inputs.get("ig_oas_bps", 100.0),
    )
    net_liq = calculate_us_net_liquidity(
        macro_inputs.get("walcl_billion", 7200.0),
        macro_inputs.get("tga_billion", 750.0),
        macro_inputs.get("onrrp_billion", 450.0),
    )

    # Synthesis
    regime_summary = {
        "global_growth_bias": "EXPANSION" if cu_au >= 1.75 else "CONTRACTION_RECESSION",
        "energy_refining_state": "HEALTHY_MARGIN" if crack_321 >= 18.0 else "REFINERY_MARGIN_COMPRESSION",
        "agricultural_allocation": corn_soy["acreage_regime"],
        "credit_liquidity_environment": "EXPANDING_LIQUIDITY" if hy_ig < 3.5 and net_liq > 5500.0 else "CREDIT_STRESS",
    }

    return {
        "growth_and_risk": {
            "copper_gold_ratio": round(cu_au, 3),
            "copper_oil_ratio": round(cu_oil, 3),
            "lumber_gold_ratio": round(lumber_au, 3),
            "cyclicals_defensives_xli_xlu": round(xli_xlu, 3),
            "move_vix_ratio": round(move_vix, 3),
        },
        "energy_and_refining": {
            "crack_spread_321_bbl": round(crack_321, 2),
            "brent_wti_spread_bbl": round(brent_wti, 2),
            "brent_dubai_efs_bbl": round(brent_dubai, 2),
            "btu_energy_parity_ratio": round(btu_parity, 2),
        },
        "agriculture_and_fertilizer": {
            "haber_bosch_ammonia_floor_ton": round(ammonia_cost, 2),
            "corn_soybean_ratio": corn_soy["corn_soy_ratio"],
            "soybean_crush_spread_bu": round(soy_crush, 2),
        },
        "metals_and_smelting": {
            "gold_silver_ratio": round(au_ag, 2),
            "steel_mill_hrc_spread_ton": round(steel_hrc, 2),
        },
        "financial_plumbing_and_liquidity": {
            "hy_ig_oas_ratio": round(hy_ig, 3),
            "us_net_liquidity_billion": round(net_liq, 1),
        },
        "macro_synthesis": regime_summary,
    }
