"""Sentiment Veto & Contrarian Signal Filter.

Implements Part1 §3.5-3.6: Sentiment-based trade veto and fade logic:
    COT_Position = (Commercial_Net_Long - Commercial_Net_Short) / Total_Open_Interest
    Z_COT = (COT - rolling_mean) / rolling_std
    When commercial hedgers > 90th percentile net-long while retail crowded short:
        Execute swing positions in commodities/FX for macro reversals

    Retail Sentiment Extremes:
        When retail sentiment > 85% one-sided: veto breakout entries (fade)
        Sudden +3sigma ticker search spikes + overbought = mean-reversion short
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np


@dataclass
class SentimentVetoResult:
    """Result of sentiment veto analysis."""
    veto_trade: bool
    fade_signal: bool
    cot_commercial_index: float
    retail_sentiment_score: float
    z_cot: float
    is_extreme_sentiment: bool
    is_cot_extreme: bool
    reason: str
    recommended_action: str     # "PROCEED", "VETO", "FADE", "REDUCE_SIZE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "veto_trade": self.veto_trade,
            "fade_signal": self.fade_signal,
            "cot_commercial_index": round(self.cot_commercial_index, 2),
            "retail_sentiment_score": round(self.retail_sentiment_score, 2),
            "z_cot": round(self.z_cot, 4),
            "is_extreme_sentiment": self.is_extreme_sentiment,
            "is_cot_extreme": self.is_cot_extreme,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
        }


class SentimentVetoEngine:
    """Sentiment-based trade veto and contrarian fade signal generator.

    Evaluates retail sentiment extremes and COT positioning to:
    1. VETO breakout entries when retail sentiment is extremely one-sided
    2. FADE when commercial hedgers show extreme positioning
    3. Reduce position size when sentiment indicates elevated risk

    Typical usage:
        engine = SentimentVetoEngine()
        result = engine.evaluate(
            retail_sentiment=0.88,
            cot_commercial_index=85.0,
            trade_direction="BUY",
        )
        if result.veto_trade:
            skip_trade()
    """

    # Sentiment thresholds (Part1 §3.6)
    EXTREME_SENTIMENT_THRESHOLD = 85.0     # Retail sentiment > 85% one-sided
    SPIKE_THRESHOLD_SIGMA = 3.0            # +3 sigma spike in ticker searches
    OVERBOUGHT_LEVEL = 70.0                # RSI-like overbought

    # COT thresholds (Part1 §3.5)
    COT_COMMERCIAL_EXTREME_LONG = 90.0     # 90th percentile
    COT_COMMERCIAL_EXTREME_SHORT = 10.0    # 10th percentile

    def __init__(
        self,
        extreme_sentiment_threshold: float = 85.0,
        cot_extreme_long: float = 90.0,
        cot_extreme_short: float = 10.0,
    ):
        self.extreme_sentiment_threshold = float(extreme_sentiment_threshold)
        self.cot_extreme_long = float(cot_extreme_long)
        self.cot_extreme_short = float(cot_extreme_short)
        self.cot_history: List[float] = []
        self.sentiment_history: List[float] = []

    def evaluate(
        self,
        retail_sentiment: float,
        cot_commercial_index: float,
        trade_direction: str = "BUY",
        cot_open_interest: Optional[float] = None,
        cot_commercial_net: Optional[float] = None,
        ticker_search_spike: float = 0.0,
        overbought_oversold: float = 50.0,
    ) -> SentimentVetoResult:
        """Evaluate sentiment conditions for trade veto or fade.

        Args:
            retail_sentiment: Retail sentiment score 0-100 (higher = more bullish).
            cot_commercial_index: COT commercial hedger index 0-100.
            trade_direction: "BUY" or "SELL".
            ticker_search_spike: Number of sigma above mean for ticker searches.
            overbought_oversold: Oscillator reading (0=oversold, 100=overbought).
        """
        sentiment = max(0.0, min(100.0, float(retail_sentiment)))
        cot_idx = max(0.0, min(100.0, float(cot_commercial_index)))
        spike_sigma = max(0.0, float(ticker_search_spike))

        # Track history
        self.cot_history.append(cot_idx)
        self.sentiment_history.append(sentiment)
        if len(self.cot_history) > 252:
            self.cot_history = self.cot_history[-252:]
        if len(self.sentiment_history) > 252:
            self.sentiment_history = self.sentiment_history[-252:]

        # Z-score of COT
        z_cot = 0.0
        if len(self.cot_history) >= 20:
            arr = np.array(self.cot_history)
            mean = float(np.mean(arr))
            std = float(np.std(arr)) + 1e-8
            z_cot = (cot_idx - mean) / std

        # Retail sentiment extreme check (Part1 §3.6)
        is_extreme = sentiment > self.extreme_sentiment_threshold

        # COT extreme check (Part1 §3.5)
        is_cot_extreme_long = cot_idx > self.cot_extreme_long
        is_cot_extreme_short = cot_idx < self.cot_extreme_short

        # Ticker spike check
        is_spike = spike_sigma >= self.SPIKE_THRESHOLD_SIGMA

        # Determine action
        veto = False
        fade = False
        reason = "NO_SENTIMENT_EXTREME"
        action = "PROCEED"

        direction = str(trade_direction).upper()

        # Rule 1: Retail sentiment extreme → VETO breakout entries
        if is_extreme:
            if direction == "BUY" and sentiment > 85:
                veto = True
                reason = f"RETAIL_SENTIMENT_EXTREME_BULLISH:{sentiment:.0f}%>85%"
                action = "VETO"
            elif direction == "SELL" and sentiment < 15:
                veto = True
                reason = f"RETAIL_SENTIMENT_EXTREME_BEARISH:{sentiment:.0f}%<15%"
                action = "VETO"

        # Rule 2: Ticker spike + overbought → mean-reversion fade
        if is_spike and overbought_oversold > self.OVERBOUGHT_LEVEL:
            fade = True
            if not veto:
                reason = f"TICKER_SPIKE_{spike_sigma:.1f}SIGMA+OVERBOUGHT"
                action = "FADE"

        # Rule 3: COT extreme → contrarian fade
        if is_cot_extreme_long and direction == "BUY":
            # Commercial hedgers extreme long → contrarian bearish signal
            fade = True
            if not veto:
                reason = f"COT_COMMERCIAL_EXTREME_LONG:{cot_idx:.0f}%>90%"
                action = "FADE"
        elif is_cot_extreme_short and direction == "SELL":
            fade = True
            if not veto:
                reason = f"COT_COMMERCIAL_EXTREME_SHORT:{cot_idx:.0f}%<10%"
                action = "FADE"

        # Rule 4: Z-COT extreme
        if abs(z_cot) > 2.0:
            if not veto and not fade:
                reason = f"Z_COT_EXTREME:{z_cot:.2f}"
                action = "REDUCE_SIZE"

        return SentimentVetoResult(
            veto_trade=veto,
            fade_signal=fade,
            cot_commercial_index=cot_idx,
            retail_sentiment_score=sentiment,
            z_cot=z_cot,
            is_extreme_sentiment=is_extreme,
            is_cot_extreme=is_cot_extreme_long or is_cot_extreme_short,
            reason=reason,
            recommended_action=action,
        )

    def calculate_cot_index(
        self,
        commercial_net_long: float,
        commercial_net_short: float,
        total_open_interest: float,
    ) -> float:
        """CFTC COT Commercial Index (Part1 §3.5):
        COT_Position = (Commercial_Net_Long - Commercial_Net_Short) / Total_Open_Interest
        """
        if total_open_interest <= 0:
            return 50.0
        return float((commercial_net_long - commercial_net_short) / total_open_interest)


# Canonical aliases
SentimentFilter = SentimentVetoEngine
ContrarianSentimentEngine = SentimentVetoEngine
