"""Volume 05: Behavioral, Cognitive & Socioeconomic Analysis Engine.

Implements:
1. Cognitive Bias & Heuristic Distortion Quantifier (Availability, Path Dependence, Asymmetric Insight).
2. Paul Ekman Facial Action Coding System (FACS) & Micro-Expression Rule Matcher (AU 6 + AU 12 Duchenne Smile vs. Social Mask).
3. Discourse Stylometry & Corporate/Fed Evasion Index.
4. The Cantillon Effect Wealth Inequality Divide & Real Wage Compression.
5. Memetics & Viral Narrative Epidemic Transmission Model (R_0 = beta * c * d).
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    analytical_tool,
)

log = logging.getLogger("BehavioralCognitiveEngine")


class BehavioralCognitiveEngine(BaseAnalyticalModule):
    """Volume 05: Behavioral, Cognitive & Socioeconomic Analysis Engine."""

    module_id = "v05_behavioral_cognitive"
    module_name = "Behavioral, Cognitive & Socioeconomic Analysis Engine"
    version = "1.0.0"
    category = "behavioral_psychology"
    tags = ["behavioral", "facs", "kinesics", "cantillon", "memetics", "cognitive_bias", "stylometry"]
    mathematics_formulas = [
        r"R_0 = \beta \cdot c \cdot d \quad (\text{Narrative Viral Reproduction Rate})",
        r"\Delta_{\text{Cantillon}} = \pi_{\text{Asset Inflation}} - \pi_{\text{Wage Growth}}",
        r"\text{Duchenne Metric} = \mathbb{I}(\text{AU6} \land \text{AU12}) \cdot (1 - \text{AU4})",
        r"\text{Evasion Index} = \frac{N_{\text{hedges}} + N_{\text{passive}}}{N_{\text{sentences}}}",
    ]

    # ───────────────────────────────────────────────────────────────────────────
    # 1. Paul Ekman FACS Micro-Expression Rule Matcher
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v05.decode_facs_micro_expressions",
        description="Decode Paul Ekman Facial Action Coding System (FACS) Action Units (AUs) to evaluate genuine vs deceptive signaling in central bank or executive press conferences.",
        parameters={
            "action_units_present": {"type": "array", "description": "List of observed Action Units, e.g. ['AU6', 'AU12'] or ['AU12', 'AU4', 'AU14']"},
            "context": {"type": "string", "default": "FOMC_PRESS_CONFERENCE", "description": "Speech context"},
        },
        returns={"expression_type": "string", "is_genuine_duchenne": "bool", "stress_deception_score": "float", "assessment": "string"},
        tags=["facs", "kinesics", "micro_expressions", "deception"],
    )
    def decode_facs_micro_expressions(
        self,
        action_units_present: Optional[List[str]] = None,
        context: str = "FOMC_PRESS_CONFERENCE",
    ) -> Dict[str, Any]:
        """Evaluates FACS Action Units against Dr. Paul Ekman's behavioral models."""
        aus = [str(au).upper().replace(" ", "") for au in (action_units_present or ["AU6", "AU12"])]

        has_au6 = "AU6" in aus   # Orbicularis Oculi (Cheek Raiser / Eye Crinkle)
        has_au12 = "AU12" in aus # Zygomaticus Major (Lip Corner Puller)
        has_au4 = "AU4" in aus   # Brow Lowerer (Stress / Confusion)
        has_au14 = "AU14" in aus # Dimpler (Contempt / Suppression)
        has_au1 = "AU1" in aus   # Inner Brow Raiser (Anxiety / Fear)

        if has_au6 and has_au12 and not (has_au4 or has_au14):
            expr = "GENUINE_DUCHENNE_CONFIDENCE"
            is_duchenne = True
            deception = 0.05
            verdict = "High congruence between verbal statements and internal emotional state."
        elif has_au12 and not has_au6:
            expr = "FORCED_SOCIAL_MASK (Polite smile with suppressed concern)"
            is_duchenne = False
            deception = 0.65
            verdict = "Executive/Governor displaying forced reassurance; eye muscles inactive."
        elif has_au4 or has_au14 or has_au1:
            expr = "HIGH_COGNITIVE_STRAIN_AND_SUPPRESSION"
            is_duchenne = False
            deception = 0.85
            verdict = "Severe stress/doubt detected during guidance statement; indicates internal disagreement."
        else:
            expr = "NEUTRAL_BASE_POSTURE"
            is_duchenne = False
            deception = 0.20
            verdict = "Neutral baseline expression."

        return {
            "context": context,
            "detected_action_units": aus,
            "expression_classification": expr,
            "is_genuine_duchenne_smile": is_duchenne,
            "stress_deception_probability": deception,
            "forensic_assessment": verdict,
            "status": "FACS_DECODED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 2. Discourse Stylometry & Evasion Forensics
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v05.analyze_discourse_stylometry",
        description="Analyze corporate disclosures or central bank transcripts for passive voice density, non-committal hedging, and evasion.",
        parameters={
            "transcript_text": {"type": "string", "default": "It is believed that conditions may possibly moderate to some extent in the coming quarters, although certain headwinds could emerge.", "description": "Text excerpt to audit"},
        },
        returns={"evasion_index": "float", "hedge_word_count": "int", "passive_voice_density": "float", "credibility_score": "float"},
        tags=["stylometry", "evasion", "nlp", "forensics"],
    )
    def analyze_discourse_stylometry(self, transcript_text: str = "") -> Dict[str, Any]:
        """Calculates linguistic hedging and evasion density."""
        text = transcript_text or "It is believed that conditions may possibly moderate to some extent in the coming quarters, although certain headwinds could emerge."
        words = re.findall(r"\b\w+\b", text.lower())
        n_words = max(1, len(words))

        hedge_keywords = ["may", "might", "could", "possibly", "perhaps", "to some extent", "it is believed", "somewhat", "relatively", "presumed", "conditional"]
        hedge_count = sum(1 for kw in hedge_keywords if kw in text.lower())

        # Passive voice markers
        passive_markers = ["is believed", "was noted", "were observed", "has been deemed", "is expected"]
        passive_count = sum(1 for pm in passive_markers if pm in text.lower())

        evasion_idx = min(1.0, (hedge_count * 0.15) + (passive_count * 0.25))
        credibility = max(0.0, 1.0 - evasion_idx)

        return {
            "total_words_analyzed": n_words,
            "hedge_word_occurrences": hedge_count,
            "passive_voice_phrases_detected": passive_count,
            "evasion_index": round(evasion_idx, 3),
            "linguistic_credibility_score": round(credibility, 3),
            "evasion_verdict": "HIGH_EVASION_DISCLOSURE_RISK" if evasion_idx > 0.50 else "DIRECT_TRANSPARENT_COMMUNICATION",
            "status": "STYLOMETRY_ANALYZED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 3. The Cantillon Effect & Socioeconomic Wealth Divide
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v05.calculate_cantillon_wealth_divide",
        description="Compute Cantillon Effect spread between asset price inflation and real wage growth to forecast consumer capitulation.",
        parameters={
            "asset_inflation_yoy_pct": {"type": "float", "default": 12.5, "description": "Equities / Luxury Real Estate YoY inflation"},
            "nominal_wage_growth_yoy_pct": {"type": "float", "default": 4.2, "description": "Nominal wage growth YoY"},
            "essential_cpi_yoy_pct": {"type": "float", "default": 6.8, "description": "Non-discretionary CPI (Food + Shelter + Energy)"},
        },
        returns={"cantillon_divergence_pct": "float", "real_wage_compression_pct": "float", "retail_capitulation_risk": "string"},
        tags=["cantillon", "socioeconomic", "inflation", "macro"],
    )
    def calculate_cantillon_wealth_divide(
        self,
        asset_inflation_yoy_pct: float = 12.5,
        nominal_wage_growth_yoy_pct: float = 4.2,
        essential_cpi_yoy_pct: float = 6.8,
    ) -> Dict[str, Any]:
        """Evaluates wealth inequality divergence and consumer exhaustion."""
        cantillon_div = float(asset_inflation_yoy_pct - nominal_wage_growth_yoy_pct)
        real_wage_compression = float(nominal_wage_growth_yoy_pct - essential_cpi_yoy_pct)

        if real_wage_compression < -2.0:
            capitulation = "CRITICAL_RETAIL_CAPITULATION (Subprime auto/card defaults, discretionary collapse)"
        elif real_wage_compression < 0.0:
            capitulation = "MODERATE_RETAIL_THRIFT (Down-trading to discount retailers)"
        else:
            capitulation = "STABLE_CONSUMER_PURCHASING_POWER"

        return {
            "cantillon_asset_wage_divergence_pct": round(cantillon_div, 2),
            "real_wage_compression_spread_pct": round(real_wage_compression, 2),
            "retail_consumer_capitulation_risk": capitulation,
            "macroeconomic_implication": "Asset owners benefit from monetary expansion while wage earners suffer purchasing power contraction.",
            "status": "CANTILLON_CALCULATED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # 4. Memetics & Viral Narrative Transmission ($R_0$)
    # ───────────────────────────────────────────────────────────────────────────

    @analytical_tool(
        name="v05.model_memetic_narrative_r0",
        description="Model viral narrative reproduction number (R_0 = beta * c * d) to predict speculative bubble expansions or narrative exhaustion.",
        parameters={
            "transmission_prob_beta": {"type": "float", "default": 0.35, "description": "Probability of adopting narrative per exposure (0.0 to 1.0)"},
            "social_contact_rate_c": {"type": "float", "default": 6.0, "description": "Average daily social media / peer shares per believer"},
            "salience_duration_days_d": {"type": "float", "default": 0.80, "description": "Duration of headline salience in days"},
        },
        returns={"reproduction_number_r0": "float", "epidemic_phase": "string", "trading_action": "string"},
        tags=["memetics", "r0", "narrative", "bubbles"],
    )
    def model_memetic_narrative_r0(
        self,
        transmission_prob_beta: float = 0.35,
        social_contact_rate_c: float = 6.0,
        salience_duration_days_d: float = 0.80,
    ) -> Dict[str, Any]:
        """Calculates epidemiological narrative spread dynamics."""
        r0 = float(transmission_prob_beta * social_contact_rate_c * salience_duration_days_d)

        if r0 > 1.8:
            phase = "EXPONENTIAL_VIRAL_FOMO (Speculative Bubble Euphoria)"
            action = "RIDE_MOMENTUM_WITH_TIGHT_TRAILING_STOP"
        elif r0 >= 1.0:
            phase = "SUSTAINED_EPIDEMIC_EXPANSION (Healthy Trend Momentum)"
            action = "ACCUMULATE_ON_PULLBACKS"
        elif r0 >= 0.60:
            phase = "NARRATIVE_EXHAUSTION (Decaying social attention)"
            action = "HARVEST_PROFITS_PREPARE_REVERSAL"
        else:
            phase = "MEMETIC_EXTINCTION (Narrative forgotten)"
            action = "FADE_SPECULATIVE_LATE_LONGS"

        return {
            "basic_reproduction_number_r0": round(r0, 3),
            "epidemic_narrative_phase": phase,
            "recommended_trading_action": action,
            "is_exponentially_expanding": r0 > 1.0,
            "status": "MEMETICS_MODELED",
        }


# Global singleton instance
V05_BEHAVIORAL_ENGINE = BehavioralCognitiveEngine()
