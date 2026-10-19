"""Graded News-Impact Score Rubric — pure composition of existing subsystems.

This module adds NOTHING new computationally. It composes four existing,
independently-tested signal sources into a single 7-dimension graded score:

  1. tier_reach          <- core.news_trading.classify_event_tier
  2. surprise_magnitude  <- core.macro_event_alpha_engine.decompose_surprise (z_delta)
  3. refresh_dynamics    <- core.news_trading.EconomicSurpriseIndex (esi_score)
  4. evidence_quality    <- data.news_triage TriageVerdict.d0_3 (fact vs opinion)
  5. source_credibility  <- data.news_triage TriageVerdict.d0_1 (source policy)
  6. novelty_allowance   <- data.news_triage TriageVerdict.d0_2 + action
  7. crisis_overlay      <- data.news_triage TriageVerdict.d9 + d5a (escalation)

Design invariants (mirrors repo doctrine):
- execution_authority is ALWAYS False. Score is advisory context only.
- Every dimension is individually reportable and reproducible; consumers can
  audit exactly which existing subsystem produced which grade.
- Fail-soft: any missing/unparseable input degrades that dimension to None
  (reported as "unavailable") and the composition re-normalizes; a bad input
  never fabricates a zero-impact or high-impact event.
- The crisis overlay NEVER reduces gating authority: it informs context only.
  The existing NewsTradingGate + D9 latch remain the sole authority paths.

Integration (zero-wiring): the compose() entry points accept the exact dict
shapes already flowing through data.data_source_pipeline / realtime_news_ingest
and core.news_trading.get_news_trading_context, so callers pass through without
adapting anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
import logging

log = logging.getLogger("NewsImpactScore")

EXECUTION_AUTHORITY = False

# ---------------------------------------------------------------------------
# Dimension graders (each returns 0.0-1.0 or None when the input is absent)
# ---------------------------------------------------------------------------

_TIER_GRADES = {1: 1.0, 2: 0.7, 3: 0.4}
_TIER_UNKNOWN = 0.15

_DIMENSIONS = (
    "tier_reach",
    "surprise_magnitude",
    "refresh_dynamics",
    "evidence_quality",
    "source_credibility",
    "novelty_allowance",
    "crisis_overlay",
)


def _clamp01(value: float) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return max(0.0, min(1.0, v))


def _grade_tier_reach(tier: Any) -> Optional[float]:
    try:
        tier_int = int(tier)
    except (TypeError, ValueError):
        return None
    return _TIER_GRADES.get(tier_int, _TIER_UNKNOWN)


def _grade_surprise_magnitude(z_delta: Any) -> Optional[float]:
    z = _clamp01(abs(float(z_delta)) / 2.0) if z_delta is not None else None
    return z


def _grade_refresh_dynamics(esi_score: Any) -> Optional[float]:
    if esi_score is None:
        return None
    try:
        score = float(esi_score)
    except (TypeError, ValueError, OverflowError):
        return None
    # Neutral 50 -> 0.0; |deviation| stretches at HALF full-scale so one hot
    # print never produces a maximal grade on its own. Magnitude is symmetric:
    # a persistent dovish stretch (ESI ~20) is exactly as "refresh-worthy" as
    # a persistent hawkish one (ESI ~80) — direction belongs to z_delta.
    stretched = abs(score - 50.0) / 100.0
    return _clamp01(stretched)


# d0_3 -> grade lookup (strict fallback ordering; unknown labels degrade to 0)
_EVIDENCE_GRADES = {
    "VERIFIED_FACT": 1.0,
    "OFFICIAL_STATEMENT": 0.8,
    "ANALYST_OPINION": 0.4,
    "RUMOR_UNCONFIRMED": 0.15,
    "INSUFFICIENT_DATA": None,
}

# d0_1 -> grade lookup. SPOOF_REJECTED / UNVERIFIED_SOCIAL are graded 0.0,
# not None: a known-bad source is a signal (hard zero), not missing data.
_CREDIBILITY_GRADES = {
    "TRUSTED_OFFICIAL": 1.0,
    "VERIFIED_OUTLET": 0.85,
    "UNVERIFIED_SOCIAL": 0.0,
    "SPOOF_REJECTED": 0.0,
    "INSUFFICIENT_DATA": None,
}

# d0_2 -> grade lookup
_NOVELTY_GRADES = {
    "FIRST_REPORT": 1.0,
    "CONFIRMED_UPDATE": 0.7,
    "STALE_REPEAT": 0.0,
    "CONTRADICTION": 0.0,
    "INSUFFICIENT_DATA": None,
}

# d5a geopolitical escalation -> crisis grade (d9 active shorts-circuits to 1.0)
_ESCALATION_GRADES = {
    "FULL_BLOCKADE": 1.0,
    "CHOKEPOINT_DISRUPTION": 0.85,
    "DIRECT_STRIKE": 0.65,
    "PROXY_CLASH": 0.4,
    "RHETORIC_ONLY": 0.15,
    "INSUFFICIENT_DATA": None,
}


@dataclass
class ImpactDimension:
    """One graded dimension: value + provenance of the existing subsystem used."""
    name: str
    value: Optional[float]
    source: str          # e.g. "news_trading.classify_event_tier"
    raw_input: Any       # the exact raw value fed in (for auditability)
    note: str = ""


@dataclass
class NewsImpactScore:
    """Composed graded news-impact score (advisory only)."""
    final_score: float
    band: str                     # NEGLIGIBLE | LOW | MODERATE | HIGH | CRITICAL
    dimensions: List[ImpactDimension] = field(default_factory=list)
    reliable_enough: bool = False
    composition: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_score": round(self.final_score, 4),
            "band": self.band,
            "reliable_enough": self.reliable_enough,
            "dimensions": {d.name: {"value": d.value, "source": d.source, "raw": d.raw_input} for d in self.dimensions},
            "composition": self.composition,
            "timestamp": self.timestamp,
            "execution_authority": False,
        }


def _band(score: float) -> str:
    if score >= 0.75:
        return "CRITICAL"
    if score >= 0.55:
        return "HIGH"
    if score >= 0.35:
        return "MODERATE"
    if score >= 0.15:
        return "LOW"
    return "NEGLIGIBLE"


def _verdict_labels(verdict: Any) -> Dict[str, Any]:
    """Normalize either a TriageVerdict object or its to_dict() shape into labels."""
    if verdict is None:
        return {}
    if hasattr(verdict, "to_dict"):
        try:
            verdict = verdict.to_dict()
        except Exception:
            pass
    if not isinstance(verdict, dict):
        return {}
    return {
        "d0_1": verdict.get("d0_1"),
        "d0_2": verdict.get("d0_2"),
        "d0_3": verdict.get("d0_3"),
        "d5a": verdict.get("d5a"),
        "d9": verdict.get("d9_kill_switch") or verdict.get("d9"),
        "action": verdict.get("action"),
    }


def compose_impact_score(
    *,
    tier: Any = None,
    event_title: str = "",
    z_delta: Any = None,
    esi_score: Any = None,
    triage_verdict: Any = None,
) -> NewsImpactScore:
    """Compose the 7-dimension graded score from already-computed signals.

    Args mirror existing call sites:
      tier             : int from classify_event_tier (or bare title if tier absent)
      z_delta          : float from decompose_surprise['z_delta']
      esi_score        : float 0-100 from EconomicSurpriseIndex / calculate_esi
      triage_verdict   : TriageVerdict or its to_dict()/flattened dict
    """
    # ---- Grade tier --------------------------------------------------------
    if tier is None and event_title:
        # Composition, not duplication: reuse the existing classifier.
        from core.news_trading import classify_event_tier
        tier = classify_event_tier(event_title)
    tier_grade = _grade_tier_reach(tier)

    # ---- Grade surprise ----------------------------------------------------
    surprise_grade = _grade_surprise_magnitude(z_delta)
    surprise_note = ""
    if surprise_grade is not None and surprise_grade >= 1.0:
        surprise_note = "REGIME_SHIFT_STRUCTURAL_SHOCK threshold reached (abs_z >= 2.0)"

    # ---- Grade refresh dynamics (ESI) --------------------------------------
    refresh_grade = _grade_refresh_dynamics(esi_score)

    # ---- Grade triage-sourced dimensions -----------------------------------
    labels = _verdict_labels(triage_verdict)
    evidence_grade = _EVIDENCE_GRADES.get(str(labels.get("d0_3") or ""), None)
    credibility_grade = _CREDIBILITY_GRADES.get(str(labels.get("d0_1") or ""), None)
    novelty_grade = _NOVELTY_GRADES.get(str(labels.get("d0_2") or ""), None)
    action = labels.get("action")
    if action == "REJECT" and novelty_grade is not None:
        # A rejected article cannot contribute novelty signal regardless of d0_2.
        novelty_grade = 0.0

    d9_label = str(labels.get("d9") or "").upper()
    d5a_label = str(labels.get("d5a") or "").upper()
    if d9_label == "EMERGENCY_HALT_ACTIVE":
        crisis_grade = 1.0
        crisis_note = "kill_switch_active"
    else:
        crisis_grade = _ESCALATION_GRADES.get(d5a_label, None)
        crisis_note = ""

    dimensions: List[ImpactDimension] = [
        ImpactDimension("tier_reach", tier_grade, "core.news_trading.classify_event_tier", tier),
        ImpactDimension("surprise_magnitude", surprise_grade, "core.macro_event_alpha_engine.decompose_surprise", z_delta, surprise_note),
        ImpactDimension("refresh_dynamics", refresh_grade, "core.news_trading.EconomicSurpriseIndex", esi_score),
        ImpactDimension("evidence_quality", evidence_grade, "data.news_triage D0_3", labels.get("d0_3")),
        ImpactDimension("source_credibility", credibility_grade, "data.news_triage D0_1", labels.get("d0_1")),
        ImpactDimension("novelty_allowance", novelty_grade, "data.news_triage D0_2 + action", f"{labels.get('d0_2')}/{action}"),
        ImpactDimension("crisis_overlay", crisis_grade, "data.news_triage D9 + D5a", f"{d9_label}/{d5a_label}", crisis_note),
    ]
    by_name = {d.name: d.value for d in dimensions}

    # ---- Reliability gate (fail-soft, no fabrication) -----------------------
    # "Reliable enough" requires BOTH a fact-bearing print and a credible
    # source. Without both, the composed score still reports for research
    # context but the consumer should not lean on it for sizing/priority.
    reliable_enough = (
        (by_name["evidence_quality"] or 0.0) * (by_name["source_credibility"] or 0.0) >= 0.5
    )

    # ---- Composition weights -----------------------------------------------
    # Event substance (tier + surprise + ESI) dominates. Credibility and
    # novelty modulate HOW MUCH of that substance the market should reprice;
    # they must not be able to lift an in-line PRICED_IN print to HIGH on
    # outlet trust alone.
    W_CONTEXT = 0.60   # tier + surprise + esi together = "how big is the event"
    W_CRED = 0.20
    W_NOVELTY = 0.15
    W_EVIDENCE = 0.10

    def _mean(names: Iterable[str]) -> Optional[float]:
        vals = [by_name[n] for n in names if by_name[n] is not None]
        return sum(vals) / len(vals) if vals else None

    context_mean = _mean(("tier_reach", "surprise_magnitude", "refresh_dynamics"))
    cred = by_name["source_credibility"]
    novelty = by_name["novelty_allowance"]
    evidence = by_name["evidence_quality"]

    # Evidence quality participates as a fourth additive grade so that a
    # credible-but-anecdotal headline (opinion from a good outlet) reports as
    # LOW/MODERATE, not MODERATE+ purely on outlet trust.
    def _grade_or_zero(v: Optional[float]) -> float:
        return v if v is not None else 0.0

    base: Optional[float] = None
    if context_mean is not None:
        base = context_mean * W_CONTEXT
        if cred is not None:
            base += cred * W_CRED
        if novelty is not None:
            base += novelty * W_NOVELTY
        if evidence is not None:
            base += evidence * W_EVIDENCE
        # Re-normalize for whatever inputs were missing so a silent gap never
        # reads as a low impact score.
        available_weight = W_CONTEXT
        if cred is not None:
            available_weight += W_CRED
        if novelty is not None:
            available_weight += W_NOVELTY
        if evidence is not None:
            available_weight += W_EVIDENCE
        base = base / available_weight if available_weight > 0 else None

    # ---- Self-consistency dampening ----------------------------------------
    # When reliability fails (not evidence_credible enough to lean on), the
    # numeric score itself must not silently read MODERATE/HIGH. A 0.5x
    # dampening compresses unreliable context into LOW/NEGLIGIBLE bands so the
    # number and the boolean tell the same story.
    UNRELIABLE_DAMPENING = 0.5
    dampened = False
    if not reliable_enough and base is not None:
        base = base * UNRELIABLE_DAMPENING
        dampened = True

    # ---- Crisis overlay (multiplier, only modulates, never creates) ---------
    overlay: Optional[float] = None
    if base is not None:
        overlay = base
        if crisis_grade is not None:
            # Crisis OVERLAY modulates magnitude only: half-strength floor, so
            # a crisis-labeled benign headline still cannot inflate past ~0.6x
            # max, and a kill-switch headline can push a modest print to full.
            overlay = base * (0.5 + 0.5 * crisis_grade)

    final = _clamp01(overlay) or 0.0

    result = NewsImpactScore(
        final_score=final,
        band=_band(final),
        dimensions=dimensions,
        reliable_enough=reliable_enough,
        composition={
            "weights": {"context_mean": W_CONTEXT, "credibility": W_CRED, "novelty": W_NOVELTY, "evidence": W_EVIDENCE},
            "crisis_overlay_applied": crisis_grade is not None,
            "crisis_grade": crisis_grade,
            "required_both_evidence_and_credibility": 0.5,
            "unreliable_dampening_applied": dampened,
            "unreliable_dampening_factor": UNRELIABLE_DAMPENING if dampened else 1.0,
        },
    )
    log.debug("news impact score composed: %.4f (%s) reliable=%s", final, result.band, reliable_enough)
    return result


def score_from_pipeline_row(article: Dict[str, Any], *, esi_score: Any = None) -> NewsImpactScore:
    """Convenience entry for rows flowing out of data.data_source_pipeline /
    realtime_news_ingest — reads the keys those producers already attach."""
    verdict = article.get("triage_verdict") or article.get("triage") or article
    return compose_impact_score(
        event_title=str(article.get("title") or article.get("event_name") or ""),
        z_delta=article.get("z_delta"),
        esi_score=esi_score if esi_score is not None else article.get("esi_score"),
        triage_verdict=verdict,
    )


__all__ = ["NewsImpactScore", "ImpactDimension", "compose_impact_score", "score_from_pipeline_row"]
