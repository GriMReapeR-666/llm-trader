"""Module 02: Geopolitical Macro Foresight, Scenario Planning & Early Warning Systems.

Implements strategic foresight, dynamic scenario matrices, early warning tripwires, and Bayesian Model Averaging:
1. COMPASS 7 Strategic Bearings Evaluation:
       Culture, Organization, Military, Production, Alliances, Spatial, Sustainability
2. Caldara-Iacoviello Geopolitical Risk (GPR) Index Quantification:
       Threat vs. Action Divergence Detector
3. SIPRI 8-Domain Early Warning System (EWS):
       Tripwire Monitoring across Justice, Socio-Cultural, Security, Geopolitical,
       Military, Environment, Governance, Socio-Economic
4. Yield Curve Inversion 4-Stage Causal Transmission Mechanics:
       Spread (10Y - 2Y) -> Bank NIM -> SLOOS Lending Standards -> Real Contraction -> Recession
5. 2x2 Dynamic Scenario Planning Matrix Formulation
6. Bayesian Model Averaging (BMA) across 5 Geopolitical Regimes (A1–A5):
       w_i(t) proportional to w_i(t-1)^lambda * exp(sum_k l(s_k | A_i))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np


class GeopoliticalRegime(Enum):
    LIBERAL_ORDER = "liberal_order"              # A1: Rules-based trade, globalization, USD dominance
    MULTIPOLAR = "multipolar"                    # A2: Regional trading blocs, currency diversification
    BIPOLAR = "bipolar"                          # A3: US-China tech decoupling, security primacy
    FRAGMENTATION = "fragmentation"              # A4: Protectionism, resource nationalism, supply crises
    HEGEMONIC_TRANSITION = "hegemonic_transition"# A5: Power parity, high conflict probability, volatility


@dataclass(frozen=True)
class GeopoliticalSignal:
    timestamp: str
    domain: str          # 'military', 'economic', 'diplomatic', 'resource', 'financial'
    indicator: str
    value: float
    direction: str       # 'escalating', 'de-escalating', 'stable'
    confidence: float    # 0.0 to 1.0
    source: str


@dataclass
class COMPASSScore:
    culture: float       # 0 to 100 risk score
    organization: float
    military: float
    production: float
    alliances: float
    spatial: float
    sustainability: float

    @property
    def composite_risk(self) -> float:
        return float(np.mean([
            self.culture, self.organization, self.military,
            self.production, self.alliances, self.spatial, self.sustainability
        ]))


def calculate_compass_strategic_bearings(
    culture: float,
    organization: float,
    military: float,
    production: float,
    alliances: float,
    spatial: float,
    sustainability: float,
) -> Dict[str, Any]:
    """Calculates the COMPASS 7 Strategic Bearings composite risk and critical vulnerability."""
    scores = {
        "culture": float(max(0.0, min(100.0, culture))),
        "organization": float(max(0.0, min(100.0, organization))),
        "military": float(max(0.0, min(100.0, military))),
        "production": float(max(0.0, min(100.0, production))),
        "alliances": float(max(0.0, min(100.0, alliances))),
        "spatial": float(max(0.0, min(100.0, spatial))),
        "sustainability": float(max(0.0, min(100.0, sustainability))),
    }

    comp = COMPASSScore(**scores)
    critical_bearing = max(scores, key=scores.get)

    return {
        "bearings": scores,
        "composite_risk": round(comp.composite_risk, 2),
        "critical_vulnerability_bearing": critical_bearing,
        "critical_vulnerability_score": scores[critical_bearing],
        "strategic_status": "HIGH_ALERT" if comp.composite_risk >= 70.0 else ("ELEVATED" if comp.composite_risk >= 45.0 else "STABLE"),
    }


def calculate_caldara_iacoviello_gpr_divergence(
    gpr_threat_index: float,
    gpr_action_index: float,
    baseline_threat_mean: float = 100.0,
    baseline_threat_std: float = 15.0,
    baseline_action_mean: float = 100.0,
    baseline_action_std: float = 15.0,
) -> Dict[str, Any]:
    """Quantifies Caldara-Iacoviello Geopolitical Risk (GPR) Index and detects Threat vs. Action Divergence.
    - Threat >> Action: Market underpricing future disruption -> Accumulate Volatility & Gold Convexity
    - Action >= Threat: Event realized -> Mean-Reversion / Risk-On Entry Signal
    """
    gpr_t = float(gpr_threat_index)
    gpr_a = float(gpr_action_index)
    mu_t = float(baseline_threat_mean)
    sigma_t = max(1e-6, float(baseline_threat_std))
    mu_a = float(baseline_action_mean)
    sigma_a = max(1e-6, float(baseline_action_std))

    z_threat = (gpr_t - mu_t) / sigma_t
    z_action = (gpr_a - mu_a) / sigma_a

    if z_threat >= 2.0 and z_threat > z_action + 1.0:
        regime = "UNDERPRICED_THREAT_ACCUMULATE_CONVEXITY"
        allocation_bias = "LONG_VOL_GOLD_DEFENSE"
    elif z_action >= 2.0 and z_action >= z_threat:
        regime = "EVENT_REALIZED_MEAN_REVERSION_RISK_ON"
        allocation_bias = "RISK_ON_EQUITIES_SELL_NEWS"
    elif z_threat >= 1.5 or z_action >= 1.5:
        regime = "ELEVATED_GEOPOLITICAL_TENSION"
        allocation_bias = "BALANCED_HEDGED"
    else:
        regime = "BENIGN_GEOPOLITICAL_ENVIRONMENT"
        allocation_bias = "NORMAL_BETA"

    return {
        "gpr_threat": gpr_t,
        "gpr_action": gpr_a,
        "z_threat": round(float(z_threat), 3),
        "z_action": round(float(z_action), 3),
        "regime": regime,
        "allocation_bias": allocation_bias,
    }


def evaluate_sipri_eight_domain_early_warning_system(
    domain_scores: Dict[str, float],
    warning_threshold: float = 70.0,
) -> Dict[str, Any]:
    """Evaluates SIPRI 8-Domain Early Warning System (EWS).
    Domains:
      1. Justice & Rule of Law
      2. Socio-Cultural
      3. Internal Security
      4. Geopolitical Setting
      5. Military & Security
      6. Environment & Resources
      7. Governance & State Capacity
      8. Socio-Economic
    """
    valid_domains = {
        "justice_rule_of_law",
        "socio_cultural",
        "internal_security",
        "geopolitical_setting",
        "military_security",
        "environment_resources",
        "governance_capacity",
        "socio_economic",
    }

    scores = {}
    tripwires = []
    for d in valid_domains:
        val = float(domain_scores.get(d, 0.0))
        scores[d] = val
        if val >= warning_threshold:
            tripwires.append(d)

    mean_score = float(np.mean(list(scores.values())))
    n_triggered = len(tripwires)

    if n_triggered >= 4 or mean_score >= 75.0:
        escalation_tier = "CRITICAL"
    elif n_triggered >= 2 or mean_score >= 60.0:
        escalation_tier = "WARNING"
    elif n_triggered >= 1 or mean_score >= 45.0:
        escalation_tier = "WATCH"
    else:
        escalation_tier = "NORMAL"

    return {
        "domain_scores": scores,
        "mean_risk_score": round(mean_score, 2),
        "triggered_tripwires": tripwires,
        "num_tripwires_triggered": n_triggered,
        "escalation_tier": escalation_tier,
    }


def evaluate_yield_curve_recession_transmission(
    yield_10y: float,
    yield_2y: float,
    sloos_tightening_net_pct: float = 20.0,
    months_since_inversion: int = 0,
) -> Dict[str, Any]:
    """Evaluates the 4-stage causal transmission of Yield Curve Inversion to NBER Recession:
    10Y - 2Y Inversion -> Bank NIM Compression -> SLOOS Lending Standard Tightening -> Real Contraction -> Recession
    """
    y10 = float(yield_10y)
    y2 = float(yield_2y)
    spread = y10 - y2
    sloos = float(sloos_tightening_net_pct)
    m_inv = max(0, int(months_since_inversion))

    is_inverted = spread < 0.0

    # Stage evaluation
    if not is_inverted and m_inv == 0:
        stage = "STAGE_0_NORMAL_EXPANSION"
        recession_prob = 0.10
    elif is_inverted and sloos < 30.0:
        stage = "STAGE_1_INVERSION_AND_NIM_COMPRESSION"
        recession_prob = 0.40
    elif sloos >= 30.0 and m_inv < 12:
        stage = "STAGE_2_SLOOS_CREDIT_TIGHTENING"
        recession_prob = 0.65
    elif m_inv >= 12 and m_inv <= 24:
        stage = "STAGE_3_REAL_ACTIVITY_CONTRACTION_IMMOBILIZATION"
        recession_prob = 0.85
    else:
        stage = "STAGE_4_NBER_RECESSION_OR_RECOVERY"
        recession_prob = 0.90 if is_inverted or sloos >= 40.0 else 0.30

    return {
        "yield_10y": y10,
        "yield_2y": y2,
        "spread_bps": round(spread * 100.0, 1),
        "is_inverted": is_inverted,
        "sloos_tightening_pct": sloos,
        "months_since_inversion": m_inv,
        "transmission_stage": stage,
        "estimated_recession_probability": recession_prob,
    }


def construct_two_by_two_scenario_matrix(
    axis_x_driver: str,
    axis_x_val: float,
    axis_y_driver: str,
    axis_y_val: float,
    threshold_x: float = 50.0,
    threshold_y: float = 50.0,
    scenario_a_label: str = "Managed Competition",
    scenario_b_label: str = "Decoupled Rivalry",
    scenario_c_label: str = "Cold War 2.0",
    scenario_d_label: str = "Hot Conflict",
) -> Dict[str, Any]:
    """Constructs dynamic 2x2 scenario planning quadrant mapping."""
    x = float(axis_x_val)
    y = float(axis_y_val)

    if x < threshold_x and y >= threshold_y:
        quadrant = "QUADRANT_1"
        active_scenario = scenario_a_label
    elif x >= threshold_x and y >= threshold_y:
        quadrant = "QUADRANT_2"
        active_scenario = scenario_b_label
    elif x < threshold_x and y < threshold_y:
        quadrant = "QUADRANT_3"
        active_scenario = scenario_c_label
    else:
        quadrant = "QUADRANT_4"
        active_scenario = scenario_d_label

    return {
        "axis_x_driver": str(axis_x_driver),
        "axis_x_val": x,
        "axis_y_driver": str(axis_y_driver),
        "axis_y_val": y,
        "active_quadrant": quadrant,
        "active_scenario": active_scenario,
    }


class BayesianGeopoliticalRegimeClassifier:
    """Core autonomous geopolitical foresight and BMA regime classifier."""

    def __init__(self, memory_decay: float = 0.99):
        self.decay = float(memory_decay)
        self.regime_weights: Dict[GeopoliticalRegime, float] = {
            GeopoliticalRegime.LIBERAL_ORDER: 0.20,
            GeopoliticalRegime.MULTIPOLAR: 0.25,
            GeopoliticalRegime.BIPOLAR: 0.35,
            GeopoliticalRegime.FRAGMENTATION: 0.15,
            GeopoliticalRegime.HEGEMONIC_TRANSITION: 0.05,
        }

    def update_with_signals(self, signals: List[GeopoliticalSignal]) -> Dict[str, float]:
        """Update Bayesian posterior weights across geopolitical regimes."""
        likelihoods = {regime: 0.0 for regime in GeopoliticalRegime}

        for s in signals:
            c = float(s.confidence)
            d = str(s.direction).lower()
            dom = str(s.domain).lower()

            if dom == "military" and d == "escalating":
                likelihoods[GeopoliticalRegime.BIPOLAR] += 1.5 * c
                likelihoods[GeopoliticalRegime.HEGEMONIC_TRANSITION] += 2.0 * c
            elif dom == "economic" and "bloc" in s.indicator.lower():
                likelihoods[GeopoliticalRegime.MULTIPOLAR] += 1.8 * c
                likelihoods[GeopoliticalRegime.FRAGMENTATION] += 1.2 * c
            elif dom == "diplomatic" and d == "de-escalating":
                likelihoods[GeopoliticalRegime.LIBERAL_ORDER] += 1.5 * c
            elif dom == "resource" and d == "escalating":
                likelihoods[GeopoliticalRegime.FRAGMENTATION] += 1.8 * c

        # Apply Bayesian log-score update with memory decay: w_i(t) ~ w_i(t-1)^lambda * exp(likelihood)
        unnorm_weights = {}
        for regime, prior_w in self.regime_weights.items():
            decayed_prior = prior_w ** self.decay
            unnorm_weights[regime] = decayed_prior * math.exp(likelihoods[regime])

        total = sum(unnorm_weights.values())
        if total > 0:
            self.regime_weights = {k: v / total for k, v in unnorm_weights.items()}

        return {k.value: round(v, 4) for k, v in self.regime_weights.items()}

    def evaluate_entropy_confidence(self) -> float:
        """Compute certainty score: 1.0 - (Shannon Entropy / max_entropy)."""
        probs = [p for p in self.regime_weights.values() if p > 0]
        entropy = -sum(p * math.log(p) for p in probs)
        max_entropy = math.log(len(self.regime_weights))
        certainty = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 1.0
        return float(max(0.0, min(1.0, certainty)))


# Canonical aliases
GeopoliticalMacroPredictor = BayesianGeopoliticalRegimeClassifier
