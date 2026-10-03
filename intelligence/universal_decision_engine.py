"""Universal Decision Engine — Philosophical Rules & Cross-Domain Decision Framework.

Applies formal philosophical decision theory, epistemic rationality principles,
game-theoretic axioms, and multi-domain arbitration to any decision type:
- Trading decisions (entries, exits, sizing)
- Macro-economic decisions (regime, liquidity, sovereign risk)
- Strategic portfolio decisions (allocation, rotation, hedging)
- Operational decisions (risk budgets, research prioritization)

Philosophical Foundations:
  1. Epistemic Humility (Socratic / Popperian falsificationism)
  2. Expected Value Maximization (von Neumann-Morgenstern)
  3. Minimax Regret (Savage regret theory)
  4. Pascal's Wager Asymmetry (catastrophic tail risk)
  5. Second-Order Thinking (Kahneman / Munger latticework)
  6. Via Negativa (Taleb antifragility: remove what's harmful)
  7. Compensated Risk Taking (expected payoff must exceed risk premium)
  8. Reversibility Preference (preserve optionality)

Invariant: execution_authority is ALWAYS False. This module is advisory only.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ===========================================================================
# Decision Domain Classification
# ===========================================================================

class DecisionDomain(str, Enum):
    TRADING = "TRADING"
    MACRO_ECONOMIC = "MACRO_ECONOMIC"
    PORTFOLIO_ALLOCATION = "PORTFOLIO_ALLOCATION"
    RISK_MANAGEMENT = "RISK_MANAGEMENT"
    RESEARCH_PRIORITIZATION = "RESEARCH_PRIORITIZATION"
    OPERATIONAL = "OPERATIONAL"


class PhilosophicalPrinciple(str, Enum):
    EPISTEMIC_HUMILITY = "EPISTEMIC_HUMILITY"
    EXPECTED_VALUE_MAX = "EXPECTED_VALUE_MAX"
    MINIMAX_REGRET = "MINIMAX_REGRET"
    CATASTROPHIC_TAIL = "CATASTROPHIC_TAIL"
    SECOND_ORDER_THINKING = "SECOND_ORDER_THINKING"
    VIA_NEGATIVA = "VIA_NEGATIVA"
    COMPENSATED_RISK = "COMPENSATED_RISK"
    REVERSIBILITY_PREFERENCE = "REVERSIBILITY_PREFERENCE"


class DecisionVerdict(str, Enum):
    ACT = "ACT"
    ACT_WITH_CAUTION = "ACT_WITH_CAUTION"
    DEFER = "DEFER"
    VETO = "VETO"
    ABSTAIN = "ABSTAIN"


# ===========================================================================
# Data Structures
# ===========================================================================

@dataclass
class PhilosophicalAssessment:
    """Result of applying a single philosophical principle to a decision."""
    principle: PhilosophicalPrinciple
    verdict: DecisionVerdict
    confidence: float          # 0.0 - 1.0
    reasoning: str
    evidence_quality: float    # 0.0 - 1.0
    risk_adjustment: float     # multiplier: 0.0 (veto) to 1.0 (full)
    is_blocking: bool          # True if this principle vetoes the decision


@dataclass
class UniversalDecision:
    """Unified output of the Universal Decision Engine."""
    domain: DecisionDomain
    decision_id: str
    verdict: DecisionVerdict
    confidence: float
    sizing_multiplier: float   # 0.0 - 1.0
    philosophical_assessments: List[PhilosophicalAssessment]
    second_order_effects: List[str]
    tail_risk_flag: bool
    reversibility_score: float  # 0.0 = irreversible, 1.0 = fully reversible
    epistemic_calibration: str  # GROUNDED, PROBABILISTIC, SPECULATIVE, CONTRADICTED
    rationale: str
    execution_authority: bool = False  # ALWAYS FALSE
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain.value,
            "decision_id": self.decision_id,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 4),
            "sizing_multiplier": round(self.sizing_multiplier, 4),
            "philosophical_assessments": [
                {
                    "principle": a.principle.value,
                    "verdict": a.verdict.value,
                    "confidence": round(a.confidence, 4),
                    "reasoning": a.reasoning,
                    "evidence_quality": round(a.evidence_quality, 4),
                    "risk_adjustment": round(a.risk_adjustment, 4),
                    "is_blocking": a.is_blocking,
                }
                for a in self.philosophical_assessments
            ],
            "second_order_effects": self.second_order_effects,
            "tail_risk_flag": self.tail_risk_flag,
            "reversibility_score": round(self.reversibility_score, 4),
            "epistemic_calibration": self.epistemic_calibration,
            "rationale": self.rationale,
            "execution_authority": self.execution_authority,
            "timestamp": self.timestamp,
        }


@dataclass
class DecisionContext:
    """Cross-domain decision input context."""
    domain: DecisionDomain
    symbol: str = ""
    action: str = "HOLD"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.5
    features: Dict[str, Any] = field(default_factory=dict)
    macro_context: Dict[str, Any] = field(default_factory=dict)
    portfolio_state: Dict[str, Any] = field(default_factory=dict)
    historical_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    time_horizon: str = "INTRADAY"  # INTRADAY, SWING, POSITION, STRATEGIC
    irreversibility_factors: List[str] = field(default_factory=list)
    second_order_chain: List[str] = field(default_factory=list)


# ===========================================================================
# Philosophical Rule Engines
# ===========================================================================

def _assess_epistemic_humility(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Socratic / Popperian: How much do we actually know vs. assume?

    Core question: What would falsify this hypothesis?
    If we cannot articulate falsification criteria, confidence must be reduced.
    """
    evidence_count = len([v for v in ctx.features.values() if v is not None])
    total_fields = max(len(ctx.features), 1)
    evidence_ratio = evidence_count / total_fields

    has_falsification = any(
        kw in str(ctx.features).lower()
        for kw in ("invalidation", "stop", "falsif", "breach", "reversal")
    )

    if evidence_ratio < 0.3:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EPISTEMIC_HUMILITY,
            verdict=DecisionVerdict.ABSTAIN,
            confidence=0.3,
            reasoning="Insufficient evidence base; Socratic ignorance applies — we do not know enough to act.",
            evidence_quality=evidence_ratio,
            risk_adjustment=0.3,
            is_blocking=True,
        )
    elif evidence_ratio < 0.6 or not has_falsification:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EPISTEMIC_HUMILITY,
            verdict=DecisionVerdict.DEFER,
            confidence=0.5,
            reasoning="Moderate evidence but no clear falsification criteria defined. Popperian standard not met.",
            evidence_quality=evidence_ratio,
            risk_adjustment=0.6,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EPISTEMIC_HUMILITY,
            verdict=DecisionVerdict.ACT,
            confidence=0.8,
            reasoning="Evidence base is solid and falsification criteria are articulable.",
            evidence_quality=evidence_ratio,
            risk_adjustment=1.0,
            is_blocking=False,
        )


def _assess_expected_value(ctx: DecisionContext) -> PhilosophicalAssessment:
    """von Neumann-Morgenstern: Maximize expected utility.

    EV = P(win) * reward - P(loss) * risk
    Must be positive to proceed.
    """
    entry = ctx.entry_price
    sl = ctx.stop_loss
    tp = ctx.take_profit

    if entry <= 0 or sl <= 0 or tp <= 0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EXPECTED_VALUE_MAX,
            verdict=DecisionVerdict.ABSTAIN,
            confidence=0.2,
            reasoning="Cannot compute expected value without defined entry, stop, and target.",
            evidence_quality=0.0,
            risk_adjustment=0.0,
            is_blocking=True,
        )

    risk_dist = abs(entry - sl)
    reward_dist = abs(tp - entry)
    rr_ratio = reward_dist / risk_dist if risk_dist > 1e-9 else 0.0

    # Assume 50% base win rate for uncalibrated systems
    win_rate = ctx.confidence
    ev = (win_rate * reward_dist) - ((1 - win_rate) * risk_dist)

    if ev <= 0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EXPECTED_VALUE_MAX,
            verdict=DecisionVerdict.VETO,
            confidence=0.9,
            reasoning=f"Negative expected value (EV={ev:.4f}). VNM rationality mandates not taking negative-EV bets.",
            evidence_quality=0.8,
            risk_adjustment=0.0,
            is_blocking=True,
        )
    elif rr_ratio < 1.5:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EXPECTED_VALUE_MAX,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.6,
            reasoning=f"Marginal EV (R:R={rr_ratio:.2f}, EV={ev:.4f}). Proceed with reduced sizing.",
            evidence_quality=0.7,
            risk_adjustment=0.5,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.EXPECTED_VALUE_MAX,
            verdict=DecisionVerdict.ACT,
            confidence=0.85,
            reasoning=f"Positive EV with favorable R:R ({rr_ratio:.2f}, EV={ev:.4f}). VNM rationality satisfied.",
            evidence_quality=0.8,
            risk_adjustment=1.0,
            is_blocking=False,
        )


def _assess_minimax_regret(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Savage regret theory: Minimize maximum possible regret.

    If we act and are wrong, what's the worst-case regret?
    If we don't act and would have been right, what's the opportunity cost?
    """
    risk_dist = abs(ctx.entry_price - ctx.stop_loss)
    reward_dist = abs(ctx.take_profit - ctx.entry_price)

    # Regret of acting = maximum loss (stop distance)
    regret_of_acting = risk_dist
    # Regret of not acting = missed opportunity (reward distance)
    regret_of_not_acting = reward_dist

    # Regret ratio: if missed opportunity >> max loss, act. Otherwise hold.
    if regret_of_acting <= 0 and regret_of_not_acting <= 0:
        ratio = 1.0
    else:
        ratio = regret_of_not_acting / max(regret_of_acting, 1e-9)

    if ratio > 2.0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.MINIMAX_REGRET,
            verdict=DecisionVerdict.ACT,
            confidence=0.7,
            reasoning=f"Opportunity cost ({regret_of_not_acting:.4f}) far exceeds downside regret ({regret_of_acting:.4f}). Minimax regret favors action.",
            evidence_quality=0.6,
            risk_adjustment=0.9,
            is_blocking=False,
        )
    elif ratio < 0.5:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.MINIMAX_REGRET,
            verdict=DecisionVerdict.DEFER,
            confidence=0.6,
            reasoning=f"Downside regret ({regret_of_acting:.4f}) exceeds opportunity cost ({regret_of_not_acting:.4f}). Minimax regret favors patience.",
            evidence_quality=0.6,
            risk_adjustment=0.5,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.MINIMAX_REGRET,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.55,
            reasoning=f"Regret ratio balanced ({ratio:.2f}). Neither action nor inaction dominates.",
            evidence_quality=0.5,
            risk_adjustment=0.7,
            is_blocking=False,
        )


def _assess_catastrophic_tail(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Pascal's Wager asymmetry: Catastrophic downside tails dominate.

    If a loss can be ruinous (loss of >20% capital, career-ending, etc.),
    the expected value calculation must include the probability-weighted
    catastrophic scenario even if its probability is small.
    """
    vix = ctx.features.get("vix", 18.0)
    max_drawdown = ctx.portfolio_state.get("max_drawdown_pct", 0.0)
    is_crisis = ctx.macro_context.get("is_crisis", False)

    tail_risk_score = 0.0
    if vix >= 30.0:
        tail_risk_score += 0.4
    if max_drawdown >= 0.15:
        tail_risk_score += 0.3
    if is_crisis:
        tail_risk_score += 0.3

    if tail_risk_score >= 0.7:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.CATASTROPHIC_TAIL,
            verdict=DecisionVerdict.VETO,
            confidence=0.9,
            reasoning=f"Tail risk score {tail_risk_score:.2f} indicates ruin-level danger. Pascal's Wager: asymmetric catastrophe dominates.",
            evidence_quality=0.9,
            risk_adjustment=0.0,
            is_blocking=True,
        )
    elif tail_risk_score >= 0.4:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.CATASTROPHIC_TAIL,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.6,
            reasoning=f"Elevated tail risk ({tail_risk_score:.2f}). Reduce sizing to survive potential catastrophe.",
            evidence_quality=0.7,
            risk_adjustment=0.4,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.CATASTROPHIC_TAIL,
            verdict=DecisionVerdict.ACT,
            confidence=0.8,
            reasoning=f"Tail risk manageable ({tail_risk_score:.2f}). No ruin-level scenarios detected.",
            evidence_quality=0.8,
            risk_adjustment=1.0,
            is_blocking=False,
        )


def _assess_second_order(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Kahneman / Munger second-order thinking: What happens after what happens?

    First-order: "If I buy EURUSD, it goes up 100 pips."
    Second-order: "If it goes up, does that attract crowding? Does the crowd
    create conditions for a reversal? What's the reflexive feedback loop?"
    """
    chain = ctx.second_order_chain
    has_second_order = len(chain) > 0

    if not has_second_order:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.SECOND_ORDER_THINKING,
            verdict=DecisionVerdict.DEFER,
            confidence=0.4,
            reasoning="No second-order effects analyzed. Munger's latticework demands multi-step causal reasoning.",
            evidence_quality=0.3,
            risk_adjustment=0.6,
            is_blocking=False,
        )

    # Analyze chain for reflexive/crowding effects
    crowding_indicators = sum(1 for s in chain if any(
        kw in s.lower() for kw in ("crowd", "herd", "reflexive", "feedback", "squeeze", "cascade")
    ))

    if crowding_indicators > 0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.SECOND_ORDER_THINKING,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.5,
            reasoning=f"Second-order analysis reveals {crowding_indicators} reflexive/crowding risk(s) in causal chain.",
            evidence_quality=0.6,
            risk_adjustment=0.6,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.SECOND_ORDER_THINKING,
            verdict=DecisionVerdict.ACT,
            confidence=0.7,
            reasoning="Second-order effects analyzed; no reflexive crowding or adverse feedback loops detected.",
            evidence_quality=0.7,
            risk_adjustment=0.9,
            is_blocking=False,
        )


def _assess_via_negativa(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Taleb's Via Negativa: Remove what's harmful rather than add what's helpful.

    Focus on what could go wrong, not what could go right.
    Risk of ruin > probability of gain.
    """
    irreversibility = ctx.irreversibility_factors
    risk_factors = [f for f in ctx.features.values() if isinstance(f, (int, float)) and f > 0]
    negative_signals = sum(1 for f in irreversibility if f)

    sl = ctx.stop_loss
    entry = ctx.entry_price
    risk_pct = abs(entry - sl) / entry if entry > 0 else 0.0

    if risk_pct > 0.03 or negative_signals >= 3:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.VIA_NEGATIVA,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.5,
            reasoning=f"Via Negativa: {negative_signals} irreversibility factor(s) and {risk_pct:.1%} risk per trade detected. Remove harm first.",
            evidence_quality=0.6,
            risk_adjustment=0.5,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.VIA_NEGATIVA,
            verdict=DecisionVerdict.ACT,
            confidence=0.75,
            reasoning="Via Negativa: No excessive irreversibility or compounding harm detected.",
            evidence_quality=0.7,
            risk_adjustment=1.0,
            is_blocking=False,
        )


def _assess_compensated_risk(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Compensated Risk: Risk must be compensated by expected reward.

    Kelly criterion intuition: Only bet when edge * odds > 1.
    Risk premium must exceed the cost of uncertainty.
    """
    entry = ctx.entry_price
    sl = ctx.stop_loss
    tp = ctx.take_profit

    if entry <= 0 or sl <= 0 or tp <= 0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.COMPENSATED_RISK,
            verdict=DecisionVerdict.ABSTAIN,
            confidence=0.2,
            reasoning="Cannot assess risk compensation without complete entry/stop/target geometry.",
            evidence_quality=0.0,
            risk_adjustment=0.0,
            is_blocking=True,
        )

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 1e-9 else 0.0

    # Simplified Kelly: f* = (bp - q) / b where b=odds, p=win_rate, q=1-p
    b = rr
    p = ctx.confidence
    q = 1.0 - p
    kelly_f = (b * p - q) / b if b > 1e-9 else 0.0

    if kelly_f <= 0:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.COMPENSATED_RISK,
            verdict=DecisionVerdict.VETO,
            confidence=0.85,
            reasoning=f"Negative Kelly fraction ({kelly_f:.4f}). Risk is NOT compensated. Rational bettor abstains.",
            evidence_quality=0.8,
            risk_adjustment=0.0,
            is_blocking=True,
        )
    elif kelly_f < 0.05:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.COMPENSATED_RISK,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.5,
            reasoning=f"Thin Kelly edge ({kelly_f:.4f}). Risk barely compensated. Use fractional Kelly (half or quarter).",
            evidence_quality=0.6,
            risk_adjustment=0.3,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.COMPENSATED_RISK,
            verdict=DecisionVerdict.ACT,
            confidence=0.8,
            reasoning=f"Healthy Kelly edge ({kelly_f:.4f}). Risk is well compensated by expected return.",
            evidence_quality=0.8,
            risk_adjustment=1.0,
            is_blocking=False,
        )


def _assess_reversibility(ctx: DecisionContext) -> PhilosophicalAssessment:
    """Reversibility Preference: Prefer actions that can be undone.

    Amazon's "one-way door" vs "two-way door" framework.
    Trading: can we exit at minimal cost? Portfolio: can we rebalance?
    """
    has_stop = ctx.stop_loss > 0
    time_horizon = ctx.time_horizon.upper()
    irreversibility_count = len(ctx.irreversibility_factors)

    reversibility_score = 0.5
    if has_stop:
        reversibility_score += 0.3
    if time_horizon in ("INTRADAY", "SWING"):
        reversibility_score += 0.1
    if irreversibility_count == 0:
        reversibility_score += 0.1
    reversibility_score = min(1.0, reversibility_score)

    if reversibility_score < 0.3:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.REVERSIBILITY_PREFERENCE,
            verdict=DecisionVerdict.ACT_WITH_CAUTION,
            confidence=0.4,
            reasoning=f"Irreversible action (score={reversibility_score:.2f}). This is a 'one-way door' — require exceptional conviction.",
            evidence_quality=0.5,
            risk_adjustment=0.4,
            is_blocking=False,
        )
    else:
        return PhilosophicalAssessment(
            principle=PhilosophicalPrinciple.REVERSIBILITY_PREFERENCE,
            verdict=DecisionVerdict.ACT,
            confidence=0.7,
            reasoning=f"Reversible action (score={reversibility_score:.2f}). 'Two-way door' — safe to proceed with standard caution.",
            evidence_quality=0.7,
            risk_adjustment=1.0,
            is_blocking=False,
        )


# ===========================================================================
# Universal Decision Engine
# ===========================================================================

class UniversalDecisionEngine:
    """Cross-domain decision engine applying philosophical principles.

    Integrates with existing Laya taxonomy, debate protocol, macro fusion gate,
    and analytical frameworks to provide a unified advisory decision layer.

    Usage:
        engine = UniversalDecisionEngine()
        decision = engine.evaluate(DecisionContext(
            domain=DecisionDomain.TRADING,
            symbol="EURUSD",
            action="BUY",
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.1000,
            confidence=0.75,
            features={"vix": 18.0, "rsi_14": 45.0, "adx_14": 28.0},
            macro_context={"regime": "RISK_ON"},
        ))
    """

    def __init__(
        self,
        principles: Optional[List[PhilosophicalPrinciple]] = None,
        veto_threshold: int = 2,
        min_confidence: float = 0.5,
    ):
        self.principles = principles or list(PhilosophicalPrinciple)
        self.veto_threshold = veto_threshold  # Number of blocking vetoes to kill decision
        self.min_confidence = min_confidence

        self._assessors: Dict[PhilosophicalPrinciple, Callable] = {
            PhilosophicalPrinciple.EPISTEMIC_HUMILITY: _assess_epistemic_humility,
            PhilosophicalPrinciple.EXPECTED_VALUE_MAX: _assess_expected_value,
            PhilosophicalPrinciple.MINIMAX_REGRET: _assess_minimax_regret,
            PhilosophicalPrinciple.CATASTROPHIC_TAIL: _assess_catastrophic_tail,
            PhilosophicalPrinciple.SECOND_ORDER_THINKING: _assess_second_order,
            PhilosophicalPrinciple.VIA_NEGATIVA: _assess_via_negativa,
            PhilosophicalPrinciple.COMPENSATED_RISK: _assess_compensated_risk,
            PhilosophicalPrinciple.REVERSIBILITY_PREFERENCE: _assess_reversibility,
        }

    def evaluate(self, ctx: DecisionContext) -> UniversalDecision:
        """Run all philosophical principle assessments and synthesize verdict."""
        # Guard the trust boundary: producers must hand in a win-rate in [0, 1].
        # (Kelly/EV math below silently produces wrong sizing when p > 1, e.g. the
        # 1.3 that a naive M1 -> confidence mapping produced.) NaN loses every
        # comparison, so map it to 0.0 explicitly instead of trusting min/max.
        try:
            conf = float(ctx.confidence)
        except (TypeError, ValueError):
            conf = 0.0
        if not math.isfinite(conf):
            conf = 0.0
        ctx.confidence = min(1.0, max(0.0, conf))
        assessments: List[PhilosophicalAssessment] = []
        veto_count = 0
        total_risk_adj = 1.0
        total_confidence = 0.0
        min_risk_adj = 1.0

        for principle in self.principles:
            assessor = self._assessors.get(principle)
            if assessor is None:
                continue
            assessment = assessor(ctx)
            assessments.append(assessment)

            if assessment.is_blocking:
                veto_count += 1
            total_risk_adj = min(total_risk_adj, assessment.risk_adjustment)
            total_confidence += assessment.confidence
            min_risk_adj = min(min_risk_adj, assessment.risk_adjustment)

        avg_confidence = total_confidence / max(len(assessments), 1)

        # Determine verdict
        if veto_count >= self.veto_threshold:
            verdict = DecisionVerdict.VETO
            sizing = 0.0
        elif veto_count == 1:
            verdict = DecisionVerdict.ACT_WITH_CAUTION
            sizing = min_risk_adj
        elif avg_confidence < self.min_confidence:
            verdict = DecisionVerdict.DEFER
            sizing = 0.3
        else:
            verdict = DecisionVerdict.ACT
            sizing = total_risk_adj

        # Tail risk flag
        tail_flag = any(
            a.principle == PhilosophicalPrinciple.CATASTROPHIC_TAIL and a.risk_adjustment < 0.5
            for a in assessments
        )

        # Epistemic calibration
        epistemic = self._calibrate_epistemic(avg_confidence, len(ctx.features), ctx.historical_outcomes)

        # Reversibility score
        rev_scores = [a.risk_adjustment for a in assessments
                      if a.principle == PhilosophicalPrinciple.REVERSIBILITY_PREFERENCE]
        reversibility = rev_scores[0] if rev_scores else 0.5

        # Second-order effects
        second_order = ctx.second_order_chain or []

        # Build decision ID
        decision_id = hashlib.sha256(
            f"{ctx.domain.value}:{ctx.symbol}:{ctx.action}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:16]

        # Rationale synthesis
        rationale_parts = []
        for a in assessments:
            if a.is_blocking:
                rationale_parts.append(f"[BLOCKED by {a.principle.value}]: {a.reasoning}")
            elif a.verdict == DecisionVerdict.ACT_WITH_CAUTION:
                rationale_parts.append(f"[CAUTION from {a.principle.value}]: {a.reasoning}")
        if not rationale_parts:
            rationale_parts = [f"Philosophical synthesis ({len(assessments)} principles) favors {verdict.value}"]

        return UniversalDecision(
            domain=ctx.domain,
            decision_id=decision_id,
            verdict=verdict,
            confidence=round(avg_confidence, 4),
            sizing_multiplier=round(max(0.0, min(1.0, sizing)), 4),
            philosophical_assessments=assessments,
            second_order_effects=second_order,
            tail_risk_flag=tail_flag,
            reversibility_score=round(reversibility, 4),
            epistemic_calibration=epistemic,
            rationale=" | ".join(rationale_parts),
            execution_authority=False,
        )

    def _calibrate_epistemic(
        self, avg_confidence: float, feature_count: int, outcomes: List[Dict]
    ) -> str:
        """Determine epistemic state of knowledge."""
        if feature_count < 5:
            return "CONTRADICTED"
        elif avg_confidence >= 0.75 and feature_count >= 10:
            return "GROUNDED"
        elif avg_confidence >= 0.55:
            return "PROBABILISTIC"
        else:
            return "SPECULATIVE"

    def evaluate_cross_domain(
        self,
        trading_ctx: Optional[DecisionContext] = None,
        macro_ctx: Optional[DecisionContext] = None,
        portfolio_ctx: Optional[DecisionContext] = None,
    ) -> Dict[str, UniversalDecision]:
        """Evaluate multiple decision domains simultaneously and reconcile."""
        results: Dict[str, UniversalDecision] = {}

        if trading_ctx is not None:
            results["trading"] = self.evaluate(trading_ctx)
        if macro_ctx is not None:
            results["macro"] = self.evaluate(macro_ctx)
        if portfolio_ctx is not None:
            results["portfolio"] = self.evaluate(portfolio_ctx)

        return results

    def as_decision_advisor(self) -> Callable[[DecisionContext], UniversalDecision]:
        """Return a callable adapter for integration with existing pipelines."""
        return self.evaluate
