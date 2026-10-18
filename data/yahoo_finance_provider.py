#!/usr/bin/env python3
"""
Yahoo Finance Data Provider
Fetches additional market data from Yahoo Finance

Hardening notes (2026-09-26):
- All history fetches route through ``YahooFinanceProvider._history`` so every
  call site shares one failure path.
- yfinance reports *any* empty/unreachable fetch as ``possibly delisted; no
  price data found`` — that message is generic and usually means a network,
  DNS, rate-limit, or Yahoo-side outage, NOT a delisted symbol. Symptom seen
  in mt5_trader.log 2026-09-26 08:00-08:12: a machine-wide DNS outage
  (``[Errno 11001] getaddrinfo failed`` hit ForexFactory in the same second)
  while ^GSPC/^VIX/CL=F fetched fine once the network recovered.
- A module-level circuit breaker opens after repeated consecutive failures and
  holds traffic for a backoff window, so an outage does not burn quota with
  10-second retry churn. Successful fetches reset it immediately.
- ``get_quote_data`` provides the latest-quote API the zero-lag macro provider
  expects (previously it called a nonexistent ``fetch_quote`` and silently
  fell back to hardcoded constants forever).
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    yf = None  # type: ignore[assignment]
    YFINANCE_AVAILABLE = False

# Env kill switch for hermetic tests / offline analysis: forces the empty
# history path without touching the network. Checked at call time.
_OFFLINE_ENV = "LLM_TRADER_YF_OFFLINE"

# Module-level outage breaker shared by every provider instance so N callers
# do not independently hammer a dead network.
_NET_FAIL_LOCK = threading.Lock()
_NET_FAIL_COUNT = 0
_NET_FAIL_UNTIL = 0.0


class _DelistedNoiseFilter(logging.Filter):
    """Demote yfinance's own 'possibly delisted' ERROR spam.

    Exchange-closed/weekend futures ($CL=F, $NG=F, ...) legitimately return
    an empty 1d history and yfinance logs its generic 'possibly delisted'
    ERROR per symbol on every boot and maintenance sweep. The information
    value of each line is nil (the breaker already models empties as
    potential outages); demoting to rendertime silence keeps outages
    readable. Network/parse failures and other messages pass through.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return "possibly delisted" not in record.getMessage()


if YFINANCE_AVAILABLE:
    logging.getLogger("yfinance").addFilter(_DelistedNoiseFilter())


class YahooFinanceProvider:
    """Provides market data from Yahoo Finance"""

    # Circuit breaker: after this many consecutive failures, stop calling
    # yfinance until the backoff window elapses. Successes reset the count.
    NET_FAIL_THRESHOLD = 4
    NET_BACKOFF_BASE_S = 30.0     # first open: 30s
    NET_BACKOFF_MAX_S = 600.0     # cap: 10 minutes

    def __init__(self):
        self.log = logging.getLogger("YahooFinance")
        if not YFINANCE_AVAILABLE:
            self.log.warning("yfinance not installed. Install with: pip install yfinance")

    # ------------------------------------------------------------------ #
    # Shared guarded fetch + outage circuit breaker
    # ------------------------------------------------------------------ #

    def _record_net_success(self) -> None:
        global _NET_FAIL_COUNT
        with _NET_FAIL_LOCK:
            if _NET_FAIL_COUNT >= self.NET_FAIL_THRESHOLD:
                # A real breaker episode opened: an explicit recovery line is
                # warranted (the operator was warned about the outage).
                self.log.info("Yahoo Finance connectivity recovered after %d failures",
                              _NET_FAIL_COUNT)
            elif _NET_FAIL_COUNT:
                # Transient single-symbol empties (weekend-closed futures
                # surface as yfinance 'possibly delisted' empty frames).
                # Logging a recovery per symbol pairs with every failure line
                # in the shared counter — alternating noise on every boot.
                # Keep at DEBUG; breaker semantics unchanged.
                self.log.debug("Yahoo transient failures cleared (%d)", _NET_FAIL_COUNT)
            _NET_FAIL_COUNT = 0
            globals()["_NET_FAIL_UNTIL"] = 0.0

    def _record_net_failure(self) -> None:
        """Track consecutive failures; open the breaker past the threshold."""
        global _NET_FAIL_COUNT, _NET_FAIL_UNTIL
        with _NET_FAIL_LOCK:
            _NET_FAIL_COUNT += 1
            if _NET_FAIL_COUNT >= self.NET_FAIL_THRESHOLD:
                backoff = min(
                    self.NET_BACKOFF_BASE_S * (2 ** (_NET_FAIL_COUNT - self.NET_FAIL_THRESHOLD)),
                    self.NET_BACKOFF_MAX_S,
                )
                _NET_FAIL_UNTIL = time.monotonic() + backoff
                self.log.warning(
                    "Yahoo Finance unavailable: %d consecutive failures; backing off %.0fs "
                    "(transient outages surface as yfinance 'possibly delisted' errors)",
                    _NET_FAIL_COUNT, backoff,
                )

    @staticmethod
    def _net_backoff_active() -> bool:
        return time.monotonic() < _NET_FAIL_UNTIL

    def _history(self, symbol: str, *, period: str, interval: str = "1d") -> Any:
        """Single guarded fetch path for all history/quote calls.

        Returns a (possibly empty) DataFrame, or None when the fetch was
        suppressed (breaker open / offline kill switch / yfinance missing).
        Callers treat None and empty identically: produce no observation.
        """
        if not YFINANCE_AVAILABLE:
            return None
        if os.getenv(_OFFLINE_ENV, "").strip() in {"1", "true", "yes"}:
            return None
        if self._net_backoff_active():
            # Suppressed retry: stay quiet (DEBUG) so outage logs stay readable.
            self.log.debug("yfinance fetch for %s suppressed (outage backoff active)", symbol)
            return None
        try:
            frame = yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as exc:  # network, DNS, rate-limit, parse errors
            self.log.debug("yfinance fetch failed for %s: %s", symbol, exc)
            self._record_net_failure()
            return None
        if frame is None or getattr(frame, "empty", True):
            # Empty result = fetch did not succeed (yfinance logs its generic
            # "possibly delisted" ERROR itself). Count it as a failure so an
            # outage trips the breaker even when no exception is raised.
            self._record_net_failure()
            return None
        self._record_net_success()
        return frame

    def get_quote_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Latest quote for one Yahoo symbol (indices, futures, FX).

        Used by the zero-lag macro provider; falls back to ``5d`` history when
        ``1d`` returns nothing (holidays/weekends). Returns None when no live
        data is available — callers keep their own safe fallbacks.
        """
        frame = self._history(symbol, period="1d")
        if frame is None or frame.empty:
            frame = self._history(symbol, period="5d")
        if frame is None or frame.empty:
            self.log.debug("No quote data for %s", symbol)
            return None
        latest = frame.iloc[-1]
        try:
            price = float(latest["Close"])
        except (KeyError, TypeError, ValueError):
            return None
        if price <= 0.0:
            return None
        observed_at = self._utcnow_from_index(latest)
        return {"symbol": symbol, "price": price, "observed_at": observed_at}

    # Backwards-compatible alias: earlier code (zero-lag macro provider) called
    # a nonexistent ``fetch_quote`` and silently fell back to constants.
    def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self.get_quote_data(symbol)

    @staticmethod
    def _utcnow_from_index(row: Any) -> str:
        try:
            ts = row.name
            if hasattr(ts, "to_pydatetime"):
                return ts.to_pydatetime().isoformat()
        except Exception:
            pass
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Public data methods
    # ------------------------------------------------------------------ #

    def get_forex_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get forex pair data from Yahoo Finance

        Args:
            symbol: Forex symbol (e.g., 'EURUSD')

        Returns:
            Dict with price data and indicators
        """
        yahoo_symbol = f"{symbol}=X"
        hist = self._history(yahoo_symbol, period="5d")
        if hist is None or hist.empty:
            self.log.debug("No data for %s", yahoo_symbol)
            return None

        latest = hist.iloc[-1]
        return {
            "symbol": symbol,
            "price": float(latest['Close']),
            "volume": int(latest['Volume']),
            "high_5d": float(hist['High'].max()),
            "low_5d": float(hist['Low'].min()),
            "change_5d": float((latest['Close'] - hist.iloc[0]['Open']) / hist.iloc[0]['Open'] * 100),
            "avg_volume_5d": int(hist['Volume'].mean())
        }

    def get_historical_data(self, symbol: str, interval: str = "1h", period: str = "5d") -> List[Dict[str, Any]]:
        """
        Fetch historical OHLC data
        """
        # Normalize symbol
        yahoo_symbol = f"{symbol}=X" if len(symbol) == 6 and not symbol.endswith("=X") else symbol
        hist = self._history(yahoo_symbol, period=period, interval=interval)
        if hist is None or hist.empty:
            return []

        data = []
        for idx, row in hist.iterrows():
            try:
                ts = idx.timestamp() if hasattr(idx, 'timestamp') else datetime.now().timestamp()
                data.append({
                    "time": int(ts),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "tick_volume": int(row["Volume"]),
                    "spread": 0,
                    "real_volume": int(row["Volume"])
                })
            except Exception:
                continue

        return data

    def get_market_indices(self) -> Dict[str, Dict[str, Any]]:
        """Get major market indices"""
        indices = {
            "SPX": "^GSPC",  # S&P 500
            "DJI": "^DJI",   # Dow Jones
            "NDX": "^IXIC",  # NASDAQ
            "VIX": "^VIX",   # Volatility Index
            "DXY": "DX-Y.NYB"  # US Dollar Index
        }

        results: Dict[str, Dict[str, Any]] = {}

        for name, symbol in indices.items():
            hist = self._history(symbol, period="5d")
            if hist is None or hist.empty:
                # 5d can legitimately be empty over long holiday breaks.
                hist = self._history(symbol, period="1mo")
            if hist is None or hist.empty:
                continue

            latest = hist.iloc[-1]
            prev = hist.iloc[0] if len(hist) > 1 else latest
            results[name] = {
                "price": float(latest['Close']),
                "change": float(latest['Close'] - prev['Close']),
                "change_pct": float((latest['Close'] - prev['Close']) / prev['Close'] * 100)
            }

        return results

    def get_economic_calendar(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Get upcoming economic events
        Note: Yahoo Finance doesn't provide a direct calendar API
        This is a placeholder for future implementation
        """
        self.log.info("Economic calendar not available via Yahoo Finance")
        return []

    def get_currency_strength(self) -> Dict[str, float]:
        """Calculate relative currency strength"""
        # Major currency pairs vs USD
        pairs = {
            "EUR": "EURUSD=X",
            "GBP": "GBPUSD=X",
            "JPY": "USDJPY=X",
            "AUD": "AUDUSD=X",
            "CAD": "USDCAD=X",
            "CHF": "USDCHF=X"
        }

        strength: Dict[str, float] = {}

        for currency, symbol in pairs.items():
            hist = self._history(symbol, period="5d")
            if hist is None or hist.empty:
                continue

            # Calculate 5-day change
            change = (hist.iloc[-1]['Close'] - hist.iloc[0]['Open']) / hist.iloc[0]['Open'] * 100

            # Invert for quote currencies
            if currency in ["JPY", "CAD", "CHF"]:
                change = -change

            strength[currency] = round(change, 2)

        # Add USD (inverse of average)
        if strength:
            strength["USD"] = round(-sum(strength.values()) / len(strength), 2)

        return strength

    def get_market_sentiment(self) -> Dict[str, Any]:
        """Get overall market sentiment indicators"""
        sentiment: Dict[str, Any] = {}

        # VIX for volatility/fear
        vix_hist = self._history("^VIX", period="5d")
        if vix_hist is None or vix_hist.empty:
            vix_hist = self._history("^VIX", period="1mo")

        # Dollar Index for USD strength
        dxy_hist = self._history("DX-Y.NYB", period="5d")
        if dxy_hist is None or dxy_hist.empty:
            dxy_hist = self._history("DX-Y.NYB", period="1mo")

        if vix_hist is not None and not vix_hist.empty:
            vix_level = float(vix_hist.iloc[-1]['Close'])
            sentiment["vix"] = vix_level
            sentiment["fear_level"] = "HIGH" if vix_level > 20 else "MODERATE" if vix_level > 15 else "LOW"

        if dxy_hist is not None and not dxy_hist.empty:
            dxy_level = float(dxy_hist.iloc[-1]['Close'])
            dxy_change = float((dxy_hist.iloc[-1]['Close'] - dxy_hist.iloc[0]['Open']) / dxy_hist.iloc[0]['Open'] * 100)
            sentiment["dollar_index"] = dxy_level
            sentiment["dollar_trend"] = "STRENGTHENING" if dxy_change > 0.5 else "WEAKENING" if dxy_change < -0.5 else "NEUTRAL"

        return sentiment


def main():
    """Test Yahoo Finance provider"""
    logging.basicConfig(level=logging.INFO)

    provider = YahooFinanceProvider()

    if not YFINANCE_AVAILABLE:
        print("❌ yfinance not installed. Install with: pip install yfinance")
        return

    print("\n=== Market Indices ===")
    indices = provider.get_market_indices()
    for name, row in indices.items():
        print(f"{name}: {row}")

    print("\n=== Currency Strength ===")
    strength = provider.get_currency_strength()
    for name, val in strength.items():
        print(f"{name}: {val:+.2f}%")

    print("\n=== Market Sentiment ===")
    sentiment = provider.get_market_sentiment()
    for name, val in sentiment.items():
        print(f"{name}: {val}")


if __name__ == "__main__":
    main()
