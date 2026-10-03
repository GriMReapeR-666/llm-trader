"""Autonomous Case Study Matching, Graph Cognition & Analogical Reasoning Engine.

Matches real-time emerging macro/geopolitical shocks against historical case study
archetypes using Case-Based Reasoning (CBR), multi-dimensional vector distances,
and graph isomorphic path matching.

Mathematical Formulations:
1. Jaccard Similarity Coefficient:
       J(A, B) = |A cap B| / |A cup B|
2. Multi-Dimensional Composite Case Similarity:
       S(E, C_k) = w_ent * J(V_E^ent, V_C^ent) + w_choke * J(V_E^choke, V_C^choke)
                   + w_spread * J(V_E^spread, V_C^spread) + w_dom * I(Domain_E == Domain_C)
3. Causal Graph Isomorphic Path Traversal & Cycle-Free Depth-Limited Search
4. Candidate Dark Node Detection & Online Causal Discovery Trigger
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


logger = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_case_based_jaccard_similarity(
    set_a: Sequence[str],
    set_b: Sequence[str],
) -> float:
    """Calculates Jaccard Similarity Coefficient between two sets of string tokens:
    J(A, B) = |A cap B| / |A cup B|
    Safely handles empty sets, whitespace, and case-insensitivity.
    """
    a_tokens = {str(x).strip().upper() for x in set_a if str(x).strip()}
    b_tokens = {str(y).strip().upper() for y in set_b if str(y).strip()}

    if not a_tokens and not b_tokens:
        return 0.0

    union_len = len(a_tokens.union(b_tokens))
    if union_len == 0:
        return 0.0

    intersection_len = len(a_tokens.intersection(b_tokens))
    return float(intersection_len / union_len)


def calculate_multi_dimensional_composite_case_similarity(
    entities_event: Sequence[str],
    entities_case: Sequence[str],
    chokepoints_event: Sequence[str],
    chokepoints_case: Sequence[str],
    spreads_event: Sequence[str],
    spreads_case: Sequence[str],
    domain_event: str,
    domain_case: str,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Calculates multi-dimensional composite similarity score S(E, C_k) in [0.0, 1.0]."""
    w = weights or {
        "entities": 0.35,
        "chokepoints": 0.25,
        "spreads": 0.15,
        "domain": 0.25,
    }

    j_ent = calculate_case_based_jaccard_similarity(entities_event, entities_case)
    j_choke = calculate_case_based_jaccard_similarity(chokepoints_event, chokepoints_case)
    j_spread = calculate_case_based_jaccard_similarity(spreads_event, spreads_case)
    dom_match = 1.0 if str(domain_event).strip().upper() == str(domain_case).strip().upper() else 0.0

    score = (
        w.get("entities", 0.35) * j_ent +
        w.get("chokepoints", 0.25) * j_choke +
        w.get("spreads", 0.15) * j_spread +
        w.get("domain", 0.25) * dom_match
    )
    return float(min(1.0, max(0.0, score)))


def traverse_causal_graph_isomorphic_path(
    start_node: str,
    causal_dag: Dict[str, List[str]],
    max_depth: int = 4,
) -> List[List[str]]:
    """Traverses directed causal chains with cycle prevention and depth-limit guards."""
    start = str(start_node).strip().upper()
    depth_limit = max(1, int(max_depth))

    paths: List[List[str]] = []

    def dfs(current: str, current_path: List[str], visited: Set[str]):
        if len(current_path) >= depth_limit:
            paths.append(list(current_path))
            return

        children = causal_dag.get(current, [])
        if not children:
            paths.append(list(current_path))
            return

        for child in children:
            ch_norm = str(child).strip().upper()
            if ch_norm not in visited:
                visited.add(ch_norm)
                current_path.append(ch_norm)
                dfs(ch_norm, current_path, visited)
                current_path.pop()
                visited.remove(ch_norm)
            else:
                paths.append(list(current_path))

    dfs(start, [start], {start})
    return paths


def detect_candidate_dark_nodes(
    observed_shocks: Sequence[str],
    existing_graph_nodes: Sequence[str],
) -> List[str]:
    """Identifies unmapped disruption nodes and flags candidate dark nodes for online causal discovery."""
    existing_set = {str(n).strip().upper() for n in existing_graph_nodes}
    dark_nodes = []

    for shock in observed_shocks:
        sh_norm = str(shock).strip().upper()
        if sh_norm and sh_norm not in existing_set:
            dark_nodes.append(sh_norm)

    return list(set(dark_nodes))


@dataclass(frozen=True)
class CaseStudyArchetype:
    """Historical or theoretical case study profile for analogical pattern matching."""
    case_id: str
    label: str
    primary_domain: str              # e.g., 'MARITIME_CHOKEPOINT', 'ENERGY_PETROCHEM', 'TECH_HARDWARE'
    historical_date_window: str      # e.g., '2023-10 to 2024-06'
    chokepoints_affected: Tuple[str, ...]
    primary_entities: Tuple[str, ...]
    transmission_channels: Tuple[str, ...]
    lead_time_days: int
    duration_days: int
    tau_substitution_horizon: str   # 'DAYS', 'SEASON', 'YEARS', 'DECADES'
    core_spread_distortions: Tuple[str, ...]
    prompt_curve_regime: str         # 'BACKWARDATION', 'CONTANGO', 'INVERTED'
    historical_outcomes: Dict[str, str]
    falsification_tripwires: Tuple[str, ...]
    reference_url: str


# ===========================================================================
# Master Knowledge Base of 16 Historical & Structural Case Studies
# ===========================================================================

CASE_STUDY_REGISTRY: Dict[str, CaseStudyArchetype] = {
    "case_a_hormuz_blockade": CaseStudyArchetype(
        case_id="case_a_hormuz_blockade",
        label="Strait of Hormuz Petroleum & Fertilizer Transit Blockade",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="1984 Tanker War / 2019 Attacks / Structural Model",
        chokepoints_affected=("STRAIT_OF_HORMUZ", "PERSIAN_GULF"),
        primary_entities=("WTI_OIL", "INFLATION", "CROP_HEALTH", "CONSUMER_SPENDING"),
        transmission_channels=("CRUDE_SPIKE", "HABER_BOSCH_AMMONIA_SURGE", "CORN_COST_INFLATION", "SOY_SUBSTITUTION"),
        lead_time_days=0,
        duration_days=90,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("AMMONIA_CASH_COST", "CORN_SOY_RATIO", "CRACK_SPREAD_321"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "oil": "Brent & WTI spike +25-40%",
            "fertilizer": "Anhydrous ammonia surges from $480 to $700/ton",
            "agri_spread": "Corn breakeven surges; farmers shift acres to Soybeans; Corn/Soy ratio collapses",
        },
        falsification_tripwires=(
            "Saudi East-West crude pipeline bypass opens 5M bpd capacity",
            "Natural gas prices fail to transmit to urea/ammonia plants",
        ),
        reference_url="https://www.eia.gov/international/analysis/special-topics/World_Oil_Transit_Chokepoints",
    ),
    "case_b_red_sea_reroute": CaseStudyArchetype(
        case_id="case_b_red_sea_reroute",
        label="Red Sea / Bab el-Mandeb Cape of Good Hope Maritime Reroute",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2023-11 to 2024-08",
        chokepoints_affected=("BAB_EL_MANDEB", "SUEZ_CANAL", "RED_SEA"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION", "WTI_OIL"),
        transmission_channels=("CAPE_TRANSIT_REROUTE", "CONTAINER_CAPACITY_SHRINK", "BUNKER_FUEL_SURGE", "IMPORT_PRICE_INFLATION"),
        lead_time_days=10,
        duration_days=240,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("FREIGHTOS_FBX_CONTAINER", "BRENT_DUBAI_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "freight": "Freightos FBX spot container rates surge from $1,200 to $4,500/FEU (+275%)",
            "transit": "+14 days transit and +3,500 nautical miles around Africa",
            "macro": "European import goods deflation stalls; consumer delivery lead times expand",
        },
        falsification_tripwires=(
            "Naval escort coalition restores >80% commercial transit through Bab el-Mandeb",
            "Insurance P&I war risk surcharges collapse below 0.2% of hull value",
        ),
        reference_url="https://www.freightos.com/freight-blog/freightos-baltic-index-fbx-global-container-pricing-index/",
    ),
    "case_c_ukraine_neon_semis": CaseStudyArchetype(
        case_id="case_c_ukraine_neon_semis",
        label="Ukrainian Neon Gas Bottleneck & DUV Lithography Shock",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2022-02 to 2022-12",
        chokepoints_affected=("BLACK_SEA_PORTS", "MARIUPOL_PURIFICATION"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "INFLATION"),
        transmission_channels=("NEON_PURIFICATION_HALT", "EXCIMER_LASER_SPIKE", "WAFER_ALLOCATION_QUOTAS", "AUTO_MCU_SHORTAGE"),
        lead_time_days=45,
        duration_days=300,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("NEON_PURITY_PREMIUM", "MANHEIM_USED_VEHICLE_INDEX"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "neon": "Excimer laser-grade neon gas spikes 600%",
            "chips": "Automotive microcontroller lead times expand to 52 weeks",
            "auto": "Global automakers cut millions of units; used car prices surge +30%",
        },
        falsification_tripwires=(
            "Air Liquide / Linde air-separation plants scale non-Ukrainian noble gas recovery within 90 days",
        ),
        reference_url="https://www.csis.org/analysis/russia-ukraine-conflict-and-semiconductor-supply-chain",
    ),
    "case_d_fukushima_thailand_hdd": CaseStudyArchetype(
        case_id="case_d_fukushima_thailand_hdd",
        label="Fukushima Disaster + Thailand Monsoon Floods JIT Electronics Collapse",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2011-03 to 2011-12",
        chokepoints_affected=("BANGKOK_INDUSTRIAL_PARKS", "TOHOKU_AUTO_CLUSTERS"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "EQUITIES"),
        transmission_channels=("HDD_MOTOR_FLOODING", "SERVER_LEAD_TIME_EXPANSION", "CLOUD_MIGRATION_ACCELERATION"),
        lead_time_days=14,
        duration_days=270,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("HDD_SPOT_PRICING", "HYPERSCALE_CAPEX_VELOCITY"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "hdd": "50% of global Hard Disk Drive assembly submerged offline in Bangkok",
            "hardware": "Enterprise server delivery lead times surge to 9 months",
            "paradigm": "Accelerated permanent enterprise migration to hyperscale cloud infrastructure (AWS)",
        },
        falsification_tripwires=(
            "Drive assembly plants pump out water and resume production within 30 days",
        ),
        reference_url="https://www.reuters.com/article/us-thailand-floods-tech-idUSTRE79T1BC20111030",
    ),
    "case_e_texas_storm_uri_pvc": CaseStudyArchetype(
        case_id="case_e_texas_storm_uri_pvc",
        label="Texas Winter Storm Uri ERCOT Petrochemical Freeze",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2021-02 to 2021-06",
        chokepoints_affected=("ERCOT_POWER_GRID", "US_GULF_COAST_REFINERIES"),
        primary_entities=("ENERGY_DEMAND", "GRID_LOAD", "HOUSING_ACTIVITY", "INFLATION"),
        transmission_channels=("POWER_GRID_COLLAPSE", "POLYMER_FREEZE_OFF", "PVC_PIPE_SHORTAGE", "LUMBER_SUBSTITUTION"),
        lead_time_days=2,
        duration_days=120,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("SPARK_SPREAD", "LUMBER_FUTURES_CURVE"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "petrochem": "60% of US PVC, ethylene, and propylene production offline",
            "construction": "Residential plumbing pipe vanish; home completion delays surge",
            "lumber": "Lumber futures curve spikes to historic all-time highs",
        },
        falsification_tripwires=(
            "Gulf coast chemical plants warm up and restart cracking furnaces within 7 days",
        ),
        reference_url="https://www.eia.gov/todayinenergy/detail.php?id=47116",
    ),
    "case_f_ever_given_suez": CaseStudyArchetype(
        case_id="case_f_ever_given_suez",
        label="Ever Given Suez Canal Grounding & Supply Chain Bullwhip Tsunami",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2021-03 to 2021-05",
        chokepoints_affected=("SUEZ_CANAL", "EUROPEAN_CONTAINER_TERMINALS"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION"),
        transmission_channels=("SUEZ_BLOCKAGE", "VESSEL_ANCHOR_CONCENTRATION", "PORT_ARRIVAL_TSUNAMI", "CHASSIS_SHORTAGE"),
        lead_time_days=1,
        duration_days=60,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("CONTAINER_DWELL_DAYS", "BALTIC_DRY_INDEX"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "blockade": "$9.6B/day of global trade blocked for 6 days (350+ ships anchored)",
            "bullwhip": "Zero European arrivals followed 4 weeks later by arrival tsunami",
            "logistics": "Rotterdam/Antwerp container dwell times doubled; warehousing saturated",
        },
        falsification_tripwires=(
            "Vessel refloated within 24 hours with minimal queue accumulation",
        ),
        reference_url="https://www.lloydslist.com/LL1136279/Ever-Given-refloated-in-Suez-Canal",
    ),
    "case_g_colonial_pipeline_cyber": CaseStudyArchetype(
        case_id="case_g_colonial_pipeline_cyber",
        label="Colonial Pipeline Ransomware SCADA Shutdown",
        primary_domain="CYBER_PHYSICAL",
        historical_date_window="2021-05",
        chokepoints_affected=("COLONIAL_PIPELINE_SYSTEM", "US_EAST_COAST_FUEL"),
        primary_entities=("ENERGY_DEMAND", "OIL_STORAGE", "CONSUMER_SPENDING"),
        transmission_channels=("SCADA_BILLING_SHUTDOWN", "PIPELINE_FLOW_HALT", "RETAIL_STATION_DRYOUT", "TRUCK_HOS_WAIVER"),
        lead_time_days=0,
        duration_days=14,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("RBOB_GASOLINE_CRACK", "SPOT_TRUCKING_FREIGHT_RATES"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "fuel": "45% of US East Coast gasoline/diesel transport offline for 6 days",
            "retail": "Retail gas stations in Southeast US experienced 70% dry-out rates",
            "freight": "FAA waived driver Hours-of-Service rules; spot trucking freight rates spiked",
        },
        falsification_tripwires=(
            "Pipeline resumes automated dispatching within 24 hours via manual bypass",
        ),
        reference_url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa21-131a",
    ),
    "case_h_kakhovka_dam_agri": CaseStudyArchetype(
        case_id="case_h_kakhovka_dam_agri",
        label="Kakhovka Hydroelectric Dam Destruction & Agricultural Floodplain Wipeout",
        primary_domain="WATER_ENVIRONMENT",
        historical_date_window="2023-06 to 2023-12",
        chokepoints_affected=("DNIPRO_RIVER_BASIN", "BLACK_SEA_GRAIN_PORTS"),
        primary_entities=("CROP_HEALTH", "SHIPPING_FLOW", "INFLATION"),
        transmission_channels=("IRRIGATION_DEPLETION", "SOUTHERN_UKRAINE_CROP_LOSS", "GRAIN_BASIS_COLLAPSE", "EU_IMPORT_BANS"),
        lead_time_days=7,
        duration_days=180,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("BLACK_SEA_WHEAT_BASIS", "WHEAT_CALENDAR_CONTANGO"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "irrigation": "Kakhovka reservoir drained, destroying irrigation for 500,000 hectares",
            "basis": "FOB Black Sea grain basis collapsed; redirected volume congested EU transit",
            "politics": "EU farmer protests triggered temporary grain import bans across Poland/Hungary",
        },
        falsification_tripwires=(
            "Canal networks gravity-feed alternative reservoirs to maintain crop irrigation",
        ),
        reference_url="https://www.fao.org/newsroom/detail/ukraine-kakhovka-dam-impact-assessment",
    ),
    "case_i_indonesia_nickel_hpal": CaseStudyArchetype(
        case_id="case_i_indonesia_nickel_hpal",
        label="Indonesian Nickel Export Ban & Tsingshan HPAL Smelter Disruption",
        primary_domain="CRITICAL_MINERALS",
        historical_date_window="2020-01 to 2024-06",
        chokepoints_affected=("SULAWESI_SMELTING_HUBS", "LME_WAREHOUSES"),
        primary_entities=("GROWTH", "CORPORATE_HEALTH", "FUTURES_LIQUIDITY"),
        transmission_channels=("ORE_EXPORT_BAN", "CHINESE_HPAL_CAPEX", "NPI_MATTE_FLOOD", "CLASS1_CLASS2_BIFURCATION"),
        lead_time_days=180,
        duration_days=720,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("LME_NICKEL_CASH_3M", "NICKEL_PIG_IRON_DISCOUNT"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "smelting": "Chinese capital built massive HPAL & RKEF smelters in Morowali/Weda Bay",
            "market": "Low-grade Nickel Pig Iron flooded market, converting into battery-grade matte",
            "short_squeeze": "Historic March 2022 LME short squeeze ($100k/t); exchange halted trading for days",
        },
        falsification_tripwires=(
            "Indonesian constitutional court overturns raw ore mineral export ban",
        ),
        reference_url="https://www.lme.com/en/market-data/historical-data/nickel-suspension",
    ),
    "case_j_yangtze_drought_power": CaseStudyArchetype(
        case_id="case_j_yangtze_drought_power",
        label="Yangtze River Drought & Sichuan Smelter Power Rationing",
        primary_domain="WATER_ENVIRONMENT",
        historical_date_window="2022-07 to 2022-09",
        chokepoints_affected=("YANGTZE_HYDRO_BASIN", "SICHUAN_INDUSTRIAL_GRID"),
        primary_entities=("GRID_LOAD", "ENERGY_DEMAND", "GROWTH"),
        transmission_channels=("HYDRO_POWER_HALT", "POLYSILICON_SMELTER_SHUTDOWN", "SOLAR_WAFER_SPIKE", "EUA_CARBON_SURGE"),
        lead_time_days=5,
        duration_days=60,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("POLYSILICON_SPOT_MARGIN", "EUA_CARBON_CREDITS"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "power": "Poyang Lake dried up; Sichuan hydropower generation collapsed 50%",
            "industry": "Industrial power rationed; Sichuan aluminum smelters and polysilicon plants shut",
            "solar": "Solar PV wafer prices spiked; European utilities increased coal burn and carbon credits",
        },
        falsification_tripwires=(
            "Thermal coal generation ramps up to replace 100% of hydro shortfall within 48h",
        ),
        reference_url="https://www.reuters.com/world/china/china-issues-first-national-drought-alert-2022-08-19/",
    ),
    "case_k_russia_sanctions_2022": CaseStudyArchetype(
        case_id="case_k_russia_sanctions_2022",
        label="Russia 2022 Financial Reserve Freeze & Dark Fleet Oil Redirection",
        primary_domain="SANCTIONS_FINANCE",
        historical_date_window="2022-02 to 2026-06",
        chokepoints_affected=("SWIFT_MESSAGING", "EUROCLEAR_CLEARSTREAM", "BALTIC_SEABORNE_OIL"),
        primary_entities=("LIQUIDITY_STRESS", "WTI_OIL", "DXY", "MARKET_STRESS"),
        transmission_channels=("RESERVE_FREEZE", "SWIFT_EXCLUSION", "G7_PRICE_CAP", "SHADOW_FLEET_STS_TRANSFERS"),
        lead_time_days=0,
        duration_days=1400,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("URALS_BRENT_DISCOUNT", "CROSS_CURRENCY_BASIS_SWAP"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "finance": "$300B sovereign foreign exchange reserves immobilized at Euroclear/FRBNY",
            "energy": "Russia acquired 600+ vintage tankers to bypass G7 insurance price caps",
            "trade": "Crude oil exports redirected to India/China; bilateral yuan-ruble settlement reached 45%",
        },
        falsification_tripwires=(
            "All non-G7 importers comply with maritime insurance price caps and halt purchases",
        ),
        reference_url="https://home.treasury.gov/news/press-releases/jy0608",
    ),
    "case_l_svb_banking_stress_2023": CaseStudyArchetype(
        case_id="case_l_svb_banking_stress_2023",
        label="March 2023 US Regional Banking Uninsured Deposit Run",
        primary_domain="SOVEREIGN_LIQUIDITY",
        historical_date_window="2023-03 to 2023-05",
        chokepoints_affected=("FED_DISCOUNT_WINDOW", "BTFP_FACILITY", "REGIONAL_BANK_DEPOSITS"),
        primary_entities=("LIQUIDITY_STRESS", "CREDIT_SPREAD", "GOLD", "DXY"),
        transmission_channels=("HTM_BOND_UNREALIZED_LOSSES", "DIGITAL_DEPOSIT_RUN", "FED_BTFP_CREATION", "CREDIT_CONTRACTION"),
        lead_time_days=2,
        duration_days=60,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("FRA_OIS_SPREAD", "KRE_REGIONAL_BANK_ETF"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "deposits": "$42B withdrawn from Silicon Valley Bank in 10 hours via mobile banking",
            "fed_response": "Fed created Bank Term Funding Program (BTFP) lending against par collateral value",
            "assets": "Gold and 2Y Treasuries surged in historic flight-to-safety rally",
        },
        falsification_tripwires=(
            "Private sector interbank lending absorbs liquidity shortfall without Fed emergency facility",
        ),
        reference_url="https://www.federalreserve.gov/publications/files/svb-review-20230428.pdf",
    ),
    "brexit_2016": CaseStudyArchetype(
        case_id="brexit_2016",
        label="Brexit Referendum FX Volatility Shock",
        primary_domain="GEOPOLITICAL_EVENT",
        historical_date_window="2016-06",
        chokepoints_affected=("DOVER_CALAIS_PORTS", "UK_CUSTOMS"),
        primary_entities=("GBP", "GOLD", "VIX", "EQUITIES"),
        transmission_channels=("POLLING_DIVERGENCE", "CURRENCY_DEPRECIATION", "SAFE_HAVEN_FLOWS"),
        lead_time_days=0,
        duration_days=90,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("GBP_USD_VOLATILITY", "GILTS_BUNDS_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "fx": "GBP/USD dropped over 10% in overnight session",
            "volatility": "VIX spiked into mid-20s",
        },
        falsification_tripwires=("Bilateral UK-EU trade agreement immediately finalized",),
        reference_url="https://www.bankofengland.co.uk",
    ),
    "trump_election_2016": CaseStudyArchetype(
        case_id="trump_election_2016",
        label="US 2016 Presidential Election Reflation Shock",
        primary_domain="MACRO_REGIME",
        historical_date_window="2016-11",
        chokepoints_affected=("US_TREASURY_MARKET",),
        primary_entities=("DXY", "NOMINAL_BOND_YIELD", "EM_FX", "EQUITIES"),
        transmission_channels=("FISCAL_EXPANSION_PRICING", "YIELD_CURVE_STEEPENING", "CYCLICAL_ROTATION"),
        lead_time_days=0,
        duration_days=180,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("US_10Y_2Y_YIELD_SPREAD", "DXY_INDEX"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "yields": "US 10Y Treasury yields surged 80 bps in weeks",
            "equities": "Russell 2000 and financials surged +15%",
        },
        falsification_tripwires=("Fiscal tax reform packages blocked by Senate filibuster",),
        reference_url="https://www.federalreserve.gov",
    ),
    "trade_war_2018": CaseStudyArchetype(
        case_id="trade_war_2018",
        label="US-China Section 301 Tariff War Escalation",
        primary_domain="GEOECONOMIC_TRADE",
        historical_date_window="2018-03 to 2019-12",
        chokepoints_affected=("US_WEST_COAST_PORTS", "SHANGHAI_PORT"),
        primary_entities=("EM_FX", "EQUITIES", "DXY", "GROWTH"),
        transmission_channels=("TARIFF_ANNOUNCEMENTS", "SUPPLY_CHAIN_RESHORING", "CHINESE_YUAN_DEPRECIATION"),
        lead_time_days=30,
        duration_days=650,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("USD_CNY_EXCHANGE_RATE", "CORN_SOY_SPREAD"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "trade": "25% tariffs applied to $250B+ of Chinese goods",
            "equities": "Semiconductors and industrial cyclicals experienced rolling 20% drawdowns",
        },
        falsification_tripwires=("Phase One trade deal eliminates all bilateral tariffs",),
        reference_url="https://ustr.gov",
    ),
    "covid_crash_2020": CaseStudyArchetype(
        case_id="covid_crash_2020",
        label="COVID-19 Pandemic Global Liquidity & Supply Freeze",
        primary_domain="SYSTEMIC_SHOCK",
        historical_date_window="2020-02 to 2020-05",
        chokepoints_affected=("GLOBAL_AIR_TRANSIT", "CRUDE_STORAGE_CUSHING"),
        primary_entities=("VIX", "GOLD", "DXY", "WTI_OIL", "CREDIT_SPREAD"),
        transmission_channels=("LOCKDOWNS", "DASH_FOR_CASH", "TANKER_STORAGE_SATURATION", "UNLIMITED_QE"),
        lead_time_days=0,
        duration_days=90,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("WTI_CALENDAR_SPREAD", "HY_OAS_CREDIT_SPREAD"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "oil": "WTI May 2020 futures collapsed to -$37.63/bbl on Cushing saturation",
            "liquidity": "Cross-asset correlation went to 1.0 in dash-for-cash",
        },
        falsification_tripwires=("Global economic mobility returns to pre-pandemic baseline within 14 days",),
        reference_url="https://www.who.int",
    ),
}

# Auto-expand CASE_STUDY_REGISTRY with the complete Master Case Study Library.
#
# This has to stay LAZY. `intelligence.case_study_library` imports
# CaseStudyArchetype from THIS module -- it instantiates ~50 archetypes at import
# time -- so importing the library back at module scope is a genuine cycle.
#
# It used to be attempted here inside a try/except, which meant the outcome
# depended on which module Python happened to import first: when the library got
# there first, MASTER_CASE_STUDY_REGISTRY was only half-built, the ImportError was
# swallowed, and all 43 master case studies were silently missing from the
# registry (48 entries instead of the full set). A "fix" for the resulting
# NameError had even been committed inside this module's docstring, so the
# handler could not even log the failure.
#
# Resolving the import on first use removes the cycle entirely: by the time any
# caller reaches the registry, this module is fully initialised and the library
# import always succeeds.
_MASTER_REGISTRY_MERGED = False


def _ensure_master_registry() -> None:
    """Merge the master case-study library into CASE_STUDY_REGISTRY, once."""
    global _MASTER_REGISTRY_MERGED
    if _MASTER_REGISTRY_MERGED:
        return
    _MASTER_REGISTRY_MERGED = True
    try:
        from intelligence.case_study_library import MASTER_CASE_STUDY_REGISTRY
    except Exception as exc:
        logger.warning("master case-study registry unavailable: %s", exc)
        return
    CASE_STUDY_REGISTRY.update(MASTER_CASE_STUDY_REGISTRY)


# ===========================================================================
# Case-Based Reasoning Matching Engine
# ===========================================================================

@dataclass
class CaseMatchResult:
    """Quantitative scoring of how closely an unfolding event matches a case study."""
    matched_case_id: str
    label: str
    similarity_score: float              # 0.0 to 1.0 (1.0 = identical structural archetype)
    jaccard_entity_overlap: float        # Overlap of affected causal nodes
    domain_match: bool
    tau_horizon: str
    expected_lag_days: int
    projected_trajectory: Dict[str, str]
    falsification_tripwires: Tuple[str, ...]
    execution_authority: bool = False   # Non-custodial safety


class CaseStudyMatcher:
    """Engine that maps real-time macro/geopolitical events to historical case studies."""

    def __init__(self, registry: Optional[Dict[str, CaseStudyArchetype]] = None):
        _ensure_master_registry()
        self.registry = registry if registry is not None else dict(CASE_STUDY_REGISTRY)

    def match_event(
        self,
        event_domain: str,
        affected_entities: List[str],
        affected_chokepoints: Optional[List[str]] = None,
        observed_spreads: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> List[CaseMatchResult]:
        """Rank and return top-K historical case study analogues for an unfolding event."""
        aff_ent = list(affected_entities or [])
        aff_choke = list(affected_chokepoints or [])
        obs_spread = list(observed_spreads or [])

        results: List[CaseMatchResult] = []

        for case_id, archetype in self.registry.items():
            sim = calculate_multi_dimensional_composite_case_similarity(
                entities_event=aff_ent,
                entities_case=archetype.primary_entities,
                chokepoints_event=aff_choke,
                chokepoints_case=archetype.chokepoints_affected,
                spreads_event=obs_spread,
                spreads_case=archetype.core_spread_distortions,
                domain_event=event_domain,
                domain_case=archetype.primary_domain,
            )

            j_ent = calculate_case_based_jaccard_similarity(aff_ent, archetype.primary_entities)

            results.append(CaseMatchResult(
                matched_case_id=case_id,
                label=archetype.label,
                similarity_score=round(sim, 4),
                jaccard_entity_overlap=round(j_ent, 4),
                domain_match=(str(event_domain).strip().upper() == str(archetype.primary_domain).strip().upper()),
                tau_horizon=archetype.tau_substitution_horizon,
                expected_lag_days=archetype.lead_time_days,
                projected_trajectory=archetype.historical_outcomes,
                falsification_tripwires=archetype.falsification_tripwires,
                execution_authority=False,
            ))

        # Sort descending by similarity score
        results.sort(key=lambda x: x.similarity_score, reverse=True)
        return results[:max(1, int(top_k))]

    def get_case(self, case_id: str) -> Optional[CaseStudyArchetype]:
        """Retrieve full archetype details by case ID."""
        return self.registry.get(case_id)


_GLOBAL_MATCHER: Optional[CaseStudyMatcher] = None


def get_case_study_matcher() -> CaseStudyMatcher:
    """Global singleton accessor for CaseStudyMatcher."""
    global _GLOBAL_MATCHER
    if _GLOBAL_MATCHER is None:
        _GLOBAL_MATCHER = CaseStudyMatcher()
    return _GLOBAL_MATCHER
