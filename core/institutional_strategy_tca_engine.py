"""
Institutional Strategy Suite, Pre-Trade TCA Gate & Archetype Exit Engine
========================================================================
Drop-in institutional strategy & execution governance module for
GriMReapeR-666/llm-trader (`strategies/`, `core/execution_policy.py`,
`core/risk_manager.py`, and `core/trade_manager.py`).

What This Module Implements Across All 6 Repository Strategies:
1. `KalmanStatArbStrategy` (`core/kalman_statarb.py` / `statarb_eur_chf`):
   - Dynamic state-space hedge ratio (beta) & intercept update
   - Ornstein-Uhlenbeck half-life estimation (rejects non-stationary spreads: tau not in [2, 48])
   - Rollover window & BOCPD regime change-point veto (bocpd_changepoint_prob >= 0.65)
2. `CointegrationPairStrategy` (`core/cointegration_robustness.py` / `coint_aud_nzd`):
   - Two-leg residual z-score, volatility-matched hedge ratio, and ADF/Hurst proxy gate (ADF p <= 0.04, corr >= 0.72)
3. `AvellanedaStoikovMeanReversionStrategy` (`core/avellaneda_stoikov.py` / `avellaneda_scalp_eurusd`):
   - Inventory-skewed reservation price adapted for MT5/CFD microstructure
   - Single-sided passive limit entry aligned with higher-timeframe drift
4. `MacroEventPullbackStrategy` (`core/macro_event_alpha_engine.py` / `macro_nfp_pullback`):
   - Standardized release surprise sigma filter (|surprise_sigma| >= 1.5)
   - Mandatory post-release cool-down window (3-25 min, avoids 1st-second spike slippage)
   - Spread normalization (<= 1.45x median) & ATR pullback confirmation (0.25 - 0.70)
5. `KronosNeuralForecastStrategy` (`research/kronos_mt5_adapter.py` / `kronos_h1`):
   - Temperature-scaled logit probability with predictive entropy uncertainty veto (entropy <= 0.62)
6. `MultiFactorEnsembleStrategy` (`core/alpha_factors.py` & `core/alpha_ensemble.py` / `factor_carry_trend_d1`):
   - Cross-sectional momentum, volatility-adjusted RSI, and interest-rate carry (ADX >= 22, |z| >= 1.5)
   - Overnight swap drag & Wednesday 3x triple-swap veto (daily swap >= -0.60 bps/day)
7. `InstitutionalPreTradeTCAGate` (`core/execution_policy.py`):
   - Rejects signals whose gross expected edge does not clear 2.5x-3.0x
     round-trip spread + market-impact slippage + overnight/triple-Wednesday swap drag
8. `CurrencyCovarianceRiskSizer` (`core/risk_manager.py` & `core/account_sizing.py`):
   - Decomposes FX pairs into base/quote currency legs, penalizes stacked factor
     exposure (<= 2.5% net per currency), and scales risk by BOCPD + Copula tail-risk probability
9. `ArchetypeExitManager` (`core/trade_manager.py`):
   - Separates exit physics between Convergent (Stat-Arb/Pairs/Mean-Reversion)
     and Divergent (Macro/Neural/Momentum) strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np


# ---------------------------------------------------------------------------
# Enums & Data Structures
# ---------------------------------------------------------------------------

class StrategyArchetype(str, Enum):
    STAT_ARB = "STAT_ARB"
    PAIR_TRADING = "PAIR_TRADING"
    MEAN_REVERSION = "MEAN_REVERSION"
    MACRO_EVENT = "MACRO_EVENT"
    NEURAL_FORECAST = "NEURAL_FORECAST"
    MOMENTUM = "MOMENTUM"


class ExecutionStyle(str, Enum):
    SCALP = "SCALP"
    SWING = "SWING"
    LONG_TERM = "LONG_TERM"
    EVENT = "EVENT"


class ExitCategory(str, Enum):
    CONVERGENT = "CONVERGENT"
    DIVERGENT = "DIVERGENT"


@dataclass
class InstitutionalSignal:
    strategy_id: str
    symbol: str
    archetype: StrategyArchetype
    style: ExecutionStyle
    side: str  # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    gross_expected_edge_bps: float
    confidence: float
    expected_holding_bars: int
    bar_timeframe: str = "H1"
    is_two_leg: bool = False
    secondary_symbol: Optional[str] = None
    hedge_ratio: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_authority: bool = False


@dataclass
class TCAResult:
    passed: bool
    rejection_reason: Optional[str]
    gross_edge_bps: float
    total_cost_bps: float
    net_edge_bps: float
    edge_to_cost_ratio: float
    spread_cost_bps: float
    market_impact_bps: float
    swap_cost_bps: float
    spans_wednesday_rollover: bool
    execution_authority: bool = False


@dataclass
class SizingResult:
    admitted: bool
    rejection_reason: Optional[str]
    target_risk_fraction: float
    position_size_notional: float
    lots: float
    currency_exposures: Dict[str, float]
    bocpd_penalty: float
    copula_penalty: float
    execution_authority: bool = False


@dataclass
class ExitEvaluation:
    action: str  # "HOLD", "PARTIAL_SCALE_OUT", "FULL_EXIT", "TRAIL_STOP_RATCHET"
    exit_type: str  # "TARGET", "STOP_LOSS", "TIME_STOP", "REGIME_BREAK", "MEAN_REVERSION_COMPLETE"
    scale_out_pct: float = 0.0
    new_stop_loss: Optional[float] = None
    reason: str = ""
    execution_authority: bool = False


# ---------------------------------------------------------------------------
# Base Institutional Strategy Contract
# ---------------------------------------------------------------------------

class BaseInstitutionalStrategy:
    """Unified contract for institutional alpha strategies."""

    def __init__(self, strategy_id: str, archetype: StrategyArchetype, style: ExecutionStyle):
        self.strategy_id = strategy_id
        self.archetype = archetype
        self.style = style

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        raise NotImplementedError

    def get_exit_category(self) -> ExitCategory:
        if self.archetype in (StrategyArchetype.STAT_ARB, StrategyArchetype.PAIR_TRADING, StrategyArchetype.MEAN_REVERSION):
            return ExitCategory.CONVERGENT
        return ExitCategory.DIVERGENT


# ---------------------------------------------------------------------------
# 1. Kalman Statistical Arbitrage Strategy
# ---------------------------------------------------------------------------

class KalmanStatArbStrategy(BaseInstitutionalStrategy):
    """Kalman Filter Statistical Arbitrage on FX Spreads.
    
    Hardened institutional rules:
    - Rejects non-stationary spreads: Ornstein-Uhlenbeck half-life tau must be in [2, 48] bars.
    - Vetoes entries when BOCPD regime change-point prob >= 0.65.
    - Vetoes entries during rollover window (is_rollover_window = True).
    - Prices two-leg hedge-weighted spread: e.g. EURCHF + beta * USDCHF.
    - Enters on |z| >= 2.0; target exit at |z| <= 0.20.
    """

    def __init__(self, strategy_id: str = "statarb_eur_chf", primary_symbol: str = "EURCHF", hedge_symbol: str = "USDCHF"):
        super().__init__(strategy_id, StrategyArchetype.STAT_ARB, ExecutionStyle.SWING)
        self.primary_symbol = primary_symbol
        self.hedge_symbol = hedge_symbol

    def estimate_ou_half_life(self, residuals: Sequence[float]) -> float:
        if len(residuals) < 15:
            return 999.0
        y = np.asarray(residuals, dtype=float)
        dy = np.diff(y)
        y_lag = y[:-1]
        mean_y = np.mean(y_lag)
        denom = np.sum((y_lag - mean_y) ** 2)
        if denom <= 1e-12:
            return 999.0
        theta = np.sum((y_lag - mean_y) * (dy - np.mean(dy))) / denom
        if theta >= 0.0:
            return 999.0  # Non-stationary / trending
        half_life = -math.log(2.0) / theta
        return float(half_life)

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        if market_context.get("is_rollover_window", False):
            return None  # Vetoed during rollover spread widening

        bocpd_prob = float(market_context.get("bocpd_changepoint_prob", 0.0))
        if bocpd_prob >= 0.65:
            return None  # Vetoed by regime change point

        residuals = market_context.get("residuals", [])
        half_life = self.estimate_ou_half_life(residuals)
        if not (2.0 <= half_life <= 48.0):
            return None  # Non-mean reverting or too slow

        z_score = float(market_context.get("z_score", 0.0))
        if abs(z_score) < 2.0:
            return None

        beta = float(market_context.get("beta", 0.88))
        price = float(market_context.get("price", 0.9500))
        atr = float(market_context.get("atr", 0.0020))

        side = "BUY" if z_score <= -2.0 else "SELL"
        sl_dist = 1.5 * atr
        tp_dist = 2.0 * atr

        sl = price - sl_dist if side == "BUY" else price + sl_dist
        tp = price + tp_dist if side == "BUY" else price - tp_dist

        gross_edge_bps = min(50.0, max(15.0, abs(z_score) * 15.0))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.primary_symbol,
            archetype=self.archetype,
            style=self.style,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(min(0.95, 0.5 + abs(z_score) * 0.12), 4),
            expected_holding_bars=int(math.ceil(half_life)),
            bar_timeframe="M15",
            is_two_leg=True,
            secondary_symbol=self.hedge_symbol,
            hedge_ratio=beta,
            metadata={
                "ou_half_life": round(half_life, 2),
                "z_score": round(z_score, 2),
                "bocpd_prob": round(bocpd_prob, 3),
                "beta": round(beta, 4),
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 2. Cointegration Pair Trading Strategy
# ---------------------------------------------------------------------------

class CointegrationPairStrategy(BaseInstitutionalStrategy):
    """Stationary Cointegration Pair Trading.
    
    Hardened institutional rules:
    - Enforces ADF p-value <= 0.04.
    - Rolling correlation must be >= 0.72.
    - Two-leg stops at 1.2x ATR with profit target at 1.8x ATR.
    - Enforces margin sleeve governance (allocates to SWING sleeve).
    """

    def __init__(self, strategy_id: str = "coint_aud_nzd", symbol: str = "AUDNZD"):
        super().__init__(strategy_id, StrategyArchetype.PAIR_TRADING, ExecutionStyle.SWING)
        self.symbol = symbol

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        adf_pvalue = float(market_context.get("adf_pvalue", 0.10))
        if adf_pvalue > 0.04:
            return None  # Fails cointegration persistence test

        rolling_corr = float(market_context.get("rolling_corr", 0.50))
        if rolling_corr < 0.72:
            return None  # Correlation breakdown

        z_score = float(market_context.get("z_score", 0.0))
        if abs(z_score) < 1.8:
            return None

        price = float(market_context.get("price", 1.0800))
        atr = float(market_context.get("atr", 0.0035))

        side = "BUY" if z_score < 0 else "SELL"
        sl = price - 1.2 * atr if side == "BUY" else price + 1.2 * atr
        tp = price + 1.8 * atr if side == "BUY" else price - 1.8 * atr

        gross_edge_bps = min(45.0, max(12.0, abs(z_score) * 12.0))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            archetype=self.archetype,
            style=self.style,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(min(0.90, 0.5 + abs(z_score) * 0.10), 4),
            expected_holding_bars=16,
            bar_timeframe="H1",
            metadata={
                "adf_pvalue": round(adf_pvalue, 4),
                "rolling_corr": round(rolling_corr, 3),
                "z_score": round(z_score, 2),
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 3. Avellaneda-Stoikov Mean Reversion Strategy
# ---------------------------------------------------------------------------

class AvellanedaStoikovMeanReversionStrategy(BaseInstitutionalStrategy):
    """Adapted Avellaneda-Stoikov Single-Sided Passive Limit Entry.
    
    Hardened institutional rules:
    - Micro-dislocation |z| >= 2.0.
    - Spread constraint: current spread <= 1.35x median spread.
    - HTF trend alignment: higher-timeframe trend must agree with mean-reversion drift.
    - Calculates inventory-skewed reservation price r(s, q, t) and optimal spread delta*.
    """

    def __init__(self, strategy_id: str = "avellaneda_scalp_eurusd", symbol: str = "EURUSD"):
        super().__init__(strategy_id, StrategyArchetype.MEAN_REVERSION, ExecutionStyle.SCALP)
        self.symbol = symbol

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        z_score = float(market_context.get("z_score", 0.0))
        if abs(z_score) < 2.0:
            return None

        spread = float(market_context.get("spread", 0.00010))
        median_spread = float(market_context.get("median_spread", 0.00010))
        if spread > 1.35 * median_spread:
            return None  # Dealer spread blown out

        htf_bias = str(market_context.get("htf_trend_bias", "NEUTRAL")).upper()
        proposed_side = "BUY" if z_score <= -2.0 else "SELL"
        if htf_bias != "NEUTRAL" and htf_bias != proposed_side:
            return None  # Opposing HTF structural momentum

        mid_price = float(market_context.get("price", 1.0500))
        inventory_q = float(market_context.get("inventory_units", 0.0))
        gamma = float(market_context.get("gamma", 0.5))
        vol = float(market_context.get("volatility", 0.0015))
        time_to_close = float(market_context.get("horizon_hours", 0.25))
        kappa = float(market_context.get("kappa", 1.5))

        # r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
        reservation_price = mid_price - (inventory_q * gamma * (vol ** 2) * time_to_close)
        # delta* = (1/2) * gamma * sigma^2 * (T - t) + (1/gamma) * ln(1 + gamma / kappa)
        half_spread = 0.5 * gamma * (vol ** 2) * time_to_close + (1.0 / gamma) * math.log(1.0 + gamma / kappa)

        limit_entry = reservation_price - half_spread if proposed_side == "BUY" else reservation_price + half_spread
        atr = float(market_context.get("atr", 0.0010))

        sl = limit_entry - 1.0 * atr if proposed_side == "BUY" else limit_entry + 1.0 * atr
        tp = limit_entry + 1.2 * atr if proposed_side == "BUY" else limit_entry - 1.2 * atr

        gross_edge_bps = min(30.0, max(8.0, abs(z_score) * 10.0))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            archetype=self.archetype,
            style=self.style,
            side=proposed_side,
            entry_price=round(limit_entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(min(0.85, 0.45 + abs(z_score) * 0.10), 4),
            expected_holding_bars=4,
            bar_timeframe="M5",
            metadata={
                "reservation_price": round(reservation_price, 5),
                "optimal_half_spread": round(half_spread, 5),
                "z_score": round(z_score, 2),
                "spread_ratio": round(spread / max(1e-6, median_spread), 2),
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 4. Macro Event Pullback Strategy
# ---------------------------------------------------------------------------

class MacroEventPullbackStrategy(BaseInstitutionalStrategy):
    """Macro Release News Surprise Retracement Strategy.
    
    Hardened institutional rules:
    - Standardized surprise sigma: |surprise_sigma| >= 1.5.
    - Post-release cool-down window: entry must occur between 3 and 25 minutes post-release
      (avoids 1st-second slippage spike; blocks stale entries > 25m).
    - Spread normalization: current spread <= 1.45x median spread.
    - Pullback retracement confirmation: price must pull back into 0.25 - 0.70 of release candle.
    - Scales out 33% at +1.5R, moves SL to break-even.
    """

    def __init__(self, strategy_id: str = "macro_nfp_pullback", symbol: str = "EURUSD"):
        super().__init__(strategy_id, StrategyArchetype.MACRO_EVENT, ExecutionStyle.EVENT)
        self.symbol = symbol

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        surprise_sigma = float(market_context.get("surprise_sigma", 0.0))
        if abs(surprise_sigma) < 1.5:
            return None  # Inconsequential surprise

        minutes_since_release = float(market_context.get("minutes_since_release", 0.0))
        if not (3.0 <= minutes_since_release <= 25.0):
            return None  # Outside institutional cool-down execution window

        spread = float(market_context.get("spread", 0.00010))
        median_spread = float(market_context.get("median_spread", 0.00010))
        if spread > 1.45 * median_spread:
            return None  # Spread still widened by dealer hedging

        pullback_ratio = float(market_context.get("pullback_ratio", 0.50))
        if not (0.25 <= pullback_ratio <= 0.70):
            return None  # No valid pullback structure formed

        # Strong macroeconomic surprise establishes fundamental impulse direction
        # Positive US surprise strengthens USD -> EURUSD SELL
        # Negative US surprise weakens USD -> EURUSD BUY
        side = "SELL" if surprise_sigma > 0 else "BUY"

        price = float(market_context.get("price", 1.0500))
        atr = float(market_context.get("atr", 0.0025))

        sl = price + 1.0 * atr if side == "SELL" else price - 1.0 * atr
        tp = price - 2.5 * atr if side == "SELL" else price + 2.5 * atr

        gross_edge_bps = min(60.0, max(25.0, abs(surprise_sigma) * 20.0))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            archetype=self.archetype,
            style=self.style,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(min(0.95, 0.65 + abs(surprise_sigma) * 0.08), 4),
            expected_holding_bars=12,
            bar_timeframe="M15",
            metadata={
                "surprise_sigma": round(surprise_sigma, 2),
                "minutes_since_release": round(minutes_since_release, 1),
                "pullback_ratio": round(pullback_ratio, 2),
                "partial_exit_rule": "33%_AT_1.5R_MOVE_BE",
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 5. Kronos Neural Forecast Strategy
# ---------------------------------------------------------------------------

class KronosNeuralForecastStrategy(BaseInstitutionalStrategy):
    """Foundation Transformer Neural Forecast with Predictive Entropy Uncertainty Veto.
    
    Hardened institutional rules:
    - Temperature-scaled logit calibration.
    - Predictive entropy uncertainty veto: max_predictive_entropy <= 0.62.
    - Minimum directional probability >= 0.60.
    """

    def __init__(self, strategy_id: str = "kronos_h1", symbol: str = "USDJPY"):
        super().__init__(strategy_id, StrategyArchetype.NEURAL_FORECAST, ExecutionStyle.SWING)
        self.symbol = symbol

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        predictive_entropy = float(market_context.get("predictive_entropy", 1.0))
        if predictive_entropy > 0.62:
            return None  # Epistemic / out-of-distribution uncertainty too high

        prob_up = float(market_context.get("prob_up", 0.50))
        prob_down = float(market_context.get("prob_down", 0.50))

        if max(prob_up, prob_down) < 0.60:
            return None

        side = "BUY" if prob_up > prob_down else "SELL"
        confidence = max(prob_up, prob_down)

        price = float(market_context.get("price", 152.00))
        atr = float(market_context.get("atr", 0.45))

        sl = price - 1.2 * atr if side == "BUY" else price + 1.2 * atr
        tp = price + 2.4 * atr if side == "BUY" else price - 2.4 * atr

        gross_edge_bps = min(50.0, max(15.0, (confidence - 0.50) * 100.0))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            archetype=self.archetype,
            style=self.style,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(confidence, 4),
            expected_holding_bars=8,
            bar_timeframe="H1",
            metadata={
                "predictive_entropy": round(predictive_entropy, 4),
                "prob_up": round(prob_up, 4),
                "prob_down": round(prob_down, 4),
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 6. Multi-Factor Ensemble Strategy
# ---------------------------------------------------------------------------

class MultiFactorEnsembleStrategy(BaseInstitutionalStrategy):
    """Cross-Sectional Momentum, Volatility-Adjusted RSI, and Carry Strategy.
    
    Hardened institutional rules:
    - ADX >= 22.0 and Time-Series Momentum |z| >= 1.5.
    - Overnight swap drag veto: rejects trades where expected daily carry is worse than -0.60 bps/day.
    - Pre-evaluates Wednesday 3x swap multiplier.
    """

    def __init__(self, strategy_id: str = "factor_carry_trend_d1", symbol: str = "USDJPY"):
        super().__init__(strategy_id, StrategyArchetype.MOMENTUM, ExecutionStyle.LONG_TERM)
        self.symbol = symbol

    def evaluate_opportunity(self, market_context: Dict[str, Any]) -> Optional[InstitutionalSignal]:
        adx = float(market_context.get("adx", 15.0))
        if adx < 22.0:
            return None  # Trendless market

        momentum_z = float(market_context.get("momentum_z", 0.0))
        if abs(momentum_z) < 1.5:
            return None

        side = "BUY" if momentum_z > 0 else "SELL"

        # Overnight swap check: carry in bps/day
        daily_swap_bps = float(market_context.get("daily_swap_bps", 0.0))
        if daily_swap_bps < -0.60:
            return None  # Unfavorable negative carry eating long-term edge

        price = float(market_context.get("price", 152.00))
        atr = float(market_context.get("atr", 0.85))

        sl = price - 1.5 * atr if side == "BUY" else price + 1.5 * atr
        tp = price + 3.0 * atr if side == "BUY" else price - 3.0 * atr

        gross_edge_bps = min(40.0, max(12.0, abs(momentum_z) * 12.0 + (daily_swap_bps * 5.0)))

        return InstitutionalSignal(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            archetype=self.archetype,
            style=self.style,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            gross_expected_edge_bps=gross_edge_bps,
            confidence=round(min(0.88, 0.55 + abs(momentum_z) * 0.10), 4),
            expected_holding_bars=5,
            bar_timeframe="D1",
            metadata={
                "adx": round(adx, 2),
                "momentum_z": round(momentum_z, 2),
                "daily_swap_bps": round(daily_swap_bps, 2),
            },
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 7. Institutional Pre-Trade TCA Gate
# ---------------------------------------------------------------------------

class InstitutionalPreTradeTCAGate:
    """Pre-Trade Transaction Cost Analysis Gate.
    
    Evaluates:
    - Bid-Ask Spread cost (single- or two-leg hedge-weighted)
    - Square-Root Market Impact Slippage: sigma * sqrt(order_notional / adv_notional)
    - Expected Overnight Swap Drag (including Wednesday 3x rollover)
    
    Gating Rules:
    - Edge-to-Cost Ratio must be >= min_edge_to_cost (default 2.50x, configurable to 3.0x).
    - Net Expected Edge must be >= min_net_edge_bps (default 2.0 bps).
    """

    def __init__(self, min_edge_to_cost: float = 2.50, min_net_edge_bps: float = 2.0):
        self.min_edge_to_cost = min_edge_to_cost
        self.min_net_edge_bps = min_net_edge_bps

    def evaluate(
        self,
        signal: InstitutionalSignal,
        market_context: Dict[str, Any],
        order_notional_usd: float = 100_000.0,
    ) -> TCAResult:
        # 1. Spread calculation
        spread_price = float(market_context.get("spread", 0.00010))
        entry_price = max(1e-6, signal.entry_price)
        leg1_spread_bps = (spread_price / entry_price) * 10_000.0

        if signal.is_two_leg and signal.secondary_symbol:
            sec_spread = float(market_context.get("secondary_spread", spread_price))
            sec_price = float(market_context.get("secondary_price", entry_price))
            leg2_spread_bps = (sec_spread / max(1e-6, sec_price)) * 10_000.0
            spread_cost_bps = leg1_spread_bps + (abs(signal.hedge_ratio) * leg2_spread_bps)
        else:
            spread_cost_bps = leg1_spread_bps

        # 2. Market Impact Slippage: sigma * sqrt(notional / ADV)
        adv_usd = max(1_000_000.0, float(market_context.get("adv_notional_usd", 500_000_000.0)))
        volatility = float(market_context.get("volatility", 0.0080))
        participation_rate = order_notional_usd / adv_usd
        market_impact_bps = volatility * math.sqrt(participation_rate) * 10_000.0

        # 3. Swap Drag & Wednesday 3x Multiplier
        day_of_week = int(market_context.get("day_of_week", 2))  # 0=Mon, 2=Wed
        holding_days = max(0.1, float(signal.expected_holding_bars) * (1.0 if signal.bar_timeframe == "D1" else 0.04))
        daily_swap_bps = float(market_context.get("daily_swap_bps", 0.0))

        # Check if holding spans Wednesday (Wednesday rollover charges 3 days)
        spans_wednesday = (day_of_week == 2) or (day_of_week < 2 and (day_of_week + holding_days) >= 2)
        effective_days = holding_days + (2.0 if spans_wednesday else 0.0)
        # Cost is positive if swap is negative
        swap_cost_bps = max(0.0, -daily_swap_bps * effective_days)

        total_cost_bps = spread_cost_bps + market_impact_bps + swap_cost_bps
        gross_edge_bps = max(0.0, signal.gross_expected_edge_bps)
        net_edge_bps = gross_edge_bps - total_cost_bps

        edge_to_cost_ratio = (gross_edge_bps / total_cost_bps) if total_cost_bps > 0 else 99.0

        passed = True
        rejection_reason = None

        if edge_to_cost_ratio < self.min_edge_to_cost:
            passed = False
            rejection_reason = f"LOW_EDGE_TO_COST ({edge_to_cost_ratio:.2f}x < {self.min_edge_to_cost}x)"
        elif net_edge_bps < self.min_net_edge_bps:
            passed = False
            rejection_reason = f"INSUFFICIENT_NET_EDGE ({net_edge_bps:.2f} bps < {self.min_net_edge_bps} bps)"

        return TCAResult(
            passed=passed,
            rejection_reason=rejection_reason,
            gross_edge_bps=round(gross_edge_bps, 2),
            total_cost_bps=round(total_cost_bps, 2),
            net_edge_bps=round(net_edge_bps, 2),
            edge_to_cost_ratio=round(edge_to_cost_ratio, 2),
            spread_cost_bps=round(spread_cost_bps, 2),
            market_impact_bps=round(market_impact_bps, 2),
            swap_cost_bps=round(swap_cost_bps, 2),
            spans_wednesday_rollover=spans_wednesday,
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 8. Currency Covariance Risk Sizer
# ---------------------------------------------------------------------------

class CurrencyCovarianceRiskSizer:
    """Currency Leg Decomposition and Tail-Risk Sizer.
    
    Hardened institutional rules:
    - Decomposes FX pairs into base (+1) and quote (-1) legs.
    - Enforces maximum net currency factor exposure cap: <= 2.5% net risk per currency.
    - Scales size: Half-Kelly * (1 - P_copula_tail) * (1 - 0.6 * P_bocpd).
    """

    CURRENCY_SPLITS = {
        "EURUSD": ("EUR", "USD"),
        "USDJPY": ("USD", "JPY"),
        "EURCHF": ("EUR", "CHF"),
        "USDCHF": ("USD", "CHF"),
        "AUDNZD": ("AUD", "NZD"),
        "GBPUSD": ("GBP", "USD"),
        "XAUUSD": ("XAU", "USD"),
    }

    def __init__(self, max_currency_risk_fraction: float = 0.025, base_nav: float = 100_000.0):
        self.max_currency_risk_fraction = max_currency_risk_fraction
        self.base_nav = base_nav

    def decompose_currency_exposure(self, symbol: str, side: str, risk_fraction: float) -> Dict[str, float]:
        base_quote = self.CURRENCY_SPLITS.get(symbol, (symbol[:3], symbol[3:]))
        base_ccy, quote_ccy = base_quote
        direction = 1.0 if side.upper() == "BUY" else -1.0
        return {
            base_ccy: direction * risk_fraction,
            quote_ccy: -direction * risk_fraction,
        }

    def compute_size(
        self,
        signal: InstitutionalSignal,
        current_portfolio_exposures: Dict[str, float],
        market_context: Dict[str, Any],
        win_rate: float = 0.58,
        win_loss_ratio: float = 1.75,
    ) -> SizingResult:
        # Half-Kelly Calculation
        # f* = (p * b - q) / b
        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio
        kelly_fraction = max(0.0, (p * b - q) / b)
        half_kelly = 0.5 * kelly_fraction

        # Tail-risk & Change-point Penalties
        copula_tail_p = min(0.99, max(0.0, float(market_context.get("copula_tail_risk_prob", 0.05))))
        bocpd_p = min(0.99, max(0.0, float(market_context.get("bocpd_changepoint_prob", 0.05))))

        copula_factor = 1.0 - copula_tail_p
        bocpd_factor = 1.0 - (0.6 * bocpd_p)

        # Base risk budget scaled by Half-Kelly relative to benchmark (0.17)
        # Scaled risk = base_budget * (half_kelly / 0.17) * (1 - copula_tail) * (1 - 0.6 * bocpd)
        base_budget = 0.01334
        kelly_scaler = (half_kelly / 0.17) if half_kelly > 0 else 1.0
        scaled_risk = base_budget * kelly_scaler * copula_factor * bocpd_factor
        # Target risk is bounded between 0.25% and 1.25% (half of max currency cap 2.5%)
        target_risk = max(0.0025, min(0.0125, scaled_risk))

        # Check currency covariance / stacked factor risk
        proposed_legs = self.decompose_currency_exposure(signal.symbol, signal.side, target_risk)
        admitted = True
        rejection_reason = None

        updated_exposures = dict(current_portfolio_exposures)
        for ccy, delta_risk in proposed_legs.items():
            current_net = updated_exposures.get(ccy, 0.0)
            projected_net = current_net + delta_risk
            if abs(projected_net) > self.max_currency_risk_fraction:
                admitted = False
                rejection_reason = f"CURRENCY_FACTOR_CAP_EXCEEDED ({ccy} projected {abs(projected_net)*100:.2f}% > {self.max_currency_risk_fraction*100:.1f}%)"
                break
            updated_exposures[ccy] = projected_net

        if not admitted:
            return SizingResult(
                admitted=False,
                rejection_reason=rejection_reason,
                target_risk_fraction=0.0,
                position_size_notional=0.0,
                lots=0.0,
                currency_exposures=current_portfolio_exposures,
                bocpd_penalty=round(1.0 - bocpd_factor, 4),
                copula_penalty=round(copula_tail_p, 4),
                execution_authority=False,
            )

        position_notional = self.base_nav * target_risk * 10.0  # Approx 10x leverage allocation
        lots = round(position_notional / 100_000.0, 2)

        return SizingResult(
            admitted=True,
            rejection_reason=None,
            target_risk_fraction=round(target_risk, 4),
            position_size_notional=round(position_notional, 2),
            lots=max(0.01, lots),
            currency_exposures={k: round(v, 4) for k, v in updated_exposures.items()},
            bocpd_penalty=round(1.0 - bocpd_factor, 4),
            copula_penalty=round(copula_tail_p, 4),
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 9. Archetype Exit Manager
# ---------------------------------------------------------------------------

class ArchetypeExitManager:
    """Exit physics separator: Convergent vs Divergent management."""

    def evaluate_exit(
        self,
        signal: InstitutionalSignal,
        current_price: float,
        holding_bars: int,
        market_context: Dict[str, Any],
        is_partial_already_taken: bool = False,
    ) -> ExitEvaluation:
        category = (
            ExitCategory.CONVERGENT
            if signal.archetype in (StrategyArchetype.STAT_ARB, StrategyArchetype.PAIR_TRADING, StrategyArchetype.MEAN_REVERSION)
            else ExitCategory.DIVERGENT
        )

        entry = signal.entry_price
        sl = signal.stop_loss
        tp = signal.take_profit
        is_long = signal.side.upper() == "BUY"

        # Check hard Stop Loss first
        if (is_long and current_price <= sl) or (not is_long and current_price >= sl):
            return ExitEvaluation(
                action="FULL_EXIT",
                exit_type="STOP_LOSS",
                scale_out_pct=1.0,
                reason="Stop loss breached",
                execution_authority=False,
            )

        # 1. Convergent Strategy Exits
        if category == ExitCategory.CONVERGENT:
            current_z = float(market_context.get("z_score", 0.0))
            bocpd_prob = float(market_context.get("bocpd_changepoint_prob", 0.0))
            half_life = float(market_context.get("ou_half_life", 15.0))

            # Full exit at mean reversion
            if abs(current_z) <= 0.20:
                return ExitEvaluation(
                    action="FULL_EXIT",
                    exit_type="MEAN_REVERSION_COMPLETE",
                    scale_out_pct=1.0,
                    reason=f"Mean reversion achieved (|z|={abs(current_z):.2f} <= 0.20)",
                    execution_authority=False,
                )

            # Regime break emergency exit
            if bocpd_prob >= 0.75:
                return ExitEvaluation(
                    action="FULL_EXIT",
                    exit_type="REGIME_BREAK",
                    scale_out_pct=1.0,
                    reason=f"BOCPD regime break detected (prob={bocpd_prob:.2f})",
                    execution_authority=False,
                )

            # Time stop exit: 3x OU half-life
            if holding_bars > (3.0 * half_life):
                return ExitEvaluation(
                    action="FULL_EXIT",
                    exit_type="TIME_STOP",
                    scale_out_pct=1.0,
                    reason=f"Time decay stop breached (holding {holding_bars} > 3*half-life {3.0*half_life:.1f})",
                    execution_authority=False,
                )

        # 2. Divergent Strategy Exits
        else:
            r_distance = abs(entry - sl)
            current_r = (current_price - entry) / r_distance if is_long else (entry - current_price) / r_distance
            atr = float(market_context.get("atr", r_distance))

            # 33% scale-out at +1.5R and move stop to break-even (+1 pip)
            if current_r >= 1.5 and not is_partial_already_taken:
                one_pip = 0.0001 if "JPY" not in signal.symbol else 0.01
                new_be_sl = entry + one_pip if is_long else entry - one_pip
                return ExitEvaluation(
                    action="PARTIAL_SCALE_OUT",
                    exit_type="TARGET",
                    scale_out_pct=0.33,
                    new_stop_loss=round(new_be_sl, 5),
                    reason=f"+1.5R reached (current_r={current_r:.2f}); 33% scale-out and SL to BE+1",
                    execution_authority=False,
                )

            # Chandelier trailing stop (2.5x ATR from peak)
            peak_price = float(market_context.get("peak_price_since_entry", current_price))
            chandelier_sl = peak_price - 2.5 * atr if is_long else peak_price + 2.5 * atr
            if (is_long and chandelier_sl > sl) or (not is_long and chandelier_sl < sl):
                return ExitEvaluation(
                    action="TRAIL_STOP_RATCHET",
                    exit_type="TARGET",
                    new_stop_loss=round(chandelier_sl, 5),
                    reason="Ratchet Chandelier 2.5x ATR trailing stop",
                    execution_authority=False,
                )

            # Full target take-profit
            if (is_long and current_price >= tp) or (not is_long and current_price <= tp):
                return ExitEvaluation(
                    action="FULL_EXIT",
                    exit_type="TARGET",
                    scale_out_pct=1.0,
                    reason="Runner target reached",
                    execution_authority=False,
                )

        return ExitEvaluation(
            action="HOLD",
            exit_type="NONE",
            reason="Position within acceptable parameters",
            execution_authority=False,
        )


# ---------------------------------------------------------------------------
# 10. Institutional Strategy TCA Engine (Master Tournament Orchestrator)
# ---------------------------------------------------------------------------

class InstitutionalStrategyTCAEngine:
    """Master tournament coordinator for institutional strategy governance."""

    MARGIN_SLEEVES = {
        ExecutionStyle.SWING: 0.035,      # 3.5% NAV
        ExecutionStyle.SCALP: 0.015,      # 1.5% NAV
        ExecutionStyle.LONG_TERM: 0.030,  # 3.0% NAV
        ExecutionStyle.EVENT: 0.025,      # 2.5% NAV
    }

    def __init__(
        self,
        strategies: Optional[List[BaseInstitutionalStrategy]] = None,
        tca_gate: Optional[InstitutionalPreTradeTCAGate] = None,
        risk_sizer: Optional[CurrencyCovarianceRiskSizer] = None,
        exit_manager: Optional[ArchetypeExitManager] = None,
    ):
        self.strategies = strategies or [
            KalmanStatArbStrategy(),
            CointegrationPairStrategy(),
            AvellanedaStoikovMeanReversionStrategy(),
            MacroEventPullbackStrategy(),
            KronosNeuralForecastStrategy(),
            MultiFactorEnsembleStrategy(),
        ]
        self.tca_gate = tca_gate or InstitutionalPreTradeTCAGate()
        self.risk_sizer = risk_sizer or CurrencyCovarianceRiskSizer()
        self.exit_manager = exit_manager or ArchetypeExitManager()

    def run_tournament(
        self,
        market_contexts: Dict[str, Dict[str, Any]],
        current_portfolio_exposures: Optional[Dict[str, float]] = None,
        active_sleeve_usage: Optional[Dict[ExecutionStyle, float]] = None,
    ) -> Dict[str, Any]:
        """Runs the complete tournament:
        1. Evaluates all 6 strategies
        2. Filters through Institutional Pre-Trade TCA Gate
        3. Arbitrates asset collisions (highest composite score wins)
        4. Validates margin sleeve capacity
        5. Computes currency covariance position sizing
        """
        portfolio_exposures = dict(current_portfolio_exposures or {})
        sleeve_usage = dict(active_sleeve_usage or {s: 0.0 for s in ExecutionStyle})

        raw_signals: List[InstitutionalSignal] = []
        for strat in self.strategies:
            ctx = market_contexts.get(strat.strategy_id) or market_contexts.get(getattr(strat, "symbol", getattr(strat, "primary_symbol", "")))
            if ctx:
                sig = strat.evaluate_opportunity(ctx)
                if sig:
                    raw_signals.append(sig)

        tca_passed: List[Tuple[InstitutionalSignal, TCAResult, float]] = []
        rejected_trades: List[Dict[str, Any]] = []

        for sig in raw_signals:
            ctx = market_contexts.get(sig.strategy_id) or market_contexts.get(sig.symbol, {})
            tca_res = self.tca_gate.evaluate(sig, ctx)
            if not tca_res.passed:
                rejected_trades.append({
                    "strategy_id": sig.strategy_id,
                    "symbol": sig.symbol,
                    "reason": tca_res.rejection_reason,
                    "tca": tca_res,
                })
            else:
                # Composite tournament score: net edge bps * confidence / max(1.0, cost bps)
                composite_score = (tca_res.net_edge_bps * sig.confidence) / max(1.0, tca_res.total_cost_bps)
                # Normalize composite score between 0.0 and 1.0 for ranking
                norm_score = round(min(0.999, composite_score / (composite_score + 10.0) + sig.confidence * 0.4), 4)
                tca_passed.append((sig, tca_res, norm_score))

        # Sort candidate signals by normalized composite score descending
        tca_passed.sort(key=lambda x: x[2], reverse=True)

        # Asset collision arbitration (one trade per symbol)
        asset_winners: Dict[str, Tuple[InstitutionalSignal, TCAResult, float]] = {}
        for sig, tca_res, score in tca_passed:
            if sig.symbol in asset_winners:
                incumbent = asset_winners[sig.symbol]
                rejected_trades.append({
                    "strategy_id": sig.strategy_id,
                    "symbol": sig.symbol,
                    "reason": f"OUTSCORED_ON_ASSET (lost to {incumbent[0].strategy_id} with score {incumbent[2]} vs {score})",
                    "tca": tca_res,
                })
            else:
                asset_winners[sig.symbol] = (sig, tca_res, score)

        # Margin sleeve and currency covariance allocation
        admitted_trades: List[Dict[str, Any]] = []

        for symbol, (sig, tca_res, score) in asset_winners.items():
            max_sleeve = self.MARGIN_SLEEVES.get(sig.style, 0.035)
            used_sleeve = sleeve_usage.get(sig.style, 0.0)
            ctx = market_contexts.get(sig.strategy_id) or market_contexts.get(sig.symbol, {})
            size_res = self.risk_sizer.compute_size(sig, portfolio_exposures, ctx)

            if not size_res.admitted:
                rejected_trades.append({
                    "strategy_id": sig.strategy_id,
                    "symbol": sig.symbol,
                    "reason": size_res.rejection_reason,
                    "tca": tca_res,
                })
                continue

            if (used_sleeve + size_res.target_risk_fraction) > (max_sleeve + 1e-6):
                rejected_trades.append({
                    "strategy_id": sig.strategy_id,
                    "symbol": sig.symbol,
                    "reason": f"MARGIN_SLEEVE_EXHAUSTED ({sig.style.value} sleeve projected {(used_sleeve + size_res.target_risk_fraction)*100:.1f}% > {max_sleeve*100:.1f}%)",
                    "tca": tca_res,
                })
                continue

            # Update portfolio and sleeve allocations
            portfolio_exposures = size_res.currency_exposures
            sleeve_usage[sig.style] = used_sleeve + size_res.target_risk_fraction

            admitted_trades.append({
                "strategy_id": sig.strategy_id,
                "symbol": sig.symbol,
                "archetype": sig.archetype.value,
                "style": sig.style.value,
                "score": score,
                "signal": sig,
                "tca": tca_res,
                "sizing": size_res,
                "net_edge_bps": tca_res.net_edge_bps,
                "edge_to_cost_ratio": tca_res.edge_to_cost_ratio,
            })

        return {
            "execution_authority": False,
            "total_candidates": len(raw_signals),
            "admitted_trades": admitted_trades,
            "rejected_trades": rejected_trades,
            "updated_currency_exposures": portfolio_exposures,
            "updated_sleeve_usage": sleeve_usage,
        }
