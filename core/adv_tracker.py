"""ADV Tracker — per-symbol Average Daily Volume tracking for capacity analysis."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class ADVTracker:
    def __init__(self, window_days: int = 20):
        self.window_days = window_days
        self._volume_history: Dict[str, List[float]] = defaultdict(list)
        self._last_update: Dict[str, float] = {}
        self._adv_cache: Dict[str, float] = {}

    def update(self, symbol: str, volume: float, timestamp: float = 0.0):
        if timestamp == 0:
            timestamp = time.time()
        self._volume_history[symbol].append(volume)
        # Keep only last window_days * 24 bars (H1 assumed)
        max_bars = self.window_days * 24
        if len(self._volume_history[symbol]) > max_bars:
            self._volume_history[symbol] = self._volume_history[symbol][-max_bars:]
        self._last_update[symbol] = timestamp
        # Invalidate cache
        self._adv_cache.pop(symbol, None)

    def get_adv(self, symbol: str) -> float:
        if symbol in self._adv_cache:
            return self._adv_cache[symbol]
        vols = self._volume_history.get(symbol, [])
        if not vols:
            return 0.0
        adv = sum(vols) / len(vols)
        self._adv_cache[symbol] = adv
        return adv

    def get_capacity_pct(self, symbol: str, trade_volume: float) -> float:
        adv = self.get_adv(symbol)
        if adv <= 0:
            return 0.0
        return trade_volume / adv

    def get_status(self) -> Dict[str, Any]:
        return {
            "symbols": {sym: {"adv": round(self.get_adv(sym), 0), "bars": len(vols)} for sym, vols in self._volume_history.items()},
            "tracked_symbols": len(self._volume_history),
        }
