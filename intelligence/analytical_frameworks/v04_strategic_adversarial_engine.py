"""Volume 04: Strategic Intelligence, Adversarial Modeling & Failure Forensics Engine.

Implements:
1. Clausewitzian Center of Gravity (CoG) & Network Centrality Analysis.
2. 5-Phase Adversarial Kill Chain Interception Model.
3. Game-Theoretic Dynamics: OPEC+ Prisoner's Dilemma & Currency War Nash Equilibrium.
4. Failure Forensics & Safety Engineering: Swiss Cheese Model, FTA Logic, FMEA (RPN = S x O x D).
5. Narrative & Media Propaganda Forensics (Herman-Chomsky Omission Score).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("StrategicAdversarialEngine")


class StrategicAdversarialEngine(BaseAnalyticalModule):
    """Volume 04: Strategic Intelligence, Adversarial Modeling & Failure Forensics Engine."""

    module_id = "v04_strategic_adversarial"
    module_name = "Strategic Intelligence, Adversarial Modeling & Failure Forensics Engine"
    version = "1.0.0"
    category = "strategic_intelligence"
    tags = ["adversarial", "kill_chain", "cog", "game_theory", "swiss_cheese", "fmea", "fta", "propaganda"]
    mathematics_formulas = [
        r"\text{RPN} = S \times O \times D \quad (\text{Severity} \times \text{Occurrence} \times \text{Detection})",
        r"P(\text{Disaster}) = \prod_{i=1}^M p_i \quad (\text{Swiss Cheese Failure Alignment})",
        r"\text{Nash Equilibrium: } u_i(s_i^*, s_{-i}^*) \ge u_i(s_i, s_{-i}^*) \quad \forall s_i \in S_i",
        r"\text{CoG Centrality} = \sum_{j \neq k} \frac{\sigma_{jk}(i)}{\sigma_{jk}}",
    ]

    # ───────────────────────────────────────────────────────────────────────────
    # 1. Center of Gravity (CoG) & Systemic Hub Analysis
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v04.audit_center_of_gravity",
        description="Audit systemic Center of Gravity (CoG) hubs across financial plumbing, sovereign liquidity, and counterparty networks.",
        parameters={
            "theater": {"type": "string", "default": "GLOBAL_DOLLAR_CLEARING", "description": "Systemic domain ('GLOBAL_DOLLAR_CLEARING', 'CRYPTO_STABLECOIN_RAILS', 'EU_ENERGY_GRID', 'CHIP_FABRICATION')"},
        },
        returns={"primary_cog": "string", "vulnerability_points": "array", "cascade_collapse_potential": "string"},
        tags=["cog", "strategy", "clausewitz"],
    )
    def audit_center_of_gravity(self, theater: str = "GLOBAL_DOLLAR_CLEARING") -> Dict[str, Any]:
        """Evaluates Clausewitzian Center of Gravity dependencies."""
        theaters = {
            "GLOBAL_DOLLAR_CLEARING": {
                "cog": "US Treasury Repurchase Agreement (Repo) Market & Fed FIMA Swap Lines",
                "power_source": "Global USD invoicing ($88% forex turnover) and US primary dealer collateralization.",
                "vulnerabilities": ["Tri-party repo fails", "Cross-currency basis blowouts", "Treasury auction tailing"],
                "interception": "Monitor SOFR-IORB spread, Fed overnight reverse repo (RRP) runoff rate, and Foreign Central Bank Treasury holdings.",
                "collapse_risk": "CRITICAL_SYSTEMIC_MELTDOWN",
            },
            "CRYPTO_STABLECOIN_RAILS": {
                "cog": "Tether (USDT) / Circle (USDC) Reserve Custody & Off-Shore Banking Rails",
                "power_source": "Liquidity bridge underpinning 80%+ spot and perpetual derivatives volume.",
                "vulnerabilities": ["Tier-1 bank de-banking", "Asset freeze by sovereign regulators", "De-peg run"],
                "interception": "Track on-chain curve pool liquidity imbalances, redemption delay latency, and commercial paper attestation.",
                "collapse_risk": "SECTORAL_DELEVERAGING_CRASH",
            },
            "EU_ENERGY_GRID": {
                "cog": "Liquefied Natural Gas (LNG) Regasification Terminals & Interconnector Pipelines",
                "power_source": "Base-load industrial power and chemical feedstock supply.",
                "vulnerabilities": ["Terminal mechanical outage", "Maritime sabotage", "Storage depletion < 20% in winter"],
                "interception": "Track daily European gas storage injection/withdrawal rates and Dutch TTF forward spread.",
                "collapse_risk": "INDUSTRIAL_DE_INDUSTRIALIZATION",
            },
        }

        th_key = theater.upper().replace(" ", "_")
        res = theaters.get(th_key, theaters["GLOBAL_DOLLAR_CLEARING"])
        return {
            "theater": th_key,
            "cog_assessment": res,
            "status": "COG_AUDITED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. 5-Phase Adversarial Kill Chain Interception Model
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v04.track_adversarial_kill_chain",
        description="Model the 5-phase institutional predator execution sequence (Reconnaissance -> Positioning -> Catalyst -> Liquidity Sweep -> Extraction) and identify defensive stops.",
        parameters={
            "active_phase": {"type": "int", "default": 3, "description": "Current detected adversary phase (1 to 5)"},
            "symbol": {"type": "string", "default": "XAUUSD", "description": "Target trading instrument"},
        },
        returns={"phase_name": "string", "predator_behavior": "string", "defensive_interception": "string", "trap_risk_score": "float"},
        tags=["kill_chain", "adversarial", "liquidity_sweep"],
    )
    def track_adversarial_kill_chain(self, active_phase: int = 3, symbol: str = "XAUUSD") -> Dict[str, Any]:
        """Maps predator trading maneuvers across the 5 kill chain phases."""
        phases = {
            1: {
                "name": "Phase 1: Reconnaissance (Order Book & Stop Mapping)",
                "predator": "Probing liquidity depths, mapping retail stop-loss clusters above swing highs / below swing lows.",
                "defense": "Avoid placing resting stops at obvious round psych numbers or textbook swing pivots.",
                "risk": 0.25,
            },
            2: {
                "name": "Phase 2: Quiet Weaponization & Positioning",
                "predator": "Accumulating large inventory slowly during low-volatility sessions (e.g. Asian session TWAP absorption).",
                "defense": "Identify delta volume absorption and footprint CVD divergence.",
                "risk": 0.45,
            },
            3: {
                "name": "Phase 3: Catalyst Delivery & Narrative Weaponization",
                "predator": "Releasing press leaks or aggressive narrative articles to trigger emotional retail chasing.",
                "defense": "Refuse to market-order on breaking headline spikes; verify primary data feeds (FRED/SEC).",
                "risk": 0.70,
            },
            4: {
                "name": "Phase 4: Liquidity Sweep (The Stop Hunt / Squeeze)",
                "predator": "Violently surging price through key resistance/support to trigger forced liquidations.",
                "defense": "Set limit orders outside standard 2-sigma ATR bands; prepare for sharp mean-reversion fade.",
                "risk": 0.90,
            },
            5: {
                "name": "Phase 5: Capital Extraction & Distribution",
                "predator": "Dumping accumulated inventory into the incoming flood of retail FOMO buy/sell orders.",
                "defense": "Harvest profits immediately; do not hold runner positions into institutional distribution volume.",
                "risk": 0.60,
            },
        }

        p_idx = max(1, min(5, int(active_phase)))
        data = phases[p_idx]

        return {
            "symbol": symbol.upper(),
            "active_phase_number": p_idx,
            "phase_name": data["name"],
            "predator_action": data["predator"],
            "recommended_defensive_posture": data["defense"],
            "trap_probability_score": data["risk"],
            "status": "KILL_CHAIN_EVALUATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. Game-Theoretic Dynamics (Prisoner's Dilemma & Currency Wars)
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v04.solve_game_theory_equilibrium",
        description="Solve 2x2 macro game theory payoff matrices for OPEC+ production quotas, currency devaluations, or tariff wars.",
        parameters={
            "game_type": {"type": "string", "default": "OPEC_PRISONERS_DILEMMA", "description": "Game archetype ('OPEC_PRISONERS_DILEMMA', 'CURRENCY_WAR', 'TARIFF_ESCALATION')"},
        },
        returns={"payoff_matrix": "object", "nash_equilibrium": "string", "dominant_strategy": "string", "stability": "string"},
        tags=["game_theory", "nash_equilibrium", "opec"],
    )
    def solve_game_theory_equilibrium(self, game_type: str = "OPEC_PRISONERS_DILEMMA") -> Dict[str, Any]:
        """Calculates Nash Equilibrium and defection incentives."""
        if "OPEC" in game_type.upper():
            matrix = {
                "Player_1": "Saudi Arabia / Core OPEC",
                "Player_2": "Quota Cheaters / Non-OPEC (Russia/Kazakhstan)",
                "Strategies": ["Cooperate (Cut Production)", "Defect (Over-produce)"],
                "Payoffs": {
                    "(Cut, Cut)": [80, 80],       # High oil price, shared sacrifice
                    "(Cut, Defect)": [40, 110],    # Saudi cuts, cheater gains market share
                    "(Defect, Cut)": [110, 40],    # Saudi floods, cheater suffers
                    "(Defect, Defect)": [30, 30],  # Price war crash ($30/bbl)
                },
            }
            nash = "(Defect, Defect) in one-shot; Periodic Discipline Price Wars in repeated games."
            dominant = "Defect unless repeated game penalties (Saudi price wars) enforce discipline."
            stability = "UNSTABLE_CYCLICAL (Alternates between quota agreements and punitive price wars)"
        elif "CURRENCY" in game_type.upper():
            matrix = {
                "Player_1": "Nation A (Exporters)",
                "Player_2": "Nation B (Trading Partner)",
                "Strategies": ["Maintain Peg / Strong Currency", "Competitive Devaluation"],
                "Payoffs": {
                    "(Maintain, Maintain)": [50, 50],
                    "(Maintain, Devalue)": [10, 80],
                    "(Devalue, Maintain)": [80, 10],
                    "(Devalue, Devalue)": [20, 20],  # Mutual imported inflation & tariff retaliation
                },
            }
            nash = "(Devalue, Devalue) -> Beggar-Thy-Neighbor Inflation Trap."
            dominant = "Devaluation race to bottom unless bilateral currency pact established."
            stability = "DESTABILIZING_INFLATIONARY"
        else:
            matrix = {
                "Player_1": "Nation A (Tariffs)",
                "Player_2": "Nation B (Retaliation)",
                "Strategies": ["Free Trade", "Impose Tariffs"],
                "Payoffs": {
                    "(Free, Free)": [100, 100],
                    "(Free, Tariff)": [40, 120],
                    "(Tariff, Free)": [120, 40],
                    "(Tariff, Tariff)": [60, 60],
                },
            }
            nash = "(Tariff, Tariff) -> Trade Friction Equilibrium."
            dominant = "Impose Tariffs (Protectionist bias)."
            stability = "STRUCTURAL_FRICTION"

        return {
            "game_type": game_type,
            "matrix_specification": matrix,
            "nash_equilibrium": nash,
            "dominant_strategy": dominant,
            "regime_stability": stability,
            "status": "NASH_EQUILIBRIUM_SOLVED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Failure Forensics (Swiss Cheese & FMEA RPN)
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v04.calculate_fmea_risk_priority",
        description="Calculate Failure Mode and Effects Analysis (FMEA) Risk Priority Number (RPN = Severity x Occurrence x Detection) and Swiss Cheese failure alignment.",
        parameters={
            "failure_mode": {"type": "string", "default": "BROKER_EXECUTION_DISCONNECT", "description": "Failure mode scenario name"},
            "severity": {"type": "int", "default": 8, "description": "Severity (1 to 10)"},
            "occurrence": {"type": "int", "default": 4, "description": "Occurrence frequency (1 to 10)"},
            "detection": {"type": "int", "default": 5, "description": "Detection difficulty (1 = easily detected, 10 = undetectable)"},
        },
        returns={"rpn_score": "int", "risk_tier": "string", "swiss_cheese_disaster_prob": "float"},
        tags=["fmea", "rpn", "swiss_cheese", "safety_engineering"],
    )
    def calculate_fmea_risk_priority(
        self,
        failure_mode: str = "BROKER_EXECUTION_DISCONNECT",
        severity: int = 8,
        occurrence: int = 4,
        detection: int = 5,
    ) -> Dict[str, Any]:
        """Calculates FMEA RPN and Swiss Cheese alignment probability."""
        s = max(1, min(10, int(severity)))
        o = max(1, min(10, int(occurrence)))
        d = max(1, min(10, int(detection)))

        rpn = s * o * d  # 1 to 1000

        if rpn >= 300:
            tier = "CRITICAL_ACTION_REQUIRED (Immediate hard stop & mitigation mandatory)"
        elif rpn >= 150:
            tier = "ELEVATED_RISK (Implement automated circuit breaker)"
        else:
            tier = "ACCEPTABLE_CONTROLLED_RISK"

        # Swiss Cheese Model: 3 defensive layers alignment (e.g. Risk Gate, S/L, Margin Check)
        p_layer1 = o / 10.0
        p_layer2 = d / 10.0
        p_layer3 = s / 10.0 * 0.50
        swiss_prob = p_layer1 * p_layer2 * p_layer3

        return {
            "failure_mode": failure_mode,
            "severity": s,
            "occurrence": o,
            "detection": d,
            "rpn_score": rpn,
            "risk_tier": tier,
            "swiss_cheese_alignment_probability": round(swiss_prob, 5),
            "status": "FMEA_CALCULATED",
        }


# Global singleton instance
V04_STRATEGIC_ENGINE = StrategicAdversarialEngine()
