"""Scenario Generator — generates edge case + synthetic market scenarios for stress testing.

Generates realistic market scenarios including:
  - Normal trending/ranging markets
  - Volatility spikes and flash crashes
  - Gap scenarios (weekend/news)
  - Liquidity droughts
  - Correlation breakdowns
  - Regime transitions
  - News-driven moves
  - Black swan events

Uses Kronos synthetic data engine when available, falls back to statistical generation.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Scenario:
    name: str
    category: str  # normal, stress, edge, black_swan
    description: str
    bars: int
    expected_behavior: str
    severity: float  # 0-1
    data: Optional[np.ndarray] = None  # OHLCV [N, 5]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "category": self.category,
            "description": self.description, "bars": self.bars,
            "expected_behavior": self.expected_behavior,
            "severity": self.severity,
        }


class ScenarioGenerator:
    """Generates market scenarios for stress testing."""

    SCENARIOS = {
        # --- NORMAL ---
        "bull_trend": {"category": "normal", "drift": 0.0003, "vol": 0.008, "bars": 500,
                       "description": "Steady uptrend with normal volatility", "severity": 0.1},
        "bear_trend": {"category": "normal", "drift": -0.0003, "vol": 0.008, "bars": 500,
                        "description": "Steady downtrend with normal volatility", "severity": 0.1},
        "sideways_range": {"category": "normal", "drift": 0.0, "vol": 0.005, "bars": 500,
                           "description": "Choppy sideways market", "severity": 0.1},
        "low_vol_grind": {"category": "normal", "drift": 0.0001, "vol": 0.003, "bars": 500,
                          "description": "Low volatility grind up", "severity": 0.15},

        # --- STRESS ---
        "volatility_spike": {"category": "stress", "drift": 0.0, "vol": 0.025, "bars": 300,
                             "description": "Sudden vol expansion (VIX-like spike)", "severity": 0.5},
        "flash_crash": {"category": "stress", "drift": -0.002, "vol": 0.03, "bars": 200,
                        "description": "Rapid 3-5% drop in 50 bars then partial recovery", "severity": 0.7},
        "flash_pump": {"category": "stress", "drift": 0.002, "vol": 0.03, "bars": 200,
                       "description": "Rapid 3-5% rise in 50 bars then partial retracement", "severity": 0.6},
        "liquidity_drought": {"category": "stress", "drift": 0.0, "vol": 0.015, "bars": 200,
                              "description": "Wide spreads, thin books, erratic moves", "severity": 0.6},
        "correlation_breakdown": {"category": "stress", "drift": 0.0, "vol": 0.02, "bars": 300,
                                  "description": "Normal pairs decorrelate suddenly", "severity": 0.5},
        "regime_transition": {"category": "stress", "drift": 0.0001, "vol": 0.012, "bars": 400,
                              "description": "Smooth transition from low-vol range to high-vol trend", "severity": 0.4},

        # --- EDGE CASES ---
        "gap_up_news": {"category": "edge", "drift": 0.005, "vol": 0.01, "bars": 200,
                        "description": "Positive news gap then continuation or fade", "severity": 0.4},
        "gap_down_news": {"category": "edge", "drift": -0.005, "vol": 0.01, "bars": 200,
                          "description": "Negative news gap then continuation or fade", "severity": 0.4},
        "whipsaw_range": {"category": "edge", "drift": 0.0, "vol": 0.015, "bars": 300,
                          "description": "Multiple false breakouts in a range", "severity": 0.5},
        "mean_reversion_extreme": {"category": "edge", "drift": 0.0, "vol": 0.02, "bars": 300,
                                   "description": "Price moves 4+ sigma then reverts", "severity": 0.5},
        "trend_exhaustion": {"category": "edge", "drift": 0.0002, "vol": 0.01, "bars": 400,
                             "description": "Strong trend that slowly loses momentum", "severity": 0.3},
        "false_breakout": {"category": "edge", "drift": 0.0, "vol": 0.012, "bars": 250,
                           "description": "Breaks key level then immediately reverses", "severity": 0.4},

        # --- BLACK SWAN ---
        "black_swan_crash": {"category": "black_swan", "drift": -0.01, "vol": 0.05, "bars": 150,
                             "description": "Extreme crash (2008/2020 style)", "severity": 0.9},
        "black_swan_spike": {"category": "black_swan", "drift": 0.01, "vol": 0.05, "bars": 150,
                             "description": "Extreme spike (short squeeze style)", "severity": 0.8},
        "slow_bleed": {"category": "black_swan", "drift": -0.0005, "vol": 0.01, "bars": 600,
                       "description": "Persistent slow decline (death by 1000 cuts)", "severity": 0.6},
    }

    def __init__(self, seed: Optional[int] = None):
        self._rng = np.random.RandomState(seed or int(time.time()) % 10000)

    def generate(self, scenario_name: str, base_price: float = 1.1000) -> Scenario:
        if scenario_name not in self.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_name}")
        cfg = self.SCENARIOS[scenario_name]
        ohlcv = self._generate_ohlcv(
            bars=cfg["bars"], drift=cfg["drift"], vol=cfg["vol"], base_price=base_price
        )
        # Apply scenario-specific modifications
        if scenario_name == "flash_crash":
            ohlcv = self._apply_flash_crash(ohlcv, severity=0.04)
        elif scenario_name == "flash_pump":
            ohlcv = self._apply_flash_pump(ohlcv, severity=0.04)
        elif scenario_name == "gap_up_news":
            ohlcv = self._apply_gap(ohlcv, gap_pct=0.008, direction=1)
        elif scenario_name == "gap_down_news":
            ohlcv = self._apply_gap(ohlcv, gap_pct=0.008, direction=-1)
        elif scenario_name == "liquidity_drought":
            ohlcv = self._apply_liquidity_drought(ohlcv)
        elif scenario_name == "whipsaw_range":
            ohlcv = self._apply_whipsaw(ohlcv)
        elif scenario_name == "mean_reversion_extreme":
            ohlcv = self._apply_extreme_move(ohlcv, sigma=4.0)
        elif scenario_name == "false_breakout":
            ohlcv = self._apply_false_breakout(ohlcv)

        return Scenario(
            name=scenario_name, category=cfg["category"],
            description=cfg["description"], bars=len(ohlcv),
            expected_behavior=cfg["description"],
            severity=cfg["severity"], data=ohlcv,
        )

    def generate_all(self, base_price: float = 1.1000) -> List[Scenario]:
        return [self.generate(name, base_price) for name in self.SCENARIOS]

    def generate_suite(self, n_trades_target: int = 150, base_price: float = 1.1000) -> List[Scenario]:
        """Generate enough scenarios to produce n_trades_target trades."""
        scenarios = self.generate_all(base_price)
        # Repeat high-severity scenarios for more coverage
        total_bars = sum(s.bars for s in scenarios)
        trades_per_bar = n_trades_target / max(total_bars, 1)
        # If we need more, duplicate stress/edge scenarios
        if trades_per_bar < 0.3:
            extra = []
            for s in scenarios:
                if s.severity >= 0.4:
                    extra.append(s)
            scenarios.extend(extra)
        return scenarios

    def _generate_ohlcv(self, bars: int, drift: float, vol: float, base_price: float) -> np.ndarray:
        returns = self._rng.normal(drift, vol, bars)
        close = base_price * np.exp(np.cumsum(returns))
        # Generate OHLV from close
        high = close * (1 + np.abs(self._rng.normal(0, vol * 0.5, bars)))
        low = close * (1 - np.abs(self._rng.normal(0, vol * 0.5, bars)))
        opn = np.roll(close, 1)
        opn[0] = base_price
        volume = self._rng.lognormal(10, 1, bars)
        return np.column_stack([opn, high, low, close, volume])

    def _apply_flash_crash(self, ohlcv: np.ndarray, severity: float = 0.04) -> np.ndarray:
        n = len(ohlcv)
        crash_start = n // 3
        crash_end = crash_start + min(50, n // 4)
        recovery_end = min(crash_end + 50, n)
        # Crash phase
        for i in range(crash_start, min(crash_end, n)):
            factor = 1 - severity * (i - crash_start) / (crash_end - crash_start)
            ohlcv[i, :4] *= factor
            ohlcv[i, 4] *= 3  # Volume spike
        # Recovery phase (partial)
        for i in range(crash_end, recovery_end):
            factor = 1 + (severity * 0.5) * (i - crash_end) / (recovery_end - crash_end)
            ohlcv[i, :4] *= factor
        return ohlcv

    def _apply_flash_pump(self, ohlcv: np.ndarray, severity: float = 0.04) -> np.ndarray:
        n = len(ohlcv)
        pump_start = n // 3
        pump_end = pump_start + min(50, n // 4)
        retrace_end = min(pump_end + 50, n)
        for i in range(pump_start, min(pump_end, n)):
            factor = 1 + severity * (i - pump_start) / (pump_end - pump_start)
            ohlcv[i, :4] *= factor
            ohlcv[i, 4] *= 3
        for i in range(pump_end, retrace_end):
            factor = 1 - (severity * 0.5) * (i - pump_end) / (retrace_end - pump_end)
            ohlcv[i, :4] *= factor
        return ohlcv

    def _apply_gap(self, ohlcv: np.ndarray, gap_pct: float, direction: int) -> np.ndarray:
        gap_bar = len(ohlcv) // 4
        ohlcv[gap_bar:, :4] *= (1 + direction * gap_pct)
        ohlcv[gap_bar, 4] *= 5  # Volume spike on gap
        return ohlcv

    def _apply_liquidity_drought(self, ohlcv: np.ndarray) -> np.ndarray:
        n = len(ohlcv)
        start = n // 3
        end = start + n // 3
        for i in range(start, min(end, n)):
            noise = self._rng.normal(0, 0.003, 4)
            ohlcv[i, :4] += noise * ohlcv[i, 0]
            ohlcv[i, 4] *= 0.2  # Thin volume
        return ohlcv

    def _apply_whipsaw(self, ohlcv: np.ndarray) -> np.ndarray:
        n = len(ohlcv)
        base = ohlcv[n // 4, 3]
        for i in range(n // 4, 3 * n // 4):
            cycle = math.sin(2 * math.pi * (i - n // 4) / 50) * 0.01
            ohlcv[i, :4] = base * (1 + cycle)
        return ohlcv

    def _apply_extreme_move(self, ohlcv: np.ndarray, sigma: float = 4.0) -> np.ndarray:
        n = len(ohlcv)
        move_bar = n // 3
        # Extreme move
        direction = self._rng.choice([-1, 1])
        for i in range(move_bar, min(move_bar + 30, n)):
            factor = direction * sigma * 0.003 * (1 - (i - move_bar) / 30)
            ohlcv[i, :4] *= (1 + factor)
        # Reversion
        for i in range(move_bar + 30, min(move_bar + 100, n)):
            factor = -direction * sigma * 0.002 * (1 - (i - move_bar - 30) / 70)
            ohlcv[i, :4] *= (1 + factor)
        return ohlcv

    def _apply_false_breakout(self, ohlcv: np.ndarray) -> np.ndarray:
        n = len(ohlcv)
        base = np.mean(ohlcv[:n // 4, 3])
        breakout_bar = n // 2
        # Break above
        for i in range(breakout_bar, breakout_bar + 15):
            ohlcv[i, :4] = base * 1.015 * (1 + 0.002 * (i - breakout_bar))
        # Sharp reversal
        for i in range(breakout_bar + 15, breakout_bar + 40):
            ohlcv[i, :4] = base * 1.015 * (1 - 0.003 * (i - breakout_bar - 15))
        return ohlcv
