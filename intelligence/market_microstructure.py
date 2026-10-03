"""Persistent, deterministic microstructure observations from broker DOM/quotes."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict


class MicrostructureMonitor:
    def __init__(self, store, *, max_quote_gap_seconds: float = 5.0, spread_rolling_window: int = 20, spread_spike_cooldown: int = 3):
        self.store = store
        self.max_quote_gap_seconds = max_quote_gap_seconds
        self.spread_rolling_window = spread_rolling_window
        self.spread_spike_cooldown = spread_spike_cooldown

    @staticmethod
    def _time(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        
    def reset(self, symbol: str) -> None:
        """Clear the rolling state for a symbol."""
        self.store.set_state(f"microstructure:{symbol}", {})

    def observe(self, symbol: str, dom: Dict[str, Any]) -> Dict[str, Any]:
        capture_value = dom.get("captured_at")
        capture_missing = capture_value in (None, "")
        now_text = str(capture_value) if not capture_missing else ""
        previous = self.store.get_state(f"microstructure:{symbol}", {}) or {}
        try:
            if capture_missing:
                raise ValueError("missing capture timestamp")
            now = self._time(now_text)
            then = self._time(previous["captured_at"]) if previous.get("captured_at") else now
            elapsed = max(0.001, (now - then).total_seconds())
        except (TypeError, ValueError):
            elapsed = float("inf")

        flags = []
        if capture_missing or elapsed == float("inf"):
            flags.append("missing or invalid DOM capture timestamp")

        try:
            spread = float(dom.get("spread"))
            spread_valid = math.isfinite(spread) and spread >= 0
        except (TypeError, ValueError):
            spread, spread_valid = 0.0, False
        if not spread_valid:
            flags.append("invalid spread")

        try:
            raw_depth = float(dom.get("dom_depth"))
            depth_valid = math.isfinite(raw_depth) and raw_depth >= 0 and raw_depth.is_integer()
            depth = int(raw_depth) if depth_valid else 0
        except (TypeError, ValueError):
            depth, depth_valid = 0, False
        if not depth_valid:
            flags.append("invalid DOM depth")

        # Some brokers expose a valid DOM snapshot without a normalized
        # imbalance field.  Treat an omitted field as neutral/unknown (the
        # setup dependency gate can still require it), but reject malformed
        # values that are explicitly supplied.
        if "imbalance" not in dom or dom.get("imbalance") is None:
            imbalance, imbalance_valid = 0.0, True
        else:
            try:
                imbalance = float(dom.get("imbalance"))
                imbalance_valid = math.isfinite(imbalance)
            except (TypeError, ValueError):
                imbalance, imbalance_valid = 0.0, False
        if not imbalance_valid:
            flags.append("invalid DOM imbalance")
        
        prior_depth = int(previous.get("depth", depth) or depth)
        prior_imbalance = float(previous.get("imbalance", imbalance) or imbalance)
        spread_history = previous.get("spread_history", [])
        spread_spike_cooldown = int(previous.get("spread_spike_cooldown", 0))
        consecutive_quote_gaps = int(previous.get("consecutive_quote_gaps", 0))
        
        if not dom.get("available", False):
            flags.append("DOM unavailable")
            
        if depth < 2:
            flags.append("CRITICAL: DOM depth < 2")
        elif depth < 5:
            flags.append("WARNING: DOM depth < 5")
            
        is_quote_gap = False
        if previous and elapsed > self.max_quote_gap_seconds:
            is_quote_gap = True
            flags.append("quote/DOM gap exceeds threshold")
            consecutive_quote_gaps += 1
        else:
            consecutive_quote_gaps = 0
            
        if consecutive_quote_gaps >= 3:
            flags.append("persistent quote gaps")

        # Quote flicker detection: rapid bid/ask flips or spread jitter (>3 flips in window)
        tick_history = previous.get("tick_history", [])
        tick_history = [t for t in tick_history if isinstance(t, dict) and elapsed < 10.0]
        flicker_detected = False
        if len(tick_history) >= 3:
            spread_flips = 0
            for earlier, later in zip(tick_history, tick_history[1:]):
                if abs(float(later.get("spread", 0.0)) - float(earlier.get("spread", 0.0))) > 0.00001:
                    spread_flips += 1
            if spread_flips >= 2:
                flicker_detected = True
                flags.append("quote flicker detected")

        median_spread = 0.0
        is_abnormal = False
        if spread_history:
            sorted_history = sorted(spread_history)
            n = len(sorted_history)
            if n % 2 == 1:
                median_spread = sorted_history[n // 2]
            else:
                median_spread = (sorted_history[n // 2 - 1] + sorted_history[n // 2]) / 2.0
                
            if median_spread > 0 and spread > median_spread * 2:
                is_abnormal = True

        if is_abnormal:
            spread_spike_cooldown = self.spread_spike_cooldown
        else:
            spread_spike_cooldown = max(0, spread_spike_cooldown - 1)
            
        if spread_spike_cooldown > 0:
            flags.append("abnormal spread expansion")
            
        if prior_depth > 0 and depth < prior_depth * 0.5:
            flags.append("liquidity withdrawal")

        # Retail DOM spoofability warning
        spoofable_dom = False
        if depth < 5 or abs(imbalance) > 0.8:
            spoofable_dom = True
            flags.append("WARNING: SPOOFABLE_RETAIL_DOM (shallow depth or extreme imbalance)")
            
        new_spread_history = spread_history + [spread]
        if len(new_spread_history) > self.spread_rolling_window:
            new_spread_history = new_spread_history[-self.spread_rolling_window:]

        new_tick_history = tick_history + [{"spread": spread, "time": now_text}]
        if len(new_tick_history) > 10:
            new_tick_history = new_tick_history[-10:]
            
        observation = {
            "captured_at": now_text, 
            "spread": spread, 
            "depth": depth, 
            "imbalance": imbalance,
            "spread_history": new_spread_history,
            "tick_history": new_tick_history,
            "spread_spike_cooldown": spread_spike_cooldown,
            "consecutive_quote_gaps": consecutive_quote_gaps,
            "spoofable_dom": spoofable_dom
        }
        self.store.set_state(f"microstructure:{symbol}", observation)
        
        critical_flags = [f for f in flags if not f.startswith("WARNING")]
        
        return {
            "spread": spread, "dom_depth": depth, "imbalance": imbalance,
            "imbalance_rate_per_second": (imbalance - prior_imbalance) / elapsed if elapsed != float("inf") else None,
            "elapsed_seconds": elapsed, "flags": flags, "allowed": len(critical_flags) == 0,
            "retail_dom_warning": "Broker DOM can be incomplete/spoofable; it is a filter, not an order-flow truth.",
            "spoofable_dom": spoofable_dom,
            "flicker_detected": flicker_detected,
        }
