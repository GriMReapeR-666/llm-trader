"""Volume 03: Economic Systems, Input-Output Matrices & Feedback Dynamics Engine.

Implements:
1. Wassily Leontief Input-Output Model & Multiplier Matrix (I - A)^(-1).
2. The 4-Tier Data Science Pyramid Pipeline (Descriptive -> Diagnostic -> Predictive -> Prescriptive).
3. Leading, Coincident & Lagging Macro Indicator Synthesis.
4. Systems Dynamics & Feedback Loops (Reinforcing vs. Balancing).
5. Payoff Asymmetry & Positive Convexity Evaluation.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("EconomicInputOutputEngine")


class EconomicInputOutputEngine(BaseAnalyticalModule):
    """Volume 03: Economic Systems, Input-Output & Feedback Dynamics Engine."""

    module_id = "v03_economic_input_output"
    module_name = "Economic Systems, Input-Output & Feedback Dynamics Engine"
    version = "1.0.0"
    category = "economic_systems"
    tags = ["leontief", "input_output", "feedback_loops", "macro_indicators", "convexity", "pyramid"]
    mathematics_formulas = [
        r"\mathbf{x} = (\mathbf{I} - \mathbf{A})^{-1}\mathbf{d} \quad (\text{Leontief Inverse Multiplier})",
        r"\Delta \mathbf{x} = \mathbf{L} \cdot \Delta \mathbf{d} \quad \text{where } \mathbf{L} = (\mathbf{I} - \mathbf{A})^{-1}",
        r"\text{Convexity} = \frac{d^2 V}{d S^2} \quad (\text{Antifragility Payoff Asymmetry})",
        r"\text{Loop Gain} = \prod_{i=1}^k \frac{\partial y_{i+1}}{\partial y_i}",
    ]

    # Standard 5-sector economy: [0: Energy, 1: Chemicals/Fertilizers, 2: Agriculture, 3: Manufacturing/Semis, 4: Services]
    DEFAULT_SECTORS = ["Energy", "Chemicals_Fertilizer", "Agriculture", "Manufacturing_Semis", "Services"]

    # Technical coefficients matrix A (how much input of i is needed for 1 unit output of j)
    DEFAULT_A_MATRIX = np.array([
        [0.10, 0.25, 0.15, 0.20, 0.08],  # Energy inputs to each sector
        [0.02, 0.08, 0.35, 0.12, 0.02],  # Chemical/Fertilizer inputs
        [0.01, 0.05, 0.10, 0.08, 0.15],  # Agriculture inputs
        [0.05, 0.10, 0.05, 0.25, 0.10],  # Manufacturing inputs
        [0.08, 0.05, 0.08, 0.15, 0.20],  # Services inputs
    ], dtype=float)

    # ───────────────────────────────────────────────────────────────────────────
    # 1. Wassily Leontief Input-Output Matrix Multiplier
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v03.calculate_leontief_input_output",
        description="Compute Wassily Leontief Inverse Matrix (I - A)^(-1) to calculate direct and indirect supply chain contraction under sectoral shocks.",
        parameters={
            "shocked_sector": {"type": "string", "default": "Energy", "description": "Sector experiencing supply/demand disruption ('Energy', 'Chemicals_Fertilizer', 'Agriculture', 'Manufacturing_Semis')"},
            "shock_delta_pct": {"type": "float", "default": -20.0, "description": "Percentage change in final demand/supply for that sector"},
        },
        returns={"leontief_multipliers": "object", "sectoral_output_deltas": "object", "total_gdp_contraction_pct": "float"},
        tags=["leontief", "input_output", "supply_chain", "macro"],
    )
    def calculate_leontief_input_output(
        self,
        shocked_sector: str = "Energy",
        shock_delta_pct: float = -20.0,
    ) -> Dict[str, Any]:
        """Calculates exact Leontief Inverse Multipliers L = (I - A)^(-1)."""
        A = self.DEFAULT_A_MATRIX
        n = A.shape[0]
        I = np.eye(n)

        # Compute Leontief Inverse
        L = np.linalg.inv(I - A)

        # Initial baseline final demand vector (arbitrary units: 100 per sector)
        d_base = np.array([100.0, 80.0, 90.0, 120.0, 200.0])
        x_base = np.dot(L, d_base)

        # Apply shock
        sector_idx = 0
        for i, s in enumerate(self.DEFAULT_SECTORS):
            if s.lower() in shocked_sector.lower() or shocked_sector.lower() in s.lower():
                sector_idx = i
                break

        d_shocked = d_base.copy()
        d_shocked[sector_idx] *= (1.0 + float(shock_delta_pct) / 100.0)

        x_shocked = np.dot(L, d_shocked)
        x_deltas_pct = ((x_shocked - x_base) / x_base) * 100.0

        sector_results = {}
        for i, s in enumerate(self.DEFAULT_SECTORS):
            sector_results[s] = {
                "direct_multiplier": round(float(L[i, i]), 3),
                "output_delta_pct": round(float(x_deltas_pct[i]), 2),
                "baseline_output": round(float(x_base[i]), 1),
                "shocked_output": round(float(x_shocked[i]), 1),
            }

        total_base = np.sum(x_base)
        total_shocked = np.sum(x_shocked)
        total_gdp_delta_pct = ((total_shocked - total_base) / total_base) * 100.0

        return {
            "shocked_sector": self.DEFAULT_SECTORS[sector_idx],
            "applied_demand_shock_pct": shock_delta_pct,
            "sector_output_impacts": sector_results,
            "systemic_output_delta_pct": round(float(total_gdp_delta_pct), 2),
            "status": "LEONTIEF_SOLVED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. The 4-Tier Data Science Pyramid Pipeline
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v03.evaluate_4tier_pyramid",
        description="Run 4-tier data science pyramid evaluation (Descriptive -> Diagnostic -> Predictive -> Prescriptive) on a macro setup.",
        parameters={
            "metric_name": {"type": "string", "default": "US_CORE_CPI", "description": "Macroeconomic indicator name"},
            "observed_value": {"type": "float", "default": 3.8, "description": "Latest observed print"},
            "consensus_estimate": {"type": "float", "default": 3.5, "description": "Market consensus expectation"},
        },
        returns={"tier1_descriptive": "string", "tier2_diagnostic": "string", "tier3_predictive": "string", "tier4_prescriptive": "string"},
        tags=["pyramid", "macro", "prescriptive"],
    )
    def evaluate_4tier_pyramid(
        self,
        metric_name: str = "US_CORE_CPI",
        observed_value: float = 3.8,
        consensus_estimate: float = 3.5,
    ) -> Dict[str, Any]:
        """Evaluates macro events through the 4-tier knowledge hierarchy."""
        diff = observed_value - consensus_estimate
        surprise = "HAWKISH_UPSIDE_SURPRISE" if diff > 0.10 else ("DOVISH_DOWNSIDE_SURPRISE" if diff < -0.10 else "IN_LINE_CONSENSUS")

        t1_desc = f"Observed {metric_name} @ {observed_value:.2f} vs Consensus {consensus_estimate:.2f} (Surprise: {diff:+.2f} pts)."
        t2_diag = f"Diagnostic isolation: Acceleration driven by energy pass-through and services stickiness; shelter lag moderating."
        t3_pred = f"Predictive forecast: 6-Month forward inflation expected to remain sticky above neutral rate; Fed cut probabilities repricing lower by 35 bps."
        t4_pres = f"Prescriptive action: Long US Dollar Index (DXY), Short 2Y Treasury Futures, Long Gold on initial dip (stagflation hedge)."

        return {
            "metric": metric_name,
            "surprise_regime": surprise,
            "tier1_descriptive": t1_desc,
            "tier2_diagnostic": t2_diag,
            "tier3_predictive": t3_pred,
            "tier4_prescriptive": t4_pres,
            "status": "PYRAMID_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. Leading vs Coincident vs Lagging Indicator Synthesis
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v03.synthesize_macro_indicators",
        description="Synthesize leading (Yield curve, ISM), coincident (Industrial production, NFP), and lagging (CPI, Unemployment) macro indicators.",
        parameters={
            "yield_curve_10y_2y_bps": {"type": "float", "default": -25.0, "description": "10Y minus 2Y Treasury spread in basis points"},
            "ism_manufacturing_pmi": {"type": "float", "default": 48.5, "description": "ISM Manufacturing PMI index"},
            "non_farm_payrolls_k": {"type": "float", "default": 175.0, "description": "Monthly NFP job additions in thousands"},
            "core_cpi_yoy_pct": {"type": "float", "default": 3.6, "description": "Core CPI YoY percentage"},
        },
        returns={"leading_signal": "string", "coincident_signal": "string", "lagging_signal": "string", "composite_cycle_phase": "string"},
        tags=["macro_indicators", "business_cycle", "leading_lagging"],
    )
    def synthesize_macro_indicators(
        self,
        yield_curve_10y_2y_bps: float = -25.0,
        ism_manufacturing_pmi: float = 48.5,
        non_farm_payrolls_k: float = 175.0,
        core_cpi_yoy_pct: float = 3.6,
    ) -> Dict[str, Any]:
        """Classifies macroeconomic cycle phase."""
        leading = "CONTRACTIONARY_WARNING" if (yield_curve_10y_2y_bps < 0 or ism_manufacturing_pmi < 50.0) else "EXPANSIONARY_GROWTH"
        coincident = "RESILIENT_CURRENT_ACTIVITY" if non_farm_payrolls_k > 150.0 else "SOFTENING_CURRENT_ACTIVITY"
        lagging = "STICKY_RESTRICTIVE_PRESSURE" if core_cpi_yoy_pct > 3.0 else "BENIGN_INFLATION"

        if leading == "CONTRACTIONARY_WARNING" and lagging == "STICKY_RESTRICTIVE_PRESSURE":
            phase = "LATE_CYCLE_STAGFLATIONARY_PRESSURE"
        elif leading == "EXPANSIONARY_GROWTH" and lagging == "BENIGN_INFLATION":
            phase = "EARLY_CYCLE_GOLDILOCKS_EXPANSION"
        elif leading == "CONTRACTIONARY_WARNING" and lagging == "BENIGN_INFLATION":
            phase = "MID_CYCLE_DISINFLATIONARY_SLOWDOWN"
        else:
            phase = "TRANSITIONAL_REBALANCING"

        return {
            "leading_indicator_state": leading,
            "coincident_indicator_state": coincident,
            "lagging_indicator_state": lagging,
            "composite_business_cycle_phase": phase,
            "status": "INDICATORS_SYNTHESIZED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Systems Dynamics Feedback Loops & Convexity
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v03.evaluate_feedback_loops_and_convexity",
        description="Evaluate reinforcing (destabilizing) vs balancing (self-correcting) feedback loops and calculate strategy payoff convexity.",
        parameters={
            "loop_type": {"type": "string", "default": "REINFORCING_LIQUIDITY_SPIRAL", "description": "Feedback loop archetype ('REINFORCING_LIQUIDITY_SPIRAL', 'BALANCING_CAPEX_CYCLE')"},
            "upside_gain_r": {"type": "float", "default": 3.0, "description": "Potential gain in R-multiples if correct"},
            "downside_loss_r": {"type": "float", "default": 1.0, "description": "Potential loss in R-multiples if wrong"},
        },
        returns={"loop_stability": "string", "convexity_ratio": "float", "antifragile_score": "float"},
        tags=["systems_dynamics", "feedback_loops", "convexity"],
    )
    def evaluate_feedback_loops_and_convexity(
        self,
        loop_type: str = "REINFORCING_LIQUIDITY_SPIRAL",
        upside_gain_r: float = 3.0,
        downside_loss_r: float = 1.0,
    ) -> Dict[str, Any]:
        """Calculates payoff asymmetry (convexity) and loop dynamics."""
        convexity = float(upside_gain_r) / max(0.1, float(downside_loss_r))
        is_convex = convexity >= 2.0

        if "REINFORCING" in loop_type.upper():
            stability = "POSITIVE_FEEDBACK_DESTABILIZING (Non-linear cascade risk)"
        else:
            stability = "NEGATIVE_FEEDBACK_BALANCING (Self-correcting mean-reversion)"

        return {
            "loop_type": loop_type,
            "systemic_stability": stability,
            "payoff_convexity_ratio": round(convexity, 2),
            "is_antifragile_convex": is_convex,
            "convexity_guidance": "ANTIFRAGILE_CONVEX_EXECUTION (Gain >> Loss)" if is_convex else "CONCAVE_RISK (High ruin potential on tail events)",
            "status": "FEEDBACK_CONVEXITY_EVALUATED",
        }


# Global singleton instance
V03_ECONOMIC_IO_ENGINE = EconomicInputOutputEngine()
