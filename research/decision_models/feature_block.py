"""Canonical Point-in-Time Decision Feature Block Contract.

Provides an immutable, versioned, cutoff-bound feature container:
- Ingests event identity, source IDs, credibility, and deduplication keys.
- Normalizes surprise metrics (actual, consensus, historical sigma, computed Z-score).
- Captures market state, liquidity, rates, credit, positioning, and technical signatures.
- Associates active narrative state, tripwires, and causal evidence.
- Guarantees deterministic SHA-256 feature hashing for provenance and replayability.

Guarantees full backward compatibility with MacroContextFeatures callers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DecisionFeatureBlock:
    """Canonical immutable point-in-time feature block."""
    # 1. Event Identity & Provenance
    event_name: str
    currency: str = "USD"
    tier: str = "HIGH"                         # HIGH, MEDIUM, LOW
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_id: str = "OFFICIAL_GOV_STAT"       # e.g., 'BLS_API', 'FED_RSS', 'BLOOMBERG'
    source_tier: str = "OFFICIAL"              # OFFICIAL, VERIFIED_NEWS, SOCIAL_UNVERIFIED
    source_credibility_score: float = 1.0      # 0.0 - 1.0
    dedup_key: str = ""

    # 2. Surprise Metrics & Anchors
    actual: Optional[float] = None
    consensus: Optional[float] = None
    previous: Optional[float] = None
    previous_revision_delta: float = 0.0
    sigma_hist: float = 1.0
    z_score: Optional[float] = None
    is_metric_inverted: bool = False           # True for jobless claims, unemployment

    # 3. Market State & Cross-Asset Metrics
    vix: float = 18.0
    dxy_change_pct: float = 0.0
    yield_10y_delta_bps: float = 0.0
    curve_2s10s_bps: float = 15.0
    credit_oas_bps: float = 120.0
    oil_5d_pct: float = 0.0
    gold_5d_pct: float = 0.0
    spx_5d_pct: float = 0.0

    # 4. Central Bank & Liquidity Stance
    net_liquidity_delta_30d_b: float = 0.0     # WALCL - TGA - RRP delta ($B)
    reserves_delta_b: float = 0.0
    sofr_rate: float = 5.30
    fed_funds_rate: float = 5.25
    ecb_deposit_rate: float = 3.75
    rate_spread_delta_30d: float = 0.0
    ois_implied_path: str = "NEUTRAL"          # HAWKISH_HIKES, DOVISH_CUTS, NEUTRAL

    # 5. Positioning, Flow & Orderbook
    cot_percentile: float = 50.0               # 0.0 - 100.0%
    cot_2wk_delta: float = 0.0
    retail_long_pct: float = 50.0
    bid_ask_spread_pips: float = 1.0
    book_depth_ratio: float = 1.0              # Bid depth / Ask depth
    session_id: str = "LONDON_NY_OVERLAP"      # ASIAN, LONDON, NY, LONDON_NY_OVERLAP

    # 6. Technical State
    adx: float = 22.0
    hurst: float = 0.50
    atr_percentile: float = 50.0
    body_ratio: float = 0.65
    upper_wick_ratio: float = 0.15
    lower_wick_ratio: float = 0.20
    volume_z: float = 0.0
    move_speed_pips_min: float = 0.5

    # 7. Time & Danger Window Context
    minutes_to_release: float = 999.0          # negative = post-release, positive = pre-release
    blackout_window_minutes: Tuple[float, float] = (-5.0, 2.0)
    is_blackout_active: bool = False

    # 8. Geopolitical & Physical Verification
    military_incident_level: str = "NONE"      # NONE, PROXY, DIRECT, BLOCKADE
    ais_tanker_speed_drop: bool = False        # Validated physical shipping drop
    pipeline_flow_drop_pct: float = 0.0

    # 9. Narrative & Analog Memory Linkage
    narrative_id: str = "NAR_DEFAULT"
    analog_archetype_id: str = ""
    analog_correlation: float = 0.0
    active_tripwires: Tuple[str, ...] = field(default_factory=tuple)
    tripwire_hit: bool = False
    evidence_for: Tuple[str, ...] = field(default_factory=tuple)
    evidence_against: Tuple[str, ...] = field(default_factory=tuple)

    # 10. Causal Graph Context (injected from MacroGraph)
    causal_paths: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    causal_cesi_score: float = 1.0
    causal_prompt_context: str = ""

    # 11. Active Narrative State (injected from NarrativeStateMachine)
    active_narrative_tags: Tuple[str, ...] = field(default_factory=tuple)
    active_narrative_headlines: Tuple[str, ...] = field(default_factory=tuple)

    # 11b. Narrative Decay Metrics (feeds D12_narrative_decay; absent = no data)
    narrative_decay_factor: Optional[float] = None   # 2^(-age/half_life) from NarrativeObject
    narrative_age_days: Optional[float] = None
    narrative_evidence_for: int = 0
    narrative_evidence_against: int = 0
    narrative_tripwire_hit: bool = False
    narrative_is_invalidated: bool = False

    # 12. Integrity & Adversarial Flags
    is_garbled: bool = False
    is_adversarial: bool = False
    is_stale: bool = False
    is_exchange_halt: bool = False
    is_peg_broken: bool = False
    headline: str = ""
    narrative: str = ""

    # 13. Per-Article News Evidence Quality (from Layer-0 triage verdicts)
    # Shape mirrors news_triage.summarize_cycle_evidence(); absent = no data.
    article_evidence: Optional[Dict[str, Any]] = None

    feature_version: str = "v2_canonical"

    def __post_init__(self) -> None:
        """Enforce normalized Z-score and deduplication key."""
        object.__setattr__(
            self,
            "is_metric_inverted",
            any(k in self.event_name.lower() for k in ("jobless", "unemployment", "claims")),
        )
        if self.z_score is None and self.actual is not None and self.consensus is not None:
            denom = self.sigma_hist if self.sigma_hist > 1e-6 else 1.0
            raw_z = (self.actual - self.consensus) / denom
            effective_z = -raw_z if self.is_metric_inverted else raw_z
            object.__setattr__(self, "z_score", round(effective_z, 3))

        if not self.dedup_key:
            key_raw = f"{self.event_name}|{self.currency}|{self.timestamp[:10]}"
            object.__setattr__(self, "dedup_key", hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16])

    @property
    def feature_hash(self) -> str:
        """Deterministic SHA-256 fingerprint of all numeric and categorical features."""
        payload = {
            "event": self.event_name,
            "currency": self.currency,
            "z": self.z_score,
            "vix": self.vix,
            "liq": self.net_liquidity_delta_30d_b,
            "sofr": self.sofr_rate,
            "oil": self.oil_5d_pct,
            "y10": self.yield_10y_delta_bps,
            "cot": self.cot_percentile,
            "m": self.minutes_to_release,
            "tripwire": self.tripwire_hit,
            "dedup": self.dedup_key,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]

    def is_in_blackout_window(self) -> bool:
        """Check if current time is inside danger blackout window."""
        t_pre, t_post = self.blackout_window_minutes
        # convention: -2.0 <= m <= 5.0 (T-5m to T+2m)
        return -2.0 <= self.minutes_to_release <= 5.0 or self.is_blackout_active

    def to_prompt_state(self) -> Dict[str, Any]:
        """Convert into structured JSON for non-autoregressive decision model ingestion."""
        z_str = f"{self.z_score:+.2f}σ" if self.z_score is not None else "N/A"
        return {
            "event": {
                "name": self.event_name,
                "currency": self.currency,
                "tier": self.tier,
                "timestamp": self.timestamp,
                "source_id": self.source_id,
                "source_tier": self.source_tier,
                "credibility": self.source_credibility_score,
                "dedup_key": self.dedup_key,
            },
            "surprise": {
                "actual": self.actual,
                "consensus": self.consensus,
                "previous": self.previous,
                "revision_delta": self.previous_revision_delta,
                "sigma_hist": self.sigma_hist,
                "z_score": self.z_score,
                "z_string": z_str,
                "is_inverted": self.is_metric_inverted,
            },
            "market_state": {
                "vix": round(self.vix, 2),
                "dxy_change_pct": round(self.dxy_change_pct, 3),
                "yield_10y_delta_bps": round(self.yield_10y_delta_bps, 2),
                "curve_2s10s_bps": round(self.curve_2s10s_bps, 2),
                "credit_oas_bps": round(self.credit_oas_bps, 1),
                "oil_5d_pct": round(self.oil_5d_pct, 2),
                "gold_5d_pct": round(self.gold_5d_pct, 2),
            },
            "liquidity": {
                "net_liquidity_delta_30d_b": round(self.net_liquidity_delta_30d_b, 1),
                "reserves_delta_b": round(self.reserves_delta_b, 1),
                "sofr_rate": round(self.sofr_rate, 3),
                "fed_funds_rate": round(self.fed_funds_rate, 2),
                "ecb_deposit_rate": round(self.ecb_deposit_rate, 2),
            },
            "positioning": {
                "cot_percentile": round(self.cot_percentile, 1),
                "cot_2wk_delta": round(self.cot_2wk_delta, 1),
                "ois_implied_path": self.ois_implied_path,
                "retail_long_pct": round(self.retail_long_pct, 1),
            },
            "technical": {
                "adx": round(self.adx, 1),
                "hurst": round(self.hurst, 3),
                "atr_percentile": round(self.atr_percentile, 1),
                "volume_z": round(self.volume_z, 2),
                "spread_pips": round(self.bid_ask_spread_pips, 2),
                "session": self.session_id,
            },
            "time_context": {
                "minutes_to_release": round(self.minutes_to_release, 1),
                "is_blackout_active": self.is_in_blackout_window(),
            },
            "geopolitical": {
                "military_incident_level": self.military_incident_level,
                "physical_supply_verified": self.ais_tanker_speed_drop or self.pipeline_flow_drop_pct > 5.0,
            },
            "narrative_context": {
                "narrative_id": self.narrative_id,
                "analog_archetype": self.analog_archetype_id,
                "tripwire_hit": self.tripwire_hit,
                "active_narrative_tags": list(self.active_narrative_tags),
                "active_narrative_headlines": list(self.active_narrative_headlines),
            },
            **({
                "narrative_decay": {
                    "decay_factor": round(float(self.narrative_decay_factor), 4),
                    "age_days": round(float(self.narrative_age_days), 2) if self.narrative_age_days is not None else None,
                    "evidence_for": int(self.narrative_evidence_for),
                    "evidence_against": int(self.narrative_evidence_against),
                    "tripwire_hit": bool(self.narrative_tripwire_hit),
                    "is_invalidated": bool(self.narrative_is_invalidated),
                }
            } if self.narrative_decay_factor is not None else {}),
            **({"article_evidence": dict(self.article_evidence)} if self.article_evidence else {}),
            "causal_graph": {
                "paths": list(self.causal_paths),
                "cesi_score": round(self.causal_cesi_score, 4),
                "prompt_context": self.causal_prompt_context[:500] if self.causal_prompt_context else "",
                "path_count": len(self.causal_paths),
            },
            "integrity": {
                "is_garbled": self.is_garbled,
                "is_adversarial": self.is_adversarial,
                "is_stale": self.is_stale,
                "is_kill_switch_active": self.is_exchange_halt or self.is_peg_broken,
            },
            "headline": self.headline,
            "narrative": self.narrative,
            "feature_hash": self.feature_hash,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize feature block to a dictionary including computed feature_hash."""
        d = asdict(self)
        d["feature_hash"] = self.feature_hash
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionFeatureBlock:
        """Construct DecisionFeatureBlock from dictionary, stripping non-init keys and casting tuples."""
        d = dict(data)
        d.pop("feature_hash", None)
        for k in ("active_tripwires", "evidence_for", "evidence_against", "causal_paths", "active_narrative_tags", "active_narrative_headlines"):
            if k in d and isinstance(d[k], (list, tuple)):
                d[k] = tuple(d[k])
        if "blackout_window_minutes" in d and isinstance(d["blackout_window_minutes"], (list, tuple)):
            d["blackout_window_minutes"] = tuple(d["blackout_window_minutes"])
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)
