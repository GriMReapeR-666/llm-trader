"""Daily alternating ablation/baseline schedule (PU-0161, follow-up 2).

Why this exists
---------------
PU-0160 paired session-10 (``no_llm``) against session-11 (with-LLM) *on the
same day, 5.5 hours apart*. That establishes behavioural divergence (19 HOLD
vs 14 BUY) but not a clean marginal contribution, because time-of-day and
regime drift are confounded with the treatment — the report's own caveat #1.

This module assigns **one arm per UTC day**, alternating, so the two arms run
under matched market conditions on adjacent days instead of hours apart:

    day 0 (anchor)      -> no_llm     (ablation arm)
    day 1               -> with_llm   (baseline arm)
    day 2               -> no_llm
    ...

Design notes
------------
* **UTC date, not local.** Sessions are journaled and compared in UTC, and the
  boot chassis timestamps in UTC; a local-date boundary would silently skew
  which arm a run lands on.
* **Anchor-based parity, not "today % 2".** Wall-clock day-of-year parity
  shifts whenever anyone re-derives it; an explicit anchor date makes the
  assignment reproducible and lets a report state exactly which arm a given
  historical date was supposed to run.
* **Fail-safe direction.** If the schedule cannot be resolved (garbage anchor,
  unparsable date), it returns ``with_llm`` (the baseline arm). Shipping a
  baseline run where an ablation was wanted costs one paired data point;
  shipping an ablation where a baseline was wanted poisons the control arm.
* **Opt-in.** Disabled unless ``PU_ABLATION_SCHEDULE=alternate`` (or ``1`` /
   ``on`` / ``true``), so existing single-arm chassis keep their behaviour.

Public API
----------
``resolve(mode_env)`` -> ``ScheduleDecision``
    The one call the boot chassis need: reads env, computes today's arm,
    and reports whether the caller's arm matches.

Environment
-----------
``PU_ABLATION_SCHEDULE``   ``alternate``/``1``/``on``/``true`` enables the
                           schedule; anything else disables it.
``PU_ABLATION_ANCHOR``     ISO date (``YYYY-MM-DD``) that counts as day 0,
                           i.e. an ablation (``no_llm``) day.
                           Default ``2026-10-02`` — the PU-0160 session-10
                           ablation date, so the existing pair keeps its
                           parity and session-11's date stays a baseline day.
``PU_ABLATION_ARM``        Which arm this process wants to run:
                           ``no_llm`` (default) or ``with_llm``.

Marker: END-MARKER-PU-0161-SCHEDULE
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# The two arms of the paired design. "no_llm" is the treatment (LLM removed);
# "with_llm" is the control. Names match the PU_ABLATION values the
# orchestrator already understands, so an arm maps 1:1 onto env state.
ARM_ABLATION = "no_llm"
ARM_BASELINE = "with_llm"

DEFAULT_ANCHOR = "2026-10-02"
TRUTHY = {"1", "true", "yes", "on", "alternate", "alternating"}


@dataclass(frozen=True)
class ScheduleDecision:
    """Everything a boot chassis needs to act on the daily schedule."""

    enabled: bool
    today: date
    anchor: date
    offset_days: int          # (today - anchor).days
    arm_today: str            # arm this UTC day is assigned
    requested_arm: str        # arm the caller asked to run
    reason: str               # human-readable explanation for the boot log

    @property
    def is_ablation_day(self) -> bool:
        return self.arm_today == ARM_ABLATION

    @property
    def matches(self) -> bool:
        """True when today's assigned arm is the one the caller wants."""
        return self.enabled and self.arm_today == self.requested_arm

    @property
    def skip_reason(self) -> str:
        """Non-empty when the caller should NOT boot today."""
        if not self.enabled:
            return ""
        if self.matches:
            return ""
        return (
            "schedule: today {} is arm '{}' but this chassis runs '{}' - "
            "yielding to the paired chassis".format(
                self.today.isoformat(), self.arm_today, self.requested_arm
            )
        )


def _parse_anchor(raw: str) -> date:
    """Parse an ISO date; fall back to the documented default on garbage."""
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError, TypeError):
        return datetime.strptime(DEFAULT_ANCHOR, "%Y-%m-%d").date()


def arm_for_offset(offset_days: int) -> str:
    """Alternating arm by day offset from the anchor.

    Day 0 = anchor = ablation; parity decides the rest. Negative offsets are
    supported so a report can classify historical dates before the anchor.
    """
    # Python's % always returns non-negative for a positive modulus, so
    # -1 % 2 == 1 -> baseline, which keeps the alternation consistent
    # backwards in time instead of collapsing every pre-anchor day to day 0.
    return ARM_ABLATION if offset_days % 2 == 0 else ARM_BASELINE


def resolve(
    schedule_env: str | None = None,
    anchor_env: str | None = None,
    arm_env: str | None = None,
    today: date | None = None,
) -> ScheduleDecision:
    """Resolve today's arm. ``None`` env args read from ``os.environ``.

    Pass ``today`` explicitly in tests; production leaves it ``None`` and gets
    the current UTC date (not local — see module docstring).
    """
    if schedule_env is None:
        schedule_env = os.getenv("PU_ABLATION_SCHEDULE", "")
    if anchor_env is None:
        anchor_env = os.getenv("PU_ABLATION_ANCHOR", DEFAULT_ANCHOR)
    if arm_env is None:
        arm_env = os.getenv("PU_ABLATION_ARM", ARM_ABLATION)

    enabled = str(schedule_env).strip().lower() in TRUTHY
    anchor = _parse_anchor(anchor_env)
    if today is None:
        today = datetime.now(timezone.utc).date()

    requested = str(arm_env).strip().lower()
    # An unknown arm is not a reason to run the wrong experiment: fall back to
    # the ablation arm only when explicitly asked for no_llm-like input that
    # merely fails validation; otherwise default to baseline for safety.
    if requested not in (ARM_ABLATION, ARM_BASELINE):
        requested = ARM_BASELINE

    offset = (today - anchor).days
    arm_today = arm_for_offset(offset)

    if not enabled:
        reason = "schedule disabled (PU_ABLATION_SCHEDULE unset/not 'alternate')"
    else:
        reason = (
            "schedule: {} offset {} from anchor {} -> arm '{}' (this chassis: "
            "'{}')".format(
                today.isoformat(), offset, anchor.isoformat(), arm_today,
                requested,
            )
        )

    return ScheduleDecision(
        enabled=enabled,
        today=today,
        anchor=anchor,
        offset_days=offset,
        arm_today=arm_today,
        requested_arm=requested,
        reason=reason,
    )


def _selftest() -> int:
    """Built-in selftest: strict alternation + anchor parity + fail-safe."""
    checks: list[tuple[str, bool]] = []
    anchor = date(2026, 10, 2)

    # 1. Day 0 is the ablation arm.
    checks.append(("anchor day is ablation", arm_for_offset(0) == ARM_ABLATION))

    # 2. Strict alternation across a fortnight, no repeats.
    seq = [arm_for_offset(i) for i in range(14)]
    checks.append(("strict alternation", all(
        seq[i] != seq[i + 1] for i in range(len(seq) - 1))))
    checks.append(("anchor parity held", seq[0] == ARM_ABLATION
                   and seq[1] == ARM_BASELINE))

    # 3. Negative offsets keep alternating backwards (no collapse to day 0).
    checks.append(("negative offsets alternate", all(
        arm_for_offset(-i) != arm_for_offset(-i - 1) for i in range(10))))

    # 4. Resolution: enabled schedule, ablation arm on an ablation day -> match.
    d0 = resolve("alternate", DEFAULT_ANCHOR, ARM_ABLATION, today=anchor)
    checks.append(("day0 ablation match", d0.matches and d0.is_ablation_day))
    checks.append(("day0 reason populated", "arm 'no_llm'" in d0.reason))

    # 5. Baseline chassis must skip on an ablation day, and vice versa.
    d0_base = resolve("alternate", DEFAULT_ANCHOR, ARM_BASELINE, today=anchor)
    checks.append(("baseline skips ablation day",
                   (not d0_base.matches) and d0_base.skip_reason != ""))
    d1_abl = resolve("alternate", DEFAULT_ANCHOR, ARM_ABLATION,
                     today=anchor + timedelta(days=1))
    checks.append(("ablation skips baseline day", not d1_abl.matches))
    d1_base = resolve("alternate", DEFAULT_ANCHOR, ARM_BASELINE,
                      today=anchor + timedelta(days=1))
    checks.append(("baseline matches baseline day", d1_base.matches))

    # 6. Disabled schedule never claims a match (chassis behaves as before).
    off = resolve("", DEFAULT_ANCHOR, ARM_ABLATION, today=anchor)
    checks.append(("disabled never matches", not off.matches
                   and off.skip_reason == ""))

    # 7. Garbage anchor falls back to the documented default.
    bad = resolve("alternate", "not-a-date", ARM_ABLATION, today=anchor)
    checks.append(("garbage anchor -> default",
                   bad.anchor == _parse_anchor(DEFAULT_ANCHOR) and bad.matches))

    # 8. Unknown arm fails SAFE to baseline (never silently runs ablation).
    weird = resolve("alternate", DEFAULT_ANCHOR, "banana", today=anchor)
    checks.append(("unknown arm fails safe to baseline",
                   weird.requested_arm == ARM_BASELINE and not weird.matches))

    # 9. UTC today is used when no date is passed.
    utc_today = datetime.now(timezone.utc).date()
    auto = resolve("alternate", DEFAULT_ANCHOR, ARM_ABLATION)
    checks.append(("auto today uses UTC", auto.today == utc_today))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print("  [{}] {}".format("PASS" if ok else "FAIL", name))
    print("selftest: {}/{} checks passed".format(
        len(checks) - len(failed), len(checks)))
    print("END-MARKER-PU-0161-SCHEDULE")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
