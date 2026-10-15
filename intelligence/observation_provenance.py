"""Evidence provenance for ``macro_observations``.

The table advertises ~448k macro observations. The independent information in
it is roughly two dozen distinct points on a handful of series. Anything that
treats row count as sample size will confidently produce nonsense.

Three distinct defects, all measured rather than assumed:

* **Sentinel series.** 42 series never vary -- ``value=101.4`` appears across
  AAR, AISHUB, CREDIT_CARD, ENTSO-E and SATELLITE simultaneously. That is a
  hardcoded placeholder, not an observation.
* **Duplicated frequency series.** Daily FRED series (``DGS10``, ``VIXCLS``,
  ``T10Y2Y``...) carry ~6,300 rows but only 14-18 distinct timestamps. The
  backfill re-ingested the same daily value hundreds of times, inflating the
  row count by ~350x while adding no information.
* **No surprise channel.** Calendar observations carry no ``surprise`` at all,
  so they cannot express a release outcome even where the row exists.

This module is the guard rail for the causal evidence path. It answers "can
this series support a statistical claim, and if not, why not" -- because the
failure mode being prevented is an edge being approved against a constant.

Every verdict carries its evidence. Nothing here raises on hostile data, and
nothing here grants execution authority.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("ObservationProvenance")

#: A series needs this many independent observations before a lag/lead test
#: can distinguish a relationship from coincidence. 14 points cannot: no
#: reasonable significance threshold survives that few degrees of freedom.
MIN_EFFECTIVE_POINTS = 60

#: A series carrying exactly one distinct value carries no information at all,
#: whatever its row count.
MIN_DISTINCT_VALUES = 2

VERDICT_SENTINEL = "SENTINEL_CONSTANT"
VERDICT_DUPLICATED = "DUPLICATED_FREQUENCY"
VERDICT_LOW_N = "INSUFFICIENT_EFFECTIVE_SAMPLES"
VERDICT_NO_VALUE = "NO_VALUE_RECORDED"
VERDICT_SCALE_CORRUPTED = "SCALE_CORRUPTED"
VERDICT_USABLE = "USABLE_AS_EVIDENCE"


@dataclass
class SeriesProvenance:
    """What one observation series actually contains."""

    series: str
    entity: str
    provider: str
    rows: int
    distinct_timestamps: int
    distinct_values: int
    distinct_dates: int
    min_value: Optional[float]
    max_value: Optional[float]
    has_surprise: bool
    verdict: str
    reason: str
    execution_authority: bool = False

    @property
    def inflation_factor(self) -> float:
        """Rows per independent observation. ~1.0 means no duplication."""
        return round(self.rows / self.distinct_timestamps, 2) if self.distinct_timestamps else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["inflation_factor"] = self.inflation_factor
        return d


@dataclass
class ProvenanceReport:
    series: List[SeriesProvenance] = field(default_factory=list)
    usable_entities: List[str] = field(default_factory=list)

    @property
    def usable(self) -> List[SeriesProvenance]:
        return [s for s in self.series if s.verdict == VERDICT_USABLE]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_series": len(self.series),
            "usable_series": len(self.usable),
            "sentinel_series": sum(1 for s in self.series if s.verdict == VERDICT_SENTINEL),
            "duplicated_series": sum(1 for s in self.series if s.verdict == VERDICT_DUPLICATED),
            "low_sample_series": sum(1 for s in self.series if s.verdict == VERDICT_LOW_N),
            "total_rows": sum(s.rows for s in self.series),
            "total_effective_points": sum(s.distinct_timestamps for s in self.series),
            "usable_entities": sorted(self.usable_entities),
            "execution_authority": False,
            "series": [s.to_dict() for s in self.series],
        }


def _classify(*, series: str = "", rows: int, distinct_timestamps: int, distinct_values: int,
              distinct_dates: int, has_surprise: bool) -> tuple:
    """Pure decision function so the policy is testable without a database."""
    if series == "realtime_fx":
        return (
            VERDICT_SCALE_CORRUPTED,
            "multi-instrument / scale-corrupted series (mixes EURUSD and DX-Y.NYB at ~50 vs ~102 scale)",
        )
    if distinct_values < MIN_DISTINCT_VALUES:
        if distinct_values == 0:
            # Rows exist but carry no measurement at all (calendar rows awaiting
            # an outcome). They support no statistical claim whatsoever, and
            # leaving them UNCLASSIFIED would make the labelling incomplete.
            return (
                VERDICT_NO_VALUE,
                f"{rows} rows carry no value - nothing measured to test against",
            )
        return (
            VERDICT_SENTINEL,
            f"one distinct value across {rows} rows - a placeholder, not an observation",
        )
    if distinct_timestamps < MIN_EFFECTIVE_POINTS:
        inflation = rows / distinct_timestamps if distinct_timestamps else 0.0
        return (
            VERDICT_LOW_N,
            f"{distinct_timestamps} independent points from {rows} rows "
            f"({inflation:.0f}x duplicated) - below the {MIN_EFFECTIVE_POINTS} "
            f"needed to distinguish lead/lag from coincidence",
        )
    if inflation_factor := (rows / distinct_timestamps if distinct_timestamps else 0.0):
        if inflation_factor > 50:
            return (
                VERDICT_DUPLICATED,
                f"{inflation_factor:.0f}x row inflation; deduplicate before use",
            )
    return (
        VERDICT_USABLE,
        f"{distinct_timestamps} independent points across {distinct_dates} days",
    )


def audit_series(con: sqlite3.Connection) -> ProvenanceReport:
    """Classify every observation series by what it can actually support."""
    rows = con.execute(
        """
        SELECT series, entity, provider,
               COUNT(*)                                        AS rows,
               COUNT(DISTINCT observed_at)                     AS dts,
               COUNT(DISTINCT value)                           AS dvals,
               COUNT(DISTINCT substr(observed_at,1,10))        AS ddates,
               MIN(value) AS lo, MAX(value) AS hi,
               SUM(CASE WHEN surprise IS NOT NULL THEN 1 ELSE 0 END) AS n_surprise
        FROM macro_observations
        GROUP BY series, provider, entity
        ORDER BY rows DESC
        """
    ).fetchall()

    report = ProvenanceReport()
    for r in rows:
        verdict, reason = _classify(
            series=str(r["series"]),
            rows=int(r["rows"] or 0),
            distinct_timestamps=int(r["dts"] or 0),
            distinct_values=int(r["dvals"] or 0),
            distinct_dates=int(r["ddates"] or 0),
            has_surprise=bool(r["n_surprise"]),
        )
        report.series.append(SeriesProvenance(
            series=str(r["series"]),
            entity=str(r["entity"] or ""),
            provider=str(r["provider"] or ""),
            rows=int(r["rows"] or 0),
            distinct_timestamps=int(r["dts"] or 0),
            distinct_values=int(r["dvals"] or 0),
            distinct_dates=int(r["ddates"] or 0),
            min_value=r["lo"],
            max_value=r["hi"],
            has_surprise=bool(r["n_surprise"]),
            verdict=verdict,
            reason=reason,
        ))
    report.usable_entities = sorted({s.entity for s in report.usable if s.entity})
    return report


def edge_evidence_coverage(con: sqlite3.Connection) -> Dict[str, Any]:
    """How many catalog edges could an observation-based validator ever judge?

    An edge needs BOTH endpoints backed by a usable series. Counting only the
    source, or only the target, overstates what is testable.
    """
    report = audit_series(con)
    usable = set(report.usable_entities)

    edges = con.execute(
        """
        SELECT e.edge_id, e.relation, e.status,
               s.name AS src, s.entity_type AS src_type,
               t.name AS tgt, t.entity_type AS tgt_type
        FROM macro_edges e
        JOIN macro_entities s ON s.entity_id = e.source_entity
        JOIN macro_entities t ON t.entity_id = e.target_entity
        """
    ).fetchall()

    event_sourced = [e for e in edges if e["src_type"] == "EVENT_CLASS"]
    both, source_only, neither = [], [], []
    for e in edges:
        if e["src_type"] == "EVENT_CLASS":
            continue
        s_ok, t_ok = e["src"] in usable, e["tgt"] in usable
        if s_ok and t_ok:
            both.append(e)
        elif s_ok:
            source_only.append(e)
        else:
            neither.append(e)

    return {
        "total_edges": len(edges),
        "event_class_edges": len(event_sourced),
        "non_event_class_edges": len(edges) - len(event_sourced),
        "observation_validatable": len(both),
        "source_measurable_target_not": len(source_only),
        "no_measurable_endpoint": len(neither),
        "usable_entities": sorted(usable),
        "verdict": (
            "NO_OBSERVATION_EVIDENCE_AVAILABLE"
            if not both else "PARTIAL_COVERAGE"
        ),
        "note": (
            "An observation-based validator has nothing to test. Edges sourced by "
            "EVENT_CLASS entities need release reactions (see "
            "docs/CALENDAR_ACTUAL_GAP.md); edges sourced by macro-state entities "
            "need a real price series for BOTH endpoints, which macro_observations "
            "does not currently contain."
        ),
        "execution_authority": False,
    }


PROV_UNCLASSIFIED = "UNCLASSIFIED"
PROV_VERIFIED = "VERIFIED_EFFECTIVE"

#: Verdicts that make a series ineligible as statistical evidence. A series is
#: quarantined by label, not by deletion: the rows are research history and may
#: become meaningful if a real feed ever lands behind the same series name.
QUARANTINED_VERDICTS = (
    VERDICT_SENTINEL,
    VERDICT_DUPLICATED,
    VERDICT_LOW_N,
    VERDICT_NO_VALUE,
    VERDICT_SCALE_CORRUPTED,
)


def quarantine(con: sqlite3.Connection) -> Dict[str, Any]:
    """Stamp every observation row with its series' provenance verdict.

    Idempotent, and additive: nothing is deleted and no value is altered. The
    only thing that changes is that ``data_provenance`` stops saying
    ``UNCLASSIFIED``, so a consumer can filter instead of guessing.

    Returns counts per verdict so the result is auditable without a second
    query, and raises nothing on hostile tables -- a failed quarantine must not
    take down whatever called it.
    """
    from intelligence.macro_observations import ensure_provenance_column

    ensure_provenance_column(con)
    report = audit_series(con)
    counts: Dict[str, int] = {}
    for s in report.series:
        try:
            con.execute(
                "UPDATE macro_observations SET data_provenance=?"
                " WHERE series=? AND provider=?",
                (s.verdict, s.series, s.provider),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("quarantine update failed for %s: %s", s.series, exc)
    for row in con.execute(
        "SELECT data_provenance, COUNT(*) n, COUNT(DISTINCT series) series_n"
        " FROM macro_observations GROUP BY data_provenance"
    ):
        counts[str(row[0])] = {"rows": int(row[1]), "series": int(row[2])}
    quarantined_rows = sum(
        v["rows"] for k, v in counts.items() if k in QUARANTINED_VERDICTS)
    quarantined_series = sum(
        v["series"] for k, v in counts.items() if k in QUARANTINED_VERDICTS)
    return {
        "labelled": counts,
        "quarantined_rows": quarantined_rows,
        "quarantined_series": quarantined_series,
        "total_rows": sum(v["rows"] for v in counts.values()),
        "policy": "labelled, never deleted",
        "execution_authority": False,
    }


def evidence_only_sql(where: str = "1=1") -> str:
    """SQL fragment selecting only rows eligible as statistical evidence.

    Consumers should use this rather than re-deriving the policy, so the rule
    lives in exactly one place.
    """
    marks = ",".join("?" * len(QUARANTINED_VERDICTS))
    return (
        f"SELECT * FROM macro_observations"
        f" WHERE data_provenance NOT IN ({marks})"
        f"   AND ({where})"
    )


def evidence_only_args(extra: tuple = ()) -> tuple:
    return QUARANTINED_VERDICTS + tuple(extra)


def quarantine_report(db_path: str = "trading_state.db") -> Dict[str, Any]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        from intelligence.macro_observations import ensure_provenance_column
        ensure_provenance_column(con)
        rows = con.execute(
            "SELECT data_provenance, COUNT(*) n, COUNT(DISTINCT series) s"
            " FROM macro_observations GROUP BY data_provenance"
        ).fetchall()
        coverage = edge_evidence_coverage(con)
    labelled = {str(r["data_provenance"]): {"rows": int(r["n"]), "series": int(r["s"])}
                for r in rows}
    return {
        "labelled": labelled,
        "quarantined_rows": sum(v["rows"] for k, v in labelled.items()
                                if k in QUARANTINED_VERDICTS),
        "unlabelled_rows": labelled.get(PROV_UNCLASSIFIED, {}).get("rows", 0),
        "edge_coverage": coverage,
        "execution_authority": False,
    }


def audit(db_path: str = "trading_state.db") -> Dict[str, Any]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        report = audit_series(con).to_dict()
        report["edge_coverage"] = edge_evidence_coverage(con)
    return report
