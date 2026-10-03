"""Evidence gates for changing or retiring deterministic setup specifications."""
from __future__ import annotations

from typing import Any, Dict


MIN_RESEARCH_SAMPLE = 100


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def change_gate(*, closed_samples: int, out_of_sample_passed: bool,
                previous_version_preserved: bool, minimum_samples: int = 100) -> Dict[str, Any]:
    reasons = []
    if not isinstance(closed_samples, int) or isinstance(closed_samples, bool) or closed_samples < 0:
        reasons.append("closed_samples must be a non-negative integer")
        closed_samples = 0
    if not isinstance(out_of_sample_passed, bool):
        reasons.append("out_of_sample_passed must be boolean")
        out_of_sample_passed = False
    if not isinstance(previous_version_preserved, bool):
        reasons.append("previous_version_preserved must be boolean")
        previous_version_preserved = False
    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < MIN_RESEARCH_SAMPLE):
        reasons.append(f"minimum_samples policy floor is {MIN_RESEARCH_SAMPLE}")
        minimum_samples = MIN_RESEARCH_SAMPLE
    if closed_samples < minimum_samples:
        reasons.append(f"need {minimum_samples} closed samples, have {closed_samples}")
    if not out_of_sample_passed:
        reasons.append("out-of-sample validation not passed")
    if not previous_version_preserved:
        reasons.append("previous setup version/results are not preserved")
    return {"allowed": not reasons, "reasons": reasons, "execution_authority": False}


def retirement_gate(*, live_expectancy_r: float | None, oos_expectancy_r: float | None,
                    degradation_fraction: float | None, max_degradation: float = 0.30,
                    minimum_samples: int = 100, closed_samples: int = 0) -> Dict[str, Any]:
    reasons = []
    if not isinstance(closed_samples, int) or isinstance(closed_samples, bool) or closed_samples < 0:
        reasons.append("closed_samples must be a non-negative integer")
        closed_samples = 0
    degradation = _finite(degradation_fraction) if degradation_fraction is not None else None
    live_expectancy = _finite(live_expectancy_r) if live_expectancy_r is not None else None
    oos_expectancy = _finite(oos_expectancy_r) if oos_expectancy_r is not None else None
    max_degradation_value = _finite(max_degradation)
    if degradation_fraction is not None and degradation is None:
        reasons.append("degradation_fraction must be finite")
    if live_expectancy_r is not None and live_expectancy is None:
        reasons.append("live_expectancy_r must be finite")
    if oos_expectancy_r is not None and oos_expectancy is None:
        reasons.append("oos_expectancy_r must be finite")
    if max_degradation_value is None or not 0 <= max_degradation_value <= 1:
        reasons.append("max_degradation must be finite and in [0,1]")
        max_degradation_value = 0.30
    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < MIN_RESEARCH_SAMPLE):
        reasons.append(f"minimum_samples policy floor is {MIN_RESEARCH_SAMPLE}")
        minimum_samples = MIN_RESEARCH_SAMPLE
    if closed_samples < minimum_samples:
        reasons.append("insufficient evidence for automatic retirement")
    if degradation is not None and degradation > max_degradation_value:
        reasons.append(f"out-of-sample degradation {degradation:.1%} exceeds {max_degradation_value:.1%}")
    if live_expectancy is not None and live_expectancy < 0:
        reasons.append("live expectancy is negative")
    if oos_expectancy is not None and oos_expectancy < 0:
        reasons.append("out-of-sample expectancy is negative")
    return {"retire_or_freeze": bool(reasons), "reasons": reasons,
            "automatic_action": "FREEZE_AND_REQUIRE_OPERATOR_REVIEW" if reasons else "CONTINUE_MONITORING",
            "execution_authority": False}
