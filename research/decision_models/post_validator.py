"""Deterministic Macro Post-Validator and Active Learning Disagreement Logger.

Enforces zero-tolerance safety guardrails on top of System 1 decision models:
1. Z-Score Boundary Consistency: If |Z| >= 2.0 and model predicts IN_LINE, override and flag for retraining.
2. Emergency Blackout Freeze Rule: If inside blackout window or VIX >= 30, force blackout freeze.
3. Stress Veto Rule: If stress level is severe or crisis panic active, force execution veto.
4. Active Learning Loop: Records all disagreements to SQLite ledger to generate next-generation contrastive pairs.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from research.decision_models.deterministic_labeler import generate_ground_truth_labels
from research.decision_models.macro_ontology import (
    BlackoutGate,
    EconomicSurprise,
    MacroContextFeatures,
    MacroRegime,
    OpportunityArbitration,
)

log = logging.getLogger("MacroPostValidator")


@dataclass
class PostValidationResult:
    """Outcome of deterministic post-validation pass."""
    raw_answers: Dict[str, Any]
    validated_answers: Dict[str, Any]
    overrides_applied: List[Dict[str, Any]]
    disagreement_count: int
    is_safe_to_execute: bool
    veto_enforced: bool
    retraining_flag: bool
    ground_truth: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeterministicPostValidator:
    """Production safety net and active learning recorder."""

    def __init__(self, db_path: str = "artifacts/llm_decisions.db") -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_disagreement_table()

    def _ensure_disagreement_table(self) -> None:
        """Create active learning ledger table if missing."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS macro_active_learning_disagreements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        event_name TEXT NOT NULL,
                        z_score REAL,
                        vix REAL,
                        question_id TEXT NOT NULL,
                        model_prediction TEXT NOT NULL,
                        deterministic_override TEXT NOT NULL,
                        override_reason TEXT NOT NULL,
                        features_json TEXT NOT NULL
                    )"""
                )
                conn.commit()
        except Exception as exc:
            log.warning("Could not initialize active learning table: %s", exc)

    def log_disagreement(
        self,
        event_name: str,
        z_score: Optional[float],
        vix: float,
        question_id: str,
        model_prediction: Any,
        deterministic_override: Any,
        reason: str,
        features: MacroContextFeatures,
    ) -> None:
        """Log production disagreement to SQLite active learning ledger."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO macro_active_learning_disagreements (
                        created_at, event_name, z_score, vix, question_id,
                        model_prediction, deterministic_override, override_reason, features_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        event_name,
                        z_score,
                        vix,
                        question_id,
                        str(model_prediction),
                        str(deterministic_override),
                        reason,
                        json.dumps(features.to_prompt_state()),
                    ),
                )
                conn.commit()
        except Exception as exc:
            log.warning("Failed to log active learning disagreement: %s", exc)

    def validate(
        self,
        model_answers: Dict[str, Any],
        features: MacroContextFeatures,
        proposed_direction: str = "BUY",
        symbol: str = "EURUSD",
    ) -> PostValidationResult:
        """Apply deterministic post-validation rules to model answers."""
        gt = generate_ground_truth_labels(features, proposed_direction, symbol)
        validated = dict(model_answers)
        overrides: List[Dict[str, Any]] = []
        retraining_flag = False

        z = features.z_score

        # Rule 1: Z-Score Consistency Check (|Z| >= 2.0 cannot be IN_LINE)
        for key in ("D1_economic_surprise", "macro_shock", "macro_surprise"):
            if key in validated and z is not None:
                pred = str(validated[key]).upper()
                if abs(z) >= 2.0 and pred == "IN_LINE":
                    target = gt["D1_economic_surprise"]
                    overrides.append({
                        "question": key,
                        "from": pred,
                        "to": target,
                        "reason": f"Z-score |{z:.2f}| >= 2.0 mathematically forbids IN_LINE",
                    })
                    self.log_disagreement(
                        features.event_name, z, features.vix, key, pred, target,
                        "Z-score |Z| >= 2.0 inconsistency", features
                    )
                    validated[key] = target
                    retraining_flag = True

        # Rule 2: Emergency Blackout Freeze Rule (Safety override)
        # If time is in blackout window, or VIX >= 30, or high tier shock |Z| >= 2.0:
        is_blackout_time = features.is_in_blackout_window()
        is_crisis_vix = features.vix >= 30.0
        is_extreme_shock = (z is not None and abs(z) >= 2.0 and features.tier == "HIGH")

        must_freeze = is_blackout_time or is_crisis_vix or is_extreme_shock or features.is_adversarial

        for key in ("blackout_freeze", "blackout_freeze_gate", "blackout_freeze_boolean"):
            if key in validated:
                current_bool = bool(validated[key])
                if must_freeze and not current_bool:
                    overrides.append({
                        "question": key,
                        "from": current_bool,
                        "to": True,
                        "reason": f"Mandatory blackout safety rule (window={is_blackout_time}, VIX={features.vix}, |Z|={abs(z) if z else 0})",
                    })
                    self.log_disagreement(
                        features.event_name, z, features.vix, key, current_bool, True,
                        "Mandatory blackout safety override", features
                    )
                    validated[key] = True
                    retraining_flag = True

        # Rule 3: Stress Veto Hard Rule
        # If stress level >= 3 or crisis panic, force execution veto
        is_crisis_regime = gt["D3_macro_regime"] == MacroRegime.CRISIS_PANIC.value
        must_veto = must_freeze or is_crisis_regime

        for key in ("execution_veto", "execution_veto_boolean"):
            if key in validated:
                current_veto = bool(validated[key])
                if must_veto and not current_veto:
                    overrides.append({
                        "question": key,
                        "from": current_veto,
                        "to": True,
                        "reason": "Mandatory execution veto during crisis/blackout conditions",
                    })
                    validated[key] = True
                    retraining_flag = True

        # Rule 4: D8 Opportunity Arbitration Consistency
        for key in ("D8_opportunity_arbitration", "opportunity_arbitration"):
            if key in validated:
                pred = validated[key]
                if must_freeze and pred != OpportunityArbitration.BLACKOUT_POSTPONE.value:
                    validated[key] = OpportunityArbitration.BLACKOUT_POSTPONE.value
                    overrides.append({
                        "question": key, "from": pred, "to": OpportunityArbitration.BLACKOUT_POSTPONE.value,
                        "reason": "Blackout window active; arbitrated setup must be postponed"
                    })
                elif is_crisis_regime and pred != OpportunityArbitration.REJECT_MACRO_CONFLICT.value:
                    validated[key] = OpportunityArbitration.REJECT_MACRO_CONFLICT.value
                    overrides.append({
                        "question": key, "from": pred, "to": OpportunityArbitration.REJECT_MACRO_CONFLICT.value,
                        "reason": "Crisis regime forbids pro-cyclical setups"
                    })

        veto_active = any(bool(validated.get(k)) for k in ("execution_veto", "execution_veto_boolean", "blackout_freeze", "blackout_freeze_boolean"))
        is_safe = not veto_active

        return PostValidationResult(
            raw_answers=model_answers,
            validated_answers=validated,
            overrides_applied=overrides,
            disagreement_count=len(overrides),
            is_safe_to_execute=is_safe,
            veto_enforced=veto_active,
            retraining_flag=retraining_flag,
            ground_truth=gt,
        )
