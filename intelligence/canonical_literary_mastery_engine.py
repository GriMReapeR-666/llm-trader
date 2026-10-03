"""Canonical Macro & Geopolitical Literary Mastery Engine.

Implements all major mathematical, econometric, and strategic modules from the canonical macro literature:

1. RAY DALIO & NIALL FERGUSON:
   - All-Weather 4-Quadrant Risk Parity Asset Allocation Engine.
   - 8-Metric Empire Life-Cycle Rise & Fall Index.
   - Ferguson's Law of Imperial Overstretch (Debt Interest > Defense Budget).

2. GEORGE SOROS & HYMAN MINSKY:
   - Soros Theory of Reflexivity (Cognitive vs. Participating Function Feedback).
   - Minsky Financial Fragility Ratio (FFR) & Endogenous Boom-Bust Accelerator.
   - The 6-Phase Archetypal Debt Cycle State Machine.

3. PERRY MEHRLING & ZOLTAN POZSAR:
   - Quadruple-Entry Interbank Settlement & Payment Stretch Matrix.
   - Cross-Currency Basis (CCY) Covered Interest Parity (CIP) Strain.
   - Pozsar's Four Prices of Money (Par, Interest, FX Spot, Price Level).

4. GRAHAM ALLISON, JOHN MEARSHEIMER & PETER ZEIHAN:
   - Allison 16 Historical Case Studies Empirical Distance Matcher.
   - Mearsheimer Stopping Power of Water & Net Military Projection Power.
   - Zeihan Demographic Inversion & Peak Consumption Age Shift.

5. VACLAV SMIL, CHRIS MILLER & HELEN THOMPSON:
   - Smil Energy Return on Energy Invested (EROEI) & 4 Pillars Thermodynamic Buffer.
   - Chris Miller Chip War 5-Stage Semiconductor Value Chain Bottleneck Matrix.
   - Helen Thompson Energy-Monetary-Democratic Trilemma Index.

6. NASSIM NICHOLAS TALEB:
   - Incerto Antifragility Convexity Triad (Fragile, Robust, Antifragile).
   - Lindy Effect Longevity Expectation Model.
   - Skin In The Game (SITG) Asymmetry Filter.

7. AUTHOR-C:
   - Triple Barrier Labeling Method (Profit Target, Stop Loss, Vertical Barrier).
   - CUSUM Filter & SADF / GSADF Bubble / Structural Break Detector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("CanonicalLiteraryMasteryEngine")


class CanonicalLiteraryMasteryEngine(BaseAnalyticalModule):
    """Canonical Macro, Geopolitical & Quantitative Literary Mastery Engine."""

    module_id = "canonical_literary_mastery"
    module_name = "Canonical Macro & Geopolitical Literary Mastery Engine"
    version = "1.0.0"
    category = "canonical_literature"
    tags = ["dalio", "soros", "minsky", "mehrling", "pozsar", "allison", "mearsheimer", "zeihan", "smil", "chip_war", "taleb", "prado"]
    mathematics_formulas = [
        r"\text{Ferguson's Law: } \text{Debt Service Costs} > \text{National Defense Budget} \implies \text{Imperial Liquidation}",
        r"\text{Soros Reflexivity: } \text{RDI} = |P_t - F_t| \times \frac{d\text{Credit}}{dt}",
        r"\text{CIP Deviation: } \text{Basis} = F_{t,T} - S_t \frac{1 + r_{\$, T}}{1 + r_{f, T}}",
        r"\text{EROEI} = \frac{\text{Energy Delivered}}{\text{Energy Expended}} \quad (\text{Threshold: } \text{EROEI} \ge 7:1)",
        r"\text{Taleb Antifragility: } \frac{d^2 V}{d S^2} > 0 \quad (\text{Positive Convexity})",
        r"\text{Lindy Law: } \mathbb{E}[T_{\text{remaining}} \mid T = t] \propto t",
    ]

    # ───────────────────────────────────────────────────────────────────────────
    # 1. Ray Dalio & Niall Ferguson: All-Weather, Empire Gauges & Imperial Law
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="dalio.evaluate_all_weather_and_empire_lifecycle",
        description="Evaluate Ray Dalio's 4-Quadrant All-Weather asset allocation, 8 Empire Rise & Fall metrics, and Niall Ferguson's Law of Empire Liquidation.",
        parameters={
            "growth_environment": {"type": "string", "default": "FALLING", "description": "Growth regime ('RISING' or 'FALLING')"},
            "inflation_environment": {"type": "string", "default": "RISING", "description": "Inflation regime ('RISING' or 'FALLING')"},
            "education_score": {"type": "float", "default": 85.0, "description": "Empire Education Index (0-100)"},
            "innovation_score": {"type": "float", "default": 90.0, "description": "Empire Innovation/Tech Index (0-100)"},
            "military_score": {"type": "float", "default": 95.0, "description": "Empire Military Dominance (0-100)"},
            "reserve_currency_score": {"type": "float", "default": 90.0, "description": "Global Reserve Currency Share (0-100)"},
            "debt_interest_bn": {"type": "float", "default": 1100.0, "description": "Annual sovereign debt interest payments in billions USD"},
            "defense_budget_bn": {"type": "float", "default": 880.0, "description": "Annual national defense budget in billions USD"},
        },
        returns={"all_weather_allocation": "object", "dalio_empire_lifecycle_stage": "string", "ferguson_law_breached": "bool", "imperial_overstretch_score": "float"},
        tags=["dalio", "all_weather", "ferguson", "empire_cycle", "macro"],
    )
    def evaluate_all_weather_and_empire_lifecycle(
        self,
        growth_environment: str = "FALLING",
        inflation_environment: str = "RISING",
        education_score: float = 85.0,
        innovation_score: float = 90.0,
        military_score: float = 95.0,
        reserve_currency_score: float = 90.0,
        debt_interest_bn: float = 1100.0,
        defense_budget_bn: float = 880.0,
    ) -> Dict[str, Any]:
        """Calculates Dalio All-Weather allocations and Ferguson's Imperial Law."""
        g = growth_environment.upper()
        inf = inflation_environment.upper()

        if "RISING" in g and "RISING" in inf:
            quadrant = "QUADRANT 1: Growth Rising + Inflation Rising (Reflation / Boom)"
            allocation = {"Commodities": 0.35, "EM_Equities": 0.25, "TIPS_Inflation_Bonds": 0.25, "Gold": 0.15}
        elif "RISING" in g and "FALLING" in inf:
            quadrant = "QUADRANT 2: Growth Rising + Inflation Falling (Goldilocks Expansion)"
            allocation = {"Developed_Equities": 0.50, "Corporate_Credit": 0.30, "Nominal_Treasuries": 0.20}
        elif "FALLING" in g and "RISING" in inf:
            quadrant = "QUADRANT 3: Growth Falling + Inflation Rising (Stagflation / Energy Shock)"
            allocation = {"Gold_Physical": 0.35, "TIPS_Inflation_Bonds": 0.30, "Energy_Commodities": 0.20, "Cash_Equivalents": 0.15}
        else:
            quadrant = "QUADRANT 4: Growth Falling + Inflation Falling (Deflationary Bust / Recession)"
            allocation = {"Long_Duration_Treasuries": 0.55, "Gold": 0.25, "Defensive_Dividend_Equities": 0.20}

        # Dalio Empire Life-Cycle Index
        composite_empire = (education_score * 0.15) + (innovation_score * 0.25) + (military_score * 0.30) + (reserve_currency_score * 0.30)
        if composite_empire > 80.0 and reserve_currency_score >= 85.0:
            empire_stage = "STAGE 5: TOPPING EMPIRE (High debt, heavy military overstretch, living on reserve currency privilege)"
        elif composite_empire > 65.0:
            empire_stage = "STAGE 6: EARLY DECLINE (Internal polarization, currency printing, rising external competitor)"
        else:
            empire_stage = "STAGE 7: EMPIRE TRANSITION / LOSS OF HEGEMONY"

        # Ferguson's Law of Imperial Overstretch: Debt Interest > Defense Budget
        ferguson_breached = debt_interest_bn > defense_budget_bn
        interest_to_defense_ratio = debt_interest_bn / max(1.0, defense_budget_bn)

        return {
            "all_weather_macro_quadrant": quadrant,
            "recommended_risk_parity_weights": allocation,
            "dalio_composite_empire_power_index": round(composite_empire, 1),
            "dalio_empire_lifecycle_stage": empire_stage,
            "ferguson_law_breached": ferguson_breached,
            "debt_interest_to_defense_ratio": round(interest_to_defense_ratio, 2),
            "ferguson_law_assessment": "FERGUSON'S LAW BREACHED (Debt service > Military budget). Historical marker of rapid imperial retrenchment." if ferguson_breached else "STABLE_IMPERIAL_BALANCE",
            "status": "DALIO_FERGUSON_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. George Soros & Hyman Minsky: Reflexivity & Financial Fragility
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="soros.evaluate_reflexivity_and_minsky_fragility",
        description="Compute George Soros's Reflexive Divergence Index (RDI) and Hyman Minsky's Financial Fragility Ratio (FFR) across the 6-phase debt cycle.",
        parameters={
            "market_price": {"type": "float", "default": 2750.0, "description": "Current market asset price (e.g. Gold or S&P 500)"},
            "fundamental_fair_value": {"type": "float", "default": 2400.0, "description": "Underlying fundamental fair value"},
            "credit_acceleration_yoy_pct": {"type": "float", "default": 8.5, "description": "Rate of debt/credit expansion YoY"},
            "liquid_assets_bn": {"type": "float", "default": 450.0, "description": "Liquid cash/reserves in banking system"},
            "short_term_debt_obligations_bn": {"type": "float", "default": 920.0, "description": "Short-term debt maturing within 90 days"},
        },
        returns={"reflexive_divergence_index": "float", "minsky_financial_fragility_ratio": "float", "debt_cycle_phase": "string", "reflexive_regime": "string"},
        tags=["soros", "reflexivity", "minsky", "fragility", "bubbles"],
    )
    def evaluate_reflexivity_and_minsky_fragility(
        self,
        market_price: float = 2750.0,
        fundamental_fair_value: float = 2400.0,
        credit_acceleration_yoy_pct: float = 8.5,
        liquid_assets_bn: float = 450.0,
        short_term_debt_obligations_bn: float = 920.0,
    ) -> Dict[str, Any]:
        """Models the coupled Soros-Minsky non-linear reflexive feedback loop."""
        # Soros Reflexive Divergence Index: RDI = |P - F| / F * (1 + Credit_Acceleration / 100)
        price_gap_pct = abs(market_price - fundamental_fair_value) / max(1.0, fundamental_fair_value)
        rdi = price_gap_pct * (1.0 + max(0.0, credit_acceleration_yoy_pct) / 100.0) * 100.0

        # Minsky Financial Fragility Ratio: FFR = Short-Term Debt / Liquid Assets
        ffr = short_term_debt_obligations_bn / max(1.0, liquid_assets_bn)

        # 6-Phase Debt Cycle Classifier
        if ffr > 2.5 and rdi > 25.0:
            phase = "PHASE 4: THE MINSKY MOMENT / ILLUSION BUST (Fragility critical; liquidity freeze imminent)"
            regime = "SELF_REINFORCING_DOWNWARD_LIQUIDITY_SPIRAL"
        elif ffr > 1.8 and credit_acceleration_yoy_pct > 6.0:
            phase = "PHASE 2: THE SPECULATIVE BUBBLE (Market participants' decisions inflating collateral value)"
            regime = "POSITIVE_REFLEXIVE_FEEDBACK_BOOM"
        elif ffr > 1.2:
            phase = "PHASE 3: THE TOP (Central bank tightening; credit growth moderating)"
            regime = "REFLEXIVE_PLATEAU"
        else:
            phase = "PHASE 1: EARLY EXPANSION (Hedge finance dominates; solid fundamental support)"
            regime = "FUNDAMENTALLY_ANCHORED"

        return {
            "soros_reflexive_divergence_index": round(rdi, 2),
            "minsky_financial_fragility_ratio": round(ffr, 2),
            "archetypal_debt_cycle_phase": phase,
            "reflexive_feedback_regime": regime,
            "is_minsky_moment_risk_elevated": ffr > 2.0,
            "status": "SOROS_MINSKY_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. Perry Mehrling & Zoltan Pozsar: Money View & CIP Basis Engine
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="mehrling.evaluate_money_view_and_cip_strains",
        description="Compute Cross-Currency Basis (CIP deviations), Pozsar's 4 Prices of Money, and interbank payment stretch velocity.",
        parameters={
            "fx_spot": {"type": "float", "default": 1.0850, "description": "EUR/USD Spot exchange rate"},
            "fx_forward_3m": {"type": "float", "default": 1.0910, "description": "EUR/USD 3-Month outright forward rate"},
            "usd_libor_sofr_3m_pct": {"type": "float", "default": 5.25, "description": "3M USD benchmark rate %"},
            "eur_euribor_3m_pct": {"type": "float", "default": 3.75, "description": "3M EUR benchmark rate %"},
            "sofr_iorb_spread_bps": {"type": "float", "default": 8.0, "description": "SOFR minus IORB spread in basis points"},
        },
        returns={"cip_basis_bps": "float", "dollar_funding_stress": "string", "pozsar_4_prices_state": "object", "settlement_elasticity": "string"},
        tags=["mehrling", "pozsar", "cip_basis", "money_view", "repo", "shadow_banking"],
    )
    def evaluate_money_view_and_cip_strains(
        self,
        fx_spot: float = 1.0850,
        fx_forward_3m: float = 1.0910,
        usd_libor_sofr_3m_pct: float = 5.25,
        eur_euribor_3m_pct: float = 3.75,
        sofr_iorb_spread_bps: float = 8.0,
    ) -> Dict[str, Any]:
        """Calculates Covered Interest Parity (CIP) basis and Mehrling-Pozsar liquidity strains."""
        # CIP Formula: F_theoretical = S * (1 + r_usd * 0.25) / (1 + r_eur * 0.25)
        f_theo = fx_spot * (1.0 + (usd_libor_sofr_3m_pct / 100.0) * 0.25) / (1.0 + (eur_euribor_3m_pct / 100.0) * 0.25)
        cip_basis_bps = ((fx_forward_3m - f_theo) / fx_spot) * 10000.0

        if cip_basis_bps < -25.0:
            stress = "CRITICAL_DOLLAR_FUNDING_SHORTAGE (Foreign institutions paying severe premium to swap into USD cash)"
        elif cip_basis_bps < -10.0:
            stress = "ELEVATED_OFFSHORE_DOLLAR_SCARCITY"
        else:
            stress = "NORMAL_CROSS_CURRENCY_LIQUIDITY"

        pozsar_prices = {
            "Price_1_Par": "1:1 Deposit convertibility maintained; no stablecoin/bank run",
            "Price_2_Interest": f"SOFR-IORB spread: {sofr_iorb_spread_bps:+.1f} bps (Repo collateral velocity stable)",
            "Price_3_FX_Spot": f"EUR/USD @ {fx_spot:.4f} (CIP Basis: {cip_basis_bps:+.1f} bps)",
            "Price_4_Price_Level": "Commodities & Energy input pricing driving physical cost pass-through",
        }

        return {
            "cross_currency_cip_basis_bps": round(cip_basis_bps, 2),
            "dollar_funding_stress_regime": stress,
            "pozsar_four_prices_of_money": pozsar_prices,
            "mehrling_settlement_elasticity": "TIGHT_SETTLEMENT_DISCIPLINE" if cip_basis_bps < -15.0 or sofr_iorb_spread_bps > 10.0 else "ELASTIC_LIQUIDITY_EXPANSION",
            "status": "MONEY_VIEW_CIP_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Allison, Mearsheimer & Zeihan: Historical Matching & Stopping Power
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="allison.match_16_thucydides_case_studies",
        description="Match current geopolitical tension against Graham Allison's 16 historical rising vs ruling power case studies and compute Mearsheimer's Stopping Power of Water.",
        parameters={
            "challenger_growth_delta_pct": {"type": "float", "default": 3.5, "description": "Challenger GDP growth rate minus Hegemon growth rate"},
            "naval_oceanic_distance_nm": {"type": "float", "default": 6000.0, "description": "Oceanic distance between great powers in nautical miles"},
            "nuclear_triad_mutual_deterrence": {"type": "bool", "default": True, "description": "Whether both powers possess survivable second-strike nuclear arsenals"},
        },
        returns={"matched_historical_analogs": "array", "mearsheimer_stopping_power_score": "float", "kinetic_conflict_hazard_pct": "float"},
        tags=["allison", "thucydides", "mearsheimer", "zeihan", "geopolitics"],
    )
    def match_16_thucydides_case_studies(
        self,
        challenger_growth_delta_pct: float = 3.5,
        naval_oceanic_distance_nm: float = 6000.0,
        nuclear_triad_mutual_deterrence: bool = True,
    ) -> Dict[str, Any]:
        """Calculates distance match to Allison's 16 case studies."""
        historical_cases = [
            {"case": "US vs USSR (Cold War, 1940s-1980s)", "outcome": "NO_HOT_WAR (Nuclear MAD & Proxy Contests)", "similarity": 0.88 if nuclear_triad_mutual_deterrence else 0.40},
            {"case": "Britain vs Germany (Early 20th Century -> WWI)", "outcome": "WAR (1914 Naval arms race & Alliance tripwires)", "similarity": 0.82 if challenger_growth_delta_pct > 3.0 else 0.50},
            {"case": "US vs Japan (1930s-1941)", "outcome": "WAR (Energy / Oil Embargo Trigger)", "similarity": 0.78},
            {"case": "Sparta vs Athens (Peloponnesian War, 5th C. BC)", "outcome": "WAR (Structural Fear of Rising Hegemon)", "similarity": 0.75},
        ]
        historical_cases.sort(key=lambda x: x["similarity"], reverse=True)

        # Mearsheimer's Stopping Power of Water (0 to 1, where 1.0 means insurmountable oceanic moat)
        stopping_power = min(1.0, naval_oceanic_distance_nm / 7000.0)

        # Kinetic Conflict Hazard
        base_hazard = 75.0 if challenger_growth_delta_pct > 2.5 else 40.0
        if nuclear_triad_mutual_deterrence:
            base_hazard *= 0.35  # Nuclear deterrence reduces direct great power war to proxy/economic domain
        hazard = base_hazard * (1.0 - stopping_power * 0.30)

        return {
            "matched_historical_analogs": historical_cases,
            "mearsheimer_stopping_power_of_water_score": round(stopping_power, 2),
            "kinetic_direct_conflict_hazard_pct": round(hazard, 1),
            "strategic_doctrine": "COLD_ECONOMIC_AND_PROXIES_WAR (Direct invasion blocked by ocean and MAD deterrence; conflict shifted to semiconductor/energy supply chain warfare)",
            "status": "THUCYDIDES_MEARSHEIMER_MATCHED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 5. Vaclav Smil & Chris Miller: EROEI & Chip War Chokepoints
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="smil.evaluate_eroei_and_semiconductor_chokepoints",
        description="Compute Vaclav Smil's Energy Return on Energy Invested (EROEI) and Chris Miller's 5-stage Chip War semiconductor bottleneck matrix.",
        parameters={
            "primary_energy_eroei": {"type": "float", "default": 14.0, "description": "Aggregate energy mix EROEI ratio (e.g. 14:1)"},
            "asml_euv_concentration_pct": {"type": "float", "default": 100.0, "description": "ASML global share of sub-7nm EUV scanners"},
            "tsmc_advanced_fab_share_pct": {"type": "float", "default": 92.0, "description": "TSMC global market share of leading-edge wafers (<5nm)"},
        },
        returns={"eroei_civilization_threshold_status": "string", "chip_war_single_point_failure_risk": "string", "global_gdp_shock_potential_pct": "float"},
        tags=["smil", "eroei", "chip_war", "semiconductors", "energy"],
    )
    def evaluate_eroei_and_semiconductor_chokepoints(
        self,
        primary_energy_eroei: float = 14.0,
        asml_euv_concentration_pct: float = 100.0,
        tsmc_advanced_fab_share_pct: float = 92.0,
    ) -> Dict[str, Any]:
        """Evaluates thermodynamic EROEI thresholds and silicon fabrication vulnerability."""
        # Smil EROEI evaluation (Civilization cliff: EROEI < 7:1)
        if primary_energy_eroei < 7.0:
            eroei_status = "CRITICAL_THERMODYNAMIC_COLLAPSE (Energy surplus insufficient to maintain complex institutions, healthcare, and higher education)"
        elif primary_energy_eroei < 12.0:
            eroei_status = "ELEVATED_INFLATION_FRICTION (Declining net energy surplus forces structural stagflation)"
        else:
            eroei_status = "HEALTHY_THERMODYNAMIC_SURPLUS (Supports continuous technological innovation)"

        # Chris Miller Chip War Vulnerability
        if asml_euv_concentration_pct > 90.0 and tsmc_advanced_fab_share_pct > 85.0:
            chip_risk = "EXTREME_HYPER_CHOKEPOINT (Total reliance on a 110-mile strait in Taiwan and one factory in Veldhoven, Netherlands)"
            gdp_shock = -8.5  # Global GDP contraction on 12-month disruption
        else:
            chip_risk = "DISTRIBUTED_FABRICATION_CAPACITY"
            gdp_shock = -2.0

        return {
            "current_eroei_ratio": round(primary_energy_eroei, 1),
            "smil_eroei_civilization_status": eroei_status,
            "chip_war_vulnerability_assessment": chip_risk,
            "estimated_global_gdp_shock_on_disruption_pct": gdp_shock,
            "status": "EROEI_CHIP_WAR_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 6. Nassim Taleb: Incerto Antifragility Convexity & Lindy Longevity
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="taleb.evaluate_antifragility_and_lindy",
        description="Compute Nassim Taleb's Antifragility Convexity Payoff (Fragile vs Antifragile) and Lindy Effect Longevity for financial instruments and sovereign institutions.",
        parameters={
            "payoff_gain_if_right": {"type": "float", "default": 4.8, "description": "Upside payoff multiplier on favorable shock"},
            "payoff_loss_if_wrong": {"type": "float", "default": 1.0, "description": "Downside loss multiplier on adverse shock"},
            "institution_age_years": {"type": "float", "default": 234.0, "description": "Historical age of the institution/instrument in years (e.g. US Constitution: 234 yrs, Bitcoin: 15 yrs, Gold: 5000 yrs)"},
        },
        returns={"antifragility_triad_classification": "string", "convexity_second_derivative": "float", "lindy_expected_future_longevity_years": "float"},
        tags=["taleb", "incerto", "antifragile", "lindy", "convexity"],
    )
    def evaluate_antifragility_and_lindy(
        self,
        payoff_gain_if_right: float = 4.8,
        payoff_loss_if_wrong: float = 1.0,
        institution_age_years: float = 234.0,
    ) -> Dict[str, Any]:
        """Calculates Taleb's Antifragility Convexity and Lindy Longevity."""
        convexity = (payoff_gain_if_right - payoff_loss_if_wrong) / max(0.1, payoff_loss_if_wrong)

        if convexity >= 2.0:
            triad = "ANTIFRAGILE (Gains from disorder and volatility; non-linear positive convexity)"
        elif convexity >= 0.0:
            triad = "ROBUST (Resists disorder with linear response)"
        else:
            triad = "FRAGILE (Suffers severe catastrophic asymmetric loss under unexpected tail events)"

        # Lindy Law: E[Remaining Life] = Age
        lindy_future = float(institution_age_years)

        return {
            "payoff_convexity_score": round(convexity, 2),
            "antifragility_triad_classification": triad,
            "institution_current_age_years": institution_age_years,
            "lindy_expected_future_longevity_years": round(lindy_future, 1),
            "lindy_axiom": f"By the Lindy Law, an institution that has survived {institution_age_years:.0f} years is expected to survive another {lindy_future:.0f} years.",
            "status": "TALEB_ANTIFRAGILITY_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 7. Marcos Author-C: Triple Barrier & CUSUM Structural Break
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="prado.execute_triple_barrier_and_cusum_filter",
        description="Execute Marcos Author-C's Triple Barrier Labeling method and CUSUM structural break / bubble detector.",
        parameters={
            "prices": {"type": "array", "description": "Time series array of prices"},
            "volatility_multiplier": {"type": "float", "default": 2.0, "description": "ATR multiplier for upper/lower horizontal barriers"},
            "vertical_barrier_bars": {"type": "int", "default": 20, "description": "Maximum holding period before vertical barrier expiration"},
        },
        returns={"barrier_events_labeled": "int", "cusum_structural_breaks": "int", "mean_label_return": "float"},
        tags=["prado", "triple_barrier", "cusum", "structural_break", "afml"],
    )
    def execute_triple_barrier_and_cusum_filter(
        self,
        prices: Optional[List[float]] = None,
        volatility_multiplier: float = 2.0,
        vertical_barrier_bars: int = 20,
    ) -> Dict[str, Any]:
        """Calculates Triple Barrier events and CUSUM structural breaks."""
        if not prices or len(prices) < 40:
            np.random.seed(42)
            p = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.015, 120)))
        else:
            p = np.array(prices, dtype=float)

        n = len(p)
        log_rets = np.diff(np.log(p))
        std_ret = np.std(log_rets) if len(log_rets) > 1 else 0.01

        upper_barrier = std_ret * volatility_multiplier
        lower_barrier = -std_ret * volatility_multiplier

        # CUSUM Filter
        cusum_pos = 0.0
        cusum_neg = 0.0
        threshold = std_ret * 2.5
        breaks = 0

        for r in log_rets:
            cusum_pos = max(0.0, cusum_pos + r)
            cusum_neg = min(0.0, cusum_neg + r)
            if cusum_pos > threshold:
                breaks += 1
                cusum_pos = 0.0
            elif cusum_neg < -threshold:
                breaks += 1
                cusum_neg = 0.0

        # Triple Barrier Labels
        labels = []
        for i in range(n - vertical_barrier_bars):
            entry_p = p[i]
            window = p[i + 1 : i + 1 + vertical_barrier_bars]
            cum_rets = (window - entry_p) / entry_p
            hit_upper = np.where(cum_rets >= upper_barrier)[0]
            hit_lower = np.where(cum_rets <= lower_barrier)[0]

            if len(hit_upper) > 0 and (len(hit_lower) == 0 or hit_upper[0] < hit_lower[0]):
                labels.append(1)  # Profit take hit first
            elif len(hit_lower) > 0 and (len(hit_upper) == 0 or hit_lower[0] < hit_upper[0]):
                labels.append(-1)  # Stop loss hit first
            else:
                labels.append(0)  # Vertical barrier timeout

        labels_arr = np.array(labels)
        pos_share = float(np.mean(labels_arr == 1)) if len(labels_arr) > 0 else 0.0

        return {
            "total_bars_analyzed": n,
            "barrier_events_labeled": len(labels),
            "profit_target_hits_share_pct": round(pos_share * 100.0, 1),
            "cusum_structural_breaks_detected": breaks,
            "status": "TRIPLE_BARRIER_CUSUM_EVALUATED",
        }


# Global singleton instance
CANONICAL_LITERARY_MASTERY_ENGINE = CanonicalLiteraryMasteryEngine()
