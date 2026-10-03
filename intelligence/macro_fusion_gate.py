"""Macro Fusion Gate & OpportunityBus Arbiter.

Enforces zero-authority advisory gating between TechnicalNet candidate proposals
and RiskController execution:
1. Fast-Path Gating: Evaluates D0 (Credibility), D1 (Surprise), D2 (Blackout), D9 (Emergency Kill-Switch).
2. Deep Macro Arbitration: Queries D8 Opportunity Arbitration with currency cluster awareness.
3. Invariant: execution_authority is ALWAYS False.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from research.decision_models.extended_taxonomy import (
    D0_1_SourceCredibility,
    D2_BlackoutGate,
    D8_OpportunityArbitration,
    D9_EmergencyKillSwitch,
    DecisionBundle,
    DecisionRecord,
)
from research.decision_models.feature_block import DecisionFeatureBlock
from research.decision_models.deterministic_labeler_expanded import (
    label_d0_1_source_credibility,
    label_d1_economic_surprise,
    label_d2_blackout_gate,
    label_d3_macro_regime,
    label_d4_liquidity_stance,
    label_d6_yield_curve_divergence,
    label_d7_analog_falsification,
    label_d8_opportunity_arbitration,
    label_d9_emergency_kill_switch,
)
from research.decision_models.narrative_state import NarrativeStateMachine

log = logging.getLogger("MacroFusionGate")


@dataclass(frozen=True)
class MacroGateVerdict:
    """Outcome of Macro Fusion Gate arbitration."""
    candidate_id: str
    symbol: str
    proposed_side: str
    verdict: str                               # STRONG_PROCEED, CONDITIONAL_HALF_RISK, REJECT_MACRO_CONFLICT, BLACKOUT_POSTPONE
    risk_multiplier: float                     # 1.0, 0.5, 0.0
    is_vetoed: bool
    rejection_reasons: List[str]
    bundle_ref: str
    feature_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    execution_authority: bool = False          # CRITICAL INVARIANT: ALWAYS FALSE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "proposed_side": self.proposed_side,
            "verdict": self.verdict,
            "risk_multiplier": self.risk_multiplier,
            "is_vetoed": self.is_vetoed,
            "rejection_reasons": self.rejection_reasons,
            "bundle_ref": self.bundle_ref,
            "feature_hash": self.feature_hash,
            "created_at": self.created_at,
            "execution_authority": self.execution_authority,
        }


class MacroFusionGate:
    """Production gateway connecting OpportunityBus proposals with Macro-Cognitive engine."""

    def __init__(self, narrative_sm: Optional[NarrativeStateMachine] = None) -> None:
        self.narrative_sm = narrative_sm or NarrativeStateMachine()

    def arbitrate_proposal(
        self,
        candidate: Dict[str, Any],
        features: DecisionFeatureBlock,
        model_decision_bundle: Optional[DecisionBundle] = None,
    ) -> MacroGateVerdict:
        """Arbitrate a technical proposal against the macro-fundamental backdrop."""
        candidate_id = str(candidate.get("candidate_id") or "UNKNOWN")
        symbol = str(candidate.get("symbol") or "EURUSD").upper()
        side = str(candidate.get("side_hint") or candidate.get("side") or "BUY").upper()

        rejection_reasons: List[str] = []
        is_vetoed = False

        # 1. Layer 0: Source Credibility & Adversarial Gate
        cred = label_d0_1_source_credibility(features)
        if cred == D0_1_SourceCredibility.SPOOF_REJECTED:
            rejection_reasons.append("Layer 0: Adversarial spoofing or corrupted source detected")
            is_vetoed = True

        # 2. Layer 1b: Emergency Flash Event Kill-Switch (D9)
        d9 = label_d9_emergency_kill_switch(features)
        if d9 == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE:
            rejection_reasons.append("Layer 1b: Emergency halt active (VIX >= 35 or exchange/peg shock)")
            is_vetoed = True

        # 3. Layer 1: Danger Window Blackout Gate (D2)
        d2 = label_d2_blackout_gate(features)
        if d2 == D2_BlackoutGate.BLACKOUT_FREEZE:
            rejection_reasons.append(f"Layer 1: Blackout danger window active (m={features.minutes_to_release:.1f}m)")
            is_vetoed = True

        # 4. Ingest narrative state
        narrative = self.narrative_sm.active_narrative
        if narrative.is_invalidated:
            rejection_reasons.append(f"Narrative: Active thesis invalidated: {narrative.invalidation_reason}")

        # 5. Layer 6: Opportunity Arbitration (D8)
        d8 = label_d8_opportunity_arbitration(features, proposed_direction=side, symbol=symbol)

        # Reconcile with Model Bundle if present (Deterministic Firewall)
        if model_decision_bundle:
            model_d8 = model_decision_bundle.get_option("D8_opportunity_arbitration")
            # If model approves but deterministic rules veto -> ENFORCE VETO
            if is_vetoed and model_d8 in ("STRONG_PROCEED", "CONDITIONAL_HALF_RISK"):
                log.warning("Deterministic Post-Validator overriding model approval for %s %s due to safety veto", symbol, side)

        # Determine Final Verdict
        if is_vetoed:
            # Only a pure timing freeze deserves POSTPONE (retry after the
            # window). Every D9 kill-switch / L0 spoof trigger also freezes D2,
            # so a naive "d2 frozen => postpone" mapping would re-fire a vetoed
            # trade after the window — those are condition-based REJECTs.
            if d2 == D2_BlackoutGate.BLACKOUT_FREEZE and not (
                cred == D0_1_SourceCredibility.SPOOF_REJECTED
                or d9 == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE
            ):
                verdict = D8_OpportunityArbitration.BLACKOUT_POSTPONE.value
            else:
                verdict = D8_OpportunityArbitration.REJECT_MACRO_CONFLICT.value
            risk_mult = 0.0
        elif d8 == D8_OpportunityArbitration.REJECT_MACRO_CONFLICT:
            verdict = D8_OpportunityArbitration.REJECT_MACRO_CONFLICT.value
            risk_mult = 0.0
            rejection_reasons.append(f"Macro conflict: Trade side {side} violates macro surprise or regime for {symbol}")
            is_vetoed = True
        elif d8 == D8_OpportunityArbitration.STRONG_PROCEED:
            verdict = D8_OpportunityArbitration.STRONG_PROCEED.value
            risk_mult = 1.0
        else:
            verdict = D8_OpportunityArbitration.CONDITIONAL_HALF_RISK.value
            risk_mult = 0.5

        bundle_ref = model_decision_bundle.bundle_id if model_decision_bundle else f"BUNDLE_{features.feature_hash[:12]}"

        return MacroGateVerdict(
            candidate_id=candidate_id,
            symbol=symbol,
            proposed_side=side,
            verdict=verdict,
            risk_multiplier=risk_mult,
            is_vetoed=is_vetoed,
            rejection_reasons=rejection_reasons,
            bundle_ref=bundle_ref,
            feature_hash=features.feature_hash,
            execution_authority=False,  # ALWAYS FALSE
        )
