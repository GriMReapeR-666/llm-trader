"""Macro Event Alpha Strategy Plugin.

Thin BaseStrategy wrapper around core.macro_event_alpha_engine.MacroEventAlphaEngine.
Evaluates scheduled macroeconomic releases (NFP, CPI, Central Bank Rates), surprise decomposition,
and 3-phase post-announcement reaction dynamics.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from strategies.base import BaseStrategy, StrategySignal
from core.macro_event_alpha_engine import MacroEventAlphaEngine


class MacroEventAlphaStrategy(BaseStrategy):
    """Systematic Macroeconomic Event Alpha Strategy."""

    name: str = "macro_event_alpha"
    description: str = "Multi-Phase Macro Announcement Surprise Decomposition & Directional Flow"
    symbols: List[str] = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    timeframe: str = "H1"
    trading_style: str = "LONG_TERM"
    score_type: str = "MACRO_SURPRISE"

    def __init__(self):
        super().__init__()
        self.engine = MacroEventAlphaEngine()

    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Evaluate macroeconomic context against current price and release data."""
        symbol = str(context.get("symbol", "EURUSD")).upper()
        price = float(context.get("price", 0.0) or 0.0)
        digits = int(context.get("digits", 5) or 5)
        features = context.get("features", {}) if isinstance(context.get("features"), dict) else {}
        macro = context.get("macro", {}) if isinstance(context.get("macro"), dict) else {}

        if price <= 0.0:
            return self.hold(symbol, "Invalid price for macro event alpha evaluation")

        event_name = str(macro.get("event_name") or features.get("event_name") or "CPI").upper()
        actual = float(macro.get("actual") or features.get("macro_actual") or 0.0)
        consensus = float(macro.get("consensus") or features.get("macro_consensus") or 0.0)
        previous = float(macro.get("previous") or features.get("macro_previous") or 0.0)

        # If no release numbers present, hold
        if actual == 0.0 and consensus == 0.0:
            return self.hold(symbol, "No pending or active macro announcement release data")

        now_ts = float(context.get("timestamp") or time.time())
        event_ts = float(macro.get("event_timestamp_unix") or features.get("event_timestamp_unix") or (now_ts - 300.0))

        point = float(context.get("point", 0.0001) or 0.0001)
        atr = float(features.get("atr", 25 * point) or (25 * point))
        quote_spread = float(context.get("spread", 1.5 * point) or (1.5 * point))
        baseline_spread = float(context.get("baseline_spread", 1.2 * point) or (1.2 * point))

        spike_high = float(features.get("spike_high") or (price + 0.5 * atr))
        spike_low = float(features.get("spike_low") or (price - 0.5 * atr))

        macro_regime = str(context.get("regime") or macro.get("macro_regime") or "EXPANSION").upper()
        cot_pct = float(macro.get("cot_percentile") or features.get("cot_percentile") or 50.0)

        eval_res = self.engine.evaluate_announcement(
            event_name=event_name,
            symbol=symbol,
            actual=actual,
            consensus=consensus,
            previous=previous,
            event_timestamp_unix=event_ts,
            current_timestamp_unix=now_ts,
            current_price=price,
            quote_spread=quote_spread,
            baseline_spread=baseline_spread,
            spike_high=spike_high,
            spike_low=spike_low,
            atr=atr,
            macro_regime=macro_regime,
            cot_percentile=cot_pct,
        )

        decision = eval_res.get("decision", "HOLD")
        side = eval_res.get("side", "BUY")
        proposal = eval_res.get("proposal")
        surprise_res = eval_res.get("surprise_decomposition", {})
        z_delta = surprise_res.get("z_delta", 0.0)

        metadata = {
            "event_name": event_name,
            "decision": decision,
            "z_delta": z_delta,
            "macro_regime": macro_regime,
            "phase": eval_res.get("phase_telemetry", {}).get("phase"),
            "trading_style": self.trading_style,
            "score_type": self.score_type,
        }

        if decision == "CIRCUIT_BREAKER_BLACKOUT":
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="REJECT",
                confidence=0.0,
                rationale=f"Macro Toxic Spike Blackout active for {event_name}",
                metadata=metadata,
            )

        if decision != "EXECUTE" or not proposal:
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                side="HOLD",
                confidence=0.0,
                rationale=f"Macro Alpha state ({decision}) Z_Delta={z_delta:.2f} — waiting for confirmed post-news setup",
                metadata=metadata,
            )

        entry = round(float(proposal.get("entry_price", price)), digits)
        sl = round(float(proposal.get("stop_loss", price - 2 * atr)), digits)
        tp = round(float(proposal.get("take_profit", price + 3 * atr)), digits)
        confidence = min(0.95, max(0.50, 0.65 + abs(z_delta) * 0.1))

        return StrategySignal(
            strategy=self.name,
            symbol=symbol,
            side=side,
            confidence=round(confidence, 3),
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            timeframe=self.timeframe,
            rationale=f"Macro Event Trend: {event_name} Z_Delta={z_delta:.2f} ({macro_regime})",
            confluence_score=round(confidence, 2),
            regime_alignment=True,
            tags=["macro", "event_driven", "economic_surprise"],
            metadata=metadata,
        )
