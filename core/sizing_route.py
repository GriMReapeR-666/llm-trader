"""Which sizing route the system uses — decided by evidence, not by opinion.

The contract (`docs/PROJECT_SUCCESS_CONTRACT.md`) already states the
precondition for trusting the demo track record: 200+ broker-reconciled
fills on a frozen symbol, an honest evidence spine, and a granted rollout
approval. This module turns that sentence into a machine check, so the
switch to the forecast-driven route happens by itself the day the evidence
justifies it — and not one day earlier.

Autonomous, fail-closed, and side-effect free in the hot path:

* `current_route()` reads a small evidence snapshot and decides. It never
  blocks trading, never raises, and returns the legacy route for any
  unknown, missing, stale or unproven state.
* `refresh_route_snapshot()` is the expensive half: it gathers the evidence
  and writes the snapshot. It is an operator/scheduled action, not something
  a trade may do.
* A stale snapshot cannot authorise the switch. The snapshot carries the
  time it was taken and the evidence it was taken from; the age and the
  hash are both checked.

The new route may only ever *reduce* the risk amount, so enabling it can
never raise risk above the rule that is in production today.

References:
    Carver, R. (2015). "Systematic Trading." Harriman House, Ch. 9-11.
    docs/PROJECT_SUCCESS_CONTRACT.md (Phase 2 exit criteria).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

LEGACY = "legacy"
CARVER = "carver"

SNAPSHOT_PATH = os.path.join("artifacts", "sizing_route.json")

DEFAULT_MIN_RECONCILED_FILLS = 200
DEFAULT_MIN_ADMISSIBLE_ROWS = 200
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 3600.0

PAPER_PROVENANCE = "paper_broker"
"""The provenance token the evidence collector writes (contract element 10)."""

_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]Users[\\/]")

_TEXT_SUFFIXES = (".json", ".txt", ".md", ".csv", ".log")
_MAX_SCANNED_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class RouteDecision:
    """The route in force, and why."""

    route: str = LEGACY
    reasons: Tuple[str, ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)
    decided_at: float = 0.0
    snapshot_age_seconds: Optional[float] = None

    @property
    def is_carver(self) -> bool:
        return self.route == CARVER

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "reasons": list(self.reasons),
            "evidence": self.evidence,
            "decided_at": self.decided_at,
            "snapshot_age_seconds": self.snapshot_age_seconds,
        }


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def select_route(
    snapshot: Optional[Dict[str, Any]],
    *,
    now: Optional[float] = None,
    max_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    min_reconciled_fills: int = DEFAULT_MIN_RECONCILED_FILLS,
    min_admissible_rows: int = DEFAULT_MIN_ADMISSIBLE_ROWS,
) -> RouteDecision:
    """Decide the route from an evidence snapshot. Pure and fail-closed.

    The snapshot is the contract's Phase 2 exit criteria, measured:
    broker-reconciled demo fills, an honest evidence spine, and a granted
    rollout approval. Anything missing or stale keeps the legacy route.
    """
    now = time.time() if now is None else float(now)
    if not isinstance(snapshot, dict) or not snapshot:
        return RouteDecision(
            route=LEGACY,
            reasons=("no evidence snapshot: the sizing route stays as it is",),
            decided_at=now,
        )

    reasons = []
    taken_at = snapshot.get("taken_at")
    age: Optional[float] = None
    if not isinstance(taken_at, (int, float)):
        reasons.append("evidence snapshot has no timestamp")
    else:
        age = max(0.0, now - float(taken_at))
        if age > float(max_age_seconds):
            reasons.append(f"evidence snapshot is {int(age)}s old (limit {int(max_age_seconds)}s)")

    track = snapshot.get("track_record")
    rollout = snapshot.get("rollout")
    if not isinstance(track, dict):
        track = {}
        reasons.append("no demo track record in the snapshot")
    if not isinstance(rollout, dict):
        rollout = {}
        reasons.append("no rollout approval in the snapshot")

    reconciled = int(track.get("broker_reconciled_fills", 0) or 0)
    admissible = int(track.get("admissible_rows", 0) or 0)
    if not track.get("ready", False):
        reasons.append("demo track record is not ready")
    if reconciled < int(min_reconciled_fills):
        reasons.append(f"only {reconciled} broker-reconciled fills (need {min_reconciled_fills})")
    if admissible < int(min_admissible_rows):
        reasons.append(f"only {admissible} admissible rows (need {min_admissible_rows})")
    if not track.get("execution_authority", False):
        reasons.append("the journal grants no execution authority")
    if not rollout.get("active", False):
        reasons.append("no live rollout approval in force")
    if not rollout.get("evidence_valid", False):
        reasons.append("the rollout approval's evidence is missing or has drifted")

    null_rows = int(snapshot.get("null_provenance_rows", 0) or 0)
    if null_rows > 0:
        reasons.append(f"{null_rows} journal rows have no provenance")
    absolute = int(snapshot.get("absolute_path_artifacts", 0) or 0)
    if absolute > 0:
        reasons.append(f"{absolute} artifacts contain an absolute path")

    evidence = {
        "broker_reconciled_fills": reconciled,
        "admissible_rows": admissible,
        "execution_authority": bool(track.get("execution_authority", False)),
        "rollout_active": bool(rollout.get("active", False)),
        "rollout_evidence_valid": bool(rollout.get("evidence_valid", False)),
        "null_provenance_rows": null_rows,
        "absolute_path_artifacts": absolute,
    }
    return RouteDecision(
        route=CARVER if not reasons else LEGACY,
        reasons=tuple(reasons),
        evidence=evidence,
        decided_at=now,
        snapshot_age_seconds=age,
    )


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _read_snapshot(path: str = SNAPSHOT_PATH) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _null_provenance_rows(journal_path: str) -> int:
    """Rows in the authoritative journal that carry no provenance token."""
    if not os.path.exists(journal_path):
        return 0
    try:
        with sqlite3.connect(journal_path, timeout=5.0) as con:
            row = con.execute(
                "SELECT COUNT(*) FROM trades WHERE provenance IS NULL OR TRAGIM(provenance) = ''"
            ).fetchone()
    except sqlite3.Error:
        return 1  # unknown -> fail closed
    return int(row[0]) if row else 1


def _read_only_track_record(
    journal_path: str,
    target_trades: int = DEFAULT_MIN_ADMISSIBLE_ROWS,
) -> Dict[str, Any]:
    """The track record, measured without writing to the journal.

    The evidence collector's own report enriches rows it wrote — a write.
    A scheduled measurement must not do that, so the same numbers are
    derived here from read-only queries.
    """
    from research.research_validation import broker_reconciled_tickets, labelled_dataset

    brokered = broker_reconciled_tickets(journal_path) or set()
    dataset = labelled_dataset(journal_path) or {}
    rows = dataset.get("rows") or []
    admissible = [r for r in rows if str(r.get("provenance") or "") == PAPER_PROVENANCE]

    closed: list = []
    if os.path.exists(journal_path):
        try:
            with sqlite3.connect(journal_path, timeout=5.0) as con:
                closed = [int(r[0]) for r in con.execute(
                    "SELECT ticket FROM trades WHERE closed_at IS NOT NULL "
                    "AND pnl IS NOT NULL AND provenance=?", (PAPER_PROVENANCE,)
                ).fetchall()]
        except sqlite3.Error:
            closed = []
    reconciled = [ticket for ticket in closed if ticket in brokered]
    return {
        "closed_fills": len(closed),
        "broker_reconciled_fills": len(reconciled),
        "admissible_rows": len(admissible),
        "target_trades": int(target_trades),
        "remaining": max(0, int(target_trades) - len(admissible)),
        "ready": len(admissible) >= int(target_trades),
        "excluded": dataset.get("excluded", {}),
    }


def _execution_authority(artifacts_root: str = "artifacts") -> bool:
    """Live authority is earned in the bootstrap state, never assumed."""
    path = os.path.join(artifacts_root, "bootstrap_state.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("execution_authority", False))


def _absolute_path_artifacts(root: str = "artifacts") -> int:
    """Artifacts that still contain an operator path (contract Phase 1)."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        for name in filenames:
            if not name.lower().endswith(_TEXT_SUFFIXES):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > _MAX_SCANNED_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    if _ABSOLUTE_PATH.search(handle.read()):
                        count += 1
            except OSError:
                continue
    return count


def refresh_route_snapshot(
    journal_path: str = "trading_state.db",
    *,
    artifacts_root: str = "artifacts",
    path: str = SNAPSHOT_PATH,
    target_trades: int = DEFAULT_MIN_ADMISSIBLE_ROWS,
) -> Dict[str, Any]:
    """Gather the evidence and write the snapshot. A scheduled/operator call.

    Read-only with respect to the journal: the track record is measured with
    SELECTs only, and nothing is enriched or rewritten. The one write that
    can still happen is the rollout gate invalidating an approval whose
    evidence has drifted — that is the gate doing its job, not a side effect
    of measuring.
    """
    track: Dict[str, Any] = {}
    rollout: Dict[str, Any] = {}
    errors = []

    try:
        track = _read_only_track_record(journal_path, target_trades)
        track["execution_authority"] = _execution_authority(artifacts_root)
    except Exception as exc:  # noqa: BLE001 - the decision must not raise
        errors.append(f"track record unavailable: {exc}")

    try:
        from operations.live_rollout_gate import status as rollout_status

        rollout = rollout_status(journal_path) or {}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"rollout status unavailable: {exc}")

    snapshot = {
        "taken_at": time.time(),
        "journal_path": journal_path,
        "track_record": track,
        "rollout": rollout,
        "null_provenance_rows": _null_provenance_rows(journal_path),
        "absolute_path_artifacts": _absolute_path_artifacts(artifacts_root),
        "errors": errors,
    }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, default=str)
    except OSError as exc:
        errors.append(f"snapshot not written: {exc}")
    return snapshot


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Any] = {"decision": None, "at": 0.0}


def current_route(
    *,
    snapshot_path: str = SNAPSHOT_PATH,
    ttl_seconds: float = 300.0,
    max_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
) -> RouteDecision:
    """The route in force right now. Cheap, cached, never raises."""
    now = time.time()
    cached = _CACHE.get("decision")
    if cached is not None and (now - float(_CACHE.get("at", 0.0))) < float(ttl_seconds):
        return cached
    try:
        snapshot = _read_snapshot(snapshot_path)
        decision = select_route(snapshot, now=now, max_age_seconds=max_age_seconds)
    except Exception as exc:  # noqa: BLE001 - an unknown state keeps the route
        decision = RouteDecision(
            route=LEGACY,
            reasons=(f"route decision failed: {exc}",),
            decided_at=now,
        )
    _CACHE["decision"] = decision
    _CACHE["at"] = now
    return decision


def reset_cache() -> None:
    """Forget the cached decision (tests, and after a snapshot refresh)."""
    _CACHE["decision"] = None
    _CACHE["at"] = 0.0
