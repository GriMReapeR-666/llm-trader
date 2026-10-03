"""Persistent advisory HMM/GARCH updates from completed candles only."""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable

from adaptive_models import OnlineGarch, OnlineHMM


class AdaptiveResearchService:
    def __init__(self, store):
        self.store = store

    def update_from_closes(self, symbol: str, timeframe: str, closes: Iterable[Any], *, completed_bar_time: str = "") -> Dict[str, Any]:
        symbol = str(symbol or "").strip().upper()
        timeframe = str(timeframe or "").strip().upper()
        if not symbol or not timeframe:
            return {"available": False, "reason": "symbol and timeframe are required", "advisory_only": True}
        prices = []
        try:
            close_values = closes or ()
            if isinstance(close_values, (str, bytes)):
                close_values = ()
            iterator = iter(close_values)
        except TypeError:
            iterator = iter(())
        for raw_price in iterator:
            try:
                price = float(raw_price)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(price) and price > 0:
                prices.append(price)
        if len(prices) < 2:
            return {"available": False, "reason": "insufficient completed closes"}
        key = f"adaptive_research:{symbol}:{timeframe}"
        raw_saved = self.store.get_state(key, {})
        saved = raw_saved if isinstance(raw_saved, dict) else {}
        if raw_saved not in ({}, None) and not isinstance(raw_saved, dict):
            return {"available": False, "reason": "malformed persisted adaptive state", "advisory_only": True}
        if completed_bar_time and saved.get("last_completed_bar_time") == completed_bar_time:
            return {**(saved.get("last") or {}), "available": True, "advisory_only": True,
                    "deduplicated": True, "completed_bar_time": completed_bar_time}
        try:
            garch_state = saved.get("garch", {})
            hmm_state = saved.get("hmm", {})
            if not isinstance(garch_state, dict) or not isinstance(hmm_state, dict):
                raise ValueError("adaptive state sections are malformed")
            g = OnlineGarch(**garch_state)
            h = OnlineHMM(**hmm_state)
        except (TypeError, ValueError, OverflowError):
            return {"available": False, "reason": "malformed persisted adaptive state", "advisory_only": True}
        # Only update with the latest completed bar; deduplication is handled by
        # the candle trigger upstream.
        latest_return = math.log(prices[-1] / prices[-2])
        volatility = g.update(latest_return)
        probabilities = h.update(latest_return)
        result = {"available": True, "advisory_only": True, "return": latest_return,
                  "garch_volatility": volatility, "volatility_multiplier": g.size_multiplier(target_volatility=0.001),
                  "hmm_state": h.state, "hmm_probabilities": probabilities,
                  "observations": min(g.observations, h.observations),
                  "completed_bar_time": completed_bar_time}
        self.store.set_state(key, {"garch": g.to_dict(), "hmm": h.to_dict(), "last": result,
                                   "last_completed_bar_time": completed_bar_time})
        return result
