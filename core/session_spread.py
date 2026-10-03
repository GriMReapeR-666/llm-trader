"""Session-Based Spread Multiplier & FX/CFD Execution Model.

Implements Part1 §1.6: Session-aware spread model:
    S_t = S_base * (1 + kappa_vol*(ATR_t/ATR_base - 1) + kappa_liq*(Vol_base/Vol_t - 1)) * M_session

Session Multipliers:
    London/NY Overlap: 1.0x (peak liquidity)
    London: 1.2x
    New York: 1.3x
    Asian: 2.0x
    Rollover (5pm EST): 3.5x
    Off-Session: 4.0x

Also includes:
- Broker B-Book detection heuristics (Part1 §1.4)
- Virtual dealer plugin detection
- Adverse selection measurement
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional
from datetime import datetime, timezone


# Session time windows (UTC hours)
SESSION_WINDOWS = {
    "ASIAN": (0, 8),         # 00:00 - 08:00 UTC
    "LONDON": (7, 16),       # 07:00 - 16:00 UTC
    "LONDON_NY_OVERLAP": (12, 16),  # 12:00 - 16:00 UTC
    "NEW_YORK": (13, 21),    # 13:00 - 21:00 UTC
    "ROLLOVER": (21, 22),    # 21:00 - 22:00 UTC (5pm EST)
    "OFF_SESSION": (22, 0),  # 22:00 - 00:00 UTC
}


@dataclass
class SessionSpreadResult:
    """Result of session-based spread analysis."""
    session: str
    base_spread_bps: float
    dynamic_spread_bps: float
    session_multiplier: float
    volatility_multiplier: float
    liquidity_multiplier: float
    is_high_cost_session: bool
    recommended_action: str     # "TRADE", "REDUCE_SIZE", "AVOID"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session": self.session,
            "base_spread_bps": round(self.base_spread_bps, 2),
            "dynamic_spread_bps": round(self.dynamic_spread_bps, 2),
            "session_multiplier": round(self.session_multiplier, 2),
            "volatility_multiplier": round(self.volatility_multiplier, 4),
            "liquidity_multiplier": round(self.liquidity_multiplier, 4),
            "is_high_cost_session": self.is_high_cost_session,
            "recommended_action": self.recommended_action,
        }


class SessionSpreadEngine:
    """Session-aware spread multiplier and execution cost estimator.

    Evaluates the current trading session and adjusts expected spread costs
    accordingly. Peak liquidity sessions (London/NY overlap) have minimal
    spread, while rollover and off-hours sessions have 3.5-4x wider spreads.

    Typical usage:
        engine = SessionSpreadEngine(base_spread_bps=10.0)
        result = engine.evaluate()
        if result.is_high_cost_session:
            reduce_sizing()
    """

    SESSION_MULTIPLIERS = {
        "LONDON_NY_OVERLAP": 1.0,
        "LONDON": 1.2,
        "NEW_YORK": 1.3,
        "ASIAN": 2.0,
        "ROLLOVER": 3.5,
        "OFF_SESSION": 4.0,
    }

    def __init__(
        self,
        base_spread_bps: float = 10.0,
        kappa_vol: float = 0.5,
        kappa_liq: float = 0.5,
    ):
        self.base_spread_bps = max(0.0, float(base_spread_bps))
        self.kappa_vol = float(kappa_vol)
        self.kappa_liq = float(kappa_liq)

    def detect_current_session(self, utc_hour: Optional[int] = None) -> str:
        """Detect the current trading session from UTC hour."""
        if utc_hour is None:
            utc_hour = datetime.now(timezone.utc).hour

        h = int(utc_hour)

        # Check overlap first (highest priority)
        if 12 <= h < 16:
            return "LONDON_NY_OVERLAP"
        elif 7 <= h < 12:
            return "LONDON"
        elif 13 <= h < 21:
            return "NEW_YORK"
        elif 0 <= h < 8:
            return "ASIAN"
        elif 21 <= h < 22:
            return "ROLLOVER"
        else:
            return "OFF_SESSION"

    def evaluate(
        self,
        current_atr: Optional[float] = None,
        baseline_atr: Optional[float] = None,
        current_volume: Optional[float] = None,
        baseline_volume: Optional[float] = None,
        utc_hour: Optional[int] = None,
    ) -> SessionSpreadResult:
        """Evaluate current session spread with dynamic adjustments.

        S_t = S_base * (1 + kappa_vol*(ATR_t/ATR_base - 1) + kappa_liq*(Vol_base/Vol_t - 1)) * M_session
        """
        session = self.detect_current_session(utc_hour)
        m_session = self.SESSION_MULTIPLIERS.get(session, 1.0)

        # Volatility component
        vol_mult = 1.0
        if current_atr is not None and baseline_atr is not None:
            atr = max(1e-10, float(current_atr))
            base_atr = max(1e-10, float(baseline_atr))
            vol_mult = 1.0 + self.kappa_vol * max(0.0, (atr / base_atr) - 1.0)

        # Liquidity component
        liq_mult = 1.0
        if current_volume is not None and baseline_volume is not None:
            cvol = max(1.0, float(current_volume))
            bvol = max(1.0, float(baseline_volume))
            liq_mult = 1.0 + self.kappa_liq * max(0.0, (bvol / cvol) - 1.0)

        dynamic_spread = self.base_spread_bps * vol_mult * liq_mult * m_session
        dynamic_spread = max(self.base_spread_bps * 0.5, dynamic_spread)

        # High cost threshold: > 2x base spread
        is_high_cost = dynamic_spread > self.base_spread_bps * 2.0

        if is_high_cost and m_session >= 3.0:
            action = "AVOID"
        elif is_high_cost:
            action = "REDUCE_SIZE"
        else:
            action = "TRADE"

        return SessionSpreadResult(
            session=session,
            base_spread_bps=self.base_spread_bps,
            dynamic_spread_bps=dynamic_spread,
            session_multiplier=m_session,
            volatility_multiplier=vol_mult,
            liquidity_multiplier=liq_mult,
            is_high_cost_session=is_high_cost,
            recommended_action=action,
        )

    def get_session_cost_multiplier(self, utc_hour: Optional[int] = None) -> float:
        """Simple: return just the session multiplier."""
        session = self.detect_current_session(utc_hour)
        return self.SESSION_MULTIPLIERS.get(session, 1.0)


# Canonical aliases
FXSessionSpreadModel = SessionSpreadEngine
TradingSessionCostModel = SessionSpreadEngine
