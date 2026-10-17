"""Observation provenance: what each observation series can actually support.

`macro_observations` advertises ~448k rows. It contains 42 never-varying
sentinel series and 18 duplicated FRED series whose independent sample is 14-18
points. A validator that treats row count as sample size will approve edges
against a constant -- so the classification policy is pinned here.
"""
from __future__ import annotations

import sqlite3

import pytest

from intelligence.observation_provenance import (
    MIN_EFFECTIVE_POINTS,
    VERDICT_DUPLICATED,
    VERDICT_LOW_N,
    VERDICT_SENTINEL,
    VERDICT_USABLE,
    _classify,
    audit_series,
    edge_evidence_coverage,
)


def _con(tmp_path, name="o.db"):
    con = sqlite3.connect(str(tmp_path / name))
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE macro_observations (
             observation_id INTEGER PRIMARY KEY, captured_at TEXT, entity TEXT,
             provider TEXT, series TEXT, observed_at TEXT, value REAL,
             previous REAL, surprise REAL, source_id TEXT, payload TEXT,
             execution_authority INTEGER)"""
    )
    return con


def _rows(con, series, entity, provider, n, distinct_ts, distinct_vals, surprise=0):
    """Insert ``n`` rows spanning exactly ``distinct_ts`` distinct timestamps.

    Timestamps must cycle over a window far larger than ``distinct_ts`` so the
    requested cardinality is genuinely produced rather than truncated.
    """
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1)
    span_days = max(distinct_ts * 3, 90)
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value,surprise)"
        " VALUES(?,?,?,?,?,?)",
        [(entity, provider, series,
          (base + timedelta(days=(i % distinct_ts) * (span_days // distinct_ts),
                            hours=(i // distinct_ts))).isoformat(),
          float(i % distinct_vals), (1.0 if i < surprise else None))
         for i in range(n)],
    )
    con.commit()


# --- classification policy --------------------------------------------------

def test_single_value_series_is_sentinel():
    verdict, reason = _classify(rows=7034, distinct_timestamps=7034,
                                distinct_values=1, distinct_dates=7034,
                                has_surprise=False)
    assert verdict == VERDICT_SENTINEL
    assert "placeholder" in reason


def test_row_count_alone_never_qualifies():
    """6,300 rows that are 18 points duplicated 350x must not read as usable."""
    verdict, reason = _classify(rows=6336, distinct_timestamps=18,
                                distinct_values=15, distinct_dates=18,
                                has_surprise=False)
    assert verdict == VERDICT_LOW_N
    assert f"{6336 // 18}x" in reason
    assert str(MIN_EFFECTIVE_POINTS) in reason


def test_few_points_even_without_duplication_are_rejected():
    verdict, _ = _classify(rows=14, distinct_timestamps=14, distinct_values=14,
                           distinct_dates=14, has_surprise=True)
    assert verdict == VERDICT_LOW_N


def test_genuine_resolution_qualifies():
    verdict, reason = _classify(rows=6885, distinct_timestamps=6876,
                                distinct_values=2213, distinct_dates=23,
                                has_surprise=False)
    assert verdict == VERDICT_USABLE
    assert "independent points" in reason


def test_extreme_inflation_is_flagged_separately():
    verdict, reason = _classify(rows=100000, distinct_timestamps=120,
                                distinct_values=900, distinct_dates=120,
                                has_surprise=False)
    assert verdict == VERDICT_DUPLICATED
    assert "inflation" in reason


# --- audit over a real-shaped table ---------------------------------------

def test_audit_separates_sentinel_from_usable(tmp_path):
    con = _con(tmp_path)
    _rows(con, "sentinel_a", "GROWTH", "NASA", 7000, 7000, 1)
    _rows(con, "real_ticks", "DXY", "MARKET", 5000, 5000, 4000)
    report = audit_series(con)
    verdicts = {s.series: s.verdict for s in report.series}
    assert verdicts["sentinel_a"] == VERDICT_SENTINEL
    assert verdicts["real_ticks"] == VERDICT_USABLE
    assert report.usable_entities == ["DXY"]
    assert all(s.execution_authority is False for s in report.series)


def test_inflation_factor_is_reported(tmp_path):
    """The DGS10 shape: thousands of rows, a handful of real timestamps."""
    con = _con(tmp_path)
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("VIX", "FRED", "dup", f"2026-01-{day:02d}", float(k))
         for day in range(1, 11) for k in range(350)],
    )
    con.commit()
    s = next(x for x in audit_series(con).series if x.series == "dup")
    assert s.rows == 3500
    assert s.distinct_timestamps == 10
    assert s.inflation_factor == 350.0
    assert s.verdict == VERDICT_LOW_N


def test_surprise_presence_is_recorded(tmp_path):
    con = _con(tmp_path)
    _rows(con, "with_surprise", "DXY", "MKT", 3000, 3000, 2000, surprise=10)
    s = next(x for x in audit_series(con).series if x.series == "with_surprise")
    assert s.has_surprise is True


def test_null_valued_series_are_labelled_not_ignored(tmp_path):
    """Rows with no measurement are still rows.

    They must be classified (as NO_VALUE_RECORDED) rather than skipped: leaving
    them UNCLASSIFIED would make the quarantine incomplete and let a consumer
    treat "unlabelled" as "probably fine".
    """
    con = _con(tmp_path)
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("X", "P", "nulls", f"2026-01-{i:02d}", None) for i in range(1, 29)],
    )
    con.commit()
    series = audit_series(con).series
    assert len(series) == 1
    assert series[0].series == "nulls"
    assert series[0].distinct_values == 0
    assert series[0].verdict == "NO_VALUE_RECORDED"
    assert "no value" in series[0].reason


def test_no_value_verdict_is_quarantined():
    from intelligence.observation_provenance import QUARANTINED_VERDICTS, VERDICT_NO_VALUE

    assert VERDICT_NO_VALUE in QUARANTINED_VERDICTS


def test_every_series_ends_up_labelled(tmp_path):
    """No series may remain UNCLASSIFIED after a quarantine run."""
    from intelligence.observation_provenance import (
        PROV_UNCLASSIFIED, quarantine,
    )

    con = _con(tmp_path)
    con.execute(
        "ALTER TABLE macro_observations ADD COLUMN data_provenance TEXT"
        " NOT NULL DEFAULT 'UNCLASSIFIED'"
    )
    # a constant series, a null series, and a genuine one
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("A", "P1", "const", f"2026-01-{(i % 28) + 1:02d}", 101.4) for i in range(60)]
        + [("B", "P2", "noval", f"2026-02-{i:02d}", None) for i in range(1, 29)],
    )
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("C", "P3", "real", f"2026-03-{i % 28 + 1:02d}T{i:02d}:00", float(i))
         for i in range(300)],
    )
    con.commit()

    result = quarantine(con)

    assert PROV_UNCLASSIFIED not in result["labelled"], \
        f"series left unlabelled: {result['labelled']}"
    assert result["labelled"]["SENTINEL_CONSTANT"]["series"] == 1
    assert result["labelled"]["NO_VALUE_RECORDED"]["series"] == 1
    assert result["labelled"]["USABLE_AS_EVIDENCE"]["series"] == 1
    assert result["quarantined_series"] == 2


def test_quarantine_does_not_delete_or_alter_values(tmp_path):
    from intelligence.observation_provenance import quarantine

    con = _con(tmp_path)
    con.execute(
        "ALTER TABLE macro_observations ADD COLUMN data_provenance TEXT"
        " NOT NULL DEFAULT 'UNCLASSIFIED'"
    )
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("A", "P1", "const", f"2026-01-{(i % 28) + 1:02d}", 101.4) for i in range(60)],
    )
    con.commit()
    before = con.execute(
        "SELECT COUNT(*), SUM(value), COUNT(DISTINCT series) FROM macro_observations"
    ).fetchone()

    quarantine(con)

    after = con.execute(
        "SELECT COUNT(*), SUM(value), COUNT(DISTINCT series) FROM macro_observations"
    ).fetchone()
    assert tuple(before) == tuple(after)


def test_quarantine_is_idempotent(tmp_path):
    from intelligence.observation_provenance import quarantine

    con = _con(tmp_path)
    con.execute(
        "ALTER TABLE macro_observations ADD COLUMN data_provenance TEXT"
        " NOT NULL DEFAULT 'UNCLASSIFIED'"
    )
    con.executemany(
        "INSERT INTO macro_observations(entity,provider,series,observed_at,value)"
        " VALUES(?,?,?,?,?)",
        [("A", "P1", "const", f"2026-01-{(i % 28) + 1:02d}", 101.4) for i in range(60)],
    )
    con.commit()
    first = quarantine(con)
    second = quarantine(con)
    assert first["labelled"] == second["labelled"]


def test_evidence_only_sql_excludes_every_quarantined_verdict():
    from intelligence.observation_provenance import (
        QUARANTINED_VERDICTS, evidence_only_args, evidence_only_sql,
    )

    sql = evidence_only_sql("entity='DXY'")
    assert "data_provenance NOT IN" in sql
    assert "entity='DXY'" in sql
    assert sql.count("?") == len(QUARANTINED_VERDICTS)
    assert len(evidence_only_args()) == len(QUARANTINED_VERDICTS)


# --- edge coverage ----------------------------------------------------------

def _edges(con, rows):
    con.execute("CREATE TABLE macro_entities(entity_id INTEGER PRIMARY KEY,"
                " name TEXT, entity_type TEXT)")
    con.execute("CREATE TABLE macro_edges(edge_id INTEGER PRIMARY KEY,"
                " relation TEXT, sign TEXT, status TEXT, source_entity INTEGER,"
                " target_entity INTEGER)")
    ids = {}
    for i, (name, etype) in enumerate(rows, start=1):
        con.execute("INSERT INTO macro_entities VALUES(?,?,?)", (i, name, etype))
        ids[name] = i
    con.commit()
    return ids


def test_coverage_requires_both_endpoints(tmp_path):
    con = _con(tmp_path, "e.db")
    _rows(con, "ticks", "DXY", "MARKET", 5000, 5000, 4000)   # DXY usable
    ids = _edges(con, [
        ("DXY", "CURRENCY"), ("EURUSD", "FX"),                # DXY -> EURUSD
        ("NOPE", "CURRENCY"), ("ALSONOPE", "FX"),             # nothing usable
    ])
    con.execute("INSERT INTO macro_edges VALUES(1,'DEPRESSES','NEGATIVE','CANDIDATE',?,?)",
                (ids["DXY"], ids["EURUSD"]))
    con.execute("INSERT INTO macro_edges VALUES(2,'X','Y','CANDIDATE',?,?)",
                (ids["NOPE"], ids["ALSONOPE"]))
    con.commit()

    cov = edge_evidence_coverage(con)

    assert cov["observation_validatable"] == 0
    assert cov["source_measurable_target_not"] == 1
    assert cov["no_measurable_endpoint"] == 1
    assert cov["verdict"] == "NO_OBSERVATION_EVIDENCE_AVAILABLE"
    assert cov["execution_authority"] is False


def test_coverage_counts_a_genuinely_testable_edge(tmp_path):
    con = _con(tmp_path, "f.db")
    _rows(con, "a", "DXY", "MARKET", 5000, 5000, 4000)
    _rows(con, "b", "VIX", "MARKET", 5000, 5000, 4000)
    ids = _edges(con, [("DXY", "CURRENCY"), ("VIX", "VOLATILITY")])
    con.execute("INSERT INTO macro_edges VALUES(1,'WIDENS','POSITIVE','CANDIDATE',?,?)",
                (ids["DXY"], ids["VIX"]))
    con.commit()

    cov = edge_evidence_coverage(con)

    assert cov["observation_validatable"] == 1
    assert cov["verdict"] == "PARTIAL_COVERAGE"


def test_event_class_edges_are_reported_separately(tmp_path):
    """They are not observation-validatable; they need release reactions."""
    con = _con(tmp_path, "g.db")
    ids = _edges(con, [("CPI_RELEASE", "EVENT_CLASS"), ("DXY", "CURRENCY")])
    con.execute("INSERT INTO macro_edges VALUES(1,'DRIVES','POSITIVE','CANDIDATE',?,?)",
                (ids["CPI_RELEASE"], ids["DXY"]))
    con.commit()

    cov = edge_evidence_coverage(con)

    assert cov["event_class_edges"] == 1
    assert cov["observation_validatable"] == 0
