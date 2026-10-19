"""New observation rows must be labelled at write time (2026-10-16 regression).

``observation_provenance.quarantine`` stamps ``data_provenance`` by batch audit,
but the three ingest paths left the column at its ``UNCLASSIFIED`` default and
waited for the next audit to catch up.  ``QUARANTINED_VERDICTS`` is a deny-list,
so an unlabelled row is *eligible* as statistical evidence -- meaning "not
checked yet" behaved as "known good".

Measured consequence: 1,082 rows across 67 series were written after the last
quarantine and still carried ``UNCLASSIFIED``, including 20 scale-corrupted
``realtime_fx`` DXY observations at ~50 while the usable ``DX-Y.NYB`` series
reads ~101 on the same dates. Those corrupt rows passed straight through
``recent()`` and into ``AutonomousCausalValidator._validate_from_observations``.

``realtime_fx`` is provably corrupt from its NAME (it mixes EURUSD and
DX-Y.NYB at two different scales), so that verdict needs no row statistics and
can be applied on the very first write.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligence.macro_observations import (  # noqa: E402
    ingest_alternative_snapshot,
    ingest_calendar_events,
    ingest_fred_snapshot,
)
from intelligence.observation_provenance import (  # noqa: E402
    PROV_UNCLASSIFIED,
    QUARANTINED_VERDICTS,
    VERDICT_SCALE_CORRUPTED,
    VERDICT_USABLE,
    provenance_for_series,
)


def _provenance(db: Path, where: str = "", args: tuple = ()) -> list:
    con = sqlite3.connect(str(db))
    try:
        sql = "SELECT data_provenance FROM macro_observations"
        if where:
            sql += " WHERE " + where
        return [r[0] for r in con.execute(sql, args)]
    finally:
        con.close()


# --- the policy itself -------------------------------------------------

def test_series_name_verdict_is_independent_of_row_statistics():
    """A corrupt series is corrupt on its first row, not after enough samples."""
    assert provenance_for_series("realtime_fx") == VERDICT_SCALE_CORRUPTED


def test_unknown_series_stays_unclassified_not_guessed():
    """The helper must not invent a verdict; 'not yet known' stays honest."""
    for s in ("DGS10", "DX-Y.NYB", "", "some_future_series"):
        assert provenance_for_series(s) == PROV_UNCLASSIFIED, s


def test_batch_audit_and_write_time_agree():
    """One source of truth: _classify and provenance_for_series cannot diverge."""
    from intelligence.observation_provenance import _classify

    audited = _classify(series="realtime_fx", rows=1, distinct_timestamps=1,
                        distinct_values=2, distinct_dates=1, has_surprise=False)[0]
    assert audited == provenance_for_series("realtime_fx") == VERDICT_SCALE_CORRUPTED


# --- the leak ----------------------------------------------------------

def test_alternative_ingest_labels_corrupt_series_on_write(tmp_path):
    """realtime_fx rows must be quarantined the instant they are written."""
    db = tmp_path / "obs.db"
    rows = [
        {"entity": "DXY", "provider": "MARKET", "source_id": "realtime_fx",
         "value": 51.4, "observed_at": "2026-10-08T00:00:00+00:00"},
        {"entity": "FUTURES_LIQUIDITY", "provider": "MARKET",
         "source_id": "fed_sofr_futures", "value": 1.0,
         "observed_at": "2026-10-08T00:00:00+00:00"},
    ]

    result = ingest_alternative_snapshot(str(db), rows)
    assert result["inserted"] == 2

    assert _provenance(db, "series='realtime_fx'") == [VERDICT_SCALE_CORRUPTED]
    assert _provenance(db, "series='fed_sofr_futures'") == [PROV_UNCLASSIFIED]


def test_corrupt_row_never_becomes_eligible_evidence(tmp_path):
    """The consumer's deny-list must now exclude the corrupt row.

    This is the property that was broken: UNCLASSIFIED is not in
    QUARANTINED_VERDICTS, so an unlabelled corrupt row was served.
    """
    db = tmp_path / "obs.db"
    ingest_alternative_snapshot(str(db), [
        {"entity": "DXY", "provider": "MARKET", "source_id": "realtime_fx",
         "value": 51.4, "observed_at": "2026-10-08T00:00:00+00:00"},
    ])
    ingest_alternative_snapshot(str(db), [
        {"entity": "CPI", "provider": "MARKET", "source_id": "fed_sofr_futures",
         "value": 2.1, "observed_at": "2026-10-08T00:00:00+00:00"},
    ])

    marks = ",".join("?" * len(QUARANTINED_VERDICTS))
    con = sqlite3.connect(str(db))
    try:
        served = con.execute(
            f"SELECT series FROM macro_observations "
            f"WHERE data_provenance NOT IN ({marks})", QUARANTINED_VERDICTS
        ).fetchall()
    finally:
        con.close()

    served_series = {r[0] for r in served}
    assert "realtime_fx" not in served_series, (
        "scale-corrupted DXY series reached the evidence consumer")
    assert "fed_sofr_futures" in served_series, (
        "an unlabelled but unproven series must still be servable")


def test_fred_and_calendar_ingest_label_too(tmp_path):
    """All three ingest paths stamp, not just the alternative-nowcast one."""
    db = tmp_path / "obs.db"

    ingest_fred_snapshot(str(db), {
        "DGS10": {"value": 4.2, "previous": 4.1, "observed_at": "2026-10-08"},
    })
    ingest_calendar_events(str(db), [
        {"currency": "USD", "event": "CPI m/m", "actual": 0.2, "forecast": 0.1},
    ])

    stamped = _provenance(db)
    assert len(stamped) == 2
    assert all(v == PROV_UNCLASSIFIED for v in stamped), (
        "a series with no name-level defect stays UNCLASSIFIED for the audit")


def test_verdict_matching_is_exact_not_substring(tmp_path):
    """A name that merely CONTAINS a corrupt series must not be quarantined.

    The lookup is exact because series identity is the key everywhere else. A
    substring match would quarantine a calendar event whose headline happens to
    mention realtime_fx, which is a real observation, not a corrupt feed.
    """
    db = tmp_path / "obs.db"
    ingest_calendar_events(str(db), [
        {"currency": "USD", "event": "realtime_fx mention in headline", "actual": 51.0},
    ])
    assert _provenance(db) == [PROV_UNCLASSIFIED]

    # ...but the series itself, on any ingest path, is stamped.
    ingest_alternative_snapshot(str(db), [
        {"entity": "DXY", "provider": "MARKET", "source_id": "realtime_fx", "value": 51.0},
    ])
    assert _provenance(db, "series='realtime_fx'") == [VERDICT_SCALE_CORRUPTED]


def test_ingest_survives_a_broken_provenance_module(tmp_path, monkeypatch):
    """Labelling must never block ingestion (fail-safe, not fail-closed)."""
    import intelligence.observation_provenance as op

    def _boom(_series):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(op, "provenance_for_series", _boom)
    db = tmp_path / "obs.db"

    result = ingest_alternative_snapshot(str(db), [
        {"entity": "DXY", "provider": "MARKET", "source_id": "realtime_fx", "value": 51.0},
    ])

    assert result["inserted"] == 1, "a classifier error must not lose the observation"
    assert _provenance(db) == [PROV_UNCLASSIFIED]