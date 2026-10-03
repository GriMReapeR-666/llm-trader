"""Provider scorecard: per-boot source verdicts + cross-session trend.

The boot datasheet (``intelligence/boot_datasheet.py``) collects raw rows;
this module turns them into an answer to "which external sources served
fresh, stale, or no data this boot, and is that getting worse?" and
journals it as one ``provider_scorecard`` event per boot.

Verdicts per source (from the datasheet's fresh + degraded markers):

* ``FRESH``  — only successful fetches recorded this boot;
* ``STALE``  — served last-good cached data after a failure;
* ``SKIP``   — backed off, not configured, or a hung series was skipped;
* ``NONE``   — data source known to the system but produced nothing this
  boot (a datasheet ``fresh`` marker whose note starts with ``0`` — e.g.
  ``0 articles`` — counts as no data served);
* ``ABORT``  — batch-level network abort (worst outcome, kept distinct).

Sources are known to the system two ways: anything observed this boot
(any fresh/degraded row), plus configured-but-never-called sources passed
via ``known_sources`` (surface them as ``UNSEEN`` so a silently-dead
source is visible in the trend instead of absent).

Trend: ``trend_since`` loads ``provider_scorecard`` events from the
operations journal and reports, per source, how the verdict moved —
``WORSE`` (fresh→anything else), ``BETTER`` (anything→fresh), ``SAME``,
or ``NEW``/``GONE``. A boot whose scorecard is worse than the previous
boot's scores severity ``WARNING`` on the event.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("BootDatasheet")

_VERDICT_RANK = {"FRESH": 0, "NONE": 1, "STALE": 2, "SKIP": 2, "ABORT": 3, "UNSEEN": 3}
_UNKNOWN_NOTE_ZERO_PREFIX = "0"

# Source names APIManager records under (datasheet fresh/degraded markers).
# Passed as known_sources so a configured-but-never-called source shows up
# as UNSEEN in the trend instead of being silently absent.
KNOWN_API_SOURCES = (
    "fred.get_series", "newsapi.get_news", "newsapi.get_hot_topics")


def derive_verdicts(records: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Fold datasheet fresh+degraded rows into one verdict per source.

    Degraded rows win over fresh (a source that degraded even once this
    boot is not counted healthy); among degraded rows the worst outcome
    wins so ABORT is not masked by a later SKIP.
    """
    verdicts: Dict[str, str] = {}
    for row in records:
        name = str(row.get("name") or "")
        if not name:
            continue
        kind = row.get("kind")
        status = str(row.get("status") or "").upper()
        note = str(row.get("note") or "")
        if kind == "fresh":
            served = note.split(" ")[0].strip().rstrip("():") if note else ""
            verdict = (
                "NONE" if served.startswith(_UNKNOWN_NOTE_ZERO_PREFIX)
                else "FRESH")
        elif kind == "degraded":
            verdict = status if status in _VERDICT_RANK else "SKIP"
        else:
            continue  # step/pulse rows are boot-structure, not provider data
        prev = verdicts.get(name)
        if prev is None or _VERDICT_RANK[verdict] > _VERDICT_RANK.get(prev, 0):
            verdicts[name] = verdict
    return verdicts


def scorecard(records: Iterable[Dict[str, Any]],
              known_sources: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Build the per-boot scorecard payload (verdicts + counts)."""
    verdicts = derive_verdicts(records)
    for src in known_sources or ():
        src = str(src)
        if src and src not in verdicts:
            verdicts[src] = "UNSEEN"
    counts: Dict[str, int] = {}
    for v in verdicts.values():
        counts[v] = counts.get(v, 0) + 1
    degraded_sources = sorted(
        s for s, v in verdicts.items() if _VERDICT_RANK.get(v, 0) > 0)
    return {
        "verdicts": dict(sorted(verdicts.items())),
        "counts": dict(sorted(counts.items())),
        "degraded_sources": degraded_sources,
    }


def _score_of(summary: Dict[str, Any]) -> int:
    """Health score: lower is better (count of non-fresh sources)."""
    counts = summary.get("counts") or {}
    return sum(int(n) for v, n in counts.items() if v != "FRESH")


def _verdict_movement(prev: str, curr: str) -> str:
    if prev == curr:
        return "SAME"
    pr = _VERDICT_RANK.get(prev, 0)
    cr = _VERDICT_RANK.get(curr, 0)
    if pr == 0 and cr > 0:
        return "WORSE"
    if pr > 0 and cr == 0:
        return "BETTER"
    if cr > pr:
        return "WORSE"
    if cr < pr:
        return "BETTER"
    return "SAME"


def trend_vs_previous(current: Dict[str, Any],
                      previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare this boot's scorecard against the previous boot's."""
    if not previous:
        return {"available": False}
    cur_v: Dict[str, str] = current.get("verdicts") or {}
    prev_v: Dict[str, str] = previous.get("verdicts") or {}
    movement: Dict[str, str] = {}
    for src in sorted(set(cur_v) | set(prev_v)):
        c, p = cur_v.get(src), prev_v.get(src)
        if p is None:
            movement[src] = "NEW"
        elif c is None:
            movement[src] = "GONE"
        else:
            movement[src] = _verdict_movement(p, c)
    cur_score = _score_of(current)
    prev_score = _score_of(previous)
    return {
        "available": True,
        "movement": movement,
        "worse": sorted(s for s, m in movement.items() if m == "WORSE"),
        "better": sorted(s for s, m in movement.items() if m == "BETTER"),
        "boot_score": cur_score,
        "prev_boot_score": prev_score,
        "regressed": cur_score > prev_score,
    }


def load_recent_summaries(con: Any, limit: int = 5) -> List[Dict[str, Any]]:
    """Most recent ``provider_scorecard`` summaries from the journal.

    Returns newest-first ``[{created_at, summary}, ...]``; best-effort —
    a missing/closed journal yields an empty list, never a boot failure.
    """
    try:
        rows = con.execute(
            "SELECT created_at, payload FROM operational_events "
            "WHERE event_type='provider_scorecard' "
            "ORDER BY created_at DESC LIMIT ?",
            (int(limit),)).fetchall()
    except Exception as exc:
        logger.debug("provider_scorecard trend read failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
            out.append({"created_at": row["created_at"],
                        "summary": payload.get("summary") or payload})
        except Exception:
            continue
    return out


def severity_for(current: Dict[str, Any], trend: Dict[str, Any]) -> str:
    """WARNING when this boot regressed vs the previous one, else INFO."""
    if trend.get("available") and trend.get("regressed"):
        return "WARNING"
    if any(v != "FRESH" for v in (current.get("verdicts") or {}).values()):
        return "INFO"
    return "INFO"


def build_event(current: Dict[str, Any],
                trend: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Return ``(severity, payload)`` for the provider_scorecard event."""
    payload = {
        "summary": {
            "verdicts": current.get("verdicts") or {},
            "counts": current.get("counts") or {},
            "degraded_sources": current.get("degraded_sources") or [],
        },
        "trend": trend,
    }
    return severity_for(current, trend), payload


def render(current: Dict[str, Any], trend: Dict[str, Any]) -> str:
    """Compact fixed-width text for the boot log, right after the datasheet."""
    verdicts = current.get("verdicts") or {}
    lines = ["PROVIDER SCORECARD (fresh=%d stale/skip=%d none/unseen=%d)" % (
        sum(1 for v in verdicts.values() if v == "FRESH"),
        sum(1 for v in verdicts.values() if v in ("STALE", "SKIP", "ABORT")),
        sum(1 for v in verdicts.values() if v in ("NONE", "UNSEEN")))]
    for src, verdict in sorted(verdicts.items()):
        suffix = ""
        if trend.get("available"):
            mv = (trend.get("movement") or {}).get(src)
            if mv in ("WORSE", "BETTER", "GONE"):
                suffix = "  (%s vs prev boot)" % mv
        lines.append("  %-26s %-8s%s" % (src[:26], verdict, suffix))
    return "\n".join(lines)
