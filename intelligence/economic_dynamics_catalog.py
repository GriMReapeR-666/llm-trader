"""Canonical Economic Dynamics Source of Truth (machine-readable).

Distilled from ``economic dynamics.txt`` into typed entities, causal edges,
correlation regimes, transmission channels, and event templates.

Design rules (project-aligned):
- Edges are hypotheses, never trading permissions.
- ``execution_authority`` is always False.
- Seeded edges enter MacroGraph as CANDIDATE only.
- Confidence is conservative theory prior until empirical validation.
- Time-varying signs use CONDITIONAL + ``active_regimes``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


CATALOG_VERSION = "1.0.0"
SOURCE_DOCUMENT = "economic dynamics.txt"
CITATION_URL = "https://www.federalreserve.gov/monetarypolicy/fomc.htm"
CITATION_PUBLISHER = "Board of Governors of the Federal Reserve System"
EXECUTION_AUTHORITY = False

# ---------------------------------------------------------------------------
# Entity taxonomy (layers map to processing / retrieval cost)
# ---------------------------------------------------------------------------
# L0 policy anchors → L1 financial prices → L2 real economy → L3 risk/sentiment
# → L4 cross-asset instruments the trader actually watches.

ENTITY_TYPES = (
    "MONETARY_POLICY",
    "LIQUIDITY",
    "CREDIT",
    "MACRO_INDICATOR",
    "YIELD_CURVE",
    "CURRENCY",
    "COMMODITY",
    "EQUITY",
    "VOLATILITY",
    "SENTIMENT",
    "GEOPOLITICAL",
    "MARKET_STRUCTURE",
    "ASSET_REGIME",
    "EVENT_CLASS",
    "NOWCAST_PROXY",
)

ENTITIES: List[Dict[str, Any]] = [
    # L0 — Policy
    {"name": "CENTRAL_BANK", "entity_type": "MONETARY_POLICY", "layer": 0, "aliases": ["FED", "ECB", "BOJ", "PBOC", "BOE", "RBA", "SNB", "BOC", "RBNZ"]},
    {"name": "POLICY_RATE", "entity_type": "MONETARY_POLICY", "layer": 0, "aliases": ["FED_FUNDS", "ECB_DEPOSIT", "BOJ_POLICY"]},
    {"name": "QE_QT", "entity_type": "LIQUIDITY", "layer": 0, "aliases": ["BALANCE_SHEET", "QT", "QE"]},
    {"name": "FORWARD_GUIDANCE", "entity_type": "MONETARY_POLICY", "layer": 0, "aliases": []},
    {"name": "RESERVE_REQUIREMENT", "entity_type": "MONETARY_POLICY", "layer": 0, "aliases": []},
    {"name": "OMO", "entity_type": "LIQUIDITY", "layer": 0, "aliases": ["OPEN_MARKET_OPERATIONS"]},
    # L1 — Money / credit / yields
    {"name": "MONEY_SUPPLY", "entity_type": "LIQUIDITY", "layer": 1, "aliases": ["M2", "GLOBAL_LIQUIDITY"]},
    {"name": "COMMERCIAL_BORROWING_COST", "entity_type": "CREDIT", "layer": 1, "aliases": ["LOAN_RATE", "MORTGAGE_RATE"]},
    {"name": "SAVINGS_INCENTIVE", "entity_type": "CREDIT", "layer": 1, "aliases": []},
    {"name": "NOMINAL_BOND_YIELD", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": ["US10Y", "US2Y", "US30Y"]},
    {"name": "REAL_YIELD", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": ["TIPS_REAL", "DFII10"]},
    {"name": "TERM_PREMIUM", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": []},
    {"name": "YIELD_CURVE_SLOPE", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": ["T10Y2Y", "T10Y3M"]},
    {"name": "CREDIT_SPREAD", "entity_type": "CREDIT", "layer": 1, "aliases": ["BAA_SPREAD", "HY_OAS"]},
    {"name": "OPPORTUNITY_COST_OF_GOLD", "entity_type": "CREDIT", "layer": 1, "aliases": []},
    # L2 — Real economy
    {"name": "AGGREGATE_DEMAND", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["SPENDING", "INVESTMENT"]},
    {"name": "INFLATION", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["CPI", "PCE", "CORE_PCE"]},
    {"name": "GROWTH", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["GDP", "INDPRO"]},
    {"name": "EMPLOYMENT", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["NFP", "UNRATE"]},
    {"name": "TRADE_BALANCE", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": []},
    {"name": "NEUTRAL_RATE_RSTAR", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["R_STAR"]},
    # L2 — Nowcast proxies (alternative data; official prints reconciled at back end)
    {"name": "CONSUMER_SPENDING", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["RETAIL_NOWCAST", "CARD_SPEND"]},
    {"name": "SHELTER_RENT", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["ZORI", "RENT_INDEX"]},
    {"name": "CPI_NOWCAST", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["SKU_INFLATION", "PRICE_BASKET"]},
    {"name": "LABOR_DEMAND", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["JOB_POSTINGS", "JOLTS_NOWCAST"]},
    {"name": "LABOR_SUPPLY", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["HOURS_WORKED", "SHIFT_HOURS"]},
    {"name": "GRID_LOAD", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["ELECTRICITY_DEMAND", "MW_LOAD"]},
    {"name": "SHIPPING_FLOW", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["AIS_FLOW", "TANKER_TRACKING"]},
    {"name": "FREIGHT_VOLUME", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["CASS_FREIGHT", "DAT_LOAD_RATIO"]},
    {"name": "RAIL_ACTIVITY", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["TRAIN_VELOCITY", "AAR_RAIL"]},
    {"name": "CROP_HEALTH", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["NDVI", "CROP_YIELD_PROXY"]},
    {"name": "OIL_STORAGE", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["TANK_STORAGE", "CUSHING_STORAGE"]},
    {"name": "GAS_STORAGE", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["NATGAS_STORAGE", "PIPELINE_FLOW"]},
    {"name": "HOUSING_ACTIVITY", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["HOUSING_STARTS_PROXY", "EXCAVATION"]},
    {"name": "CORPORATE_HEALTH", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["CORP_NOWCAST", "EARNINGS_PROXY"]},
    {"name": "GRAIN_EXPORTS", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["BARGE_FREIGHT", "GRAIN_QUEUES"]},
    {"name": "FUTURES_LIQUIDITY", "entity_type": "NOWCAST_PROXY", "layer": 1, "aliases": ["SOFR_FUTURES", "FED_FUNDS_FUTURES"]},
    {"name": "FUTURES_POSITIONING", "entity_type": "NOWCAST_PROXY", "layer": 3, "aliases": ["CME_POSITIONING", "COT_NOWCAST"]},
    {"name": "OPTIONS_SKEW", "entity_type": "NOWCAST_PROXY", "layer": 3, "aliases": ["VOL_SKEW", "DOM_IMBALANCE"]},
    {"name": "ENERGY_DEMAND", "entity_type": "NOWCAST_PROXY", "layer": 2, "aliases": ["POWER_DEMAND", "DEGREE_DAYS"]},
    {"name": "MARKET_STRESS", "entity_type": "NOWCAST_PROXY", "layer": 3, "aliases": ["CREDIT_SPREADS_OAS", "FINANCIAL_CONDITIONS"]},
    {"name": "POLICY_SENTIMENT", "entity_type": "NOWCAST_PROXY", "layer": 0, "aliases": ["FED_SPEECH_SENTIMENT", "CENTRAL_BANK_TONE"]},
    {"name": "MACRO_SENTIMENT", "entity_type": "NOWCAST_PROXY", "layer": 3, "aliases": ["SEARCH_INTEREST", "RECESSION_FEAR"]},
    # L3 — Risk / FX / sentiment
    {"name": "DXY", "entity_type": "CURRENCY", "layer": 3, "aliases": ["USD_INDEX", "DX_Y"]},
    {"name": "USD", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "EUR", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "JPY", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "CHF", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "AUD", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "NZD", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "CAD", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "GBP", "entity_type": "CURRENCY", "layer": 3, "aliases": []},
    {"name": "EM_FX", "entity_type": "CURRENCY", "layer": 3, "aliases": ["MXN", "BRL", "ZAR", "TRY"]},
    {"name": "VIX", "entity_type": "VOLATILITY", "layer": 3, "aliases": ["EQUITY_IMPLIED_VOL"]},
    {"name": "RISK_SENTIMENT", "entity_type": "SENTIMENT", "layer": 3, "aliases": ["RISK_ON", "RISK_OFF"]},
    {"name": "UNCERTAINTY", "entity_type": "SENTIMENT", "layer": 3, "aliases": ["GEOPOLITICAL_RISK"]},
    {"name": "LIQUIDITY_STRESS", "entity_type": "MARKET_STRUCTURE", "layer": 3, "aliases": ["REPO_STRESS", "FUNDING_STRESS"]},
    # L4 — Traded assets / commodities
    {"name": "GOLD", "entity_type": "COMMODITY", "layer": 4, "aliases": ["XAUUSD", "XAU"]},
    {"name": "SILVER", "entity_type": "COMMODITY", "layer": 4, "aliases": ["XAGUSD"]},
    {"name": "WTI_OIL", "entity_type": "COMMODITY", "layer": 4, "aliases": ["USOIL", "CL", "WTI"]},
    {"name": "EQUITIES", "entity_type": "EQUITY", "layer": 4, "aliases": ["SPX", "ES", "EQ"]},
    {"name": "US_TREASURIES", "entity_type": "YIELD_CURVE", "layer": 4, "aliases": ["TLT", "UST"]},
    {"name": "CRYPTO", "entity_type": "ASSET_REGIME", "layer": 4, "aliases": ["BTC", "ETH"]},
    # Event classes
    {"name": "CB_MEETING", "entity_type": "EVENT_CLASS", "layer": 0, "aliases": ["FOMC", "ECB_MEETING", "BOJ_MEETING"]},
    {"name": "CPI_RELEASE", "entity_type": "EVENT_CLASS", "layer": 2, "aliases": ["INFLATION_PRINT"]},
    {"name": "NFP_RELEASE", "entity_type": "EVENT_CLASS", "layer": 2, "aliases": []},
    {"name": "WAR_SHOCK", "entity_type": "EVENT_CLASS", "layer": 0, "aliases": ["MILITARY_CONFLICT"]},
    {"name": "SANCTIONS", "entity_type": "GEOPOLITICAL", "layer": 0, "aliases": []},
    {"name": "TRADE_WAR", "entity_type": "EVENT_CLASS", "layer": 0, "aliases": ["TARIFF_SHOCK"]},
    {"name": "POLITICAL_SHOCK", "entity_type": "EVENT_CLASS", "layer": 0, "aliases": ["ELECTION", "POLICY_TWEET"]},
    {"name": "BANKING_STRESS", "entity_type": "EVENT_CLASS", "layer": 1, "aliases": ["BANK_FAILURE"]},
    {"name": "SUPPLY_SHOCK_ENERGY", "entity_type": "EVENT_CLASS", "layer": 4, "aliases": ["HORMUZ", "OPEC_CUT"]},
    {"name": "VOLATILITY", "entity_type": "VOLATILITY", "layer": 3, "aliases": ["IMPLIED_VOL"]},
    # L1 / L2 / L3 — Advanced Macro Dynamics, Plumbing & Conversion Spreads
    {"name": "US_NET_LIQUIDITY", "entity_type": "LIQUIDITY", "layer": 1, "aliases": ["FED_NET_LIQUIDITY", "WALCL_TGA_RRP"]},
    {"name": "BEAR_STEEPENER", "entity_type": "ASSET_REGIME", "layer": 3, "aliases": ["CURVE_BEAR_STEEPENER"]},
    {"name": "BULL_STEEPENER", "entity_type": "ASSET_REGIME", "layer": 3, "aliases": ["CURVE_BULL_STEEPENER"]},
    {"name": "BEAR_FLATTENER", "entity_type": "ASSET_REGIME", "layer": 3, "aliases": ["CURVE_BEAR_FLATTENER"]},
    {"name": "BULL_FLATTENER", "entity_type": "ASSET_REGIME", "layer": 3, "aliases": ["CURVE_BULL_FLATTENER"]},
    {"name": "AMMONIA_CASH_COST", "entity_type": "COMMODITY", "layer": 4, "aliases": ["NH3_PRICE", "HABER_BOSCH_COST"]},
    {"name": "SOYBEAN_CRUSH", "entity_type": "COMMODITY", "layer": 4, "aliases": ["CRUSH_SPREAD", "SOY_MEAL_OIL"]},
    {"name": "TERMS_OF_TRADE", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["EXPORT_IMPORT_RATIO"]},
    {"name": "CROSS_CURRENCY_BASIS", "entity_type": "MARKET_STRUCTURE", "layer": 3, "aliases": ["FX_SWAP_BASIS", "USD_FUNDING_PREMIUM"]},
    {"name": "CENTRAL_BANK_CREDIBILITY", "entity_type": "SENTIMENT", "layer": 3, "aliases": ["INFLATION_ANCHORING"]},
    {"name": "FISCAL_DEFICIT_DOMINANCE", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["TREASURY_SUPPLY_OVERHANG", "DEBT_ISSUANCE"]},
    {"name": "COMPOSITE_LEADING_INDICATOR", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["OECD_CLI", "LEADING_INDEX"]},
    {"name": "INFLATION_EXPECTATIONS", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["FIVE_YEAR_BREAKEVEN", "T10YIE", "INFLATION_SURVEY"]},
    {"name": "SOFR", "entity_type": "LIQUIDITY", "layer": 1, "aliases": ["SECURED_OVERNIGHT"]},
    {"name": "EFFR", "entity_type": "LIQUIDITY", "layer": 1, "aliases": ["EFFECTIVE_FED_FUNDS"]},
    {"name": "YIELD_CURVE", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": ["UST_YIELD_CURVE", "TREASURY_CURVE"]},
    {"name": "TREASURY_RATES", "entity_type": "YIELD_CURVE", "layer": 1, "aliases": ["UST_RATES", "SHORT_END_RATES"]},
    {"name": "TOTAL_FACTOR_PRODUCTIVITY", "entity_type": "MACRO_INDICATOR", "layer": 2, "aliases": ["TFP", "SOLow_RESIDUAL"]},
    {"name": "GLOBAL_INTEREST_RATES", "entity_type": "MONETARY_POLICY", "layer": 0, "aliases": ["GLOBAL_POLICY_RATE", "DM_POLICY_RATE"]},
    {"name": "CFTC_COT", "entity_type": "NOWCAST_PROXY", "layer": 3, "aliases": ["COT_REPORT", "COMMITMENTS_OF_TRADERS"]},
    {"name": "EQUITY_SCREENING", "entity_type": "EQUITY", "layer": 4, "aliases": ["STOCK_SCREENER", "EQUITY_FILTER"]},
    {"name": "SEC_INSTITUTIONAL", "entity_type": "MARKET_STRUCTURE", "layer": 3, "aliases": ["13F_FILINGS", "INSTITUTIONAL_OWNERSHIP"]},
    {"name": "SEC_LITIGATION", "entity_type": "EVENT_CLASS", "layer": 0, "aliases": ["SEC_RSS", "ENFORCEMENT_ACTION"]},
]


def _edge(
    source: str,
    source_type: str,
    target: str,
    target_type: str,
    relation: str,
    sign: str,
    *,
    confidence: float = 0.72,
    channel: str = "general",
    active_regimes: Optional[Tuple[str, ...]] = None,
    min_lag_seconds: float = 0.0,
    max_lag_seconds: float = 86400.0,
    decay_half_life_seconds: float = 43200.0,
    notes: str = "",
    contradiction_group: str = "",
) -> Dict[str, Any]:
    return {
        "source_name": source,
        "source_type": source_type,
        "target_name": target,
        "target_type": target_type,
        "relation": relation,
        "sign": sign,
        "confidence": confidence,
        "channel": channel,
        "active_regimes": list(active_regimes or ("ALL",)),
        "min_lag_seconds": min_lag_seconds,
        "max_lag_seconds": max_lag_seconds,
        "decay_half_life_seconds": decay_half_life_seconds,
        "notes": notes,
        "contradiction_group": contradiction_group,
        "execution_authority": False,
        "catalog_version": CATALOG_VERSION,
    }


# ---------------------------------------------------------------------------
# Causal edges — full intermarket graph (theory priors)
# ---------------------------------------------------------------------------

CAUSAL_EDGES: List[Dict[str, Any]] = [
    # --- Monetary transmission (contractionary / expansionary core) ---
    _edge("POLICY_RATE", "MONETARY_POLICY", "COMMERCIAL_BORROWING_COST", "CREDIT",
          "PASSES_THROUGH", "POSITIVE", channel="interest_rate",
          min_lag_seconds=0, max_lag_seconds=7 * 86400, decay_half_life_seconds=3 * 86400,
          notes="Policy rate pass-through to commercial borrowing costs"),
    _edge("POLICY_RATE", "MONETARY_POLICY", "SAVINGS_INCENTIVE", "CREDIT",
          "RAISES", "POSITIVE", channel="interest_rate",
          notes="Higher policy rates raise incentive to save"),
    _edge("COMMERCIAL_BORROWING_COST", "CREDIT", "AGGREGATE_DEMAND", "MACRO_INDICATOR",
          "SUPPRESSES", "NEGATIVE", channel="interest_rate",
          min_lag_seconds=7 * 86400, max_lag_seconds=180 * 86400, decay_half_life_seconds=60 * 86400,
          notes="Cost of capital reduces spending/investment"),
    _edge("AGGREGATE_DEMAND", "MACRO_INDICATOR", "INFLATION", "MACRO_INDICATOR",
          "PULLS", "POSITIVE", channel="demand",
          min_lag_seconds=30 * 86400, max_lag_seconds=365 * 86400, decay_half_life_seconds=90 * 86400,
          notes="Demand-pull inflation channel"),
    _edge("AGGREGATE_DEMAND", "MACRO_INDICATOR", "GROWTH", "MACRO_INDICATOR",
          "DRIVES", "POSITIVE", channel="demand", confidence=0.78,
          notes="Spending/investment supports growth"),
    _edge("QE_QT", "LIQUIDITY", "MONEY_SUPPLY", "LIQUIDITY",
          "EXPANDS", "POSITIVE", channel="liquidity",
          notes="QE expands base money/liquidity; QT contracts (sign on QT inverted via CONDITIONAL ops)"),
    _edge("MONEY_SUPPLY", "LIQUIDITY", "NOMINAL_BOND_YIELD", "YIELD_CURVE",
          "COMPRESSES", "NEGATIVE", channel="liquidity",
          active_regimes=("NORMAL", "RISK_ON", "EASY_MONEY"),
          notes="Liquidity usually compresses yields; breaks in fiscal-stress regimes"),
    _edge("POLICY_RATE", "MONETARY_POLICY", "DXY", "CURRENCY",
          "STRENGTHENS", "POSITIVE", channel="fx_carry",
          confidence=0.80, notes="Higher USD policy rate attracts capital → DXY up"),
    _edge("QE_QT", "LIQUIDITY", "DXY", "CURRENCY",
          "WEAKENS_VIA_QE", "NEGATIVE", channel="liquidity",
          notes="QE tends to weaken USD; QT strengthens (operator must tag QE vs QT)"),
    # --- Opportunity cost / gold ---
    _edge("POLICY_RATE", "MONETARY_POLICY", "OPPORTUNITY_COST_OF_GOLD", "CREDIT",
          "RAISES", "POSITIVE", channel="opportunity_cost",
          notes="Higher rates raise opportunity cost of non-yielding gold"),
    _edge("REAL_YIELD", "YIELD_CURVE", "OPPORTUNITY_COST_OF_GOLD", "CREDIT",
          "RAISES", "POSITIVE", channel="opportunity_cost", confidence=0.82,
          notes="Real yields are the cleanest gold opportunity-cost driver"),
    _edge("OPPORTUNITY_COST_OF_GOLD", "CREDIT", "GOLD", "COMMODITY",
          "SUPPRESSES", "NEGATIVE", channel="opportunity_cost",
          active_regimes=("NORMAL", "RISK_ON", "TIGHTENING", "EASY_MONEY"),
          contradiction_group="gold_dxy_safehaven",
          notes="Normal state: higher opportunity cost → lower gold demand/price"),
    _edge("DXY", "CURRENCY", "GOLD", "COMMODITY",
          "INVERSE_PRICES", "NEGATIVE", channel="fx_pricing",
          active_regimes=("NORMAL", "RISK_ON", "TIGHTENING", "EASY_MONEY", "STAGFLATION"),
          contradiction_group="gold_dxy_safehaven",
          confidence=0.78, notes="Normal inverse: strong USD → gold more expensive globally"),
    _edge("UNCERTAINTY", "SENTIMENT", "GOLD", "COMMODITY",
          "SAFE_HAVEN_BID", "POSITIVE", channel="safe_haven",
          active_regimes=("CRISIS_PANIC", "WAR", "BANKING_STRESS", "ALL"),
          contradiction_group="gold_dxy_safehaven",
          confidence=0.80, notes="Crisis overrides: gold bid even if DXY rises"),
    _edge("UNCERTAINTY", "SENTIMENT", "DXY", "CURRENCY",
          "SAFE_HAVEN_BID", "POSITIVE", channel="safe_haven",
          active_regimes=("CRISIS_PANIC", "WAR", "BANKING_STRESS"),
          contradiction_group="gold_dxy_safehaven",
          notes="USD also bid in global USD-funding crises"),
    _edge("SANCTIONS", "GEOPOLITICAL", "GOLD", "COMMODITY",
          "RESERVE_DIVERSIFICATION", "POSITIVE", channel="reserves",
          confidence=0.70, notes="Sanction/de-dollarization risk → CB gold buying"),
    # --- Oil / petrodollar / commodities ---
    _edge("DXY", "CURRENCY", "WTI_OIL", "COMMODITY",
          "INVERSE_PRICES", "NEGATIVE", channel="fx_pricing",
          active_regimes=("NORMAL", "RISK_ON", "EASY_MONEY", "TIGHTENING"),
          confidence=0.70, notes="Strong USD → oil expensive for non-USD buyers"),
    _edge("SUPPLY_SHOCK_ENERGY", "EVENT_CLASS", "WTI_OIL", "COMMODITY",
          "SUPPLY_SHOCK", "POSITIVE", channel="supply",
          min_lag_seconds=0, max_lag_seconds=7 * 86400, decay_half_life_seconds=2 * 86400,
          confidence=0.85, notes="Energy supply shock lifts oil immediately"),
    _edge("WTI_OIL", "COMMODITY", "INFLATION", "MACRO_INDICATOR",
          "COST_PUSH", "POSITIVE", channel="energy",
          min_lag_seconds=7 * 86400, max_lag_seconds=120 * 86400,
          notes="Oil cost-push into CPI/PCE"),
    _edge("WTI_OIL", "COMMODITY", "CAD", "CURRENCY",
          "SUPPORTS_EXPORTER", "POSITIVE", channel="terms_of_trade",
          notes="Oil ↑ supports CAD (exporter)"),
    _edge("GOLD", "COMMODITY", "AUD", "CURRENCY",
          "SUPPORTS_EXPORTER", "POSITIVE", channel="terms_of_trade", confidence=0.65,
          notes="Gold ↑ mildly supportive for AUD/ZAR exporters"),
    _edge("WTI_OIL", "COMMODITY", "DXY", "CURRENCY",
          "PETRODOLLAR_LEGACY", "CONDITIONAL", channel="petrodollar",
          active_regimes=("NORMAL",), confidence=0.55,
          notes="Legacy oil↑→USD outflow weakened; US net-exporter status made Oil-DXY unstable"),
    # --- Risk / equities / FX hierarchy ---
    _edge("UNCERTAINTY", "SENTIMENT", "VIX", "VOLATILITY",
          "SPURS_VOLATILITY", "POSITIVE", channel="volatility", confidence=0.88,
          notes="Uncertainty spikes implied vol"),
    _edge("VIX", "VOLATILITY", "RISK_SENTIMENT", "SENTIMENT",
          "DRIVES_RISK_OFF", "NEGATIVE", channel="volatility",
          notes="VIX ↑ → risk-off"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "EQUITIES", "EQUITY",
          "DRIVES_TREND", "POSITIVE", channel="risk",
          notes="Risk-on supports equities"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "JPY", "CURRENCY",
          "FUNDING_UNWIND", "NEGATIVE", channel="carry",
          active_regimes=("CRISIS_PANIC", "RISK_OFF"),
          notes="Risk-off → JPY bid via carry unwind (RISK_SENTIMENT↓ → JPY↑ encoded as NEGATIVE on risk)"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "CHF", "CURRENCY",
          "SAFE_HAVEN_BID", "NEGATIVE", channel="safe_haven",
          active_regimes=("CRISIS_PANIC", "RISK_OFF"),
          notes="Risk-off → CHF bid"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "AUD", "CURRENCY",
          "RISK_PROXY", "POSITIVE", channel="risk",
          notes="AUD/NZD as risk-on proxies"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "NZD", "CURRENCY",
          "RISK_PROXY", "POSITIVE", channel="risk", notes="NZD risk-on proxy"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "EM_FX", "CURRENCY",
          "CARRY_FLOW", "POSITIVE", channel="carry",
          notes="EM FX sold in risk-off / funded in risk-on"),
    _edge("DXY", "CURRENCY", "EM_FX", "CURRENCY",
          "PRESSURES", "NEGATIVE", channel="fx_carry",
          notes="Strong USD pressures EM FX"),
    _edge("VIX", "VOLATILITY", "CREDIT_SPREAD", "CREDIT",
          "WIDENS", "POSITIVE", channel="credit",
          notes="Vol spike widens credit spreads"),
    _edge("CREDIT_SPREAD", "CREDIT", "EQUITIES", "EQUITY",
          "PRESSURES", "NEGATIVE", channel="credit",
          notes="Wider spreads pressure risk assets"),
    # --- Yield curve / growth ---
    _edge("NOMINAL_BOND_YIELD", "YIELD_CURVE", "GROWTH", "MACRO_INDICATOR",
          "CONSTRAINS_VIA_COST", "NEGATIVE", channel="long_rates",
          active_regimes=("TIGHTENING", "FISCAL_STRESS"),
          confidence=0.68, notes="High long yields can crowd out / constrain growth"),
    _edge("NOMINAL_BOND_YIELD", "YIELD_CURVE", "GROWTH", "MACRO_INDICATOR",
          "REFLECTS_OPTIMISM", "POSITIVE", channel="long_rates",
          active_regimes=("RISK_ON", "RECOVERY"),
          contradiction_group="yield_growth_duality",
          confidence=0.65, notes="Yields can rise with growth optimism — context required"),
    _edge("YIELD_CURVE_SLOPE", "YIELD_CURVE", "GROWTH", "MACRO_INDICATOR",
          "PREDICTS", "POSITIVE", channel="curve",
          confidence=0.75, notes="Steeper curve associated with better growth outlook; inversion warns recession"),
    _edge("REAL_YIELD", "YIELD_CURVE", "EQUITIES", "EQUITY",
          "DISCOUNTS", "NEGATIVE", channel="valuation",
          notes="Higher real yields compress equity valuations"),
    _edge("TERM_PREMIUM", "YIELD_CURVE", "NOMINAL_BOND_YIELD", "YIELD_CURVE",
          "ADDS", "POSITIVE", channel="term_premium",
          notes="Term premium lifts long yields independent of expected short rates"),
    _edge("INFLATION", "MACRO_INDICATOR", "NOMINAL_BOND_YIELD", "YIELD_CURVE",
          "LIFTS_BREAKEVENS", "POSITIVE", channel="inflation",
          notes="Higher inflation expectations lift nominal yields"),
    _edge("POLICY_RATE", "MONETARY_POLICY", "NOMINAL_BOND_YIELD", "YIELD_CURVE",
          "ANCHORS_FRONT_END", "POSITIVE", channel="interest_rate",
          notes="Policy anchors short end; long end also via expectations"),
    # --- Events → markets ---
    _edge("CB_MEETING", "EVENT_CLASS", "VIX", "VOLATILITY",
          "SPURS_VOLATILITY", "POSITIVE", channel="event",
          min_lag_seconds=0, max_lag_seconds=86400, decay_half_life_seconds=21600,
          notes="CB meetings expand realized/implied vol around release"),
    _edge("CB_MEETING", "EVENT_CLASS", "DXY", "CURRENCY",
          "POLICY_SHOCK", "CONDITIONAL", channel="event",
          notes="Hawkish surprise → DXY↑; dovish → DXY↓"),
    _edge("CPI_RELEASE", "EVENT_CLASS", "POLICY_RATE", "MONETARY_POLICY",
          "DRIVES_POLICY", "POSITIVE", channel="event",
          notes="Hot CPI raises hike odds / delays cuts"),
    _edge("CPI_RELEASE", "EVENT_CLASS", "DXY", "CURRENCY",
          "DRIVES_CURRENCY_STRENGTH", "CONDITIONAL", channel="event",
          notes="Inflation surprise conditional on reaction function"),
    _edge("NFP_RELEASE", "EVENT_CLASS", "DXY", "CURRENCY",
          "DRIVES_CURRENCY_STRENGTH", "CONDITIONAL", channel="event",
          notes="Strong NFP typically USD supportive if it raises rate path"),
    _edge("WAR_SHOCK", "EVENT_CLASS", "WTI_OIL", "COMMODITY",
          "SUPPLY_SHOCK", "POSITIVE", channel="geopolitics",
          min_lag_seconds=0, max_lag_seconds=2 * 86400, confidence=0.82,
          notes="War / Hormuz risk → oil spike"),
    _edge("WAR_SHOCK", "EVENT_CLASS", "GOLD", "COMMODITY",
          "SAFE_HAVEN_BID", "POSITIVE", channel="geopolitics", confidence=0.80,
          notes="War → gold bid"),
    _edge("WAR_SHOCK", "EVENT_CLASS", "VIX", "VOLATILITY",
          "SPURS_VOLATILITY", "POSITIVE", channel="geopolitics", confidence=0.85,
          notes="War → vol spike"),
    _edge("WAR_SHOCK", "EVENT_CLASS", "EQUITIES", "EQUITY",
          "RISK_OFF_SELL", "NEGATIVE", channel="geopolitics",
          notes="War → equity sell (first order)"),
    _edge("TRADE_WAR", "EVENT_CLASS", "RISK_SENTIMENT", "SENTIMENT",
          "WEAKENS", "NEGATIVE", channel="geopolitics",
          notes="Tariff escalation → risk-off"),
    _edge("TRADE_WAR", "EVENT_CLASS", "EM_FX", "CURRENCY",
          "PRESSURES", "NEGATIVE", channel="geopolitics",
          confidence=0.70, notes="Tariff/trade-war shocks typically pressure EM FX"),
    _edge("TRADE_WAR", "EVENT_CLASS", "DXY", "CURRENCY",
          "DRIVES_CURRENCY_STRENGTH", "CONDITIONAL", channel="geopolitics",
          confidence=0.60, notes="USD often bids on trade-war risk; conditional on risk-off USD demand"),
    _edge("POLITICAL_SHOCK", "EVENT_CLASS", "DXY", "CURRENCY",
          "DRIVES_CURRENCY_STRENGTH", "CONDITIONAL", channel="narrative",
          confidence=0.55, notes="Macro/trade political shocks can bid USD; low prior until measured"),
    _edge("POLITICAL_SHOCK", "EVENT_CLASS", "UNCERTAINTY", "SENTIMENT",
          "SPURS_UNCERTAINTY", "POSITIVE", channel="narrative",
          confidence=0.72, notes="Policy tweets/election shocks raise uncertainty (hypothesis)"),
    _edge("POLITICAL_SHOCK", "EVENT_CLASS", "VIX", "VOLATILITY",
          "SPURS_VOLATILITY", "POSITIVE", channel="narrative",
          confidence=0.58, notes="Macro-relevant political shocks can lift vol; many tweets are noise"),
    _edge("CPI_RELEASE", "EVENT_CLASS", "INFLATION", "MACRO_INDICATOR",
          "UPDATES", "POSITIVE", channel="event",
          notes="CPI print updates inflation state (directional with surprise)"),
    _edge("NFP_RELEASE", "EVENT_CLASS", "EMPLOYMENT", "MACRO_INDICATOR",
          "UPDATES", "POSITIVE", channel="event",
          notes="NFP print updates employment state"),
    _edge("BANKING_STRESS", "EVENT_CLASS", "LIQUIDITY_STRESS", "MARKET_STRUCTURE",
          "TRIGGERS", "POSITIVE", channel="funding", confidence=0.85,
          notes="Bank stress → funding/liquidity stress"),
    _edge("BANKING_STRESS", "EVENT_CLASS", "CREDIT_SPREAD", "CREDIT",
          "WIDENS", "POSITIVE", channel="funding", confidence=0.80,
          notes="Banking stress widens credit spreads"),
    _edge("LIQUIDITY_STRESS", "MARKET_STRUCTURE", "GOLD", "COMMODITY",
          "SAFE_HAVEN_BID", "POSITIVE", channel="funding",
          active_regimes=("BANKING_STRESS", "CRISIS_PANIC"),
          notes="Funding stress can bid gold + USD together"),
    _edge("LIQUIDITY_STRESS", "MARKET_STRUCTURE", "CREDIT_SPREAD", "CREDIT",
          "WIDENS", "POSITIVE", channel="funding", notes="Funding stress widens spreads"),
    # --- Feedback to policy ---
    _edge("INFLATION", "MACRO_INDICATOR", "POLICY_RATE", "MONETARY_POLICY",
          "DRIVES_POLICY", "POSITIVE", channel="reaction_function",
          min_lag_seconds=0, max_lag_seconds=120 * 86400,
          notes="Reaction function: hot inflation → tighter policy"),
    _edge("EMPLOYMENT", "MACRO_INDICATOR", "POLICY_RATE", "MONETARY_POLICY",
          "DRIVES_POLICY", "POSITIVE", channel="reaction_function",
          notes="Strong labor → tighter or slower easing"),
    _edge("GROWTH", "MACRO_INDICATOR", "POLICY_RATE", "MONETARY_POLICY",
          "DRIVES_POLICY", "POSITIVE", channel="reaction_function",
          notes="Overheating growth → tighter policy"),
    _edge("VIX", "VOLATILITY", "POLICY_RATE", "MONETARY_POLICY",
          "FORCES_RESPONSE", "CONDITIONAL", channel="financial_stability",
          active_regimes=("CRISIS_PANIC", "BANKING_STRESS"),
          confidence=0.60, notes="Extreme stress can force emergency easing (conditional)"),
    # --- Crypto / cross-asset (weaker priors) ---
    _edge("MONEY_SUPPLY", "LIQUIDITY", "CRYPTO", "ASSET_REGIME",
          "LIQUIDITY_BETA", "POSITIVE", channel="liquidity", confidence=0.58,
          notes="Crypto often acts as high-beta liquidity asset"),
    _edge("RISK_SENTIMENT", "SENTIMENT", "CRYPTO", "ASSET_REGIME",
          "RISK_PROXY", "POSITIVE", channel="risk", confidence=0.62,
          notes="Crypto tracks risk-on/off with high beta"),
    # --- US Net Liquidity Plumbing (WALCL - TGA - RRP) ---
    _edge("US_NET_LIQUIDITY", "LIQUIDITY", "EQUITIES", "EQUITY",
          "DRIVES_TREND", "POSITIVE", channel="liquidity", confidence=0.82,
          notes="Net Liquidity (Fed Balance Sheet minus TGA minus RRP) drives equity multiple expansion"),
    _edge("US_NET_LIQUIDITY", "LIQUIDITY", "CRYPTO", "ASSET_REGIME",
          "LIQUIDITY_BETA", "POSITIVE", channel="liquidity", confidence=0.80,
          notes="Net Liquidity expansion strongly correlates with crypto risk rallies"),
    _edge("US_NET_LIQUIDITY", "LIQUIDITY", "DXY", "CURRENCY",
          "WEAKENS", "NEGATIVE", channel="liquidity", confidence=0.74,
          notes="Expanding dollar liquidity lowers scarcity value of USD"),
    # --- Yield Curve Morphology Regimes (Steepeners / Flatteners) ---
    _edge("POLICY_RATE", "MONETARY_POLICY", "BEAR_FLATTENER", "ASSET_REGIME",
          "DRIVES_TREND", "POSITIVE", channel="terms_structure", confidence=0.75,
          notes="Aggressive central bank rate hiking pushes short yields up faster than long yields"),
    _edge("GROWTH", "MACRO_INDICATOR", "BEAR_STEEPENER", "ASSET_REGIME",
          "DRIVES_TREND", "POSITIVE", channel="terms_structure", confidence=0.72,
          notes="Reflation / economic acceleration pushes long term rates higher via growth optimism"),
    _edge("BANKING_STRESS", "EVENT_CLASS", "BULL_STEEPENER", "ASSET_REGIME",
          "DRIVES_TREND", "POSITIVE", channel="terms_structure", confidence=0.78,
          notes="Emergency central bank cuts collapse front-end yields faster than long end"),
    _edge("UNCERTAINTY", "SENTIMENT", "BULL_FLATTENER", "ASSET_REGIME",
          "DRIVES_TREND", "POSITIVE", channel="terms_structure", confidence=0.70,
          notes="Flight-to-quality pushes long duration Treasury yields down aggressively"),
    # --- Synthetic Stoichiometry Conversion Spreads ---
    _edge("ENERGY_DEMAND", "NOWCAST_PROXY", "AMMONIA_CASH_COST", "COMMODITY",
          "COST_PUSH", "POSITIVE", channel="stoichiometry", confidence=0.85,
          notes="Natural gas price spikes transmit directly to Haber-Bosch anhydrous ammonia costs"),
    _edge("AMMONIA_CASH_COST", "COMMODITY", "CROP_HEALTH", "NOWCAST_PROXY",
          "SUPPRESSES", "NEGATIVE", channel="stoichiometry", confidence=0.76,
          notes="Prohibitive nitrogen fertilizer prices reduce crop application rates and yields"),
    _edge("WTI_OIL", "COMMODITY", "TERMS_OF_TRADE", "MACRO_INDICATOR",
          "SUPPORTS_EXPORTER", "POSITIVE", channel="terms_of_trade", confidence=0.80,
          notes="Higher crude prices boost terms of trade for net energy exporters"),
    _edge("TERMS_OF_TRADE", "MACRO_INDICATOR", "CAD", "CURRENCY",
          "STRENGTHENS", "POSITIVE", channel="terms_of_trade", confidence=0.78,
          notes="Improving terms of trade drives persistent commodity exporter currency appreciation"),
    # --- Fiscal Dominance & Funding Plumbing ---
    _edge("FISCAL_DEFICIT_DOMINANCE", "MACRO_INDICATOR", "TERM_PREMIUM", "YIELD_CURVE",
          "ADDS", "POSITIVE", channel="fiscal", confidence=0.78,
          notes="Heavy sovereign bond issuance supply overhang widens term premium"),
    _edge("FISCAL_DEFICIT_DOMINANCE", "MACRO_INDICATOR", "NOMINAL_BOND_YIELD", "YIELD_CURVE",
          "LIFTS_BREAKEVENS", "POSITIVE", channel="fiscal", confidence=0.75,
          notes="Sovereign debt supply pressure pushes benchmark long yields higher"),
    _edge("LIQUIDITY_STRESS", "MARKET_STRUCTURE", "CROSS_CURRENCY_BASIS", "MARKET_STRUCTURE",
          "WIDENS", "NEGATIVE", channel="plumbing", confidence=0.82,
          notes="Offshore dollar funding scramble drives negative cross-currency basis swap blowout"),
    _edge("INFLATION", "MACRO_INDICATOR", "CENTRAL_BANK_CREDIBILITY", "SENTIMENT",
          "WEAKENS", "NEGATIVE", channel="reaction_function", confidence=0.70,
          notes="Unanchored inflation overshoots erode central bank credibility"),
    _edge("CENTRAL_BANK_CREDIBILITY", "SENTIMENT", "GOLD", "COMMODITY",
          "SAFE_HAVEN_BID", "NEGATIVE", channel="safe_haven", confidence=0.75,
          notes="Loss of central bank credibility drives structural monetary gold accumulation"),
]


# ---------------------------------------------------------------------------
# Dynamic correlation regimes (from intermarket opportunity matrix)
# ---------------------------------------------------------------------------

CORRELATION_REGIMES: List[Dict[str, Any]] = [
    {
        "regime_id": "NORMAL_RISK_OFF",
        "label": "Normal risk-off (inverse gold-DXY holds)",
        "expected_moves": {"DXY": "UP", "GOLD": "DOWN", "WTI_OIL": "DOWN", "EQUITIES": "DOWN", "JPY": "UP"},
        "pair_signs": {
            "DXY|GOLD": "NEGATIVE",
            "DXY|WTI_OIL": "NEGATIVE",
            "GOLD|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["USD strength vs EM/AUD/NZD", "gold under pressure if real yields rising"],
        "execution_authority": False,
    },
    {
        "regime_id": "CRISIS_PANIC",
        "label": "Crisis panic (safe-haven correlation break)",
        "expected_moves": {"DXY": "UP", "GOLD": "UP", "EQUITIES": "DOWN", "JPY": "UP", "VIX": "UP"},
        "pair_signs": {
            "DXY|GOLD": "POSITIVE",
            "GOLD|EQUITIES": "NEGATIVE",
            "VIX|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": ["DXY|GOLD_INVERSE"],
        "advisory_bias": ["dual safe-haven: USD + gold + JPY", "do not fade gold solely on DXY↑"],
        "execution_authority": False,
    },
    {
        "regime_id": "STAGFLATION",
        "label": "Stagflation / cost-push",
        "expected_moves": {"DXY": "DOWN_OR_MIXED", "GOLD": "UP", "WTI_OIL": "UP", "EQUITIES": "DOWN", "REAL_YIELD": "DOWN"},
        "pair_signs": {
            "GOLD|WTI_OIL": "POSITIVE",
            "GOLD|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["long gold/oil vs bonds/equities (research only)", "real yields matter more than nominal"],
        "execution_authority": False,
    },
    {
        "regime_id": "EASY_MONEY",
        "label": "Expansionary / liquidity boom",
        "expected_moves": {"DXY": "DOWN", "GOLD": "UP", "WTI_OIL": "UP", "EQUITIES": "UP", "CRYPTO": "UP"},
        "pair_signs": {
            "DXY|GOLD": "NEGATIVE",
            "DXY|EQUITIES": "NEGATIVE",
            "GOLD|EQUITIES": "POSITIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["risk-on + weak USD supports commodities/crypto"],
        "execution_authority": False,
    },
    {
        "regime_id": "TIGHTENING",
        "label": "Policy tightening / QT",
        "expected_moves": {"DXY": "UP", "GOLD": "DOWN", "WTI_OIL": "DOWN", "EQUITIES": "DOWN", "NOMINAL_BOND_YIELD": "UP"},
        "pair_signs": {
            "DXY|GOLD": "NEGATIVE",
            "REAL_YIELD|GOLD": "NEGATIVE",
            "REAL_YIELD|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["opportunity cost dominates gold", "watch real yields and credit spreads"],
        "execution_authority": False,
    },
    {
        "regime_id": "SUPPLY_SHOCK",
        "label": "Energy / commodity supply shock",
        "expected_moves": {"WTI_OIL": "UP", "GOLD": "UP", "EQUITIES": "DOWN", "INFLATION": "UP"},
        "pair_signs": {
            "GOLD|WTI_OIL": "POSITIVE",
            "WTI_OIL|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["oil leads; inflation second-order; CB reaction lag"],
        "execution_authority": False,
    },
    {
        "regime_id": "RECOVERY",
        "label": "Post-crisis recovery / steepening",
        "expected_moves": {"YIELD_CURVE_SLOPE": "UP", "EQUITIES": "UP", "GOLD": "DOWN_THEN_MIXED", "DXY": "DOWN"},
        "pair_signs": {
            "YIELD_CURVE_SLOPE|EQUITIES": "POSITIVE",
            "DXY|EQUITIES": "NEGATIVE",
        },
        "broken_pairs": [],
        "advisory_bias": ["curve steepener + cyclical risk assets (research)"],
        "execution_authority": False,
    },
]


# ---------------------------------------------------------------------------
# Transmission channels (for retrieval grouping / UI)
# ---------------------------------------------------------------------------

TRANSMISSION_CHANNELS: List[Dict[str, Any]] = [
    {"id": "interest_rate", "label": "Interest-rate pass-through", "lag_class": "weeks_to_quarters"},
    {"id": "liquidity", "label": "QE/QT & money supply", "lag_class": "days_to_months"},
    {"id": "opportunity_cost", "label": "Real yield vs gold", "lag_class": "hours_to_weeks"},
    {"id": "fx_pricing", "label": "USD pricing of commodities", "lag_class": "minutes_to_days"},
    {"id": "fx_carry", "label": "Rate differentials & carry", "lag_class": "hours_to_weeks"},
    {"id": "safe_haven", "label": "Flight-to-safety", "lag_class": "seconds_to_days"},
    {"id": "supply", "label": "Physical supply shocks", "lag_class": "minutes_to_weeks"},
    {"id": "energy", "label": "Energy cost-push inflation", "lag_class": "weeks_to_quarters"},
    {"id": "terms_of_trade", "label": "Commodity exporter FX", "lag_class": "hours_to_weeks"},
    {"id": "petrodollar", "label": "Oil-USD historical link", "lag_class": "regime_dependent"},
    {"id": "volatility", "label": "Vol / correlation collapse", "lag_class": "seconds_to_days"},
    {"id": "risk", "label": "Risk-on / risk-off beta", "lag_class": "minutes_to_weeks"},
    {"id": "carry", "label": "Carry unwind", "lag_class": "minutes_to_days"},
    {"id": "credit", "label": "Credit spread contagion", "lag_class": "hours_to_weeks"},
    {"id": "long_rates", "label": "Long-term yields ↔ growth", "lag_class": "months"},
    {"id": "curve", "label": "Yield-curve slope signal", "lag_class": "months"},
    {"id": "valuation", "label": "Discount-rate valuation", "lag_class": "days_to_months"},
    {"id": "term_premium", "label": "Term premium", "lag_class": "weeks_to_months"},
    {"id": "inflation", "label": "Inflation expectations", "lag_class": "days_to_quarters"},
    {"id": "event", "label": "Scheduled macro event", "lag_class": "seconds_to_hours"},
    {"id": "geopolitics", "label": "Geopolitical shock", "lag_class": "seconds_to_days"},
    {"id": "narrative", "label": "Political / social narrative", "lag_class": "minutes_to_hours"},
    {"id": "funding", "label": "Funding / liquidity stress", "lag_class": "minutes_to_days"},
    {"id": "reaction_function", "label": "CB reaction function", "lag_class": "meetings"},
    {"id": "financial_stability", "label": "Crisis policy override", "lag_class": "days"},
    {"id": "reserves", "label": "CB reserve diversification", "lag_class": "months"},
    {"id": "demand", "label": "Aggregate demand", "lag_class": "quarters"},
    {"id": "stoichiometry", "label": "Synthetic physical commodity conversion spreads", "lag_class": "days_to_weeks"},
    {"id": "plumbing", "label": "Financial plumbing, repo & dollar basis", "lag_class": "minutes_to_days"},
    {"id": "fiscal", "label": "Fiscal dominance & debt issuance", "lag_class": "months_to_quarters"},
    {"id": "terms_structure", "label": "Yield curve steepening / flattening dynamics", "lag_class": "days_to_months"},
    {"id": "general", "label": "General / unclassified", "lag_class": "unknown"},
]


# ---------------------------------------------------------------------------
# Event playbook templates (advisory; used with event_policy windows)
# ---------------------------------------------------------------------------

EVENT_TEMPLATES: List[Dict[str, Any]] = [
    {
        "event_id": "cb_meeting",
        "entity": "CB_MEETING",
        "pre_minutes": 60,
        "post_minutes": 30,
        "watch": ["DXY", "NOMINAL_BOND_YIELD", "GOLD", "EQUITIES", "VIX"],
        "first_order": ["policy path repricing", "vol expansion"],
        "second_order": ["FX trend continuation", "equity sector rotation"],
        "execution_authority": False,
    },
    {
        "event_id": "cpi_release",
        "entity": "CPI_RELEASE",
        "pre_minutes": 30,
        "post_minutes": 15,
        "watch": ["DXY", "REAL_YIELD", "GOLD", "EQUITIES"],
        "first_order": ["rate-path odds", "real yield jump"],
        "second_order": ["gold opportunity-cost reset"],
        "execution_authority": False,
    },
    {
        "event_id": "nfp_release",
        "entity": "NFP_RELEASE",
        "pre_minutes": 30,
        "post_minutes": 15,
        "watch": ["DXY", "NOMINAL_BOND_YIELD", "EQUITIES"],
        "first_order": ["USD and front-end yields"],
        "second_order": ["risk sentiment if recession narrative flips"],
        "execution_authority": False,
    },
    {
        "event_id": "war_shock",
        "entity": "WAR_SHOCK",
        "pre_minutes": 0,
        "post_minutes": 1440,
        "watch": ["WTI_OIL", "GOLD", "DXY", "VIX", "EQUITIES"],
        "first_order": ["oil + gold + VIX", "equities down"],
        "second_order": ["Hormuz open/closed branch", "SPR/OPEC response"],
        "key_questions": ["Is Hormuz open?", "Contained or escalating?"],
        "execution_authority": False,
    },
    {
        "event_id": "banking_stress",
        "entity": "BANKING_STRESS",
        "pre_minutes": 0,
        "post_minutes": 2880,
        "watch": ["CREDIT_SPREAD", "GOLD", "DXY", "EQUITIES", "LIQUIDITY_STRESS"],
        "first_order": ["funding stress", "safe-haven bid"],
        "second_order": ["policy backstop odds"],
        "execution_authority": False,
    },
    {
        "event_id": "trade_war",
        "entity": "TRADE_WAR",
        "pre_minutes": 0,
        "post_minutes": 1440,
        "watch": ["EM_FX", "DXY", "EQUITIES", "RISK_SENTIMENT", "VIX"],
        "first_order": ["risk-off bid in USD", "EM FX pressure"],
        "second_order": ["sector rotation", "supply-chain narrative"],
        "execution_authority": False,
    },
    {
        "event_id": "political_shock",
        "entity": "POLITICAL_SHOCK",
        "pre_minutes": 0,
        "post_minutes": 360,
        "watch": ["DXY", "VIX", "UNCERTAINTY", "GOLD", "EQUITIES"],
        "first_order": ["uncertainty spike", "conditional USD bid"],
        "second_order": ["fade noise tweets; wait for confirmation"],
        "key_questions": ["Is the shock macro/trade relevant?", "Confirmed by primary release/calendar?"],
        "execution_authority": False,
    },
]


# ---------------------------------------------------------------------------
# Free-data bindings (ideal stack from the document → project providers)
# ---------------------------------------------------------------------------

FREE_DATA_BINDINGS: List[Dict[str, Any]] = [
    {"entity": "POLICY_RATE", "provider": "FRED", "series": ["FEDFUNDS", "DFEDTARU"], "module": "openbb_data_bridge"},
    {"entity": "NOMINAL_BOND_YIELD", "provider": "FRED", "series": ["DGS10", "DGS2", "DGS30"], "module": "openbb_data_bridge"},
    {"entity": "YIELD_CURVE_SLOPE", "provider": "FRED", "series": ["T10Y2Y", "T10Y3M"], "module": "openbb_data_bridge"},
    {"entity": "REAL_YIELD", "provider": "FRED", "series": ["DFII10"], "module": "openbb_data_bridge"},
    {"entity": "INFLATION", "provider": "FRED", "series": ["CPIAUCSL", "PCEPILFE"], "module": "openbb_data_bridge"},
    {"entity": "EMPLOYMENT", "provider": "FRED", "series": ["UNRATE", "PAYEMS"], "module": "openbb_data_bridge"},
    {"entity": "GROWTH", "provider": "FRED", "series": ["INDPRO", "GDPC1"], "module": "openbb_data_bridge"},
    {"entity": "VIX", "provider": "FRED", "series": ["VIXCLS"], "module": "openbb_data_bridge"},
    {"entity": "CREDIT_SPREAD", "provider": "FRED", "series": ["BAA10Y"], "module": "openbb_data_bridge"},
    {"entity": "DXY", "provider": "MARKET", "symbols": ["DX-Y.NYB", "UUP"], "module": "yahoo_finance_provider"},
    {"entity": "GOLD", "provider": "MARKET", "symbols": ["XAUUSD", "GC=F", "GLD"], "module": "mt5_trader|yahoo_finance_provider"},
    {"entity": "WTI_OIL", "provider": "MARKET", "symbols": ["USOIL", "CL=F", "USO"], "module": "mt5_trader|yahoo_finance_provider"},
    {"entity": "EQUITIES", "provider": "MARKET", "symbols": ["SPY", "ES=F"], "module": "yahoo_finance_provider"},
    {"entity": "CB_MEETING", "provider": "CALENDAR", "symbols": [], "module": "mt5_calendar_wrapper|event_policy"},
    {"entity": "MONEY_SUPPLY", "provider": "OPENBB", "router": "economy.money_measures", "fields": ["m1", "m2", "currency", "demand_deposits"], "module": "openbb_data_bridge"},
    {"entity": "US_NET_LIQUIDITY", "provider": "OPENBB", "router": "economy.central_bank_holdings", "fields": ["par_value", "description"], "module": "openbb_data_bridge"},
    {"entity": "QE_QT", "provider": "OPENBB", "router": "economy.central_bank_holdings", "fields": ["par_value", "change"], "module": "openbb_data_bridge"},
    {"entity": "SHIPPING_FLOW", "provider": "OPENBB", "routers": ["economy.shipping.chokepoint_volume", "economy.shipping.port_volume"], "fields": ["vessels_total", "vessels_cargo", "vessels_tanker"], "module": "openbb_data_bridge"},
    {"entity": "FREIGHT_VOLUME", "provider": "OPENBB", "router": "economy.shipping.port_volume", "fields": ["date", "port_code", "country"], "module": "openbb_data_bridge"},
    {"entity": "FUTURES_POSITIONING", "provider": "OPENBB", "router": "cftc.cot_search", "fields": ["code", "name", "category", "subcategory"], "module": "openbb_data_bridge"},
    {"entity": "HOUSING_ACTIVITY", "provider": "OPENBB", "router": "economy.house_price_index", "fields": ["date", "country", "value"], "module": "openbb_data_bridge"},
    {"entity": "COMPOSITE_LEADING_INDICATOR", "provider": "OPENBB", "router": "economy.composite_leading_indicator", "fields": ["date", "value", "country"], "module": "openbb_data_bridge"},
    {"entity": "POLICY_SENTIMENT", "provider": "OPENBB", "router": "economy.fomc_documents", "fields": ["date", "doc_type", "url"], "module": "openbb_data_bridge"},
    {"entity": "INFLATION_EXPECTATIONS", "provider": "OPENBB", "router": "economy.survey.inflation_expectations", "fields": ["date", "infpgdp1yr", "infcpi1yr", "infcpi10yr"], "module": "openbb_data_bridge"},
    {"entity": "SOFR", "provider": "OPENBB", "router": "fixedincome.rate.sofr", "fields": ["date", "rate"], "module": "openbb_data_bridge"},
    {"entity": "EFFR", "provider": "OPENBB", "router": "fixedincome.rate.effr", "fields": ["date", "rate", "target_range_upper", "target_range_lower"], "module": "openbb_data_bridge"},
    {"entity": "YIELD_CURVE", "provider": "OPENBB", "router": "fixedincome.government.yield_curve", "fields": ["date", "maturity", "rate", "maturity_years"], "module": "openbb_data_bridge"},
    {"entity": "TREASURY_RATES", "provider": "OPENBB", "router": "fixedincome.government.treasury_rates", "fields": ["date", "week_4", "month_1", "month_3", "month_6", "year_1", "year_10"], "module": "openbb_data_bridge"},
    {"entity": "TOTAL_FACTOR_PRODUCTIVITY", "provider": "OPENBB", "router": "economy.total_factor_productivity", "fields": ["date", "d_y_prod", "d_y_inc", "d_y", "d_hours"], "module": "openbb_data_bridge"},
    {"entity": "GLOBAL_INTEREST_RATES", "provider": "OPENBB", "router": "economy.interest_rates", "fields": ["date", "value", "country"], "module": "openbb_data_bridge"},
    {"entity": "CFTC_COT", "provider": "OPENBB", "router": "cftc.cot_search", "fields": ["code", "name", "category", "subcategory", "units"], "module": "openbb_data_bridge"},
    {"entity": "ENERGY_DEMAND", "provider": "OPENBB", "router": "commodity.petroleum_status_report", "fields": ["date", "table", "title"], "module": "openbb_data_bridge"},
    {"entity": "EQUITY_SCREENING", "provider": "OPENBB", "router": "equity.screener", "fields": ["symbol", "name", "price", "change", "percent_change"], "module": "openbb_data_bridge"},
    {"entity": "SEC_INSTITUTIONAL", "provider": "OPENBB", "router": "regulators.sec.institutions_search", "fields": ["name", "cik"], "module": "openbb_data_bridge"},
    {"entity": "SEC_LITIGATION", "provider": "OPENBB", "router": "regulators.sec.rss_litigation", "fields": ["published", "title", "summary", "link"], "module": "openbb_data_bridge"},
]

try:
    from data.alternative_data_registry import ALTERNATIVE_DATA_BINDINGS, NOWCAST_MODELING_PRINCIPLES
except ImportError:
    try:
        from alternative_data_registry import ALTERNATIVE_DATA_BINDINGS, NOWCAST_MODELING_PRINCIPLES
    except ImportError:
        ALTERNATIVE_DATA_BINDINGS = []  # type: ignore[assignment,no-redef]
        NOWCAST_MODELING_PRINCIPLES = ()  # type: ignore[assignment,no-redef]


# ---------------------------------------------------------------------------
# Decade shock anchors (2016–2026) for historical_regimes extension
# ---------------------------------------------------------------------------

DECADE_SHOCK_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "brexit_2016": {
        "label": "Brexit referendum FX shock",
        "date_window": {"start": "2016-06-20", "end": "2016-07-15"},
        "source_url": "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2016/mpc-august-2016",
        "publisher": "Bank of England",
        "research_questions": [
            "How did GBP and safe-haven assets gap around the vote?",
            "Which event windows mattered for FX vs equities?",
        ],
        "required_fields": ["observed_at", "event_time", "instrument", "volatility", "source_span"],
        "primary_entities": ["GBP", "GOLD", "VIX", "EQUITIES"],
    },
    "trump_election_2016": {
        "label": "US 2016 election surprise and reflation trade",
        "date_window": {"start": "2016-11-01", "end": "2016-12-15"},
        "source_url": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20161214.htm",
        "publisher": "Federal Reserve Board",
        "research_questions": [
            "How fast did yields and USD/MXN reprice after the overnight futures crash?",
            "Did financials outperform as a regime, not a headline?",
        ],
        "required_fields": ["observed_at", "event_time", "instrument", "yield_proxy", "source_span"],
        "primary_entities": ["DXY", "NOMINAL_BOND_YIELD", "EM_FX", "EQUITIES", "GOLD"],
    },
    "trade_war_2018": {
        "label": "US-China trade war escalation",
        "date_window": {"start": "2018-03-01", "end": "2018-12-31"},
        "source_url": "https://home.treasury.gov/",
        "publisher": "U.S. Department of the Treasury",
        "research_questions": [
            "Did tariff headlines create repeatable risk-off FX/commodity patterns?",
            "How sticky were correlation breaks vs mean reversion?",
        ],
        "required_fields": ["observed_at", "event_time", "instrument", "volatility", "source_span"],
        "primary_entities": ["EM_FX", "EQUITIES", "DXY", "RISK_SENTIMENT"],
    },
    "repo_stress_2019": {
        "label": "September 2019 US repo stress",
        "date_window": {"start": "2019-09-01", "end": "2019-10-31"},
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20191011a.htm",
        "publisher": "Federal Reserve Board",
        "research_questions": [
            "Did funding stress lead equity/credit stress or remain contained?",
            "What liquidity proxies were point-in-time available?",
        ],
        "required_fields": ["observed_at", "liquidity_proxy", "instrument", "source_span"],
        "primary_entities": ["LIQUIDITY_STRESS", "CREDIT_SPREAD", "DXY"],
    },
    "covid_crash_2020": {
        "label": "COVID crash and emergency easing",
        "date_window": {"start": "2020-02-15", "end": "2020-04-30"},
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323b.htm",
        "publisher": "Federal Reserve Board",
        "research_questions": [
            "When did Gold-DXY inverse break into dual safe-haven?",
            "How did oil storage/physical constraints interact with paper prices?",
        ],
        "required_fields": ["observed_at", "event_time", "volatility", "spread", "instrument", "source_span"],
        "primary_entities": ["VIX", "GOLD", "DXY", "WTI_OIL", "EQUITIES"],
    },
    "oil_negative_2020": {
        "label": "WTI negative price / storage shock",
        "date_window": {"start": "2020-04-15", "end": "2020-04-30"},
        "source_url": "https://www.eia.gov/",
        "publisher": "U.S. Energy Information Administration",
        "research_questions": [
            "Which instruments were safe vs futures-expiry traps?",
            "Did calendar spreads dominate directional oil?",
        ],
        "required_fields": ["observed_at", "instrument", "storage_proxy", "source_span"],
        "primary_entities": ["WTI_OIL"],
    },
    "inflation_tightening_2022": {
        "label": "2022 inflation shock and hiking cycle",
        "date_window": {"start": "2022-01-01", "end": "2022-12-31"},
        "source_url": "https://www.federalreserve.gov/monetarypolicy/2022-06-mpr-summary.htm",
        "publisher": "Federal Reserve Board",
        "research_questions": [
            "Did real yields dominate gold more than DXY?",
            "Which FX pairs tracked policy differentials cleanly?",
        ],
        "required_fields": ["observed_at", "inflation_surprise", "policy_rate", "volatility", "instrument", "source_span"],
        "primary_entities": ["INFLATION", "POLICY_RATE", "REAL_YIELD", "DXY", "GOLD"],
    },
    "banking_stress_2023": {
        "label": "2023 regional bank stress",
        "date_window": {"start": "2023-03-01", "end": "2023-05-31"},
        "source_url": "https://www.fdic.gov/resources/resolutions/bank-failures/failed-bank-list/silicon-valley.html",
        "publisher": "Federal Deposit Insurance Corporation",
        "research_questions": [
            "Did gold and USD rise together under funding stress?",
            "Which credit-spread thresholds mattered for FX?",
        ],
        "required_fields": ["observed_at", "event_time", "liquidity_proxy", "spread", "instrument", "source_span"],
        "primary_entities": ["BANKING_STRESS", "GOLD", "DXY", "CREDIT_SPREAD"],
    },
}


# ---------------------------------------------------------------------------
# Integration complexity tiers (efficiency plan)
# ---------------------------------------------------------------------------

COMPLEXITY_TIERS: Dict[str, Dict[str, Any]] = {
    "T0_CORE": {
        "description": "Always-on: seed catalog edges + event blackout + approved macro context",
        "modules": ["economic_dynamics_catalog", "economic_dynamics_seed", "macro_graph", "event_policy"],
        "cost": "low",
    },
    "T1_REGIME": {
        "description": "Classify live correlation regime; gate CONDITIONAL edges",
        "modules": ["intermarket_regime", "correlation_risk", "lag_delay_engine"],
        "cost": "medium",
    },
    "T2_VALIDATE": {
        "description": "Autonomous statistical validation + lifecycle promotion",
        "modules": ["autonomous_causal_engine", "edge_lifecycle", "broker_replay"],
        "cost": "medium-high",
    },
    "T3_OPPORTUNITY": {
        "description": "Advisory opportunity candidates from approved edges + setups",
        "modules": ["causal_setup_map", "opportunity_bus", "scanner"],
        "cost": "medium",
    },
    "T4_LEARN": {
        "description": "Outcome feedback, feature builder, research sprints — no execution authority",
        "modules": ["reward_ledger", "feature_builder", "adaptive_research_service"],
        "cost": "high",
    },
}


def entity_index() -> Dict[str, Dict[str, Any]]:
    return {e["name"]: e for e in ENTITIES}


def edges_for_regime(regime_id: str) -> List[Dict[str, Any]]:
    key = str(regime_id or "").upper()
    out: List[Dict[str, Any]] = []
    for edge in CAUSAL_EDGES:
        regimes = {str(r).upper() for r in edge.get("active_regimes") or []}
        if "ALL" in regimes or key in regimes:
            out.append(edge)
    return out


def edges_by_channel(channel: str) -> List[Dict[str, Any]]:
    channel_key = str(channel or "").lower()
    return [e for e in CAUSAL_EDGES if str(e.get("channel") or "").lower() == channel_key]


def catalog_summary() -> Dict[str, Any]:
    return {
        "catalog_version": CATALOG_VERSION,
        "source_document": SOURCE_DOCUMENT,
        "entity_count": len(ENTITIES),
        "edge_count": len(CAUSAL_EDGES),
        "regime_count": len(CORRELATION_REGIMES),
        "channel_count": len(TRANSMISSION_CHANNELS),
        "event_template_count": len(EVENT_TEMPLATES),
        "free_data_bindings": len(FREE_DATA_BINDINGS),
        "alternative_data_bindings": len(ALTERNATIVE_DATA_BINDINGS),
        "decade_shock_templates": len(DECADE_SHOCK_TEMPLATES),
        "execution_authority": EXECUTION_AUTHORITY,
        "complexity_tiers": list(COMPLEXITY_TIERS.keys()),
        "tradable_setup_gaps": len(tradable_setup_gaps()),
    }


# Critical doc chains that must remain in the machine SoT (fail tests if missing).
CRITICAL_DOC_CHAINS: Tuple[Tuple[str, str], ...] = (
    ("POLICY_RATE", "DXY"),
    ("POLICY_RATE", "COMMERCIAL_BORROWING_COST"),
    ("REAL_YIELD", "OPPORTUNITY_COST_OF_GOLD"),
    ("OPPORTUNITY_COST_OF_GOLD", "GOLD"),
    ("DXY", "GOLD"),
    ("WAR_SHOCK", "WTI_OIL"),
    ("VIX", "RISK_SENTIMENT"),
    ("RISK_SENTIMENT", "EQUITIES"),
    ("WTI_OIL", "INFLATION"),
    ("TRADE_WAR", "EM_FX"),
    ("POLITICAL_SHOCK", "UNCERTAINTY"),
    ("CPI_RELEASE", "INFLATION"),
    ("NFP_RELEASE", "EMPLOYMENT"),
    ("BANKING_STRESS", "CREDIT_SPREAD"),
)


def coverage_report() -> Dict[str, Any]:
    """Operator-facing coverage of free-data bindings and critical doc chains."""
    names = {e["name"] for e in ENTITIES}
    pairs = {(e["source_name"], e["target_name"]) for e in CAUSAL_EDGES}
    missing_bindings = [
        str(b.get("entity"))
        for b in FREE_DATA_BINDINGS
        if str(b.get("entity") or "") not in names
    ]
    missing_alt_bindings = [
        str(b.get("entity"))
        for b in ALTERNATIVE_DATA_BINDINGS
        if str(b.get("entity") or "") not in names
    ]
    missing_bindings = missing_bindings + missing_alt_bindings
    missing_chains = [
        {"source": src, "target": tgt}
        for src, tgt in CRITICAL_DOC_CHAINS
        if (src, tgt) not in pairs
    ]
    template_entities = {str(t.get("entity")) for t in EVENT_TEMPLATES}
    return {
        "status": "OK" if not missing_bindings and not missing_chains else "GAPS",
        "missing_binding_entities": missing_bindings,
        "missing_critical_chains": missing_chains,
        "event_template_entities": sorted(template_entities),
        "entity_count": len(names),
        "edge_count": len(CAUSAL_EDGES),
        "execution_authority": False,
        "limitation": (
            "Coverage checks machine SoT against critical doc chains only; "
            "the prose dump is not executable truth."
        ),
    }


def resolve_entity_alias(name: str) -> Optional[str]:
    """Map raw extracted names/aliases onto catalog entity names."""
    key = str(name or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not key:
        return None
    index = entity_index()
    if key in index:
        return key
    for ent in ENTITIES:
        aliases = {str(a).upper().replace(" ", "_") for a in (ent.get("aliases") or [])}
        if key in aliases:
            return str(ent["name"])
    return None


# Target types that can become advisory trade setups (not pure transmission nodes).
TRADABLE_TARGET_TYPES = frozenset({
    "CURRENCY", "COMMODITY", "EQUITY", "VOLATILITY", "ASSET_REGIME", "MARKET_STRUCTURE",
})


def tradable_setup_gaps() -> List[Tuple[str, str]]:
    """Return (relation, target_type) pairs for tradable edges lacking setup maps."""
    try:
        from causal_setup_map import resolve_causal_setup
    except ImportError:
        try:
            from intelligence.causal_setup_map import resolve_causal_setup
        except ImportError:
            return []
    index = entity_index()
    gaps: List[Tuple[str, str]] = []
    seen = set()
    for edge in CAUSAL_EDGES:
        target_type = index[edge["target_name"]]["entity_type"]
        if target_type not in TRADABLE_TARGET_TYPES:
            continue
        key = (str(edge["relation"]).upper(), str(target_type).upper())
        if key in seen:
            continue
        seen.add(key)
        mapped = resolve_causal_setup(
            relation=edge["relation"],
            source_type=index[edge["source_name"]]["entity_type"],
            target_type=target_type,
            source_name=edge["source_name"],
            target_name=edge["target_name"],
        )
        if not mapped.get("eligible"):
            gaps.append(key)
    return gaps


def validate_catalog() -> List[str]:
    """Return list of integrity problems (empty = OK)."""
    problems: List[str] = []
    names = {e["name"] for e in ENTITIES}
    types = set(ENTITY_TYPES)
    for ent in ENTITIES:
        if ent.get("entity_type") not in types:
            problems.append(f"entity {ent.get('name')} has unknown type {ent.get('entity_type')}")
    regime_ids = {r["regime_id"] for r in CORRELATION_REGIMES} | {"ALL", "NORMAL", "RISK_ON", "RISK_OFF", "WAR", "BANKING_STRESS", "FISCAL_STRESS"}
    for i, edge in enumerate(CAUSAL_EDGES):
        if edge["source_name"] not in names:
            problems.append(f"edge[{i}] unknown source {edge['source_name']}")
        if edge["target_name"] not in names:
            problems.append(f"edge[{i}] unknown target {edge['target_name']}")
        if edge["sign"] not in {"POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN"}:
            problems.append(f"edge[{i}] bad sign {edge['sign']}")
        conf = float(edge["confidence"])
        if not 0.0 <= conf <= 1.0:
            problems.append(f"edge[{i}] bad confidence")
        if edge.get("execution_authority") is not False:
            problems.append(f"edge[{i}] must lock execution_authority=False")
        for reg in edge.get("active_regimes") or []:
            if str(reg).upper() not in {x.upper() for x in regime_ids} and str(reg).upper() not in {
                "NORMAL", "CRISIS_PANIC", "STAGFLATION", "EASY_MONEY", "TIGHTENING",
                "SUPPLY_SHOCK", "RECOVERY", "ALL", "RISK_ON", "RISK_OFF", "WAR",
                "BANKING_STRESS", "FISCAL_STRESS",
            }:
                problems.append(f"edge[{i}] unknown regime {reg}")
    channels = {c["id"] for c in TRANSMISSION_CHANNELS}
    for i, edge in enumerate(CAUSAL_EDGES):
        if edge.get("channel") not in channels:
            problems.append(f"edge[{i}] unknown channel {edge.get('channel')}")
    for relation, target_type in tradable_setup_gaps():
        problems.append(f"tradable edge lacks setup map: {relation}->{target_type}")
    return problems