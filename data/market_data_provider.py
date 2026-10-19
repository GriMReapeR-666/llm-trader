"""Market data provider, depth of market collection, and performance monitoring."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("MarketData")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Single import root (P1-C1/M3): repo root only, never subpackage folders.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Validated-candidate state written by core.alpha_discovery; the rotation
# block reads it as its promotion-candidate feed (single connection point).
DISCOVERY_STATE_FILE = "alpha_discovery_state.json"

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    from core.technical_indicators import (
        adx, atr, compute_indicators, ema, hma, macd, rsi, wma
    )
except ImportError:
    from core.technical_indicators import (
        adx, atr, compute_indicators, ema, hma, macd, rsi, wma
    )

try:
    from core.order_execution import place_market_order, place_pending_order
except ImportError:
    from core.order_execution import place_market_order, place_pending_order

try:
    from data.api_manager import APIManager
    API_MANAGER_AVAILABLE = True
except ImportError:
    API_MANAGER_AVAILABLE = False
    APIManager = None

try:
    from core.trade_manager import TradeManager
    TRADE_MANAGER_AVAILABLE = True
except ImportError:
    TRADE_MANAGER_AVAILABLE = False
    TradeManager = None

try:
    from data.data_source_pipeline import catalog_fred_series
except Exception:
    try:
        from data.data_source_pipeline import catalog_fred_series
    except Exception:
        def catalog_fred_series(*args, **kwargs):
            return {}

# ── Liquidity Vacuum + ADV Tracker (Blind Spot audit) ────────────────────────
try:
    from core.liquidity_vacuum_detector import LiquidityVacuumDetector as _LiqVacDetector
except ImportError:
    _LiqVacDetector = None

try:
    from core.adv_tracker import ADVTracker as _ADVTrackerMod
except ImportError:
    _ADVTrackerMod = None


def _get_mt5():
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "mt5"):
        return mt5_mod.mt5
    return mt5


def _get_fetch_ohlc():
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "fetch_ohlc") and mt5_mod.fetch_ohlc is not fetch_ohlc:
        return mt5_mod.fetch_ohlc
    return fetch_ohlc


def _get_tf_const():
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "tf_const") and mt5_mod.tf_const is not tf_const:
        return mt5_mod.tf_const
    return tf_const


TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def tf_const(tf: str) -> int:
    mt5_inst = _get_mt5()
    name = TIMEFRAME_MAP.get(tf.upper())
    if not name or not hasattr(mt5_inst, name):
        raise ValueError(f"Unsupported timeframe {tf}")
    return getattr(mt5_inst, name)


# Broker server→UTC offset cache (offset seconds + expiry epoch).
# 24h TTL: broker timezone changes at most twice a year (DST); weekend closed
# markets keep the last known offset instead of falling back to 0.
_SERVER_OFFSET_TTL = 24 * 3600
_SERVER_OFFSET_CACHE: Dict[str, Any] = {"offset": 0, "expires": 0.0}


def _server_utc_offset_seconds(mt5_inst: Any, probe_symbols: Tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD")) -> int:
    """Estimate the broker server-timezone offset (server ahead of UTC), seconds.

    MT5 bar timestamps are epoch seconds expressed in the BROKER SERVER's
    timezone, not UTC (e.g. MetaQuotes demo servers run UTC+3). Decoding them
    naively with ``unit='s'`` presents the completed bar as ~3h in the future,
    which the feature-lineage validator correctly rejects as look-ahead
    ("completed bar timestamp is later than capture time") — every proposal
    blocked, pipeline starved.

    Method: a fresh tick carries the server's *now* in server epoch; compare
    against true UTC now. Broker clocks are NTP-synced, so the residual after
    snapping to a 30-minute grid must be small (<5 min) — a frozen/stale tick
    (market closed) produces a large random residual and is discarded. When 2+
    independent symbols agree on the offset, it is cached for 24h so closed-
    market periods (weekends) keep the last known offset. Returns 0 when no
    trustworthy probe exists — fail-open to the legacy naive behaviour rather
    than guessing wrong.
    """
    now = time.time()
    if now < _SERVER_OFFSET_CACHE["expires"]:
        return _SERVER_OFFSET_CACHE["offset"]
    try:
        candidates = []  # (offset_seconds, residual_ms)
        for name in probe_symbols:
            t = mt5_inst.symbol_info_tick(name)
            if t is None:
                continue
            server_ms = float(getattr(t, "time_msc", 0) or 0)
            if server_ms <= 0:
                continue
            residual_ms = server_ms - now * 1000.0
            steps = int(round(residual_ms / 1_800_000.0))  # snap to 30-min grid
            if steps < -28 or steps > 28:                  # clamp to ±14h
                continue
            residual = abs(residual_ms - steps * 1_800_000.0)
            if residual > 300_000:  # frozen/stale tick → untrustworthy clock
                continue
            candidates.append((steps * 1800, residual))
        if not candidates:
            return 0
        from collections import Counter
        offset, votes = Counter(off for off, _ in candidates).most_common(1)[0]
        if votes < 2:
            # single candidate: only trust a very tight residual
            if candidates[0][1] > 120_000:
                return 0
        logger.info("MT5 server-timezone offset detected: %+ds (UTC%+g)",
                    offset, offset / 3600.0)
        _SERVER_OFFSET_CACHE["offset"] = int(offset)
        _SERVER_OFFSET_CACHE["expires"] = now + _SERVER_OFFSET_TTL
        return int(offset)
    except Exception:
        return 0


def _ensure_mt5_attached(mt5_inst: Any) -> bool:
    """Lazily attach this process to the MT5 terminal for data-only consumers.

    fetch_ohlc() used to work only in processes where some other component
    (MT5Client login, paper session) had already called mt5.initialize().
    Scheduled/standalone validation consumers — the continuous backtest
    pipeline, research CLIs — import fetch_ohlc directly and never attach,
    so terminal_info() was None and every certification run failed closed
    with "MT5 terminal is unavailable ... refusing external or synthetic
    fallback" (100/100 strategy runs failed on exactly this, 2026-09-29 →
    10-01) even though the terminal was installed and reachable on the host.

    The bare attach is the same data-plane session mt5_trader.py already
    sanctions for paper market data: it returns real broker OHLC, never
    synthetic or external fallback, so the fail-closed doctrine is intact.
    Objects without an ``initialize`` attribute (test fakes, exotic shims)
    keep the prior behavior exactly.
    """
    try:
        if mt5_inst.terminal_info() is not None:
            return True
    except Exception:
        return False
    init = getattr(mt5_inst, "initialize", None)
    if init is None:
        return False
    try:
        attached = bool(init())
    except Exception:
        return False
    if not attached:
        return False
    try:
        return mt5_inst.terminal_info() is not None
    except Exception:
        return False


def fetch_ohlc(symbol: str, timeframe: str, count: int = 100) -> pd.DataFrame:
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        raise RuntimeError(
            "MetaTrader5 package is unavailable; refusing external or synthetic market-data fallback"
        )
    terminal_available = False
    try:
        terminal_available = mt5_inst.terminal_info() is not None
    except Exception:
        terminal_available = False
    if not terminal_available:
        # Data-only consumer with no prior broker session in this process:
        # attach lazily (real broker data only — never a synthetic fallback).
        terminal_available = _ensure_mt5_attached(mt5_inst)
    symbol_info = mt5_inst.symbol_info(symbol)
    if symbol_info is None:
        if mt5_inst.terminal_info() is not None:
            all_symbols = mt5_inst.symbols_get()
            similar = []
            if all_symbols:
                similar = [s.name for s in all_symbols if symbol.upper() in s.name.upper()][:5]
            alternatives = []
            if all_symbols:
                if 'BTC' in symbol.upper():
                    alternatives = [s.name for s in all_symbols if 'BTC' in s.name][:5]
                elif 'ETH' in symbol.upper():
                    alternatives = [s.name for s in all_symbols if 'ETH' in s.name][:5]
            error_msg = f"Symbol '{symbol}' not found on broker."
            if similar:
                error_msg += f"\nSimilar symbols: {', '.join(similar)}"
            if alternatives and alternatives != similar:
                error_msg += f"\nAlternatives: {', '.join(alternatives)}"
            error_msg += f"\nTip: Use AUTO mode to let LLM select available symbols, or try: EURUSD, GBPUSD, USDJPY, XAUUSD"
            raise RuntimeError(error_msg)

    if symbol_info and not getattr(symbol_info, "visible", True):
        if not mt5_inst.symbol_select(symbol, True):
            raise RuntimeError(f"Failed to enable symbol '{symbol}'")

    tf_fn = _get_tf_const()
    tf = tf_fn(timeframe)
    rates = None
    try:
        rates = mt5_inst.copy_rates_from_pos(symbol, tf, 0, count)
    except Exception as exc:
        logger.debug("LLM operation failed: %s", exc)

    if rates is None or len(rates) == 0:
        reason = "MT5 returned no OHLC bars" if terminal_available else "MT5 terminal is unavailable"
        raise RuntimeError(
            f"{reason} for {symbol}/{timeframe}; refusing external or synthetic fallback; "
            "supply an explicit broker-replay or test fixture instead"
        )

    rates_frame = pd.DataFrame(rates)
    if "tick_volume" in rates_frame.columns and "volume" not in rates_frame.columns:
        rates_frame["volume"] = rates_frame["tick_volume"]
    elif "real_volume" in rates_frame.columns and "volume" not in rates_frame.columns:
        rates_frame["volume"] = rates_frame["real_volume"]
    # MT5 epochs are broker-server-time; convert to true UTC so completed-bar
    # lineage never appears in the future (server time > capture time).
    offset = _server_utc_offset_seconds(mt5_inst)
    if offset:
        rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s') - pd.Timedelta(seconds=offset)
    else:
        rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')
    rates_frame.set_index('time', inplace=True)
    return rates_frame


def ohlc_records(df: pd.DataFrame) -> list:
    """Convert a time-indexed OHLC frame to records WITHOUT dropping timestamps.

    ``DataFrame.to_dict(orient="records")`` silently omits the index, which
    stripped every bar of its ``time`` when ``fetch_ohlc`` returns a
    time-indexed frame. Downstream consumers (feature builder's
    ``completed_bar_time``, correlation capture, validator lineage) all rely
    on that timestamp — its loss made every LLM proposal fail validation with
    "completed bar timestamp is missing or malformed". Always use this helper
    instead of a bare ``to_dict(orient="records")`` on ``fetch_ohlc`` output.
    """
    if df is None or len(df) == 0:
        return []
    frame = df
    # Only fabricate a 'time' field when the index genuinely IS a timestamp.
    if "time" not in getattr(df, "columns", []) and isinstance(df.index, pd.DatetimeIndex):
        frame = df.reset_index()
        first = frame.columns[0]
        frame[first] = frame[first].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if first != "time":
            frame = frame.rename(columns={first: "time"})
    return frame.to_dict(orient="records")


def collect_calendar_event_rows(calendar: Any, client: Any,
                                 mt5_module: Any = None,
                                 now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], str]:
    rows: List[Dict[str, Any]] = []
    source = "UNAVAILABLE"
    if calendar is not None:
        try:
            events = calendar.get_events(
                limit=5,
                min_importance=2,
                currencies=["USD", "EUR", "GBP", "JPY"],
            )
            for event in events or []:
                row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                if isinstance(row, dict):
                    rows.append(row)
            source = "configured_calendar"
        except Exception:
            source = "ERROR"

    if rows:
        return rows, source

    module = mt5_module if mt5_module is not None else _get_mt5()
    if client is not None and module is not None and hasattr(module, "calendar_get"):
        anchor = now or datetime.now(timezone.utc)
        anchor_naive = anchor.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            events = module.calendar_get(anchor_naive, anchor_naive + timedelta(hours=24))
            high_impact = []
            for event in events or []:
                try:
                    importance = int(getattr(event, "importance"))
                except (TypeError, ValueError):
                    continue
                if importance >= 2:
                    high_impact.append(event)
            high_impact.sort(key=lambda event: getattr(event, "time", 0))
            for event in high_impact[:5]:
                try:
                    event_time = datetime.fromtimestamp(float(getattr(event, "time")), tz=timezone.utc).isoformat()
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                rows.append({
                    "time": event_time,
                    "currency": getattr(event, "currency", ""),
                    "event": getattr(event, "event", ""),
                    "importance": int(getattr(event, "importance")),
                    "forecast": getattr(event, "forecast_value", None),
                    "previous": getattr(event, "previous_value", None),
                })
            source = "mt5_calendar" if rows else source
        except Exception:
            if source == "UNAVAILABLE":
                source = "ERROR"
    return rows, source


def collect_calendar_event_rows_with_ff(calendar: Any, client: Any,
                                         mt5_module: Any = None,
                                         now: Optional[datetime] = None,
                                         ff_provider: Any = None) -> Tuple[List[Dict[str, Any]], str]:
    """Calendar collection with Forex Factory fallback."""
    rows, source = collect_calendar_event_rows(calendar, client, mt5_module, now)
    if rows:
        return rows, source

    # --- Forex Factory fallback ---
    if ff_provider is not None:
        try:
            ff_events = ff_provider.fetch_calendar(min_impact="HIGH")
            for ev in ff_events[:5]:
                rows.append({
                    "time": ev.datetime_utc or f"{ev.date}T{ev.time}Z",
                    "currency": ev.currency,
                    "event": ev.title,
                    "importance": 3 if ev.impact == "HIGH" else 2,
                    "forecast": ev.forecast,
                    "previous": ev.previous,
                })
            source = "forex_factory" if rows else source
        except Exception as e:
            logging.getLogger("MarketData").debug("ForexFactory fallback failed: %s", e)
    return rows, source


class DOMCollector:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.subscribed = False

    def subscribe(self) -> bool:
        mt5_inst = _get_mt5()
        if not self.subscribed and mt5_inst is not None:
            self.subscribed = mt5_inst.market_book_add(self.symbol)
        return self.subscribed

    def unsubscribe(self) -> None:
        mt5_inst = _get_mt5()
        if self.subscribed and mt5_inst is not None:
            mt5_inst.market_book_release(self.symbol)
            self.subscribed = False

    def get_snapshot(self) -> Dict[str, Any]:
        captured_at = datetime.now(timezone.utc).isoformat()
        mt5_inst = _get_mt5()
        if not self.subscribed or mt5_inst is None:
            return {"captured_at": captured_at, "source": "mt5_dom", "available": False}
        
        book = mt5_inst.market_book_get(self.symbol)
        if not book:
            return {"captured_at": captured_at, "source": "mt5_dom", "available": False}

        asks = sorted([{'price': x.price, 'vol': x.volume} for x in book if x.type == getattr(mt5_inst, "BOOK_TYPE_SELL", 1)], key=lambda x: x['price'])
        bids = sorted([{'price': x.price, 'vol': x.volume} for x in book if x.type == getattr(mt5_inst, "BOOK_TYPE_BUY", 2)], key=lambda x: x['price'], reverse=True)

        best_ask = asks[0]['price'] if asks else 0.0
        best_bid = bids[0]['price'] if bids else 0.0
        
        total_ask_vol = sum(x['vol'] for x in asks)
        total_bid_vol = sum(x['vol'] for x in bids)
        imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol) if (total_bid_vol + total_ask_vol) > 0 else 0

        return {
            "captured_at": captured_at,
            "source": "mt5_dom",
            "available": True,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
            "bid_vol_total": total_bid_vol,
            "ask_vol_total": total_ask_vol,
            "imbalance": imbalance,
            "dom_depth": min(len(bids), len(asks)),
            "top_bids": bids[:5],
            "top_asks": asks[:5],
        }


def _classify_decay_status(r_multiple: Optional[float]) -> str:
    """Classify an R-multiple into a decay status enum.

    The pattern miner in ``core/meta_loop`` expects one of:
    HEALTHY, DEGRADED, DEAD, REVERSED — not a raw numeric string.
    """
    if r_multiple is None:
        return "UNKNOWN"
    try:
        r = float(r_multiple)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if r < -1.5:
        return "DEAD"
    elif r < -0.5:
        return "DEGRADED"
    elif r < 0:
        return "REVERSED"
    else:
        return "HEALTHY"


class PerformanceMonitor:
    def __init__(self, history_file: str = "trading_rewards.json", store: Optional[Any] = None):
        self.history_file = history_file
        self.store = store
        self.history: List[Dict[str, Any]] = []
        self.recent_actions: List[Dict[str, Any]] = []
        self.rl_update_callback = None
        self.load_history()

    @property
    def trades(self) -> List[Dict[str, Any]]:
        return self.history

    @trades.setter
    def trades(self, value: List[Dict[str, Any]]) -> None:
        self.history = list(value or [])
        
    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.history = data[-2000:] if isinstance(data, list) and len(data) > 2000 else (data if isinstance(data, list) else [])
            except Exception:
                self.history = []

        if not self.history and self.store is not None:
            try:
                if hasattr(self.store, "open_trade_rows"):
                    for row in self.store.open_trade_rows():
                        t = {
                            "ticket": int(row.get("ticket", 0)),
                            "symbol": str(row.get("symbol", "")),
                            "action": str(row.get("side", "BUY")),
                            "volume": float(row.get("volume", 0.0) or 0.0),
                            "open_price": float(row.get("price_open", 0.0) or 0.0),
                            "open_time_epoch": float(row.get("opened_at", time.time()) or time.time()),
                            "decision": {},
                            "risk_amount": float(row.get("risk_amount", 0.0) or 0.0),
                            "metrics": {},
                            "pnl": 0.0,
                            "status": "OPEN",
                            "rewards": {},
                        }
                        self.history.append(t)
            except Exception:
                pass

    def save_history(self):
        with open(self.history_file, 'w', encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)

    def log_trade(self, ticket: int, symbol: str, action: str, volume: float, 
                  price: float, decision: Dict[str, Any]):
        metrics = decision.get("metrics", {})
        expected_price = metrics.get("expected_price", price)
        slippage = abs(price - expected_price)
        
        record = {
            "ticket": ticket,
            "symbol": symbol,
            "action": action,
            "volume": volume,
            "open_price": price,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "open_time_epoch": time.time(),
            "decision": decision,
            "risk_amount": float(decision.get("risk_amount", 0.0) or 0.0),
            "metrics": {
                "slippage": slippage,
                "decision_latency": metrics.get("decision_latency", 0),
                "total_latency": metrics.get("total_latency", 0),
            },
            "pnl": 0.0,
            "status": "OPEN",
            "rewards": {},
        }
        self.history.append(record)
        if len(self.history) > 2000:
            self.history = self.history[-2000:]
        self.save_history()

    def close_trade(self, ticket: int, exit_price: float, pnl: float):
        for t in self.history:
            if t["ticket"] == ticket and t["status"] == "OPEN":
                t["status"] = "CLOSED"
                t["exit_time"] = time.time()
                t["exit_price"] = exit_price
                t["pnl"] = pnl
                risk_amount = float(t.get("risk_amount", 0.0) or 0.0)
                if risk_amount <= 0:
                    risk_amount = float(t.get("volume", 0.01) or 0.01) * 100
                t["r_multiple"] = pnl / risk_amount if risk_amount > 0 else None
                
                if "rewards" not in t:
                    t["rewards"] = {}
                t["rewards"]["immediate_pnl"] = pnl
                t["rewards"]["rolling_sharpe"] = self.calc_rolling_sharpe()
                t["rewards"]["max_drawdown"] = self.calc_max_drawdown()

                self.save_history()
                self._persist_alert_metrics()
                
                if self.rl_update_callback and callable(self.rl_update_callback):
                    try:
                        decision = t.get("decision", {})
                        trade_result = {
                            "ticket": ticket,
                            "pnl": pnl,
                            "risk": risk_amount,
                            "r_multiple": t.get("r_multiple"),
                            "duration_seconds": max(0.0, float(t.get("exit_time", 0)) - float(t.get("open_time_epoch", t.get("exit_time", 0)))),
                            "confluence_score": decision.get("confluence", {}).get("score", decision.get("confluence_score", 0)),
                            "news_aligned": decision.get("news_aligned", False),
                            "max_drawdown": t["rewards"]["max_drawdown"],
                            "rl_state": decision.get("rl_state"),
                            "rl_action": decision.get("rl_action"),
                            "action": t.get("action") or decision.get("action"),
                            "exit_state": {"position": "FLAT", "r_multiple": t.get("r_multiple"),
                                           "realized_pnl": pnl, "max_drawdown": t["rewards"]["max_drawdown"],
                                           "duration_seconds": max(0.0, float(t.get("exit_time", 0)) - float(t.get("open_time_epoch", t.get("exit_time", 0))))},
                            "broker_reconciled": bool(t.get("broker_reconciled", True)),
                        }
                        # Fix: paper trades auto-reconciled — RL should learn from all closed trades with valid pnl/r, not only broker flag. Live MT5 will set explicit True, paper defaults True, auto-clears when trade closed.
                        self.rl_update_callback(trade_result)
                    except Exception as e:
                        logging.getLogger("PerformanceMonitor").error(f"RL update callback failed: {e}")

                # ── UAQP Loop 4: Feed closed trade outcomes to meta-loop corpus ──
                try:
                    from core.meta_loop import get_meta_loop
                    meta = get_meta_loop()
                    rolling_sharpe = t["rewards"].get("rolling_sharpe", 0.0)
                    max_dd = t["rewards"].get("max_drawdown", 0.0)
                    r_mult = t.get("r_multiple")
                    # Capture diverse hypothesis for pattern mining — not dummy BUY. Use symbol:strategy:action + regime/feature for variance
                    decision = t.get("decision", {}) or {}
                    strat = decision.get("strategy") or decision.get("setup_id") or decision.get("strategy_id") or t.get("symbol") or "live_trade"
                    expr = f"{t.get('symbol','')}:{t.get('action','')}:{strat}:{decision.get('timeframe','')}:{t.get('symbol','')}_{strat}" if strat != "live_trade" else f"{t.get('symbol','')}:{t.get('action','')}:{ticket}"
                    regime_val = decision.get("regime") or decision.get("regime_allocation", {}).get("regime", {}).get("primary_regime", "") or t.get("regime", "") or decision.get("primary_regime", "")
                    # Journal rows persist the payload regime (trending/ranging);
                    # prefer it when the in-memory decision chain lost it.
                    if not regime_val:
                        try:
                            _row_payload = json.loads(t.get("payload") or "{}") if isinstance(t.get("payload"), str) else (t.get("payload") or {})
                            regime_val = _row_payload.get("regime", "")
                        except (json.JSONDecodeError, TypeError):
                            regime_val = ""
                    # Derive feature_type from alpha or strategy, holding_horizon from timeframe, backtest metrics from decision if present
                    feat_type = decision.get("feature_type") or decision.get("alpha_source") or ("momentum" if "trend" in str(strat).lower() else "mean_reversion" if "reversal" in str(strat).lower() else "volume_price" if "volume" in str(strat).lower() else strat.split("_")[0] if "_" in strat else "mixed")
                    horizon = decision.get("timeframe") or decision.get("holding_horizon") or t.get("timeframe") or "M15"
                    # Try to get real backtest stats from decision metrics or strategy registry
                    bt_metrics = decision.get("metrics") or decision.get("backtest") or {}
                    meta.log_outcome(
                        hypothesis_id=f"{ticket}_{t.get('symbol','')}_{strat}",
                        hypothesis_source=str(strat),
                        hypothesis_expression=expr[:120],
                        regime=str(regime_val)[:40],
                        backtest_sharpe=float(bt_metrics.get("sharpe", 0.0) or 0.0),
                        backtest_max_dd=float(bt_metrics.get("max_dd", 0.0) or 0.0),
                        backtest_win_rate=float(bt_metrics.get("win_rate", 0.0) or 0.0),
                        backtest_n_trades=int(bt_metrics.get("total_trades", 0) or 0),
                        live_sharpe=rolling_sharpe,
                        live_pnl=pnl,
                        live_max_dd=max_dd,
                        live_n_trades=1,
                        decay_status=_classify_decay_status(r_mult),
                        ic_raw=float(decision.get("ic_raw", 0.0) or 0.0),
                        ic_purified=float(decision.get("ic_purified", 0.0) or 0.0),
                        feature_type=str(feat_type)[:30],
                        holding_horizon=str(horizon)[:10],
                    )
                except Exception as e:
                    logging.getLogger("PerformanceMonitor").debug(f"Meta-loop log_outcome failed: {e}")
                break

    def _persist_alert_metrics(self, window: int = 20) -> None:
        """Publish rolling performance metrics used by the alert rules and the
        degradation detector.

        Historically nothing ever wrote ``rolling_win_rate``/
        ``rolling_expectancy_r``/``consecutive_losses`` to the store, so the
        low-win-rate alert fired every few minutes on an empty sample
        (value=0.0 with zero closed trades) and the detector considered the
        same phantom metric. Samples are persisted alongside the rates so
        consumers can distinguish "no data" from "genuinely bad".
        """
        store = self.store
        if store is None or not hasattr(store, "set_state"):
            return
        try:
            closed = [t for t in self.history
                      if t.get("status") == "CLOSED"
                      and t.get("pnl") is not None]
            closed.sort(key=lambda t: float(t.get("exit_time", 0) or 0))
            recent = closed[-window:]
            n = len(recent)
            if n:
                wins = sum(1 for t in recent if float(t.get("pnl", 0.0) or 0.0) > 0)
                store.set_state("rolling_win_rate", wins / n)
                store.set_state("rolling_win_rate_samples", n)
                rs = [float(t.get("r_multiple") or 0.0) for t in recent]
                store.set_state("rolling_expectancy_r", sum(rs) / n)
                streak = 0
                for t in reversed(recent):
                    if float(t.get("pnl", 0.0) or 0.0) <= 0:
                        streak += 1
                    else:
                        break
                store.set_state("consecutive_losses", streak)
        except Exception as exc:
            logging.getLogger("PerformanceMonitor").debug(
                "alert-metric persistence skipped: %s", exc)

    def calc_rolling_sharpe(self, window: int = 20) -> float:
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        if len(closed) < 2:
            return 0.0
        pnls = [t["pnl"] for t in closed[-window:]]
        avg = np.mean(pnls)
        std = np.std(pnls)
        if std == 0:
            return 0.0
        return float(avg / std)

    def calc_rolling_sortino(self, window: int = 20) -> float:
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        if len(closed) < 2:
            return 0.0
        try:
            pnls = [float(t.get("pnl", 0.0) or 0.0) for t in closed[-max(1, int(window)):]]
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if len(pnls) < 2 or any(not math.isfinite(value) for value in pnls):
            return 0.0
        average = float(np.mean(pnls))
        downside_deviation = float(np.sqrt(np.mean(np.square([min(0.0, value) for value in pnls]))))
        if downside_deviation <= 0 or not math.isfinite(downside_deviation):
            return 0.0
        return float(average / downside_deviation)

    def calc_max_drawdown(self) -> float:
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        if not closed:
            return 0.0
        cum_pnl = np.cumsum([t["pnl"] for t in closed])
        peak = np.maximum.accumulate(cum_pnl)
        dd = (peak - cum_pnl)
        return float(np.max(dd)) if len(dd) > 0 else 0.0

    def print_summary(self):
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        total_trades = len(closed)
        if total_trades == 0:
            open_t = [t for t in self.history if t.get("status") == "OPEN"]
            if open_t:
                print(f"\n[Performance] 0 Closed Trades. {len(open_t)} Open Positions.")
            else:
                print("\n[Performance] No trades recorded this session.")
            return

        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        win_rate = (len(wins) / total_trades) * 100
        total_pnl = sum(t["pnl"] for t in closed)
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float('inf')
        sharpe = self.calc_rolling_sharpe()
        dd = self.calc_max_drawdown()

        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            table = Table(title="📊 Session Performance Summary", show_header=True, header_style="bold magenta")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="bold white")
            table.add_row("Total Trades", str(total_trades))
            table.add_row("Win Rate", f"{win_rate:.1f}%")
            table.add_row("Total PnL", f"${total_pnl:.2f}")
            table.add_row("Profit Factor", f"{profit_factor:.2f}")
            table.add_row("Avg Win", f"${avg_win:.2f}")
            table.add_row("Avg Loss", f"${avg_loss:.2f}")
            table.add_row("Sharpe Ratio", f"{sharpe:.2f}")
            table.add_row("Max Drawdown", f"${dd:.2f}")
            console.print(table)
        except ImportError:
            print(f"\n--- Performance Summary ---\nTrades: {total_trades}\nPnL: {total_pnl}\nWin Rate: {win_rate}%")

    def log_missed_signal(self, symbol: str, decision: str, price_move: float):
        self.history.append({
            "ticket": -1,
            "symbol": symbol,
            "action": "MISSED",
            "entry_time": time.time(),
            "rationale": {"note": f"Missed move of {price_move:.4f} after {decision}"},
            "status": "CLOSED",
            "pnl": -abs(price_move),
            "rewards": {"immediate_pnl": -abs(price_move)},
        })
        self.save_history()
    
    def log_action(self, action_type: str, symbol: str, details: str, result: str):
        self.recent_actions.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action_type,
            "symbol": symbol,
            "details": details,
            "result": result,
        })
        if len(self.recent_actions) > 50:
            self.recent_actions = self.recent_actions[-50:]


class MarketDataProvider:
    def __init__(self, client: Optional[Any], risk_manager: Any, api_key: str = "", api_manager=None):
        self.client = client
        self.risk = risk_manager
        # Opt-in paper backstop for missing account evidence. Enabled ONLY by
        # LLMOrchestrator when journal_mode == PAPER; live/demo never enable
        # it, so missing account evidence keeps failing closed there.
        self.paper_account_evidence = False
        
        if API_MANAGER_AVAILABLE and api_manager is None:
            try:
                self.api_manager = APIManager()
            except Exception as e:
                logging.warning(f"APIManager init failed: {e}")
                self.api_manager = None
        else:
            self.api_manager = api_manager
        
        try:
            from data.mt5_calendar_enhanced import create_enhanced_calendar_wrapper
            mt5_inst = _get_mt5()
            terminal_info = mt5_inst.terminal_info() if mt5_inst else None
            dll_path = None
            if terminal_info and getattr(terminal_info, "path", None):
                dll_path = os.path.join(terminal_info.path, "terminal64.dll")
                if not os.path.exists(dll_path):
                    dll_path = os.path.join(terminal_info.path, "MQL5", "Libraries", "terminal64.dll")
                    if not os.path.exists(dll_path):
                        dll_path = None

            if dll_path and os.path.exists(dll_path):
                self.calendar = create_enhanced_calendar_wrapper(dll_path=dll_path)
            else:
                self.calendar = create_enhanced_calendar_wrapper()
        except Exception as e:
            self.calendar = None
            logging.info(f"Enhanced calendar unavailable; no calendar fallback is trusted: {e}")

        # --- Forex Factory fallback: always available, no DLL needed ---
        self._ff_calendar = None
        try:
            from data.forexfactory_provider import ForexFactoryProvider
            self._ff_calendar = ForexFactoryProvider()
        except Exception as e:
            logging.debug("Forex Factory calendar provider unavailable: %s", e)

        # The event gate asks for .get_events(limit, min_importance, currencies).
        # Neither the MT5 Python package (no calendar API) nor the DLL wrapper
        # provides it, so when the DLL path is unusable the gate resolved
        # UNAVAILABLE on every cycle and fail-closed every proposal. Promote the
        # ForexFactory provider, which does implement the duck-type, so
        # collect_calendar_event_rows() has a working source.
        if self.calendar is None and self._ff_calendar is not None:
            self.calendar = self._ff_calendar
        
        self.dom = DOMCollector(client) if client else None
        self.trade_manager = None

        # --- Engine cache: instantiated once, reused every cycle ---
        self._alpha_rotation_engine = None
        self._alpha_decay_monitor = None
        self._hierarchical_portfolio_engine = None
        self._auto_quant_tools = None
        self._regime_duration_tracker = None
        self._regime_alpha_selector = None
        self._shared_pulse_cache: Dict[str, Any] = {}

    def _state_store(self):
        """Authoritative TradingStore via the risk manager when attached (else None).

        Lets lazily-constructed engines persist to the single source of truth
        without threading a store through every provider call site.
        """
        return getattr(self.risk, "store", None)

    def _load_discovered_alpha_candidates(self) -> List[Any]:
        """Load validated expression alphas persisted by core.alpha_discovery.

        The single connection point between alpha discovery and alpha rotation:
        discovery persists its validated candidates to alpha_discovery_state.json;
        this reads them into SourceCandidate objects for the rotation engine,
        which evaluates them under the SAME unchanged promotion gates. Read
        failures return an empty list — never mock, never fabricate candidates.
        """
        out: List[Any] = []
        try:
            if not os.path.exists(DISCOVERY_STATE_FILE):
                return out
            with open(DISCOVERY_STATE_FILE, "r", encoding="utf-8") as _f:
                _disc_state = json.load(_f)
            _va = _disc_state.get("validated_alphas") or []
            for _a in _va:
                if not isinstance(_a, dict):
                    continue
                _name = _a.get("name") or ""
                _expr = _a.get("expression") or ""
                if not _name or not _expr:
                    continue
                try:
                    from core.alpha_rotation import SourceCandidate
                    out.append(SourceCandidate(
                        name=str(_name),
                        ic_mean=float(_a.get("ic_mean", 0.0) or 0.0),
                        ic_ir=float(_a.get("ic_ir", 0.0) or 0.0),
                        source="discovery",
                        expression=str(_expr),
                        status=str(_a.get("status") or "VALIDATED"),
                    ))
                except (TypeError, ValueError):
                    continue
        except Exception as exc:
            logging.debug("Discovered alpha candidates unavailable: %s", exc)
        return out

    def set_trade_manager(self, trade_manager) -> None:
        """Attach a TradeManager instance to this provider.

        Called by the orchestrator after construction to wire the trade manager
        for order execution and position tracking.
        """
        self.trade_manager = trade_manager

    def check_impact_events(self, minutes: int = 15) -> bool:
        mt5_inst = _get_mt5()
        if self.calendar and mt5_inst is not None:
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                start = now - timedelta(minutes=minutes)
                end = now + timedelta(minutes=minutes)
                events = mt5_inst.calendar_get(start, end)
                if events:
                    for e in events:
                        if getattr(e, "importance", 0) >= 2:
                            logging.warning(f"IMPACT EVENT DETECTED: {e.event} ({e.currency}) at {e.time}")
                            return True
            except Exception as e:
                logging.getLogger("MarketData").warning("MT5 calendar check failed: %s", e)

        # --- Forex Factory fallback ---
        if self._ff_calendar is not None:
            try:
                ff_events = self._ff_calendar.get_upcoming_high_impact_events(
                    window_minutes=minutes
                )
                if ff_events:
                    logging.warning("IMPACT EVENT DETECTED (ForexFactory): %d high-impact events nearby", len(ff_events))
                    return True
            except Exception as e:
                logging.getLogger("MarketData").warning("ForexFactory calendar check failed: %s", e)

        if not self.calendar and not self._ff_calendar:
            logging.getLogger("MarketData").warning("Calendar unavailable; fail-closed event blackout")
            return True
        return False

    def place_market_order(self, symbol: str, side: str, volume: float,
                           sl: Optional[float], tp: Optional[float], comment: str, atr: Optional[float] = None, paper: Optional[Any] = None,
                           magic: int = 0, validation_result: Optional[Any] = None,
                           setup_key: str = "", risk_amount: float = 0.0,
                           intent_key: Optional[str] = None) -> Dict[str, Any]:
        log = logging.getLogger("MarketData")
        log.info(f"Placing order: {side} {volume} {symbol} (Paper={paper is not None})")

        # --- Ψ GUARDIAN HARD GATE: Cannot be countermanded by any agent ---
        # If Ψ is tripped, NO order passes — flatten-only mode active.
        try:
            from core.external_guardian import get_guardian
            _guardian = get_guardian()
            if _guardian.is_tripped:
                log.warning("Ψ GUARDIAN TRIPPED — REJECTING ORDER: %s %s %s (severity=%s)",
                            side, volume, symbol, _guardian.trip_severity.value if _guardian.trip_severity else "?")
                return {
                    "retcode": 0, "order": 0, "rejected": True,
                    "rejection_reason": f"Ψ_GUARDIAN_TRIPPED:{_guardian.trip_severity.value if _guardian.trip_severity else 'UNKNOWN'}",
                    "source": "ExternalGuardian",
                }
        except ImportError:
            pass  # Guardian not available — continue without gate
        except Exception as _g_exc:
            # Fail CLOSED. The guardian is a pre-dispatch kill authority; if its
            # own check faults, continuing means a broken guardian silently
            # stops gating. Only the ImportError above is a legitimate
            # "not installed" pass-through.
            log.error("Guardian check failed (fail-closed): %s", _g_exc)
            return {"retcode": 0, "order": 0, "rejected": True,
                    "rejection_reason": f"GUARDIAN_CHECK_ERROR: {str(_g_exc)[:160]}",
                    "source": "ExternalGuardian"}

        # --- G1: Execution Safety Pre-Trade Gate (CRITICAL) ---
        # Must pass latency, spread, and drawdown checks before ANY MT5 dispatch.
        # Paper mode skips broker-dependent checks but still evaluates drawdown.
        if not paper:
            try:
                from core.execution_safety_supervisor import get_execution_safety_supervisor
                _safety = get_execution_safety_supervisor()
                _tick = None
                # Time the tick fetch: it is a real broker round-trip and the only
                # honest latency signal available before dispatch. This used to be a
                # hardcoded 50.0, so the supervisor's 250ms guard never saw real
                # latency on the market-order path.
                _latency = 0.0
                try:
                    _lat_t0 = time.perf_counter()
                    _tick = _symbol_tick(symbol)
                    _latency = (time.perf_counter() - _lat_t0) * 1000.0
                except Exception:
                    pass
                _current_spread = 0.0
                if _tick is not None:
                    bid = float(getattr(_tick, 'bid', 0) or 0)
                    ask = float(getattr(_tick, 'ask', 0) or 0)
                    if bid > 0 and ask > 0:
                        _current_spread = (ask - bid) / max(bid, 1e-10) * 10000
                _safety_result = _safety.validate_pre_trade_safety(
                    symbol=symbol,
                    requested_volume=float(volume),
                    current_spread_pips=_current_spread,
                    historical_avg_spread_pips=max(_current_spread * 0.8, 0.5),
                    roundtrip_latency_ms=_latency,
                )
                if not _safety_result.get("is_approved", False):
                    _reject_reason = _safety_result.get("rejection_reason", "unknown")
                    log.warning("EXECUTION SAFETY REJECTED: %s %s — %s", side, symbol, _reject_reason)
                    return {"retcode": 0, "order": 0, "rejected": True,
                            "rejection_reason": _reject_reason, "source": "ExecutionSafetySupervisor"}
                log.debug("Execution safety PASSED for %s %s", symbol, side)
            except Exception as _safety_exc:
                # Fail CLOSED. core/order_execution.py enforces this exact control
                # closed on the pending-order path; letting the market-order path
                # continue on an exception would mean a supervisor fault silently
                # disables the gate for one order type and not the other.
                log.error("Execution safety gate failed closed: %s", _safety_exc)
                return {"retcode": 0, "order": 0, "rejected": True,
                        "rejection_reason": f"EXECUTION_SAFETY_GATE_ERROR: {str(_safety_exc)[:160]}",
                        "source": "ExecutionSafetySupervisor"}

        # --- UrgencySOR: compute alpha-decay urgency to annotate the order ---
        try:
            from core.urgency_sor import UrgencySmartOrderRouter
            _urg = UrgencySmartOrderRouter()
            _atr = float(atr) if atr else 0.0
            # Use ATR as volatility proxy; OFI/depth from latest DOM snapshot
            dom = getattr(self, '_last_dom', {}) or {}
            _ofi = float(dom.get('order_flow_imbalance', 0.0))
            _depth = float(dom.get('depth', 100.0))
            from core.urgency_sor import calculate_alpha_decay_urgency_score
            _urgency = calculate_alpha_decay_urgency_score(
                instantaneous_volatility=_atr, order_flow_imbalance=_ofi,
                order_book_depth=_depth, elapsed_time_seconds=0.0)
            log.debug("UrgencySOR score=%.3f for %s %s", _urgency, symbol, side)
        except Exception as _urg_exc:
            log.debug("UrgencySOR skipped: %s", _urg_exc)
        return place_market_order(symbol, side, volume, sl, tp, comment, paper=paper, atr=atr, magic=magic,
                                  validation_result=validation_result, setup_key=setup_key,
                                  risk_amount=risk_amount, intent_key=intent_key)

    def place_pending_order(self, symbol: str, otype: str, price: float, volume: float,
                            sl: Optional[float], tp: Optional[float], comment: str,
                            paper: Optional[Any] = None, magic: int = 0,
                            expires_at: Optional[datetime] = None, validation_result: Optional[Any] = None,
                            setup_key: str = "", risk_amount: float = 0.0,
                            intent_key: Optional[str] = None,
                            account_equity: Optional[float] = None,
                            token: Optional[Any] = None) -> Dict[str, Any]:
        log = logging.getLogger("MarketData")
        # --- Ψ GUARDIAN HARD GATE: Cannot be countermanded by any agent ---
        try:
            from core.external_guardian import get_guardian
            _guardian = get_guardian()
            if _guardian.is_tripped:
                log.warning("Ψ GUARDIAN TRIPPED — REJECTING PENDING ORDER: %s %s %s @ %s",
                            otype, symbol, price, _guardian.trip_severity.value if _guardian.trip_severity else "?")
                return {
                    "retcode": 0, "order": 0, "rejected": True,
                    "rejection_reason": f"Ψ_GUARDIAN_TRIPPED:{_guardian.trip_severity.value if _guardian.trip_severity else 'UNKNOWN'}",
                    "source": "ExternalGuardian",
                }
        except ImportError:
            pass
        except Exception as _g_exc:
            log.warning("Ψ Guardian check failed (fail-closed): %s", _g_exc)
            return {
                "retcode": 0, "order": 0, "rejected": True,
                "rejection_reason": f"Ψ_GUARDIAN_CHECK_FAILED:{type(_g_exc).__name__}",
                "source": "ExternalGuardian",
            }

        log.info(
            "Placing planned %s %s @ %s (%s)", otype, symbol, price, "paper" if paper else "MT5"
        )
        return place_pending_order(symbol, otype, price, volume, sl, tp, comment,
                                   paper=paper, magic=magic, expires_at=expires_at,
                                   validation_result=validation_result, setup_key=setup_key,
                                   risk_amount=risk_amount, intent_key=intent_key,
                                   account_equity=account_equity, token=token)

    def check_data_sources(self) -> Dict[str, str]:
        status = {
            "MT5 Connection": "ONLINE" if self.client else "OFFLINE (explicit fixture/replay required)",
            "MT5 Calendar": "ONLINE" if self.calendar else ("ONLINE_FF" if self._ff_calendar else "OFFLINE"),
            "Market Data (OHLC/DOM)": "ONLINE" if self.client else "UNAVAILABLE (explicit fixture required)",
        }
        if self.api_manager:
            api_status = self.api_manager.get_status()
            status.update(api_status)
        else:
            status["NewsAPI"] = "OFFLINE"
            status["FRED"] = "OFFLINE"
            status["SEC"] = "OFFLINE"
        return status
    
    def _analyze_volume(self, df: pd.DataFrame) -> Dict[str, Any]:
        try:
            current_vol = int(df["tick_volume"].iloc[-1])
            avg_vol_20 = int(df["tick_volume"].tail(20).mean())
            ratio = current_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
            recent_5_avg = df["tick_volume"].tail(5).mean()
            prev_5_avg = df["tick_volume"].tail(10).head(5).mean()
            trend = "INCREASING" if recent_5_avg > prev_5_avg * 1.1 else "DECREASING" if recent_5_avg < prev_5_avg * 0.9 else "NEUTRAL"
            return {
                "current_volume": current_vol,
                "avg_volume_20": avg_vol_20,
                "volume_ratio": ratio,
                "trend": trend,
            }
        except Exception as e:
            logging.warning(f"Volume analysis failed: {e}")
            return {"current_volume": 0, "avg_volume_20": 0, "volume_ratio": 1.0, "trend": "UNKNOWN"}
    
    def _calculate_hma(self, series: pd.Series, period: int = 200) -> pd.Series:
        try:
            half_length = int(period / 2)
            sqrt_length = int(np.sqrt(period))
            wma_half = wma(series, half_length)
            wma_full = wma(series, period)
            raw_hma = 2 * wma_half - wma_full
            return wma(raw_hma, sqrt_length)
        except Exception as e:
            logging.warning(f"HMA calculation failed: {e}")
            return pd.Series(index=series.index, dtype=float)

    def _identify_support_resistance(self, df: pd.DataFrame, window: int = 20) -> List[float]:
        levels = []
        try:
            for i in range(window, len(df) - window):
                is_support = df['low'].iloc[i] < df['low'].iloc[i-window:i].min() and \
                             df['low'].iloc[i] < df['low'].iloc[i+1:i+window+1].min()
                is_resistance = df['high'].iloc[i] > df['high'].iloc[i-window:i].max() and \
                                df['high'].iloc[i] > df['high'].iloc[i+1:i+window+1].max()
                if is_support:
                    levels.append(df['low'].iloc[i])
                if is_resistance:
                    levels.append(df['high'].iloc[i])
            if not levels:
                return []
            levels.sort()
            clustered = []
            current_cluster = [levels[0]]
            for i in range(1, len(levels)):
                if levels[i] <= current_cluster[0] * 1.001:
                    current_cluster.append(levels[i])
                else:
                    clustered.append(sum(current_cluster) / len(current_cluster))
                    current_cluster = [levels[i]]
            clustered.append(sum(current_cluster) / len(current_cluster))
            return clustered[-3:]
        except Exception as e:
            logging.warning(f"S/R identification failed: {e}")
            return []

    def fetch_multi_timeframe_data(self, symbol: str) -> Dict[str, Any]:
        fetch_fn = _get_fetch_ohlc()
        timeframes = ["D1", "H4", "H1", "M30", "M15", "M5"]
        data = {}
        for tf in timeframes:
            try:
                df = fetch_fn(symbol, tf, count=50)
                if df.empty:
                    continue
                df = compute_indicators(df, ["RSI", "MACD", "EMA(50)", "ATR(14)"])
                data[tf] = {
                    "current_price": df["close"].iloc[-1],
                    "ohlc_last_3": ohlc_records(df.tail(3)),
                    "rsi": df["rsi_14"].iloc[-1],
                    "macd": {
                        "line": df["macd_line"].iloc[-1],
                        "signal": df["macd_signal"].iloc[-1],
                        "hist": df["macd_hist"].iloc[-1],
                    },
                    "ema_50": df["ema_50"].iloc[-1],
                    "atr": df["atr_14"].iloc[-1],
                    "trend": "BULLISH" if df["close"].iloc[-1] > df["ema_50"].iloc[-1] else "BEARISH",
                }
            except Exception as e:
                logging.getLogger("MarketData").warning(f"Failed to fetch {tf} data: {e}")
        return data

    def analyze_confluence(self, symbol: str, news_events: List[Dict] = None, volume_trend: str = "NEUTRAL") -> Dict[str, Any]:
        log = logging.getLogger("MarketData")
        fetch_fn = _get_fetch_ohlc()
        timeframes_to_analyze = ["D1", "H1", "M30"]
        confluence_score = 0
        max_score = (len(timeframes_to_analyze) * 3) + 3
        breakdown = {}
        
        try:
            current_price = None
            for tf in timeframes_to_analyze:
                try:
                    df = fetch_fn(symbol, tf, count=100)
                    if df.empty:
                        continue
                    if current_price is None:
                        current_price = df["close"].iloc[-1]
                    df["ma_50"] = df["close"].rolling(50).mean()
                    df["ma_200"] = df["close"].rolling(200).mean()
                    df["rsi_14"] = rsi(df["close"], 14)
                    sr_levels = self._identify_support_resistance(df)
                    tf_signals = {
                        "near_sr": False,
                        "ma_alignment": False,
                        "rsi_signal": False,
                    }
                    for level in sr_levels:
                        if abs(current_price - level) / current_price < 0.001:
                            tf_signals["near_sr"] = True
                            confluence_score += 1
                            break
                    ma_50 = df["ma_50"].iloc[-1]
                    ma_200 = df["ma_200"].iloc[-1]
                    if not pd.isna(ma_50) and not pd.isna(ma_200):
                        if ma_50 > ma_200:
                            tf_signals["ma_alignment"] = "BULLISH"
                            confluence_score += 1
                        elif ma_50 < ma_200:
                            tf_signals["ma_alignment"] = "BEARISH"
                            confluence_score += 1
                    rsi_val = df["rsi_14"].iloc[-1]
                    if not pd.isna(rsi_val):
                        if rsi_val < 30:
                            tf_signals["rsi_signal"] = "OVERSOLD"
                            confluence_score += 1
                        elif rsi_val > 70:
                            tf_signals["rsi_signal"] = "OVERBOUGHT"
                            confluence_score += 1
                    breakdown[tf] = tf_signals
                except Exception as e:
                    log.warning(f"Confluence analysis failed for {tf}: {e}")
                    breakdown[tf] = {"error": str(e)}
            
            m30_align = breakdown.get("M30", {}).get("ma_alignment", "NEUTRAL")
            news_score = 0
            if news_events:
                high_impact = [n for n in news_events if n.get('importance', 0) >= 2]
                if high_impact:
                    news_score = 1
                    confluence_score += 1
            breakdown["news_bonus"] = news_score
            
            vol_score = 0
            if volume_trend == "INCREASING":
                vol_score = 1
                confluence_score += 1
            breakdown["volume_bonus"] = vol_score

            return {
                "score": confluence_score,
                "max_score": max_score,
                "breakdown": breakdown,
                "current_price": current_price,
            }
        except Exception as e:
            log.error(f"Confluence analysis failed: {e}")
            return {"score": 0, "max_score": 9, "breakdown": {}, "error": str(e)}

    def _get_symbol_keywords(self, symbol: str) -> List[str]:
        try:
            import configparser
            cp = configparser.ConfigParser()
            cp.read("config.ini")
            if "symbol_keywords" in cp:
                keywords_str = cp["symbol_keywords"].get(symbol, "")
                if keywords_str:
                    return [k.strip() for k in keywords_str.split(",")]
        except Exception as e:
            logging.debug(f"Could not load keywords for {symbol}: {e}")
        return [symbol]

    def _get_higher_timeframe(self, tf: str) -> str:
        mapping = {
            "M1": "M5", "M5": "M15", "M15": "H1", "M30": "H4",
            "H1": "H4", "H4": "D1", "D1": "W1",
        }
        return mapping.get(tf, "H1")

    def enable_paper_account_evidence(self) -> None:
        """Enable the honestly-labeled PAPER_SIMULATED account backstop.

        Called only by the orchestrator for paper journal sessions. Paper
        mode performs no authenticated MT5 login, so ``get_account_info()``
        legitimately yields nothing and the decision protocol would
        otherwise deadlock every cycle on "missing account evidence ->
        HOLD". The backstop surfaces the same seeded RiskManager state the
        sizing/heat math already uses, clearly labeled as simulated so the
        model can never mistake it for broker evidence.
        """
        self.paper_account_evidence = True

    def fetch_data(self, symbol: str, timeframe: str, cycle_num: int = 0) -> Dict[str, Any]:
        log = logging.getLogger("MarketData")
        fetch_fn = _get_fetch_ohlc()
        if self.client is None:
            # Paper mode intentionally has no authenticated MT5Client, but its
            # analysis path consumes REAL broker data through the module-level
            # terminal session established read-only at boot (no credential
            # login, no order path).  If no terminal is attached there is no
            # data plane at all: fail closed exactly as before.
            try:
                _mt5 = _get_mt5()
                _terminal = _mt5.terminal_info() if _mt5 is not None else None
            except Exception:
                _terminal = None
            if _terminal is None:
                raise RuntimeError(
                    "paper market data is unavailable; refusing synthetic candles/DOM/news; "
                    "provide an explicit broker-replay or test fixture"
                )
            log.info("Paper data plane: using read-only terminal session (no credential login)")
        if timeframe == "AUTO":
            timeframes_to_check = ["D1", "H4", "H1", "M30"]
            best_timeframe = "M30"
            best_score = 0
            log.info(f"AUTO mode: Analyzing timeframes {timeframes_to_check} for {symbol}")
            for tf in timeframes_to_check:
                try:
                    test_df = fetch_fn(symbol, tf, count=50)
                    if len(test_df) >= 20:
                        score = len(test_df)
                        if tf == "H1":
                            score += 10
                        if score > best_score:
                            best_score = score
                            best_timeframe = tf
                except Exception as e:
                    log.debug(f"Could not fetch {tf} for {symbol}: {e}")
                    continue
            timeframe = best_timeframe
            log.info(f"AUTO mode selected: {timeframe} for {symbol}")

        df = fetch_fn(symbol, timeframe, count=250)
        news_events = []
        htf = self._get_higher_timeframe(timeframe)
        try:
            df_htf = fetch_fn(symbol, htf, count=100)
            df_htf["ema_50"] = ema(df_htf["close"], 50)
            df_htf["rsi_14"] = rsi(df_htf["close"], 14)
            htf_trend = "BULLISH" if df_htf["close"].iloc[-1] > df_htf["ema_50"].iloc[-1] else "BEARISH"
            htf_data = {
                "timeframe": htf,
                "trend": htf_trend,
                "rsi": df_htf["rsi_14"].iloc[-1],
            }
        except Exception as e:
            log.warning(f"HTF analysis failed: {e}")
            htf_data = {"timeframe": htf, "trend": "UNKNOWN", "rsi": 0.0}

        dom_collector = DOMCollector(symbol)
        dom_collector.subscribe()
        # Retry loop: poll up to 5x with 200ms gaps (1s max) instead of blind sleep(1)
        dom_data = {"available": False}
        for _dom_attempt in range(5):
            dom_data = dom_collector.get_snapshot()
            if dom_data.get("available"):
                break
            time.sleep(0.2)
        dom_collector.unsubscribe()
        
        # ── Liquidity Vacuum Detection (Blind Spot audit) ────────────────────
        if _LiqVacDetector is not None:
            try:
                _liq_detector = _LiqVacDetector()
                _liq_health = _liq_detector.analyze(dom_data, symbol)
                dom_data["liquidity_health"] = {
                    "composite_score": _liq_health.composite_score,
                    "depth_score": _liq_health.depth_score,
                    "spread_score": _liq_health.spread_score,
                    "alerts": [str(a) for a in _liq_health.alerts],
                }
            except Exception as _liq_exc:
                log.debug("LiquidityVacuumDetector skipped: %s", _liq_exc)

        # ── ADV Tracker (Blind Spot audit) ──────────────────────────────────
        if _ADVTrackerMod is not None:
            try:
                if not hasattr(self, '_adv_tracker'):
                    self._adv_tracker = _ADVTrackerMod()
                _volume = dom_data.get("last_volume", 0) or volume_data.get("volume", 0) or 0
                _price = float(dom_data.get("best_bid", 0) or dom_data.get("best_ask", 0) or 0)
                if _volume and _volume > 0:
                    self._adv_tracker.update(symbol, float(_volume), _price)
                    _adv_info = self._adv_tracker.get_adv(symbol)
                    if _adv_info:
                        dom_data["adv_info"] = {
                            "adv_24h": _adv_info.adv_24h,
                            "adv_20d": _adv_info.adv_20d,
                            "symbol": symbol,
                        }
            except Exception as _adv_exc:
                log.debug("ADVTracker skipped: %s", _adv_exc)
        
        if dom_data.get('best_bid', 0.0) <= 0.0001 or dom_data.get('best_ask', 0.0) <= 0.0001:
            dom_data["available"] = False
            log.warning("MT5 DOM unavailable; retaining missing-data state")

        df["hma_200"] = self._calculate_hma(df["close"], 200)
        current_price = df["close"].iloc[-1]
        hma_val = df["hma_200"].iloc[-1] if not df["hma_200"].empty else 0.0
        trend_status = "BULLISH" if current_price > hma_val else "BEARISH"
        sr_levels = self._identify_support_resistance(df)
        volume_data = self._analyze_volume(df)

        confluence_data = self.analyze_confluence(
            symbol, 
            news_events=news_events, 
            volume_trend=(volume_data or {}).get("trend", "NEUTRAL"),
        ) if self.client else {"score": 0, "max_score": 12, "breakdown": {}}

        # --- Alpha rotation: use active set from rotation engine ---
        _rotation_active = []
        try:
            if self._alpha_rotation_engine is None:
                from core.alpha_rotation import AlphaRotationEngine
                self._alpha_rotation_engine = AlphaRotationEngine(store=self._state_store())
            _rotation_active = self._alpha_rotation_engine.get_active_set()
        except Exception as exc:
            logger.debug("news ingestion failed: %s", exc)

        # Convert rotation active set (alpha_012) to indicator requests (ALPHA012)
        _alpha_requests = ["RSI", "MACD", "EMA(50)", "ATR(14)", "ADX(14)"]
        if _rotation_active:
            try:
                from core.alpha_factors import _ALPHA_REGISTRY as _valid_alphas
            except Exception:
                _valid_alphas = None
            for col in _rotation_active:
                num = col.replace("alpha_", "").replace("ALPHA", "")
                req_key = f"ALPHA{num.zfill(3)}".upper()
                if _valid_alphas is None or req_key in _valid_alphas:
                    _alpha_requests.append(req_key)
        else:
            # Fallback: full default set
            _alpha_requests.extend([
                "ALPHA012", "ALPHA053", "ALPHA055", "ALPHA034", "ALPHA017",
                "ALPHA009", "ALPHA041", "ALPHA101", "ALPHA030", "ALPHA035",
                "ALPHA014", "ALPHA022", "ALPHA025", "ALPHA019",
                "ALPHA023", "ALPHA036", "ALPHA037", "ALPHA043",
            ])

        if "volume" not in df.columns and "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"]

        try:
            df = compute_indicators(df, _alpha_requests)
        except Exception as e:
            log.warning(f"compute_indicators failed: {e}; falling back to standard indicators")
            try:
                df = compute_indicators(df, ["RSI", "MACD", "EMA(50)", "ATR(14)", "ADX(14)"])
            except Exception as e2:
                log.error(f"standard indicators calculation failed: {e2}")

        # --- Honesty: enforce latency_bars=1 for alpha factors (no zero-bar clairvoyance) ---
        # Alphas computed from close[t] must not be traded at close[t]; shift by 1 bar
        # so signal at t uses prior bar's alpha. Backtest honesty gap Author-P (2005):§3.
        try:
            _alpha_cols_raw = [c for c in df.columns if c.startswith("alpha_")]
            if _alpha_cols_raw:
                df[_alpha_cols_raw] = df[_alpha_cols_raw].shift(1)
        except Exception as exc:
            logger.debug("file close failed: %s", exc)

        acc_info = {}
        if self.client and hasattr(self.client, "get_account_info"):
            try:
                acc_info = self.client.get_account_info()
            except Exception as exc:
                logger.debug("file close failed: %s", exc)
        # Paper backstop: enabled only for PAPER journal sessions (no
        # authenticated MT5 login -> account evidence is legitimately
        # absent). Surface the seeded risk state instead of an empty object
        # so the decision protocol does not deadlock on missing account
        # evidence. Live/demo never enable the flag and keep failing closed
        # with an empty object.
        if not acc_info and self.paper_account_evidence:
            _bal = float(getattr(self.risk, "balance", 0.0) or 0.0)
            # RiskManager has no ``equity`` attribute (only equity_floor_fraction),
            # so the old getattr(self.risk, "equity", _bal) always fell back to
            # the balance and paper evidence reported balance as equity - open
            # floating P&L never surfaced here. current_equity() is the real
            # accessor: MT5, else the injected paper venue, else the snapshot.
            try:
                _eq = float(self.risk.current_equity())
            except Exception:
                _eq = _bal
            if not (_eq > 0.0):
                _eq = _bal
            if _bal > 0.0 or _eq > 0.0:
                acc_info = {
                    "server": "PAPER_SIMULATED", "currency": "USD", "leverage": 100,
                    "balance": _bal, "equity": _eq, "margin": 0.0,
                    "margin_free": _eq, "margin_level": 0.0,
                    "mode": "PAPER_SIMULATED", "source": "risk_manager_equity",
                    "simulated": True,
                }

        balance = float(acc_info.get("balance", self.risk.balance) or self.risk.balance)
        equity = float(acc_info.get("equity") or balance)
        self.risk.update_account(balance, equity)

        now_ts = time.time()
        cached_cal = self._shared_pulse_cache.get(symbol)
        if cached_cal and (now_ts - float(cached_cal.get("ts", 0))) < 5.0:
            news_events = cached_cal.get("news_events", [])
            calendar_source = cached_cal.get("calendar_source", "none")
        else:
            news_events, calendar_source = collect_calendar_event_rows(self.calendar, self.client)
            self._shared_pulse_cache[symbol] = {
                "ts": now_ts,
                "news_events": news_events,
                "calendar_source": calendar_source,
            }

        # --- Hierarchical Meta-Portfolio Regime Engine (moved BEFORE ensemble so regime multipliers are live) ---
        regime_allocation = {}
        try:
            if self._hierarchical_portfolio_engine is None:
                from core.hierarchical_portfolio import HierarchicalMetaPortfolioEngine
                self._hierarchical_portfolio_engine = HierarchicalMetaPortfolioEngine(
                    base_risk_pct=self.risk.risk_per_trade,
                    min_master_ev=0.20,
                    portfolio_heat_cap_r=4.0,
                )
            _hp_engine = self._hierarchical_portfolio_engine
            _close_prices = df["close"].dropna().tolist()
            _adx_val = float(df["adx_14"].iloc[-1]) if "adx_14" in df and len(df) > 0 else 25.0
            _atr_history = df["atr_14"].dropna().tolist() if "atr_14" in df else []
            _atr_current = float(df["atr_14"].iloc[-1]) if "atr_14" in df and len(df) > 0 else 0.0010
            _strategy_type = "TREND_MOMENTUM"
            _open_r = 0.0
            if hasattr(self.risk, "open_positions") and self.risk.open_positions:
                _open_r = sum(abs(float(p.get("risk_r", 0) or 0)) for p in self.risk.open_positions if isinstance(p, dict))
            _hp_result = _hp_engine.evaluate(
                prices=_close_prices,
                current_adx=_adx_val,
                atr_history=_atr_history,
                current_atr=_atr_current,
                strategy_type=_strategy_type,
                open_portfolio_r=_open_r,
            )
            regime_allocation = {
                "approved": _hp_result["approved"],
                "regime": _hp_result["regime"],
                "allocation": _hp_result["allocation"],
                "applied_risk_multiplier": _hp_result["applied_risk_multiplier"],
                "effective_risk_pct": _hp_result["effective_risk_pct"],
                "reasons": _hp_result["reasons"],
            }
            log.info("Regime engine: regime=%s approved=%s multiplier=%.2f",
                     _hp_result["regime"]["primary_regime"], _hp_result["approved"], _hp_result["applied_risk_multiplier"])
        except Exception as e:
            log.debug(f"Hierarchical portfolio regime engine skipped: {e}")

        # --- Correlation & Copula Tail Heat wiring (per-cycle, not just advisory) ---
        try:
            # Record completed-bar returns for correlation engine (point-in-time, no forming bar)
            from intelligence.correlation_risk import CorrelationRisk
            cr = CorrelationRisk(db_path="trading_state.db")
            # Build bars from df excluding last (forming) bar
            _completed = ohlc_records(df.iloc[:-1]) if len(df) > 10 else []
            if _completed:
                cr.record_completed_bars(symbol, timeframe, _completed)
            # Copula tail: use REAL multi-asset returns from CorrelationRisk
            # Falls back to single-asset proxy only if fewer than 2 assets have stored returns
            try:
                from core.copula_tail_risk import clayton_copula_tail_risk
                import numpy as np

                # Attempt real multi-asset matrix from CorrelationRisk stored returns
                _multi = cr.get_multi_asset_returns_matrix(timeframe, lookback=60, min_assets=2)
                if _multi is not None:
                    mat, _cop_assets = _multi
                    log.info("Copula multi-asset matrix: %d assets, %d samples — %s",
                             len(_cop_assets), len(mat), ", ".join(_cop_assets))
                else:
                    # Fallback: single-asset lagged returns (tail autocorrelation proxy)
                    rets = df["close"].pct_change().dropna().tail(60).values
                    if len(rets) >= 20:
                        mat = np.column_stack([rets[:-1], rets[1:]])
                        log.debug("Copula fallback: single-asset lagged proxy (%d samples)", len(mat))
                    else:
                        mat = None

                if mat is not None and len(mat) >= 20:
                    cop = clayton_copula_tail_risk(mat, normal_heat_budget_r=4.0, crisis_heat_budget_r=1.0)
                    # Wire to RiskManager heat budget
                    adj = float(cop.get("adjusted_heat_budget_r", 4.0))
                    if hasattr(self.risk, "set_heat_budget_r") and abs(adj - 4.0) > 0.01:
                        self.risk.set_heat_budget_r(adj)
                        log.info("Copula tail heat wired: %s tail_dep=%.3f -> heat %.1fR",
                                 cop.get("tail_risk_state"), cop.get("avg_tail_dependence"), adj)
            except Exception as ce:
                log.debug(f"Copula heat wiring skipped: {ce}")
        except Exception as e:
            log.debug(f"Correlation/Copula wiring skipped: {e}")

        # --- Collect alpha factor columns + ensemble (after regime & shift) ---
        alpha_cols = [c for c in df.columns if c.startswith("alpha_")]
        alpha_snapshot = {}
        for col in alpha_cols:
            vals = df[col].dropna()
            if len(vals) > 0:
                alpha_snapshot[col] = {
                    "current": float(vals.iloc[-1]),
                    "mean_20": float(vals.tail(20).mean()),
                    "std_20": float(vals.tail(20).std()) if len(vals) >= 20 else float(vals.std()),
                }

        # --- Compute alpha ensemble composite signal (regime now live, alphas shifted) ---
        alpha_ensemble = {}
        try:
            from core.alpha_ensemble import combine_alphas, interpret_composite, format_quality_report
            if alpha_cols and len(df) >= 60:
                # Pass previous cycle's decay health to suppress dead/degraded alphas
                prev_decay = getattr(self, '_prev_decay_health', None)
                ensemble_result = combine_alphas(
                    df, alpha_cols,
                    forward_return_periods=1,
                    ic_window=60,
                    weighting="ic_ir",
                    regime=regime_allocation.get("regime"),
                    orthogonalize=True,
                    ortho_method="ols",
                    ortho_rolling_window=120,
                    decay_health=prev_decay,
                )
                interp = interpret_composite(ensemble_result)
                alpha_ensemble = {
                    "composite_signal": interp["signal"],
                    "composite_strength": float(interp["strength"]),
                    "composite_confidence": interp["confidence"],
                    "composite_value": float(interp["composite_value"]),
                    "raw_value": float(interp["raw_value"]),
                    "n_alphas_used": ensemble_result.n_alphas_used,
                    "overall_ic": float(ensemble_result.ic_mean_overall),
                    "weights": {k: float(v) for k, v in ensemble_result.alpha_weights.items()},
                    "top_contributors": [
                        {"alpha": c["alpha"], "weight": float(c["weight"]), "ic_ir": float(c["ic_ir"])}
                        for c in interp["contributing_alphas"][:5]
                    ],
                }
        except Exception as e:
            log.debug(f"Alpha ensemble computation skipped: {e}")

        # --- Regime Alpha Selector: regime-conditioned alpha subset selection ---
        regime_alpha_selection = {}
        try:
            from core.regime_alpha_selector import RegimeAlphaSelector
            if self._regime_alpha_selector is None:
                self._regime_alpha_selector = RegimeAlphaSelector()
            _current_regime = regime_allocation.get("regime", "UNKNOWN")
            if alpha_cols:
                _selection = self._regime_alpha_selector.select_alphas(_current_regime, alpha_cols)
                regime_alpha_selection = _selection.to_dict()
                # Feed IC performance back into selector for learning
                if alpha_ensemble and alpha_ensemble.get("weights"):
                    for _a_name, _a_weight in alpha_ensemble["weights"].items():
                        self._regime_alpha_selector.update_alpha_performance(
                            _a_name, _current_regime, _a_weight * alpha_ensemble.get("overall_ic", 0.02)
                        )
        except Exception as e:
            log.debug(f"Regime alpha selector skipped: {e}")

        # --- Alpha decay monitoring ---
        alpha_decay_health = {}
        try:
            if self._alpha_decay_monitor is None:
                from core.alpha_decay_monitor import AlphaDecayMonitor
                self._alpha_decay_monitor = AlphaDecayMonitor(
                    ic_window=60,
                    decay_threshold=0.02,
                    consecutive_bars=5,
                    alert_db_path="trading_state.db",
                    orthogonalize=True,
                    ortho_method="ols",
                    ortho_rolling_window=120,
                    store=self._state_store(),
                )
            if alpha_cols and len(df) >= 60:
                health = self._alpha_decay_monitor.update(df, alpha_cols)
                alerts = self._alpha_decay_monitor.check_decay()
                alpha_decay_health = {
                    "summary": self._alpha_decay_monitor.get_summary(),
                    "alerts": [
                        {"code": a.code, "severity": a.severity, "message": a.message}
                        for a in alerts
                    ],
                }
                # Store for next cycle's ensemble re-weighting
                self._prev_decay_health = alpha_decay_health["summary"].get("alphas", {})
        except Exception as e:
            log.debug(f"Alpha decay monitoring skipped: {e}")

        # --- Alpha rotation: update active set based on decay health ---
        alpha_rotation_status = {}
        try:
            if self._alpha_rotation_engine is None:
                from core.alpha_rotation import AlphaRotationEngine
                self._alpha_rotation_engine = AlphaRotationEngine(store=self._state_store())
            if alpha_decay_health:
                health_dict = alpha_decay_health.get("summary", {}).get("alphas", {})
                if health_dict:
                    _disc_candidates = self._load_discovered_alpha_candidates()
                    if _disc_candidates:
                        log.info(
                            "Alpha rotation feed: %d discovered candidate(s) offered for promotion evaluation",
                            len(_disc_candidates),
                        )
                    _rot_events = self._alpha_rotation_engine.update(
                        health_dict,
                        all_alpha_names=[c for c in df.columns if c.startswith("alpha_")],
                        discovered_candidates=_disc_candidates,
                    )
                    if _rot_events:
                        log.info(
                            "Alpha rotation: %d alphas rotated",
                            len(_rot_events),
                        )
                        for _ev in _rot_events:
                            try:
                                from operations.event_bridge import emit as _rot_emit
                                _rot_emit("ALPHA_ROTATED", {
                                    "removed_alpha": _ev.removed_alpha,
                                    "promoted_alpha": _ev.promoted_alpha,
                                    "reason": _ev.reason,
                                    "removed_ic_mean": _ev.removed_ic_mean,
                                    "promoted_ic_mean": _ev.promoted_ic_mean,
                                    "discovered": "discovered" in (_ev.reason or ""),
                                }, correlation_id="alpha-rotation")
                            except Exception:
                                pass
                    # Rejected discovered candidates are journaled here so the
                    # decision trail is visible even without a promotion event.
                    for _dd in self._alpha_rotation_engine.get_discovery_decisions():
                        if _dd.get("verdict") == "REJECT" and _dd.get("journaled"):
                            continue
                        try:
                            from operations.event_bridge import emit as _rej_emit
                            _rej_emit("ALPHA_DISCOVERY_PROMOTION_DECISION", {
                                "alpha": _dd.get("name"),
                                "verdict": _dd.get("verdict"),
                                "ic_mean": _dd.get("ic_mean"),
                                "threshold": _dd.get("threshold"),
                                "reason": _dd.get("reason"),
                                "source": _dd.get("source"),
                            }, correlation_id="alpha-rotation")
                            _dd["journaled"] = True
                        except Exception:
                            pass
                    # Materialize any promoted discovered expression: evaluate
                    # it on live data; record the outcome honestly (a failed
                    # evaluation never invents a column; failures are retried
                    # on later cycles with fresh data).
                    for _pname, _pend in list(
                        self._alpha_rotation_engine._pending_discovery_promotions.items()
                    ):
                        _pexpr = _pend.get("expression") or ""
                        try:
                            from core.alpha_discovery import SafeAlphaEvaluator
                            _series = SafeAlphaEvaluator().evaluate(_pexpr, df)
                        except Exception as _mat_exc:
                            _series = None
                            log.warning("Discovered alpha materialization evaluator error: %s", _mat_exc)
                        if _series is not None and len(_series) == len(df) and not _series.isna().all():
                            df[_pname] = _series
                            self._alpha_rotation_engine.record_discovery_materialized(
                                _pname, True, "column added to live dataframe"
                            )
                            log.info(
                                "Discovered alpha %s materialized: column added from expression (%.80s)",
                                _pname, _pexpr,
                            )
                            try:
                                from operations.event_bridge import emit as _mat_emit
                                _mat_emit("ALPHA_DISCOVERY_MATERIALIZED", {
                                    "alpha": _pname,
                                    "expression": _pexpr,
                                    "ic_mean": _pend.get("ic_mean"),
                                }, correlation_id="alpha-rotation")
                            except Exception:
                                pass
                        else:
                            _first_fail = not _pend.get("failed_attempts")
                            _pend["failed_attempts"] = int(_pend.get("failed_attempts", 0)) + 1
                            self._alpha_rotation_engine.record_discovery_materialized(
                                _pname, False,
                                "SafeAlphaEvaluator returned no usable series on live data",
                            )
                            if _first_fail:
                                log.warning(
                                    "Discovered alpha %s materialization FAILED: no usable series on live data (expression=%.80s)",
                                    _pname, _pexpr,
                                )
                                try:
                                    from operations.event_bridge import emit as _fail_emit
                                    _fail_emit("ALPHA_DISCOVERY_MATERIALIZATION_FAILED", {
                                        "alpha": _pname,
                                        "expression": _pexpr,
                                        "reason": "no usable series on live data",
                                    }, correlation_id="alpha-rotation")
                                except Exception:
                                    pass
                alpha_rotation_status = self._alpha_rotation_engine.get_status()
        except Exception as e:
            log.debug(f"Alpha rotation skipped: {e}")

        # (Regime already computed before ensemble — see above; skip duplicate)

        n_bars = len(df)
        indicators = {
            "rsi_14": df["rsi_14"].fillna(50).tolist() if "rsi_14" in df else [50.0] * n_bars,
            "macd": {
                "line": df["macd_line"].fillna(0).tolist() if "macd_line" in df else [0.0] * n_bars,
                "signal": df["macd_signal"].fillna(0).tolist() if "macd_signal" in df else [0.0] * n_bars,
                "histogram": df["macd_hist"].fillna(0).tolist() if "macd_hist" in df else [0.0] * n_bars,
            },
            "ema_50": df["ema_50"].fillna(0).tolist() if "ema_50" in df else [0.0] * n_bars,
            "atr_14": df["atr_14"].fillna(0).tolist() if "atr_14" in df else [0.0] * n_bars,
            "adx_14": df["adx_14"].fillna(0).tolist() if "adx_14" in df else [],
            "hma_200": df["hma_200"].fillna(0).tolist() if "hma_200" in df else [],
            "alpha_factors": alpha_snapshot,
            "alpha_ensemble": alpha_ensemble,
            "alpha_decay_health": alpha_decay_health,
            "alpha_rotation": alpha_rotation_status,
            "regime_allocation": regime_allocation,
        }

        # --- Auto-Trigger Quantitative Tools ---
        auto_quant_results = {}
        try:
            if self._auto_quant_tools is None:
                try:
                    from core.auto_quant_tools import AutoQuantTools
                except ImportError:
                    from core.auto_quant_tools import AutoQuantTools
                self._auto_quant_tools = AutoQuantTools(state_file="auto_quant_tools_state.json", store=self._state_store())
            auto_quant_results = self._auto_quant_tools.update(cycle_num, toon={
                "ohlc": ohlc_records(df),
                "context": {"symbol": symbol, "timeframe": timeframe},
            })
        except Exception as e:
            log.debug(f"Auto quant tools skipped: {e}")

        # --- Regime Duration Tracker ---
        regime_duration = {}
        try:
            if self._regime_duration_tracker is None:
                from core.regime_duration_tracker import RegimeDurationTracker
                self._regime_duration_tracker = RegimeDurationTracker(state_file="regime_duration_state.json", store=self._state_store())
            self._regime_duration_tracker.update(cycle_num, regime_allocation.get("regime", {}))
            regime_duration = self._regime_duration_tracker.get_status()
        except Exception as e:
            log.debug(f"Regime duration tracker skipped: {e}")

        api_news_events = []
        hot_topics = []
        if self.api_manager:
            try:
                keywords = self._get_symbol_keywords(symbol)
                api_news_events = self.api_manager.get_news(keywords, page_size=5)
                hot_topics = self.api_manager.get_hot_topics(limit=3)
            except Exception as e:
                log.warning(f"NewsAPI fetch failed: {e}")

        macro_data = {}
        if self.api_manager:
            try:
                fred_series = catalog_fred_series()
                macro_data = self.api_manager.get_fred_data(fred_series)
                if macro_data:
                    log.info(f"Fetched {len(macro_data)} FRED indicators")
            except Exception as e:
                log.warning(f"FRED fetch failed: {e}")

        multi_tf_data = self.fetch_multi_timeframe_data(symbol)

        fund_score = 0
        if api_news_events:
            bullish = sum(1 for n in api_news_events if n.get('sentiment') == 'BULLISH')
            bearish = sum(1 for n in api_news_events if n.get('sentiment') == 'BEARISH')
            fund_score = bullish - bearish

        if 'confluence_data' in locals():
            confluence_data['fundamental_score'] = fund_score
            confluence_data['news_sentiment'] = "BULLISH" if fund_score > 0 else "BEARISH" if fund_score < 0 else "NEUTRAL"

        source_identity = {}
        if self.client and acc_info:
            live_server = str(acc_info.get("server", "") or "").strip()
            live_login = str(acc_info.get("login", "") or "").strip()
            if live_server and live_login:
                source_identity = {
                    "provider": "mt5",
                    "server": live_server,
                    "account_hash": hashlib.sha256(
                        f"{live_login}|{live_server}".encode("utf-8")
                    ).hexdigest(),
                }

        toon = {
            "context": {
                "symbol": symbol,
                "timeframe": timeframe,
                "now_utc": datetime.now(timezone.utc).isoformat(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "market_data_source": "mt5" if self.client else "paper_simulation",
                "source_identity": source_identity,
                "data_status": self.check_data_sources(),
                "calendar_source": calendar_source,
                "calendar_event_count": len(news_events),
            },
            "ohlc": ohlc_records(df),
            "indicators": indicators,
            "dom": dom_data,
            "news": news_events,
            "api_news": api_news_events,
            "hot_topics": hot_topics,
            "macro_data": macro_data,
            "volume_analysis": volume_data,
            "multi_timeframe": multi_tf_data,
            "technical_analysis": {
                "primary_trend": trend_status,
                "key_levels": sr_levels,
                "htf_context": htf_data,
                "confluence": confluence_data,
            },
            "account": acc_info,
            "risk": {
                "risk_per_trade": self.risk.risk_per_trade,
                "max_drawdown": self.risk.max_drawdown,
            },
            "metadata": {"version": "1.1", "source": "mt5_llm_trader"},
            "auto_quant_tools": auto_quant_results.get("auto_quant_tools", {}),
            "regime_duration": regime_duration,
            "regime_alpha_selection": regime_alpha_selection,
        }

        # --- ZeroLag Macro: inject macro nowcast into toon every cycle ---
        try:
            from data.zero_lag_macro_provider import get_zero_lag_provider
            _zl = get_zero_lag_provider()
            _zl_snapshot = _zl.get_snapshot()
            toon["zero_lag_macro"] = {
                "net_liquidity_bias": _zl_snapshot.net_liquidity_bias,
                "regime": _zl_snapshot.regime,
                "nowcast_gdp": _zl_snapshot.nowcast_gdp,
                "nowcast_inflation": _zl_snapshot.nowcast_inflation,
                "confidence": _zl_snapshot.confidence,
                "data_freshness_hours": _zl_snapshot.data_freshness_hours,
            }
        except Exception as _zl_exc:
            log.debug("ZeroLag snapshot failed: %s", _zl_exc)
            toon["zero_lag_macro"] = {"net_liquidity_bias": 0.0, "regime": "UNKNOWN"}

        return toon
