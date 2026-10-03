"""Kronos Live Market Trading Integrator.

Provides production readiness for the 4 core Kronos applications:
1. Alpha Factor in Multi-Factor Ensemble
2. Volatility Input for Risk & Position Sizing Models
3. Synthetic Data Generator for Robustness Testing
4. Interactive Market Simulator for RL Agent Training

All outputs are advisory only (``execution_authority=False``).
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from kronos_bridge import (
    BACKEND_HEURISTIC,
    CandleForecast,
    KronosMultiDimForecast,
    KronosPredictor,
)
from kronos_downloader import KronosDownloader

log = logging.getLogger("KronosTradingIntegrator")


def _advisory_meta(predictor: KronosPredictor) -> Dict[str, Any]:
    status = predictor.backend_status()
    return {
        "backend": status["backend"],
        "weights_loaded": status["weights_loaded"],
        "model_name": status["model_name"],
        "load_reason": status.get("load_reason", ""),
        "execution_authority": False,
    }


class KronosTradingIntegrator:
    """Production Integrator wiring Kronos model into live trading systems."""

    def __init__(
        self,
        model_key: str = "kronos-mini",
        *,
        prefer_neural: bool = True,
        force_heuristic: Optional[bool] = None,
    ):
        self.model_key = model_key
        self.downloader = KronosDownloader()
        self.local_path = self.downloader.get_local_path(model_key)
        if force_heuristic is None:
            force_heuristic = os.getenv("KRONOS_FORCE_HEURISTIC", "").lower() in {"1", "true", "yes"}
        self.predictor = KronosPredictor(
            model_name=model_key,
            prefer_neural=prefer_neural,
            force_heuristic=bool(force_heuristic),
        )

    # ── Task 1: Alpha Factor in Multi-Factor Ensemble ──────────────────────────

    def get_alpha_factor(
        self, symbol: str, ohlcv_history: List[Dict[str, float]], pred_len: int = 24
    ) -> Dict[str, Any]:
        """Generate Alpha Factor signal [-1.0, 1.0] for multi-factor ensemble.

        Returns:
            alpha_score: float in [-1.0, 1.0]
            confidence: float in [0.0, 1.0]
            direction: 'BUY', 'SELL', or 'NEUTRAL'
            predicted_return_pct: float
        """
        forecast = self.predictor.predict(
            symbol=symbol, ohlcv_history=ohlcv_history, pred_len=pred_len, sample_count=10
        )

        ret_pct = forecast.predicted_return_pct
        confidence = forecast.confidence_score

        # Bound alpha score to [-1.0, 1.0] using tanh scaling
        raw_score = math.tanh(ret_pct / 2.0)
        weighted_alpha = raw_score * confidence

        if weighted_alpha > 0.15:
            direction = "BUY"
        elif weighted_alpha < -0.15:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        return {
            "symbol": symbol,
            "alpha_score": round(weighted_alpha, 4),
            "raw_score": round(raw_score, 4),
            "confidence": round(confidence, 4),
            "direction": direction,
            "predicted_return_pct": round(ret_pct, 4),
            "horizon_bars": pred_len,
            **_advisory_meta(self.predictor),
            "forecast_backend": forecast.backend,
        }

    # ── Task 2: Volatility Input for Risk Models ──────────────────────────────

    def get_volatility_input(
        self, symbol: str, ohlcv_history: List[Dict[str, float]]
    ) -> Dict[str, Any]:
        """Predict 5-day realized volatility for RiskManager position sizing.

        Returns:
            predicted_volatility_5d: float (annualized/period ratio)
            volatility_regime: 'LOW', 'NORMAL', 'ELEVATED', 'HIGH'
            risk_adjustment_factor: float (0.5 to 1.0)
        """
        forecast = self.predictor.predict(
            symbol=symbol, ohlcv_history=ohlcv_history, pred_len=24, sample_count=10
        )

        vol_5d = forecast.predicted_volatility_5d

        if vol_5d < 0.015:
            regime = "LOW"
            adj_factor = 1.0
        elif vol_5d < 0.035:
            regime = "NORMAL"
            adj_factor = 0.9
        elif vol_5d < 0.06:
            regime = "ELEVATED"
            adj_factor = 0.7
        else:
            regime = "HIGH"
            adj_factor = 0.5

        return {
            "symbol": symbol,
            "predicted_volatility_5d": round(vol_5d, 4),
            "volatility_regime": regime,
            "risk_adjustment_factor": round(adj_factor, 2),
            **_advisory_meta(self.predictor),
            "forecast_backend": forecast.backend,
        }

    # ── Multi-Dimensional Forecasting (Paper §3 BSQ hierarchical tokens) ───

    def get_multi_dim_forecast(
        self, symbol: str, ohlcv_history: List[Dict[str, float]],
        pred_len: int = 24, sample_count: int = 10, temperature: float = 1.0, top_p: float = 0.9,
        timeframe: str = "H1",
    ) -> Dict[str, Any]:
        """Full OHLCVA forecast via Kronos hierarchical tokenizer + autoregressive decoder.

        Returns paper's 6-dim K-line prediction (open/high/low/close/volume/amount)
        with per-bar mean/std and trajectories for every horizon step, plus
        confidence derived from trajectory dispersion (capped 0.55 when heuristic).
        """
        md: KronosMultiDimForecast = self.predictor.predict_multi_dim(
            symbol=symbol, ohlcv_history=ohlcv_history, pred_len=pred_len,
            sample_count=sample_count, temperature=temperature, top_p=top_p, timeframe=timeframe,
        )
        payload = md.as_advisory_dict()
        payload["alpha_signal"] = self._alpha_from_return(payload["predicted_return_pct"], payload["confidence"])
        payload["execution_authority"] = False
        return payload

    def get_volatility_term_structure(
        self, symbol: str, ohlcv_history: List[Dict[str, float]], horizon_bars: int = 24
    ) -> Dict[str, Any]:
        """Advanced volatility: 5d/10d/20d + per-bar term structure for RiskManager.

        Paper §4.2 volatility forecasting: 9% lower MAE vs baselines via
        trajectory-dispersion-aware scaling. Returns regime (LOW/NORMAL/ELEVATED/HIGH)
        and reduction-only risk_adjustment_factor (0.5—1.0) to feed RiskManager.
        """
        return self.predictor.get_advanced_volatility(symbol, ohlcv_history, horizon_bars=horizon_bars)

    def get_risk_adjusted_sizing(
        self, symbol: str, ohlcv_history: List[Dict[str, float]],
        account_balance: float = 10000.0, risk_per_trade_pct: float = 1.0,
        entry_price: Optional[float] = None, stop_loss: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Volatility-aware position sizing that respects RiskManager + paper vol regime.

        Combines Kronos predicted vol term structure with account risk budget.
        The returned lots are capped by 1% risk-per-trade and the predicted vol
        regime's risk_adjustment_factor (0.5–1.0). Never claims execution authority.
        """
        ohlcv = ohlcv_history
        vol_bundle = self.predictor.get_advanced_volatility(symbol, ohlcv_history, horizon_bars=24)
        pred_vol = float(vol_bundle["volatility_5d"])
        risk_adj = float(vol_bundle["risk_adjustment_factor"])
        dollar_risk = float(account_balance) * (float(risk_per_trade_pct) / 100.0) * risk_adj
        last_close = float(ohlcv[-1].get("close", entry_price or 1.0) or 1.0) if ohlcv else float(entry_price or 1.0)
        if entry_price is not None and stop_loss is not None and abs(entry_price - stop_loss) > 0:
            sl_dist = abs(float(entry_price) - float(stop_loss))
        else:
            sl_dist = max(0.0005, last_close * (pred_vol / 2.0))
        recommended_lots = round(max(0.01, min(10.0, (dollar_risk / max(sl_dist, 1e-6)) / 100000.0)), 2)
        return {
            "symbol": symbol,
            "volatility_term_structure": vol_bundle["term_structure"],
            "volatility_5d": vol_bundle["volatility_5d"],
            "volatility_10d": vol_bundle["volatility_10d"],
            "volatility_20d": vol_bundle["volatility_20d"],
            "volatility_regime": vol_bundle["regime"],
            "risk_adjustment_factor": risk_adj,
            "dollar_risk_allowed": round(dollar_risk, 2),
            "estimated_sl_distance": round(sl_dist, 5),
            "recommended_lots": recommended_lots,
            "confidence": vol_bundle["confidence"],
            "backend": vol_bundle["backend"],
            "weights_loaded": vol_bundle["weights_loaded"],
            "execution_authority": False,
        }

    @staticmethod
    def _alpha_from_return(ret_pct: float, confidence: float) -> Dict[str, Any]:
        raw = math.tanh(float(ret_pct) / 2.0)
        weighted = raw * float(confidence)
        direction = "BUY" if weighted > 0.15 else "SELL" if weighted < -0.15 else "NEUTRAL"
        return {"raw_score": round(raw, 4), "weighted_alpha": round(weighted, 4), "direction": direction}

    # ── Task 3: Synthetic Data Generator for Robustness Testing ───────────────

    def generate_robustness_suite(
        self, symbol: str = "XAUUSD", current_price: float = 2000.0, length: int = 100
    ) -> Dict[str, List[Dict[str, float]]]:
        """Generate synthetic market scenarios for Monte Carlo stress testing."""
        regimes = ["bull", "bear", "volatile", "range"]
        suite = {}
        for r in regimes:
            suite[r] = self.predictor.generate_synthetic_regime(
                symbol=symbol, start_price=current_price, length=length, regime=r
            )
        return suite

    def advisory_bundle(
        self, symbol: str, ohlcv_history: List[Dict[str, float]], pred_len: int = 24
    ) -> Dict[str, Any]:
        """Combined advisory payload for the trading cycle (never execution authority)."""
        alpha = self.get_alpha_factor(symbol, ohlcv_history, pred_len=pred_len)
        vol = self.get_volatility_input(symbol, ohlcv_history)
        multi = self.get_multi_dim_forecast(symbol, ohlcv_history, pred_len=pred_len)
        adv_vol = self.get_volatility_term_structure(symbol, ohlcv_history)
        return {
            "symbol": symbol,
            "alpha": alpha,
            "volatility": vol,
            "multi_dim_forecast": {"horizon_bars": multi["horizon_bars"], "predicted_return_pct": multi["predicted_return_pct"], "confidence": multi["confidence"], "volatility": multi["volatility"], "trajectory_count": multi["trajectory_count"], "backend": multi["backend"]},
            "advanced_volatility": adv_vol,
            "backend": self.predictor.backend,
            "weights_loaded": self.predictor.weights_loaded,
            "load_reason": self.predictor.load_reason,
            "execution_authority": False,
        }


# ── Task 4: Interactive Kronos RL Market Simulator ──────────────────────────

# Entry actions (Q-learning baseline) + position-management actions (PPO Phase 2).
MANAGEMENT_ACTIONS = ("HOLD", "TIGHTEN_SL", "TRAIL", "PARTIAL_CLOSE", "FULL_EXIT")
ENTRY_ACTIONS = ("BUY", "SELL")
ALL_SIM_ACTIONS = ENTRY_ACTIONS + MANAGEMENT_ACTIONS


class KronosRLMarketSimulator:
    """Gym-compatible market simulator environment powered by Kronos.

    Allows RL Agents (e.g. RLTrainer / PPOTrainer) to train on diverse synthetic
    market regimes. Synthetic paths are heuristic-regime generators; forecasts
    used elsewhere may be neural when weights load.

    Action space
    ------------
    Entry (legacy Q-learning): BUY, SELL, HOLD
    Management (PPO Phase 2): HOLD, TIGHTEN_SL, TRAIL, PARTIAL_CLOSE, FULL_EXIT

    HOLD is shared. Management actions are no-ops when flat.
    """

    def __init__(
        self,
        symbol: str = "EURUSD",
        initial_balance: float = 10000.0,
        regime: str = "volatile",
        max_steps: int = 100,
        *,
        force_heuristic: bool = True,
        seed: int = 42,
    ):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.regime = regime
        self.max_steps = max_steps
        self.seed = int(seed)
        # Offline sim paths do not require neural weights; keep startup cheap/deterministic.
        self.predictor = KronosPredictor("kronos-mini", force_heuristic=force_heuristic)

        self.reset()

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        """Reset environment and generate new synthetic price path."""
        if seed is not None:
            self.seed = int(seed)
        self.balance = self.initial_balance
        self.position = 0.0  # signed size: +long / -short / 0 flat
        self.position_size = 0.0  # absolute size in (0, 1]
        self.entry_price = 0.0
        self.stop_loss: Optional[float] = None
        self.trail_active = False
        self.trail_pct = 0.008
        self.current_step = 0
        self.total_pnl = 0.0

        # Generate fresh market series
        self.candles = self.predictor.generate_synthetic_regime(
            symbol=self.symbol,
            start_price=1.0850,
            length=self.max_steps + 10,
            regime=self.regime,
            seed=self.seed,
        )
        return self._get_observation()

    def open_management_episode(
        self, direction: float = 1.0, *, seed: Optional[int] = None
    ) -> Dict[str, Any]:
        """Reset, open a unit position, and return observation for management training."""
        obs = self.reset(seed=seed)
        side = 1.0 if float(direction) >= 0 else -1.0
        price = float(obs["close"])
        self.position = side
        self.position_size = 1.0
        self.entry_price = price
        # Default protective stop ~1% away from entry.
        self.stop_loss = price * (1.0 - 0.03 * side)
        self.trail_active = False
        return self._get_observation()

    def _get_observation(self) -> Dict[str, Any]:
        c = self.candles[self.current_step]
        return {
            "step": self.current_step,
            "close": c["close"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "volume": c["volume"],
            "balance": self.balance,
            "position": self.position,
            "position_size": self.position_size,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "trail_active": self.trail_active,
            "total_pnl": self.total_pnl,
            "regime": self.regime,
            "backend": BACKEND_HEURISTIC,
            "execution_authority": False,
        }

    def _direction(self) -> float:
        if self.position > 0:
            return 1.0
        if self.position < 0:
            return -1.0
        return 0.0

    def _apply_trail(self, price: float) -> None:
        if not self.trail_active or self.position == 0.0:
            return
        side = self._direction()
        candidate = price * (1.0 - self.trail_pct * side)
        if self.stop_loss is None:
            self.stop_loss = candidate
        elif side > 0:
            self.stop_loss = max(float(self.stop_loss), candidate)
        else:
            self.stop_loss = min(float(self.stop_loss), candidate)

    def _stop_hit(self, candle: Dict[str, float]) -> bool:
        if self.stop_loss is None or self.position == 0.0:
            return False
        side = self._direction()
        if side > 0:
            return float(candle["low"]) <= float(self.stop_loss)
        return float(candle["high"]) >= float(self.stop_loss)

    def _close_fraction(self, price: float, fraction: float) -> float:
        """Realize PnL for a fraction of the open position; return reward component."""
        if self.position == 0.0 or self.entry_price <= 0 or fraction <= 0:
            return 0.0
        fraction = min(1.0, max(0.0, float(fraction)))
        side = self._direction()
        size = abs(self.position) * fraction
        pnl = side * ((price - self.entry_price) / self.entry_price) * 100.0 * size
        remaining = abs(self.position) * (1.0 - fraction)
        if remaining < 1e-9:
            self.position = 0.0
            self.position_size = 0.0
            self.entry_price = 0.0
            self.stop_loss = None
            self.trail_active = False
        else:
            self.position = side * remaining
            self.position_size = remaining
        return pnl

    def step(self, action: str) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        """Execute RL action and advance simulator.

        Supports entry actions BUY/SELL and management actions
        HOLD / TIGHTEN_SL / TRAIL / PARTIAL_CLOSE / FULL_EXIT.

        Returns (observation, reward, done, info)
        """
        action = str(action or "HOLD").upper()
        curr = self.candles[self.current_step]
        curr_price = float(curr["close"])
        self.current_step += 1
        nxt = self.candles[self.current_step]
        next_price = float(nxt["close"])
        price_change_pct = (next_price - curr_price) / curr_price if curr_price else 0.0

        reward = 0.0
        info_extra: Dict[str, Any] = {}

        if action == "BUY":
            if self.position == 0.0:
                self.position = 1.0
                self.position_size = 1.0
                self.entry_price = curr_price
                self.stop_loss = curr_price * 0.99
                self.trail_active = False
            reward = price_change_pct * 100.0 * abs(self.position or 1.0)

        elif action == "SELL":
            if self.position == 0.0:
                self.position = -1.0
                self.position_size = 1.0
                self.entry_price = curr_price
                self.stop_loss = curr_price * 1.01
                self.trail_active = False
            reward = -price_change_pct * 100.0 * abs(self.position or 1.0)

        elif action == "TIGHTEN_SL":
            if self.position != 0.0:
                side = self._direction()
                if self.stop_loss is None:
                    self.stop_loss = curr_price * (1.0 - 0.01 * side)
                else:
                    # Move stop halfway toward current price (tighten risk).
                    self.stop_loss = float(self.stop_loss) + 0.5 * (curr_price - float(self.stop_loss))
                reward = self.position * price_change_pct * 100.0
                # Small shaping for keeping a protective stop while in profit.
                unreal = side * ((curr_price - self.entry_price) / self.entry_price) if self.entry_price else 0.0
                if unreal > 0:
                    reward += 0.05

        elif action == "TRAIL":
            if self.position != 0.0:
                self.trail_active = True
                self._apply_trail(curr_price)
                reward = self.position * price_change_pct * 100.0

        elif action == "PARTIAL_CLOSE":
            if self.position != 0.0:
                reward = self._close_fraction(curr_price, 0.5)
            # Flat after partial still advances; residual gets mark-to-market below.

        elif action == "FULL_EXIT":
            if self.position != 0.0:
                reward = self._close_fraction(curr_price, 1.0)
                info_extra["exited"] = True

        else:  # HOLD (default / unknown)
            if self.position != 0.0:
                reward = self.position * price_change_pct * 100.0

        # Trailing / protective stop evaluated after price advance, except when
        # the agent already closed (avoid same-bar double-exit after PARTIAL/FULL).
        if self.position != 0.0 and action not in {"PARTIAL_CLOSE", "FULL_EXIT"}:
            self._apply_trail(next_price)
            if self._stop_hit(nxt):
                exit_px = float(self.stop_loss) if self.stop_loss is not None else next_price
                # Incremental move from curr_price to stop exit replaces mark-to-market
                step_exit_ret = (
                    self._direction() * ((exit_px - curr_price) / curr_price) * 100.0 * abs(self.position)
                    if curr_price > 0 else 0.0
                )
                reward = step_exit_ret
                self._close_fraction(exit_px, 1.0)
                info_extra["stop_hit"] = True
        elif self.position != 0.0 and action == "PARTIAL_CLOSE" and self.trail_active:
            self._apply_trail(next_price)

        self.total_pnl += reward
        done = self.current_step >= self.max_steps or (
            action == "FULL_EXIT" and abs(self.position) < 1e-9
        )

        return (
            self._get_observation(),
            round(reward, 4),
            done,
            {"pnl": self.total_pnl, "execution_authority": False, **info_extra},
        )

    def observation_to_rl_state(self, obs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Map simulator observation into RLTrainer state feature schema."""
        obs = obs or self._get_observation()
        close = float(obs.get("close") or 0.0)
        open_p = float(obs.get("open") or close)
        high = float(obs.get("high") or close)
        low = float(obs.get("low") or close)
        ret = ((close - open_p) / open_p) if open_p else 0.0
        rng = ((high - low) / open_p) if open_p else 0.0
        # Real RSI(14) computed point-in-time from the simulated close series up
        # to the current step (never uses future bars). RLTrainer._build_state_key
        # bins this feature, so the key must be present in the state schema.
        # With < 15 closes the RSI is not measurable; a neutral 50.0 prior is
        # used instead of a fabricated reading.
        rsi_14 = 50.0
        try:
            import pandas as _pd
            from core.technical_indicators import rsi as _rsi_fn
            closes = [float(c.get("close")) for c in self.candles[: self.current_step + 1]]
            if len(closes) >= 15:
                rsi_14 = float(_rsi_fn(_pd.Series(closes), 14).iloc[-1])
        except Exception:
            rsi_14 = 50.0
        # Candle geometry metrics (no synthetic RSI/MACD — use real price features)
        # Volatility regime from bar range
        vol_regime = "high" if rng > 0.01 else "medium" if rng > 0.004 else "low"
        # Price position within bar: 0.0=at low, 1.0=at high (real-time momentum proxy)
        bar_position = (close - low) / (high - low) if (high - low) > 0 else 0.5
        # Candle body ratio: |close-open| / (high-low) = 0=doji, 1=full body
        body_ratio = abs(close - open_p) / (high - low) if (high - low) > 0 else 0.0
        # Direction: +1 bullish candle, -1 bearish, 0 doji
        direction = 1 if close > open_p else (-1 if close < open_p else 0)
        stop = obs.get("stop_loss")
        entry = float(obs.get("entry_price") or 0.0)
        stop_dist = 0.0
        if stop is not None and close:
            stop_dist = abs(float(stop) - close) / close
        return {
            "rsi_14": round(rsi_14, 2),
            "volatility_range": round(rng, 6),          # Real volatility (bar range %)
            "bar_position": round(bar_position, 4),       # Real momentum proxy (0-1)
            "body_ratio": round(body_ratio, 4),           # Real candle strength (0-1)
            "direction": direction,                        # Real candle direction (-1/0/+1)
            "confluence_score": 5,
            "near_sr": abs(ret) < 0.0005,
            "htf_trend": 1 if ret > 0 else (-1 if ret < 0 else 0),
            "news_sentiment": 0.0,
            "volatility_regime": vol_regime,
            "trend_strength": min(1.0, abs(ret) * 50.0),
            "session": "sim",
            "kronos_position": float(obs.get("position") or 0.0),
            "kronos_position_size": float(obs.get("position_size") or 0.0),
            "kronos_stop_distance": stop_dist,
            "kronos_trail_active": 1.0 if obs.get("trail_active") else 0.0,
            "kronos_regime": obs.get("regime", self.regime),
            "execution_authority": False,
        }

    def observation_vector(self, obs: Optional[Dict[str, Any]] = None) -> List[float]:
        """Dense numeric features for PPO (fixed length)."""
        obs = obs or self._get_observation()
        close = float(obs.get("close") or 0.0)
        open_p = float(obs.get("open") or close)
        high = float(obs.get("high") or close)
        low = float(obs.get("low") or close)
        entry = float(obs.get("entry_price") or 0.0)
        stop = obs.get("stop_loss")
        ret = ((close - open_p) / open_p) if open_p else 0.0
        rng = ((high - low) / open_p) if open_p else 0.0
        unreal = 0.0
        if entry and abs(float(obs.get("position") or 0.0)) > 0:
            side = 1.0 if float(obs["position"]) > 0 else -1.0
            unreal = side * ((close - entry) / entry)
        stop_dist = 0.0
        if stop is not None and close:
            stop_dist = (float(stop) - close) / close
        return [
            ret,
            rng,
            float(obs.get("position") or 0.0),
            float(obs.get("position_size") or 0.0),
            unreal,
            stop_dist,
            1.0 if obs.get("trail_active") else 0.0,
            float(obs.get("total_pnl") or 0.0) / 100.0,
            float(obs.get("step") or 0) / max(1, self.max_steps),
        ]
