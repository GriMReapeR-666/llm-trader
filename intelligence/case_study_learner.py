"""Autonomous Case Study Research, Knowledge Expansion & Graph Cognition Learner.

Enables the system to autonomously ingest, parse, validate, and learn new
macro/geopolitical case studies, relationship actors, causal transmission channels,
and falsification tripwires into the live knowledge graph and case study registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from intelligence.case_study_matcher import (
    CaseStudyArchetype,
    CASE_STUDY_REGISTRY,
    get_case_study_matcher,
)

log = logging.getLogger("CaseStudyLearner")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CaseStudyLearningReport:
    """Result of an autonomous case study ingestion and learning operation."""
    case_id: str
    label: str
    status: str                         # 'LEARNED_SUCCESSFULLY', 'DUPLICATE_UPDATED', 'VALIDATION_FAILED'
    primary_domain: str
    actors_and_entities_count: int
    transmission_channels_count: int
    tripwires_count: int
    persisted_to_disk: bool
    persisted_path: str
    timestamp: str
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["execution_authority"] = False
        return d


class CaseStudyLearner:
    """Autonomous engine for continuous research and assimilation of new macro case studies."""

    def __init__(self, persistence_file: str = "artifacts/learned_case_studies.json") -> None:
        self.persistence_file = persistence_file
        self.matcher = get_case_study_matcher()
        self._load_persisted_cases()

    def _load_persisted_cases(self) -> int:
        """Load previously learned case studies from disk and register them."""
        if not os.path.exists(self.persistence_file):
            return 0

        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = 0
            for item in data:
                archetype = CaseStudyArchetype(
                    case_id=item["case_id"],
                    label=item["label"],
                    primary_domain=item["primary_domain"],
                    historical_date_window=item.get("historical_date_window", "N/A"),
                    chokepoints_affected=tuple(item.get("chokepoints_affected", ())),
                    primary_entities=tuple(item.get("primary_entities", ())),
                    transmission_channels=tuple(item.get("transmission_channels", ())),
                    lead_time_days=int(item.get("lead_time_days", 0)),
                    duration_days=int(item.get("duration_days", 30)),
                    tau_substitution_horizon=item.get("tau_substitution_horizon", "DAYS"),
                    core_spread_distortions=tuple(item.get("core_spread_distortions", ())),
                    prompt_curve_regime=item.get("prompt_curve_regime", "BACKWARDATION"),
                    historical_outcomes=item.get("historical_outcomes", {}),
                    falsification_tripwires=tuple(item.get("falsification_tripwires", ())),
                    reference_url=item.get("reference_url", "https://antigravity.internal"),
                )
                self.matcher.registry[archetype.case_id] = archetype
                loaded += 1
            log.info("Loaded %d persisted case studies into matcher registry.", loaded)
            return loaded
        except Exception as e:
            log.warning("Failed to load persisted case studies from %s: %s", self.persistence_file, e)
            return 0

    def learn_case_study(
        self,
        case_id: str,
        label: str,
        primary_domain: str,
        primary_entities: List[str],
        chokepoints_affected: Optional[List[str]] = None,
        transmission_channels: Optional[List[str]] = None,
        historical_date_window: str = "Unfolding Real-Time Incident",
        lead_time_days: int = 0,
        duration_days: int = 30,
        tau_substitution_horizon: str = "DAYS",
        core_spread_distortions: Optional[List[str]] = None,
        prompt_curve_regime: str = "BACKWARDATION",
        historical_outcomes: Optional[Dict[str, str]] = None,
        falsification_tripwires: Optional[List[str]] = None,
        reference_url: str = "https://antigravity.internal",
    ) -> CaseStudyLearningReport:
        """
        Validate, register, and persist a new case study archetype into the live knowledge base.
        """
        now_utc = utcnow()
        clean_case_id = case_id.strip().lower().replace(" ", "_")

        # 1. Validation
        if not clean_case_id or not label or not primary_entities:
            return CaseStudyLearningReport(
                case_id=clean_case_id,
                label=label,
                status="VALIDATION_FAILED",
                primary_domain=primary_domain,
                actors_and_entities_count=len(primary_entities),
                transmission_channels_count=len(transmission_channels or []),
                tripwires_count=len(falsification_tripwires or []),
                persisted_to_disk=False,
                persisted_path=self.persistence_file,
                timestamp=now_utc,
                execution_authority=False,
            )

        # 2. Construct Archetype
        archetype = CaseStudyArchetype(
            case_id=clean_case_id,
            label=label.strip(),
            primary_domain=primary_domain.strip().upper(),
            historical_date_window=historical_date_window.strip(),
            chokepoints_affected=tuple(c.strip().upper() for c in (chokepoints_affected or [])),
            primary_entities=tuple(e.strip().upper() for e in primary_entities),
            transmission_channels=tuple(t.strip().upper() for t in (transmission_channels or [])),
            lead_time_days=max(0, lead_time_days),
            duration_days=max(1, duration_days),
            tau_substitution_horizon=tau_substitution_horizon.strip().upper(),
            core_spread_distortions=tuple(s.strip().upper() for s in (core_spread_distortions or [])),
            prompt_curve_regime=prompt_curve_regime.strip().upper(),
            historical_outcomes=historical_outcomes or {"general": "Disruption observed across primary transmission channels"},
            falsification_tripwires=tuple(f.strip() for f in (falsification_tripwires or ["Alternative logistics restore 100% capacity within 7 days"])),
            reference_url=reference_url.strip(),
        )

        # 3. Register in Memory
        is_update = clean_case_id in self.matcher.registry
        self.matcher.registry[clean_case_id] = archetype

        # 4. Persist to Disk
        persisted = self._save_persisted_case(archetype)

        # 5. Bridge into TradingMemory journal
        self._bridge_to_journal(archetype)

        status_msg = "DUPLICATE_UPDATED" if is_update else "LEARNED_SUCCESSFULLY"
        log.info("Learned case study: %s (%s) - status: %s", label, clean_case_id, status_msg)

        return CaseStudyLearningReport(
            case_id=clean_case_id,
            label=label,
            status=status_msg,
            primary_domain=archetype.primary_domain,
            actors_and_entities_count=len(archetype.primary_entities),
            transmission_channels_count=len(archetype.transmission_channels),
            tripwires_count=len(archetype.falsification_tripwires),
            persisted_to_disk=persisted,
            persisted_path=self.persistence_file,
            timestamp=now_utc,
            execution_authority=False,
        )

    def learn_from_json_file(self, file_path: str) -> CaseStudyLearningReport:
        """Parse and learn a case study from a local JSON document."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Case study file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        return self.learn_case_study(
            case_id=payload.get("case_id", f"case_{payload.get('label', 'unknown').lower().replace(' ', '_')}"),
            label=payload.get("label", "Unknown Incident"),
            primary_domain=payload.get("primary_domain", "MACRO_REGIME"),
            primary_entities=payload.get("primary_entities", ["GROWTH", "INFLATION"]),
            chokepoints_affected=payload.get("chokepoints_affected", []),
            transmission_channels=payload.get("transmission_channels", []),
            historical_date_window=payload.get("historical_date_window", "2026-Incident"),
            lead_time_days=payload.get("lead_time_days", 0),
            duration_days=payload.get("duration_days", 30),
            tau_substitution_horizon=payload.get("tau_substitution_horizon", "DAYS"),
            core_spread_distortions=payload.get("core_spread_distortions", []),
            prompt_curve_regime=payload.get("prompt_curve_regime", "BACKWARDATION"),
            historical_outcomes=payload.get("historical_outcomes", {}),
            falsification_tripwires=payload.get("falsification_tripwires", []),
            reference_url=payload.get("reference_url", "https://antigravity.internal"),
        )

    def _save_persisted_case(self, archetype: CaseStudyArchetype) -> bool:
        """Append or update the archetype in the persistence JSON store."""
        try:
            os.makedirs(os.path.dirname(self.persistence_file) or ".", exist_ok=True)
            existing: Dict[str, Dict[str, Any]] = {}
            if os.path.exists(self.persistence_file):
                try:
                    with open(self.persistence_file, "r", encoding="utf-8") as f:
                        for row in json.load(f):
                            existing[row["case_id"]] = row
                except Exception:
                    existing = {}

            existing[archetype.case_id] = {
                "case_id": archetype.case_id,
                "label": archetype.label,
                "primary_domain": archetype.primary_domain,
                "historical_date_window": archetype.historical_date_window,
                "chokepoints_affected": list(archetype.chokepoints_affected),
                "primary_entities": list(archetype.primary_entities),
                "transmission_channels": list(archetype.transmission_channels),
                "lead_time_days": archetype.lead_time_days,
                "duration_days": archetype.duration_days,
                "tau_substitution_horizon": archetype.tau_substitution_horizon,
                "core_spread_distortions": list(archetype.core_spread_distortions),
                "prompt_curve_regime": archetype.prompt_curve_regime,
                "historical_outcomes": archetype.historical_outcomes,
                "falsification_tripwires": list(archetype.falsification_tripwires),
                "reference_url": archetype.reference_url,
            }

            with open(self.persistence_file, "w", encoding="utf-8") as f:
                json.dump(list(existing.values()), f, indent=2)

            return True
        except Exception as e:
            log.warning("Failed to persist case study %s: %s", archetype.case_id, e)
            return False

    def _bridge_to_journal(self, archetype: CaseStudyArchetype) -> None:
        """Bridge a learned case study into TradingMemory journal for retrieval."""
        try:
            from intelligence.trading_memory import TradingMemory
            mem = TradingMemory()
            mem.propose_case_study_memory(
                case_id=archetype.case_id,
                label=archetype.label,
                primary_domain=archetype.primary_domain,
                entities=list(archetype.primary_entities),
                chokepoints=list(archetype.chokepoints_affected),
                transmission_channels=list(archetype.transmission_channels),
                tripwires=list(archetype.falsification_tripwires),
                historical_outcomes=archetype.historical_outcomes,
                lead_time_days=archetype.lead_time_days,
                duration_days=archetype.duration_days,
                tau_substitution_horizon=archetype.tau_substitution_horizon,
                reference_url=archetype.reference_url,
            )
        except Exception as e:
            log.debug("Failed to bridge case study %s to journal: %s", archetype.case_id, e)


_GLOBAL_LEARNER: Optional[CaseStudyLearner] = None


def get_case_study_learner() -> CaseStudyLearner:
    """Global singleton accessor for CaseStudyLearner."""
    global _GLOBAL_LEARNER
    if _GLOBAL_LEARNER is None:
        _GLOBAL_LEARNER = CaseStudyLearner()
    return _GLOBAL_LEARNER
