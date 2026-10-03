"""Master Compendium & Universal Case Study Library.

Contains 50+ deeply researched historical, structural, macroeconomic, supply chain,
and geopolitical case study archetypes across all 10 canonical domains:
1. MARITIME_CHOKEPOINT
2. TECH_HARDWARE
3. ENERGY_PETROCHEM
4. CRITICAL_MINERALS
5. AGRICULTURAL_FERTILIZER
6. SOVEREIGN_LIQUIDITY
7. MONETARY_POLICY
8. CURRENCY_PEG
9. SANCTIONS_FINANCE
10. SYSTEMIC_LIQUIDITY

All archetypes are machine-auditable, source-referenced, and non-custodial
with execution_authority=False.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from intelligence.case_study_matcher import CaseStudyArchetype

log = logging.getLogger("CaseStudyLibrary")


# ===========================================================================
# 1. MARITIME & GLOBAL CHOKEPOINT DISRUPTIONS
# ===========================================================================

MARITIME_CHOKEPOINT_CASES: Dict[str, CaseStudyArchetype] = {
    "case_a_hormuz_blockade": CaseStudyArchetype(
        case_id="case_a_hormuz_blockade",
        label="Strait of Hormuz Petroleum & Fertilizer Transit Blockade",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="1984 Tanker War / 2019 Attacks / Structural Model",
        chokepoints_affected=("STRAIT_OF_HORMUZ", "PERSIAN_GULF", "GULF_OF_OMAN"),
        primary_entities=("WTI_OIL", "INFLATION", "CROP_HEALTH", "CONSUMER_SPENDING", "DXY"),
        transmission_channels=("CRUDE_SPIKE", "HABER_BOSCH_AMMONIA_SURGE", "CORN_COST_INFLATION", "SOY_SUBSTITUTION", "WAR_RISK_INSURANCE_PREMIUM"),
        lead_time_days=0,
        duration_days=90,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("AMMONIA_CASH_COST", "CORN_SOY_RATIO", "CRACK_SPREAD_321", "BRENT_WTI_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "oil": "Brent & WTI spike +25-40% within 72 hours; prompt backwardation blows out to >$4/bbl",
            "fertilizer": "Anhydrous ammonia surges from $480 to $700/ton as Middle East urea export halts",
            "agri_spread": "Corn breakeven surges; US farmers shift acres to Soybeans; Corn/Soy ratio collapses",
            "shipping": "P&I Joint War Committee adds Persian Gulf to listed areas; hull insurance rises 10x",
        },
        falsification_tripwires=(
            "Saudi East-West crude pipeline (Petroline) bypass successfully routes >5M bpd to Yanbu",
            "Natural gas prices fail to transmit to global urea/ammonia plants within 14 days",
            "Commercial tanker convoys resume under US Fifth Fleet escort within 5 days",
        ),
        reference_url="https://www.eia.gov/international/analysis/special-topics/World_Oil_Transit_Chokepoints",
    ),
    "case_b_red_sea_reroute": CaseStudyArchetype(
        case_id="case_b_red_sea_reroute",
        label="Red Sea / Bab el-Mandeb Cape of Good Hope Maritime Reroute",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2023-11 to 2024-08",
        chokepoints_affected=("BAB_EL_MANDEB", "SUEZ_CANAL", "RED_SEA", "GULF_OF_ADEN"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION", "WTI_OIL", "EUR"),
        transmission_channels=("CAPE_TRANSIT_REROUTE", "CONTAINER_CAPACITY_SHRINK", "BUNKER_FUEL_SURGE", "IMPORT_PRICE_INFLATION", "INSURANCE_WAR_SURCHARGE"),
        lead_time_days=10,
        duration_days=240,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("FREIGHTOS_FBX_CONTAINER", "BRENT_DUBAI_SPREAD", "GASOIL_CRACK_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "freight": "Freightos FBX Asia-Europe spot rates surged from $1,200 to $4,500/FEU (+275%)",
            "transit_time": "Vessels rerouted via Cape of Good Hope (+14 days transit, +3,500 nautical miles)",
            "macro_cpi": "European goods disinflation stalled; delivery times expanded across automotive/retail",
            "energy": "European LNG shipments from Qatar delayed; bunker fuel demand in Singapore/Rotterdam jumped",
        },
        falsification_tripwires=(
            "Naval escort coalition restores >80% commercial transit through Bab el-Mandeb",
            "Insurance P&I war risk surcharges collapse below 0.2% of hull value",
            "Suez Canal transits recover to pre-disruption baseline of >65 vessels/day",
        ),
        reference_url="https://www.freightos.com/freight-blog/freightos-baltic-index-fbx-global-container-pricing-index/",
    ),
    "case_f_ever_given_suez": CaseStudyArchetype(
        case_id="case_f_ever_given_suez",
        label="Ever Given Suez Canal Grounding & Global Bullwhip Tsunami",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2021-03 to 2021-05",
        chokepoints_affected=("SUEZ_CANAL", "EUROPEAN_CONTAINER_TERMINALS", "MEDITERRANEAN_PORTS"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION", "CORPORATE_HEALTH"),
        transmission_channels=("SUEZ_BLOCKAGE", "VESSEL_ANCHOR_CONCENTRATION", "PORT_ARRIVAL_TSUNAMI", "CHASSIS_SHORTAGE", "CONTAINER_DWELL_SURGE"),
        lead_time_days=1,
        duration_days=60,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("CONTAINER_DWELL_DAYS", "BALTIC_DRY_INDEX", "EUROPEAN_PORT_CONGESTION"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "blockade": "$9.6B/day of global trade immobilized for 6 days with 350+ vessels anchored",
            "bullwhip": "Zero European port arrivals for 10 days followed by sudden multi-vessel cluster arrivals",
            "warehousing": "Rotterdam/Antwerp container dwell times doubled; truck chassis deficits saturated yards",
            "cascading_lead": "Manufacturing component delivery dates pushed back 4-8 weeks across Eurozone",
        },
        falsification_tripwires=(
            "Vessel refloated within 24 hours with queue clearing within 48 hours",
            "European port terminal yard utilization remains under 70%",
        ),
        reference_url="https://www.lloydslist.com/LL1136279/Ever-Given-refloated-in-Suez-Canal",
    ),
    "case_m_panama_canal_drought_2023": CaseStudyArchetype(
        case_id="case_m_panama_canal_drought_2023",
        label="Panama Canal Gatun Lake Drought & Transit Slot Restriction",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2023-06 to 2024-03",
        chokepoints_affected=("PANAMA_CANAL", "GATUN_LAKE", "US_GULF_COAST_PORTS"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION", "GROWTH", "WTI_OIL"),
        transmission_channels=("DRAFT_RESTRICTION", "AUCTION_SLOT_SPIKE", "SUEZ_CAPE_DIVERSION", "US_GRAIN_EXPORT_DELAY", "LNG_TRANSIT_REROUTE"),
        lead_time_days=14,
        duration_days=270,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("PANAMA_TRANSIT_AUCTION_BID", "BALTIC_PANAMAX_INDEX", "US_GULF_ASIA_GRAIN_BASIS"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "daily_slots": "Daily booking slots slashed from 36 to 22 vessels; max draft reduced from 50 to 44 feet",
            "auction_records": "Spot transit booking auction bids surged past $4.0M per vessel for prompt passage",
            "lng_bulk": "US Gulf Coast LNG and grain bulk carriers redirected around Cape Horn or Cape of Good Hope",
            "cost_basis": "US agricultural exports to Asia incurred additional $0.35/bushel freight penalty",
        },
        falsification_tripwires=(
            "Intense tropical rainfall raises Gatun Lake water levels above 88 feet within 14 days",
            "Panama Canal Authority (ACP) restores slot reservations to >32 vessels/day",
        ),
        reference_url="https://pancanal.com/en/water-levels/",
    ),
    "case_n_malacca_strait_congestion": CaseStudyArchetype(
        case_id="case_n_malacca_strait_congestion",
        label="Strait of Malacca & Singapore Anchorage Chokepoint Squeeze",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2024-05 to 2024-07 / Structural Model",
        chokepoints_affected=("STRAIT_OF_MALACCA", "PORT_OF_SINGAPORE", "SUNDA_STRAIT"),
        primary_entities=("SHIPPING_FLOW", "FREIGHT_VOLUME", "CORPORATE_HEALTH", "WTI_OIL"),
        transmission_channels=("VESSEL_BUNCHING", "SINGAPORE_BERTH_DELAYS", "OFF-SCHEDULE_OFFLOADING", "SUPPLY_CHAIN_BULLWHIP"),
        lead_time_days=5,
        duration_days=60,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("SINGAPORE_BUNKER_SPREAD", "CONTAINER_TRANSSHIPMENT_PREMIUM"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "anchorage_wait": "Wait times for container berths in Singapore exploded from 0.5 days to 7.5 days",
            "diverted_cargo": "Vessels bypassed Singapore to offload in Port Klang/Tanjung Pelepas, saturating feeder hubs",
            "container_shortage": "Severe empty container box deficit formed in China export hubs (Ningbo/Shanghai)",
        },
        falsification_tripwires=(
            "Tuas mega-port opens surge berths restoring average dwell time to <24 hours",
            "Container lines re-establish fixed schedule reliability above 75%",
        ),
        reference_url="https://www.singaporepsa.com/",
    ),
    "case_o_baltimore_bridge_collapse": CaseStudyArchetype(
        case_id="case_o_baltimore_bridge_collapse",
        label="Baltimore Francis Scott Key Bridge Allision & Port Closure",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2024-03 to 2024-06",
        chokepoints_affected=("PORT_OF_BALTIMORE", "PATAPSCO_RIVER_CHANNEL", "I-695_LOGISTICS"),
        primary_entities=("FREIGHT_VOLUME", "RAIL_ACTIVITY", "CORPORATE_HEALTH", "GROWTH"),
        transmission_channels=("PORT_CHANNEL_BLOCKAGE", "RO-RO_AUTO_DIVERSION", "COAL_EXPORT_BOTTLENECK", "EAST_COAST_TRUCK_DETOUR"),
        lead_time_days=0,
        duration_days=75,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("CSX_COAL_BASIS", "EAST_COAST_DRAYAGE_RATES"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "port_halt": "Zero vessel access to Baltimore inner harbor (US #1 port for autos and light trucks: 850k vehicles/year)",
            "rerouting": "Automotive cargo shifted to Norfolk and Newark; CSX thermal coal train exports halted",
            "channel_clearance": "US Army Corps of Engineers took 75 days to fully clear 50,000 tons of steel wreckage",
        },
        falsification_tripwires=(
            "Temporary shallow-draft 35-foot channel accommodates >80% of roll-on/roll-off auto carriers within 10 days",
            "Norfolk and Philadelphia absorb 100% of diverted container volume with 0 day delay",
        ),
        reference_url="https://www.usace.army.mil/",
    ),
    "case_p_bosporus_turkish_straits": CaseStudyArchetype(
        case_id="case_p_bosporus_turkish_straits",
        label="Turkish Straits (Bosporus & Dardanelles) Black Sea Chokepoint Squeeze",
        primary_domain="MARITIME_CHOKEPOINT",
        historical_date_window="2022-03 to 2026-06 / Montreux Convention",
        chokepoints_affected=("BOSPORUS_STRAIT", "DARDANELLES", "BLACK_SEA", "MARMARA_SEA"),
        primary_entities=("GRAIN_EXPORTS", "WTI_OIL", "CROP_HEALTH", "INFLATION"),
        transmission_channels=("MONTREUX_WARSHIP_CLOSURE", "P&I_INSURANCE_LETTER_SCRUTINY", "GRAIN_CORRIDOR_INSPECTION_BACKLOG", "URALS_TANKER_CONGESTION"),
        lead_time_days=3,
        duration_days=365,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("BLACK_SEA_GRAIN_BASIS", "MEDITERRANEAN_AFRAMAX_FREIGHT"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "tanker_queue": "Turkey required physical P&I oil spill insurance verification letters; 30+ oil tankers anchored in queue",
            "grain_checks": "Joint Coordination Center physical inspections throttled Black Sea grain throughput to 2-3 ships/day",
            "freight_surge": "Aframax and Suezmax tanker charter rates in the Black Sea surged >200%",
        },
        falsification_tripwires=(
            "Automated digital insurance verification clears tanker backlog within 48 hours",
            "Balkan rail/barge pipelines bypass Bosporus carrying >70% of export volume",
        ),
        reference_url="https://www.denizcilik.gov.tr/",
    ),
}


# ===========================================================================
# 2. TECHNOLOGY, SEMICONDUCTORS & HARDWARE BOTTLENECKS
# ===========================================================================

TECH_HARDWARE_CASES: Dict[str, CaseStudyArchetype] = {
    "case_c_ukraine_neon_semis": CaseStudyArchetype(
        case_id="case_c_ukraine_neon_semis",
        label="Ukrainian Neon Gas Bottleneck & DUV Lithography Shock",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2022-02 to 2022-12",
        chokepoints_affected=("BLACK_SEA_PORTS", "MARIUPOL_PURIFICATION", "ODESA_AIR_SEPARATION"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "INFLATION", "EQUITIES"),
        transmission_channels=("NEON_PURIFICATION_HALT", "EXCIMER_LASER_SPIKE", "WAFER_ALLOCATION_QUOTAS", "AUTO_MCU_SHORTAGE", "LEAD_TIME_BLOWOUT"),
        lead_time_days=45,
        duration_days=300,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("NEON_PURITY_PREMIUM", "MANHEIM_USED_VEHICLE_INDEX", "SEMICONDUCTOR_LEAD_TIME"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "neon_price": "Excimer laser-grade neon gas (99.999% purity) spiked 600% from $250/m3 to $1,800/m3",
            "chip_shortage": "Automotive microcontroller and power management IC lead times blew out to 52 weeks",
            "auto_production": "Global automakers slashed 4M+ vehicle production; used car prices surged +30%",
        },
        falsification_tripwires=(
            "Air Liquide, Linde, and Chinese noble gas air-separation plants scale non-Ukrainian recovery within 90 days",
            "Chip foundries transition excimer lasers to chemical recycling systems cutting fresh neon burn by 70%",
        ),
        reference_url="https://www.csis.org/analysis/russia-ukraine-conflict-and-semiconductor-supply-chain",
    ),
    "case_d_fukushima_thailand_hdd": CaseStudyArchetype(
        case_id="case_d_fukushima_thailand_hdd",
        label="Fukushima Disaster + Thailand Monsoon Floods JIT Electronics Collapse",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2011-03 to 2011-12",
        chokepoints_affected=("BANGKOK_INDUSTRIAL_PARKS", "TOHOKU_AUTO_CLUSTERS", "CHAO_PHRAYA_BASIN"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "EQUITIES"),
        transmission_channels=("HDD_MOTOR_FLOODING", "SERVER_LEAD_TIME_EXPANSION", "CLOUD_MIGRATION_ACCELERATION", "AUTO_MICROCONTROLLER_HALT"),
        lead_time_days=14,
        duration_days=270,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("HDD_SPOT_PRICING", "HYPERSCALE_CAPEX_VELOCITY", "Nidec_MOTOR_MARGIN"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "hdd_collapse": "Thailand floods inundated Nidec and Western Digital factories, taking 50% of global HDD motor supply offline",
            "server_delays": "Enterprise PC and server delivery lead times surged from 3 weeks to 9 months; spot HDD prices doubled",
            "cloud_paradigm": "Forced enterprise CIOs to accelerate migration from on-premise hardware to hyperscale cloud (AWS, Azure)",
        },
        falsification_tripwires=(
            "Assembly factories pump out flood waters and restore assembly lines within 30 days",
            "Nidec shifts motor manufacturing to alternative plants in the Philippines within 2 weeks",
        ),
        reference_url="https://www.reuters.com/article/us-thailand-floods-tech-idUSTRE79T1BC20111030",
    ),
    "case_q_tsmc_taiwan_drought_quake": CaseStudyArchetype(
        case_id="case_q_tsmc_taiwan_drought_quake",
        label="Taiwan TSMC Hsinchu/Tainan Ultra-Pure Water & Seismic Squeeze",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2021-04 Drought / 2024-04 Hualien Earthquake",
        chokepoints_affected=("HSINCHU_SCIENCE_PARK", "TAINAN_FAB_CLUSTER", "BAOSHAN_RESERVOIR"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "EQUITIES", "DXY"),
        transmission_channels=("ULTRA_PURE_WATER_RATIONING", "FAB_TOOL_RECALIBRATION", "LEADING_EDGE_WAFER_SCRAP", "AI_ACCELERATOR_DELAY"),
        lead_time_days=2,
        duration_days=90,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("TSMC_WAFER_ASP", "GPU_SECONDARY_PREMIUM", "SEMICONDUCTOR_CAPEX_RATIO"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "water_logistics": "Drought forced TSMC to deploy 100+ water tanker trucks daily to keep 3nm/5nm fabs operational",
            "quake_impact": "7.4M earthquake automatically halted lithography steppers; thousands of in-process wafers scrapped",
            "supply_concentration": "Highlighted that 90%+ of sub-5nm advanced AI compute logic is concentrated on a single tectonic fault line",
        },
        falsification_tripwires=(
            "Seismic automated safety interlocks reboot lithography tools with 0 wafer scrap within 24 hours",
            "Industrial reclaimed water and desalination plants supply 100% of fab water needs",
        ),
        reference_url="https://www.tsmc.com/english/dedicatedFoundry/technology/water_management",
    ),
    "case_r_us_china_gpu_export_controls": CaseStudyArchetype(
        case_id="case_r_us_china_gpu_export_controls",
        label="US BIS Advanced AI Semiconductor & Advanced Packaging Sanctions",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2022-10 to 2026-06",
        chokepoints_affected=("TSMC_COWOS_PACKAGING", "NVIDIA_SUPPLY_CHAIN", "SHENZHEN_HUAQIANGBEI"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "EQUITIES", "UNCERTAINTY"),
        transmission_channels=("BIS_EXPORT_LICENSING", "COWOS_INTERPOSER_CAPACITY_CRUNCH", "SMUGGLING_PRICE_PREMIUM", "CHINESE_DOMESTIC_ASIC_PIVOT"),
        lead_time_days=0,
        duration_days=720,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("CHINESE_BLACK_MARKET_GPU_SPREAD", "HYPERSCALE_AI_RENTAL_RATE"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "export_ban": "US prohibited sale of Nvidia A100/H100/B200 and banned US persons from supporting Chinese advanced fabs",
            "black_market": "Smuggled Nvidia H100 GPUs commanded 2.5x MSRP ($70,000/unit) in Shenzhen and Hong Kong",
            "capacity_shift": "TSMC CoWoS advanced packaging capacity became the premier global computational bottleneck",
        },
        falsification_tripwires=(
            "Huawei Ascend AI chips achieve 100% parity with CUDA software ecosystem within 12 months",
            "US Department of Commerce grants unconditional export licenses to all Chinese cloud providers",
        ),
        reference_url="https://www.bis.doc.gov/index.php/regulations/commerce-control-list-ccl",
    ),
    "case_s_asml_high_na_euv_controls": CaseStudyArchetype(
        case_id="case_s_asml_high_na_euv_controls",
        label="ASML High-NA Extreme Ultraviolet (EUV) Lithography Tool Monopoly",
        primary_domain="TECH_HARDWARE",
        historical_date_window="2023-06 to 2026-06",
        chokepoints_affected=("VELDHOVEN_ASML_HEADQUARTERS", "ZEISS_OPTICS_OBERKOCHEN", "CYMER_LIGHT_SOURCE"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "EQUITIES"),
        transmission_channels=("DUTCH_EXPORT_RESTRICTIONS", "EUV_MIRROR_POLISHING_BOTTLENECK", "FAB_CONSTRUCTION_LEAD_TIME", "2NM_NODE_ALLOCATION"),
        lead_time_days=90,
        duration_days=1000,
        tau_substitution_horizon="DECADES",
        core_spread_distortions=("ASML_TOOL_BACKLOG", "SUB_2NM_FOUNDRY_PREMIUM"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "tool_monopoly": "ASML is the sole global manufacturer of EUV tools (0.33 NA & 0.55 High-NA) priced at $350M+ per machine",
            "zeiss_bottleneck": "Carl Zeiss optics mirrors require atomic-level polishing with 18-month manufacturing lead times",
            "geopolitics": "Trilateral agreement (US, Netherlands, Japan) restricts advanced immersion DUV and EUV tools to non-aligned nations",
        },
        falsification_tripwires=(
            "Alternative lithography technology (e.g. Canon Nanoimprint Lithography) achieves <3nm defect rates in high-volume manufacturing",
        ),
        reference_url="https://www.asml.com/en/technology/lithography-principles",
    ),
}


# ===========================================================================
# 3. ENERGY, REFINING, PETROCHEMICAL & POWER SHOCKS
# ===========================================================================

ENERGY_PETROCHEM_CASES: Dict[str, CaseStudyArchetype] = {
    "case_e_texas_storm_uri_pvc": CaseStudyArchetype(
        case_id="case_e_texas_storm_uri_pvc",
        label="Texas Winter Storm Uri ERCOT Petrochemical & Refined Freeze",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2021-02 to 2021-06",
        chokepoints_affected=("ERCOT_POWER_GRID", "US_GULF_COAST_REFINERIES", "HOUSTON_SHIP_CHANNEL"),
        primary_entities=("ENERGY_DEMAND", "GRID_LOAD", "HOUSING_ACTIVITY", "INFLATION", "WTI_OIL"),
        transmission_channels=("POWER_GRID_COLLAPSE", "WELLHEAD_FREEZE_OFF", "POLYMER_CRACKER_SHUTDOWN", "PVC_PIPE_SHORTAGE", "LUMBER_SUBSTITUTION"),
        lead_time_days=2,
        duration_days=120,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("SPARK_SPREAD", "LUMBER_FUTURES_CURVE", "ETHYLENE_SPOT_MARGIN", "CRACK_SPREAD_321"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "petrochem": "60% of US PVC resin, ethylene, and propylene production knocked offline for months",
            "construction": "Residential plumbing pipe supplies vanished nationwide; home construction delays surged",
            "lumber_spillover": "Builders hoarded alternative materials; lumber futures spiked to all-time historic record ($1,700/MBF)",
            "power_prices": "ERCOT wholesale electricity prices hit the regulatory cap of $9,000/MWh for days",
        },
        falsification_tripwires=(
            "Gulf Coast chemical crackers complete thermal thawing and restart cracking within 7 days",
            "ERCOT power grid restores industrial baseload power within 48 hours",
        ),
        reference_url="https://www.eia.gov/todayinenergy/detail.php?id=47116",
    ),
    "case_g_colonial_pipeline_cyber": CaseStudyArchetype(
        case_id="case_g_colonial_pipeline_cyber",
        label="Colonial Pipeline Ransomware SCADA Shutdown",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2021-05",
        chokepoints_affected=("COLONIAL_PIPELINE_SYSTEM", "US_EAST_COAST_FUEL", "NEW_YORK_HARBOR"),
        primary_entities=("ENERGY_DEMAND", "OIL_STORAGE", "CONSUMER_SPENDING", "INFLATION"),
        transmission_channels=("SCADA_BILLING_SHUTDOWN", "PIPELINE_FLOW_HALT", "RETAIL_STATION_DRYOUT", "TRUCK_HOS_WAIVER", "JONES_ACT_WAIVER"),
        lead_time_days=0,
        duration_days=14,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("RBOB_GASOLINE_CRACK", "SPOT_TRUCKING_FREIGHT_RATES", "GULF_COAST_NY_HARBOR_ARBITRAGE"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "fuel_cutoff": "45% of US East Coast gasoline, diesel, and jet fuel pipeline transport offline for 6 days",
            "retail_dryout": "Retail gas stations in Southeast US experienced 70%+ fuel dry-out rates amid consumer panic buying",
            "regulatory_action": "DOT issued emergency Hours-of-Service truck waivers; Jones Act maritime transport waivers granted",
        },
        falsification_tripwires=(
            "Pipeline operator restores operational dispatching within 24 hours via manual control override",
            "East Coast regional refined product inventories exceed 30 days of forward consumption",
        ),
        reference_url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa21-131a",
    ),
    "case_t_nord_stream_sabotage_2022": CaseStudyArchetype(
        case_id="case_t_nord_stream_sabotage_2022",
        label="Nord Stream 1 & 2 Baltic Pipeline Explosive Sabotage",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2022-09 to 2023-04",
        chokepoints_affected=("BALTIC_SEA_SEABED", "LUBMIN_RECEIVING_TERMINAL", "EUROPEAN_GAS_GRID"),
        primary_entities=("ENERGY_DEMAND", "INFLATION", "EUR", "CORPORATE_HEALTH"),
        transmission_channels=("SUBSEA_PIPE_RUPTURE", "RUSSIAN_GAS_TERMINATION", "EUROPEAN_TTF_SURGE", "GERMAN_SMELTER_CURTAILMENT", "GLOBAL_LNG_ARBITRAGE"),
        lead_time_days=0,
        duration_days=365,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("TTF_HENRY_HUB_RATIO", "CLEAN_SPARK_SPREAD", "AMMONIA_EUROPEAN_IMPORT_PARITY"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "physical_destruction": "Permanent structural destruction of 3 of 4 subsea pipeline strings in international waters",
            "price_dislocation": "Dutch TTF natural gas prices surged above €300/MWh (~$90/MMBtu, 10x US Henry Hub parity)",
            "industrial_deindustrialization": "German chemical giants (BASF) shut European ammonia/fertilizer plants and shifted capex to US/China",
        },
        falsification_tripwires=(
            "Diver inspection verifies pipeline integrity allowing immediate gas re-pressurization",
            "Alternative pipeline flows through Norway (Baltic Pipe) and Algeria fully replace 100% of Nord Stream volumes",
        ),
        reference_url="https://www.reuters.com/world/europe/nord-stream-gas-leaks-what-we-know-so-far-2022-09-27/",
    ),
    "case_u_1973_opec_oil_embargo": CaseStudyArchetype(
        case_id="case_u_1973_opec_oil_embargo",
        label="1973 Arab-Israeli War OAPEC Petroleum Embargo & Stagflation Shock",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="1973-10 to 1974-03",
        chokepoints_affected=("PERSIAN_GULF_TERMINALS", "SUEZ_CANAL", "GLOBAL_REFINERIES"),
        primary_entities=("WTI_OIL", "INFLATION", "GROWTH", "DXY", "EQUITIES"),
        transmission_channels=("OAPEC_PRODUCTION_CUT", "PETROLEUM_EXPORT_EMBARGO", "GLOBAL_STAGFLATION", "CENTRAL_BANK_DILEMMA", "EQUITY_BEAR_MARKET"),
        lead_time_days=0,
        duration_days=180,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("OIL_EQUITY_CORRELATION", "NOMINAL_YIELD_CPI_GAP"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "crude_quadrupling": "Benchmark crude oil prices quadrupled from $2.90 to $11.65 per barrel",
            "macro_regime": "Triggered the canonical 1970s Stagflation regime: simultaneous high inflation and severe economic contraction",
            "equity_crash": "S&P 500 suffered a -48% bear market; transformed global energy policy (creation of IEA, US SPR)",
        },
        falsification_tripwires=(
            "Non-OAPEC producers (US, North Sea, Alaska) scale swing production to fill 100% of shortfall within 30 days",
        ),
        reference_url="https://history.state.gov/milestones/1969-1976/opec-embargo",
    ),
    "case_v_us_spr_drain_2022": CaseStudyArchetype(
        case_id="case_v_us_spr_drain_2022",
        label="US Strategic Petroleum Reserve (SPR) Historic 180M Barrel Depletion",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2022-03 to 2023-01",
        chokepoints_affected=("BIG_HILL_STORAGE", "BRYAN_MOUND", "US_GULF_COAST_SALT_CAVERNS"),
        primary_entities=("OIL_STORAGE", "WTI_OIL", "INFLATION", "DXY"),
        transmission_channels=("SPR_CRUDE_AUCTION", "PROMPT_CURVE_FLATTENING", "RESERVE_BUFFER_EXHAUSTION", "REPLENISHMENT_PUT_OPTION"),
        lead_time_days=7,
        duration_days=300,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("WTI_CALENDAR_BACKWARDATION", "SPR_INVENTORY_LEVEL"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "reserve_draw": "US DOE released 180M barrels (1M bpd for 6 months), draining SPR to lowest level since 1983 (347M bbls)",
            "curve_impact": "Compressed prompt crude backwardation by $3-$5/bbl and temporarily suppressed US retail gasoline prices before midterms",
            "structural_floor": "Created a long-term commercial floor at $68-$72/bbl where DOE committed to repurchasing crude for refill",
        },
        falsification_tripwires=(
            "Commercial crude inventories surge >50M barrels eliminating the need for emergency reserve drawdowns",
        ),
        reference_url="https://www.energy.gov/ceser/strategic-petroleum-reserve",
    ),
    "case_w_abqaiq_drone_attack_2019": CaseStudyArchetype(
        case_id="case_w_abqaiq_drone_attack_2019",
        label="Saudi Aramco Abqaiq-Khurais Processing Facility Drone Strike",
        primary_domain="ENERGY_PETROCHEM",
        historical_date_window="2019-09-14 to 2019-10-01",
        chokepoints_affected=("ABQAIQ_CRUDE_STABILIZATION", "KHURAIS_OIL_FIELD", "RAS_TANURA_EXPORT"),
        primary_entities=("WTI_OIL", "INFLATION", "VIX", "DXY"),
        transmission_channels=("STABILIZATION_PLANT_DAMAGE", "5.7M_BPD_PRODUCTION_HALT", "OVERNIGHT_GAP_SURGE", "SPARE_CAPACITY_ELIMINATION"),
        lead_time_days=0,
        duration_days=20,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("BRENT_PROMPT_SPREAD", "ARAB_LIGHT_OFFICIAL_SELLING_PRICE"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "largest_shock": "Knocked out 5.7M bpd of crude processing (5% of global oil production)—the single largest supply disruption in history",
            "price_gap": "Brent crude gapped up +19.5% at Monday open (largest intraday percentage spike since Gulf War)",
            "rapid_repair": "Aramco mobilized emergency spare parts and storage inventories, restoring 100% capacity in 18 days",
        },
        falsification_tripwires=(
            "Satellite radar confirms stabilization towers suffered superficial damage with pipeline bypasses online within 48h",
        ),
        reference_url="https://www.eia.gov/todayinenergy/detail.php?id=41453",
    ),
}


# ===========================================================================
# 4. CRITICAL MINERALS, METALS & SMELTING
# ===========================================================================

CRITICAL_MINERALS_CASES: Dict[str, CaseStudyArchetype] = {
    "case_i_indonesia_nickel_hpal": CaseStudyArchetype(
        case_id="case_i_indonesia_nickel_hpal",
        label="Indonesian Nickel Export Ban, Tsingshan HPAL & LME Short Squeeze",
        primary_domain="CRITICAL_MINERALS",
        historical_date_window="2020-01 to 2024-06",
        chokepoints_affected=("SULAWESI_SMELTING_HUBS", "LME_WAREHOUSES", "MOROWALI_PARK"),
        primary_entities=("GROWTH", "CORPORATE_HEALTH", "FUTURES_LIQUIDITY"),
        transmission_channels=("ORE_EXPORT_BAN", "CHINESE_HPAL_CAPEX", "NPI_MATTE_FLOOD", "CLASS1_CLASS2_BIFURCATION", "LME_SHORT_SQUEEZE"),
        lead_time_days=180,
        duration_days=720,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("LME_NICKEL_CASH_3M", "NICKEL_PIG_IRON_DISCOUNT", "CLASS1_CLASS2_NICKEL_SPREAD"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "hpal_revolution": "Chinese investment built massive High-Pressure Acid Leach (HPAL) rotary kiln smelters in Indonesia",
            "market_flood": "Low-grade Nickel Pig Iron flooded the market and was converted into battery-grade nickel matte",
            "short_squeeze": "Historic March 2022 LME short squeeze to $100,000/ton; exchange took unprecedented step to cancel trades and halt trading",
        },
        falsification_tripwires=(
            "Indonesian constitutional court overturns raw ore mineral export ban",
            "Western battery manufacturers eliminate nickel chemistry in favor of 100% LFP cathodes",
        ),
        reference_url="https://www.lme.com/en/market-data/historical-data/nickel-suspension",
    ),
    "case_x_china_gallium_germanium_export": CaseStudyArchetype(
        case_id="case_x_china_gallium_germanium_export",
        label="China Gallium, Germanium & Antimony Strategic Mineral Export Controls",
        primary_domain="CRITICAL_MINERALS",
        historical_date_window="2023-08 to 2026-06",
        chokepoints_affected=("CHINESE_ALUMINA_REFINERIES", "HUNAN_ANTIMONY_MINES", "TAIWAN_COMPOUND_SEMI_FABS"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "UNCERTAINTY"),
        transmission_channels=("MOFCOM_EXPORT_LICENSING", "DEFENSE_RADAR_SUPPLY_PINCH", "WESTERN_SMELTER_RESTART_LAG", "SUBSTITUTION_HORIZON_EXPANSION"),
        lead_time_days=60,
        duration_days=500,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("GALLIUM_SPOT_EXPORT_PREMIUM", "GERMANIUM_INGOT_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "export_curb": "China (controlling 80%+ of global gallium/germanium) imposed dual-use export licensing on defense and chip materials",
            "defense_impact": "Gallium nitride (GaN) active electronically scanned array (AESA) military radar supply chains faced acute lead-time expansion",
            "ex-china_costs": "Gallium prices outside China surged >100% due to export permit administrative friction",
        },
        falsification_tripwires=(
            "Zinc and bauxite smelters in Europe/US install byproduct gallium extraction circuits within 90 days",
        ),
        reference_url="https://www.mofcom.gov.cn/",
    ),
    "case_y_drc_cobalt_copper_bottleneck": CaseStudyArchetype(
        case_id="case_y_drc_cobalt_copper_bottleneck",
        label="DRC Tenke Fungurume Cobalt Export Blockade & Artisanal Mining Quotas",
        primary_domain="CRITICAL_MINERALS",
        historical_date_window="2022-07 to 2023-04",
        chokepoints_affected=("KOLWEZI_MINING_HUB", "DURBAN_PORT_CORRIDOR", "KASUMBALESA_BORDER"),
        primary_entities=("CORPORATE_HEALTH", "GROWTH", "FUTURES_LIQUIDITY"),
        transmission_channels=("CUSTOMS_EXPORT_BAN", "PORT_DURBAN_FLOOD_CONGESTION", "BATTERY_CHEMISTRY_LFP_PIVOT", "WAREHOUSE_STOCKPILE_BUILD"),
        lead_time_days=30,
        duration_days=270,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("COBALT_METAL_HYDROXIDE_PAYABLE", "LME_COPPER_CASH_SETTLEMENT"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "mine_dispute": "Judicial dispute halted export of 120,000 tons of copper and cobalt from Tenke Fungurume mine",
            "trucking_queues": "10,000+ trucks backed up at Kasumbalesa border crossing into Zambia towards Durban port",
            "battery_revolution": "High cobalt volatility permanently accelerated EV automaker shift toward Cobalt-Free LFP batteries",
        },
        falsification_tripwires=(
            "DRC state miner Gécamines signs commercial export settlement releasing stockpiles within 14 days",
        ),
        reference_url="https://www.cmoc.com/",
    ),
    "case_z_south_africa_eskom_pgm": CaseStudyArchetype(
        case_id="case_z_south_africa_eskom_pgm",
        label="South Africa Eskom Stage 6 Load-Shedding & Platinum/Palladium Smelter Halt",
        primary_domain="CRITICAL_MINERALS",
        historical_date_window="2022-10 to 2023-06",
        chokepoints_affected=("BUSHVELD_COMPLEX_MINES", "ESKOM_GRID_TRANSMISSION", "RUSTENBURG_SMELTERS"),
        primary_entities=("GRID_LOAD", "CORPORATE_HEALTH", "COMMODITY"),
        transmission_channels=("GRID_STAGE6_LOAD_SHEDDING", "UNDERGROUND_SHAFT_EVACUATION", "PLATINUM_PALLADIUM_SUPPLY_CRUNCH", "AUTO_CATALYST_PRICE_SPIKE"),
        lead_time_days=1,
        duration_days=180,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("PLATINUM_GOLD_RATIO", "PALLADIUM_PLATINUM_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "power_cuts": "Eskom cut up to 6,000 MW of power, forcing underground deep-level platinum mines to halt operations",
            "smelter_shutdowns": "Continuous smelting furnaces suffered thermal freeze risks, reducing refined PGM output by 15%",
            "platinum_spread": "Platinum spot surged as automotive catalyst producers scrambled for physical London/Zurich bar delivery",
        },
        falsification_tripwires=(
            "Private solar and diesel microgrids allow Bushveld mines to operate 100% off-grid within 30 days",
        ),
        reference_url="https://www.eskom.co.za/",
    ),
}


# ===========================================================================
# 5. AGRICULTURAL, FERTILIZERS & FOOD SECURITY
# ===========================================================================

AGRICULTURAL_FERTILIZER_CASES: Dict[str, CaseStudyArchetype] = {
    "case_h_kakhovka_dam_agri": CaseStudyArchetype(
        case_id="case_h_kakhovka_dam_agri",
        label="Kakhovka Hydroelectric Dam Destruction & Agricultural Floodplain Wipeout",
        primary_domain="AGRICULTURAL_FERTILIZER",
        historical_date_window="2023-06 to 2023-12",
        chokepoints_affected=("DNIPRO_RIVER_BASIN", "BLACK_SEA_GRAIN_PORTS", "NORTH_CRIMEAN_CANAL"),
        primary_entities=("CROP_HEALTH", "SHIPPING_FLOW", "INFLATION"),
        transmission_channels=("IRRIGATION_DEPLETION", "SOUTHERN_UKRAINE_CROP_LOSS", "GRAIN_BASIS_COLLAPSE", "EU_IMPORT_BANS"),
        lead_time_days=7,
        duration_days=180,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("BLACK_SEA_WHEAT_BASIS", "WHEAT_CALENDAR_CONTANGO", "MATIF_CBOT_WHEAT_SPREAD"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "irrigation_loss": "Kakhovka reservoir drained into Black Sea, destroying irrigation for 500,000 hectares of prime arable land",
            "basis_blowout": "FOB Black Sea grain export basis collapsed; redirected grain clogged Central European rail corridors",
            "farmer_protests": "Triggered political crisis leading to unilateral Polish and Hungarian import bans on Ukrainian grain",
        },
        falsification_tripwires=(
            "Canal networks gravity-feed alternative reservoirs to maintain summer crop irrigation",
        ),
        reference_url="https://www.fao.org/newsroom/detail/ukraine-kakhovka-dam-impact-assessment",
    ),
    "case_aa_haber_bosch_ammonia_spike_2022": CaseStudyArchetype(
        case_id="case_aa_haber_bosch_ammonia_spike_2022",
        label="European Natural Gas Spike & Global Nitrogen Fertilizer Shut-in",
        primary_domain="AGRICULTURAL_FERTILIZER",
        historical_date_window="2021-09 to 2022-10",
        chokepoints_affected=("EUROPEAN_AMMONIA_PLANTS", "YARA_SOCIETE_GENERALE", "US_GULF_COAST_FERTILIZER"),
        primary_entities=("ENERGY_DEMAND", "INFLATION", "CROP_HEALTH", "CONSUMER_SPENDING"),
        transmission_channels=("NATGAS_COST_SURGE", "AMMONIA_PLANT_CURTAILMENT", "GLOBAL_FERTILIZER_SPIKE", "CORN_ACREAGE_CONTRACTION"),
        lead_time_days=14,
        duration_days=365,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("HABER_BOSCH_AMMONIA_COST", "CORN_SOY_RATIO", "UREA_MIDDLE_EAST_FOB"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "ammonia_halt": "70% of European ammonia synthesis offline as gas feedstock costs exceeded $1,200/ton of finished NH3",
            "urea_record": "Global urea and diammonium phosphate (DAP) prices hit all-time record highs, doubling farmers' planting costs",
            "acreage_shift": "US farmers shifted 2M+ acres from fertilizer-intensive corn to nitrogen-fixing soybeans",
        },
        falsification_tripwires=(
            "European gas prices fall below €40/MWh allowing full chemical plant restart economics",
        ),
        reference_url="https://www.yara.com/news-and-media/press-releases/",
    ),
    "case_ab_india_rice_wheat_export_bans": CaseStudyArchetype(
        case_id="case_ab_india_rice_wheat_export_bans",
        label="India Non-Basmati Rice & Wheat Export Embargo on Monsoon Deficit",
        primary_domain="AGRICULTURAL_FERTILIZER",
        historical_date_window="2022-05 to 2024-06",
        chokepoints_affected=("MUMBAI_JNPT_PORT", "KANDLA_GRAIN_TERMINALS", "WEST_AFRICA_FOOD_IMPORT"),
        primary_entities=("CROP_HEALTH", "INFLATION", "EM_FX"),
        transmission_channels=("DIRECTORATE_EXPORT_PROHIBITION", "GLOBAL_RICE_DEFICIT", "THAI_VIETNAMESE_PRICE_SPIKE", "FOOD_CPI_INFLATION"),
        lead_time_days=0,
        duration_days=700,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=("THAI_WHITE_RICE_5PCT", "FAO_FOOD_PRICE_INDEX"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "export_lock": "India (40% of global rice trade) banned export of broken and non-basmati white rice to protect domestic supply",
            "global_prices": "Benchmark Thai and Vietnamese 5% broken white rice surged from $450 to $650/ton (+45%)",
            "emerging_markets": "Severe food inflation spike across West Africa (Nigeria, Senegal, Ghana) and the Philippines",
        },
        falsification_tripwires=(
            "Monsoon rainfall in central/northern India exceeds historical long-period average by >15%",
            "Food Corporation of India (FCI) rice buffer stocks exceed mandatory buffer norms by 3x",
        ),
        reference_url="https://dgft.gov.in/",
    ),
}


# ===========================================================================
# 6. SOVEREIGN LIQUIDITY, BANKING SQUEEZES & CREDIT RUNS
# ===========================================================================

SOVEREIGN_LIQUIDITY_CASES: Dict[str, CaseStudyArchetype] = {
    "case_l_svb_banking_stress_2023": CaseStudyArchetype(
        case_id="case_l_svb_banking_stress_2023",
        label="March 2023 US Regional Bank Digital Deposit Run & BTFP Facility",
        primary_domain="SOVEREIGN_LIQUIDITY",
        historical_date_window="2023-03 to 2023-05",
        chokepoints_affected=("FED_DISCOUNT_WINDOW", "BTFP_FACILITY", "REGIONAL_BANK_DEPOSITS"),
        primary_entities=("LIQUIDITY_STRESS", "CREDIT_SPREAD", "GOLD", "DXY", "NOMINAL_BOND_YIELD"),
        transmission_channels=("HTM_BOND_UNREALIZED_LOSSES", "DIGITAL_DEPOSIT_RUN", "FED_BTFP_CREATION", "CREDIT_CONTRACTION", "YIELD_CURVE_STEEPENING"),
        lead_time_days=2,
        duration_days=60,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("FRA_OIS_SPREAD", "KRE_REGIONAL_BANK_ETF", "US_2Y_TREASURY_VOLATILITY"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "digital_run": "$42B withdrawn from Silicon Valley Bank in 10 hours via mobile banking and venture Slack channels",
            "fed_backstop": "Fed invoked Systemic Risk Exception, created Bank Term Funding Program (BTFP) lending against par collateral",
            "rates_repricing": "US 2-Year Treasury yields plummeted 109 bps in 3 trading days (largest 3-day drop since October 1987)",
            "safe_haven": "Gold and Mega-Cap Tech rallied aggressively as long-duration safe-haven liquidity assets",
        },
        falsification_tripwires=(
            "Interbank uncollateralized lending absorbs regional bank outflows without central bank liquidity facility",
        ),
        reference_url="https://www.federalreserve.gov/publications/files/svb-review-20230428.pdf",
    ),
    "case_ac_lehman_repo_freeze_2008": CaseStudyArchetype(
        case_id="case_ac_lehman_repo_freeze_2008",
        label="September 2008 Lehman Brothers Collapse & Triparty Repo Market Freeze",
        primary_domain="SOVEREIGN_LIQUIDITY",
        historical_date_window="2008-09 to 2009-03",
        chokepoints_affected=("TRIPARTY_REPO_CLEARING", "COMMERCIAL_PAPER_MARKET", "MONEY_MARKET_FUNDS"),
        primary_entities=("LIQUIDITY_STRESS", "CREDIT_SPREAD", "VIX", "EQUITIES", "DXY"),
        transmission_channels=("LEHMAN_BANKRUPTCY", "RESERVE_PRIMARY_BREAK_THE_BUCK", "CP_ROLLOVER_FREEZE", "GLOBAL_DOLLAR_SHORTAGE", "TARP_AND_QE1"),
        lead_time_days=0,
        duration_days=180,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("TED_SPREAD", "LIBOR_OIS_SPREAD", "HY_OAS_CREDIT_SPREAD", "MOVE_VIX_RATIO"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "freeze": "Triparty repo haircuts spiked from 0% to 50%; Reserve Primary Fund broke the buck ($0.97/share)",
            "ted_spread": "TED spread (3M LIBOR minus 3M T-bill) blew out to unprecedented all-time record 458 bps",
            "fx_swap_lines": "Global dollar funding seized; Fed established unlimited central bank bilateral FX liquidity swap lines",
        },
        falsification_tripwires=(
            "Private sector consortium buys broker-dealer assets without government backstop within 24h",
        ),
        reference_url="https://www.newyorkfed.org/research/quarterly_review/2009_issue1/0902fost.html",
    ),
    "case_ad_ltcm_russian_gko_1998": CaseStudyArchetype(
        case_id="case_ad_ltcm_russian_gko_1998",
        label="1998 Russian GKO Sovereign Default & LTCM Relative Value Collapse",
        primary_domain="SOVEREIGN_LIQUIDITY",
        historical_date_window="1998-08 to 1998-11",
        chokepoints_affected=("RUSSIAN_OFZ_GKO_BONDS", "WALL_STREET_PRIME_BROKERS", "ON-THE-RUN_TREASURY_MARKET"),
        primary_entities=("LIQUIDITY_STRESS", "CREDIT_SPREAD", "EQUITIES", "GOLD"),
        transmission_channels=("SOVEREIGN_RUBLE_DEFAULT", "SWAP_SPREAD_WIDENING", "VOLATILITY_SMILE_BLOWOUT", "CONSORTIUM_BAILOUT", "FED_INTERMEETING_CUTS"),
        lead_time_days=0,
        duration_days=90,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("ON_THE_RUN_OFF_THE_RUN_SPREAD", "INTEREST_RATE_SWAP_SPREAD", "VIX"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "default": "Russia devalued the ruble and defaulted on $40B of domestic Treasury debt (GKO/OFZ)",
            "ltcm_margin": "Long-Term Capital Management lost $4.6B as mean-reverting spread trades widened astronomically",
            "fed_intervention": "Federal Reserve Bank of New York organized a $3.625B private Wall Street consortium bailout and cut rates 75 bps",
        },
        falsification_tripwires=(
            "Russian Ministry of Finance honors GKO debt service payments in hard currency",
        ),
        reference_url="https://www.federalreserve.gov/pubs/oss/oss3/abstracts/1999_ltcm.html",
    ),
    "case_ae_european_sovereign_debt_2011": CaseStudyArchetype(
        case_id="case_ae_european_sovereign_debt_2011",
        label="European Sovereign Debt Crisis (PIIGS), Greek PSI & Draghi 'Whatever It Takes'",
        primary_domain="SOVEREIGN_LIQUIDITY",
        historical_date_window="2010-05 to 2012-09",
        chokepoints_affected=("GREEK_BTP_BONOS_SPREADS", "TARGET2_CLEARING", "ECB_COLLATERAL_FRAMEWORK"),
        primary_entities=("LIQUIDITY_STRESS", "EUR", "CREDIT_SPREAD", "EQUITIES"),
        transmission_channels=("SOVEREIGN_BANK_DOOM_LOOP", "TARGET2_CAPITAL_FLIGHT", "BTP_BUND_SPREAD_WIDENING", "DRAGHI_OMT_COMMITMENT"),
        lead_time_days=30,
        duration_days=850,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("BTP_BUND_10Y_SPREAD", "BONOS_BUND_SPREAD", "EURUSD_CROSS_CURRENCY_BASIS"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "doom_loop": "Italian 10Y yields breached 7.4% and Greek debt underwent historic 53.5% Private Sector Involvement (PSI) haircut",
            "capital_flight": "Deposits fled Greek, Italian, and Spanish banks to German Bunds, ballooning Bundesbank TARGET2 claims",
            "resolution": "Mario Draghi's July 2012 'Whatever it takes' speech and Outright Monetary Transactions (OMT) eliminated redenomination risk",
        },
        falsification_tripwires=(
            "Fiscal transfers between northern and southern Eurozone nations approved without conditionality",
        ),
        reference_url="https://www.ecb.europa.eu/press/key/date/2012/html/sp120726.en.html",
    ),
}


# ===========================================================================
# 7. MONETARY POLICY REGIME SHIFTS & INFLATION SHOCKS
# ===========================================================================

MONETARY_POLICY_CASES: Dict[str, CaseStudyArchetype] = {
    "case_af_volcker_fed_shock_1979": CaseStudyArchetype(
        case_id="case_af_volcker_fed_shock_1979",
        label="1979 Volcker Saturday Night Fed Shock & Money Supply Targeting",
        primary_domain="MONETARY_POLICY",
        historical_date_window="1979-10 to 1982-10",
        chokepoints_affected=("FED_DISCOUNT_RATE", "FED_FUNDS_EFFECTIVE", "COMMERCIAL_PRIME_RATE"),
        primary_entities=("POLICY_RATE", "INFLATION", "GROWTH", "DXY", "GOLD"),
        transmission_channels=("NON_BORROWED_RESERVE_TARGETING", "FED_FUNDS_SPIKE_20PCT", "DOUBLE_DIP_RECESSION", "LATAM_DEBT_CRISIS", "INFLATION_BREAK"),
        lead_time_days=0,
        duration_days=1100,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("REAL_FED_FUNDS_RATE", "YIELD_CURVE_INVERSION_2Y10Y"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "rates_spike": "Paul Volcker abandoned interest rate pegging; Fed funds rate spiked to an all-time peak of 20.0%",
            "double_dip": "Triggered severe 1980 and 1981-1982 back-to-back recessions with US unemployment hitting 10.8%",
            "dollar_supercycle": "DXY soared >50%, breaking the back of 1970s structural inflation and triggering 1982 Latin American sovereign debt crisis",
        },
        falsification_tripwires=(
            "Fed abandons reserve targets at the first sign of rising unemployment in Q1 1980",
        ),
        reference_url="https://www.federalreservehistory.org/essays/volckers-anti-inflation-measures",
    ),
    "case_ag_taper_tantrum_2013": CaseStudyArchetype(
        case_id="case_ag_taper_tantrum_2013",
        label="May 2013 Bernanke Taper Tantrum & Fragile Five EM Currency Crash",
        primary_domain="MONETARY_POLICY",
        historical_date_window="2013-05 to 2013-09",
        chokepoints_affected=("US_10Y_TREASURY_AUCTIONS", "EM_LOCAL_CURRENCY_BONDS", "CENTRAL_BANK_SWAP_LINES"),
        primary_entities=("NOMINAL_BOND_YIELD", "EM_FX", "DXY", "EQUITIES"),
        transmission_channels=("QE_TAPER_HINT", "TREASURY_TERM_PREMIUM_SPIKE", "EM_CAPITAL_OUTFLOW", "FRAGILE_FIVE_DEFENSIVE_HIKES"),
        lead_time_days=0,
        duration_days=120,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("US10Y_TERM_PREMIUM", "EM_FX_VOLATILITY_INDEX", "MOVE_INDEX"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "treasury_surge": "Ben Bernanke congressional testimony hinted at slowing QE3 purchases; US 10Y yields surged 140 bps (1.60% to 3.00%)",
            "em_rout": "'Fragile Five' currencies (INR, BRL, ZAR, TRY, IDR) suffered violent 15-25% devaluations as foreign hot money exited",
            "reversal": "Forced emerging market central banks to execute emergency rate hikes despite slowing domestic growth",
        },
        falsification_tripwires=(
            "FOMC issues immediate formal clarifying statement ruling out balance sheet reduction for 2+ years",
        ),
        reference_url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20130619.htm",
    ),
    "case_ah_fed_75bp_superhike_2022": CaseStudyArchetype(
        case_id="case_ah_fed_75bp_superhike_2022",
        label="June 2022 Fed 75bp Super-Hike Pivot & WSJ Timiraos Leak",
        primary_domain="MONETARY_POLICY",
        historical_date_window="2022-06 to 2022-11",
        chokepoints_affected=("FOMC_INTER_MEETING_GUIDANCE", "SOFR_FUTURES", "TREASURY_MARKET_LIQUIDITY"),
        primary_entities=("POLICY_RATE", "INFLATION", "NOMINAL_BOND_YIELD", "DXY", "EQUITIES"),
        transmission_channels=("WSJ_LEAKED_PIVOT", "75BP_JUMBO_RATE_HIKES", "MAXIMUM_GLOBAL_DOLLAR_SURGE", "CROSS_ASSET_VALUATION_COMPRESSION"),
        lead_time_days=2,
        duration_days=150,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("FED_FUNDS_TERMINAL_RATE_PRICING", "2Y10Y_INVERSION", "DXY_PARITY_BREACH"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "blackout_pivot": "Following 8.6% CPI print during FOMC blackout, Fed leaked 75bp hike via WSJ, breaking 50bp forward guidance",
            "jumbo_cycle": "Fed executed four consecutive 75bp rate hikes (fastest tightening cycle in 40 years)",
            "fx_extremes": "DXY surged to 20-year peak of 114.7; EUR/USD dropped below parity (0.9535); GBP hit historic low of 1.0350",
        },
        falsification_tripwires=(
            "Next CPI print drops >100 bps month-over-month prompting return to 25bp increments",
        ),
        reference_url="https://www.wsj.com/articles/fed-likely-to-consider-0-75-percentage-point-rate-rise-this-week-11655140788",
    ),
    "case_ai_boj_carry_unwind_2024": CaseStudyArchetype(
        case_id="case_ai_boj_carry_unwind_2024",
        label="August 5, 2024 Bank of Japan 25bp Hike & Global Yen Carry Trade Unwind",
        primary_domain="MONETARY_POLICY",
        historical_date_window="2024-07-31 to 2024-08-09",
        chokepoints_affected=("BOJ_POLICY_RATE", "USDJPY_SPOT_LIQUIDITY", "TOPIX_FUTURES_CIRCUIT_BREAKERS"),
        primary_entities=("JPY", "EQUITIES", "VIX", "LIQUIDITY_STRESS", "DXY"),
        transmission_channels=("BOJ_HAWKISH_HIKE", "YEN_SHORT_SQUEEZE", "GLOBAL_CROSS_ASSET_DE-LEVERAGING", "NIKKEI_CRASH", "BOJ_UCHIDA_PAUSE"),
        lead_time_days=0,
        duration_days=10,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("USDJPY_1W_RISK_REVERSAL", "VIX_SPOT_FUTURES_INVERSION", "NIKKEI_SPX_CORRELATION"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "rate_hike": "BOJ raised rates to 0.25% with hawkish Ueda press conference while US recession fears mounted (July weak payrolls)",
            "yen_appreciation": "USD/JPY collapsed from 161.95 to 141.68 in days; global carry trades funded in cheap yen violently unwound",
            "historic_crash": "Nikkei 225 plummeted -12.4% on Black Monday (worst single-day point drop in history, eclipsing 1987)",
            "vix_spike": "US VIX spiked intraday to 65.73 (3rd highest in history after 2008 and 2020); BOJ Deputy Uchida issued emergency dovish walkback",
        },
        falsification_tripwires=(
            "BOJ Deputy Governor announces central bank will not hike rates during financial market instability",
            "USD/JPY stabilizes above 148.0 with CME Yen net-speculative short positioning rebuilt",
        ),
        reference_url="https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2024/k240731a.pdf",
    ),
}


# ===========================================================================
# 8. CURRENCY PEG BREAKAGES & FX DEVALUATIONS
# ===========================================================================

CURRENCY_PEG_CASES: Dict[str, CaseStudyArchetype] = {
    "case_aj_black_wednesday_gbp_1992": CaseStudyArchetype(
        case_id="case_aj_black_wednesday_gbp_1992",
        label="September 16, 1992 Black Wednesday GBP European Exchange Rate Mechanism (ERM) Exit",
        primary_domain="CURRENCY_PEG",
        historical_date_window="1992-09-16 to 1992-10-01",
        chokepoints_affected=("BANK_OF_ENGLAND_FOREIGN_RESERVES", "ERM_EXCHANGE_FLOOR_2.778_DM", "UK_MINIMUM_LENDING_RATE"),
        primary_entities=("GBP", "POLICY_RATE", "LIQUIDITY_STRESS", "EQUITIES"),
        transmission_channels=("SPECULATIVE_FX_ATTACK", "FOREIGN_EXCHANGE_RESERVE_DEPLETION", "EMERGENCY_RATE_HIKES_10_TO_15PCT", "ERM_MEMBERSHIP_SUSPENSION", "GBP_DEVALUATION"),
        lead_time_days=5,
        duration_days=30,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("GBP_DEM_EXCHANGE_RATE", "UK_10Y_GILT_SPREAD"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "reserve_drain": "Bank of England spent £3.3B in foreign currency reserves in hours attempting to defend the 2.778 DM floor",
            "panic_hikes": "UK Treasury hiked interest rates twice in one day from 10% to 12% and then to 15% to stop capital flight",
            "peg_collapse": "At 7:00 PM, Chancellor Norman Lamont announced Britain's exit from the ERM; Sterling plunged 15% against the Deutsche Mark",
            "economic_rebirth": "UK economy entered an export-led boom as monetary independence allowed domestic rate cuts",
        },
        falsification_tripwires=(
            "German Bundesbank executes massive unlimited bilateral Deutsche Mark purchases of Sterling",
        ),
        reference_url="https://www.bankofengland.co.uk/working-paper/2005/the-operation-of-the-exchange-rate-mechanism",
    ),
    "case_ak_asian_financial_crisis_1997": CaseStudyArchetype(
        case_id="case_ak_asian_financial_crisis_1997",
        label="July 2, 1997 Thai Baht Peg Collapse & Asian Contagion",
        primary_domain="CURRENCY_PEG",
        historical_date_window="1997-07 to 1998-06",
        chokepoints_affected=("BANK_OF_THAILAND_FORWARD_BOOK", "OFFSHORE_ONSHORE_FX_BIFURCATION", "IMF_STANDBY_FACILITIES"),
        primary_entities=("EM_FX", "LIQUIDITY_STRESS", "GROWTH", "DXY"),
        transmission_channels=("DOLLAR_PEG_SPECULATIVE_ATTACK", "FORWARD_RESERVE_EXHAUSTION", "BAHT_FLOAT_DEVALUATION", "CORPORATE_FOREIGN_DEBT_INSOLVENCY", "REGIONAL_CONTAGION_CRASH"),
        lead_time_days=14,
        duration_days=365,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("OFFSHORE_BAHT_INTERBANK_RATE", "EM_SOVEREIGN_CDS_SPREAD"),
        prompt_curve_regime="INVERTED",
        historical_outcomes={
            "peg_break": "Bank of Thailand depleted its net usable reserves defending the 25 Baht/USD peg and floated the currency",
            "devaluation": "Baht lost 50% of value, triggering corporate insolvencies across dollar-denominated unhedged borrowers",
            "contagion": "Spilled over into Indonesian Rupiah (-80%), Malaysian Ringgit, and South Korean Won; IMF enforced structural adjustment programs",
        },
        falsification_tripwires=(
            "US Federal Reserve and Bank of Japan establish multi-billion dollar swap backstop to support Southeast Asian pegs",
        ),
        reference_url="https://www.imf.org/external/pubs/ft/fandd/1998/06/fischer.htm",
    ),
    "case_al_snb_eurchf_floor_2015": CaseStudyArchetype(
        case_id="case_al_snb_eurchf_floor_2015",
        label="January 15, 2015 Swiss National Bank (SNB) Unannounced EUR/CHF 1.20 Floor Removal",
        primary_domain="CURRENCY_PEG",
        historical_date_window="2015-01-15 to 2015-01-20",
        chokepoints_affected=("SNB_BALANCE_SHEET_FOREIGN_ASSETS", "EURCHF_1.2000_ORDER_BOOK", "RETAIL_FX_BROKER_CLEARING"),
        primary_entities=("CHF", "EUR", "LIQUIDITY_STRESS", "EQUITIES"),
        transmission_channels=("SNB_SURPRISE_CAPITULATION", "LIQUIDITY_VACUUM_COLLAPSE", "CHF_FLASH_SURGE_30PCT", "FX_BROKER_INSOLVENCIES", "NEGATIVE_INTEREST_RATE_EXPANSION"),
        lead_time_days=0,
        duration_days=7,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("EURCHF_SPOT_DISLOCATION", "SNB_POLICY_RATE_DISCOUNT"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "surprise_removal": "SNB abruptly discontinued its 3-year-old 1.2000 peg just days ahead of ECB Quantitative Easing announcement",
            "fx_tsunami": "EUR/CHF plummeted from 1.2000 to an intraday low of 0.8500 in 20 minutes (-30% move in a G10 currency)",
            "broker_failures": "Major retail FX brokerages (Alpari UK, Excel Markets) went bankrupt due to negative client equity and lack of liquidity",
        },
        falsification_tripwires=(
            "SNB reaffirms unconditional commitment to expand balance sheet infinitely to defend 1.2000",
        ),
        reference_url="https://www.snb.ch/en/publications/communication/press-releases/2015/pre_20150115",
    ),
}


# ===========================================================================
# 9. ECONOMIC SANCTIONS, FINANCIAL PLUMBING & ASSET FREEZES
# ===========================================================================

SANCTIONS_FINANCE_CASES: Dict[str, CaseStudyArchetype] = {
    "case_k_russia_sanctions_2022": CaseStudyArchetype(
        case_id="case_k_russia_sanctions_2022",
        label="Russia 2022 Sovereign FX Reserve Freeze, SWIFT Ban & Shadow Tanker Fleet",
        primary_domain="SANCTIONS_FINANCE",
        historical_date_window="2022-02 to 2026-06",
        chokepoints_affected=("SWIFT_MESSAGING", "EUROCLEAR_CLEARSTREAM", "BALTIC_SEABORNE_OIL", "G7_MARITIME_INSURANCE"),
        primary_entities=("LIQUIDITY_STRESS", "WTI_OIL", "DXY", "GOLD", "MARKET_STRESS"),
        transmission_channels=("RESERVE_FREEZE", "SWIFT_EXCLUSION", "G7_PRICE_CAP", "SHADOW_FLEET_STS_TRANSFERS", "DE-DOLLARIZATION_BILATERAL_SETTLEMENT"),
        lead_time_days=0,
        duration_days=1400,
        tau_substitution_horizon="YEARS",
        core_spread_distortions=("URALS_BRENT_DISCOUNT", "CROSS_CURRENCY_BASIS_SWAP", "CENTRAL_BANK_GOLD_PURCHASES"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "reserve_freeze": "$300B of Bank of Russia sovereign FX assets immobilized at Euroclear, Clearstream, and FRBNY",
            "shadow_fleet": "Russia assembled 600+ vintage dark fleet oil tankers operating with non-Western P&I insurance and ship-to-ship transfers",
            "trade_redirection": "Crude oil exports redirected to India and China; bilateral non-dollar settlement (Yuan, Rubles, Dirhams) surged to >85%",
            "gold_buying": "Global central banks (PBOC, RBI, Turkey) accelerated physical gold accumulation to historic records",
        },
        falsification_tripwires=(
            "G7 and non-aligned nations establish unified compliance enforcement halting 100% of shadow fleet port access",
        ),
        reference_url="https://home.treasury.gov/news/press-releases/jy0608",
    ),
    "case_am_iran_secondary_sanctions_dark_fleet": CaseStudyArchetype(
        case_id="case_am_iran_secondary_sanctions_dark_fleet",
        label="Iranian Petroleum Secondary Sanctions & Malaysian Riau STS Dark Fleet Hubs",
        primary_domain="SANCTIONS_FINANCE",
        historical_date_window="2018-11 to 2026-06",
        chokepoints_affected=("STRAIT_OF_MALACCA_STS_ZONES", "TEAPOT_REFINERIES_SHANDONG", "KUNLUN_BANK_SETTLEMENT"),
        primary_entities=("WTI_OIL", "LIQUIDITY_STRESS", "GROWTH"),
        transmission_channels=("US_SECONDARY_SANCTIONS", "AIS_TRANSPONDER_SPOOFING", "SHIP_TO_SHIP_CARGO_BLENDING", "SHANDONG_TEAPOT_DEMAND", "OFFSHORE_RMB_CLEARING"),
        lead_time_days=0,
        duration_days=2000,
        tau_substitution_horizon="DECADES",
        core_spread_distortions=("MALAYSIAN_BLEND_CRUDE_DISCOUNT", "VLCC_DIRTY_TANKER_RATE"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "dark_transfers": "1.5M bpd of Iranian crude transported via transponder-spoofed dark tankers and blended off Malaysia as 'Malaysian blend'",
            "independent_refiners": "Sold at $5-$10/bbl discounts to independent Chinese 'teapot' refiners in Shandong settled in offshore RMB",
            "evasion_plumbing": "Demonstrated that secondary sanctions without physical maritime interception result in permanent trade architecture bifurcation",
        },
        falsification_tripwires=(
            "Maritime coastal authorities in Malaysia and Indonesia seize unflagged STS transshipment vessels",
        ),
        reference_url="https://www.treasury.gov/resource-center/sanctions/Programs/pages/iran.aspx",
    ),
    "case_an_us_secondary_sanctions_correspondent_banking": CaseStudyArchetype(
        case_id="case_an_us_secondary_sanctions_correspondent_banking",
        label="Executive Order 14114 US Secondary Sanctions on Foreign Correspondent Banks",
        primary_domain="SANCTIONS_FINANCE",
        historical_date_window="2023-12 to 2026-06",
        chokepoints_affected=("CHINESE_STATE_BANKS", "TURKISH_ISBANK_VAKIFBANK", "UAE_NBD_MASHREQ"),
        primary_entities=("LIQUIDITY_STRESS", "CORPORATE_HEALTH", "DXY"),
        transmission_channels=("OFAC_SECONDARY_AUTHORITY", "CORRESPONDENT_CLEARING_CHILL", "DUAL-USE_PAYMENT_REJECTION", "CROSS_BORDER_SETTLEMENT_FRICTION"),
        lead_time_days=10,
        duration_days=500,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("RUSSIAN_IMPORT_PAYMENT_DELAY_DAYS", "OFFSHORE_CNY_RUBLE_CROSS_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "compliance_chill": "Major Chinese banks (ICBC, Bank of China, CCB) rejected cross-border payments from Russia to preserve US Dollar clearing",
            "payment_friction": "Russian commercial import payment processing delays expanded from 2 days to 60+ days",
            "alternative_rails": "Spurred adoption of decentralized payment agents, gold barter, and central bank digital currencies (mBridge)",
        },
        falsification_tripwires=(
            "OFAC rescinds secondary sanctions authority for commercial trade transactions",
        ),
        reference_url="https://ofac.treasury.gov/recent-actions/20231222",
    ),
}


# ===========================================================================
# 10. SYSTEMIC LIQUIDITY VACUUMS & FLASH CRASHES
# ===========================================================================

SYSTEMIC_LIQUIDITY_CASES: Dict[str, CaseStudyArchetype] = {
    "case_ao_flash_crash_may_2010": CaseStudyArchetype(
        case_id="case_ao_flash_crash_may_2010",
        label="May 6, 2010 E-mini S&P 500 Algorithmic Flash Crash & Order Book Vacuum",
        primary_domain="SYSTEMIC_LIQUIDITY",
        historical_date_window="2010-05-06",
        chokepoints_affected=("CME_GLOBEX_EMINI_ORDER_BOOK", "NYSE_ARCA_CLEARING", "DIRECT_FEED_INTERNALIZERS"),
        primary_entities=("EQUITIES", "VIX", "LIQUIDITY_STRESS", "MARKET_STRESS"),
        transmission_channels=("WADDELL_REED_ALGO_SELL_ORDER", "HFT_MARKET_MAKER_WITHDRAWAL", "CROSS_MARKET_ARBITRAGE_DRAIN", "STUB_QUOTE_EXECUTIONS", "LIMIT_UP_LIMIT_DOWN_REFORMS"),
        lead_time_days=0,
        duration_days=1,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("ORDER_BOOK_DEPTH_TOP5", "VIX_INTRADAY_EXPLOSION", "ETF_NAV_DISCOUNT"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "crash_cascade": "In 36 minutes, the Dow dropped 998.5 points (-9%) as 75,000 E-mini contracts dumped without price limits",
            "liquidity_hole": "High-Frequency Trading algorithms withdrew bids; blue-chip stocks (Accenture, P&G) traded at $0.01 or $100,000",
            "market_reform": "SEC and CFTC established single-stock circuit breakers and Limit-Up/Limit-Down (LULD) bands",
        },
        falsification_tripwires=(
            "Market-maker stub quotes are physically prevented by exchange matching engine price bands",
        ),
        reference_url="https://www.sec.gov/news/studies/2010/marketevents-report.pdf",
    ),
    "case_ap_covid_crash_negative_crude_2020": CaseStudyArchetype(
        case_id="case_ap_covid_crash_negative_crude_2020",
        label="March-April 2020 COVID-19 Liquidity Crash & -$37.63/bbl WTI Negative Crude Settlement",
        primary_domain="SYSTEMIC_LIQUIDITY",
        historical_date_window="2020-03-01 to 2020-04-25",
        chokepoints_affected=("CUSHING_OKLAHOMA_STORAGE", "GLOBAL_AIR_TRAVEL", "OFFSHORE_VLCC_FLOATING_STORAGE"),
        primary_entities=("WTI_OIL", "VIX", "EQUITIES", "DXY", "NOMINAL_BOND_YIELD", "GOLD"),
        transmission_channels=("PANDEMIC_MOBILITY_COLLAPSE", "DASH_FOR_CASH_ALL_ASSETS_LIQUIDATED", "CUSHING_STORAGE_TANK_SATURATION", "NEGATIVE_COMMODITY_PRICING", "UNLIMITED_FED_QE"),
        lead_time_days=7,
        duration_days=60,
        tau_substitution_horizon="MONTHS",
        core_spread_distortions=("WTI_PROMPT_DEFERRED_SPREAD", "MOVE_INDEX", "GOLD_SILVER_RATIO", "CRACK_SPREAD_321"),
        prompt_curve_regime="CONTANGO",
        historical_outcomes={
            "dash_for_cash": "In March 2020, even Gold and US Treasuries were sold to meet margin calls; VIX hit 82.69 (equaling 2008 peak)",
            "negative_oil": "On April 20, May WTI futures expired at -$37.63/barrel as Cushing storage reached 100% saturation and retail ETFs were forced to dump",
            "monetary_flood": "Fed unleashed unlimited QE, corporate credit facilities (SMCCF), and Congress passed $2.2T CARES Act",
        },
        falsification_tripwires=(
            "OPEC+ immediately agrees to 15M bpd production cuts prior to April contract expiry",
        ),
        reference_url="https://www.cftc.gov/PressRoom/PressReleases/8315-20",
    ),
    "case_aq_black_monday_1987": CaseStudyArchetype(
        case_id="case_aq_black_monday_1987",
        label="October 19, 1987 Black Monday Dynamic Hedging & Portfolio Insurance Cascade",
        primary_domain="SYSTEMIC_LIQUIDITY",
        historical_date_window="1987-10-14 to 1987-10-23",
        chokepoints_affected=("NYSE_DESIGNATED_ORDER_TURNAROUND_DOT", "CME_S&P_FUTURES_PIT", "SPECIALIST_CAPITAL_FLOORS"),
        primary_entities=("EQUITIES", "VIX", "NOMINAL_BOND_YIELD", "LIQUIDITY_STRESS"),
        transmission_channels=("RISING_RATES_AND_TRADE_DEFICIT", "PORTFOLIO_INSURANCE_DYNAMIC_HEDGING", "FUTURES_CASH_ARBITRAGE_BREAKDOWN", "22.6PCT_EQUITY_CRASH", "GREENSPAN_FED_PUT_BIRTH"),
        lead_time_days=2,
        duration_days=10,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("SP500_FUTURES_CASH_BASIS", "TED_SPREAD"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "greatest_crash": "Dow Jones plunged -22.61% in a single day (largest percentage drop in US stock market history)",
            "synthetic_selling": "Algorithmic 'portfolio insurance' systematically sold futures as prices dropped, creating a self-reinforcing reflexive death spiral",
            "greenspan_put": "Alan Greenspan's Fed issued a 1-sentence statement confirming central bank liquidity backstop, birthing the modern 'Fed Put'",
        },
        falsification_tripwires=(
            "Exchange floor specialists maintain continuous two-sided liquidity using designated capital reserves",
        ),
        reference_url="https://www.federalreservehistory.org/essays/stock-market-crash-of-1987",
    ),
    "case_ar_gamestop_meme_short_squeeze_2021": CaseStudyArchetype(
        case_id="case_ar_gamestop_meme_short_squeeze_2021",
        label="January 2021 GameStop (GME) Retail Gamma Squeeze & DTCC Clearinghouse Margin Squeeze",
        primary_domain="SYSTEMIC_LIQUIDITY",
        historical_date_window="2021-01-20 to 2021-02-05",
        chokepoints_affected=("DTCC_NSCC_CLEARING_COLLATERAL", "APEX_CLEARING", "ROBINHOOD_APP_BROKERAGE"),
        primary_entities=("EQUITIES", "VIX", "CORPORATE_HEALTH", "MARKET_STRESS"),
        transmission_channels=("140PCT_SHORT_INTEREST", "OUT_OF_THE_MONEY_CALL_BUYING", "DEALER_GAMMA_HEDGING", "NSCC_EXCESS_CAPITAL_SURGE", "PCO_BUY_BUTTON_HALT"),
        lead_time_days=3,
        duration_days=14,
        tau_substitution_horizon="DAYS",
        core_spread_distortions=("EQUITY_BORROW_FEE_RATE", "OPTION_IMPLIED_VOLATILITY_SKEW"),
        prompt_curve_regime="BACKWARDATION",
        historical_outcomes={
            "gamma_squeeze": "Retail call option volume forced market maker dealers into reflexive upside delta-hedging; GME surged from $18 to $483",
            "hedge_fund_losses": "Short-focused hedge funds (Melvin Capital) suffered multi-billion dollar losses requiring emergency capital injections",
            "clearing_crisis": "NSCC demanded $3.0B in collateral from retail brokers; Robinhood restricted trading to 'Position Closing Only' (PCO), collapsing the rally",
        },
        falsification_tripwires=(
            "Stock borrow fees drop below 10% annualized with short interest resolving below 50% of float",
        ),
        reference_url="https://www.sec.gov/files/staff-report-equity-options-market-stucture-conditions-early-2021.pdf",
    ),
}


# ===========================================================================
# MASTER CONSOLIDATED CASE STUDY REGISTRY
# ===========================================================================

MASTER_CASE_STUDY_REGISTRY: Dict[str, CaseStudyArchetype] = {}
for group in (
    MARITIME_CHOKEPOINT_CASES,
    TECH_HARDWARE_CASES,
    ENERGY_PETROCHEM_CASES,
    CRITICAL_MINERALS_CASES,
    AGRICULTURAL_FERTILIZER_CASES,
    SOVEREIGN_LIQUIDITY_CASES,
    MONETARY_POLICY_CASES,
    CURRENCY_PEG_CASES,
    SANCTIONS_FINANCE_CASES,
    SYSTEMIC_LIQUIDITY_CASES,
):
    MASTER_CASE_STUDY_REGISTRY.update(group)


class CaseStudyLibrary:
    """Master repository, indexer, search engine and manager for all historical and learned case studies."""

    def __init__(self, persistence_file: str = "artifacts/learned_case_studies.json") -> None:
        self.persistence_file = persistence_file
        self.library: Dict[str, CaseStudyArchetype] = dict(MASTER_CASE_STUDY_REGISTRY)
        self._load_persisted_learned_cases()

    def _load_persisted_learned_cases(self) -> int:
        if not os.path.exists(self.persistence_file):
            return 0
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = 0
            for item in data:
                case_id = item.get("case_id")
                if not case_id:
                    continue
                archetype = CaseStudyArchetype(
                    case_id=case_id,
                    label=item.get("label", "Learned Case"),
                    primary_domain=item.get("primary_domain", "MACRO_REGIME"),
                    historical_date_window=item.get("historical_date_window", "N/A"),
                    chokepoints_affected=tuple(item.get("chokepoints_affected", ())),
                    primary_entities=tuple(item.get("primary_entities", ())),
                    transmission_channels=tuple(item.get("transmission_channels", ())),
                    lead_time_days=int(item.get("lead_time_days", 0)),
                    duration_days=int(item.get("duration_days", 30)),
                    tau_substitution_horizon=item.get("tau_substitution_horizon", "DAYS"),
                    core_spread_distortions=tuple(item.get("core_spread_distortions", ())),
                    prompt_curve_regime=item.get("prompt_curve_regime", "BACKWARDATION"),
                    historical_outcomes=item.get("historical_outcomes", {}),
                    falsification_tripwires=tuple(item.get("falsification_tripwires", ())),
                    reference_url=item.get("reference_url", "https://antigravity.internal"),
                )
                self.library[case_id] = archetype
                loaded += 1
            log.info("Loaded %d dynamically learned case studies into Library.", loaded)
            return loaded
        except Exception as e:
            log.warning("Failed to load persisted learned cases into Library: %s", e)
            return 0

    def get_case(self, case_id: str) -> Optional[CaseStudyArchetype]:
        return self.library.get(case_id)

    def list_all_cases(self) -> List[CaseStudyArchetype]:
        return list(self.library.values())

    def filter_by_domain(self, domain: str) -> List[CaseStudyArchetype]:
        target = domain.strip().upper()
        return [c for c in self.library.values() if c.primary_domain == target]

    def search_cases(self, query: str) -> List[CaseStudyArchetype]:
        q = query.strip().lower()
        if not q:
            return self.list_all_cases()
        results: List[CaseStudyArchetype] = []
        for c in self.library.values():
            text_corpus = (
                f"{c.case_id} {c.label} {c.primary_domain} "
                f"{' '.join(c.chokepoints_affected)} {' '.join(c.primary_entities)} "
                f"{' '.join(c.transmission_channels)} {' '.join(c.core_spread_distortions)} "
                f"{' '.join(c.historical_outcomes.values())}"
            ).lower()
            if q in text_corpus:
                results.append(c)
        return results

    def get_domains_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for c in self.library.values():
            summary[c.primary_domain] = summary.get(c.primary_domain, 0) + 1
        return summary

    def to_dict_list(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.library.values():
            out.append({
                "case_id": c.case_id,
                "label": c.label,
                "primary_domain": c.primary_domain,
                "historical_date_window": c.historical_date_window,
                "chokepoints_affected": list(c.chokepoints_affected),
                "primary_entities": list(c.primary_entities),
                "transmission_channels": list(c.transmission_channels),
                "lead_time_days": c.lead_time_days,
                "duration_days": c.duration_days,
                "tau_substitution_horizon": c.tau_substitution_horizon,
                "core_spread_distortions": list(c.core_spread_distortions),
                "prompt_curve_regime": c.prompt_curve_regime,
                "historical_outcomes": c.historical_outcomes,
                "falsification_tripwires": list(c.falsification_tripwires),
                "reference_url": c.reference_url,
                "execution_authority": False,
            })
        return out


_GLOBAL_LIBRARY: Optional[CaseStudyLibrary] = None


def get_case_study_library() -> CaseStudyLibrary:
    global _GLOBAL_LIBRARY
    if _GLOBAL_LIBRARY is None:
        _GLOBAL_LIBRARY = CaseStudyLibrary()
    return _GLOBAL_LIBRARY
