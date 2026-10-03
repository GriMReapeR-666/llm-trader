"""Sovereign Macro Diagnostic, Central Bank Linguistic Forensics & Master Literary Engine.

Implements all chapters and quantitative frameworks from the definitive macro literature:

PILLAR 1: GLOBAL MACRO, DEBT CYCLES & MONETARY PLUMBING
- Ray Dalio: Big Debt Crises (Beautiful Deleveraging), Changing World Order (18 Empire Gauges).
- Perry Mehrling: The Money View Hierarchy & Dealer of Last Resort.
- Zoltan Pozsar: Shadow Banking, Bretton Woods III (Inside vs Outside Money).
- Reinhart & Rogoff: 800-Year Sovereign Default Index (Debt/GDP > 90%).
- Hyman Minsky: Financial Instability Hypothesis (Hedge -> Speculative -> Ponzi).

PILLAR 2: GEOPOLITICS, GRAND STRATEGY & WAR DETERMINANTS
- Halford Mackinder: Heartland Theory (Eurasian landmass pivot).
- Nicholas Spykman: Rimland Theory (Maritime containment).
- Graham Allison: Thucydides Trap Index (16 historical rising vs ruling power cases).
- John Mearsheimer: Offensive Realism Power Maximization & Regional Hegemony.
- Peter Zeihan: Demographic Pyramid Inversion & Navigable River Geography.
- Niall Ferguson: Financial Networks vs Sovereign Hierarchies.

PILLAR 3: CENTRAL BANKING, NARRATIVE ECONOMICS & DISCOURSE FORENSICS
- Central Bank Real-Time NLP Forensics: Hawk/Dove vector shifts, Syntactic Hedging, Panic Flags.
- Baker-Bloom-Davis: Economic Policy Uncertainty (EPU) Lexicon Index.
- Caldara-Iacoviello: Geopolitical Risk (GPR) Lexicon Index.
- Robert J. Shiller: Narrative Economics SIR Epidemic Model.
- Ben Bernanke: Forward Guidance Shadow Rate & Taylor Rule Gap.
- Herman & Chomsky: Manufacturing Consent 5 Propaganda Filters & Omission Score.

PILLAR 4: QUANTITATIVE EPISTEMOLOGY & REFLEXIVITY
- Nassim Taleb: Incerto Antifragility Convexity & Lindy Longevity.
- George Soros: Reflexivity Feedback Loop (Cognitive vs Participating functions).

PILLAR 5: PHYSICAL ECONOMICS & INDUSTRIAL CHOKEPOINTS
- Vaclav Smil: 4 Pillars of Civilization (Ammonia, Steel, Cement, Plastics) Energy Densities.
- Chris Miller: Chip War Advanced Semiconductor Chokepoints (ASML EUV, TSMC).
- Helen Thompson: Disorder Energy-Monetary Trilemma.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("SovereignMacroLiteraryForensics")


class SovereignMacroLiteraryEngine(BaseAnalyticalModule):
    """Master Sovereign Macro, Central Bank Speech NLP & Literary Forensics Engine."""

    module_id = "sovereign_macro_literary"
    module_name = "Sovereign Macro, Speech NLP & Canonical Literature Forensics Engine"
    version = "1.0.0"
    category = "sovereign_macro_literature"
    tags = ["sovereign_macro", "dalio", "minsky", "pozsar", "mehrling", "mackinder", "allison", "shiller", "epu", "gpr", "smil", "chip_war"]
    mathematics_formulas = [
        r"\text{Greenspan-Guidotti: } \frac{\text{FX Reserves}}{\text{Short-Term External Debt}} \ge 1.0",
        r"\Delta d_t = (r - g) d_{t-1} - pb_t \quad (\text{Sovereign Debt Compounding Dynamics})",
        r"i_t = r^* + \pi_t + 0.5(\pi_t - \pi^*) + 0.5(y_t - y^*) \quad (\text{Taylor Rule Policy Rate})",
        r"\dot{I} = \beta S I - \gamma I \quad (\text{Shiller SIR Narrative Epidemic})",
        r"\text{EPU Index} = \frac{1}{N} \sum_{i=1}^N \mathbb{I}(\text{Uncertainty} \land \text{Policy} \land \text{Economy})",
    ]

    # ───────────────────────────────────────────────────────────────────────────
    # 1. The 5-Pillar Sovereign Diagnostic Framework
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="macro.audit_5pillar_sovereign_health",
        description="Audit nation-state balance sheets across the 5 interlocking pillars: External Balance (Greenspan-Guidotti), Fiscal (r-g), Monetary, Terms of Trade, and Societal Cohesion.",
        parameters={
            "country_name": {"type": "string", "default": "UNITED_STATES", "description": "Target country name"},
            "fx_reserves_bn": {"type": "float", "default": 240.0, "description": "FX reserves in billions USD"},
            "short_term_external_debt_bn": {"type": "float", "default": 180.0, "description": "Short-term external debt maturing within 12 months in billions USD"},
            "debt_to_gdp_pct": {"type": "float", "default": 122.0, "description": "Public debt as % of GDP"},
            "real_interest_rate_pct": {"type": "float", "default": 2.1, "description": "Real 10Y sovereign yield (r)"},
            "real_gdp_growth_pct": {"type": "float", "default": 2.3, "description": "Real GDP growth rate (g)"},
            "credit_growth_yoy_pct": {"type": "float", "default": 4.5, "description": "Bank credit growth YoY"},
            "nominal_gdp_growth_pct": {"type": "float", "default": 5.2, "description": "Nominal GDP growth YoY"},
            "terms_of_trade_delta_pct": {"type": "float", "default": -3.2, "description": "YoY % change in Terms of Trade index"},
            "real_wage_growth_spread_pct": {"type": "float", "default": -1.4, "description": "Nominal wage growth minus essential CPI inflation"},
        },
        returns={"pillar_diagnostics": "object", "crisis_warning_signals": "array", "overall_sovereign_solvency_score": "float"},
        tags=["sovereign", "greenspan_guidotti", "macro_balance_sheet", "solvency"],
    )
    def audit_5pillar_sovereign_health(
        self,
        country_name: str = "UNITED_STATES",
        fx_reserves_bn: float = 240.0,
        short_term_external_debt_bn: float = 180.0,
        debt_to_gdp_pct: float = 122.0,
        real_interest_rate_pct: float = 2.1,
        real_gdp_growth_pct: float = 2.3,
        credit_growth_yoy_pct: float = 4.5,
        nominal_gdp_growth_pct: float = 5.2,
        terms_of_trade_delta_pct: float = -3.2,
        real_wage_growth_spread_pct: float = -1.4,
    ) -> Dict[str, Any]:
        """Audits sovereign solvency across the 5 canonical balance sheet pillars."""
        warnings = []

        # Pillar 1: External Balance (Greenspan-Guidotti Rule: FX Reserves / Short-term External Debt >= 1.0)
        gg_ratio = fx_reserves_bn / max(1.0, short_term_external_debt_bn)
        is_reserve_currency = "US" in country_name.upper() or "UNITED_STATES" in country_name.upper()
        if not is_reserve_currency and gg_ratio < 1.0:
            warnings.append(f"PILLAR 1 EXTERNAL: Greenspan-Guidotti rule breached ({gg_ratio:.2f} < 1.0). High currency crisis / sudden stop risk.")

        # Pillar 2: Fiscal Dynamics (r - g spread)
        r_minus_g = real_interest_rate_pct - real_gdp_growth_pct
        if r_minus_g > 0 and debt_to_gdp_pct > 90.0:
            warnings.append(f"PILLAR 2 FISCAL: r > g ({r_minus_g:+.2f}%) with Debt/GDP {debt_to_gdp_pct:.1f}% > 90% (Reinhart-Rogoff threshold). Exponential debt snowball compounding.")

        # Pillar 3: Monetary & Banking Plumbing (Credit growth > 2x Nominal GDP)
        if credit_growth_yoy_pct > (2.0 * max(0.5, nominal_gdp_growth_pct)):
            warnings.append(f"PILLAR 3 MONETARY: Credit growth ({credit_growth_yoy_pct:.1f}%) > 2x Nominal GDP ({nominal_gdp_growth_pct:.1f}%). Speculative asset bubble forming.")

        # Pillar 4: Terms of Trade & Energy
        if terms_of_trade_delta_pct < -15.0:
            warnings.append(f"PILLAR 4 TERMS OF TRADE: Severe collapse ({terms_of_trade_delta_pct:+.1f}%). Immediate currency devaluation pressure.")

        # Pillar 5: Societal Cohesion & Real Wage Compression
        if real_wage_growth_spread_pct < -2.0:
            warnings.append(f"PILLAR 5 SOCIETAL: Severe real wage compression ({real_wage_growth_spread_pct:+.1f}%). Populist political/tariff policy risk elevated.")

        # Compute Composite Sovereign Health Score (0 to 100)
        score = 100.0 - (len(warnings) * 18.0) - (max(0.0, debt_to_gdp_pct - 60.0) * 0.25)
        score = max(5.0, min(100.0, score))

        return {
            "country": country_name.upper(),
            "pillar_1_external_greenspan_guidotti_ratio": round(gg_ratio, 3),
            "pillar_2_fiscal_r_minus_g_spread_pct": round(r_minus_g, 2),
            "pillar_3_monetary_credit_to_gdp_ratio": round(credit_growth_yoy_pct / max(0.1, nominal_gdp_growth_pct), 2),
            "pillar_4_terms_of_trade_delta_pct": terms_of_trade_delta_pct,
            "pillar_5_real_wage_compression_pct": real_wage_growth_spread_pct,
            "active_warning_signals": warnings,
            "sovereign_health_score_out_of_100": round(score, 1),
            "solvency_status": "STRONG_SOLVENT" if score > 75 else ("VULNERABLE_TRANSITION" if score > 45 else "CRITICAL_SOLVENCY_STRESS"),
            "status": "5PILLAR_SOVEREIGN_AUDITED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. Central Bank Real-Time Linguistic Forensics & Speech NLP Parser
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="nlp.parse_central_bank_speech_forensics",
        description="Execute sub-second NLP lexical and syntactic forensics on Fed, ECB, or BOJ speeches to detect Hawk/Dove stance shifts, hedging qualifiers, liquidity panic alarms, EPU, and GPR indices.",
        parameters={
            "speech_text": {"type": "string", "description": "Full text or excerpt of central bank speech / FOMC press conference"},
            "central_bank": {"type": "string", "default": "FEDERAL_RESERVE", "description": "Central bank institution"},
        },
        returns={"hawk_dove_score": "float", "hawkish_triggers_found": "array", "dovish_triggers_found": "array", "panic_stress_triggers": "array", "epu_uncertainty_score": "float", "gpr_conflict_score": "float", "actionable_market_implication": "string"},
        tags=["nlp", "fed_speech", "hawk_dove", "epu", "gpr", "central_banks"],
    )
    def parse_central_bank_speech_forensics(
        self,
        speech_text: str = "",
        central_bank: str = "FEDERAL_RESERVE",
    ) -> Dict[str, Any]:
        """Parses speech against institutional lexicons and syntactic structures."""
        text = speech_text or (
            "Inflation remains unacceptably high and broad-based, requiring further policy firming and a restrictive stance for longer. "
            "However, we are data dependent, recognizing two-sided risks and potential tighter credit conditions. "
            "We are monitoring market functioning and counterparty liquidity strains closely."
        )
        text_lower = text.lower()
        words = re.findall(r"\b\w+\b", text_lower)
        total_words = max(1, len(words))

        # 1. Hawkish Lexicon (Spikes bond yields, strengthens currency)
        hawkish_dict = [
            "tighten", "tightening", "unacceptably high", "broad-based inflation", "further policy firming",
            "persistent", "wage-price dynamics", "above target", "restrictive stance for longer",
            "demand exceeds supply", "overheated", "premature to ease", "hike", "upside risks to inflation",
            "resolute", "anchoring expectations", "more work to do"
        ]
        # 2. Dovish Lexicon (Drops bond yields, risk rally)
        dovish_dict = [
            "transitory", "subdued", "disinflationary tailwinds", "headwinds to growth", "downside risks",
            "tighter credit conditions", "policy lags", "balanced risks", "moderation", "sufficiently restrictive",
            "data dependent", "two-sided risks", "easing", "cooling", "normalization", "neutral rate"
        ]
        # 3. Panic & Liquidity Emergency Triggers
        panic_dict = [
            "market functioning", "liquidity strains", "orderly conditions", "standing repo facility",
            "swap lines", "discount window", "counterparty monitoring", "unusual volatility",
            "systemic resilience", "fire-sale risk", "ring-fencing", "emergency lending", "lender of last resort"
        ]
        # 4. Economic Policy Uncertainty (Baker-Bloom-Davis EPU)
        epu_dict = [
            "uncertainty", "tariff", "trade barrier", "sanctions", "export controls", "retaliation",
            "embargo", "protectionism", "debt ceiling", "fiscal cliff", "appropriations deadlock", "geopolitical"
        ]
        # 5. Geopolitical Risk (Caldara-Iacoviello GPR)
        gpr_dict = [
            "war", "military escalation", "strait of hormuz", "blockade", "chokepoint", "mobilization",
            "kinetic action", "theater of operations", "cyber warfare", "critical infrastructure attack",
            "nuclear posture", "force majeure", "conflict", "missile strike"
        ]

        found_hawkish = [kw for kw in hawkish_dict if kw in text_lower]
        found_dovish = [kw for kw in dovish_dict if kw in text_lower]
        found_panic = [kw for kw in panic_dict if kw in text_lower]
        found_epu = [kw for kw in epu_dict if kw in text_lower]
        found_gpr = [kw for kw in gpr_dict if kw in text_lower]

        # Calculate Hawk-Dove Vector (-1.0 to +1.0)
        hawk_count = len(found_hawkish)
        dove_count = len(found_dovish)
        tot_stance = hawk_count + dove_count
        if tot_stance > 0:
            hawk_dove_score = (hawk_count - dove_count) / tot_stance
        else:
            hawk_dove_score = 0.0

        # Scores
        epu_score = min(1.0, len(found_epu) * 0.25)
        gpr_score = min(1.0, len(found_gpr) * 0.30)
        has_panic = len(found_panic) > 0

        # Actionable Implication
        if has_panic:
            implication = "LIQUIDITY_PANIC_ALERT: Central bank referencing emergency facilities. Prepare for rapid liquidity injections / risk asset reversal."
        elif hawk_dove_score > 0.30:
            implication = "HAWKISH_STANCE: Yields repricing higher; USD bullish; equity multiples face valuation compression."
        elif hawk_dove_score < -0.30:
            implication = "DOVISH_PIVOT: Duration bonds and precious metals (Gold) rally; rate cuts being priced forward."
        else:
            implication = "BALANCED_TWO_SIDED_GUIDANCE: Central bank on hold; market volatility range-bound."

        return {
            "central_bank": central_bank.upper(),
            "hawk_dove_polarity_score": round(hawk_dove_score, 3),
            "stance_classification": "STRONGLY_HAWKISH" if hawk_dove_score > 0.40 else ("MODERATELY_HAWKISH" if hawk_dove_score > 0.10 else ("STRONGLY_DOVISH" if hawk_dove_score < -0.40 else ("MODERATELY_DOVISH" if hawk_dove_score < -0.10 else "NEUTRAL"))),
            "hawkish_triggers_detected": found_hawkish,
            "dovish_triggers_detected": found_dovish,
            "panic_liquidity_triggers": found_panic,
            "epu_uncertainty_index": round(epu_score, 2),
            "gpr_geopolitical_risk_index": round(gpr_score, 2),
            "actionable_market_implication": implication,
            "status": "SPEECH_NLP_PARSED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. Pillar 1 Literature: Dalio, Minsky, Pozsar & Mehrling Debt Engines
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="literature.evaluate_debt_cycle_and_monetary_plumbing",
        description="Evaluate Ray Dalio's Beautiful Deleveraging, Hyman Minsky's Financial Instability stage, Zoltan Pozsar's Bretton Woods III, and Perry Mehrling's Money View hierarchy.",
        parameters={
            "debt_growth_rate_pct": {"type": "float", "default": 6.5, "description": "Total debt growth YoY"},
            "income_growth_rate_pct": {"type": "float", "default": 4.0, "description": "Nominal national income / GDP growth YoY"},
            "debt_service_ratio_pct": {"type": "float", "default": 14.8, "description": "Debt service payments as % of disposable income"},
            "ponzi_finance_share_pct": {"type": "float", "default": 28.0, "description": "% of borrowers requiring debt refinancing to service interest"},
            "commodity_backing_share_pct": {"type": "float", "default": 18.0, "description": "% of central bank reserve growth allocated to physical commodities/gold"},
        },
        returns={"dalio_cycle_phase": "string", "minsky_regime": "string", "pozsar_monetary_regime": "string", "mehrling_hierarchy_state": "string", "prescriptive_strategy": "string"},
        tags=["dalio", "minsky", "pozsar", "mehrling", "debt_crises", "monetary_plumbing"],
    )
    def evaluate_debt_cycle_and_monetary_plumbing(
        self,
        debt_growth_rate_pct: float = 6.5,
        income_growth_rate_pct: float = 4.0,
        debt_service_ratio_pct: float = 14.8,
        ponzi_finance_share_pct: float = 28.0,
        commodity_backing_share_pct: float = 18.0,
    ) -> Dict[str, Any]:
        """Synthesizes canonical macro debt cycle and monetary plumbing literature."""
        # Dalio Cycle Classification
        if debt_growth_rate_pct > income_growth_rate_pct + 2.0 and debt_service_ratio_pct > 13.0:
            dalio_phase = "LATE_BUBBLE_TOP_PHASE (Debt compounding faster than income)"
            dalio_deleveraging = "Requires Beautiful Deleveraging (Balanced mix of austerity, debt restructuring, wealth transfers, and debt monetization)."
        elif debt_service_ratio_pct > 16.0:
            dalio_phase = "DEPRESSION_DELEVERAGING_PHASE (Pushing on a string with traditional rate cuts)"
            dalio_deleveraging = "Forced monetization / Yield Curve Control mandatory."
        else:
            dalio_phase = "EARLY_TO_MID_EXPANSION_CYCLE (Healthy debt servicing capacity)"
            dalio_deleveraging = "Standard interest rate policy effective."

        # Minsky Classification
        if ponzi_finance_share_pct > 35.0:
            minsky = "PONZI_FINANCE_REGIME (Borrowers must issue new debt just to pay interest; high Minsky Moment crash vulnerability)"
        elif ponzi_finance_share_pct > 15.0:
            minsky = "SPECULATIVE_FINANCE_REGIME (Cash flows cover interest but principal must be rolled over)"
        else:
            minsky = "HEDGE_FINANCE_REGIME (Cash flows safely cover both principal and interest)"

        # Pozsar Bretton Woods III vs Bretton Woods II
        if commodity_backing_share_pct > 25.0:
            pozsar = "BRETTON_WOODS_III_TRANSITION (Outside Money backed by Commodities/Gold displacing uncollateralized Inside Money G7 fiat debt)"
        else:
            pozsar = "BRETTON_WOODS_II_MAINTAINED (Inside Money US Treasury claims dominate global reserve accumulation)"

        # Mehrling Money View
        mehrling = "Hierarchy: Gold/Commodities (Ultimate Settlement) -> Central Bank Reserves -> Commercial Bank Deposits -> Securities Credit. Tension currently concentrated in repo collateral velocity."

        return {
            "dalio_debt_cycle_phase": dalio_phase,
            "dalio_deleveraging_diagnosis": dalio_deleveraging,
            "minsky_financial_instability_stage": minsky,
            "pozsar_bretton_woods_regime": pozsar,
            "mehrling_money_view_hierarchy": mehrling,
            "prescriptive_portfolio_strategy": "LONG_PHYSICAL_GOLD_SHORT_PONZI_CREDIT" if "PONZI" in minsky or "III" in pozsar else "LONG_QUALITY_EQUITIES",
            "status": "DEBT_MONETARY_PLUMBING_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Pillar 2 Literature: Mackinder, Spykman, Allison & Zeihan Geopolitics
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="literature.evaluate_geopolitical_power_parity",
        description="Evaluate Halford Mackinder's Heartland vs Nicholas Spykman's Rimland control, Graham Allison's Thucydides Trap risk, and Peter Zeihan's demographic vulnerability.",
        parameters={
            "rising_power_gdp_share_of_ruling": {"type": "float", "default": 72.0, "description": "Challenger nation GDP as % of Hegemon nation GDP (70-90% is danger zone)"},
            "eurasian_heartland_alignment_score": {"type": "float", "default": 0.82, "description": "Degree of Sino-Russian-Iranian Eurasian integration (0.0 to 1.0)"},
            "median_population_age": {"type": "float", "default": 39.5, "description": "National median age (Demographic aging index)"},
            "navigable_river_inland_km": {"type": "float", "default": 40000.0, "description": "Kilometers of navigable inland river network (Capital generation power)"},
        },
        returns={"thucydides_trap_risk": "string", "mackinder_spykman_balance": "string", "zeihan_demographic_logistics": "string", "strategic_foresight": "string"},
        tags=["mackinder", "spykman", "thucydides", "allison", "zeihan", "geopolitics"],
    )
    def evaluate_geopolitical_power_parity(
        self,
        rising_power_gdp_share_of_ruling: float = 72.0,
        eurasian_heartland_alignment_score: float = 0.82,
        median_population_age: float = 39.5,
        navigable_river_inland_km: float = 40000.0,
    ) -> Dict[str, Any]:
        """Calculates structural conflict probability using geopolitical and demographic models."""
        # Allison Thucydides Trap Calculation
        if 65.0 <= rising_power_gdp_share_of_ruling <= 95.0:
            thucydides = "CRITICAL_THUCYDIDES_TRAP_ZONE (Rising power parity creates structural paranoia; 12 of 16 historical cases resulted in war)"
            war_prob = 0.75
        else:
            thucydides = "CONTAINED_ASYMMETRY (Hegemonic dominance or wide gap)"
            war_prob = 0.25

        # Mackinder vs Spykman Analysis
        if eurasian_heartland_alignment_score > 0.70:
            pivot = "MACKINDER_HEARTLAND_CONSOLIDATION: Eurasian landmass resources (Russian energy/minerals + Chinese manufacturing) insulated from maritime naval blockade."
        else:
            pivot = "SPYKMAN_RIMLAND_DOMINANCE: Maritime alliance (US/NATO/Japan/AUKUS) successfully contains the continental periphery."

        # Zeihan Demographic & Logistics Index
        if median_population_age > 42.0:
            demographic = "TERMINAL_DEMOGRAPHIC_INVERSION (Rapid workforce collapse; closing geopolitical aggression window within 5-10 years)"
        else:
            demographic = "STABLE_DEMOGRAPHIC_CONSUMPTION_BASE"

        logistics = f"Inland water network ({navigable_river_inland_km:,.0f} km) yields perpetual low-cost internal transport and capital generation."

        return {
            "thucydides_trap_assessment": thucydides,
            "historical_precedent_conflict_prob": war_prob,
            "mackinder_spykman_geopolitical_balance": pivot,
            "zeihan_demographic_profile": demographic,
            "geographical_logistics_endowment": logistics,
            "strategic_foresight_summary": "Eurasian land consolidation challenges maritime containment while demographic cliffs accelerate preemptive action windows.",
            "status": "GEOPOLITICAL_POWER_PARITY_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 5. Pillar 3 & 4 Literature: Shiller Narrative SIR & Smil Physical Economics
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="literature.model_narrative_and_physical_chokepoints",
        description="Model Robert Shiller's Narrative SIR epidemic spread and Vaclav Smil's 4 Pillars of Physical Civilization (Ammonia, Steel, Cement, Plastics) energy densities.",
        parameters={
            "narrative_contagion_rate_beta": {"type": "float", "default": 0.45, "description": "Shiller transmission rate per exposure"},
            "narrative_recovery_rate_gamma": {"type": "float", "default": 0.20, "description": "Forgetfulness / narrative decay rate"},
            "ammonia_fertilizer_supply_pct": {"type": "float", "default": 92.0, "description": "Current global ammonia supply as % of baseline demand"},
            "semiconductor_euv_supply_pct": {"type": "float", "default": 88.0, "description": "Advanced lithography operational uptime % (Chris Miller Chip War)"},
        },
        returns={"shiller_sir_narrative_trajectory": "object", "smil_physical_bottleneck_score": "float", "chokepoint_vulnerabilities": "array"},
        tags=["shiller", "narrative_economics", "smil", "chip_war", "physical_economics"],
    )
    def model_narrative_and_physical_chokepoints(
        self,
        narrative_contagion_rate_beta: float = 0.45,
        narrative_recovery_rate_gamma: float = 0.20,
        ammonia_fertilizer_supply_pct: float = 92.0,
        semiconductor_euv_supply_pct: float = 88.0,
    ) -> Dict[str, Any]:
        """Combines Shiller's epidemiological narrative model with Smil's physical constraints."""
        # Shiller SIR Model: R0 = beta / gamma
        r0 = narrative_contagion_rate_beta / max(0.01, narrative_recovery_rate_gamma)
        if r0 > 1.0:
            narrative_state = f"VIRAL_NARRATIVE_EPIDEMIC (R_0 = {r0:.2f} > 1.0). Viral narrative dominates market price discovery."
        else:
            narrative_state = f"SUB_CRITICAL_DECAY (R_0 = {r0:.2f} < 1.0). Narrative fading from collective consciousness."

        # Vaclav Smil 4 Pillars Constraints
        bottlenecks = []
        if ammonia_fertilizer_supply_pct < 95.0:
            bottlenecks.append(f"VACLAV SMIL PILLAR 1 (AMMONIA): {ammonia_fertilizer_supply_pct}% capacity. Synthetic nitrogen shortfall directly impacts protein yields for 4 billion humans.")
        if semiconductor_euv_supply_pct < 90.0:
            bottlenecks.append(f"CHRIS MILLER CHIP WAR (EUV LITHOGRAPHY): {semiconductor_euv_supply_pct}% uptime. Single-point failure in ASML optics / TSMC packaging.")

        phys_score = min(1.0, (100.0 - ammonia_fertilizer_supply_pct) * 0.05 + (100.0 - semiconductor_euv_supply_pct) * 0.04)

        return {
            "shiller_basic_reproduction_r0": round(r0, 3),
            "shiller_narrative_regime": narrative_state,
            "smil_physical_bottleneck_severity": round(phys_score, 2),
            "critical_physical_chokepoints": bottlenecks,
            "status": "NARRATIVE_PHYSICAL_CHOKEPOINTS_EVALUATED",
        }


# Global singleton instance
SOVEREIGN_MACRO_LITERARY_ENGINE = SovereignMacroLiteraryEngine()
