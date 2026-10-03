"""News Trading Gate Backtest — validates that the gate improves trade quality.

Runs two parallel simulations on the same historical data:
  1. WITH gate: trades are blocked during SPIKE_BLACKOUT / PRE_NEWS phases
  2. WITHOUT gate: trades execute regardless of news phase

Measures improvement in:
  - Win rate
  - Average trade P&L
  - Max drawdown
  - Sharpe ratio
  - Number of whipsaw trades avoided
  - Slippage savings

Usage:
    from research.news_gate_backtest import NewsGateBacktest
    bt = NewsGateBacktest()
    result = bt.run(
        trades=historical_trades,
        events=historical_events,
        symbol="EURUSD",
    )
    print(result.summary())
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.news_trading import NewsTradingGate, classify_event_tier, is_relevant_to_symbol

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """A single historical trade for backtesting."""
    entry_time: datetime
    exit_time: datetime
    direction: str  # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    symbol: str = "EURUSD"
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    slippage_pips: float = 0.0

    def __post_init__(self):
        if self.pnl_pips == 0.0 and self.entry_price > 0 and self.exit_price > 0:
            if self.direction == "BUY":
                self.pnl_pips = (self.exit_price - self.entry_price) * 10000
            else:
                self.pnl_pips = (self.entry_price - self.exit_price) * 10000


@dataclass
class BacktestResult:
    """Result of the news gate backtest comparison."""
    with_gate: Dict[str, Any] = field(default_factory=dict)
    without_gate: Dict[str, Any] = field(default_factory=dict)
    improvement: Dict[str, Any] = field(default_factory=dict)
    whipsaw_trades_avoided: int = 0
    total_trades: int = 0
    events_analyzed: int = 0
    # S.Ξ DSR gate results (computed post-backtest)
    dsr_confidence: float = 0.0     # DSR confidence Φ(z) = 1 − p (higher = more significant)
    dsr_pvalue: float = 0.0         # DEPRECATED compat alias for dsr_confidence (legacy name)
    dsr_passed: Optional[bool] = None
    dsr_n_trials: int = 0
    dsr_raw_sharpe: float = 0.0
    dsr_expected_max: float = 0.0
    dsr_pbo_proxy: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep dsr_confidence and its deprecated alias in sync at construction.
        # (If both are set explicitly and disagree, both are respected as-is.)
        if self.dsr_confidence == 0.0 and self.dsr_pvalue != 0.0:
            self.dsr_confidence = self.dsr_pvalue
        elif self.dsr_pvalue == 0.0 and self.dsr_confidence != 0.0:
            self.dsr_pvalue = self.dsr_confidence

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = []
        lines.append("=" * 60)
        lines.append("NEWS GATE BACKTEST RESULTS")
        lines.append("=" * 60)
        lines.append(f"Total trades: {self.total_trades}")
        lines.append(f"Events analyzed: {self.events_analyzed}")
        lines.append(f"Whipsaw trades avoided: {self.whipsaw_trades_avoided}")
        lines.append("")
        lines.append(f"{'Metric':<25} {'With Gate':>12} {'Without':>12} {'Δ':>12}")
        lines.append("-" * 61)

        for key in ("win_rate", "avg_pnl_pips", "sharpe", "max_drawdown", "total_pnl_pips"):
            w = self.with_gate.get(key, 0)
            wo = self.without_gate.get(key, 0)
            delta = w - wo
            if key == "win_rate":
                lines.append(f"{key:<25} {w:>11.1%} {wo:>11.1%} {delta:>+11.1%}")
            elif key == "sharpe":
                lines.append(f"{key:<25} {w:>11.2f} {wo:>11.2f} {delta:>+11.2f}")
            else:
                lines.append(f"{key:<25} {w:>11.1f} {wo:>11.1f} {delta:>+11.1f}")

        # DSR gate info
        if self.dsr_passed is not None:
            lines.append("")
            lines.append("S.Ξ DSR GATE:")
            lines.append(f"  Raw Sharpe:     {self.dsr_raw_sharpe:.4f}")
            lines.append(f"  DSR confidence: {self.dsr_confidence:.4f}")
            _v = "PASS" if self.dsr_passed else "FAIL"
            lines.append(f"  Verdict:        {_v}")
            lines.append(f"  n_trials:       {self.dsr_n_trials}")

        lines.append("=" * 60)
        return "\n".join(lines)


def _simulate_trades(
    trades: List[TradeRecord],
    events: List[Dict[str, Any]],
    gate_enabled: bool,
    symbol: str,
) -> Dict[str, Any]:
    """Simulate trading with or without the news gate.

    When gate_enabled=True, trades whose entry_time falls within
    SPIKE_BLACKOUT or PRE_NEWS of a relevant event are skipped.
    """
    gate = NewsTradingGate()
    now_fn = lambda: datetime.now(timezone.utc)

    kept_trades = []
    skipped_trades = []

    for trade in trades:
        if not gate_enabled:
            kept_trades.append(trade)
            continue

        # Check if this trade's entry would be blocked
        result = gate.evaluate(symbol=symbol, now=trade.entry_time, events=events)
        if result.allowed:
            kept_trades.append(trade)
        else:
            skipped_trades.append(trade)

    if not kept_trades:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "avg_pnl_pips": 0.0,
            "total_pnl_pips": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "skipped": len(skipped_trades),
        }

    pnls = np.array([t.pnl_pips for t in kept_trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    # Max drawdown
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Sharpe (annualized, assuming ~252 trading days, trades per day ~ few)
    avg = float(np.mean(pnls))
    std = float(np.std(pnls)) if len(pnls) > 1 else 1.0
    sharpe = avg / std if std > 0 else 0.0

    # Avoid division by zero
    total = len(pnls)
    win_count = len(wins)

    return {
        "n_trades": total,
        "win_rate": win_count / total if total > 0 else 0.0,
        "avg_pnl_pips": avg,
        "total_pnl_pips": float(np.sum(pnls)),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "skipped": len(skipped_trades),
        "avg_slippage_pips": float(np.mean([t.slippage_pips for t in kept_trades])) if kept_trades else 0.0,
    }


class NewsGateBacktest:
    """Backtest harness comparing trading with vs without news gate.

    Generates synthetic but realistic trade data if no historical trades are
    provided. The key insight: trades that happen during spike blackouts tend
    to have worse outcomes (higher slippage, more whipsaws).
    """

    def run(
        self,
        trades: Optional[List[TradeRecord]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        symbol: str = "EURUSD",
        n_synthetic_trades: int = 500,
        n_synthetic_events: int = 20,
        seed: int = 42,
    ) -> BacktestResult:
        """Run the backtest.

        Args:
            trades: Historical trades. If None, generates synthetic data.
            events: Calendar events. If None, generates synthetic events.
            symbol: Trading symbol.
            n_synthetic_trades: Number of synthetic trades to generate.
            n_synthetic_events: Number of synthetic events to generate.
            seed: Random seed for reproducibility.

        Returns:
            BacktestResult with with-gate vs without-gate comparison
        """
        rng = np.random.RandomState(seed)

        if trades is None:
            trades = self._generate_synthetic_trades(n_synthetic_trades, symbol, rng)
        if events is None:
            events = self._generate_synthetic_events(n_synthetic_events, symbol, rng)

        result = BacktestResult(
            total_trades=len(trades),
            events_analyzed=len(events),
        )

        # Run WITHOUT gate (baseline)
        without = _simulate_trades(trades, events, gate_enabled=False, symbol=symbol)
        result.without_gate = without

        # Run WITH gate
        with_g = _simulate_trades(trades, events, gate_enabled=True, symbol=symbol)
        result.with_gate = with_g

        # Whipsaw trades avoided = trades skipped by gate
        result.whipsaw_trades_avoided = without["n_trades"] - with_g["n_trades"]

        # Compute improvements
        result.improvement = {
            "win_rate_delta": with_g["win_rate"] - without["win_rate"],
            "avg_pnl_delta": with_g["avg_pnl_pips"] - without["avg_pnl_pips"],
            "sharpe_delta": with_g["sharpe"] - without["sharpe"],
            "max_drawdown_reduction": without["max_drawdown"] - with_g["max_drawdown"],
            "trades_reduced_pct": result.whipsaw_trades_avoided / max(without["n_trades"], 1),
        }

        return result

    def _generate_synthetic_trades(
        self, n: int, symbol: str, rng: np.random.RandomState,
    ) -> List[TradeRecord]:
        """Generate synthetic trades with realistic properties.

        Key property: trades during event blackouts have 30-50% worse outcomes.
        """
        trades = []
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        for i in range(n):
            # Random time over ~6 months
            offset_hours = rng.uniform(0, 6 * 30 * 24)
            entry_time = base_time + timedelta(hours=offset_hours)

            # Random duration 15 min to 4 hours
            duration_min = rng.uniform(15, 240)
            exit_time = entry_time + timedelta(minutes=duration_min)

            direction = rng.choice(["BUY", "SELL"])

            # Base price movement
            base_move = rng.normal(0, 15)  # pips

            # Add whipsaw penalty for entries near events (simulated)
            hour_of_day = entry_time.hour
            # NFP-like events at 8:30 ET → 13:30 UTC
            is_event_window = (13 <= hour_of_day <= 14) and (entry_time.weekday() < 5)

            slippage = abs(rng.normal(0, 2))
            if is_event_window:
                slippage *= 3  # 3x slippage during event windows
                base_move *= rng.choice([-1, 1], p=[0.4, 0.6])  # More likely to whip

            entry_price = 1.1000 + rng.normal(0, 0.005)
            pnl_pips = base_move - slippage

            trades.append(TradeRecord(
                entry_time=entry_time,
                exit_time=exit_time,
                direction=direction,
                entry_price=entry_price,
                exit_price=entry_price + pnl_pips / 10000,
                symbol=symbol,
                pnl_pips=pnl_pips,
                slippage_pips=slippage,
            ))

        return trades

    def _generate_synthetic_events(
        self, n: int, symbol: str, rng: np.random.RandomState,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic calendar events."""
        events = []
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        event_titles = [
            "NON-FARM PAYROLLS", "CONSUMER PRICE INDEX", "FOMC STATEMENT",
            "RETAIL SALES", "UNEMPLOYMENT RATE", "ISM MANUFACTURING PMI",
            "INITIAL JOBLESS CLAIMS", "JOLTS JOB OPENINGS",
            "CONSUMER CONFIDENCE", "GROSS DOMESTIC PRODUCT",
        ]
        currencies = ["USD", "EUR", "GBP"]
        importances = ["high", "medium", "low"]

        for i in range(n):
            offset_days = rng.uniform(0, 180)
            event_time = base_time + timedelta(days=offset_days, hours=rng.randint(6, 20))
            title = rng.choice(event_titles)
            currency = "USD" if "NON-FARM" in title or "CPI" in title or "FOMC" in title else rng.choice(currencies)
            importance = "high" if i < n // 3 else rng.choice(importances)

            events.append({
                "title": title,
                "currency": currency,
                "time": event_time,
                "importance": importance,
                "forecast": rng.uniform(100, 300),
                "previous": rng.uniform(100, 300),
                "actual": None,
            })

        return events

    def run_from_mt5_history(
        self,
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        days_back: int = 90,
        correlation_engine=None,
        live_indicator_fetcher=None,
        fred_backtest_data=None,
        full_history: bool = False,
    ) -> BacktestResult:
        """Run backtest using MT5 historical data with real price reactions.

        Fetches actual OHLCV M15 bars, aligns economic events to real timestamps,
        measures actual price reactions (spike pips, direction), records outcomes
        in the correlation engine, and runs WITH-gate vs WITHOUT-gate comparison.

        When fred_backtest_data is provided, also:
          - Fetches FRED historical actual vs forecast per release
          - Computes surprise Z-scores for each event
          - Attributes spike direction/magnitude to surprise direction
          - Reports surprise→spike correlation and directional accuracy

        Args:
            symbol: Trading symbol
            timeframe: MT5 timeframe string
            days_back: How many days of history to fetch
            correlation_engine: Optional NewsRegimeCorrelation for recording outcomes
            live_indicator_fetcher: Optional LiveIndicatorFetcher for FRED data
            fred_backtest_data: Optional FredBacktestData for full FRED history
            full_history: If True, fetch maximum available MT5 history

        Returns:
            BacktestResult with with-gate vs without-gate comparison on real data
        """
        # Determine effective days_back
        effective_days = days_back
        if full_history:
            effective_days = max(days_back, 730)  # Up to 2 years of M15

        # Fetch MT5 bars
        bars = self._fetch_mt5_bars(symbol, timeframe, effective_days)
        if bars is None:
            logger.warning("No MT5 data available, falling back to synthetic")
            return self.run(symbol=symbol)

        logger.info("Fetched %d M15 bars spanning %s to %s",
                     len(bars),
                     bars[0]["time"].strftime("%Y-%m-%d") if bars else "?",
                     bars[-1]["time"].strftime("%Y-%m-%d") if bars else "?")

        # Generate realistic event times
        events = self._generate_realistic_events(effective_days, symbol)

        # Enhance events with FRED data (two paths)
        if fred_backtest_data is not None:
            # Full FRED history with surprise scores
            events = fred_backtest_data.enhance_events_with_fred(events, days_back=effective_days)
            logger.info("Enhanced %d events with FRED historical data", len(events))
        elif live_indicator_fetcher is not None:
            # Legacy path: latest indicators only
            events = self._enhance_events_with_fred(events, live_indicator_fetcher, days_back=effective_days)

        # Measure actual price reactions for each event and record in correlation engine
        event_reactions = self._measure_event_reactions(bars, events, symbol)
        if correlation_engine is not None:
            self._record_outcomes_to_correlation(correlation_engine, event_reactions, symbol)

        # Convert bars to TradeRecords (every other bar = entry/exit pair)
        trades = self._bars_to_trades(bars, symbol)

        # Run base comparison (static windows)
        result = self.run(trades=trades, events=events, symbol=symbol)

        # If correlation engine is available, also run with dynamic windows
        if correlation_engine is not None and event_reactions:
            result = self._run_with_dynamic_windows(
                trades, events, symbol, correlation_engine, event_reactions,
            )

        # Attach FRED attribution analytics
        if fred_backtest_data is not None and event_reactions:
            attribution = fred_backtest_data.compute_event_attribution(
                event_reactions, days_back=effective_days,
            )
            if not hasattr(result, "details"):
                result.details = {}
            result.details["fred_attribution"] = attribution
            result.details["bars_span"] = {
                "n_bars": len(bars),
                "start": bars[0]["time"].isoformat() if bars else None,
                "end": bars[-1]["time"].isoformat() if bars else None,
                "days": effective_days,
            }
            logger.info("FRED attribution: %d events attributed, directional_accuracy=%.1f%%",
                        attribution["n_attributed"],
                        attribution["directional_accuracy"] * 100)

        # --- S.Ξ DSR Gate: validate news gate improvement against multiple-testing bias ---
        try:
            from research.backtest_dsr_integrator import BacktestDSRIntegrator
            _dsr_integrator = BacktestDSRIntegrator(strategy_class="news_gate")
            # Use the with-gate Sharpe for DSR validation
            _sharpe = result.with_gate.get("sharpe", 0.0)
            _n_obs = max(len(bars), 50) if bars else 50
            _report = _dsr_integrator.validate_backtest(
                strategy_id=f"news_gate_{symbol}",
                sharpe=_sharpe,
                n_observations=_n_obs,
                metadata={
                    "symbol": symbol, "timeframe": timeframe,
                    "bars": len(bars) if bars else 0,
                    "events": len(events),
                    "whipsaw_avoided": result.whipsaw_trades_avoided,
                },
            )
            result.dsr_confidence = _report.dsr
            result.dsr_pvalue = _report.dsr  # compat alias
            result.dsr_passed = _report.passed_gate
            result.dsr_n_trials = _report.n_trials
            result.dsr_raw_sharpe = _report.raw_sharpe
            result.dsr_expected_max = _report.expected_max_sharpe
            result.dsr_pbo_proxy = _report.pbo_proxy
            if _report.passed_gate:
                logger.info("DSR GATE PASS: news_gate_%s Sharpe=%.3f DSR=%.4f n_trials=%d",
                            symbol, _sharpe, _report.dsr, _report.n_trials)
            else:
                logger.warning("DSR GATE REJECT: news_gate_%s Sharpe=%.3f DSR=%.4f n_trials=%d",
                               symbol, _sharpe, _report.dsr, _report.n_trials)
        except Exception as _dsr_exc:
            logger.debug("DSR gate skipped for news backtest: %s", _dsr_exc)

        return result

    def _fetch_mt5_bars(self, symbol: str, timeframe: str, days_back: int):
        """Fetch M15 bars from MT5. Returns list of bar dicts or None."""
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                logger.warning("MT5 not available")
                return None
        except ImportError:
            logger.warning("MetaTrader5 not installed")
            return None

        try:
            now = datetime.now(timezone.utc)
            from_date = now - timedelta(days=days_back)
            tf_const = getattr(mt5, f"TIMEFRAME_{timeframe.upper()}", mt5.TIMEFRAME_M15)
            rates = mt5.copy_rates_range(symbol, tf_const, from_date, now)
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass

        if rates is None or len(rates) == 0:
            return None

        bars = []
        for r in rates:
            # MT5 returns numpy structured arrays (numpy.void), not dicts
            names = getattr(getattr(r, "dtype", None), "names", None) or ()
            vol = float(r["tick_volume"]) if "tick_volume" in names else (
                float(r["real_volume"]) if "real_volume" in names else 0.0)
            bars.append({
                "time": datetime.fromtimestamp(float(r["time"]), tz=timezone.utc),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": vol,
            })
        return bars

    def _bars_to_trades(self, bars: List[Dict[str, Any]], symbol: str) -> List[TradeRecord]:
        """Convert M15 bars to TradeRecords (every other bar = entry/exit)."""
        trades = []
        rng = np.random.RandomState(42)
        pip_mult = 100.0 if "JPY" in symbol.upper() else 10000.0

        for i in range(0, len(bars) - 1, 2):
            bar = bars[i]
            next_bar = bars[i + 1]
            direction = "BUY" if next_bar["close"] > bar["close"] else "SELL"
            entry_price = bar["close"]
            exit_price = next_bar["close"]
            pnl_pips = (exit_price - entry_price) * pip_mult

            trades.append(TradeRecord(
                entry_time=bar["time"],
                exit_time=next_bar["time"],
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                symbol=symbol,
                pnl_pips=pnl_pips,
                slippage_pips=abs(rng.normal(0, 1)),
            ))

        return trades

    def _measure_event_reactions(
        self,
        bars: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        symbol: str,
    ) -> List[Dict[str, Any]]:
        """Measure actual price reaction for each event using real M15 bars.

        For each event, finds the bar closest to the event time and measures:
        - Price before (close of bar at event time)
        - Price after 15m, 30m, 60m
        - Max spike (high-low range across first 4 bars)
        - Direction and duration

        Returns list of dicts with event info + reaction measurements.
        """
        if not bars:
            return []

        pip_mult = 100.0 if "JPY" in symbol.upper() else 10000.0
        reactions = []

        for ev in events:
            ev_time = ev.get("time")
            if ev_time is None:
                continue
            if isinstance(ev_time, str):
                try:
                    ev_time = datetime.fromisoformat(ev_time.replace("Z", "+00:00"))
                except ValueError:
                    continue
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)

            # Find closest bar
            ev_ts = ev_time.timestamp()
            best_idx = None
            best_delta = float("inf")
            for i, bar in enumerate(bars):
                bar_ts = bar["time"].timestamp() if isinstance(bar["time"], datetime) else bar["time"]
                delta = abs(bar_ts - ev_ts)
                if delta < best_delta:
                    best_delta = delta
                    best_idx = i

            if best_idx is None or best_delta > 3600:  # > 1 hour off
                continue

            price_before = bars[best_idx]["close"]
            if price_before <= 0:
                continue

            # Measure reaction across next 4 bars (1 hour)
            max_high = price_before
            min_low = price_before
            price_after_15m = price_before
            price_after_30m = price_before
            price_after_60m = price_before

            end_idx = min(best_idx + 5, len(bars))
            for j in range(best_idx + 1, end_idx):
                bar = bars[j]
                max_high = max(max_high, bar["high"])
                min_low = min(min_low, bar["low"])
                offset = j - best_idx
                if offset == 1:
                    price_after_15m = bar["close"]
                elif offset == 2:
                    price_after_30m = bar["close"]
                elif offset == 4:
                    price_after_60m = bar["close"]

            max_spike = (max_high - min_low) * pip_mult
            spike_15m = (price_after_15m - price_before) * pip_mult
            spike_30m = (price_after_30m - price_before) * pip_mult
            spike_60m = (price_after_60m - price_before) * pip_mult

            reactions.append({
                **ev,
                "price_before": price_before,
                "price_after_15m": price_after_15m,
                "price_after_30m": price_after_30m,
                "price_after_60m": price_after_60m,
                "max_spike_pips": max_spike,
                "spike_15m_pips": spike_15m,
                "spike_30m_pips": spike_30m,
                "spike_60m_pips": spike_60m,
                "direction": "BULLISH" if spike_15m > 1.0 else "BEARISH" if spike_15m < -1.0 else "NEUTRAL",
                "bar_index": best_idx,
            })

        return reactions

    def _record_outcomes_to_correlation(
        self,
        correlation_engine,
        reactions: List[Dict[str, Any]],
        symbol: str,
    ):
        """Record measured event reactions into the correlation engine."""
        from core.news_trading import classify_event_tier

        recorded = 0
        for r in reactions:
            title = r.get("title", "")
            tier = classify_event_tier(title)
            if tier == 0:
                imp = str(r.get("importance", "")).upper()
                tier = 2 if imp == "HIGH" else 3 if imp == "MEDIUM" else 0
            if tier == 0:
                continue

            event_time = r.get("time")
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                except ValueError:
                    continue

            try:
                correlation_engine.record_price_snapshot(
                    event_title=title,
                    tier=tier,
                    currency=r.get("currency", "USD"),
                    event_time=event_time,
                    price_at_event=r["price_before"],
                    symbol=symbol,
                    price_after_15m=r.get("price_after_15m"),
                    price_after_30m=r.get("price_after_30m"),
                    price_after_60m=r.get("price_after_60m"),
                    max_price_in_window=r["price_before"] + r["max_spike_pips"] / (10000 if "JPY" not in symbol else 100),
                    min_price_in_window=r["price_before"] - r["max_spike_pips"] / (10000 if "JPY" not in symbol else 100),
                )
                recorded += 1
            except Exception as exc:
                logger.warning("Failed to record event outcome: %s", exc)

        logger.info("Recorded %d/%d event outcomes to correlation engine", recorded, len(reactions))

    def _run_with_dynamic_windows(
        self,
        trades: List[TradeRecord],
        events: List[Dict[str, Any]],
        symbol: str,
        correlation_engine,
        event_reactions: List[Dict[str, Any]],
    ) -> BacktestResult:
        """Run backtest with dynamically-adjusted blackout windows.

        Compares:
        1. Static windows (baseline)
        2. Dynamic windows (from correlation engine)
        3. No gate (unfiltered)

        Also computes per-phase analytics using real reaction data.
        """
        result = BacktestResult(
            total_trades=len(trades),
            events_analyzed=len(event_reactions),
        )

        # Get dynamic windows
        try:
            dynamic_windows = correlation_engine.get_all_adjusted_windows(symbol=symbol)
        except Exception:
            dynamic_windows = None

        # Run WITH static gate (baseline)
        static_result = _simulate_trades(trades, events, gate_enabled=True, symbol=symbol)

        # Run WITH dynamic gate
        if dynamic_windows is not None:
            gate = NewsTradingGate(blackout_windows_override=dynamic_windows)
            dynamic_kept = []
            dynamic_skipped = []
            for trade in trades:
                result_check = gate.evaluate(symbol=symbol, now=trade.entry_time, events=events)
                if result_check.allowed:
                    dynamic_kept.append(trade)
                else:
                    dynamic_skipped.append(trade)

            dynamic_stats = self._compute_trade_stats(dynamic_kept)
            dynamic_stats["skipped"] = len(dynamic_skipped)
        else:
            dynamic_stats = static_result

        # Run WITHOUT gate (unfiltered)
        unfiltered = _simulate_trades(trades, events, gate_enabled=False, symbol=symbol)

        # Compute per-phase analytics from real event reactions
        phase_analytics = self._compute_phase_analytics(event_reactions, symbol)

        # Use dynamic as the primary "with gate" result
        result.with_gate = dynamic_stats if dynamic_windows else static_result
        result.without_gate = unfiltered
        result.whipsaw_trades_avoided = unfiltered["n_trades"] - result.with_gate["n_trades"]
        result.improvement = {
            "win_rate_delta": result.with_gate["win_rate"] - unfiltered["win_rate"],
            "avg_pnl_delta": result.with_gate["avg_pnl_pips"] - unfiltered["avg_pnl_pips"],
            "sharpe_delta": result.with_gate["sharpe"] - unfiltered["sharpe"],
            "max_drawdown_reduction": unfiltered["max_drawdown"] - result.with_gate["max_drawdown"],
            "trades_reduced_pct": result.whipsaw_trades_avoided / max(unfiltered["n_trades"], 1),
        }

        # Attach phase analytics
        if not hasattr(result, "details"):
            result.details = {}
        result.details["phase_analytics"] = phase_analytics
        if dynamic_windows:
            result.details["dynamic_windows"] = {str(k): v for k, v in dynamic_windows.items()}

        return result

    def _compute_trade_stats(self, trades: List[TradeRecord]) -> Dict[str, Any]:
        """Compute standard trade statistics from a list of TradeRecords."""
        if not trades:
            return {
                "n_trades": 0, "win_rate": 0.0, "avg_pnl_pips": 0.0,
                "total_pnl_pips": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "skipped": 0,
            }
        pnls = np.array([t.pnl_pips for t in trades])
        wins = pnls[pnls > 0]
        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        avg = float(np.mean(pnls))
        std = float(np.std(pnls)) if len(pnls) > 1 else 1.0
        return {
            "n_trades": len(pnls),
            "win_rate": len(wins) / len(pnls),
            "avg_pnl_pips": avg,
            "total_pnl_pips": float(np.sum(pnls)),
            "max_drawdown": float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0,
            "sharpe": avg / std if std > 0 else 0.0,
            "skipped": 0,
        }

    def _compute_phase_analytics(
        self,
        reactions: List[Dict[str, Any]],
        symbol: str,
    ) -> Dict[str, Any]:
        """Compute per-phase analytics from real event reactions.

        Classifies each reaction into phases and computes statistics:
        - spike: max excursion during the first 15 minutes
        - pre_news: price movement in the 30 minutes before the event
        - post_news_entry: price movement 15-60 minutes after (the entry zone)
        - clear: normal trading periods
        """
        if not reactions:
            return {}

        pip_mult = 100.0 if "JPY" in symbol.upper() else 10000.0

        # Classify reactions by severity
        spike_sizes = [abs(r.get("max_spike_pips", 0)) for r in reactions]
        directions = [r.get("direction", "NEUTRAL") for r in reactions]
        tiers = []
        for r in reactions:
            from core.news_trading import classify_event_tier
            tiers.append(classify_event_tier(r.get("title", "")))

        # Statistics
        spike_arr = np.array(spike_sizes)
        hit_thresholds = {1: 20.0, 2: 10.0, 3: 5.0}

        per_tier = {}
        for tier in (1, 2, 3):
            tier_spikes = [abs(r.get("max_spike_pips", 0)) for r, t in zip(reactions, tiers) if t == tier]
            if tier_spikes:
                arr = np.array(tier_spikes)
                threshold = hit_thresholds.get(tier, 10.0)
                per_tier[tier] = {
                    "n_events": len(tier_spikes),
                    "avg_spike_pips": round(float(np.mean(arr)), 2),
                    "median_spike_pips": round(float(np.median(arr)), 2),
                    "p95_spike_pips": round(float(np.percentile(arr, 95)), 2) if len(arr) >= 5 else round(float(np.max(arr)), 2),
                    "hit_rate": round(float(np.mean(arr >= threshold)), 4),
                    "bullish_rate": round(float(np.mean([d == "BULLISH" for r, d in zip(reactions, directions) if tiers[reactions.index(r)] == tier])), 4) if len(tier_spikes) > 0 else 0.5,
                }

        # Overall
        return {
            "total_events": len(reactions),
            "avg_spike_pips": round(float(np.mean(spike_arr)), 2) if len(spike_arr) > 0 else 0,
            "median_spike_pips": round(float(np.median(spike_arr)), 2) if len(spike_arr) > 0 else 0,
            "p95_spike_pips": round(float(np.percentile(spike_arr, 95)), 2) if len(spike_arr) >= 5 else 0,
            "overall_hit_rate": round(float(np.mean(spike_arr >= 10.0)), 4) if len(spike_arr) > 0 else 0,
            "directional_balance": {
                "bullish": directions.count("BULLISH"),
                "bearish": directions.count("BEARISH"),
                "neutral": directions.count("NEUTRAL"),
            },
            "per_tier": per_tier,
        }

    def _enhance_events_with_fred(
        self,
        events: List[Dict[str, Any]],
        live_indicator_fetcher,
        days_back: int = 90,
    ) -> List[Dict[str, Any]]:
        """Enhance synthetic events with FRED indicator data.

        Adds actual vs forecast values from FRED for ESI calculation
        and NFP/CPI prediction accuracy.
        """
        try:
            indicator_data = live_indicator_fetcher.fetch_all_indicators()
            nfp_indicators = indicator_data.get("nfp")
            cpi_indicators = indicator_data.get("cpi")
            esi_releases = indicator_data.get("esi_releases", [])
        except Exception as exc:
            logger.warning("Failed to fetch FRED indicators for backtest: %s", exc)
            return events

        # Enhance NFP events with real ADP/claims data
        for ev in events:
            title = ev.get("title", "").upper()
            if "PAYROLL" in title and nfp_indicators:
                ev["fred_adp"] = getattr(nfp_indicators, "adp_change_k", 0.0)
                ev["fred_claims"] = getattr(nfp_indicators, "jobless_claims_4w_ma", 215.0)
                ev["fred_ism_employment"] = getattr(nfp_indicators, "ism_employment_pmi", 50.0)
                ev["fred_jolts"] = getattr(nfp_indicators, "jolts_openings_millions", 8.5)
            elif "CPI" in title and cpi_indicators:
                ev["fred_ppi"] = getattr(cpi_indicators, "ppi_mom_pct", 0.0)
                ev["fred_import_prices"] = getattr(cpi_indicators, "import_price_mom_pct", 0.0)
                ev["fred_ism_prices"] = getattr(cpi_indicators, "ism_prices_paid_pmi", 50.0)
                ev["fred_wages"] = getattr(cpi_indicators, "wage_growth_yoy_pct", 4.0)

        # Add ESI releases as synthetic events for correlation
        if esi_releases:
            for release in esi_releases[-10:]:  # Last 10 releases
                try:
                    release_date = datetime.fromisoformat(release.get("date", ""))
                    if release_date.tzinfo is None:
                        release_date = release_date.replace(tzinfo=timezone.utc)
                    # Skip if too old
                    age_days = (datetime.now(timezone.utc) - release_date).days
                    if age_days > days_back:
                        continue
                    events.append({
                        "title": release.get("description", "ECONOMIC_RELEASE"),
                        "currency": "USD",
                        "time": release_date,
                        "importance": "medium",
                        "forecast": release.get("consensus", 0),
                        "actual": release.get("actual", 0),
                    })
                except (ValueError, TypeError):
                    continue

        return events

    def _generate_realistic_events(self, days_back: int, symbol: str) -> List[Dict[str, Any]]:
        """Generate realistic weekly US economic events."""
        events = []
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Weekly NFP on first Friday
        # CPI mid-month
        # FOMC every ~6 weeks
        for week in range(days_back // 7):
            event_date = base + timedelta(weeks=week)

            # NFP: First Friday of month at 13:30 UTC (8:30 ET)
            if event_date.weekday() == 4:  # Friday
                events.append({
                    "title": "NON-FARM PAYROLLS",
                    "currency": "USD",
                    "time": event_date.replace(hour=13, minute=30),
                    "importance": "high",
                    "forecast": 180.0,
                    "previous": 175.0,
                })

            # ISM PMI: First business day at 14:00 UTC
            if event_date.weekday() < 5 and event_date.day <= 5:
                events.append({
                    "title": "ISM MANUFACTURING PMI",
                    "currency": "USD",
                    "time": event_date.replace(hour=14, minute=0),
                    "importance": "medium",
                    "forecast": 50.0,
                    "previous": 49.5,
                })

            # Initial Claims: Thursday at 13:30 UTC
            if event_date.weekday() == 3:  # Thursday
                events.append({
                    "title": "INITIAL JOBLESS CLAIMS",
                    "currency": "USD",
                    "time": event_date.replace(hour=13, minute=30),
                    "importance": "medium",
                    "forecast": 215.0,
                    "previous": 218.0,
                })

        return events
