"""Dynamic trade planning engine — equity-unbounded contextual sizing ladder.

Replaces the fixed-$100 gold-only ladder with a continuous, context-aware plan
computed per proposed trade. The plan is **reduction-only**: it can scale risk
*down* and blacklist entries (HOLD/VETO); it can never enlarge beyond the
validator-approved risk. All hard breakers in ``RiskManager`` dominate.

The engine composes, in order:

1. **Growth ladder** (continuous, not bounded to $100): percent-risk exponentially
   tapers with the log-ratio of peak equity to a durable anchor, so dollar risk
   keeps compounding with equity while the account's percent exposure tapers as
   the account ages. At any size ($1 .. $10M) the curve is the same — no fixed
   dollar rungs.
2. **Event calendar** (macro-fundamental timing): reuses
   :class:`core.news_trading.NewsTradingGate` phases — ``PRE_NEWS`` → HOLD,
   ``SPIKE_BLACKOUT`` → VETO (this is the manipulation/slippage window both
   before and after the release), ``POST_NEWS_ENTRY`` → allowed (institutional
   pullback zone), ``CLEAR``/``TREND_CONTINUATION`` → neutral.
3. **Session timing** (technical liquidity): per symbol-family session windows;
   dead rollover hours and late-Friday are refuses (weekend gap defense).
4. **Spread/slippage guard**: entries refused when live spread > 2.5x the
   symbol's normal spread hint (spike-proxy when the provider surfaces it).
5. **Macro-fusion directive passthrough**: when the caller surfaces the macro
   fusion gate verdict (``market["fusion_directive"]``) the plan honors
   REJECT/POSTPONE (refuse) and HALF_RISK (0.5x); STRONG_PROCEED stays neutral.

Modes (env ``TRADE_PLANNER_MODE``): ``off`` (bypass entirely), ``observe``
(default — compute and log the plan, change nothing), ``enforce`` (apply the
scale and vetoes). A planner fault degrades to a neutral pass-through and
never disables the upstream gate stack.

Stateless beyond a small memoized calendar read; the journal provider reads
``macro_events`` from the store path with a 60-second TTL and marks the
calendar ``STALE``/``UNKNOWN`` (neutral, taper-only) instead of trusting a
dead feed as evidence of calm.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.news_trading import NewsTradingGate

logger = logging.getLogger("RiskManager")

MODES = ("off", "observe", "enforce")
DEFAULT_MODE = "observe"
_ENV_KEY = "TRADE_PLANNER_MODE"

# Spread-widening ratio that reads like a spike/manipulation window.
_SPREAD_SPIKE_RATIO = 2.5
# Chop filter: below this ADX the trend-phase edge is absent (taper, not veto).
_ADX_MIN = 18.0
# Growth-ladder taper strength: 1 decade of equity growth ⇒ 10% risk taper.
_GROWTH_TAPER = 0.10
_GROWTH_FLOOR = 0.60
_GROWTH_CAP = 1.0
# Calendar read TTL (seconds) and staleness bar for mirror feeds.
_CALENDAR_TTL_S = 60.0
_CALENDAR_STALE_S = 36.0 * 3600.0

# Session windows by symbol family (UTC hours). Per-hour risk scale; the
# ``None`` slots are refuses (dead rollover / closed market).
_DEFAULT_HOURS = {**{h: 1.0 for h in range(7, 21)}, 21: 0.5,
                  4: 0.75, 5: 0.75, 6: 0.75, 2: 0.5, 3: 0.5,
                  22: None, 23: None, 0: None, 1: 0.5}
_ASIA_HOURS = {**_DEFAULT_HOURS, 0: 0.75, 1: 0.75, 2: 0.75, 3: 0.75, 23: 0.5}
_RANGE_HOURS = _DEFAULT_HOURS

_SESSION_TABLES = {
    "asia": _ASIA_HOURS,
    "range24": _RANGE_HOURS,
}


def _classify_symbol(symbol: str) -> str:
    """Cheap symbol-family classification for session timing."""
    s = symbol.upper()
    if any(g in s for g in ("BTC", "ETH", "USDT", "CRYPTO", "XRP", "DOGE")):
        return "range24"          # 24/5-7 continuous markets
    if "JPY" in s:
        return "asia"
    # FX majors, metals, indices and everything else: London+NY relevance.
    return "default"


def _friday_defense(now: datetime) -> Optional[str]:
    """Return a refusal reason when late-Friday holds are prudent."""
    if now.weekday() == 4 and now.hour >= 20:
        return "late_friday_gap_defense"
    if now.weekday() in (5, 6):
        return "weekend_market_closed"
    return None


def _rollover_refuse(now: datetime) -> Optional[str]:
    """Roll-over/trading-day edge when spreads widen and fills degrade."""
    if now.hour in (22, 23):
        return "rollover_spread_window"
    if now.hour == 0 and now.minute < 45:
        return "rollover_spread_window"
    return None


@dataclass(frozen=True)
class TradePlan:
    """Immutable output of one planning evaluation."""

    decision: str                        # PROCEED | HOLD | VETO
    risk_scale: float                    # multiplicative scale in (0, 1]
    reasons: Tuple[str, ...] = ()
    event_phase: str = "CLEAR"
    minutes_to_event: Optional[float] = None
    minutes_since_event: Optional[float] = None
    entry_execute_from: Optional[str] = None   # earliest re-evaluation stamp
    nearest_event_title: Optional[str] = None
    calendar_state: str = "UNKNOWN"      # OK | STALE | UNKNOWN
    mode: str = DEFAULT_MODE
    parts: Dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        parts = ", ".join(f"{k}={v:.2f}" for k, v in sorted(self.parts.items()))
        return (f"TradePlan({self.decision}, scale={self.risk_scale:.2f}, phase={self.event_phase}, "
                f"calendar={self.calendar_state}{', ' + parts if parts else ''}"
                f"{', reasons=' + '|'.join(self.reasons) if self.reasons else ''})")


class TradePlanEngine:
    """Context-aware, equity-unbounded sizing ladder (see module docstring)."""

    def __init__(self,
                 events_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 mode: Optional[str] = None,
                 calendar_state: str = "UNKNOWN",
                 now_fn: Optional[Callable[[], datetime]] = None):
        if mode is None:
            mode = os.getenv(_ENV_KEY, "").strip().lower() or DEFAULT_MODE
        if mode not in MODES:
            mode = DEFAULT_MODE
        self.mode = mode
        self._events_provider = events_provider
        self._calendar_state = calendar_state
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._gate = NewsTradingGate()
        self._memo_lock = threading.Lock()
        self._memo: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

    # ------------------------------------------------------------------
    # Calendar plumbing
    # ------------------------------------------------------------------
    def _events(self, now: datetime) -> Tuple[List[Dict[str, Any]], str]:
        """Events with stamp-staleness classification (never network I/O)."""
        if self._events_provider is None:
            return [], "UNKNOWN"
        cache_key = "events"
        with self._memo_lock:
            cached = self._memo.get(cache_key)
            if cached and (now.timestamp() - cached[0]) < _CALENDAR_TTL_S:
                return cached[1], cached[2] if len(cached) > 2 else "OK" if cached[1] else "STALE"
        try:
            events, state = self._events_provider() or ([], "OK")
            events = list(events or [])
            state = state or ("OK" if events else "STALE")
        except Exception as exc:                      # failing feed ⇒ neutral taper
            logger.debug("TradePlanEngine events provider failed: %s", exc)
            events, state = [], "STALE"
        with self._memo_lock:
            self._memo[cache_key] = (now.timestamp(), events, state)
        return events, state

    # ------------------------------------------------------------------
    # Dynamic growth ladder (the fixed-$100 rungs, generalized)
    # ------------------------------------------------------------------
    @staticmethod
    def growth_scale(peak_equity: float, anchor_equity: float) -> float:
        """Continuous ladder: percent-risk exponentially tapered by growth decades.

        Anchor = durable smallest known equity. Accounts of any size get the
        identical curve: $100 → $8,300 (2 decades) tapers 20% percent-risk
        while dollars-at-risk compound ~80x. Only *genuine* growth engages
        the taper; a foreign-scale mis-configuration cannot create one because
        the T-1 anchor check clamps the ratio at the documented scale limit.
        """
        if not (peak_equity > 0 and anchor_equity > 0):
            return _GROWTH_CAP
        ratio = max(1.0, float(peak_equity) / float(anchor_equity))
        decades = math.log10(ratio)
        return max(_GROWTH_FLOOR, min(_GROWTH_CAP, _GROWTH_CAP - _GROWTH_TAPER * decades))

    # ------------------------------------------------------------------
    # Session / timing components
    # ------------------------------------------------------------------
    def session_scale(self, symbol: str, now: datetime) -> Tuple[float, Tuple[str, ...]]:
        reasons: List[str] = []
        friday = _friday_defense(now)
        if friday:
            reasons.append(friday)
            return 0.0, tuple(reasons)
        rollover = _rollover_refuse(now)
        if rollover:
            reasons.append(rollover)
            return 0.0, tuple(reasons)
        table = _SESSION_TABLES.get(_classify_symbol(symbol), _DEFAULT_HOURS)
        scale = table.get(now.hour, 0.5)
        if scale is None:
            reasons.append("outside_symbol_liquidity_window")
            return 0.0, tuple(reasons)
        if scale < 1.0:
            reasons.append(f"low_liquidity_session({scale:.2f})")
        return float(scale), tuple(reasons)

    # ------------------------------------------------------------------
    # Guard bands
    # ------------------------------------------------------------------
    def _blackout_after(self, gate: Any, symbol: str) -> int:
        """Minutes after the nearest event until the gate re-opens entries."""
        try:
            windows = self._gate._get_blackout_windows(
                tier=gate.tier or 2,
                event_title=(gate.nearest_event or {}).get("title", ""),
                symbol=symbol,
            )
            return int(windows.get("after", 15))
        except Exception:
            return 15

    @staticmethod
    def spread_guard(market: Optional[dict]) -> Tuple[float, Tuple[str, ...]]:
        if not market:
            return 1.0, ()
        try:
            spread = float(market.get("spread")) if market.get("spread") is not None else None
            normal = float(market.get("normal_spread")) if market.get("normal_spread") is not None else None
        except (TypeError, ValueError):
            return 1.0, ()
        if not spread or not normal or spread <= 0 or normal <= 0:
            return 1.0, ()
        if spread / normal >= _SPREAD_SPIKE_RATIO:
            return 0.0, ("spread_spike_window",)
        return 1.0, ()

    @staticmethod
    def adx_guard(market: Optional[dict]) -> Tuple[float, Tuple[str, ...]]:
        if not market:
            return 1.0, ()
        try:
            adx = float(market.get("adx")) if market.get("adx") is not None else None
        except (TypeError, ValueError):
            return 1.0, ()
        if adx is None or adx <= 0:
            return 1.0, ()
        if adx < _ADX_MIN:
            return 0.5, ("adx_chop",)
        return 1.0, ()

    @staticmethod
    def fusion_guard(market: Optional[dict]) -> Tuple[float, Tuple[str, ...]]:
        """Honor an upstream macro-fusion verdict when the caller surfaces it."""
        if not market:
            return 1.0, ()
        directive = market.get("fusion_directive") or market.get("macro_fusion")
        if not directive:
            return 1.0, ()
        d = str(directive).upper().strip()
        if d in ("PROCEED", "STRONG_PROCEED"):
            return 1.0, ()
        if d == "HALF_RISK":
            return 0.5, ("macro_fusion_half_risk",)
        if d in ("REJECT", "POSTPONE"):
            return 0.0, ("macro_fusion_refuse",)
        return 1.0, ()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def plan(self, symbol: str, entry: float, sl: float,
             market: Optional[dict] = None,
             equity: float = 0.0,
             peak_equity: float = 0.0,
             anchor_equity: float = 0.0,
             now: Optional[datetime] = None) -> TradePlan:
        now = now or self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        parts: Dict[str, float] = {}
        reasons: List[str] = []
        veto = False
        entry_from: Optional[datetime] = None

        # 1) growth ladder (equity-unbounded)
        g_scale = self.growth_scale(peak_equity or equity, anchor_equity or equity)
        parts["growth"] = g_scale

        # 2) session timing
        s_scale, s_reasons = self.session_scale(symbol, now)
        parts["session"] = s_scale
        reasons.extend(s_reasons)
        veto = veto or (s_scale <= 0.0)

        # 3) guard bands
        sp_scale, sp_reasons = self.spread_guard(market)
        parts["spread"] = sp_scale
        reasons.extend(sp_reasons)
        veto = veto or (sp_scale <= 0.0)

        adx_scale, adx_reasons = self.adx_guard(market)
        parts["adx"] = adx_scale
        reasons.extend(adx_reasons)

        f_scale, f_reasons = self.fusion_guard(market)
        parts["fusion"] = f_scale
        reasons.extend(f_reasons)
        veto = veto or (f_scale <= 0.0)

        # 4) event calendar via the shared news gate (technical+macro timing)
        ev_phase = "CLEAR"
        minutes_to = minutes_since = None
        nearest_title = None
        e_scale = 1.0
        events, cal_state = self._events(now)
        if events:
            gate = self._gate.evaluate(symbol, now, events)
            ev_phase = gate.phase
            minutes_to = gate.minutes_to_event
            minutes_since = gate.minutes_since_event
            nearest_title = (gate.nearest_event or {}).get("title")
            if gate.phase == "SPIKE_BLACKOUT":
                e_scale = 0.0
                reasons.append("news_spike_blackout")
                veto = True
                ev_time = (gate.nearest_event or {}).get("time")
                if isinstance(ev_time, datetime):
                    entry_from = ev_time + timedelta(minutes=self._blackout_after(gate, symbol))
            elif gate.phase == "PRE_NEWS":
                e_scale = 0.0
                reasons.append("pre_news_warning")
                veto = True
                ev_time = (gate.nearest_event or {}).get("time")
                if isinstance(ev_time, datetime):
                    entry_from = ev_time + timedelta(minutes=self._blackout_after(gate, symbol) + 1)
            elif gate.phase == "POST_NEWS_ENTRY":
                reasons.append("post_news_pullback_zone")
        parts["event"] = e_scale
        veto = veto or (e_scale <= 0.0)
        veto = veto or (g_scale < 0.01)  # defensive: pathological scale

        scale = g_scale * s_scale * sp_scale * adx_scale * f_scale * e_scale
        scale = max(0.0, min(1.0, scale))

        # A veto from any refuse-stage is a real block; taper-only stages
        # (growth/adx) reduce the size instead. In observe mode a would-be
        # veto is reported as HOLD so the log honestly shows a trade that
        # would have been blocked without changing behavior.
        decision = "VETO" if veto else "PROCEED"
        if self.mode != "enforce" and decision == "VETO":
            decision = "HOLD"

        return TradePlan(
            decision=decision,
            risk_scale=scale,
            reasons=tuple(reasons),
            event_phase=ev_phase,
            minutes_to_event=minutes_to,
            minutes_since_event=minutes_since,
            entry_execute_from=entry_from.isoformat() if entry_from else None,
            nearest_event_title=nearest_title,
            calendar_state=cal_state,
            mode=self.mode,
            parts=parts,
        )


def state_calendar_provider(db_path: str):
    """Build an events provider over the live ``event_monitor:*`` state rows.

    The ``macro_events`` mirror is frequently stale (its last refresh depends on
    the bulk_calendar_ingest step, which times out on slow boots), but the
    event_monitor schedule writes a fresh ``event_monitor:<SYMBOL>`` state row
    every 60 s with the full calendar for that symbol.  This provider merges
    every such row so :class:`NewsTradingGate` can filter by symbol relevance,
    and reports staleness from the freshest captured_at stamp.
    """

    def provider():
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            try:
                rows = con.execute(
                    "select key, value from state where key like 'event_monitor:%'"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            mirror = con.execute(
                "select event_time, currency, title, importance, captured_at from macro_events "
                "where event_time is not null and event_time <> ''"
            ).fetchall()
        finally:
            con.close()

        events: List[Dict[str, Any]] = []
        captured_stamps: List[datetime] = []
        now = datetime.now(timezone.utc)

        for key, value in rows:
            try:
                blob = json.loads(value)
            except (TypeError, ValueError):
                continue
            cap = blob.get("captured_at")
            if cap:
                try:
                    cap_dt = datetime.fromisoformat(str(cap).replace("Z", "+00:00"))
                    if cap_dt.tzinfo is None:
                        cap_dt = cap_dt.replace(tzinfo=timezone.utc)
                    captured_stamps.append(cap_dt)
                except ValueError:
                    pass
            status = str(blob.get("status", "")).upper()
            if status not in ("AVAILABLE", "OK", ""):
                continue
            for ev in (blob.get("events") or []):
                currency = ev.get("currency") or ev.get("country") or ""
                title = ev.get("title") or ev.get("name") or ""
                importance = ev.get("impact") or ev.get("importance") or ""
                raw_time = ev.get("date") or ev.get("time") or ev.get("event_time") or ev.get("datetime_utc")
                if not raw_time:
                    continue
                try:
                    dt = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                events.append({"time": dt, "currency": currency, "title": title,
                               "importance": importance})

        # Merge the (possibly stale) mirror as a fallback for symbols that have
        # no live state row yet; de-dup by (time, title).
        seen = {(e["time"], e["title"]) for e in events}
        for event_time, currency, title, importance, captured in mirror:
            try:
                dt = datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (dt, title) in seen:
                continue
            if abs((dt - now).total_seconds()) > 7 * 24 * 3600:
                continue
            events.append({"time": dt, "currency": currency or "", "title": title or "",
                           "importance": importance or ""})
            seen.add((dt, title))

        if captured_stamps:
            freshest = max(captured_stamps)
            state = "OK" if (now - freshest).total_seconds() <= 2 * 3600 else "STALE"
        elif mirror:
            # Fall back to the mirror's own captured_at stamp.
            try:
                caps = []
                for row in mirror:
                    c = row[4] if len(row) > 4 else None
                    if c:
                        dt = datetime.fromisoformat(str(c).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        caps.append(dt)
                if caps and (now - max(caps)).total_seconds() <= 36 * 3600:
                    state = "OK"
                else:
                    state = "STALE"
            except (ValueError, TypeError):
                state = "STALE"
        else:
            state = "UNKNOWN"
        return events, state

    return provider, "UNKNOWN"

    def provider() -> Tuple[List[Dict[str, Any]], str]:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "select event_time, currency, title, importance, captured_at from macro_events "
                "where event_time is not null and event_time <> '' "
                "order by event_time desc limit 200"
            ).fetchall()
            captured = con.execute(
                "select max(captured_at) from macro_events"
            ).fetchone()[0]
        finally:
            con.close()
        events: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for event_time, currency, title, importance, _captured in rows:
            try:
                dt = datetime.fromisoformat(str(event_time)) if str(event_time).endswith(("Z", "+00:00")) \
                    else datetime.fromisoformat(str(event_time)).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if abs((dt - now).total_seconds()) > 7 * 24 * 3600:
                continue          # mirror rows far from now are dead-weight noise
            events.append({"time": dt, "currency": currency or "", "title": title or "",
                           "importance": importance or ""})
        if captured:
            try:
                cap_dt = datetime.fromisoformat(str(captured)) if str(captured).endswith(("Z", "+00:00")) \
                    else datetime.fromisoformat(str(captured)).replace(tzinfo=timezone.utc)
                state = "OK" if (now - cap_dt).total_seconds() <= _CALENDAR_STALE_S else "STALE"
            except ValueError:
                state = "STALE"
        else:
            state = "STALE"
        return events, state

    # Fail-closed default until the first read proves the feed is alive.
    return provider, "STALE"


# Backwards-compatible macro_events-only provider (kept for tests / legacy callers).
def journal_events_provider(db_path: str):
    def provider():
        uri = 'file:' + db_path + '?mode=ro'
        con = __import__('sqlite3').connect(uri, uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "select event_time, currency, title, importance, captured_at from macro_events "
                "where event_time is not null and event_time <> ''"
            ).fetchall()
            captured = con.execute('select max(captured_at) from macro_events').fetchone()[0]
        finally:
            con.close()
        events = []
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        for event_time, currency, title, importance, _captured in rows:
            try:
                dt = __import__('datetime').datetime.fromisoformat(str(event_time).replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=__import__('datetime').timezone.utc)
            except ValueError:
                continue
            if abs((dt - now).total_seconds()) > 7 * 24 * 3600:
                continue
            events.append({'time': dt, 'currency': currency or '', 'title': title or '',
                           'importance': importance or ''})
        if captured:
            try:
                cap_dt = __import__('datetime').datetime.fromisoformat(str(captured).replace('Z', '+00:00'))
                if cap_dt.tzinfo is None:
                    cap_dt = cap_dt.replace(tzinfo=__import__('datetime').timezone.utc)
                state = 'OK' if (now - cap_dt).total_seconds() <= 36.0 * 3600 else 'STALE'
            except ValueError:
                state = 'STALE'
        else:
            state = 'STALE'
        return events, state
    return provider, 'STALE'
