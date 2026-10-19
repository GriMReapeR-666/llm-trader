"""Open-Source Intelligence Ingestion Layer (GDELT 2.0, ACLED, DBnomics, BIS).

Provides standardized, non-paywalled telemetry for institutional macro trading:
1. GDELT 2.0 Conflict Scoring & Goldstein Intensity (-10.0 to +10.0).
2. ACLED Geocoded Chokepoint & Maritime Incident Tracking.
3. DBnomics Global Economic Indicator Normalization.
4. BIS Effective Exchange Rates (EER) and Cross-Border Liquidity.

Doctrine:
- execution_authority: strictly False.
- Fail-closed: missing/unparseable telemetry defaults safely to neutral baseline.
- Pure Python stdlib & NumPy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

EXECUTION_AUTHORITY = False


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


class GDELTFeed:
    """Parses GDELT 2.0 event streams, actor-action-target triplets, and Goldstein scores."""

    def compute_conflict_intensity(
        self, records: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not records:
            return {
                "status": "READY_FOR_REVIEW",
                "mean_goldstein": 0.0,
                "material_conflict_ratio": 0.0,
                "normalized_conflict_intensity": 0.0,
                "execution_authority": False,
            }

        goldstein_scores: List[float] = []
        material_conflicts = 0

        for r in records:
            g = _safe_float(r.get("goldstein"))
            if g is not None:
                goldstein_scores.append(g)
            qc = r.get("quad_class")
            if qc in (3, 4, "3", "4", "MATERIAL_CONFLICT"):
                material_conflicts += 1

        if not goldstein_scores:
            mean_g = 0.0
        else:
            mean_g = float(np.mean(goldstein_scores))

        mat_ratio = material_conflicts / len(records)
        # Normalized conflict intensity: 0.0 (peace/cooperation) to 1.0 (extreme conflict)
        # Goldstein ranges from -10 (extreme war) to +10 (peace/cooperation)
        norm_intensity = float(np.clip((-mean_g + 10.0) / 20.0, 0.0, 1.0))

        return {
            "status": "READY_FOR_REVIEW",
            "mean_goldstein": round(mean_g, 2),
            "material_conflict_ratio": round(mat_ratio, 4),
            "normalized_conflict_intensity": round(norm_intensity, 4),
            "execution_authority": False,
        }


class ACLEDFeed:
    """Monitors geocoded armed conflict events targeting critical global maritime chokepoints."""

    CHOKEPOINT_BOUNDS = {
        "BAB_EL_MANDEB": {"lat_min": 11.5, "lat_max": 15.0, "lon_min": 42.0, "lon_max": 45.0},
        "STRAIT_OF_HORMUZ": {"lat_min": 25.5, "lat_max": 27.5, "lon_min": 55.0, "lon_max": 57.5},
        "MALACCA_STRAIT": {"lat_min": 1.0, "lat_max": 6.0, "lon_min": 98.0, "lon_max": 104.0},
    }

    def evaluate_chokepoints(
        self, incidents: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        at_risk = {cp: False for cp in self.CHOKEPOINT_BOUNDS}
        disrupted_count = 0

        for inc in incidents:
            lat = _safe_float(inc.get("latitude"))
            lon = _safe_float(inc.get("longitude"))
            loc = str(inc.get("location", "")).upper()

            for cp, bounds in self.CHOKEPOINT_BOUNDS.items():
                in_geo = (
                    lat is not None
                    and lon is not None
                    and bounds["lat_min"] <= lat <= bounds["lat_max"]
                    and bounds["lon_min"] <= lon <= bounds["lon_max"]
                )
                in_text = cp.replace("_", " ") in loc or cp in loc
                if in_geo or in_text:
                    at_risk[cp] = True

        disrupted_count = sum(1 for v in at_risk.values() if v)
        supply_shock = disrupted_count > 0

        return {
            "status": "READY_FOR_REVIEW",
            "chokepoints_at_risk": at_risk,
            "disrupted_chokepoint_count": disrupted_count,
            "supply_shock_detected": supply_shock,
            "execution_authority": False,
        }


class DBnomicsFeed:
    """Normalizes international macro releases from DBnomics (Eurostat, OECD, Cabinet Office Japan)."""

    def normalize_indicator(
        self, series_id: str, raw_series: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not raw_series:
            return {
                "status": "INSUFFICIENT_DATA",
                "series_id": series_id,
                "latest_value": None,
                "mom_change": None,
                "execution_authority": False,
            }

        sorted_points = []
        for pt in raw_series:
            val = _safe_float(pt.get("value"))
            period = str(pt.get("period", ""))
            if val is not None and period:
                sorted_points.append((period, val))

        sorted_points.sort(key=lambda x: x[0])
        if not sorted_points:
            return {"status": "INSUFFICIENT_DATA", "series_id": series_id, "execution_authority": False}

        latest = sorted_points[-1][1]
        prior = sorted_points[-2][1] if len(sorted_points) >= 2 else latest
        mom = latest - prior

        return {
            "status": "READY_FOR_REVIEW",
            "series_id": series_id,
            "latest_period": sorted_points[-1][0],
            "latest_value": round(latest, 4),
            "prior_value": round(prior, 4),
            "mom_change": round(mom, 4),
            "execution_authority": False,
        }


class BISFeed:
    """Parses Bank for International Settlements effective exchange rate indices."""

    def evaluate_effective_exchange_rate(
        self, currency: str, raw_eer: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not raw_eer:
            return {
                "status": "INSUFFICIENT_DATA",
                "currency": currency,
                "execution_authority": False,
            }

        sorted_eer = []
        for pt in raw_eer:
            val = _safe_float(pt.get("value"))
            period = str(pt.get("period", ""))
            if val is not None and period:
                sorted_eer.append((period, val))

        sorted_eer.sort(key=lambda x: x[0])
        if len(sorted_eer) < 2:
            val = sorted_eer[0][1] if sorted_eer else 100.0
            return {
                "status": "READY_FOR_REVIEW",
                "currency": currency,
                "latest_eer": val,
                "eer_change_pct": 0.0,
                "direction": "NEUTRAL",
                "execution_authority": False,
            }

        curr_val = sorted_eer[-1][1]
        prev_val = sorted_eer[-2][1]
        pct = ((curr_val - prev_val) / max(prev_val, 1e-6)) * 100.0

        direction = "STRENGTHENING" if pct > 0.1 else ("WEAKENING" if pct < -0.1 else "NEUTRAL")

        return {
            "status": "READY_FOR_REVIEW",
            "currency": currency,
            "latest_period": sorted_eer[-1][0],
            "latest_eer": round(curr_val, 2),
            "prior_eer": round(prev_val, 2),
            "eer_change_pct": round(pct, 2),
            "direction": direction,
            "execution_authority": False,
        }


class OpenIntelligenceAggregator:
    """Aggregates all open-source intelligence feeds into a unified telemetry vector."""

    def __init__(self) -> None:
        self.gdelt = GDELTFeed()
        self.acled = ACLEDFeed()
        self.dbnomics = DBnomicsFeed()
        self.bis = BISFeed()

    def get_geopolitical_telemetry_vector(
        self,
        gdelt_records: Optional[Sequence[Dict[str, Any]]] = None,
        acled_incidents: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        gdelt_res = self.gdelt.compute_conflict_intensity(gdelt_records or [])
        acled_res = self.acled.evaluate_chokepoints(acled_incidents or [])

        return {
            "status": "READY_FOR_REVIEW",
            "goldstein_conflict_intensity": gdelt_res["normalized_conflict_intensity"],
            "chokepoint_disruptions": acled_res["chokepoints_at_risk"],
            "supply_shock_detected": acled_res["supply_shock_detected"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_authority": False,
        }
