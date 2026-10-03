"""MT5 Technical Data Adapter for Kronos Foundation Model.

Integrates MetaTrader 5 (MT5) technical data:
- MT5 Rates / OHLCV (open, high, low, close, tick_volume, spread, real_volume)
- MT5 Depth of Market (DOM / L2 order book depth & imbalance_rate)
- Technical Indicators (ATR, RSI, moving averages)

Directly into Kronos for all 4 live production tasks:
1. Alpha Factor in Multi-Factor Ensemble (MT5 Rates + DOM Imbalance + ATR)
2. Volatility Input for MT5 Risk & Lot Sizing Models
3. Synthetic MT5 Rates & Technical Data Generator (Monte Carlo)
4. Interactive MT5 RL Market Simulator (with Spread, DOM Depth & Slippage)
"""
from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Optional, Tuple, Union

from kronos_trading_integrator import KronosTradingIntegrator
from kronos_bridge import KronosPredictor

log = logging.getLogger("KronosMT5Adapter")


class KronosMT5Adapter:
    """MetaTrader 5 (MT5) Technical Data Integration Adapter for Kronos."""

    def __init__(self, model_key: str = "kronos-mini", *, force_heuristic: Optional[bool] = None):
        self.integrator = KronosTradingIntegrator(model_key=model_key, force_heuristic=force_heuristic)
        self.predictor = self.integrator.predictor

    # ── MT5 Technical Data Normalizer ──────────────────────────────────────────

    @staticmethod
    def normalize_mt5_rates(raw_rates: Union[List[Dict[str, Any]], List[Tuple]]) -> List[Dict[str, float]]:
        """Convert MT5 rates (from mt5.copy_rates_from_pos) into standardized OHLCV dicts."""
        normalized = []
        for r in raw_rates:
            if isinstance(r, dict):
                normalized.append({
                    "open": float(r.get("open", 0.0)),
                    "high": float(r.get("high", 0.0)),
                    "low": float(r.get("low", 0.0)),
                    "close": float(r.get("close", 0.0)),
                    "volume": float(r.get("tick_volume", r.get("volume", 0.0))),
                    "spread": float(r.get("spread", 0.0)),
                    "real_volume": float(r.get("real_volume", 0.0)),
                })
            elif isinstance(r, (tuple, list)) and len(r) >= 5:
                # Standard MT5 rates tuple: (time, open, high, low, close, tick_volume, spread, real_volume)
                normalized.append({
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]) if len(r) > 5 else 0.0,
                    "spread": float(r[6]) if len(r) > 6 else 0.0,
                    "real_volume": float(r[7]) if len(r) > 7 else 0.0,
                })
        return normalized

    # ── Task 1: MT5 Technical Alpha Factor ────────────────────────────────────

    def get_mt5_alpha_factor(
        self,
        symbol: str,
        mt5_rates: List[Any],
        dom: Optional[Dict[str, Any]] = None,
        pred_len: int = 24,
    ) -> Dict[str, Any]:
        """Generate MT5 Technical Alpha Factor combining Kronos forecasts with MT5 DOM depth & spread."""
        ohlcv = self.normalize_mt5_rates(mt5_rates)
        base_alpha = self.integrator.get_alpha_factor(symbol, ohlcv, pred_len=pred_len)

        # Incorporate MT5 DOM (Depth of Market) imbalance if available
        dom_modifier = 0.0
        spread_penalty = 1.0
        if dom and isinstance(dom, dict):
            imbalance = float(dom.get("imbalance_rate", 0.0))  # Positive = net buyer depth, Negative = seller
            dom_modifier = math.tanh(imbalance) * 0.2

            spread = float(dom.get("spread", 0.0))
            if spread > 20.0:  # High spread penalty
                spread_penalty = 0.7

        # Composite MT5 Alpha Score
        composite_score = (base_alpha["alpha_score"] + dom_modifier) * spread_penalty
        composite_score = max(-1.0, min(1.0, composite_score))

        if composite_score > 0.15:
            direction = "BUY"
        elif composite_score < -0.15:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        return {
            "symbol": symbol,
            "composite_alpha_score": round(composite_score, 4),
            "kronos_alpha_score": base_alpha["alpha_score"],
            "dom_imbalance_modifier": round(dom_modifier, 4),
            "spread_penalty": spread_penalty,
            "confidence": base_alpha["confidence"],
            "direction": direction,
            "predicted_return_pct": base_alpha["predicted_return_pct"],
        }

    # ── Task 2: MT5 Volatility Input for Risk & Lot Sizing ───────────────────

    def get_mt5_volatility_risk(
        self,
        symbol: str,
        mt5_rates: List[Any],
        account_balance: float = 10000.0,
        risk_per_trade_pct: float = 1.0,
    ) -> Dict[str, Any]:
        """Calculate Kronos volatility input and recommended MT5 lot size."""
        ohlcv = self.normalize_mt5_rates(mt5_rates)
        vol_input = self.integrator.get_volatility_input(symbol, ohlcv)

        pred_vol = vol_input["predicted_volatility_5d"]
        risk_adj = vol_input["risk_adjustment_factor"]

        # Calculate dynamic MT5 position size (lot sizing) based on predicted volatility
        dollar_risk = account_balance * (risk_per_trade_pct / 100.0) * risk_adj
        last_close = ohlcv[-1]["close"] if ohlcv else 1.0

        # Estimated stop loss distance in price based on predicted volatility
        est_sl_distance = last_close * (pred_vol / 2.0)
        est_sl_distance = max(0.0005, est_sl_distance)

        # Standard Forex 1 Lot = 100,000 units
        recommended_lots = (dollar_risk / est_sl_distance) / 100000.0
        recommended_lots = round(max(0.01, min(10.0, recommended_lots)), 2)

        return {
            "symbol": symbol,
            "predicted_volatility_5d": pred_vol,
            "volatility_regime": vol_input["volatility_regime"],
            "risk_adjustment_factor": risk_adj,
            "dollar_risk_allowed": round(dollar_risk, 2),
            "estimated_sl_distance": round(est_sl_distance, 5),
            "recommended_lots": recommended_lots,
        }

    def get_mt5_multi_dim_forecast(
        self, symbol: str, mt5_rates: List[Any], pred_len: int = 24,
        sample_count: int = 10, temperature: float = 1.0, top_p: float = 0.9,
    ) -> Dict[str, Any]:
        """Multi-dimensional OHLCVA forecast from MT5 rates — paper §3 hierarchical BSQ.

        Returns full 6-dim K-line prediction with per-bar OHLCVA mean/std,
        trajectory_count, confidence, and advanced vol term structure.
        """
        ohlcv = self.normalize_mt5_rates(mt5_rates)
        return self.integrator.get_multi_dim_forecast(
            symbol, ohlcv, pred_len=pred_len, sample_count=sample_count,
            temperature=temperature, top_p=top_p,
        )

    def get_mt5_advanced_volatility(
        self, symbol: str, mt5_rates: List[Any], horizon_bars: int = 24,
    ) -> Dict[str, Any]:
        """Advanced volatility term structure (5d/10d/20d) + regime + risk factor for RiskManager.

        Paper §4.2: 9% lower MAE in vol forecasting via Kronos vs baselines.
        """
        ohlcv = self.normalize_mt5_rates(mt5_rates)
        bundle = self.integrator.get_volatility_term_structure(symbol, ohlcv, horizon_bars=horizon_bars)
        # also expose MT5-aware lot sizing via term structure
        last_close = ohlcv[-1]["close"] if ohlcv else 1.0
        vol_5d = bundle["volatility_5d"]
        bundle["mt5_last_close"] = round(last_close, 5)
        bundle["mt5_spread_aware"] = True
        bundle["execution_authority"] = False
        return bundle

    def get_mt5_risk_adjusted_sizing(
        self, symbol: str, mt5_rates: List[Any],
        account_balance: float = 10000.0, risk_per_trade_pct: float = 1.0,
        entry_price: Optional[float] = None, stop_loss: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Risk-adjusted lot sizing wired to Advanced Volatility + MT5 rates."""
        ohlcv = self.normalize_mt5_rates(mt5_rates)
        return self.integrator.get_risk_adjusted_sizing(
            symbol, ohlcv, account_balance=account_balance,
            risk_per_trade_pct=risk_per_trade_pct,
            entry_price=entry_price, stop_loss=stop_loss,
        )

    # ── Task 3: Synthetic MT5 Rates Data Generator ───────────────────────────

    def generate_synthetic_mt5_rates(
        self,
        symbol: str = "EURUSD",
        start_price: float = 1.0850,
        length: int = 100,
        regime: str = "bull",
        seed: int = 42,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic MT5 rates dictionaries matching MT5 copy_rates_from_pos schema."""
        raw_candles = self.predictor.generate_synthetic_regime(
            symbol=symbol, start_price=start_price, length=length, regime=regime, seed=seed
        )

        mt5_rates = []
        base_time = int(1700000000)
        rng = random.Random(int(seed))

        for c in raw_candles:
            spread_ticks = int(rng.randint(8, 25))
            mt5_rates.append({
                "time": base_time + (c["bar"] * 3600),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "tick_volume": int(c["volume"]),
                "spread": spread_ticks,
                "real_volume": int(c["volume"] * 1.5),
            })

        return mt5_rates


# ── Task 4: Interactive MT5 RL Market Simulator ─────────────────────────────

class MT5KronosRLSimulator:
    """Gym-compatible MT5 market simulator including tick volume, spread, and DOM depth.

    Action space mirrors ``KronosRLMarketSimulator``: BUY/SELL entry plus
    HOLD / TIGHTEN_SL / TRAIL / PARTIAL_CLOSE / FULL_EXIT management actions.
    """

    def __init__(
        self,
        symbol: str = "EURUSD",
        initial_balance: float = 10000.0,
        regime: str = "volatile",
        max_steps: int = 100,
        *,
        seed: int = 42,
    ):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.regime = regime
        self.max_steps = max_steps
        self.seed = int(seed)
        self.adapter = KronosMT5Adapter(force_heuristic=True)
        self.reset()

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        if seed is not None:
            self.seed = int(seed)
        self.balance = self.initial_balance
        self.position = 0.0
        self.position_size = 0.0
        self.entry_price = 0.0
        self.stop_loss: Optional[float] = None
        self.trail_active = False
        self.trail_pct = 0.008
        self.current_step = 0
        self.total_pnl = 0.0

        self.rates = self.adapter.generate_synthetic_mt5_rates(
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
        obs = self.reset(seed=seed)
        side = 1.0 if float(direction) >= 0 else -1.0
        price = float(obs["close"])
        self.position = side
        self.position_size = 1.0
        self.entry_price = price
        self.stop_loss = price * (1.0 - 0.03 * side)
        self.trail_active = False
        return self._get_observation()

    def _get_observation(self) -> Dict[str, Any]:
        r = self.rates[self.current_step]
        rng = random.Random(r["time"])
        imbalance = rng.uniform(-0.8, 0.8)

        return {
            "step": self.current_step,
            "time": r["time"],
            "close": r["close"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "tick_volume": r["tick_volume"],
            "spread": r["spread"],
            "dom": {
                "dom_depth": 10,
                "spread": r["spread"],
                "imbalance_rate": round(imbalance, 4),
            },
            "balance": self.balance,
            "position": self.position,
            "position_size": self.position_size,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "trail_active": self.trail_active,
            "total_pnl": self.total_pnl,
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

    def _stop_hit(self, bar: Dict[str, Any]) -> bool:
        if self.stop_loss is None or self.position == 0.0:
            return False
        side = self._direction()
        if side > 0:
            return float(bar["low"]) <= float(self.stop_loss)
        return float(bar["high"]) >= float(self.stop_loss)

    def _close_fraction(self, price: float, fraction: float, spread_cost: float = 0.0) -> float:
        if self.position == 0.0 or self.entry_price <= 0 or fraction <= 0:
            return 0.0
        fraction = min(1.0, max(0.0, float(fraction)))
        side = self._direction()
        size = abs(self.position) * fraction
        pnl = side * (price - self.entry_price) * 10000.0 * size - abs(spread_cost) * 10000.0 * size
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

    def observation_vector(self, obs: Optional[Dict[str, Any]] = None) -> List[float]:
        obs = obs or self._get_observation()
        close = float(obs.get("close") or 0.0)
        open_p = float(obs.get("open") or close)
        high = float(obs.get("high") or close)
        low = float(obs.get("low") or close)
        entry = float(obs.get("entry_price") or 0.0)
        stop = obs.get("stop_loss")
        ret = ((close - open_p) / open_p) if open_p else 0.0
        rng = ((high - low) / open_p) if open_p else 0.0
        spread = float(obs.get("spread") or 0.0) / 100.0
        imbalance = float((obs.get("dom") or {}).get("imbalance_rate") or 0.0)
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
            spread,
            imbalance,
        ]

    def step(self, action: str) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        action = str(action or "HOLD").upper()
        curr = self.rates[self.current_step]
        curr_price = float(curr["close"])
        spread_cost = float(curr["spread"]) * 0.00001

        self.current_step += 1
        nxt = self.rates[self.current_step]
        next_price = float(nxt["close"])
        price_change = next_price - curr_price

        reward = 0.0
        info_extra: Dict[str, Any] = {}

        if action == "BUY":
            if self.position == 0.0:
                self.position = 1.0
                self.position_size = 1.0
                self.entry_price = curr_price
                self.stop_loss = curr_price * 0.99
                self.trail_active = False
                reward = (price_change - spread_cost) * 10000.0
            else:
                reward = price_change * 10000.0 * abs(self.position)

        elif action == "SELL":
            if self.position == 0.0:
                self.position = -1.0
                self.position_size = 1.0
                self.entry_price = curr_price
                self.stop_loss = curr_price * 1.01
                self.trail_active = False
                reward = (-price_change - spread_cost) * 10000.0
            else:
                reward = -price_change * 10000.0 * abs(self.position)

        elif action == "TIGHTEN_SL":
            if self.position != 0.0:
                side = self._direction()
                if self.stop_loss is None:
                    self.stop_loss = curr_price * (1.0 - 0.01 * side)
                else:
                    self.stop_loss = float(self.stop_loss) + 0.5 * (curr_price - float(self.stop_loss))
                reward = self.position * price_change * 10000.0

        elif action == "TRAIL":
            if self.position != 0.0:
                self.trail_active = True
                self._apply_trail(curr_price)
                reward = self.position * price_change * 10000.0

        elif action == "PARTIAL_CLOSE":
            if self.position != 0.0:
                reward = self._close_fraction(curr_price, 0.5, spread_cost=spread_cost)

        elif action == "FULL_EXIT":
            if self.position != 0.0:
                reward = self._close_fraction(curr_price, 1.0, spread_cost=spread_cost)
                info_extra["exited"] = True

        else:  # HOLD
            if self.position != 0.0:
                reward = self.position * price_change * 10000.0

        if self.position != 0.0 and action not in {"PARTIAL_CLOSE", "FULL_EXIT"}:
            self._apply_trail(next_price)
            if self._stop_hit(nxt):
                exit_px = float(self.stop_loss) if self.stop_loss is not None else next_price
                reward += self._close_fraction(exit_px, 1.0)
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
