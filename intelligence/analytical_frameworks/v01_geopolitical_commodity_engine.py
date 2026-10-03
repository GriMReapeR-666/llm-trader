"""Volume 01: Geopolitical & Macro-Commodity Cross-Market Analysis Engine.

Implements:
1. Cascade Ripple Analysis (2nd, 3rd, 4th Order Transmission Mechanisms).
2. Relative Value (Spread) Analysis: Corn-Soybean Ratio, 3:2:1 Crack Spread, Soybean Crush Spread, Spark/Dark Margins.
3. Input-Cost & Chokepoint Vulnerability: Geographic & Precursor Chemical Bottlenecks.
4. Substitution Effect Vectors (Energy, Agricultural, Reserve Currency).
5. Herman Kahn 5-Level Escalation Ladder & Third-Party Arbitrageur Flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("GeopoliticalCommodityEngine")


class GeopoliticalCommodityEngine(BaseAnalyticalModule):
    """Volume 01: Geopolitical & Macro-Commodity Cross-Market Engine."""

    module_id = "v01_geopolitical_commodity"
    module_name = "Geopolitical & Macro-Commodity Cross-Market Engine"
    version = "1.0.0"
    category = "macro_commodities"
    tags = ["geopolitics", "commodities", "spreads", "chokepoints", "cascade", "substitution", "escalation"]
    mathematics_formulas = [
        r"\text{Spread}_t = P_{\text{Asset A}, t} - \beta \cdot P_{\text{Asset B}, t}",
        r"\text{Ratio}_{\text{Corn-Soy}} = \frac{P_{\text{Soybean}}}{P_{\text{Corn}}} \quad (\text{Equilibrium: } [2.2, 2.6])",
        r"\text{Crack}_{\text{3:2:1}} = \frac{2 \cdot P_{\text{Gasoline}} + 1 \cdot P_{\text{Diesel}} - 3 \cdot P_{\text{Crude}}}{3}",
        r"\text{Crush}_{\text{Soy}} = P_{\text{Meal}} \cdot w_{\text{meal}} + P_{\text{Oil}} \cdot w_{\text{oil}} - P_{\text{Soybeans}}",
        r"\text{Cascade Impact}_k = \prod_{i=1}^k \eta_i \cdot \Delta S_{\text{primary}}",
    ]

    # ───────────────────────────────────────────────────────────────────────────
    # 1. Cascade Ripple (Higher-Order Transmission) Analysis
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v01.simulate_cascade_ripple",
        description="Simulate 1st to 4th order supply chain and cross-market cascade ripples from geopolitical/commodity shocks.",
        parameters={
            "shock_type": {"type": "string", "default": "HORMUZ_BLOCKADE", "description": "Primary shock archetype ('HORMUZ_BLOCKADE', 'FERTILIZER_SPIKE', 'NATGAS_CUT')"},
            "shock_magnitude_pct": {"type": "float", "default": 25.0, "description": "Percentage shock magnitude on primary commodity"},
        },
        returns={"cascade_orders": "array", "recommended_trades": "array", "systemic_risk_score": "float"},
        tags=["cascade", "macro", "higher_order"],
    )
    def simulate_cascade_ripple(
        self,
        shock_type: str = "HORMUZ_BLOCKADE",
        shock_magnitude_pct: float = 25.0,
    ) -> Dict[str, Any]:
        """Traces 1st, 2nd, 3rd, and 4th order transmission effects."""
        shock = shock_type.upper()
        mag = float(shock_magnitude_pct) / 100.0

        if "HORMUZ" in shock or "OIL" in shock:
            orders = [
                {
                    "order": 1,
                    "transmission_node": "Crude Oil Transport & Energy",
                    "mechanism": "Strait of Hormuz transit disrupted (~20% global petroleum). Brent/WTI surge.",
                    "estimated_delta_pct": round(mag * 100.0, 1),
                    "impacted_assets": ["BRENT", "WTI", "GASOLINE", "DIESEL"],
                },
                {
                    "order": 2,
                    "transmission_node": "Energy Substitution & Biofuels",
                    "mechanism": "Refineries substitute expensive crude with ethanol blending. Corn demand rallies.",
                    "estimated_delta_pct": round(mag * 0.65 * 100.0, 1),
                    "impacted_assets": ["CORN", "ETHANOL", "SUGAR"],
                },
                {
                    "order": 2,
                    "transmission_node": "Petrochemical Feedstocks (Ammonia/Urea)",
                    "mechanism": "Middle Eastern natural gas & ammonia export cutbacks drive nitrogen fertilizer spike.",
                    "estimated_delta_pct": round(mag * 0.85 * 100.0, 1),
                    "impacted_assets": ["UREA", "AMMONIA", "DAP"],
                },
                {
                    "order": 3,
                    "transmission_node": "Crop Acreage Substitution",
                    "mechanism": "Farmers facing prohibitive fertilizer costs shift planting acreage from Corn to Soybeans (nitrogen fixing).",
                    "estimated_delta_pct": round(-mag * 0.40 * 100.0, 1),
                    "impacted_assets": ["SOYBEANS", "SOYMEAL"],
                },
                {
                    "order": 4,
                    "transmission_node": "Long-Term Market Equilibrium & Feed Margins",
                    "mechanism": "Expanding forward soybean acreage creates supply glut; high feed costs compress livestock/protein margins.",
                    "estimated_delta_pct": round(-mag * 0.50 * 100.0, 1),
                    "impacted_assets": ["LIVE_CATTLE", "LEAN_HOGS", "SOYBEAN_OIL"],
                },
            ]
            trades = [
                {"strategy": "LONG_SHORT_RELATIVE_VALUE", "leg_1": "LONG SOYBEANS", "leg_2": "SHORT CORN", "horizon": "3-6M"},
                {"strategy": "CRACK_SPREAD_EXPANSION", "leg_1": "LONG DIESEL/GASOLINE", "leg_2": "SHORT CRUDE", "horizon": "1-3M"},
                {"strategy": "FEEDSTOCK_HEDGE", "leg_1": "LONG ETHANOL", "leg_2": "SHORT CRUDE", "horizon": "1-2M"},
            ]
        elif "FERTILIZER" in shock:
            orders = [
                {"order": 1, "transmission_node": "Nitrogen / Urea Input", "mechanism": "Haber-Bosch input curtailment spikes fertilizer $480 -> $750/ton.", "estimated_delta_pct": round(mag * 100.0, 1), "impacted_assets": ["UREA", "NATGAS"]},
                {"order": 2, "transmission_node": "Corn Production Breakeven", "mechanism": "Corn production cost per acre rises 35%. Farmers reduce application.", "estimated_delta_pct": round(mag * 0.70 * 100.0, 1), "impacted_assets": ["CORN"]},
                {"order": 3, "transmission_node": "Soybean Planting Shift", "mechanism": "Acreage shift to Soybeans suppresses forward soybean futures.", "estimated_delta_pct": round(-mag * 0.35 * 100.0, 1), "impacted_assets": ["SOYBEANS"]},
            ]
            trades = [
                {"strategy": "CORN_SOY_SPREAD", "leg_1": "LONG CORN", "leg_2": "SHORT SOYBEANS", "horizon": "6M"},
            ]
        else:
            orders = [
                {"order": 1, "transmission_node": "Natural Gas Direct", "mechanism": "Pipeline delivery curtailment spikes TTF/Henry Hub.", "estimated_delta_pct": round(mag * 100.0, 1), "impacted_assets": ["NATGAS_TTF", "HENRY_HUB"]},
                {"order": 2, "transmission_node": "Power Generation Substitution", "mechanism": "Utilities switch from gas to thermal coal and distillate fuel.", "estimated_delta_pct": round(mag * 0.60 * 100.0, 1), "impacted_assets": ["THERMAL_COAL", "DIESEL"]},
            ]
            trades = [
                {"strategy": "SPARK_SPREAD", "leg_1": "LONG POWER", "leg_2": "SHORT GAS", "horizon": "1-3M"},
            ]

        risk_score = min(1.0, 0.35 + mag * 0.60)

        return {
            "shock_type": shock,
            "shock_magnitude_pct": shock_magnitude_pct,
            "cascade_orders": orders,
            "recommended_trades": trades,
            "systemic_risk_score": round(risk_score, 3),
            "status": "CASCADE_SIMULATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. Relative Value & Spread Analysis
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v01.calculate_relative_spreads",
        description="Compute commodity relative value spreads: Corn/Soybean Ratio, 3:2:1 Crack Spread, and Soybean Crush Spread.",
        parameters={
            "corn_price": {"type": "float", "default": 4.50, "description": "Corn price ($/bushel)"},
            "soybean_price": {"type": "float", "default": 11.25, "description": "Soybean price ($/bushel)"},
            "crude_price": {"type": "float", "default": 75.0, "description": "Crude oil price ($/barrel)"},
            "gasoline_price": {"type": "float", "default": 105.0, "description": "Gasoline price ($/barrel)"},
            "diesel_price": {"type": "float", "default": 115.0, "description": "Diesel / Heating oil price ($/barrel)"},
            "soymeal_price": {"type": "float", "default": 350.0, "description": "Soybean meal price ($/ton)"},
            "soyoil_price": {"type": "float", "default": 0.50, "description": "Soybean oil price ($/lb)"},
        },
        returns={"corn_soy_ratio": "float", "crack_321_spread": "float", "soy_crush_spread": "float", "valuation_signals": "object"},
        tags=["spreads", "relative_value", "crack", "crush"],
    )
    def calculate_relative_spreads(
        self,
        corn_price: float = 4.50,
        soybean_price: float = 11.25,
        crude_price: float = 75.0,
        gasoline_price: float = 105.0,
        diesel_price: float = 115.0,
        soymeal_price: float = 350.0,
        soyoil_price: float = 0.50,
    ) -> Dict[str, Any]:
        """Calculates exact mathematical spreads across agricultural and energy commodities."""
        # 1. Corn-Soybean Ratio (Historical norm: 2.2 to 2.6)
        corn_soy_ratio = float(soybean_price) / max(0.01, float(corn_price))
        if corn_soy_ratio < 2.0:
            corn_soy_signal = "SOYBEANS_UNDERVALUED_LONG_SOY_SHORT_CORN"
        elif corn_soy_ratio > 2.8:
            corn_soy_signal = "SOYBEANS_OVERVALUED_LONG_CORN_SHORT_SOY"
        else:
            corn_soy_signal = "EQUILIBRIUM_FAIR_VALUE"

        # 2. 3:2:1 Crack Spread: (2*Gasoline + 1*Diesel - 3*Crude) / 3
        crack_321 = ((2.0 * gasoline_price) + (1.0 * diesel_price) - (3.0 * crude_price)) / 3.0
        refining_margin_signal = "HIGH_REFINING_MARGIN" if crack_321 > 28.0 else ("LOW_REFINING_MARGIN" if crack_321 < 14.0 else "NORMAL_MARGIN")

        # 3. Soybean Crush Spread: (Meal * 0.022) + (Oil * 11.0) - Soybeans
        # 1 bushel of soybeans (~60 lbs) yields ~44 lbs meal (0.022 tons) + 11 lbs oil
        soy_crush = (soymeal_price * 0.022) + (soyoil_price * 11.0) - soybean_price
        crush_margin_signal = "EXPANDED_CRUSH_MARGIN" if soy_crush > 2.0 else ("COMPRESSED_CRUSH_MARGIN" if soy_crush < 0.80 else "NORMAL_CRUSH")

        return {
            "corn_soy_ratio": round(corn_soy_ratio, 3),
            "corn_soy_signal": corn_soy_signal,
            "crack_321_dollar_per_bbl": round(crack_321, 2),
            "refining_margin_signal": refining_margin_signal,
            "soy_crush_dollar_per_bushel": round(soy_crush, 2),
            "crush_margin_signal": crush_margin_signal,
            "historical_bounds": {
                "corn_soy_normal_range": [2.2, 2.6],
                "crack_321_normal_range": [16.0, 26.0],
                "soy_crush_normal_range": [1.0, 1.8],
            },
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. Chokepoint & Bottleneck Vulnerability Analysis
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v01.audit_chokepoint_vulnerability",
        description="Audit maritime and chemical precursor chokepoints against daily transit volumes and substitution elasticity.",
        parameters={
            "chokepoint_name": {"type": "string", "default": "STRAIT_OF_HORMUZ", "description": "Chokepoint name ('STRAIT_OF_HORMUZ', 'MALACCA', 'BAB_EL_MANDEB', 'PANAMA_CANAL', 'HABER_BOSCH_AMMONIA', 'NEON_LITHOGRAPHY')"},
        },
        returns={"daily_transit_volume": "string", "global_share_pct": "float", "substitution_elasticity": "float", "vulnerability_index": "float"},
        tags=["chokepoint", "geopolitics", "supply_chain"],
    )
    def audit_chokepoint_vulnerability(self, chokepoint_name: str = "STRAIT_OF_HORMUZ") -> Dict[str, Any]:
        """Evaluates chokepoint capacity and elasticity."""
        chokepoints = {
            "STRAIT_OF_HORMUZ": {
                "name": "Strait of Hormuz",
                "transit_volume": "21.0 Million bpd Petroleum + 20% Global LNG + Nitrogen Fertilizer",
                "global_share_pct": 21.0,
                "substitution_elasticity": 0.08,  # Near zero bypass capacity
                "vulnerability_index": 0.94,
                "primary_threats": ["Iranian anti-ship missiles", "Naval mine warfare", "Tanker insurance cancellation"],
            },
            "MALACCA": {
                "name": "Strait of Malacca",
                "transit_volume": "16.5 Million bpd Petroleum & Liquefied Gas to East Asia",
                "global_share_pct": 16.5,
                "substitution_elasticity": 0.15,  # Sunda/Lombok detours add 4-6 days sailing
                "vulnerability_index": 0.88,
                "primary_threats": ["Blockade", "Maritime interdiction", "Piracy in Phillips Channel"],
            },
            "BAB_EL_MANDEB": {
                "name": "Bab el-Mandeb & Suez Canal",
                "transit_volume": "8.8 Million bpd Petroleum + 12% Global Container Trade",
                "global_share_pct": 12.0,
                "substitution_elasticity": 0.40,  # Cape of Good Hope reroute (+10-14 days)
                "vulnerability_index": 0.82,
                "primary_threats": ["Houthi anti-ship cruise missiles / drones", "Red Sea insurance hikes"],
            },
            "PANAMA_CANAL": {
                "name": "Panama Canal",
                "transit_volume": "5% Global Maritime Trade (US Grain & LNG to Asia)",
                "global_share_pct": 5.0,
                "substitution_elasticity": 0.50,  # Cape Horn / Rail bridge alternative
                "vulnerability_index": 0.65,
                "primary_threats": ["Freshwater drought in Gatun Lake", "Transit auction draft restrictions"],
            },
            "HABER_BOSCH_AMMONIA": {
                "name": "Haber-Bosch Nitrogen Precursor Network",
                "transit_volume": "180M metric tons/yr ammonia essential for 50% global food protein",
                "global_share_pct": 50.0,
                "substitution_elasticity": 0.05,  # Zero physical synthetic substitute
                "vulnerability_index": 0.96,
                "primary_threats": ["Natural gas price spikes", "European fertilizer plant shutdowns", "Export quotas"],
            },
            "NEON_LITHOGRAPHY": {
                "name": "Neon Gas for DUV Semiconductor Lithography",
                "transit_volume": "Crucial laser buffer gas for advanced silicon wafers",
                "global_share_pct": 45.0,
                "substitution_elasticity": 0.20,
                "vulnerability_index": 0.85,
                "primary_threats": ["Black Sea steel mill byproduct disruption", "Rare gas export controls"],
            },
        }

        key = chokepoint_name.upper().replace(" ", "_")
        item = chokepoints.get(key, chokepoints["STRAIT_OF_HORMUZ"])
        return {
            "chokepoint_key": key,
            "details": item,
            "status": "CHOKEPOINT_AUDITED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Herman Kahn 5-Level Escalation Ladder
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v01.evaluate_escalation_ladder",
        description="Evaluate Herman Kahn 5-level geopolitical escalation ladder and calculate third-party arbitrageur flows.",
        parameters={
            "escalation_level": {"type": "int", "default": 3, "description": "Current escalation level (1 to 5)"},
            "target_country": {"type": "string", "default": "RUSSIA", "description": "Target of sanctions / conflict"},
        },
        returns={"level_label": "string", "market_regime": "string", "arbitrage_hubs": "array", "portfolio_posture": "string"},
        tags=["escalation", "geopolitics", "kahn_ladder"],
    )
    def evaluate_escalation_ladder(
        self,
        escalation_level: int = 3,
        target_country: str = "RUSSIA",
    ) -> Dict[str, Any]:
        """Evaluates systemic market reactions across the Herman Kahn escalation continuum."""
        ladder = {
            1: {"label": "LEVEL 1: Diplomatic Rhetoric & Military Exercises", "regime": "VOLATILITY_PRICED_NO_DISRUPTION", "posture": "SELL_OVERPRICED_OTM_PUTS"},
            2: {"label": "LEVEL 2: Targeted Export Controls & Sectoral Tariffs", "regime": "SELECTIVE_SUPPLY_FRICTION", "posture": "LONG_DOMESTIC_SUBSTITUTES"},
            3: {"label": "LEVEL 3: Asset Freezes & Secondary Financial Sanctions (SWIFT)", "regime": "DE_DOLLARIZATION_CROSS_BORDER_DISCOUNT", "posture": "LONG_PHYSICAL_GOLD_SHORT_TARGET_CURRENCY"},
            4: {"label": "LEVEL 4: Maritime Blockade & Shipping Insurance Blacklist", "regime": "CRITICAL_LOGISTICS_STRANGULATION", "posture": "LONG_CRUDE_LONG_CONTAINER_RATES"},
            5: {"label": "LEVEL 5: Kinetic Infrastructure Strikes & Total Severance", "regime": "SYSTEMIC_CRISIS_SUPPLY_SHOCK", "posture": "MAX_CASH_LONG_COMMODITY_SUPER_SPREAD"},
        }
        lvl = max(1, min(5, int(escalation_level)))
        state = ladder[lvl]

        arbitrage_hubs = [
            {"hub": "UAE / Dubai", "role": "Financial settlement, gold bullion clearing, maritime trading desks"},
            {"hub": "Turkey / Bosphorus", "role": "Grain corridors, re-export of refined products, dual-use electronics transit"},
            {"hub": "India (Reliance/Nayara)", "role": "Refining discounted heavy sour crude into compliant diesel/jet fuel for Europe"},
            {"hub": "Singapore / Malaysia STS", "role": "Ship-to-ship crude blending in Malacca Strait waters"},
        ]

        return {
            "escalation_level": lvl,
            "state_details": state,
            "target_country": target_country.upper(),
            "third_party_arbitrage_hubs": arbitrage_hubs,
            "status": "ESCALATION_EVALUATED",
        }


# Global singleton instance
V01_GEOPOLITICAL_ENGINE = GeopoliticalCommodityEngine()
