"""Model Staleness Tracker — tracks freshness of ML models and RL policies."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


@dataclass
class ModelEntry:
    name: str
    last_trained_ts: float = 0.0
    last_used_ts: float = 0.0
    train_count: int = 0
    performance: float = 0.0
    staleness_hours: float = 0.0
    status: str = "UNKNOWN"  # ACTIVE, AGING, STALE, CRITICAL


class ModelStalenessTracker:
    STALENESS_THRESHOLDS = {
        "ACTIVE": 24,     # < 24 hours
        "AGING": 72,      # < 72 hours
        "STALE": 168,     # < 7 days
        "CRITICAL": 720,  # > 7 days
    }

    def __init__(self, stale_threshold_hours: float = 168):
        self.models: Dict[str, ModelEntry] = {}
        self.stale_threshold_hours = stale_threshold_hours

    def record_training(self, model_name: str, performance: float = 0.0):
        now = time.time()
        if model_name not in self.models:
            self.models[model_name] = ModelEntry(name=model_name)
        m = self.models[model_name]
        m.last_trained_ts = now
        m.train_count += 1
        m.performance = performance
        self._update_status(m)

    def record_usage(self, model_name: str):
        now = time.time()
        if model_name not in self.models:
            self.models[model_name] = ModelEntry(name=model_name)
        self.models[model_name].last_used_ts = now

    def _update_status(self, m: ModelEntry):
        if m.last_trained_ts == 0:
            m.status = "UNKNOWN"
            return
        hours = (time.time() - m.last_trained_ts) / 3600
        m.staleness_hours = hours
        if hours < self.STALENESS_THRESHOLDS["ACTIVE"]:
            m.status = "ACTIVE"
        elif hours < self.STALENESS_THRESHOLDS["AGING"]:
            m.status = "AGING"
        elif hours < self.STALENESS_THRESHOLDS["STALE"]:
            m.status = "STALE"
        else:
            m.status = "CRITICAL"

    def get_status(self) -> Dict[str, Any]:
        for m in self.models.values():
            self._update_status(m)
        return {
            "models": {name: {"status": m.status, "staleness_hours": round(m.staleness_hours, 1), "train_count": m.train_count, "performance": round(m.performance, 4)} for name, m in self.models.items()},
            "stale_count": sum(1 for m in self.models.values() if m.status in ("STALE", "CRITICAL")),
        }

    def needs_retrain(self, model_name: str) -> bool:
        m = self.models.get(model_name)
        if not m:
            return True
        self._update_status(m)
        return m.status in ("STALE", "CRITICAL")
