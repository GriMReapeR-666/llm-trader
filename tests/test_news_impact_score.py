"""Tests for the graded news-impact score rubric (composition-only module)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Single import root: repo root only.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import core.news_impact_score as nis


def _verdict(
    d0_1: str = "TRUSTED_OFFICIAL",
    d0_2: str = "FIRST_REPORT",
    d0_3: str = "VERIFIED_FACT",
    d5a: str = "INSUFFICIENT_DATA",
    d9: str = "INSUFFICIENT_DATA",
    action: str = "INGEST",
) -> Dict[str, Any]:
    return {
        "d0_1": d0_1, "d0_2": d0_2, "d0_3": d0_3,
        "d5a": d5a, "d9_kill_switch": d9, "action": action,
    }


# ---------------------------------------------------------------------------
# Dimension graders
# ---------------------------------------------------------------------------

def test_tier_grades_match_tier_classification():
    assert nis._grade_tier_reach(1) == 1.0
    assert nis._grade_tier_reach(2) == 0.7
    assert nis._grade_tier_reach(3) == 0.4
    assert nis._grade_tier_reach(0) == 0.15  # unknown tier degrades, not zero
    assert nis._grade_tier_reach(None) is None


def test_surprise_grade_hits_full_at_regime_shift_threshold():
    # abs_z = 2.0 is exactly the REGIME_SHIFT_STRUCTURAL_SHOCK boundary
    assert nis._grade_surprise_magnitude(2.0) == 1.0
    assert nis._grade_surprise_magnitude(-2.0) == 1.0
    assert nis._grade_surprise_magnitude(0.0) == 0.0
    assert abs(nis._grade_surprise_magnitude(0.8) - 0.4) < 1e-9


def test_esi_neutral_50_is_zero_and_deviations_are_half_stretch():
    assert nis._grade_refresh_dynamics(50.0) == 0.0
    assert abs(nis._grade_refresh_dynamics(80.0) - 0.3) < 1e-9
    assert abs(nis._grade_refresh_dynamics(20.0) - 0.3) < 1e-9  # symmetric


def test_evidence_quality_ordering():
    assert nis._EVIDENCE_GRADES["VERIFIED_FACT"] > nis._EVIDENCE_GRADES["OFFICIAL_STATEMENT"]
    assert nis._EVIDENCE_GRADES["OFFICIAL_STATEMENT"] > nis._EVIDENCE_GRADES["ANALYST_OPINION"]
    assert nis._EVIDENCE_GRADES["ANALYST_OPINION"] > nis._EVIDENCE_GRADES["RUMOR_UNCONFIRMED"]


# ---------------------------------------------------------------------------
# Full composition
# ---------------------------------------------------------------------------

def test_full_composition_high_impact_beat():
    r = nis.compose_impact_score(
        tier=1,
        z_delta=2.5,
        esi_score=85.0,
        triage_verdict=_verdict(),
    )
    assert r.final_score > 0.75
    assert r.band == "CRITICAL"
    assert r.reliable_enough is True
    assert r.execution_authority is False
    names = {d.name for d in r.dimensions}
    assert names == set(nis._DIMENSIONS)


def test_full_composition_low_impact_in_line_print():
    r = nis.compose_impact_score(
        tier=3,
        z_delta=0.1,
        esi_score=52.0,
        triage_verdict=_verdict(d0_3="ANALYST_OPINION", d0_1="VERIFIED_OUTLET"),
    )
    # An in-line print from opinion-grade reporting must not reach MODERATE.
    assert r.band in ("NEGLIGIBLE", "LOW")
    assert r.reliable_enough is False
    # Unreliable context is numerically dampened, not merely flagged.
    assert r.composition["unreliable_dampening_applied"] is True


def test_reject_action_floors_novelty():
    r = nis.compose_impact_score(
        tier=1, z_delta=2.0, triage_verdict=_verdict(action="REJECT"),
    )
    novelty = next(d for d in r.dimensions if d.name == "novelty_allowance")
    assert novelty.value == 0.0


def test_stale_repeat_has_zero_novelty():
    r = nis.compose_impact_score(
        tier=1, z_delta=2.0, triage_verdict=_verdict(d0_2="STALE_REPEAT"),
    )
    novelty = next(d for d in r.dimensions if d.name == "novelty_allowance")
    assert novelty.value == 0.0


def test_kill_switch_crisis_drives_full_overlay():
    r = nis.compose_impact_score(
        tier=1, z_delta=1.0, triage_verdict=_verdict(d9="EMERGENCY_HALT_ACTIVE"),
    )
    crisis = next(d for d in r.dimensions if d.name == "crisis_overlay")
    assert crisis.value == 1.0
    # With overlay at full strength the composed score cannot be lower than
    # the same inputs without crisis (half-strength floor prevents as well as
    # boosts, but never inverts ordering).
    assert r.final_score > 0


def test_crisis_overlay_cannot_create_score_from_nowhere():
    # benign headline + no context inputs -> even a kill-switch triage alone
    # yields a bounded score, not a fabricated CRITICAL.
    r = nis.compose_impact_score(
        triage_verdict=_verdict(d9="EMERGENCY_HALT_ACTIVE"),
    )
    assert r.final_score <= 0.75 or r.band != "CRITICAL"


def test_missing_inputs_degrade_without_fabrication():
    r = nis.compose_impact_score(triage_verdict=None)
    # Every triage dimension degrades to None (unavailable), not 0.
    assert all(
        d.value is None
        for d in r.dimensions
        if d.name in ("evidence_quality", "source_credibility", "novelty_allowance", "crisis_overlay")
    )
    # tier was not given and no title -> None too
    tier_dim = next(d for d in r.dimensions if d.name == "tier_reach")
    assert tier_dim.value is None


def test_untrusted_source_caps_reliability_even_with_fact():
    r = nis.compose_impact_score(
        tier=1, z_delta=2.0, triage_verdict=_verdict(d0_1="UNVERIFIED_SOCIAL"),
    )
    assert r.reliable_enough is False


def test_reliability_requires_both_evidence_and_credibility():
    r = nis.compose_impact_score(
        tier=1, z_delta=2.0,
        triage_verdict=_verdict(d0_3="RUMOR_UNCONFIRMED", d0_1="TRUSTED_OFFICIAL"),
    )
    assert r.reliable_enough is False


# ---------------------------------------------------------------------------
# Provenance / auditability
# ---------------------------------------------------------------------------

def test_dimensions_carry_source_provenance():
    r = nis.compose_impact_score(
        tier=2, z_delta=1.2, esi_score=60.0, triage_verdict=_verdict(),
    )
    sources = {d.name: d.source for d in r.dimensions}
    assert "classify_event_tier" in sources["tier_reach"]
    assert "decompose_surprise" in sources["surprise_magnitude"]
    assert "EconomicSurpriseIndex" in sources["refresh_dynamics"]
    assert "D0_3" in sources["evidence_quality"]
    assert "D0_1" in sources["source_credibility"]


def test_output_dict_is_audit_serializable():
    r = nis.compose_impact_score(tier=1, z_delta=0.0, triage_verdict=_verdict())
    payload = r.to_dict()
    assert payload["execution_authority"] is False
    assert set(payload["dimensions"]) == set(nis._DIMENSIONS)


def test_triage_verdict_object_shape_accepted():
    """Accepts a TriageVerdict-like object with to_dict(), not just dicts."""
    class _FakeVerdict:
        def to_dict(self):
            return _verdict()
    r = nis.compose_impact_score(tier=1, z_delta=1.0, triage_verdict=_FakeVerdict())
    evidence = next(d for d in r.dimensions if d.name == "evidence_quality")
    assert evidence.value == 1.0


def test_legacy_d9_key_without_kill_suffix_supported():
    """d9 label under either 'd9' or 'd9_kill_switch' keys resolves."""
    v = _verdict(d9="EMERGENCY_HALT_ACTIVE")
    dropped = {k: v for k, v in v.items() if k != "d9_kill_switch"}
    dropped["d9"] = "EMERGENCY_HALT_ACTIVE"
    r = nis.compose_impact_score(tier=1, triage_verdict=dropped)
    crisis = next(d for d in r.dimensions if d.name == "crisis_overlay")
    assert crisis.value == 1.0


def test_nan_and_inf_inputs_fail_soft():
    r = nis.compose_impact_score(tier=1, z_delta=float("nan"), esi_score=float("inf"), triage_verdict=_verdict())
    assert next(d for d in r.dimensions if d.name == "surprise_magnitude").value is None
    assert next(d for d in r.dimensions if d.name == "refresh_dynamics").value is None
    assert 0.0 <= r.final_score <= 1.0
