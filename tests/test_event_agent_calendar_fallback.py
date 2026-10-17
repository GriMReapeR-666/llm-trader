"""EventAgent triage with the modern FF calendar shape (audit P0-4).

Calendar rows whose timestamp lives only in ``date`` (a full ISO offset) and
whose impact is spelled "High"/"Medium" must still gate; only HIGH rows open a
blackout window. The timestamp precedence matters as much as the shape: a bare
clock in ``time`` does not parse, and a bare date parses as midnight *local*
time, which silently slid every window by the host's UTC offset.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from operations.agents.event_agent import (  # noqa: E402
    BLACKOUT_AFTER_MIN, BLACKOUT_BEFORE_MIN, EventWindowManager,
)


def _mgr(tmp_path) -> EventWindowManager:
    return EventWindowManager(state_file=str(tmp_path / "evt.json"))


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class TestModernShapeTriage:
    def test_date_only_high_row_opens_blackout(self, tmp_path):
        mgr = _mgr(tmp_path)
        rows = [{"title": "FOMC Statement", "currency": "USD",
                 "date": _iso(hours=1), "impact": "High"}]
        result = mgr.triage(rows)
        assert result["added"] == 1
        assert result["active_windows"] >= 1

    def test_offset_date_is_read_as_that_instant(self, tmp_path):
        """A -04:00 timestamp is 4 hours later in UTC, not the same wall clock."""
        mgr = _mgr(tmp_path)
        when = datetime.now(timezone.utc) + timedelta(hours=2)
        rows = [{"title": "CPI", "currency": "USD", "impact": "High",
                 "date": when.astimezone(timezone(timedelta(hours=-4))).isoformat()}]
        mgr.triage(rows)
        assert mgr._windows[0]["start"] == pytest.approx(when.timestamp(), abs=1)

    def test_datetime_utc_wins_over_a_bare_date(self, tmp_path):
        """datetime_utc is the provider's normalized instant and must win.

        A bare date falls through to a naive midnight-local parse, which on this
        host is a 5.5-hour error.
        """
        mgr = _mgr(tmp_path)
        when = datetime.now(timezone.utc) + timedelta(hours=3)
        rows = [{"title": "NFP", "currency": "USD", "impact": "High",
                 "date": "2026-01-01",
                 "datetime_utc": when.isoformat()}]
        mgr.triage(rows)
        assert mgr._windows[0]["start"] == pytest.approx(when.timestamp(), abs=1)

    def test_legacy_datetime_utc_field_fallback(self, tmp_path):
        mgr = _mgr(tmp_path)
        rows = [{"title": "CPI", "currency": "USD",
                 "datetime_utc": _iso(hours=2), "impact": "High"}]
        assert mgr.triage(rows)["added"] == 1

    def test_bare_clock_in_time_field_does_not_gate(self, tmp_path):
        """A bare clock carries no date; guessing one would gate the wrong day.

        The provider now folds date+time into datetime_utc before this point, so
        reaching this branch means the row really is unusable.
        """
        mgr = _mgr(tmp_path)
        rows = [{"title": "Retail Sales", "currency": "USD",
                 "date": "2026-01-01", "time": "8:30am", "impact": "High"}]
        assert mgr.triage(rows)["added"] == 0

    def test_full_timestamp_in_time_field_still_gates(self, tmp_path):
        mgr = _mgr(tmp_path)
        rows = [{"title": "NFP", "currency": "USD", "date": "2026-01-01",
                 "time": _iso(hours=1), "impact": "high"}]
        assert mgr.triage(rows)["added"] == 1


class TestImpactContract:
    def test_medium_row_does_not_journal_or_gate(self, tmp_path):
        """Contract preserved: only HIGH rows journal."""
        mgr = _mgr(tmp_path)
        rows = [{"title": "PMI", "currency": "EUR",
                 "date": _iso(hours=3), "impact": "Medium"}]
        result = mgr.triage(rows)
        assert result["added"] == 0
        assert result["active_windows"] == 0

    @pytest.mark.parametrize("impact", ["high", "High", "HIGH", "3", "red", "Red"])
    def test_every_high_spelling_gates(self, tmp_path, impact):
        assert _mgr(tmp_path).triage(
            [{"title": "FOMC", "currency": "USD",
              "date": _iso(hours=1), "impact": impact}])["added"] == 1

    @pytest.mark.parametrize("impact", ["med", "Medium", "1", "low", "holiday", ""])
    def test_non_high_spellings_never_gate(self, tmp_path, impact):
        assert _mgr(tmp_path).triage(
            [{"title": "PMI", "currency": "EUR",
              "date": _iso(hours=1), "impact": impact}])["added"] == 0

    def test_window_records_currency_and_impact(self, tmp_path):
        mgr = _mgr(tmp_path)
        mgr.triage([{"title": "FOMC", "currency": "USD",
                     "date": _iso(hours=1), "impact": "High"}])
        window = mgr.active()[0]
        assert window["currency"] == "USD"
        assert window["impact"] == "High"


class TestWindowGeometry:
    def test_window_spans_before_to_after_the_event(self, tmp_path):
        mgr = _mgr(tmp_path)
        when = datetime.now(timezone.utc) + timedelta(hours=2)
        mgr.triage([{"title": "FOMC", "currency": "USD",
                     "date": when.isoformat(), "impact": "High"}])
        window = mgr._windows[0]
        assert window["blackout_from"] == pytest.approx(
            when.timestamp() - BLACKOUT_BEFORE_MIN * 60, abs=1)
        assert window["blackout_to"] == pytest.approx(
            when.timestamp() + BLACKOUT_AFTER_MIN * 60, abs=1)

    def test_window_is_inactive_until_the_pre_period_opens(self, tmp_path):
        """A +2h event is not gating yet; its window opens 90 minutes out."""
        mgr = _mgr(tmp_path)
        mgr.triage([{"title": "FOMC", "currency": "USD",
                     "date": _iso(hours=2), "impact": "High"}])
        assert mgr.is_blackout()[0] is False
        assert mgr.is_blackout(now=time.time() + (2 - 1.5) * 3600)[0] is True

    def test_event_inside_the_tail_is_still_gated(self, tmp_path):
        """An event 20 minutes old has not left its +30m blackout yet."""
        mgr = _mgr(tmp_path)
        rows = [{"title": "FOMC", "currency": "USD",
                 "date": _iso(minutes=-20), "impact": "High"}]
        assert mgr.triage(rows)["added"] == 1
        assert mgr.is_blackout()[0] is True

    def test_event_past_the_tail_is_skipped(self, tmp_path):
        mgr = _mgr(tmp_path)
        rows = [{"title": "old", "currency": "USD",
                 "date": _iso(hours=-2), "impact": "High"}]
        assert mgr.triage(rows)["added"] == 0
        assert mgr.active() == []

    def test_repeated_triage_does_not_duplicate_windows(self, tmp_path):
        mgr = _mgr(tmp_path)
        rows = [{"title": "FOMC", "currency": "USD",
                 "date": _iso(hours=1), "impact": "High"}]
        assert mgr.triage(rows)["added"] == 1
        assert mgr.triage(rows)["added"] == 0


class TestWindowExpiry:
    def test_expired_windows_are_dropped_and_never_come_back(self, tmp_path):
        mgr = _mgr(tmp_path)
        mgr.triage([{"title": "FOMC", "currency": "USD",
                     "date": _iso(hours=1), "impact": "High"}])
        assert len(mgr.active()) == 1

        # 30 hours on: past both the blackout end and the 24h retention cutoff.
        later = time.time() + 30 * 3600
        mgr.triage([], now=later)
        assert mgr.active(later) == []
        assert mgr._windows == []

    def test_persisted_state_stays_bounded(self, tmp_path):
        """_persist_locked keeps only the newest 200 windows on disk."""
        import json

        mgr = _mgr(tmp_path)
        rows = [{"title": f"E{i}", "currency": "USD",
                 "date": _iso(minutes=i), "impact": "High"} for i in range(1, 300)]
        mgr.triage(rows)
        persisted = json.loads(open(mgr.state_file, encoding="utf-8").read())
        assert len(persisted["windows"]) == 200
