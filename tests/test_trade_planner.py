"""Tests for the dynamic trade planner (core/trade_planner.py).

Doctrine under test:
- The plan is a reduction-only overlay on deterministic sizing (can scale to
  zero, never enlarge; hard breakers dominate).
- The ladder is continuous and equity-unbounded — identical curve at any
  account size; no fixed dollar rungs anywhere.
- Refuses fire in the manipulation/slippage window (spike blackout) and the
  pre-news warning; entries re-open at a computable "execute_from" stamp.
- Shortest-diff safety: with mode=off/unattached, sizing is byte-identical
  to the legacy path.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.risk_manager import RiskManager
from core.trade_planner import TradePlanEngine, journal_events_provider

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)  # Thursday 14:00 UTC


def _state(tmp_path, name="planner_risk.json"):
    return str(tmp_path / name)


def _mk_engine(events, mode="enforce", state=None):
    return TradePlanEngine(events_provider=(lambda: (events, state or "OK")) if events is not None else None,
                           mode=mode, now_fn=lambda: NOW)


def _clean_rm(tmp_path, balance=1000.0):
    rm = RiskManager(balance=balance, risk_per_trade=0.01, state_file=_state(tmp_path))
    return rm


@pytest.fixture(autouse=True)
def _scoped_planner_mode(monkeypatch):
    """Ambient TRADE_PLANNER_MODE must not leak into planner-mode assertions."""
    monkeypatch.delenv("TRADE_PLANNER_MODE", raising=False)


# ---------------------------------------------------------------------------
# Growth ladder: continuous, equity-unbounded, monotone
# ---------------------------------------------------------------------------

class TestGrowthLadder:
    def test_no_lock_to_any_dollar_amount(self):
        # Identical curve at $100, $1,000 and $10M for the same growth ratio.
        for base in (100.0, 1000.0, 10_000_000.0):
            assert TradePlanEngine.growth_scale(base * 2, base) == pytest.approx(0.9699, abs=1e-3)

    def test_flat_when_no_growth(self):
        assert TradePlanEngine.growth_scale(1000.0, 1000.0) == 1.0
        assert TradePlanEngine.growth_scale(0, 0) == 1.0

    def test_monotone_in_growth_and_floored(self):
        prev = 1.0
        ratio = 1.0
        while ratio < 1000.0:
            ratio *= 2
            s = TradePlanEngine.growth_scale(ratio, 1.0)
            assert s <= prev + 1e-9
            assert 0.60 <= s <= 1.0
            prev = s

    def test_never_amplifies_on_contraction(self):
        # Account shrank below the anchor: still capped at 1.0 (no leverage-up).
        assert TradePlanEngine.growth_scale(500.0, 1000.0) == 1.0


# ---------------------------------------------------------------------------
# Session / timing / guard bands
# ---------------------------------------------------------------------------

class TestTiming:
    def test_main_session_neutral(self):
        eng = _mk_engine(None)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, peak_equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "PROCEED"
        assert plan.risk_scale == 1.0

    def test_rollover_refuse_is_a_veto(self):
        eng = _mk_engine(None)
        late = NOW.replace(hour=22, minute=30)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, now=late)
        assert plan.decision == "VETO"
        assert "rollover_spread_window" in plan.reasons

    def test_late_friday_defense(self):
        eng = _mk_engine(None)
        friday_evening = datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, now=friday_evening)
        assert plan.decision == "VETO"
        assert "late_friday_gap_defense" in plan.reasons

    def test_jppy_session_shifts(self):
        eng = _mk_engine(None)
        asia_morning = NOW.replace(hour=1)
        plan = eng.plan("USDJPY", 150.0, 149.8, equity=1000.0, anchor_equity=1000.0, now=asia_morning)
        assert plan.decision == "PROCEED"
        assert plan.parts["session"] == pytest.approx(0.75)
        # But a non-Asia pair gets the wider low-liquidity taper at the same hour.
        plan_eu = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, anchor_equity=1000.0, now=asia_morning)
        assert plan_eu.parts["session"] == pytest.approx(0.5)

    def test_spread_spike_guard(self):
        eng = _mk_engine(None)
        plan = eng.plan("EURUSD", 1.1, 1.099,
                        market={"spread": 30.0, "normal_spread": 10.0},
                        equity=1000.0, now=NOW)
        assert plan.decision == "VETO"
        assert "spread_spike_window" in plan.reasons

    def test_adx_chop_tapers_but_does_not_veto(self):
        eng = _mk_engine(None)
        plan = eng.plan("EURUSD", 1.1, 1.099, market={"adx": 12.0},
                        equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "PROCEED"
        assert plan.risk_scale == pytest.approx(0.5)

    def test_fusion_directive_honored(self):
        eng = _mk_engine(None)
        plan = eng.plan("EURUSD", 1.1, 1.099, market={"fusion_directive": "HALF_RISK"},
                        equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.risk_scale == pytest.approx(0.5)
        plan = eng.plan("EURUSD", 1.1, 1.099, market={"fusion_directive": "REJECT"},
                        equity=1000.0, now=NOW)
        assert plan.decision == "VETO"


# ---------------------------------------------------------------------------
# Event calendar phases (macro-fundamental timing)
# ---------------------------------------------------------------------------

class TestEventPhases:
    def test_spike_blackout_refuses_with_execute_from(self):
        # Tier 2 event in 10 minutes → hard blackout (before=30 for tier 2).
        events = [{"time": NOW + timedelta(minutes=10), "currency": "USD",
                   "title": "RETAIL SALES", "importance": "HIGH"}]
        eng = _mk_engine(events)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "VETO"
        assert plan.event_phase == "SPIKE_BLACKOUT"
        assert plan.entry_execute_from is not None
        stamp = datetime.fromisoformat(plan.entry_execute_from)
        assert stamp >= NOW + timedelta(minutes=10)  # after the release...

        # ... and matches the gate's own post-blackout re-open window.
        gate_after = eng._blackout_after(type("G", (), {"tier": 2, "nearest_event": events[0], "minutes_to_event": 10.0})(), "EURUSD")
        assert stamp.isoformat() == (events[0]["time"] + timedelta(minutes=gate_after)).isoformat()

    def test_blackout_ends_after_spike_window(self):
        # Release was 22 min ago; tier-2 after=15 → re-opened entry zone.
        events = [{"time": NOW - timedelta(minutes=22), "currency": "USD",
                   "title": "RETAIL SALES", "importance": "HIGH"}]
        eng = _mk_engine(events)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "PROCEED"
        assert plan.event_phase == "POST_NEWS_ENTRY"
        assert "post_news_pullback_zone" in plan.reasons

    def test_irrelevant_event_ignored(self):
        # GBP event never touches EURUSD sizing.
        events = [{"time": NOW + timedelta(minutes=5), "currency": "GBP",
                   "title": "RETAIL SALES", "importance": "HIGH"}]
        eng = _mk_engine(events)
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "PROCEED"

    def test_stale_calendar_neutral_not_amplifying(self):
        eng = _mk_engine([], state="STALE")
        plan = eng.plan("EURUSD", 1.1, 1.099, equity=1000.0, anchor_equity=1000.0, now=NOW)
        assert plan.decision == "PROCEED"
        assert plan.calendar_state == "STALE"
        assert plan.risk_scale == 1.0


# ---------------------------------------------------------------------------
# Journal calendar provider
# ---------------------------------------------------------------------------

def _mk_calendar_db(path, rows):
    con = sqlite3.connect(str(path))
    con.execute("create table macro_events (id integer primary key autoincrement, "
                "event_time text, currency text, title text, importance text, "
                "payload text, captured_at text)")
    for r in rows:
        con.execute("insert into macro_events (event_time, currency, title, importance, payload, captured_at) "
                    "values (?,?,?,?,?,?)", r)
    con.commit()
    con.close()


class TestJournalEventsProvider:
    def test_fresh_feed_reports_ok_rows(self, tmp_path):
        real_now = datetime.now(timezone.utc)
        db = tmp_path / "journal.db"
        _mk_calendar_db(db, [
            ((real_now + timedelta(hours=30)).isoformat(), "USD", "RETAIL SALES", "MED", "{}",
             (real_now - timedelta(hours=1)).isoformat()),
            # Distant row beyond the 7-day horizon must be dropped
            ((real_now + timedelta(days=30)).isoformat(), "USD", "GDP", "HIGH", "{}",
             (real_now - timedelta(hours=1)).isoformat()),
        ])
        provider, state = journal_events_provider(str(db))
        events, state2 = provider()
        assert state2 == "OK"
        assert len(events) == 1
        assert events[0]["title"] == "RETAIL SALES"

    def test_dead_feed_reports_stale(self, tmp_path):
        real_now = datetime.now(timezone.utc)
        db = tmp_path / "journal_dead.db"
        _mk_calendar_db(db, [
            ((real_now + timedelta(hours=26)).isoformat(), "USD", "RETAIL SALES", "MED", "{}",
             (real_now - timedelta(days=5)).isoformat()),
        ])
        provider, state = journal_events_provider(str(db))
        events, state2 = provider()
        assert state2 == "STALE"
        assert events  # rows still serve for phase math; only trust is downgraded

    def test_missing_table_fails_to_stale(self, tmp_path):
        db = tmp_path / "journal_empty.db"
        sqlite3.connect(str(db)).close()
        provider, state = journal_events_provider(str(db))
        # No macro_events table → provider must raise → engine downgrades STALE.
        with pytest.raises(sqlite3.OperationalError):
            provider()


# ---------------------------------------------------------------------------
# End-to-end: RiskManager integration (reduction-only wiring)
# ---------------------------------------------------------------------------

class TestRiskManagerIntegration:
    def test_off_by_default_is_byte_identical(self, tmp_path):
        rm = _clean_rm(tmp_path)
        assert rm.trade_plan_engine is None
        vol = rm.calc_position_size("EURUSD", 1.1000, 1.0990)
        rm2 = _clean_rm(tmp_path, balance=1000.0)  # same balance → identical math
        vol2 = rm2.calc_position_size("EURUSD", 1.1000, 1.0990)
        assert vol == vol2 == pytest.approx(0.10)

    def test_observe_never_changes_size(self, tmp_path):
        rm = _clean_rm(tmp_path)
        events = [{"time": NOW + timedelta(minutes=8), "currency": "USD",
                   "title": "FOMC STATEMENT", "importance": "HIGH"}]
        rm.trade_plan_engine = TradePlanEngine(events_provider=lambda: (events, "OK"),
                                               mode="observe", now_fn=lambda: NOW)
        vol_with_planner = rm.calc_position_size("EURUSD", 1.1000, 1.0990, market={})
        rm2 = _clean_rm(tmp_path)
        vol_legacy = rm2.calc_position_size("EURUSD", 1.1000, 1.0990)
        # Would-veto recorded honestly, but the size is untouched (log-only).
        assert rm.last_trade_plan is not None
        assert rm.last_trade_plan.decision == "HOLD"
        assert vol_with_planner == vol_legacy

    def test_enforce_veto_returns_zero_volume(self, tmp_path):
        rm = _clean_rm(tmp_path)
        events = [{"time": NOW + timedelta(minutes=8), "currency": "USD",
                   "title": "FOMC STATEMENT", "importance": "HIGH"}]
        rm.trade_plan_engine = TradePlanEngine(events_provider=lambda: (events, "OK"),
                                               mode="enforce", now_fn=lambda: NOW)
        vol = rm.calc_position_size("EURUSD", 1.1000, 1.0990, market={})
        assert vol == 0.0
        assert "news_spike_blackout" in rm.last_trade_plan.reasons

    def test_enforce_applies_taper_scale(self, tmp_path):
        rm = _clean_rm(tmp_path)
        rm.trade_plan_engine = TradePlanEngine(events_provider=None,
                                               mode="enforce", now_fn=lambda: NOW)
        # Dead chop market at 0.5 scale, main session, no growth taper.
        vol = rm.calc_position_size("EURUSD", 1.1000, 1.0990,
                                    market={"adx": 12.0})
        # Legacy base: $10 risk / $100 per lot = 0.10 lots; halved → 0.05.
        assert vol == pytest.approx(0.05)
        assert rm.last_trade_plan.risk_scale == pytest.approx(0.5)

    def test_planner_fault_degrades_to_legacy(self, tmp_path):
        rm = _clean_rm(tmp_path)
        def boom():
            raise RuntimeError("calendar exploded")
        rm.trade_plan_engine = TradePlanEngine(events_provider=boom, mode="enforce",
                                               now_fn=lambda: NOW)
        vol = rm.calc_position_size("EURUSD", 1.1000, 1.0990)
        assert vol == pytest.approx(0.10)

    def test_attach_idempotent_and_journal_backed(self, tmp_path):
        real_now = datetime.now(timezone.utc)
        db = tmp_path / "journal_ok.db"
        _mk_calendar_db(db, [
            ((real_now + timedelta(hours=48)).isoformat(), "USD", "RETAIL SALES", "MED", "{}",
             (real_now - timedelta(hours=1)).isoformat()),
        ])
        rm = _clean_rm(tmp_path)
        class _Store:  # minimal duck-type
            path = str(db)
        rm.store = _Store()
        engine = rm.attach_trade_plan_engine()
        assert rm.attach_trade_plan_engine() is engine
        # Synthetic weekday time for phase logic; provider freshness still uses real clock stamps.
        plan = engine.plan("EURUSD", 1.1, 1.099, equity=1000.0, now=NOW)
        assert plan.calendar_state == "OK"      # 1h-old capture = live feed
        assert plan.decision == "PROCEED"       # 48h-forward event is irrelevant

    def test_hard_breakers_dominate_planner(self, tmp_path):
        rm = _clean_rm(tmp_path)
        rm.trade_plan_engine = _mk_engine(None)
        # Trip the consecutive-loss breaker — planner can never re-open the door.
        rm.consecutive_losses = rm.max_consecutive_losses
        vol = rm.calc_position_size("EURUSD", 1.1000, 1.0990)
        assert vol == 0.0
