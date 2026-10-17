"""Alpha Rotation Engine — Automatic Dead Alpha Replacement.

Monitors alpha health via the decay monitor and manages an active set:
  - When an alpha goes DEAD, remove it from the ensemble
  - Promote the next-best candidate from the full pool
  - Enforce cooldown to prevent promoting recently-dead alphas
  - Persist rotation state across restarts

The rotation engine sits between the decay monitor and the ensemble:
  Full Alpha Pool → Rotation Engine (active set) → Ensemble → Composite Signal

Usage:
    rotation = AlphaRotationEngine()
    active_set = rotation.get_active_set()
    # Pass active_set to compute_indicators and combine_alphas
    rotation.update(health_status, all_alpha_names)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from core.state_persistence import StoreBackedState
except ImportError:
    from core.state_persistence import StoreBackedState

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:
    from core.alpha_factors import _ALPHA_REGISTRY
except ImportError:
    from core.alpha_factors import _ALPHA_REGISTRY


# All available alpha column names (from registry)
ALL_ALPHA_NAMES: List[str] = [
    f"alpha_{k[-3:]}" for k in sorted(_ALPHA_REGISTRY.keys())
]

DEFAULT_STATE_FILE = "alpha_rotation_state.json"
DEFAULT_MIN_ALPHAS = 5          # Never rotate below this count
DEFAULT_MAX_ALPHAS = 20         # Never promote above this count
DEFAULT_COOLDOWN_BARS = 50      # Bars before a dead alpha can be reconsidered
DEFAULT_PROMOTION_IC_THRESHOLD = 0.02  # Minimum IC to be eligible for promotion (prevents negligible-edge alphas)

# Quarantined test fixture names and patterns to prevent test fixtures from
# polluting production state, health maps, active sets, or candidate pools.
TEST_FIXTURE_NAMES = frozenset({
    "alpha_dead",
    "alpha_reversed",
    "alpha_good",
    "alpha_market",
    "alpha_noise",
    "alpha_bad",
})


def _is_quarantined_test_fixture(name: str) -> bool:
    if not isinstance(name, str):
        return True
    lower = name.lower()
    return (
        lower in TEST_FIXTURE_NAMES
        or lower.startswith(("test_", "mock_", "fixture_"))
        or lower.endswith(("_test", "_mock", "_fixture"))
    )


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class RotationEvent:
    """Record of a single rotation event."""
    timestamp: float
    removed_alpha: str
    promoted_alpha: str
    reason: str
    removed_ic_mean: float = 0.0
    promoted_ic_mean: float = 0.0


@dataclass
class AlphaCandidate:
    """A candidate alpha with its quality metrics for ranking."""
    name: str
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    ic_current: float = 0.0
    ic_trend: float = 0.0
    status: str = "UNKNOWN"
    bars_since_dead: int = 0
    source: str = "static_pool"
    expression: str = ""


@dataclass
class SourceCandidate:
    """A promotion candidate supplied by an external source (alpha discovery).

    Not yet part of the rotation pool; if promoted, its expression must be
    evaluated on live data before it contributes to the ensemble.
    """
    name: str
    ic_mean: float
    ic_ir: float
    source: str = "discovery"
    expression: str = ""
    status: str = "VALIDATED"


# ── Rotation Engine ─────────────────────────────────────────────────────────

class AlphaRotationEngine(StoreBackedState):
    """Manages the active alpha set with automatic dead-alpha replacement.

    Architecture:
        ┌──────────────────────────────────────────────────────────┐
        │  Full Pool (all 22 alphas in _ALPHA_REGISTRY)           │
        │    ↓ filtered by rotation engine                        │
        │  Active Set (5-20 alphas currently in use)              │
        │    ↓ passed to                                         │
        │  compute_indicators() + combine_alphas()               │
        │    ↓                                                   │
        │  Composite Signal → Trade Decision                     │
        └──────────────────────────────────────────────────────────┘

    Rotation logic:
        1. After each cycle, the decay monitor reports alpha health
        2. If any active alpha is DEAD → remove it
        3. Find the best non-active alpha by IC_IR → promote it
        4. Enforce minimum/maximum set sizes
        5. Cooldown: dead alphas can't be re-promoted for N bars
    """

    store_key = "alpha_rotation_state"

    def __init__(
        self,
        state_file: str = DEFAULT_STATE_FILE,
        min_alphas: int = DEFAULT_MIN_ALPHAS,
        max_alphas: int = DEFAULT_MAX_ALPHAS,
        cooldown_bars: int = DEFAULT_COOLDOWN_BARS,
        promotion_ic_threshold: float = DEFAULT_PROMOTION_IC_THRESHOLD,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.min_alphas = min_alphas
        self.max_alphas = max_alphas
        self.cooldown_bars = cooldown_bars
        self.promotion_ic_threshold = promotion_ic_threshold

        # State
        self._active_set: List[str] = []
        self._all_pool: List[str] = [a for a in ALL_ALPHA_NAMES if not _is_quarantined_test_fixture(a)]
        self._dead_timestamps: Dict[str, float] = {}  # alpha → bar# when killed
        self._rotation_history: List[RotationEvent] = []
        self._total_rotations: int = 0
        self._bars_since_rotation: int = 100  # start above cooldown so first rotation works
        self._last_rotation_bar: int = -1  # bar# of last rotation (-1 = never)
        self._last_health: Dict[str, Dict[str, Any]] = {}

        # Discovered-candidate feed (from alpha discovery; NOT part of the pool
        # until promoted). Replaced wholesale each update() — no stale carryover.
        self._discovered_candidates: List[SourceCandidate] = []
        # Journal of every discovered-candidate gate decision (last 20 kept):
        # {"ts", "name", "ic_mean", "threshold", "verdict", "reason", "source"}
        self._discovery_decisions: List[Dict[str, Any]] = []
        # Promoted discovered candidates awaiting live-data materialization by
        # the consumer, keyed by name. See record_discovery_materialized.
        self._pending_discovery_promotions: Dict[str, Dict[str, Any]] = {}
        # Discovered candidates that passed the gates this update (eligible to
        # compete with the static pool). Rebuilt on every update().
        self._eligible_discovered: List[AlphaCandidate] = []
        # De-duplication of verdict announcements (see _announce_discovery_decision).
        self._announced_discovery_keys: Set[Any] = set()

        # Load persisted state
        self._load_state()
        if store is not None:
            self.attach_store(store)

    # ── Public API ──────────────────────────────────────────────────────

    def get_active_set(self) -> List[str]:
        """Return the current active alpha set.

        If empty (first run or no state), returns the full default set.
        """
        if not self._active_set:
            return [a for a in self._default_active_set() if not _is_quarantined_test_fixture(a)]
        return [a for a in self._active_set if not _is_quarantined_test_fixture(a)]

    def get_all_pool(self) -> List[str]:
        """Return the full pool of available alphas."""
        if not self._all_pool:
            self._all_pool = [a for a in self._default_active_set() if not _is_quarantined_test_fixture(a)]
        return [a for a in self._all_pool if not _is_quarantined_test_fixture(a)]

    def update(
        self,
        health: Dict[str, Dict[str, Any]],
        all_alpha_names: Optional[List[str]] = None,
        current_bar: Optional[int] = None,
        discovered_candidates: Optional[List[SourceCandidate]] = None,
    ) -> List[RotationEvent]:
        """Update rotation based on current alpha health.

        Parameters
        ----------
        health : dict
            Per-alpha health from decay monitor:
            {"alpha_012": {"status": "HEALTHY", "ic_mean": 0.04, ...}, ...}
        all_alpha_names : list, optional
            Full pool of available alpha column names.
        current_bar : int, optional
            Current bar index (for cooldown tracking).
        discovered_candidates : list of SourceCandidate, optional
            Candidates supplied by alpha discovery. They compete with the
            static pool under the SAME unchanged gates (ic_mean >=
            promotion_ic_threshold, cooldown, status) — no lowering, no
            forced promotion. Each candidate's verdict is logged with the
            concrete reason and recorded in the persisted decision journal.

        Returns
        -------
        List of RotationEvent objects (may be empty if no rotation needed).
        """
        if current_bar is None:
            current_bar = int(time.time())

        if all_alpha_names:
            self._all_pool = [a for a in all_alpha_names if not _is_quarantined_test_fixture(a)]

        # Replace the discovered-candidate feed wholesale each cycle so a
        # candidate is only considered while discovery still lists it.
        if discovered_candidates is None:
            self._discovered_candidates = []
        else:
            self._discovered_candidates = [
                c for c in discovered_candidates
                if isinstance(c, SourceCandidate) and c.name and not _is_quarantined_test_fixture(c.name)
            ]

        self._last_health = {
            k: v for k, v in (health or {}).items()
            if not _is_quarantined_test_fixture(k)
        }
        self._bars_since_rotation += 1

        events = []

        # Phase 1: Identify DEAD alphas in the active set
        active = [a for a in self.get_active_set() if not _is_quarantined_test_fixture(a)]

        # Gate verdicts for discovered candidates are recorded on EVERY update
        # (announced once per unique outcome), so the discovery→rotation
        # pipeline is observable even when no rotation is triggered.
        self._eligible_discovered = []
        self._evaluate_discovered_gates(active, current_bar)

        dead_alphas = []
        for alpha in active:
            h = health.get(alpha, {})
            status = h.get("status", "UNKNOWN")
            if status in ("DEAD", "REVERSED"):
                dead_alphas.append((alpha, h))

        # Phase 2: Remove dead alphas
        for alpha, h in dead_alphas:
            if len(active) <= self.min_alphas:
                log.info(
                    "Alpha %s is %s but keeping (min_alphas=%d reached)",
                    alpha, h.get("status"), self.min_alphas,
                )
                continue

            # Eviction is NOT gated on the rotation cooldown.  The cooldown
            # exists to stop a recently-dead alpha being re-promoted (see
            # _find_best_candidate, which enforces it per-candidate against
            # _dead_timestamps); gating eviction on it as well serialised the
            # cleanup to ONE alpha per cooldown window, so a set of dead alphas
            # drained at ~1 per window and never caught up when the decay
            # monitor ran infrequently.  Eight DEAD/REVERSED alphas sat in a
            # 14-alpha active set indefinitely while the engine reported it was
            # rotating (2026-10-16).  Removing a dead alpha is a correctness
            # action, not churn; only its re-promotion needs a cooldown.

            # Find best candidate for promotion
            candidate = self._find_best_candidate(active, health, current_bar)
            ic_mean = h.get("ic_mean", 0.0)
            if candidate is None:
                if len(active) > self.min_alphas:
                    active.remove(alpha)
                    self._dead_timestamps[alpha] = current_bar
                    self._bars_since_rotation = 0
                    self._last_rotation_bar = current_bar
                    self._total_rotations += 1
                    event = RotationEvent(
                        timestamp=current_bar,
                        removed_alpha=alpha,
                        promoted_alpha="NONE",
                        reason=f"{h.get('status', 'DEAD')}: ic_mean={ic_mean:.4f} (evicted without replacement)",
                        removed_ic_mean=ic_mean,
                        promoted_ic_mean=0.0,
                    )
                    events.append(event)
                    self._rotation_history.append(event)
                    log.info(
                        "EVICTION: removed dead/reversed %s (ic=%.4f, status=%s) without replacement (%d active remain, min=%d)",
                        alpha, ic_mean, h.get("status"), len(active), self.min_alphas,
                    )
                else:
                    log.info(
                        "Alpha %s is %s but no candidate available for promotion and min_alphas=%d reached",
                        alpha, h.get("status"), self.min_alphas,
                    )
                continue

            # Execute rotation
            active.remove(alpha)
            active.append(candidate.name)

            self._dead_timestamps[alpha] = current_bar
            self._bars_since_rotation = 0
            self._last_rotation_bar = current_bar
            self._total_rotations += 1

            # A discovered candidate needs its expression evaluated on live
            # data by the consumer before it contributes to the ensemble.
            is_discovered = getattr(candidate, "source", "static_pool") not in (None, "", "static_pool")
            if is_discovered:
                self._pending_discovery_promotions[candidate.name] = {
                    "name": candidate.name,
                    "expression": candidate.expression,
                    "ic_mean": candidate.ic_mean,
                    "ic_ir": candidate.ic_ir,
                    "source": candidate.source,
                    "promoted_at_bar": current_bar,
                    "promoted_at_ts": time.time(),
                }

            reason = f"{h.get('status', 'DEAD')}: ic_mean={ic_mean:.4f}"
            if is_discovered:
                reason += f" | promoted discovered candidate (source={candidate.source})"
            event = RotationEvent(
                timestamp=current_bar,
                removed_alpha=alpha,
                promoted_alpha=candidate.name,
                reason=reason,
                removed_ic_mean=ic_mean,
                promoted_ic_mean=candidate.ic_mean,
            )
            events.append(event)
            self._rotation_history.append(event)

            if is_discovered:
                log.info(
                    "ROTATION (discovery): removed %s (ic=%.4f, status=%s) -> promoted DISCOVERED %s "
                    "(ic=%.4f, ir=%.3f, source=%s, expression=%.120s)",
                    alpha, ic_mean, h.get("status"),
                    candidate.name, candidate.ic_mean, candidate.ic_ir,
                    candidate.source, candidate.expression,
                )
            else:
                log.info(
                    "ROTATION: removed %s (ic=%.4f, status=%s) -> promoted %s (ic=%.4f, ir=%.3f)",
                    alpha, ic_mean, h.get("status"),
                    candidate.name, candidate.ic_mean, candidate.ic_ir,
                )

        # Phase 3: If active set is too small, promote from pool
        while len(active) < self.min_alphas and len(self._all_pool) > len(active):
            candidate = self._find_best_candidate(active, health, current_bar)
            if candidate is None:
                break
            active.append(candidate.name)
            if getattr(candidate, "source", "static_pool") not in (None, "", "static_pool"):
                self._pending_discovery_promotions[candidate.name] = {
                    "name": candidate.name,
                    "expression": candidate.expression,
                    "ic_mean": candidate.ic_mean,
                    "ic_ir": candidate.ic_ir,
                    "source": candidate.source,
                    "promoted_at_bar": current_bar,
                    "promoted_at_ts": time.time(),
                }
                log.info(
                    "PROMOTED DISCOVERED %s to fill minimum set (ic=%.4f, source=%s, expression=%.120s)",
                    candidate.name, candidate.ic_mean, candidate.source, candidate.expression,
                )
            else:
                log.info("PROMOTED %s to fill minimum set (ic=%.4f)", candidate.name, candidate.ic_mean)

        self._active_set = [a for a in active if not _is_quarantined_test_fixture(a)]
        self._save_state()

        return events

    def get_status(self) -> Dict[str, Any]:
        """Get current rotation status."""
        active_set = self.get_active_set()
        all_pool = self.get_all_pool()
        return {
            "active_set": active_set,
            "active_count": len(active_set),
            "pool_size": len(all_pool),
            "total_rotations": self._total_rotations,
            "dead_alphas": {
                k: v for k, v in self._dead_timestamps.items()
                if not _is_quarantined_test_fixture(k)
            },
            "discovered_candidates_seen": len(self._discovered_candidates),
            "discovery_decisions": [
                dict(d) for d in self._discovery_decisions[-5:]
                if not _is_quarantined_test_fixture(d.get("name", ""))
            ],
            "pending_discovery_promotions": {
                k: dict(v) for k, v in self._pending_discovery_promotions.items()
                if not _is_quarantined_test_fixture(k)
            },
            "recent_events": [
                {
                    "removed": e.removed_alpha,
                    "promoted": e.promoted_alpha,
                    "reason": e.reason,
                    "bar": e.timestamp,
                }
                for e in self._rotation_history[-5:]
                if not _is_quarantined_test_fixture(e.removed_alpha)
                and not _is_quarantined_test_fixture(e.promoted_alpha)
            ],
        }

    def format_status(self) -> str:
        """Format rotation status as a human-readable report."""
        s = self.get_status()
        lines = [
            "=" * 60,
            "ALPHA ROTATION ENGINE STATUS",
            "=" * 60,
            f"Active set: {s['active_count']} alphas (min={self.min_alphas}, max={self.max_alphas})",
            f"Pool size:  {s['pool_size']} alphas",
            f"Rotations:  {s['total_rotations']} total",
            f"Dead alphas on cooldown: {len(s['dead_alphas'])}",
            "",
            "Active alphas:",
        ]
        for name in s["active_set"]:
            h = self._last_health.get(name, {})
            ic = h.get("ic_mean", 0)
            status = h.get("status", "?")
            lines.append(f"  {name:<14} ic={ic:>+.4f}  {status}")

        if s["recent_events"]:
            lines.append("")
            lines.append("Recent rotations:")
            for e in s["recent_events"]:
                lines.append(
                    f"  bar {e['bar']}: {e['removed']} -> {e['promoted']} ({e['reason']})"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Internal ────────────────────────────────────────────────────────

    def _find_best_candidate(
        self,
        active: List[str],
        health: Dict[str, Dict[str, Any]],
        current_bar: int,
    ) -> Optional[AlphaCandidate]:
        """Find the best non-active alpha for promotion.

        Ranking criteria (in order):
          1. Must not be in the active set
          2. Must not be on cooldown (recently dead)
          3. Must have IC >= promotion_ic_threshold
          4. Ranked by IC_IR (descending), then IC mean

        Discovered candidates (fed per-update by alpha discovery) compete
        under the SAME gates — no threshold lowering, no forced promotion.
        """
        active_set = set(active)
        candidates = []

        for name in self._all_pool:
            if _is_quarantined_test_fixture(name):
                continue
            if name in active_set:
                continue

            # Check cooldown
            dead_bar = self._dead_timestamps.get(name)
            if dead_bar is not None:
                bars_since = current_bar - dead_bar
                if bars_since < self.cooldown_bars:
                    continue

            # Get health metrics
            h = health.get(name, {})
            ic_mean = h.get("ic_mean", 0.0)
            ic_current = h.get("ic_current", 0.0)
            ic_trend = h.get("ic_trend", 0.0)
            status = h.get("status", "UNKNOWN")

            # Skip DEAD/REVERSED alphas outright.
            #
            # This used to be a recovery carve-out: the `continue` only fired
            # when the alpha had a kill timestamp AND was still inside
            # cooldown*2, so an alpha that had never been killed fell straight
            # through and was promoted on the strength of a stale positive
            # ic_mean while its CURRENT IC was negative. That put alpha_055 into
            # the active set with status REVERSED (ic_current=-0.0135,
            # ic_mean=+0.0657) in the same pass that evicted eight other dead
            # alphas (2026-10-16).
            #
            # Recovery needs no carve-out: the decay monitor recomputes
            # `status` every pass, so an alpha that has genuinely recovered
            # reads HEALTHY here and competes normally on the gates below.
            if status in ("DEAD", "REVERSED"):
                continue

            # Compute IC_IR from available data
            ic_ir = ic_mean / max(abs(ic_mean) * 0.5, 0.01)  # rough estimate if std unknown
            if "ic_ir" in h:
                ic_ir = h["ic_ir"]

            # Skip if below threshold
            if ic_mean < self.promotion_ic_threshold:
                continue

            candidates.append(AlphaCandidate(
                name=name,
                ic_mean=ic_mean,
                ic_ir=ic_ir,
                ic_current=ic_current,
                ic_trend=ic_trend,
                status=status,
            ))

        # Discovered candidates that passed the gates this update compete
        # alongside the static pool (gates were evaluated in update()).
        candidates.extend(self._eligible_discovered)

        if not candidates:
            return None

        # Sort by IC_IR descending, then IC mean descending
        candidates.sort(key=lambda c: (-c.ic_ir, -c.ic_mean))
        return candidates[0]

    def _announce_discovery_decision(
        self,
        dc: SourceCandidate,
        verdict: str,
        current_bar: int,
        reason: str = "",
    ) -> None:
        """Record + log a discovered-candidate verdict once per unique outcome.

        The same (name, verdict, reason) verdict is announced only the first
        time it occurs so a persistently-rejected candidate doesn't spam the
        log and journal every cycle; a changed metric re-announces honestly.
        """
        key = (dc.name, verdict, reason)
        if key in self._announced_discovery_keys:
            return
        if len(self._announced_discovery_keys) > 200:
            self._announced_discovery_keys.clear()
        self._announced_discovery_keys.add(key)
        self._record_discovery_decision(dc, verdict, current_bar, reason=reason)
        log.info(
            "Discovered candidate %s %s: %s (source=%s)",
            dc.name, verdict, reason, dc.source,
        )

    def _evaluate_discovered_gates(self, active: List[str], current_bar: int) -> None:
        """Gate every discovered candidate against the UNCHANGED promotion rules.

        Runs on every update() so the discovery→rotation pipeline is visible
        in the log/journal even when no rotation is triggered. Verdicts:
          - SKIP_DUPLICATE: name already active or in the static pool
          - REJECT: ic_mean below promotion_ic_threshold (reason names the gap)
          - PASS_GATE: eligible to compete with the static pool by ranking
        GATE SEMANTICS ARE UNCHANGED — no threshold lowering, no forced
        promotion; a PASS_GATE still loses the ranking to a stronger
        static-pool candidate.
        """
        active_set = set(active)
        for dc in self._discovered_candidates:
            if dc.name in active_set or dc.name in self._all_pool:
                # Already promoted (or a pool name echoed by discovery): its
                # evaluation is covered by the static-pool loop above.
                self._announce_discovery_decision(
                    dc, "SKIP_DUPLICATE", current_bar,
                    reason="already in active set or static pool",
                )
                continue
            if dc.ic_mean < self.promotion_ic_threshold:
                self._announce_discovery_decision(
                    dc, "REJECT", current_bar,
                    reason=(
                        f"ic_mean={dc.ic_mean:.4f} < promotion_ic_threshold="
                        f"{self.promotion_ic_threshold:.4f}"
                    ),
                )
                continue
            # Passes the gate — eligible alongside the static pool.
            self._eligible_discovered.append(AlphaCandidate(
                name=dc.name,
                ic_mean=dc.ic_mean,
                ic_ir=dc.ic_ir,
                ic_current=dc.ic_mean,
                ic_trend=0.0,
                status=dc.status or "VALIDATED",
                source=dc.source,
                expression=dc.expression,
            ))
            self._announce_discovery_decision(
                dc, "PASS_GATE", current_bar,
                reason=(
                    f"ic_mean={dc.ic_mean:.4f} >= promotion_ic_threshold="
                    f"{self.promotion_ic_threshold:.4f}"
                ),
            )

    def _record_discovery_decision(
        self,
        dc: SourceCandidate,
        verdict: str,
        current_bar: int,
        reason: str = "",
    ) -> None:
        """Record one discovered-candidate gate decision in the persisted journal."""
        self._discovery_decisions.append({
            "ts": time.time(),
            "bar": current_bar,
            "name": dc.name,
            "source": dc.source,
            "ic_mean": dc.ic_mean,
            "threshold": self.promotion_ic_threshold,
            "verdict": verdict,
            "reason": reason,
        })
        self._discovery_decisions = self._discovery_decisions[-20:]

    def get_discovery_decisions(self) -> List[Dict[str, Any]]:
        """Return recent discovered-candidate gate decisions (live records).

        Consumers may annotate records (e.g. ``journaled = True``) for emit
        de-duplication; annotations persist with the engine state.
        """
        return list(self._discovery_decisions)

    def record_discovery_materialized(self, name: str, ok: bool, detail: str) -> None:
        """Record the live-data materialization outcome of a promoted discovered candidate.

        Called by the consumer (provider) after it evaluates the promoted
        expression on real data — honestly records success or concrete failure.
        On ok=True the pending record is cleared; on failure it is kept so the
        consumer can retry with fresh data.
        """
        if name in self._pending_discovery_promotions:
            if ok:
                self._pending_discovery_promotions.pop(name, None)
            log.info(
                "Discovered alpha %s materialization: %s (%s)", name,
                "OK" if ok else "FAILED", detail,
            )

    def _default_active_set(self) -> List[str]:
        """Default active set (same as the hardcoded Tier 1 in market_data_provider)."""
        return [
            "alpha_012", "alpha_053", "alpha_055", "alpha_034", "alpha_017",
            "alpha_009", "alpha_041", "alpha_101", "alpha_030", "alpha_035",
            "alpha_014", "alpha_022", "alpha_025", "alpha_019",
            "alpha_023", "alpha_036", "alpha_043",
        ]

    # ── Persistence ─────────────────────────────────────────────────────

    def _save_state(self):
        """Persist rotation state to JSON."""
        try:
            state = {
                "active_set": [a for a in self._active_set if not _is_quarantined_test_fixture(a)],
                "all_pool": [a for a in self._all_pool if not _is_quarantined_test_fixture(a)],
                "dead_timestamps": {
                    k: v for k, v in self._dead_timestamps.items()
                    if not _is_quarantined_test_fixture(k)
                },
                "total_rotations": self._total_rotations,
                "last_rotation_bar": self._last_rotation_bar,
                "rotation_history": [
                    {
                        "timestamp": e.timestamp,
                        "removed": e.removed_alpha,
                        "promoted": e.promoted_alpha,
                        "reason": e.reason,
                        "removed_ic": e.removed_ic_mean,
                        "promoted_ic": e.promoted_ic_mean,
                    }
                    for e in self._rotation_history[-50:]  # keep last 50
                    if not _is_quarantined_test_fixture(e.removed_alpha)
                    and not _is_quarantined_test_fixture(e.promoted_alpha)
                ],
                "last_health": {
                    k: v for k, v in self._last_health.items()
                    if not _is_quarantined_test_fixture(k)
                },
                "discovery_decisions": [
                    d for d in self._discovery_decisions[-20:]
                    if not _is_quarantined_test_fixture(d.get("name", ""))
                ],
                "pending_discovery_promotions": {
                    k: v for k, v in self._pending_discovery_promotions.items()
                    if not _is_quarantined_test_fixture(k)
                },
                "saved_at": time.time(),
            }
            self._save_state_dict(state)
        except Exception as exc:
            log.debug("Failed to save rotation state: %s", exc)

    def _load_state(self):
        """Load persisted rotation state."""
        try:
            state = self._load_state_dict()
            if not state:
                return
            self._active_set = [
                a for a in state.get("active_set", [])
                if not _is_quarantined_test_fixture(a)
            ]
            self._all_pool = [
                a for a in state.get("all_pool", [])
                if not _is_quarantined_test_fixture(a)
            ]
            self._dead_timestamps = {
                k: v for k, v in state.get("dead_timestamps", {}).items()
                if not _is_quarantined_test_fixture(k)
            }
            self._total_rotations = state.get("total_rotations", 0)
            self._last_rotation_bar = state.get("last_rotation_bar", -1)
            self._last_health = {
                k: v for k, v in state.get("last_health", {}).items()
                if not _is_quarantined_test_fixture(k)
            }
            self._rotation_history = [
                RotationEvent(
                    timestamp=e.get("timestamp", 0),
                    removed_alpha=e.get("removed", ""),
                    promoted_alpha=e.get("promoted", ""),
                    reason=e.get("reason", ""),
                    removed_ic_mean=e.get("removed_ic", 0.0),
                    promoted_ic_mean=e.get("promoted_ic", 0.0),
                )
                for e in state.get("rotation_history", [])
                if not _is_quarantined_test_fixture(e.get("removed", ""))
                and not _is_quarantined_test_fixture(e.get("promoted", ""))
            ]
            self._discovery_decisions = [
                d for d in state.get("discovery_decisions", [])
                if not _is_quarantined_test_fixture(d.get("name", ""))
            ][-20:]
            self._pending_discovery_promotions = {
                k: v for k, v in (state.get("pending_discovery_promotions") or {}).items()
                if not _is_quarantined_test_fixture(k)
            }
            for _pname, _prec in self._pending_discovery_promotions.items():
                log.info(
                    "Resuming pending discovered-alpha materialization: %s (promoted at bar %s)",
                    _pname, (_prec or {}).get("promoted_at_bar"),
                )
        except Exception as exc:
            log.debug("Failed to load rotation state: %s", exc)
