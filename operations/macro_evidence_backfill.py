"""Backfill historical FRED release evidence into macro_events and reaction trackers.

Closes the 2026-09-26 audit finding: 225k ``alternative_nowcast`` macro_sources
and 397k ``macro_observations`` accumulated while ``macro_events``,
``event_reaction_trackers`` and ``event_reactions`` stayed at zero, leaving the
causal engine with no measured reaction evidence.

This module deliberately does NOT mass-convert the rolling nowcasts — they are
indicator readings, not timestamped releases, and converting them would poison
``macro_events`` with non-events. The genuinely backfillable inventory is the
set of distinct FRED observations (series, observed_at date, value, previous):
official-statistics level releases, PRIMARY tier per source policy. Note the
traded-vs-forecast semantic difference: FRED publishes levels, so ``forecast``
here is the prior value and ``surprise`` is the day-over-day change. Only
nonzero-change releases are backfilled — the directional evidence framework
counts a zero-surprise reaction as a directional miss, and an in-line release
carries no directional hypothesis to test.

Semantics mirror the live macro_event_evidence stage exactly:

- One real ``macro_sources`` row per release (content-hash deduped by
  ``ingest_source``, PRIMARY tier for fred.stlouisfed.org) so reactions carry
  a valid foreign key and remain retrievable via ``recent_event_reactions``.
- ``macro_events`` rows are written through ``TradingMemory.record_events``
  gated on atomic-fact keys ``macro_event:<time>:<CCY>:<title>`` (exactly-once,
  identical key convention to the live stage so both paths interlock).
- Trackers are scheduled through ``schedule_event_reaction`` with a REAL
  historical anchor: the first FRESH tick at/after the release date from the
  journal's append-only ``market_ticks`` ledger. The ledger is daily snapshots,
  not intraday, so the anchor time is recorded honestly and guard-railed
  (release-to-anchor lag must stay within ``anchor_max_lag_seconds``).
- Reactions are measured only at windows the ledger can honestly support: the
  single ``1d`` window, self-labelled ``resolution: daily``. A tracker with no
  tick inside ledger coverage stays TRACKING — partial evidence is never
  faked into a completed reaction.

Observational and idempotent: no execution authority, safe to re-run, and
``apply=False`` (the default) performs a pure dry run with zero writes.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from operations.agent_operations import AgentOperations
    from intelligence.macro_graph import MacroGraph
except ImportError:  # package-style import
    from operations.agent_operations import AgentOperations
    from intelligence.macro_graph import MacroGraph

# FRED series carried by the observation backlog, mapped to the currency whose
# event risk they inform. All are US series; VIXCLS is an implied-vol index,
# not an economic release, so it stays out of macro_events.
FRED_SERIES_CURRENCIES: Dict[str, Tuple[str, ...]] = {
    "BAA10Y": ("USD",), "CPIAUCSL": ("USD",), "DFEDTARU": ("USD",),
    "DFF": ("USD",), "DFII10": ("USD",), "DGS10": ("USD",),
    "DGS2": ("USD",), "DGS30": ("USD",), "FEDFUNDS": ("USD",),
    "GDP": ("USD",), "GDPC1": ("USD",), "INDPRO": ("USD",),
    "PAYEMS": ("USD",), "PCEPILFE": ("USD",), "T10Y2Y": ("USD",),
    "T10Y3M": ("USD",), "UNRATE": ("USD",),
}

# The tick ledger records daily snapshots, so only the 1d window can be
# measured honestly. Self-labelled so the validation engine can distinguish
# daily-resolution returns from live intraday ones.
BACKFILL_REACTION_WINDOWS: Dict[str, int] = {"1d": 86400}

BACKFILL_SOURCE_QUALITY = "BACKFILL_FRED_LEVEL_RELEASE"


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _load_tick_stream(db_path: str, symbol: str) -> List[Tuple[datetime, float]]:
    """Historical FRESH tick stream (time, mid) for one symbol, oldest first.

    Read-only against the journal; the ledger is append-only so no writes are
    possible or attempted. Only FRESH quality rows are consumed — the ledger's
    own designation of a tradable quote (STALE/MISSING rows are operational
    evidence, never prices).
    """
    stream: List[Tuple[datetime, float]] = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT observed_at, bid, ask, mid FROM market_ticks
               WHERE symbol=? AND quality='FRESH' ORDER BY observed_at ASC""",
            (symbol,),
        ).fetchall()
    finally:
        con.close()
    for row in rows:
        ts = _parse_ts(row["observed_at"])
        bid, ask, mid = _finite(row["bid"]), _finite(row["ask"]), _finite(row["mid"])
        price = None
        if mid is not None and mid > 0:
            price = mid
        elif bid is not None and ask is not None and ask >= bid and bid > 0:
            price = (bid + ask) / 2.0
        if ts is not None and price is not None:
            stream.append((ts, price))
    return stream


def _first_tick_at_or_after(stream: List[Tuple[datetime, float]], when: datetime) -> Optional[Tuple[datetime, float]]:
    for ts, price in stream:  # stream is small (tens of rows); linear scan is fine
        if ts >= when:
            return (ts, price)
    return None


def _iter_distinct_fred_releases(operations: AgentOperations) -> Iterable[Dict[str, Any]]:
    """Yield one deduplicated FRED release per (series, date, value, previous)."""
    with operations._con() as con:
        from intelligence.observation_provenance import QUARANTINED_VERDICTS
        marks = ",".join("?" * len(QUARANTINED_VERDICTS))
        rows = con.execute(
            f"""SELECT DISTINCT series, observed_at, value, previous, surprise, entity
               FROM macro_observations
               WHERE surprise IS NOT NULL
                 AND data_provenance NOT IN ({marks})
               ORDER BY observed_at ASC, series ASC""",
            QUARANTINED_VERDICTS,
        ).fetchall()
    for row in rows:
        yield {
            "series": str(row["series"]),
            "observed_at": str(row["observed_at"]),
            "value": row["value"],
            "previous": row["previous"],
            "surprise": row["surprise"],
            "entity": row["entity"],
        }


def backfill_macro_evidence_from_history(
    operations: AgentOperations,
    trade_memory: Any,
    graph: MacroGraph,
    symbol: str,
    *,
    apply: bool = False,
    max_releases: Optional[int] = None,
    anchor_max_lag_seconds: int = 172800,
) -> Dict[str, Any]:
    """Backfill macro_events + reaction evidence from the historical FRED backlog.

    ``apply=False`` (default) evaluates every gate against live data and
    writes nothing. With ``apply=True`` the same plan executes through the
    exact live-stage primitives (ingest_source, record_events with fact-key
    gating, schedule_event_reaction, record_event_reaction) so backfilled and
    live evidence are structurally identical and future live pulses dedupe
    against it naturally.
    """
    symbol = str(symbol or "").upper()
    if not symbol:
        raise ValueError("symbol is required")

    journal_path = getattr(operations, "db_path", "trading_state.db")
    tick_stream = _load_tick_stream(journal_path, symbol)
    report: Dict[str, Any] = {
        "symbol": symbol,
        "apply": bool(apply),
        "tick_observations": len(tick_stream),
        "tick_span": [tick_stream[0][0].isoformat(), tick_stream[-1][0].isoformat()] if tick_stream else [],
        "releases_scanned": 0,
        "releases_eligible": 0,
        "macro_events_recorded": 0,
        "macro_events_already_gated": 0,
        "sources_ingested": 0,
        "trackers_scheduled": 0,
        "trackers_gated_invalid": 0,
        "anchors_skipped_no_tick": 0,
        "anchors_skipped_stale": 0,
        "reactions_completed": 0,
        "trackers_still_tracking": 0,
        "trackers_abandoned": 0,
        "windows_unmeasured": 0,
        "skipped_series": {},
        "execution_authority": False,
    }
    skipped: Dict[str, int] = report["skipped_series"]

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for release in _iter_distinct_fred_releases(operations):
        report["releases_scanned"] += 1
        if max_releases is not None and report["releases_eligible"] >= int(max_releases):
            break
        series = release["series"]
        currencies = FRED_SERIES_CURRENCIES.get(series)
        if not currencies:
            _skip(f"unmapped_series:{series}")
            continue
        event_time = _parse_ts(release["observed_at"])
        value, previous = _finite(release["value"]), _finite(release["previous"])
        if event_time is None or value is None or previous is None:
            _skip(f"malformed_row:{series}")
            continue
        change = round(value - previous, 6)
        if change == 0.0:
            # An in-line release carries no directional hypothesis; counting
            # it would only add guaranteed misses to the win-rate statistics.
            _skip(f"zero_change:{series}")
            continue
        currency = currencies[0]
        title = f"FRED {series}"

        # ---- exactly-once gate: same fact-key convention as the live stage
        fact_key = f"macro_event:{event_time.isoformat()}:{currency}:{title}"
        try:
            known = trade_memory.retrieve_facts([fact_key], limit=1)
        except Exception:
            known = []
        if known:
            report["macro_events_already_gated"] += 1
            continue

        # ---- historical anchor: first FRESH tick at/after the release date
        anchor = _first_tick_at_or_after(tick_stream, event_time)
        if anchor is None:
            report["anchors_skipped_no_tick"] += 1
            continue
        anchor_at, anchor_price = anchor
        lag = (anchor_at - event_time).total_seconds()
        if lag > max(0, int(anchor_max_lag_seconds)):
            report["anchors_skipped_stale"] += 1
            continue
        report["releases_eligible"] += 1

        if not apply:
            # Dry-run accounting only: what apply would record.
            report["macro_events_recorded"] += 1
            report["sources_ingested"] += 1
            report["trackers_scheduled"] += 1
            measured = _first_tick_at_or_after(tick_stream, anchor_at + timedelta(seconds=86400))
            if measured is None:
                report["windows_unmeasured"] += 1
                report["trackers_abandoned"] += 1
            else:
                report["reactions_completed"] += 1
            continue

        # ---- apply path: real source, real event, real tracker, real reaction
        source_id = graph.ingest_source(
            title=f"FRED level release: {series} {event_time.date().isoformat()}",
            payload={
                "kind": "macro_evidence_backfill",
                "series": series,
                "observed_at": event_time.date().isoformat(),
                "value": value,
                "previous": previous,
                "prev_day_change": change,
                "entity": release["entity"],
                "note": "backfilled historical FRED observation; daily level release",
            },
            source_url=f"https://fred.stlouisfed.org/series/{series}",
            publisher="FRED",
            published_at=event_time.date().isoformat(),
            rights_note="FRED observation; research evidence only; not a calendar release blackout trigger",
        )
        report["sources_ingested"] += 1

        trade_memory.record_events([{
            "time": event_time.isoformat(), "currency": currency, "title": title,
            "event": title, "actual": value, "forecast": previous, "previous": previous,
            "importance": 2, "source": "FRED", "series": series,
        }])
        report["macro_events_recorded"] += 1

        # The tracker's event time is the ANCHOR time (first observed price
        # after release), keeping anchor_observed_at consistent with event_time
        # so live capture pulses compute elapsed windows from the same origin.
        tracker_key = graph.schedule_event_reaction(
            source_id, symbol,
            {"event": title, "title": title, "time": anchor_at.isoformat(),
             "currency": currency, "actual": value, "forecast": previous,
             "previous": previous},
            anchor_price=anchor_price, observed_at=anchor_at,
        )
        if tracker_key is None:
            report["trackers_gated_invalid"] += 1
            continue
        report["trackers_scheduled"] += 1

        # ---- measure the 1d window from the historical tick stream
        measurement: Optional[Dict[str, Any]] = None
        seconds = BACKFILL_REACTION_WINDOWS["1d"]
        tick = _first_tick_at_or_after(tick_stream, anchor_at + timedelta(seconds=seconds))
        if tick is None:
            # The 1d window cannot be measured from the ledger, so this tracker
            # can never be completed honestly. Leaving it TRACKING would let a
            # live capture_due_event_reactions pulse fill every window from one
            # current spot price, labelling a multi-day return as a 1m/5m/1h
            # reaction. Retire it instead: only status='TRACKING' is claimable.
            with graph._con() as con:
                con.execute(
                    "UPDATE event_reaction_trackers SET status='ABANDONED' "
                    "WHERE tracker_key=? AND status='TRACKING'",
                    (tracker_key,),
                )
            report["windows_unmeasured"] += 1
            report["trackers_abandoned"] += 1
        else:
            observed_at, observed_price = tick
            measurement = {
                "target_seconds": seconds,
                "observed_at": observed_at.isoformat(),
                "observed_seconds": (observed_at - anchor_at).total_seconds(),
                "observed_price": observed_price,
                "return_fraction": (observed_price - anchor_price) / anchor_price,
                "resolution": "daily",
            }
        if measurement is not None:
            reaction_id = graph.record_event_reaction(
                source_id, symbol,
                actual=value, forecast=previous, previous=previous,
                windows={"1d": measurement}, source_quality=BACKFILL_SOURCE_QUALITY,
            )
            # Mirror capture_due_event_reactions' completion contract exactly:
            # otherwise live pulses would re-complete these trackers and
            # duplicate the reactions.
            with graph._con() as con:
                con.execute(
                    "UPDATE event_reaction_trackers SET status='COMPLETE', final_reaction_id=? WHERE tracker_key=?",
                    (int(reaction_id), tracker_key),
                )
            report["reactions_completed"] += 1

    logging.getLogger("MacroEvidenceBackfill").info(
        "backfill scan complete: %s", {k: report[k] for k in (
            "apply", "releases_scanned", "releases_eligible", "macro_events_recorded",
            "trackers_scheduled", "reactions_completed")})
    return report
