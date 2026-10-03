"""MT5 historical data fetcher: pulls OHLCV bars from MetaTrader5."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# MT5 timeframe map
_TIMEFRAME_MAP = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388,
    "D1": 16408, "W1": 32769, "MN1": 49153,
}


class DataFetcher:
    """Fetches historical OHLCV data from MT5."""

    def __init__(self, client: Any = None):
        self._client = client

    def get_historical_data(
        self,
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        count: int = 1000,
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV bars from MT5.

        Returns a DataFrame with columns: open, high, low, close, time.
        Returns None if MT5 is unavailable.
        """
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                logger.warning("MT5 initialize failed")
                return None
            tf = _TIMEFRAME_MAP.get(timeframe.upper(), 15)
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            mt5.shutdown()
            if rates is None or len(rates) == 0:
                return None
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            return df[["open", "high", "low", "close", "time"]]
        except ImportError:
            logger.warning("MetaTrader5 not installed")
            return None
        except Exception as exc:
            logger.warning("MT5 data fetch failed: %s", exc)
            return None
