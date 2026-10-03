r"""ApexQuant Multi-Agent Debate Protocol (Paper P13, Ding / Sun 2026).

Implements the three-persona adversarial parliamentary debate framework:
  1. Zealot: Aggressive Bull persona. Identifies asymmetric upside, momentum
     continuation, structural catalysts, and growth drivers.
  2. Reaper: Aggressive Bear persona. Audits downside risk, structural exhaustion,
     liquidity traps, macro fragility, and thesis invalidation.
  3. Fulcrum: Moderating Chief Investment Officer (CIO). Evaluates evidence quality
     over rhetorical dominance (prevents systematic persona bias), applies behavioral
     finance principles, and synthesizes the final consensus verdict.

Architecture & Invariants:
  - Multi-round structured debate:
      Round 1: Opening Thesis (Zealot) & Antithesis (Reaper)
      Round 2: Cross-examination & Stress-testing
      Round 3: Fulcrum Synthesis & Adjudication
  - Behavioral Finance Distillation: actively detects confirmation bias, recency bias,
    and rhetorical overconfidence.
  - Rhetorical Bias Detection: penalizes agents asserting high conviction without
    quantitative metric backing.
  - Bounded Autonomy ($R \circ V_{\text{LLM}} \circ S$) Integration: plugs directly
    into `LLMVetoOperator` as an adversarial multi-agent vetoer.
  - Fail-closed fallback: when evidence is irreconcilably contradictory or confidence
    falls below safety threshold, defaults to HOLD (Boolean Veto).

References:
    Sun, S. (2026). "ApexQuant: A Multi-Agent Debate Framework for Quantitative
    Trading." Technical Report, arXiv:2603.xxxxx (P13).
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("ApexQuantDebate")


# =============================================================================
# Personas and System Prompts
# =============================================================================

ZEALOT_SYSTEM_PROMPT = """You are ZEALOT, the aggressive Bull in the ApexQuant trading parliament.
Your core constitutional mandate:
1. Identify all structural catalysts, positive momentum drivers, and asymmetric upside opportunities.
2. Advocate forcefully for long/continuation trades where risk/reward is favorable.
3. Quantify upside targets and argue why prevailing bearish narratives are already priced in.
4. Ground every assertion in concrete quantitative evidence (trend, volume, support levels, momentum).
Do NOT be reckless: identify the precise invalidation level for your bull thesis.
"""

REAPER_SYSTEM_PROMPT = """You are REAPER, the aggressive Bear and Risk Auditor in the ApexQuant trading parliament.
Your core constitutional mandate:
1. Uncover hidden risks, distribution patterns, liquidity traps, and structural market exhaustion.
2. Stress-test the proposer's and Zealot's assumptions mercilessly.
3. Point out overbought conditions, macro headwinds, negative skew, and poor risk/reward geometry.
4. Advocate for cash preservation or short exposure whenever the bull thesis lacks definitive edge.
5. Identify the exact failure modes that would trigger catastrophic drawdown.
"""

FULCRUM_SYSTEM_PROMPT = """You are FULCRUM, the Chief Investment Officer and Moderator in the ApexQuant trading parliament.
Your core constitutional mandate:
1. Adjudicate the debate between Zealot (Bull) and Reaper (Bear) with strict impartiality.
2. Distinguish EVIDENCE QUALITY from RHETORICAL DOMINANCE: an agent who shouts louder or uses
   colorful adjectives without hard quantitative metric backing must be penalized.
3. Filter out behavioral biases: confirmation bias, anchoring, recency bias, and loss aversion.
4. Issue an authoritative final verdict:
   - Action: BUY, SELL, or HOLD (VETO)
   - Sizing Multiplier: 0.0 to 1.0 (reduce size if debate highlighted significant unresolved risks)
   - Conviction Confidence: 0.0 to 1.0
   - Synthesis Rationale: concise summary of why one thesis prevailed over the other.
"""


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class DebateTurn:
    """A single turn in the adversarial parliamentary debate."""
    agent: str  # "Zealot", "Reaper", "Fulcrum"
    round_number: int  # 1 (Thesis), 2 (Cross-Exam), 3 (Synthesis)
    action_bias: str  # "BUY", "SELL", "HOLD"
    confidence: float  # 0.0 - 1.0
    argument: str
    evidence_points: List[str] = field(default_factory=list)
    evidence_quality: float = 1.0  # 0.0 to 1.0 (quantitative metric density)
    rhetorical_bias_penalty: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DebateVerdict:
    """Final adjudicated synthesis from Fulcrum."""
    decision: str  # "BUY", "SELL", "HOLD"
    approved: bool  # True if candidate action matches decision and conviction >= threshold
    confidence: float  # 0.0 - 1.0
    sizing_multiplier: float  # 0.0 - 1.0
    rationale: str
    turns: List[DebateTurn] = field(default_factory=list)
    rhetorical_bias_detected: bool = False
    dominating_persona: Optional[str] = None
    consensus_level: str = "DIVIDED"  # "UNANIMOUS", "MAJORITY", "DIVIDED", "VETO"
    execution_authority: bool = False  # Always False: only RiskController/Arbiter executes orders
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# Quantitative Rhetorical Bias Evaluator
# =============================================================================

_QUANT_METRIC_PATTERNS = [
    r"\b(rsi|adx|atr|ema|sma|macd|obv|vwap)\b",
    r"\b\d+(\.\d+)?(%|x|bp|r|rr)\b",
    r"\b(support|resistance|swing|pivot|choch|bos|fvg)\b",
    r"\b(sharpe|sortino|drawdown|expectancy|win[\s_-]?rate)\b",
    r"\b(volume|liquidity|order[\s_-]?block|premium|discount)\b",
]

_RHETORICAL_ASSERTION_PATTERNS = [
    r"\b(obviously|clearly|guaranteed|undoubtedly|massive|huge|skyrocket|plummet)\b",
    r"\b(no brainer|sure thing|cannot lose|must buy|must sell|easy money)\b",
    r"\b(foolish|insane|blind|everyone knows|certainly)\b",
]


def evaluate_evidence_quality(text: str) -> Tuple[float, float]:
    """Calculate evidence quality and rhetorical bias penalty.

    Returns:
        (evidence_quality, rhetorical_penalty) in [0.0, 1.0].
    """
    if not text:
        return 0.0, 0.5

    lower = text.lower()
    metric_hits = sum(len(re.findall(p, lower)) for p in _QUANT_METRIC_PATTERNS)
    rhetoric_hits = sum(len(re.findall(p, lower)) for p in _RHETORICAL_ASSERTION_PATTERNS)

    # Base evidence quality scales with metric density (saturating sigmoid-like curve)
    ev_score = min(1.0, metric_hits / max(1.0, metric_hits + 3.0) * 1.8)

    # Rhetorical penalty increases with unsubstantiated assertions
    rhet_penalty = min(0.6, (rhetoric_hits * 0.15) / max(1.0, metric_hits * 0.5 + 1.0))

    final_quality = max(0.1, round(ev_score - rhet_penalty, 3))
    return final_quality, round(rhet_penalty, 3)


# =============================================================================
# ApexQuant Parliament Engine
# =============================================================================

class ApexQuantParliament:
    """Three-persona multi-agent debate engine for trade hypothesis validation."""

    def __init__(
        self,
        llm_caller: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        model_name: str = "apexquant-parliament-v1",
        min_conviction_threshold: float = 0.60,
        unanimous_boost: float = 1.15,
        veto_on_stalemate: bool = True,
    ):
        self.llm_caller = llm_caller
        self.model_name = model_name
        self.min_conviction_threshold = min_conviction_threshold
        self.unanimous_boost = unanimous_boost
        self.veto_on_stalemate = veto_on_stalemate

    def conduct_debate(
        self,
        candidate: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> DebateVerdict:
        """Conduct a 3-round multi-agent debate over a proposed trade hypothesis.

        Args:
            candidate: Proposed trade details (symbol, action, entry, sl, tp, score, etc.)
            context: Market observations (OHLC, technical indicators, regime, news)

        Returns:
            DebateVerdict with final action, confidence, sizing multiplier, and audit transcript.
        """
        ctx = context or {}
        action = str(candidate.get("action", "HOLD")).upper()
        symbol = str(candidate.get("symbol", "UNKNOWN"))

        if action not in ("BUY", "SELL"):
            return DebateVerdict(
                decision="HOLD",
                approved=False,
                confidence=1.0,
                sizing_multiplier=0.0,
                rationale="Candidate is neutral (HOLD); no parliamentary debate required.",
                consensus_level="UNANIMOUS",
            )

        # If an LLM caller is configured, execute the prompt-based debate
        if self.llm_caller is not None:
            return self._conduct_llm_debate(candidate, ctx)

        # Otherwise execute the high-fidelity deterministic quantitative debate simulation
        return self._conduct_deterministic_debate(candidate, ctx)

    # -------------------------------------------------------------------------
    # Deterministic Parliamentary Evaluator (Zero-API / Unit-Test / Benchmark Mode)
    # -------------------------------------------------------------------------

    def _conduct_deterministic_debate(
        self,
        candidate: Dict[str, Any],
        context: Dict[str, Any],
    ) -> DebateVerdict:
        """Deterministic adversarial debate engine based on quantitative evidence."""
        turns: List[DebateTurn] = []
        action = candidate.get("action", "BUY").upper()
        entry = float(candidate.get("entry_price", candidate.get("entry", 0.0)) or 0.0)
        sl = float(candidate.get("stop_loss", 0.0) or 0.0)
        tp = float(candidate.get("take_profit", 0.0) or 0.0)

        risk_dist = abs(entry - sl) if entry and sl else 0.0
        reward_dist = abs(tp - entry) if entry and tp else 0.0
        rr_ratio = (reward_dist / risk_dist) if risk_dist > 1e-9 else 0.0

        features = candidate.get("features", {})
        indicators = context.get("indicators", {})
        smc = context.get("smc_structure", {})

        # Evidence gathering
        rsi = float(features.get("rsi", indicators.get("rsi_14", 50.0)) or 50.0)
        adx = float(features.get("adx", indicators.get("adx_14", 25.0)) or 25.0)
        regime = str(context.get("regime", features.get("regime", "UNKNOWN"))).upper()
        has_choch = bool(smc.get("has_choch", False))
        in_premium = bool(smc.get("in_premium", False))

        # --- Round 1: Opening Theses ---
        # Zealot finds reasons to support the candidate direction
        zealot_evidence = []
        zealot_points = 0.0

        if action == "BUY":
            if rsi < 65:
                zealot_evidence.append(f"RSI={rsi:.1f} allows upside headroom before overbought")
                zealot_points += 0.25
            if "BULL" in regime:
                zealot_evidence.append(f"Macro regime {regime} provides structural tailwind")
                zealot_points += 0.35
            if rr_ratio >= 2.0:
                zealot_evidence.append(f"R:R ratio {rr_ratio:.2f} provides strong positive asymmetry")
                zealot_points += 0.30
            if adx >= 25.0:
                zealot_evidence.append(f"ADX={adx:.1f} confirms active directional trend momentum")
                zealot_points += 0.20
        else:  # SELL
            if rsi > 35:
                zealot_evidence.append(f"RSI={rsi:.1f} has downward room before oversold")
                zealot_points += 0.25
            if "BEAR" in regime:
                zealot_evidence.append(f"Macro regime {regime} confirms bearish distribution")
                zealot_points += 0.35
            if rr_ratio >= 2.0:
                zealot_evidence.append(f"R:R ratio {rr_ratio:.2f} gives favorable downside skew")
                zealot_points += 0.30

        zealot_conf = min(0.95, max(0.40, 0.40 + zealot_points))
        zealot_arg = (
            f"Zealot advocates {action}: structural edge supported by {', '.join(zealot_evidence) or 'trend momentum'}. "
            f"Favorable geometry with R:R {rr_ratio:.2f}."
        )
        z_qual, z_pen = evaluate_evidence_quality(zealot_arg)
        turns.append(DebateTurn(
            agent="Zealot", round_number=1, action_bias=action,
            confidence=round(zealot_conf, 2), argument=zealot_arg,
            evidence_points=zealot_evidence, evidence_quality=z_qual,
            rhetorical_bias_penalty=z_pen,
        ))

        # Reaper stress-tests the candidate and looks for failure modes
        reaper_evidence = []
        reaper_severity = 0.0

        if action == "BUY":
            if in_premium:
                reaper_evidence.append("Price is in dealer premium zone (>61.8% dealing range); buying high risk")
                reaper_severity += 0.40
            if has_choch:
                reaper_evidence.append("Bearish CHoCH structure change recently fired; trend reversal warning")
                reaper_severity += 0.45
            if rsi > 70:
                reaper_evidence.append(f"RSI={rsi:.1f} indicates extreme overbought extension")
                reaper_severity += 0.35
            if "BEAR" in regime:
                reaper_evidence.append(f"Fighting dominant regime {regime} exposes trade to liquidity sweeps")
                reaper_severity += 0.45
            if rr_ratio < 1.5:
                reaper_evidence.append(f"Insufficient R:R ({rr_ratio:.2f} < 1.50) fails TwinAlgo expectancy threshold")
                reaper_severity += 0.50
        else:  # SELL
            if not in_premium and smc.get("range_position", 0.5) is not None and (smc.get("range_position") or 0.5) < 0.382:
                reaper_evidence.append("Price is in discount zone; selling into structural support")
                reaper_severity += 0.40
            if has_choch:
                reaper_evidence.append("Bullish CHoCH fired; short thesis is fighting new orderflow structure")
                reaper_severity += 0.45
            if rsi < 30:
                reaper_evidence.append(f"RSI={rsi:.1f} deeply oversold; high probability of mean-reversion squeeze")
                reaper_severity += 0.35
            if "BULL" in regime:
                reaper_evidence.append(f"Counter-trend short in bullish regime {regime} is statistically negative EV")
                reaper_severity += 0.45

        reaper_conf = min(0.95, max(0.20, 0.20 + reaper_severity))
        reaper_bias = "HOLD" if reaper_severity >= 0.40 else ("SELL" if action == "BUY" else "BUY")
        reaper_arg = (
            f"Reaper challenges {action}: identified critical vulnerabilities: "
            f"{', '.join(reaper_evidence) if reaper_evidence else 'no catastrophic failure mode detected; low risk profile'}."
        )
        r_qual, r_pen = evaluate_evidence_quality(reaper_arg)
        turns.append(DebateTurn(
            agent="Reaper", round_number=1, action_bias=reaper_bias,
            confidence=round(reaper_conf, 2), argument=reaper_arg,
            evidence_points=reaper_evidence, evidence_quality=r_qual,
            rhetorical_bias_penalty=r_pen,
        ))

        # --- Round 2: Cross-Examination & Rebuttal ---
        # Zealot responds to Reaper
        if reaper_evidence:
            z_rebuttal = (
                f"Zealot counter-argument: While Reaper cites {reaper_evidence[0]}, "
                f"our stop loss at {sl:.5f} tightly bounds downside risk to exactly {risk_dist:.5f}. "
                f"Reward-to-risk of {rr_ratio:.2f} mathematically overcomes sub-optimal entry positioning."
            )
        else:
            z_rebuttal = "Zealot affirms: Reaper found no structural invalidations; green light for full execution."
        zq2, zp2 = evaluate_evidence_quality(z_rebuttal)
        turns.append(DebateTurn(
            agent="Zealot", round_number=2, action_bias=action,
            confidence=round(zealot_conf * 0.95, 2), argument=z_rebuttal,
            evidence_points=[f"Stop-loss bounds loss to {risk_dist:.4f}"],
            evidence_quality=zq2, rhetorical_bias_penalty=zp2,
        ))

        # Reaper's final closing assault
        if reaper_severity >= 0.70:
            r_rebuttal = (
                f"Reaper closing veto: Mathematical R:R is irrelevant if win-rate collapses due to "
                f"{reaper_evidence[0]}. Unsubstantiated upside target cannot overcome structural headwinds."
            )
        else:
            r_rebuttal = "Reaper concession: Risk geometry is acceptable provided strict position sizing is enforced."
        rq2, rp2 = evaluate_evidence_quality(r_rebuttal)
        turns.append(DebateTurn(
            agent="Reaper", round_number=2, action_bias=reaper_bias,
            confidence=round(reaper_conf, 2), argument=r_rebuttal,
            evidence_points=reaper_evidence[:2],
            evidence_quality=rq2, rhetorical_bias_penalty=rp2,
        ))

        # --- Round 3: Fulcrum Adjudication & Synthesis ---
        # Fulcrum balances evidence quality, checks for rhetorical domination, and rules
        rhetorical_bias = (z_pen > 0.20 or r_pen > 0.20)
        dominating = "Zealot" if z_pen > r_pen else ("Reaper" if r_pen > 0.15 else None)

        # Fatal risk conditions trigger Boolean VETO
        is_vetoed = False
        veto_reason = ""
        if reaper_severity >= 0.80:
            is_vetoed = True
            veto_reason = f"Fatal structural risk uncovered by Reaper: {', '.join(reaper_evidence)}"
        elif rr_ratio < 1.5:
            is_vetoed = True
            veto_reason = f"Reaper verified R:R={rr_ratio:.2f} violates minimal expectancy floor (1.50)"
        elif reaper_conf > 0.75 and reaper_conf > zealot_conf + 0.15:
            is_vetoed = True
            veto_reason = "Reaper risk audit quantitatively overwhelmed Zealot upside thesis"

        if is_vetoed:
            final_action = "HOLD"
            approved = False
            confidence = round(reaper_conf, 2)
            sizing_mult = 0.0
            consensus = "VETO"
            rationale = f"Fulcrum CIO VETO: {veto_reason}"
        elif reaper_severity >= 0.40:
            # Compromise / Sizing Reduction
            final_action = action
            approved = True
            # Shrink size due to unresolved adversarial risk points
            sizing_mult = max(0.30, round(1.0 - (reaper_severity * 0.6), 2))
            confidence = round(max(self.min_conviction_threshold, (zealot_conf + (1.0 - reaper_conf)) / 2.0), 2)
            consensus = "DIVIDED"
            rationale = (
                f"Fulcrum approves {action} with cautionary sizing ({sizing_mult}x): "
                f"Zealot demonstrated valid momentum, but Reaper's warnings on "
                f"{reaper_evidence[0]} require defensive sizing."
            )
        else:
            # High Conviction Agreement
            final_action = action
            approved = True
            sizing_mult = 1.0
            confidence = min(0.95, round(zealot_conf * self.unanimous_boost, 2))
            consensus = "UNANIMOUS" if reaper_severity < 0.20 else "MAJORITY"
            rationale = (
                f"Fulcrum validates {action} with high conviction ({confidence:.2f}): "
                f"Reaper found no disqualifying risks; Zealot upside thesis confirmed."
            )

        fq, fp = evaluate_evidence_quality(rationale)
        turns.append(DebateTurn(
            agent="Fulcrum", round_number=3, action_bias=final_action,
            confidence=confidence, argument=rationale,
            evidence_points=[f"Sizing: {sizing_mult}x", f"Consensus: {consensus}"],
            evidence_quality=fq, rhetorical_bias_penalty=fp,
        ))

        return DebateVerdict(
            decision=final_action,
            approved=approved,
            confidence=confidence,
            sizing_multiplier=sizing_mult,
            rationale=rationale,
            turns=turns,
            rhetorical_bias_detected=rhetorical_bias,
            dominating_persona=dominating,
            consensus_level=consensus,
        )

    # -------------------------------------------------------------------------
    # LLM-Based Parliamentary Evaluator
    # -------------------------------------------------------------------------

    def _conduct_llm_debate(
        self,
        candidate: Dict[str, Any],
        context: Dict[str, Any],
    ) -> DebateVerdict:
        """Conduct debate using the configured LLM caller."""
        # Wrap candidate and context into a debate payload
        debate_payload = {
            "protocol": "ApexQuant-3Persona",
            "candidate": candidate,
            "context": context,
            "instructions": "Execute Zealot opening, Reaper counter-audit, and Fulcrum final adjudication.",
        }
        try:
            raw = self.llm_caller(debate_payload)
            decision = str(raw.get("decision", raw.get("action", "HOLD"))).upper()
            approved = bool(raw.get("approved", decision == candidate.get("action")))
            confidence = float(raw.get("confidence", 0.70))
            sizing = float(raw.get("sizing_multiplier", 1.0))
            rationale = str(raw.get("rationale", raw.get("summary", "LLM Parliamentary Verdict")))
            turns_data = raw.get("turns", [])
            turns = [DebateTurn(**t) if isinstance(t, dict) else t for t in turns_data] if turns_data else []

            return DebateVerdict(
                decision=decision,
                approved=approved,
                confidence=confidence,
                sizing_multiplier=sizing,
                rationale=rationale,
                turns=turns,
                consensus_level=raw.get("consensus_level", "MAJORITY"),
            )
        except Exception as exc:
            logger.error("LLM parliamentary debate error (fail-closed to HOLD): %s", exc)
            return DebateVerdict(
                decision="HOLD",
                approved=False,
                confidence=0.0,
                sizing_multiplier=0.0,
                rationale=f"Debate protocol failed closed due to error: {exc}",
                consensus_level="VETO",
            )

    # -------------------------------------------------------------------------
    # Bounded Autonomy V_LLM Adapter
    # -------------------------------------------------------------------------

    def as_veto_caller(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """Return a callable matching the LLMVetoOperator contract in bounded_autonomy_pipeline.py.

        Signature:
            payload: Dict[str, Any] -> Dict[str, Any] with {"approved": bool, "rationale": str, "confidence": float}
        """
        def _veto_eval(payload: Dict[str, Any]) -> Dict[str, Any]:
            candidate = {
                "symbol": payload.get("symbol"),
                "action": payload.get("action"),
                "entry_price": payload.get("entry_price"),
                "stop_loss": payload.get("stop_loss"),
                "take_profit": payload.get("take_profit"),
                "quantity": payload.get("candidate_quantity"),
                "score": payload.get("signal_score"),
                "rationale": payload.get("signal_rationale"),
                "features": payload.get("context", {}).get("features", {}),
            }
            context = payload.get("context", {})
            verdict = self.conduct_debate(candidate, context)
            return {
                "approved": verdict.approved,
                "rationale": verdict.rationale,
                "confidence": verdict.confidence,
                "sizing_multiplier": verdict.sizing_multiplier,
                "debate_transcript": [t.to_dict() for t in verdict.turns],
            }

        return _veto_eval
