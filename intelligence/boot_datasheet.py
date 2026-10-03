"""Boot datasheet: one table for at-a-glance boot data health.

Three independent layers each know a fragment of how boot went:

* ``core/autonomous_bootstrap.py`` records per-step rows (its PREPARE steps
  each carry an elapsed time and a status from ``details``);
* ``intelligence/boot_fetch_watchdog.py`` outcomes are recorded by the
  orchestrator per pre-pulse stage (``boot_stage_outcomes``);
* ``data/api_manager.py`` records degradation markers at every fail-open
  point (stale-cache serve, per-series FRED skip, batch network abort).

Before this module, each layer logged separately (and APIManager only
logged warnings), so nobody could answer "was boot healthy?" from one
place. ``get_datasheet()`` returns a process-lifetime singleton; bootstrap
resets it in ``run()``, all three layers record into it, and the
orchestrator renders the table exactly once after the first pre-pulse
ingest and journals it as a ``boot_datasheet`` event (best-effort, never
fatal). The render is idempotent: a second pulse logs DEBUG, not a second
table.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("BootDatasheet")

# Fixed-width columns so the log table stays scannable in any console.
# Total line width: 2 + _NAME_W + 1 + 8 + 1 + 8 + 2 + _TAIL_W = 80.
_NAME_W = 26
_TAIL_W = 32


class BootDatasheet:
    """Collect + render the boot data-health table (thread-safe)."""

    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []
        # RLock: summary() holds the lock while _summary_counts() re-acquires
        # it (a plain Lock self-deadlocks there).
        self._lock = threading.RLock()
        self._rendered = False
        self._summary: Dict[str, Any] = {}

    # -- recording ---------------------------------------------------------

    def reset(self) -> None:
        """Clear all recorded rows (bootstrap calls this at run() start)."""
        with self._lock:
            self._rows.clear()
            self._rendered = False
            self._summary = {}

    def record_step(self, name: str, elapsed_s: float, status: str,
                    note: str = "") -> None:
        """Record one bootstrap PREPARE step row."""
        with self._lock:
            self._rows.append({
                "kind": "step",
                "name": str(name)[:_NAME_W],
                "elapsed_s": max(0.0, float(elapsed_s)),
                "status": str(status or "?")[:7].upper(),
                "note": str(note)[:_TAIL_W],
            })

    def record_degraded(self, source: str, outcome: str, note: str = "") -> None:
        """Record one provider degradation marker (APIManager fail-opens)."""
        with self._lock:
            # Cap: some fail-open points fire per call, so a churning
            # session must not grow the table unboundedly.
            if len([r for r in self._rows if r["kind"] == "degraded"]) >= 24:
                return
            self._rows.append({
                "kind": "degraded",
                "name": str(source)[:_NAME_W],
                "elapsed_s": 0.0,
                "status": str(outcome)[:7].upper(),
                "note": str(note)[:_TAIL_W],
            })

    def record_fresh(self, source: str, note: str = "") -> None:
        """Record one successful fetch (provider served fresh data).

        Fresh rows never render in the datasheet table (healthy noise);
        they feed the per-boot provider scorecard instead.
        """
        with self._lock:
            if len([r for r in self._rows if r["kind"] == "fresh"]) >= 64:
                return
            self._rows.append({
                "kind": "fresh",
                "name": str(source)[:_NAME_W],
                "elapsed_s": 0.0,
                "status": "FRESH",
                "note": str(note)[:_TAIL_W],
            })

    def records(self) -> List[Dict[str, Any]]:
        """Snapshot of all recorded rows (scorecard input)."""
        with self._lock:
            return [dict(r) for r in self._rows]

    def record_pulse_stage(self, stage: str, outcome: Dict[str, Any]) -> None:
        """Record one boot-fetch watchdog stage outcome dict."""
        ok = bool(outcome.get("ok"))
        timed_out = bool(outcome.get("timed_out"))
        status = "OK" if ok else ("TIMEOUT" if timed_out else "FAIL")
        note = "" if ok else str(outcome.get("error") or "")[:_TAIL_W]
        with self._lock:
            self._rows.append({
                "kind": "pulse",
                "name": str(stage)[:_NAME_W],
                "elapsed_s": max(0.0, float(outcome.get("elapsed_s") or 0.0)),
                "status": status,
                "note": note,
            })

    # -- rendering ---------------------------------------------------------

    @staticmethod
    def _fmt_cell(text: str, width: int) -> str:
        text = text or ""
        return text if len(text) <= width else text[: width - 3] + "..."

    def _summary_counts(self) -> Dict[str, Any]:
        with self._lock:
            steps = [r for r in self._rows if r["kind"] == "step"]
            degraded = [r for r in self._rows if r["kind"] == "degraded"]
            pulse = [r for r in self._rows if r["kind"] == "pulse"]
            bad = [r for r in self._rows if r["status"] in ("TIMEOUT", "FAIL", "SKIPPED", "ERROR")]

        slow = max(steps, key=lambda r: r["elapsed_s"], default=None)
        return {
            "steps": len(steps),
            "degraded_markers": len(degraded),
            "pulse_stages": len(pulse),
            "fresh_markers": len([r for r in self._rows if r["kind"] == "fresh"]),
            "degraded_total": len(bad),
            "slowest_step": (
                {"name": slow["name"], "elapsed_s": slow["elapsed_s"]}
                if slow is not None else None),
            "degraded_sources": sorted({r["name"] for r in degraded}),
        }

    def render(self) -> str:
        """Return the fixed-width datasheet table (one line per row)."""
        with self._lock:
            rows = list(self._rows)

        def _line(name: str, status: str, elapsed: str, note: str) -> str:
            return "  {n:<{nw}} {s:<8} {e:>8}  {t}".format(
                n=self._fmt_cell(name, _NAME_W), nw=_NAME_W,
                s=self._fmt_cell(status, 8), e=elapsed,
                t=self._fmt_cell(note, _TAIL_W))

        def _count(kind: str) -> int:
            return len([r for r in rows if r["kind"] == kind])

        lines = [
            "BOOT DATA HEALTH (steps=%d pulse-stages=%d degraded-markers=%d)"
            % (_count("step"), _count("pulse"), _count("degraded")),
            _line("source/stage", "status", "secs", "note"),
            "-" * (2 + _NAME_W + 1 + 8 + 1 + 8 + 2 + _TAIL_W),
        ]
        for r in rows:
            if r["kind"] == "fresh":
                continue  # healthy noise: kept for the scorecard, not the table
            elapsed = ("%.1f" % r["elapsed_s"]) if r["elapsed_s"] else "-"
            lines.append(_line(r["name"], r["status"], elapsed, r["note"]))
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Compact structured digest for the journal event payload."""
        with self._lock:
            self._summary = self._summary_counts()
        return dict(self._summary)

    def maybe_render_once(self, emit_event=None) -> Optional[str]:
        """Render the table on the first call only; journal it best-effort.

        Subsequent calls (later pulses) are no-ops that return None so a
        long-lived session never re-prints the boot table.
        """
        with self._lock:
            if self._rendered:
                return None
            self._rendered = True
        table = self.render()
        logger.info("boot datasheet:\n%s", table)
        if emit_event is not None:
            try:
                emit_event("boot_datasheet", {
                    "table": table,
                    "summary": self.summary(),
                })
            except Exception:
                logger.debug("boot_datasheet journal event failed", exc_info=True)
        return table


_ds_singleton: Optional[BootDatasheet] = None
_ds_lock = threading.Lock()


def get_datasheet() -> BootDatasheet:
    """Process-lifetime datasheet singleton."""
    global _ds_singleton
    with _ds_lock:
        if _ds_singleton is None:
            _ds_singleton = BootDatasheet()
        return _ds_singleton


def boot_datasheet_enabled() -> bool:
    """Opt-out switch (LLM_TRADER_BOOT_DATASHEET=0 disables recording)."""
    return os.environ.get("LLM_TRADER_BOOT_DATASHEET", "1").strip().lower() \
        not in ("0", "false", "no")
