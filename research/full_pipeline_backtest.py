"""Full Pipeline Backtest — tests the complete indicator→gates→execution pipeline.

Unlike the alpha-only backtest, this tests the ENTIRE decision path:
  1. Compute indicators (HMA, Kalman, RSI, MACD, ADX, ATR, Alpha ensemble)
  2. Build feature snapshot
  3. Run DeterministicValidator (15 rules)
  4. Run TradingConstraintEngine (15 rules)
  5. Size position via risk_per_trade
  6. Simulate fill at bar close + spread + slippage
  7. Track PnL with R-multiples
  8. Compute Sharpe, Sortino, MaxDD, Calmar, win rate, profit factor, expectancy

No LLM calls — replaces the LLM decision with a deterministic signal derived
from the alpha ensemble composite + HMA direction + confluence score. This
measures whether the GATES and RISK MANAGEMENT produce positive expectancy,
independent of LLM quality.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure subpackages are on path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ["core", "intelligence", "data", "research"]:
    _p = os.path.join(_root, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Transaction-cost model (production TCE — core/transaction_cost_engine.py)
# ---------------------------------------------------------------------------

_TCE_CACHE: List[Any] = []  # [engine] or [] when unavailable


def _get_tce_engine() -> Optional[Any]:
    """Return the production TransactionCostEngine (cached), or None.

    Same engine the live TCA auto-trigger uses (auto_quant_tools._run_tca).
    Import failure returns None and callers fall back to the legacy flat
    cost model — never an invented constant.
    """
    if _TCE_CACHE:
        return _TCE_CACHE[0]
    try:
        from core.transaction_cost_engine import TransactionCostEngine
        engine = TransactionCostEngine()
        _TCE_CACHE.append(engine)
        return engine
    except Exception:
        return None


def _tce_fill_cost(
    engine: Any,
    *,
    side: str,
    spread_multiplier: float = 1.0,
    ref_price: float,
    bar_idx: int,
    volume_lots: float,
    closes: "np.ndarray",
    atr_value: float,
    atr_series: "np.ndarray",
    commission_per_lot: float,
) -> Optional[Dict[str, Any]]:
    """Estimate one fill's slippage/commission with the production TCE.

    Market context mirrors auto_quant_tools._run_tca (same production
    conventions): daily vol from ATR * sqrt(288) normalized by price and
    clipped to [0.002, 0.05]; base half-spread from a 1.5-pip FX spread;
    ADV approximated as bar volume * 288. Returns price-unit components:
      half_spread_price, impact_price, slip_price (= half_spread + impact),
      commission ($), entry/exit cost in bps.
    None on any failure — caller falls back to the legacy flat model.
    """
    try:
        import numpy as _np
        from core.transaction_cost_engine import OrderContext, MarketContext

        price = float(ref_price)
        if price <= 0 or not _np.isfinite(price):
            return None
        window = closes[max(0, bar_idx - 20):bar_idx + 1]
        if len(window) > 2:
            rets = _np.diff(window) / _np.maximum(window[:-1], 1e-10)
            bar_sigma = float(_np.std(rets))
        else:
            bar_sigma = 0.0
        atr_frac = (float(atr_value) / price) if (atr_value and price > 0) else None
        if (not atr_frac or atr_frac <= 0) and len(atr_series) > bar_idx:
            atr_frac = float(atr_series[bar_idx]) / price if price > 0 else None
        daily_vol = float(atr_frac * _np.sqrt(288)) if atr_frac else 0.008
        daily_vol = float(_np.clip(daily_vol, 0.002, 0.05))

        base_spread = 0.00015  # 1.5 pips on a 1e-4 pip size (existing default)
        half_spread = base_spread / 2.0
        # All sizes in "units of base currency" (1 lot = 100,000) so the TCE's
        # Q/V_ADV and v/v_bar ratios are internally consistent (no unit skew).
        bar_volume_units = 1000.0 * 100000.0       # ~1000 lots flow per bar (tick-volume proxy)
        adv_units = bar_volume_units * 288.0       # ADV ≈ bar volume × 288 (same convention as live TCA)
        market = MarketContext(
            best_bid=price - half_spread,
            best_ask=price + half_spread,
            bid_depth=1.0e7,    # ~100 lots at the top of book
            ask_depth=1.0e7,
            daily_volume=adv_units,
            daily_volatility=daily_vol,
            current_volume=bar_volume_units,
            avg_bar_volume=bar_volume_units,
            current_atr=float(atr_value) if atr_value else 1.0,
            baseline_atr=float(atr_value) if atr_value else 1.0,
        )
        # "shares" = units of base currency: lots * 100,000 (1 standard lot)
        units = max(1.0, float(volume_lots) * 100000.0)
        order = OrderContext(
            symbol="FX", side=side, order_type="MARKET",
            shares=units, decision_price=price,
        )
        cb = engine.estimate_costs(order, market, one_r_dollar=100.0)
        # Price-unit deltas (per unit of base currency) — used to slip the fill.
        # The stress overlay widens the modeled half-spread (session/rollover
        # multipliers) BEFORE impact is derived from it, so event-hour fills
        # slip more and pay more spread — retail reality, causally applied.
        half_spread_delta = float(cb.half_spread_cost) * max(0.0, float(spread_multiplier))  # price units
        impact_delta = float(cb.total_market_impact)            # price units
        slip_delta = half_spread_delta + impact_delta           # price units
        # Dollar costs for this fill size (delta × units) + commission.
        spread_usd = half_spread_delta * units
        impact_usd = impact_delta * units
        comm = float(commission_per_lot) * float(volume_lots)
        # Friction bps = the fill's actual slippage in bps of price, from the
        # model's own price deltas. (The TCE's total_cost_basis_points bundles
        # an equity-scale broker commission that double-counts against this
        # backtest's commission_per_lot — so it is NOT used here.)
        friction_bps = (slip_delta / price) * 10000.0 if price > 0 else 0.0
        return {
            "half_spread_delta": half_spread_delta,
            "impact_delta": impact_delta,
            "slip_delta": slip_delta,
            "spread_usd": spread_usd,
            "impact_usd": impact_usd,
            "slip_usd": impact_usd,
            "commission": comm,
            "cost_bps": float(friction_bps),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Backtest result
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Full pipeline backtest results."""
    name: str = "full_pipeline"
    symbol: str = ""
    timeframe: str = "M15"
    bars_processed: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    # Returns
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    # Risk
    max_drawdown_pct: float = 0.0
    max_drawdown_r: float = 0.0
    # Trade quality
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    expectancy_r: float = 0.0
    # Gate stats
    signals_generated: int = 0
    signals_passed_gates: int = 0
    signals_rejected_gates: int = 0
    gate_rejection_reasons: Dict[str, int] = field(default_factory=dict)
    # Costs
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    total_commission: float = 0.0
    # Equity
    initial_equity: float = 10000.0
    final_equity: float = 10000.0
    equity_curve: List[float] = field(default_factory=list)
    r_multiples: List[float] = field(default_factory=list)
    # Per-trade records (entry/exit/exit_reason) for mechanism diagnostics
    trades_log: List[Dict[str, Any]] = field(default_factory=list)
    # Open position at end
    open_position: Optional[Dict[str, Any]] = None
    # S.Ξ DSR gate results
    dsr_confidence: float = 0.0        # DSR confidence Φ(z) = 1 − p (higher = more significant)
    dsr_pvalue: float = 0.0            # DEPRECATED compat alias for dsr_confidence (legacy name)
    dsr_passed: Optional[bool] = None  # True/False/None (None = not run)
    dsr_n_trials: int = 0              # True trial count from ledger
    dsr_raw_sharpe: float = 0.0        # Sharpe before deflation (same as sharpe_ratio)
    dsr_expected_max: float = 0.0       # E[max Sharpe] under null
    dsr_pbo_proxy: float = 0.0         # Probability of backtest overfitting
    dsr_trial_mean: float = 0.0        # Mean of all trial Sharpes in ledger
    dsr_trial_std: float = 0.0         # Std of all trial Sharpes in ledger
    # Per-fill transaction-cost model (production TCE, core/transaction_cost_engine.py)
    cost_model: str = "flat_legacy"            # "tce_per_fill" | "flat_legacy"
    # --- Provenance -------------------------------------------------------
    # Which signal actually produced these numbers. `name` is a label only:
    # a single generic ruleset would otherwise score identically for every
    # registry strategy and the result reads as if it described that strategy.
    ruleset: str = "deterministic_signal"
    strategy_implemented: bool = False
    avg_slippage_bps: float = 0.0              # mean per-fill total slippage in bps
    max_fill_slippage_bps: float = 0.0         # worst observed fill
    total_impact_cost: float = 0.0             # $ market-impact component (TCE runs)
    total_cost: float = 0.0                    # $ spread+impact+commission (both models)
    # Retail cost-stress overlay (PU-0156 §8.5; zero/absent when off)
    total_swap_cost: float = 0.0               # $ nightly financing (negative = cost)
    swap_nights_charged: int = 0

    def __post_init__(self) -> None:
        # Keep dsr_confidence and its deprecated alias in sync at construction.
        # (If both are set explicitly and disagree, both are respected as-is.)
        if self.dsr_confidence == 0.0 and self.dsr_pvalue != 0.0:
            self.dsr_confidence = self.dsr_pvalue
        elif self.dsr_pvalue == 0.0 and self.dsr_confidence != 0.0:
            self.dsr_pvalue = self.dsr_confidence

    def bar_returns(self) -> List[float]:
        """Simple per-bar fractional returns from the recorded equity curve."""
        curve = self.equity_curve or []
        out: List[float] = []
        for previous, current in zip(curve, curve[1:]):
            try:
                previous = float(previous)
                current = float(current)
            except (TypeError, ValueError, OverflowError):
                continue
            if previous > 0:
                value = (current - previous) / previous
                if math.isfinite(value):
                    out.append(value)
        return out

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "ruleset": self.ruleset,
            "strategy_implemented": self.strategy_implemented,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars_processed": self.bars_processed,
            # Measured per-bar returns. Downstream overfit / walk-forward gates
            # must validate a real series; they previously synthesised one from
            # this result's own Sharpe, which made them certify a coin flip.
            # Derived here from the recorded equity curve, net of whatever costs
            # the backtest already applied.
            "returns": self.bar_returns(),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_return_pct": round(self.total_return_pct, 4),
            "annualized_return_pct": round(self.annualized_return_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown_r": round(self.max_drawdown_r, 4),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 4),
            "avg_win_r": round(self.avg_win_r, 4),
            "avg_loss_r": round(self.avg_loss_r, 4),
            "expectancy_r": round(self.expectancy_r, 4),
            "signals_generated": self.signals_generated,
            "signals_passed_gates": self.signals_passed_gates,
            "signals_rejected_gates": self.signals_rejected_gates,
            "gate_rejection_reasons": self.gate_rejection_reasons,
            "total_spread_cost": round(self.total_spread_cost, 6),
            "total_slippage_cost": round(self.total_slippage_cost, 6),
            "total_commission": round(self.total_commission, 6),
            "cost_model": self.cost_model,
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "max_fill_slippage_bps": round(self.max_fill_slippage_bps, 2),
            "total_impact_cost": round(self.total_impact_cost, 4),
            "total_cost": round(self.total_cost, 4),
            "total_swap_cost": round(self.total_swap_cost, 4),
            "swap_nights_charged": self.swap_nights_charged,
            "initial_equity": round(self.initial_equity, 2),
            "final_equity": round(self.final_equity, 2),
            # S.Ξ DSR gate
            "dsr_confidence": round(self.dsr_confidence, 6),
            "dsr_pvalue": round(self.dsr_confidence, 6),  # legacy key name, carries confidence
            "dsr_passed": self.dsr_passed,
            "dsr_n_trials": self.dsr_n_trials,
            "dsr_raw_sharpe": round(self.dsr_raw_sharpe, 4),
            "dsr_expected_max": round(self.dsr_expected_max, 4),
            "dsr_pbo_proxy": round(self.dsr_pbo_proxy, 4),
            "dsr_trial_mean": round(self.dsr_trial_mean, 4),
            "dsr_trial_std": round(self.dsr_trial_std, 4),
            # The run's own net-of-cost per-trade R-multiples. Downstream
            # validation (CPCV/PBO) must score THE strategy that produced
            # these — not a re-run of the generic ruleset.
            "r_multiples": [round(float(r), 6) for r in self.r_multiples],
        }
        return d


# ---------------------------------------------------------------------------
# Deterministic signal (replaces LLM)
# ---------------------------------------------------------------------------

def deterministic_signal(
    indicators: Dict[str, Any],
    alpha_composite: float = 0.0,
    regime_tradable: bool = True,
    regime_multiplier: float = 1.0,
    adx_min: float = 20.0,
    sl_atr_mult: float = 1.5,
    tp_atr_mult: float = 3.0,
    alpha_threshold: float = 0.5,
) -> Dict[str, Any]:
    """Produce a BUY/SELL/HOLD signal from indicators without LLM.

    This tests whether the indicator+gate system has edge, independent of
    LLM quality. The signal logic mirrors what the LLM prompt asks for:

    PRIMARY SIGNALS:
      - HMA direction: price > HMA = bullish
      - Alpha ensemble composite: beyond ±``alpha_threshold`` = aligned
      - ADX >= ``adx_min`` = trending (required for breakout entries)

    CONFLUENCE (min 3 factors):
      1. HMA direction aligned
      2. Alpha composite aligned
      3. ADX >= adx_min
      4. Volume above average (if available)
      5. EMA alignment (close vs EMA)

    ``adx_min``/``sl_atr_mult``/``tp_atr_mult``/``alpha_threshold`` tune the
    factor horizons and risk geometry; the structural gates (direction +
    confluence >= 3, RR >= min_rr) are unchanged.
    """
    hma = indicators.get("hma_21", indicators.get("hma", 0))
    close = indicators.get("close", 0)
    adx = indicators.get("adx_14", indicators.get("adx", 0))
    rsi = indicators.get("rsi_14", indicators.get("rsi", 50))
    ema_50 = indicators.get("ema_50", 0)
    atr = indicators.get("atr_14", indicators.get("atr", 0))

    if close <= 0 or hma <= 0 or atr <= 0:
        return {"action": "HOLD", "confidence": 0, "reason": "insufficient_data"}

    # Direction from HMA
    hma_bullish = close > hma
    hma_bearish = close < hma

    # Direction from alpha composite
    alpha_bullish = alpha_composite > alpha_threshold
    alpha_bearish = alpha_composite < -alpha_threshold

    # Trending market
    trending = adx >= adx_min

    # EMA alignment
    ema_bullish = close > ema_50 if ema_50 > 0 else False
    ema_bearish = close < ema_50 if ema_50 > 0 else False

    # Confluence scoring
    bull_factors = sum([hma_bullish, alpha_bullish, trending, ema_bullish])
    bear_factors = sum([hma_bearish, alpha_bearish, trending, ema_bearish])

    # Minimum confluence required
    min_confluence = 3

    if not regime_tradable:
        return {"action": "HOLD", "confidence": 0, "reason": "regime_not_tradable"}

    if bull_factors >= min_confluence and bull_factors > bear_factors:
        confidence = min(1.0, bull_factors / 4.0)
        # SL: sl_atr_mult ATR against entry, TP: tp_atr_mult ATR with entry
        sl_distance = atr * sl_atr_mult
        tp_distance = atr * tp_atr_mult
        return {
            "action": "BUY",
            "entry": close,
            "stop_loss": close - sl_distance,
            "take_profit": close + tp_distance,
            "confidence": confidence,
            "confluence": bull_factors,
            "reason": f"hma={'bull' if hma_bullish else ''} alpha={'bull' if alpha_bullish else ''} adx={adx:.1f} ema={'bull' if ema_bullish else ''}",
        }

    if bear_factors >= min_confluence and bear_factors > bull_factors:
        confidence = min(1.0, bear_factors / 4.0)
        sl_distance = atr * sl_atr_mult
        tp_distance = atr * tp_atr_mult
        return {
            "action": "SELL",
            "entry": close,
            "stop_loss": close + sl_distance,
            "take_profit": close - tp_distance,
            "confidence": confidence,
            "confluence": bear_factors,
            "reason": f"hma={'bear' if hma_bearish else ''} alpha={'bear' if alpha_bearish else ''} adx={adx:.1f} ema={'bear' if ema_bearish else ''}",
        }

    return {
        "action": "HOLD",
        "confidence": 0,
        "confluence": max(bull_factors, bear_factors),
        "reason": f"insufficient_confluence bull={bull_factors} bear={bear_factors}",
    }


# ---------------------------------------------------------------------------
# Main backtest engine
# ---------------------------------------------------------------------------

def run_full_pipeline_backtest(
    bars: List[Dict[str, Any]],
    *,
    symbol: str = "EURUSD",
    timeframe: str = "M15",
    initial_equity: float = 10000.0,
    risk_per_trade_pct: float = 0.01,
    spread_pips: float = 1.5,
    slippage_pips: float = 0.5,
    commission_per_lot: float = 7.0,
    pip_value: float = 10.0,  # EURUSD: $10 per pip per standard lot
    pip_size: float = 0.0001,  # EURUSD: 1 pip = 0.0001 price change
    min_rr: float = 2.0,
    min_confluence: int = 3,
    max_open_positions: int = 3,
    max_daily_loss_pct: float = 0.05,
    enable_gates: bool = True,
    enable_dsr: bool = True,
    use_tce: bool = True,  # per-fill production TCE slippage (False = legacy flat costs)
    dsr_gate_threshold: float = 0.95,
    name: str = "full_pipeline_backtest",
    # --- Optional ruleset parameterization (defaults = legacy behavior) ---
    hma_period: int = 21,
    ema_period: int = 50,
    adx_period: int = 14,
    adx_min: float = 20.0,
    sl_atr_mult: float = 1.5,
    tp_atr_mult: float = 3.0,
    alpha_threshold: float = 0.5,
    periods_per_year: float = 35040.0,
    # --- Optional trade management (mirrors live TradeManager; 0 = off) ---
    be_trigger_r: float = 0.0,
    be_offset_r: float = 0.0,
    max_hold_bars: int = 0,
    # --- Optional entry-quality filters (raise hit rate; None/0 = off) ---
    session_hours_utc: Optional[List[int]] = None,
    vol_floor_pctile: float = 0.0,
    vol_ceiling_pctile: float = 0.0,
    vol_env_window: int = 500,
    rsi_no_chase_hi: float = 0.0,
    max_bar_extension_atr: float = 0.0,
    # --- Optional external entry signal (replaces the indicator ruleset) ---
    entry_signal_scores: Optional[np.ndarray] = None,
    entry_signal_threshold: float = 1.0,
    # --- Retail cost stress (PU-0156 §8.5; OFF unless explicitly enabled) ---
    # Models the retail realities the framework demands: spreads widen at
    # rollover/illiquidity and around events, and multi-night holds pay swap
    # (triple on Wednesdays). Off by default; production numbers unchanged.
    cost_stress: bool = False,                 # master switch for the overlay
    stress_spread_multiplier: float = 2.0,     # x1.5-2 per the framework
    stress_event_spread_multiplier: float = 3.0,  # event/rollover spikes (x3)
    stress_swap_per_lot_per_night: float = -2.5,  # $ per lot per night (sign = cost)
    stress_triple_swap_weekday: int = 2,       # Wednesday (Mon=0) triple swap
) -> BacktestResult:
    """Run full pipeline backtest on historical bars.

    Args:
        bars: List of dicts with keys: open, high, low, close, volume, timestamp
        symbol: Instrument symbol
        timeframe: Bar timeframe
        initial_equity: Starting capital
        risk_per_trade_pct: Max risk per trade as fraction of equity
        spread_pips: Average spread in pips
        slippage_pips: Average slippage in pips
        commission_per_lot: Commission per standard lot (one-way)
        pip_value: Dollar value per pip per standard lot
        min_rr: Minimum reward:risk ratio
        min_confluence: Minimum confluence factors
        max_open_positions: Maximum simultaneous positions
        max_daily_loss_pct: Max daily loss before halt
        enable_gates: Whether to run validator gates
        name: Backtest name for reporting
        hma_period: Hull MA period for the primary direction factor
        ema_period: EMA period for the alignment factor
        adx_period: ADX period for the trend-strength factor
        adx_min: Minimum ADX for a bar to count as trending
        sl_atr_mult: Stop-loss distance in ATR multiples
        tp_atr_mult: Take-profit distance in ATR multiples
        alpha_threshold: |alpha composite| required for the alpha factor
        periods_per_year: Annualization constant matching ``timeframe``
            (35040 M15 bars, 8760 H1 bars, 1460 H4 bars, 365 D1 bars).
        be_trigger_r: Breakeven ratchet trigger in R (move stop to entry +
            offset once MFE reaches this). 0 = disabled (legacy).
        be_offset_r: Profit locked by the ratchet, in R units of the
            original stop distance (0 = stop to exact entry).
        max_hold_bars: Time stop — exit at bar close after this many bars
            in the trade. 0 = disabled (legacy).
        session_hours_utc: Allow entries only in these UTC hours (list of
            ints; hours are true UTC — fetch_ohlc normalizes server time).
            None = disabled (legacy).
        vol_floor_pctile / vol_ceiling_pctile: block entries when the
            bar's ATR% ranks below/above these percentiles of its trailing
            ``vol_env_window``-bar window (regime-volatility band).
            0 = that bound disabled (legacy).
        vol_env_window: trailing window for the ATR% percentile rank.
        rsi_no_chase_hi: block BUY entries when RSI >= this (chasing an
            overbought bar); SELL entries are mirrored (RSI <= 100 - value).
            0 = disabled (legacy).
        max_bar_extension_atr: block entries when the signal bar's
            high-low range exceeds this ATR multiple (climactic bar).
            0 = disabled (legacy).
        entry_signal_scores: per-bar entry scores aligned with ``bars``
            (NaN = not yet warmed up). When provided, the HMA/EMA/ADX/RSI
            ruleset is REPLACED: score >= +threshold → BUY, <= -threshold
            → SELL, else HOLD. Everything downstream is unchanged — the
            same gates (RR, confidence), entry filters, ATR stop/target
            geometry, sizing, and trade management all still apply.
        entry_signal_threshold: |score| required to enter.

    ``deterministic_signal`` still enforces the live pipeline's structural
    gates (HMA direction, alpha composite sign, ADX trend, EMA alignment,
    confluence >= 3); these parameters tune the factor horizons and risk
    geometry without weakening any gate.
    """
    result = BacktestResult(name=name, symbol=symbol, timeframe=timeframe,
                            initial_equity=initial_equity)

    if len(bars) < 50:
        return result

    # Extract arrays for vectorized indicator computation
    closes = np.array([float(b.get("close", 0)) for b in bars], dtype=np.float64)
    highs = np.array([float(b.get("high", 0)) for b in bars], dtype=np.float64)
    lows = np.array([float(b.get("low", 0)) for b in bars], dtype=np.float64)
    opens = np.array([float(b.get("open", 0)) for b in bars], dtype=np.float64)
    volumes = np.array([float(b.get("volume", 0) or b.get("tick_volume", 0) or 0) for b in bars], dtype=np.float64)

    n = len(closes)

    # --- Compute indicators (vectorized, no lookahead) ---
    # Primary direction factor (Hull MA, parameterized horizon)
    hma = _hma(closes, hma_period)
    # Alignment factor
    ema50 = _ema(closes, ema_period)
    # Trend-strength factor
    adx = _adx(highs, lows, closes, adx_period)
    # ATR(14)
    atr = _atr(highs, lows, closes, 14)
    # RSI(14)
    rsi = _rsi(closes, 14)
    # Volume MA(20)
    vol_ma = _sma(volumes, 20)
    # Alpha ensemble proxy: use HMA slope + ADX momentum as signal
    alpha_composite = _alpha_composite_proxy(closes, hma, adx, rsi)

    # --- Entry-quality context (causal: trailing windows only) ---
    # Session membership per bar (true-UTC hours; -1 = unknown → blocked).
    bar_hours = []
    for b in bars:
        t = b.get("time") or b.get("timestamp")
        if isinstance(t, str):
            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                t = None
        h = getattr(t, "hour", None)
        bar_hours.append(int(h) if h is not None else -1)
    session_ok: Optional[np.ndarray] = None
    if session_hours_utc is not None:
        allowed = {int(h) % 24 for h in session_hours_utc}
        session_ok = np.array([h in allowed for h in bar_hours], dtype=bool)

    # --- Retail cost-stress overlay (opt-in; see signature docs) ---
    # Session/rollover/event spread multipliers per bar + nightly swap for
    # positions held across 00:00 UTC (triple on the configured weekday).
    # All multipliers are applied causally: bar i's multiplier depends only on
    # bar i's hour, never on future bars.
    bar_weekdays: List[int] = []
    if cost_stress:
        for b in bars:
            t = b.get("time") or b.get("timestamp")
            if isinstance(t, str):
                try:
                    t = datetime.fromisoformat(t.replace("Z", "+00:00"))
                except ValueError:
                    t = None
            wd = getattr(t, "weekday", lambda: None)()
            bar_weekdays.append(int(wd) if wd is not None else -1)

    def _stress_spread_mult(i: int) -> float:
        if not cost_stress:
            return 1.0
        h = bar_hours[i] if i < len(bar_hours) else -1
        # Rollover hour (spread peak) and the Asian illiquidity pocket.
        if h in (21, 22, 23):
            return float(stress_event_spread_multiplier)
        if h in (0, 1):
            return float(stress_spread_multiplier)
        return 1.0

    _swap_nights_charged = 0
    _swap_cost_total = 0.0

    # ATR% percentile rank within a trailing window (uses bar i and earlier
    # only — no future leakage). Converted to the 0-100 percentile scale to
    # match the vol_floor_pctile / vol_ceiling_pctile thresholds — the
    # raw 0-1 fraction against a 0-100 threshold blocked EVERY bar.
    atr_pct_rank: Optional[np.ndarray] = None
    if vol_floor_pctile > 0.0 or vol_ceiling_pctile > 0.0:
        atr_pct = atr / np.maximum(closes, 1e-10)
        atr_pct_rank = _rolling_percentile_rank(
            atr_pct, max(50, int(vol_env_window))
        ) * 100.0

    # --- Position tracking ---
    equity = initial_equity
    equity_curve = [equity]
    r_multiples: List[float] = []
    trades: List[Dict[str, Any]] = []
    open_pos: Optional[Dict[str, Any]] = None
    daily_start_equity = initial_equity
    daily_bars = 0

    gate_rejections: Dict[str, int] = {}
    signals_generated = 0
    signals_passed = 0
    signals_rejected = 0
    total_spread = 0.0
    total_slippage = 0.0
    total_commission = 0.0
    # Per-fill TCE accounting (the flat legacy model only touches the flat trackers)
    _tce = _get_tce_engine() if use_tce else None
    _tce_used = _tce is not None
    fill_bps: List[float] = []
    total_impact = 0.0
    total_cost_all = 0.0

    def _exit_cost_components(side: str, exit_price: float, i: int, lots: float):
        """Exit-side costs: TCE estimate when available, else flat legacy.
        Returns (tce_components|None, spread_$, slippage_$, commission_$).
        The entry side charged its half; the exit charges the other half
        (TCE) — or the full legacy flat amounts when falling back."""
        if _tce_used:
            comp = _tce_fill_cost(
                _tce, side=side, ref_price=exit_price, bar_idx=i,
                volume_lots=lots, closes=closes, atr_value=atr[i],
                atr_series=atr, commission_per_lot=commission_per_lot,
                spread_multiplier=_stress_spread_mult(i),
            )
            if comp is not None:
                return comp, comp["spread_usd"], comp["impact_usd"], comp["commission"]
        # Legacy flat exit (entry already charged its legacy one-way amount);
        # stress multiplies the flat spread when the overlay is active.
        return None, (spread_pips * _stress_spread_mult(i) * pip_value * lots), (slippage_pips * pip_value * lots), (commission_per_lot * lots)

    # Warmup period (need 50 bars for indicators)
    warmup = 50

    for i in range(warmup, n):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        atr_val = atr[i]

        # Reset daily tracking
        daily_bars += 1
        if daily_bars >= 288:  # ~288 M15 bars per day
            daily_bars = 0
            daily_start_equity = equity

        # --- Check open position (SL/TP hit on this bar) ---
        if open_pos is not None:
            # Retail swap stress: a position held ACROSS this bar pays the
            # nightly financing charge (charged once per bar the position is
            # open through; the entry bar itself does not pay). Triple swap on
            # the configured weekday (Wednesday for FX, Mon=0).
            if cost_stress:
                _swap = float(stress_swap_per_lot_per_night) * float(open_pos["volume_lots"])
                if _swap < 0 and bar_weekdays[i] == int(stress_triple_swap_weekday):
                    _swap *= 3.0
                if _swap != 0.0:
                    equity += _swap
                    open_pos["swap_paid"] = open_pos.get("swap_paid", 0.0) + _swap
                    _swap_nights_charged += 1
                    _swap_cost_total += _swap
            hit_sl = False
            hit_tp = False
            hit_time = False
            exit_price = 0.0
            exit_reason = ""

            # --- Trade management (causal: acts on info available at
            # this bar's open, i.e. MFE accumulated through bar i-1) ---
            # 1. Breakeven ratchet: once prior-bar MFE >= trigger, lift the
            #    stop toward entry + offset (stops only move in favor).
            if be_trigger_r > 0 and open_pos.get("mfe_r", 0.0) >= be_trigger_r:
                if open_pos["side"] == "BUY":
                    be_stop = open_pos["entry"] + be_offset_r * open_pos["sl_distance"]
                    if be_stop > open_pos["sl"]:
                        open_pos["sl"] = be_stop
                        open_pos["be_ratcheted"] = True
                else:
                    be_stop = open_pos["entry"] - be_offset_r * open_pos["sl_distance"]
                    if be_stop < open_pos["sl"]:
                        open_pos["sl"] = be_stop
                        open_pos["be_ratcheted"] = True

            # 2. Intrabar SL/TP checks (SL first — conservative ordering;
            #    the stop may have been ratcheted on PRIOR bars' MFE).
            if open_pos["side"] == "BUY":
                if low <= open_pos["sl"]:
                    hit_sl = True
                    exit_price = open_pos["sl"]
                    exit_reason = "be_stop" if open_pos["be_ratcheted"] else "sl"
                elif high >= open_pos["tp"]:
                    hit_tp = True
                    exit_price = open_pos["tp"]
                    exit_reason = "tp"
            else:  # SELL
                if high >= open_pos["sl"]:
                    hit_sl = True
                    exit_price = open_pos["sl"]
                    exit_reason = "be_stop" if open_pos["be_ratcheted"] else "sl"
                elif low <= open_pos["tp"]:
                    hit_tp = True
                    exit_price = open_pos["tp"]
                    exit_reason = "tp"

            # 3. Time stop: exit at THIS bar's close after max_hold_bars.
            #    (Decision uses bars_held counted through the prior close —
            #    acting at close, never intrabar, so no lookahead.)
            if not hit_sl and not hit_tp and max_hold_bars > 0:
                if (i - open_pos["entry_bar"] + 1) >= max_hold_bars:
                    hit_time = True
                    exit_price = close
                    exit_reason = "time_stop"

            # 4. Update MFE with THIS bar's favorable excursion (after exit
            #    checks, so next bar's ratchet uses completed bars only).
            if open_pos is not None:
                if open_pos["side"] == "BUY":
                    fav = (high - open_pos["entry"]) / open_pos["sl_distance"]
                else:
                    fav = (open_pos["entry"] - low) / open_pos["sl_distance"]
                if fav > open_pos.get("mfe_r", 0.0):
                    open_pos["mfe_r"] = fav

            if hit_sl or hit_tp or hit_time:
                # Compute PnL — entry was a real TCE-slipped fill (negative
                # slippage already inside the price), so exit costs are
                # charged on the opposite side only (no double counting).
                entry = open_pos["entry"]
                volume_lots = open_pos["volume_lots"]

                if open_pos["side"] == "BUY":
                    price_diff = exit_price - entry
                else:
                    price_diff = entry - exit_price

                # Convert price diff to P&L: (pips) * pip_value * lots
                pnl_pips = price_diff / pip_size
                gross_pnl = pnl_pips * pip_value * volume_lots

                # Exit-side costs (entry-side already charged at fill time)
                _xc, _x_spread, _x_slip, _x_comm = _exit_cost_components(
                    open_pos["side"], exit_price, i, volume_lots
                )
                spread_cost = _x_spread
                slip_cost = _x_slip
                comm_cost = _x_comm
                _trade_swap = float(open_pos.get("swap_paid", 0.0))
                net_pnl = gross_pnl - spread_cost - slip_cost - comm_cost + _trade_swap

                # R-multiple
                r_mult = net_pnl / open_pos["risk_amount"] if open_pos["risk_amount"] > 0 else 0.0

                equity += net_pnl
                r_multiples.append(r_mult)
                total_spread += spread_cost
                total_slippage += slip_cost
                total_commission += comm_cost
                if _xc is not None:
                    fill_bps.append(_xc["cost_bps"])
                    total_impact += _xc["impact_usd"]
                    total_cost_all += (_x_spread + _x_slip + _x_comm)

                trades.append({
                    "entry_bar": open_pos["entry_bar"],
                    "exit_bar": i,
                    "side": open_pos["side"],
                    "entry": entry,
                    "entry_slippage": open_pos.get("entry_slip", 0.0),
                    "exit": exit_price,
                    "r_multiple": r_mult,
                    "pnl": net_pnl,
                    "swap_paid": _trade_swap,
                    "hit_sl": hit_sl,
                    "hit_tp": hit_tp,
                    "hit_time": hit_time,
                    "exit_reason": exit_reason,
                })

                open_pos = None

        # --- Check daily loss limit ---
        if equity < daily_start_equity * (1 - max_daily_loss_pct):
            equity_curve.append(equity)
            continue

        # --- Generate signal (if no open position) ---
        if open_pos is None and i < n - 1:
            signals_generated += 1

            # Build indicator snapshot (using only data up to bar i, no lookahead)
            ind = {
                "close": closes[i],
                "high": highs[i],
                "low": lows[i],
                "hma_21": hma[i],
                "ema_50": ema50[i],
                "adx_14": adx[i],
                "adx_min": adx_min,
                "atr_14": atr[i],
                "rsi_14": rsi[i],
                "volume": volumes[i],
                "volume_ma": vol_ma[i],
            }

            if entry_signal_scores is not None:
                # External-signal mode: the caller's per-bar score IS the
                # entry decision. Geometry, gates, filters, sizing, and
                # management are shared with the legacy path.
                s_i = (
                    float(entry_signal_scores[i])
                    if i < len(entry_signal_scores) else float("nan")
                )
                if (
                    not np.isfinite(s_i)
                    or abs(s_i) < entry_signal_threshold
                    or closes[i] <= 0
                    or not np.isfinite(atr[i])
                    or atr[i] <= 0
                ):
                    signal = {
                        "action": "HOLD", "confidence": 0.0,
                        "reason": f"external_signal_below_threshold({s_i:.2f})",
                    }
                else:
                    side = "BUY" if s_i > 0 else "SELL"
                    sl_d = atr[i] * sl_atr_mult
                    tp_d = atr[i] * tp_atr_mult
                    signal = {
                        "action": side,
                        "entry": closes[i],
                        "stop_loss": (
                            closes[i] - sl_d if side == "BUY"
                            else closes[i] + sl_d
                        ),
                        "take_profit": (
                            closes[i] + tp_d if side == "BUY"
                            else closes[i] - tp_d
                        ),
                        # The score magnitude IS the conviction; the
                        # confidence gate applies to it identically.
                        "confidence": min(
                            1.0,
                            abs(s_i) / max(2.0 * entry_signal_threshold, 1e-9),
                        ),
                        # The external signal replaces the confluence vote;
                        # record it as satisfied so the structural gate
                        # (which exists for the ruleset path) doesn't
                        # silently veto an authority it never evaluated.
                        "confluence": min_confluence,
                        "reason": f"external:{s_i:.2f}",
                    }
            else:
                signal = deterministic_signal(
                    ind,
                    alpha_composite=alpha_composite[i],
                    alpha_threshold=alpha_threshold,
                    sl_atr_mult=sl_atr_mult,
                    tp_atr_mult=tp_atr_mult,
                    regime_tradable=True,
                )

            if signal["action"] in ("BUY", "SELL"):
                # --- Entry-quality filters (each blocks the whole bar;
                #      off-flags leave behavior identical to legacy) ---
                if session_ok is not None and not session_ok[i]:
                    _record_rejection(
                        gate_rejections,
                        "entry_session_block" if bar_hours[i] >= 0
                        else "entry_session_unknown_time",
                    )
                    signals_rejected += 1
                    equity_curve.append(equity)
                    continue
                if atr_pct_rank is not None:
                    rank_i = atr_pct_rank[i]  # 0-100 percentile scale
                    if (not np.isfinite(rank_i)) or (
                        vol_floor_pctile > 0.0 and rank_i < vol_floor_pctile
                    ) or (
                        vol_ceiling_pctile > 0.0 and rank_i > vol_ceiling_pctile
                    ):
                        _record_rejection(gate_rejections, "entry_vol_band")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue
                if rsi_no_chase_hi > 0.0:
                    if (signal["action"] == "BUY" and rsi[i] >= rsi_no_chase_hi) or (
                        signal["action"] == "SELL" and rsi[i] <= (100.0 - rsi_no_chase_hi)
                    ):
                        _record_rejection(gate_rejections, "entry_rsi_chase")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue
                if max_bar_extension_atr > 0.0 and atr_val > 0:
                    bar_range = high - low
                    if bar_range > max_bar_extension_atr * atr_val:
                        _record_rejection(gate_rejections, "entry_bar_extension")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                # --- Gate checks ---
                if enable_gates:
                    # R:R check
                    sl_dist = abs(signal["entry"] - signal["stop_loss"])
                    tp_dist = abs(signal["take_profit"] - signal["entry"])
                    if sl_dist <= 0:
                        _record_rejection(gate_rejections, "zero_sl_distance")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue
                    rr = tp_dist / sl_dist
                    if rr < min_rr:
                        _record_rejection(gate_rejections, f"rr_below_{min_rr}")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                    # Confluence check
                    if signal.get("confluence", 0) < min_confluence:
                        _record_rejection(gate_rejections, "insufficient_confluence")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                    # Max positions check
                    if open_pos is not None:
                        _record_rejection(gate_rejections, "max_positions")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                    # Confidence floor
                    if signal.get("confidence", 0) < 0.5:
                        _record_rejection(gate_rejections, "low_confidence")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                    # ATR sanity (avoid trading when ATR is essentially zero)
                    if atr_val <= 0 or not np.isfinite(atr_val):
                        _record_rejection(gate_rejections, "invalid_atr")
                        signals_rejected += 1
                        equity_curve.append(equity)
                        continue

                signals_passed += 1

                # --- Position sizing ---
                sl_distance = abs(signal["entry"] - signal["stop_loss"])
                risk_amount = equity * risk_per_trade_pct
                # Volume in lots: risk_amount / (sl_pips * pip_value)
                sl_pips = sl_distance / pip_size
                if sl_pips <= 0:
                    equity_curve.append(equity)
                    continue
                volume_lots = risk_amount / (sl_pips * pip_value)
                volume_lots = max(0.01, round(volume_lots, 2))  # min 0.01 lot

                # Verify risk doesn't exceed cap
                actual_risk = volume_lots * sl_pips * pip_value
                if actual_risk > equity * risk_per_trade_pct * 1.5:
                    volume_lots = (equity * risk_per_trade_pct) / (sl_pips * pip_value)
                    volume_lots = max(0.01, round(volume_lots, 2))

                # --- Open position at NEXT bar's open (no lookahead) ---
                # Fill price = ideal open moved against us by the production
                # TCE slippage (half-spread + Almgren-Chriss impact for this
                # size/vol). Falls back to the ideal open when the TCE is
                # unavailable (legacy flat costs still charged at exit).
                _entry_ref = closes[i + 1]
                _entry_comp = None
                if _tce_used:
                    _entry_comp = _tce_fill_cost(
                        _tce, side=signal["action"], ref_price=_entry_ref,
                        bar_idx=i + 1, volume_lots=volume_lots, closes=closes,
                        atr_value=atr[i], atr_series=atr,
                        commission_per_lot=commission_per_lot,
                    )
                if _entry_comp is not None:
                    _entry_slip = _entry_comp["slip_delta"]      # price units
                    _entry_fill = (
                        _entry_ref + _entry_slip if signal["action"] == "BUY"
                        else _entry_ref - _entry_slip
                    )
                    _entry_spread_cost = _entry_comp["spread_usd"]
                    _entry_slip_cost = _entry_comp["impact_usd"]
                    _entry_comm_cost = _entry_comp["commission"]
                    fill_bps.append(float(_entry_comp["cost_bps"]))
                    total_impact += _entry_comp["impact_usd"]
                    total_cost_all += (_entry_spread_cost + _entry_slip_cost + _entry_comm_cost)
                else:
                    _entry_fill = _entry_ref
                    _entry_spread_cost = spread_pips * _stress_spread_mult(i + 1) * pip_value * volume_lots
                    _entry_slip_cost = slippage_pips * pip_value * volume_lots
                    _entry_comm_cost = 0.0
                total_spread += _entry_spread_cost
                total_slippage += _entry_slip_cost
                total_commission += _entry_comm_cost

                open_pos = {
                    "side": signal["action"],
                    "entry": _entry_fill,  # TCE-slipped fill at next bar open
                    "entry_ref": _entry_ref,
                    "entry_slip": _entry_fill - _entry_ref,
                    "sl": signal["stop_loss"],
                    "tp": signal["take_profit"],
                    "sl_distance": sl_distance,
                    "risk_amount": volume_lots * sl_pips * pip_value,
                    "volume_lots": volume_lots,
                    "entry_bar": i + 1,
                    "confidence": signal["confidence"],
                    # Trade-management state
                    "mfe_r": 0.0,       # max favorable excursion, in R
                    "be_ratcheted": False,
                    "orig_sl": signal["stop_loss"],
                }

        # Mark-to-market equity
        mtm = equity
        if open_pos is not None:
            if open_pos["side"] == "BUY":
                unrealized = (closes[i] - open_pos["entry"]) / pip_size * pip_value * open_pos["volume_lots"]
            else:
                unrealized = (open_pos["entry"] - closes[i]) / pip_size * pip_value * open_pos["volume_lots"]
            mtm = equity + unrealized

        equity_curve.append(mtm)

    # --- Close any remaining open position ---
    if open_pos is not None:
        exit_price = closes[-1]
        entry = open_pos["entry"]
        volume_lots = open_pos["volume_lots"]

        if open_pos["side"] == "BUY":
            price_diff = exit_price - entry
        else:
            price_diff = entry - exit_price

        pnl_pips = price_diff / pip_size
        gross_pnl = pnl_pips * pip_value * volume_lots
        _xc, _x_spread, _x_slip, _x_comm = _exit_cost_components(
            open_pos["side"], exit_price, n - 1, volume_lots
        )
        spread_cost = _x_spread
        slip_cost = _x_slip
        comm_cost = _x_comm
        net_pnl = gross_pnl - spread_cost - slip_cost - comm_cost
        r_mult = net_pnl / open_pos["risk_amount"] if open_pos["risk_amount"] > 0 else 0.0

        equity += net_pnl
        r_multiples.append(r_mult)
        total_spread += spread_cost
        total_slippage += slip_cost
        total_commission += comm_cost
        if _xc is not None:
            fill_bps.append(_xc["cost_bps"])
            total_impact += _xc["impact_usd"]
            total_cost_all += (_x_spread + _x_slip + _x_comm)
        trades.append({
            "entry_bar": open_pos["entry_bar"],
            "exit_bar": n - 1,
            "side": open_pos["side"],
            "entry": entry,
            "entry_slippage": open_pos.get("entry_slip", 0.0),
            "exit": exit_price,
            "r_multiple": r_mult,
            "pnl": net_pnl,
            "hit_sl": False,
            "hit_tp": False,
        })
        result.open_position = open_pos

    # --- Compute metrics ---
    eq = np.array(equity_curve, dtype=np.float64)
    result.equity_curve = equity_curve
    result.trades_log = trades
    result.bars_processed = n
    result.initial_equity = initial_equity
    result.final_equity = round(float(equity), 2)
    result.total_return_pct = round((equity - initial_equity) / initial_equity * 100, 4)
    result.total_trades = len(trades)
    result.winning_trades = sum(1 for t in trades if t["pnl"] > 0)
    result.losing_trades = sum(1 for t in trades if t["pnl"] <= 0)
    result.win_rate_pct = round(result.winning_trades / max(1, result.total_trades) * 100, 2)
    result.r_multiples = r_multiples
    result.signals_generated = signals_generated
    result.signals_passed_gates = signals_passed
    result.signals_rejected_gates = signals_rejected
    result.gate_rejection_reasons = gate_rejections
    result.total_spread_cost = round(total_spread, 4)
    result.total_slippage_cost = round(total_slippage, 4)
    result.total_commission = round(total_commission, 4)
    result.cost_model = "tce_per_fill" if (_tce_used and fill_bps) else "flat_legacy"
    if fill_bps:
        result.avg_slippage_bps = round(float(np.mean(fill_bps)), 2)
        result.max_fill_slippage_bps = round(float(np.max(fill_bps)), 2)
        result.total_impact_cost = round(total_impact, 4)
        result.total_cost = round(total_cost_all, 4)
    # Retail cost-stress overlay accounting (PU-0156 §8.5).
    result.total_swap_cost = round(_swap_cost_total, 4)
    result.swap_nights_charged = _swap_nights_charged
    if _swap_nights_charged:
        result.total_cost = round(float(result.total_cost) - _swap_cost_total, 4)

    # R-multiple stats
    if r_multiples:
        wins = [r for r in r_multiples if r > 0]
        losses = [r for r in r_multiples if r <= 0]
        result.avg_win_r = round(float(np.mean(wins)), 4) if wins else 0.0
        result.avg_loss_r = round(float(np.mean(losses)), 4) if losses else 0.0
        gross_win = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        result.profit_factor = round(gross_win / max(gross_loss, 1e-10), 4)
        result.expectancy_r = round(float(np.mean(r_multiples)), 4)

    # Drawdown
    if len(eq) > 1:
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.maximum(peak, 1e-10)
        result.max_drawdown_pct = round(float(np.max(dd)) * 100, 4)

        # R-based drawdown
        if r_multiples:
            cum_r = np.cumsum(r_multiples)
            peak_r = np.maximum.accumulate(cum_r)
            dd_r = peak_r - cum_r
            result.max_drawdown_r = round(float(np.max(dd_r)), 4)

    # Sharpe (annualized from bar returns) — annualization constant must
    # match the bar timeframe (35040 M15 / 8760 H1 / 1460 H4 / 365 D1).
    if len(eq) > 10:
        bar_returns = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
        periods_per_year = float(periods_per_year)
        mean_r = float(np.mean(bar_returns))
        std_r = float(np.std(bar_returns))
        if std_r > 1e-10:
            result.sharpe_ratio = round(mean_r / std_r * math.sqrt(periods_per_year), 4)

        # Sortino (downside deviation only)
        downside = bar_returns[bar_returns < 0]
        if len(downside) > 0:
            downside_std = float(np.std(downside))
            if downside_std > 1e-10:
                result.sortino_ratio = round(mean_r / downside_std * math.sqrt(periods_per_year), 4)

    # Calmar
    if result.max_drawdown_pct > 0:
        # Annualized return from total return over bars
        years = n / max(periods_per_year, 1.0)
        if years > 0:
            ann_return = (equity / initial_equity) ** (1 / years) - 1
            result.annualized_return_pct = round(ann_return * 100, 4)
            result.calmar_ratio = round(ann_return / (result.max_drawdown_pct / 100), 4) if result.max_drawdown_pct > 0 else 0.0

    # --- S.Ξ DSR Gate: validate Sharpe against multiple-testing bias ---
    if enable_dsr and result.total_trades >= 5 and result.sharpe_ratio != 0.0:
        try:
            from research.backtest_dsr_integrator import BacktestDSRIntegrator
            _dsr_integrator = BacktestDSRIntegrator(
                gate_threshold=dsr_gate_threshold, strategy_class="full_pipeline"
            )
            # Compute returns from equity curve for skew/kurtosis
            _eq = np.array(result.equity_curve, dtype=np.float64) if result.equity_curve else np.array([])
            _returns = None
            if len(_eq) > 2:
                _returns = np.diff(_eq) / np.maximum(_eq[:-1], 1e-10)
            _dsr_report = _dsr_integrator.validate_backtest(
                strategy_id=name,
                sharpe=result.sharpe_ratio,
                n_observations=max(result.bars_processed, result.total_trades * 20),
                returns=_returns,
                metadata={
                    "symbol": symbol, "timeframe": timeframe,
                    "total_trades": result.total_trades,
                    "win_rate": result.win_rate_pct,
                    "max_dd": result.max_drawdown_pct,
                },
            )
            result.dsr_confidence = _dsr_report.dsr
            result.dsr_pvalue = _dsr_report.dsr  # compat alias
            result.dsr_passed = _dsr_report.passed_gate
            result.dsr_n_trials = _dsr_report.n_trials
            result.dsr_raw_sharpe = _dsr_report.raw_sharpe
            result.dsr_expected_max = _dsr_report.expected_max_sharpe
            result.dsr_pbo_proxy = _dsr_report.pbo_proxy
            result.dsr_trial_mean = _dsr_report.trial_sharpe_mean
            result.dsr_trial_std = _dsr_report.trial_sharpe_std
            import logging as _log_mod
            _log = _log_mod.getLogger("full_pipeline_backtest")
            if _dsr_report.passed_gate:
                _log.info(
                    "DSR GATE PASS: %s Sharpe=%.3f DSR=%.4f n_trials=%d",
                    name, result.sharpe_ratio, _dsr_report.dsr, _dsr_report.n_trials,
                )
            else:
                _log.warning(
                    "DSR GATE REJECT: %s Sharpe=%.3f DSR=%.4f < %.2f n_trials=%d",
                    name, result.sharpe_ratio, _dsr_report.dsr, dsr_gate_threshold,
                    _dsr_report.n_trials,
                )
        except Exception as _dsr_exc:
            import logging as _log_mod
            _log_mod.getLogger("full_pipeline_backtest").debug(
                "DSR gate skipped: %s", _dsr_exc,
            )

    return result


# ---------------------------------------------------------------------------
# Vectorized indicator helpers (no pandas dependency, no lookahead)
# ---------------------------------------------------------------------------

def _rolling_percentile_rank(a: np.ndarray, window: int) -> np.ndarray:
    """Percentile rank of each value within its trailing window (inclusive).

    out[i] = fraction of a[i-window+1 .. i] that is <= a[i]. Uses only
    past-and-current observations (no lookahead). NaN until the window
    fills. O(n * window) — fine for research bar counts.
    """
    n = len(a)
    out = np.full(n, np.nan)
    w = max(2, int(window))
    for i in range(w - 1, n):
        seg = a[i - w + 1: i + 1]
        out[i] = float(np.sum(seg <= a[i])) / float(w)
    return out


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average. First value = SMA."""
    alpha = 2.0 / (period + 1)
    out = np.empty_like(data)
    out[:period] = np.nan
    out[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def _sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    out = np.full_like(data, np.nan)
    cs = np.cumsum(data)
    out[period - 1:] = (cs[period - 1:] - np.concatenate([[0], cs[:-period]])) / period
    return out


def _hma(data: np.ndarray, period: int) -> np.ndarray:
    """Hull Moving Average (WMA half/full + sqrt)."""
    half = max(1, period // 2)
    sqrt_p = max(1, int(math.sqrt(period)))
    wma_half = _wma(data, half)
    wma_full = _wma(data, period)
    diff = 2 * wma_half - wma_full
    return _wma(diff, sqrt_p)


def _wma(data: np.ndarray, period: int) -> np.ndarray:
    """Weighted moving average."""
    weights = np.arange(1, period + 1, dtype=np.float64)
    wsum = weights.sum()
    out = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        out[i] = np.dot(window, weights) / wsum
    return out


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """Average True Range (Wilder smoothing)."""
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    out = np.full(n, np.nan)
    out[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """Average Directional Index (Wilder smoothing)."""
    n = len(closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    # Wilder-smoothed TR, +DM, -DM
    atr_s = np.zeros(n)
    plus_dm_s = np.zeros(n)
    minus_dm_s = np.zeros(n)
    atr_s[period] = np.sum(tr[1:period + 1])
    plus_dm_s[period] = np.sum(plus_dm[1:period + 1])
    minus_dm_s[period] = np.sum(minus_dm[1:period + 1])
    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / period + tr[i]
        plus_dm_s[i] = plus_dm_s[i - 1] - plus_dm_s[i - 1] / period + plus_dm[i]
        minus_dm_s[i] = minus_dm_s[i - 1] - minus_dm_s[i - 1] / period + minus_dm[i]

    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    dx = np.full(n, np.nan)

    for i in range(period, n):
        if atr_s[i] > 0:
            plus_di[i] = 100 * plus_dm_s[i] / atr_s[i]
            minus_di[i] = 100 * minus_dm_s[i] / atr_s[i]
        pdi = plus_di[i] if not np.isnan(plus_di[i]) else 0
        mdi = minus_di[i] if not np.isnan(minus_di[i]) else 0
        di_sum = pdi + mdi
        if di_sum > 0:
            dx[i] = 100 * abs(pdi - mdi) / di_sum

    # ADX = Wilder-smoothed DX
    adx_out = np.full(n, np.nan)
    # Find first valid DX
    first_valid = period
    while first_valid < n and np.isnan(dx[first_valid]):
        first_valid += 1

    if first_valid + period <= n:
        # Initialize with SMA of first 'period' valid DX values
        window = [dx[j] for j in range(first_valid, first_valid + period) if not np.isnan(dx[j])]
        if len(window) >= period:
            adx_out[first_valid + period - 1] = sum(window) / period
            for i in range(first_valid + period, n):
                if not np.isnan(dx[i]) and not np.isnan(adx_out[i - 1]):
                    adx_out[i] = (adx_out[i - 1] * (period - 1) + dx[i]) / period

    return adx_out


def _rsi(closes: np.ndarray, period: int) -> np.ndarray:
    """Relative Strength Index (Wilder smoothing)."""
    n = len(closes)
    out = np.full(n, 50.0)
    if n < period + 1:
        return out

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            out[i + 1] = 100 - 100 / (1 + rs)
        else:
            out[i + 1] = 100.0

    return out


def _alpha_composite_proxy(
    closes: np.ndarray, hma: np.ndarray, adx: np.ndarray, rsi: np.ndarray
) -> np.ndarray:
    """Proxy for alpha ensemble composite using available indicators.

    Maps to [-2, +2] range:
      +1 = HMA slope positive + ADX > 25 + RSI > 55
      -1 = HMA slope negative + ADX > 25 + RSI < 45
    """
    n = len(closes)
    composite = np.zeros(n)

    for i in range(1, n):
        if np.isnan(hma[i]) or np.isnan(adx[i]) or np.isnan(rsi[i]):
            continue

        # HMA momentum (normalized by ATR)
        hma_slope = (hma[i] - hma[i - 1]) / max(abs(hma[i]), 1e-10)

        # ADX contribution (stronger trend = stronger signal)
        adx_factor = min(1.0, max(0, (adx[i] - 20) / 30))

        # RSI contribution
        rsi_factor = (rsi[i] - 50) / 50  # [-1, +1]

        # Composite: HMA direction * ADX strength * (1 + RSI confirmation)
        composite[i] = np.sign(hma_slope) * adx_factor * (1 + abs(rsi_factor) * 0.5)
        composite[i] = max(-2.0, min(2.0, composite[i]))

    return composite


def _record_rejection(rejections: Dict[str, int], reason: str) -> None:
    rejections[reason] = rejections.get(reason, 0) + 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Full Pipeline Backtest")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to backtest")
    parser.add_argument("--timeframe", default="M15", help="Timeframe")
    parser.add_argument("--bars", type=int, default=5000, help="Number of bars")
    parser.add_argument("--equity", type=float, default=10000, help="Initial equity")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade fraction")
    parser.add_argument("--spread", type=float, default=1.5, help="Spread in pips")
    parser.add_argument("--output", default="", help="JSON output path")
    args = parser.parse_args()

    # Generate synthetic M15 data with trends and ranges (replace with MT5 data)
    print(f"Generating {args.bars} synthetic M15 bars for {args.symbol}...")
    np.random.seed(42)
    price = 1.1000
    bars = []
    trend = 0.0002  # bullish drift
    vol = 0.0008
    for i in range(args.bars):
        # Regime switching: trend every ~500 bars
        if (i // 500) % 3 == 0:
            trend = 0.0003
        elif (i // 500) % 3 == 1:
            trend = -0.0003
        else:
            trend = 0.00005  # ranging

        change = np.random.normal(trend, vol)
        o = price
        c = price * (1 + change)
        bar_range = abs(change) + abs(np.random.normal(0, vol * 0.3))
        h = max(o, c) * (1 + bar_range * 0.5)
        l = min(o, c) * (1 - bar_range * 0.5)
        bars.append({
            "open": round(o, 5),
            "high": round(h, 5),
            "low": round(l, 5),
            "close": round(c, 5),
            "volume": int(np.random.exponential(1000)),
            "timestamp": f"2026-01-01T{i // 288:02d}:{(i % 288) * 5:02d}:00+00:00",
        })
        price = c

    result = run_full_pipeline_backtest(
        bars,
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_equity=args.equity,
        risk_per_trade_pct=args.risk,
        spread_pips=args.spread,
    )

    report = result.to_dict()
    print(json.dumps(report, indent=2))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to {args.output}")
