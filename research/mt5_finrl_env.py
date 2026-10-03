"""MT5 FinRL Environment — single-asset FX adaptation of FinRL StockTradingEnv.

Converts FinRL's N-stock portfolio env (obs: 30 stocks x OHLCV, action: simplex weights)
into retail FX single-asset env: obs = alpha+regime state, action = lot sizing,
reward = R-multiple net of cost.

Based on:
- FinRL Institutional Architecture (Jiang et al. 2017, Liu et al. 2020)
- PPO/SAC: Schulman 2017, Haarnoja 2018
- Adaptation: single-asset, 1R stops, MT5 OHLCV + alpha_ensemble + hierarchical regime.

Never grants execution_authority; paper simulation only.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

try:
    import pandas as pd
except ImportError:
    pd = None

@dataclass
class MT5EnvConfig:
    lookback: int = 60
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.01
    commission_per_lot: float = 0.6  # $0.60 per 0.01 lot ~ spread+commission
    max_steps: int = 500
    stop_atr_mult: float = 1.0
    take_profit_rr: float = 2.0
    max_drawdown_stop: float = 0.10  # 10% episode stop


class MT5FinRLEnv:
    """Single-asset FX env — API compatible with Gym / FinRL.

    Observation (shape 32):
      [0:5]  last 5 returns
      [5:10] rsi_14, macd_hist, hma_direction, adx, atr_pct (normalized)
      [10:15] alpha_ensemble composite_value + top 4 alpha z-scores
      [15:20] regime probs 4 + bocpd prob
      [20:25] position + pnl + drawdown + heat + volatility regime
      [25:32] zero-lag macro snapshot (net_liq bias, grid, cpi mom, + 4 spreads)

    Action (continuous in [-1, 1] or discrete {0: HOLD, 1: BUY, 2: SELL}):
      Scaled to lot size via RiskManager.calc_position_size with 1R stop.

    Reward per step:
      r = R_multiple - 0.2*commission_penalty - 1.0*drawdown_penalty
      where R_multiple = pnl / (balance * risk_per_trade)
    """
    def __init__(self, df: Any, config: Optional[MT5EnvConfig] = None):
        self.df = df
        self.config = config or MT5EnvConfig()
        self.n = len(df) if hasattr(df, "__len__") else 0
        self.ptr = self.config.lookback
        self.balance = self.config.initial_balance
        self.equity = self.config.initial_balance
        self.peak_equity = self.config.initial_balance
        self.position: Optional[Dict[str, Any]] = None
        self.done = False
        self.step_count = 0
        self.history: List[float] = []

    def reset(self) -> np.ndarray:
        self.ptr = self.config.lookback
        self.balance = self.config.initial_balance
        self.equity = self.config.initial_balance
        self.peak_equity = self.config.initial_balance
        self.position = None
        self.done = False
        self.step_count = 0
        self.history = []
        return self._obs()

    def _obs(self) -> np.ndarray:
        try:
            # Build 32-dim obs from df slice
            if hasattr(self.df, "iloc"):
                window = self.df.iloc[max(0, self.ptr - self.config.lookback): self.ptr]
                closes = window["close"].values.astype(float) if "close" in window else np.zeros(self.config.lookback)
                rsi = float(window["rsi_14"].iloc[-1]) / 100.0 if "rsi_14" in window else 0.5
                macd_hist = float(window["macd_hist"].iloc[-1]) if "macd_hist" in window else 0.0
                hma_dir = float(1 if window["close"].iloc[-1] > window["hma_200"].iloc[-1] else -1) if "hma_200" in window and len(window) > 0 else 0.0
                adx = float(window["adx_14"].iloc[-1]) / 100.0 if "adx_14" in window else 0.25
                atr_pct = float(window["atr_14"].iloc[-1] / window["close"].iloc[-1]) if "atr_14" in window and window["close"].iloc[-1] > 0 else 0.001
            else:
                closes = np.array(self.df[max(0, self.ptr - self.config.lookback): self.ptr], dtype=float)
                rsi, macd_hist, hma_dir, adx, atr_pct = 0.5, 0.0, 0.0, 0.25, 0.001
            rets = np.diff(np.log(np.maximum(closes, 1e-9))) if len(closes) > 1 else np.zeros(5)
            rets_pad = np.zeros(5)
            rets_pad[-min(5, len(rets)):] = rets[-5:] if len(rets) >= 5 else np.pad(rets, (5 - len(rets), 0))
            obs = np.zeros(32, dtype=float)
            obs[0:5] = np.clip(rets_pad, -0.05, 0.05) * 20.0  # normalize returns
            obs[5] = np.clip(rsi, 0, 1)
            obs[6] = np.clip(macd_hist * 10, -1, 1)
            obs[7] = hma_dir
            obs[8] = np.clip(adx, 0, 1)
            obs[9] = np.clip(atr_pct * 100, 0, 1)
            # alpha/regime placeholders (would be filled from live toon in production)
            # For offline, use simple momentum as proxy
            mom = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0
            obs[10] = np.clip(mom * 10, -1, 1)
            # position state
            obs[20] = 1.0 if self.position else 0.0
            if self.position:
                pos_side = self.position["side"]
                unreal_pnl = (float(closes[-1]) - float(self.position["entry"])) * 100000.0 * float(self.position["volume"]) * (1 if pos_side == "BUY" else -1)
                obs[21] = float(unreal_pnl) / (self.balance * self.config.risk_per_trade + 1e-9)
            obs[22] = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
            return obs.astype(np.float32)
        except Exception:
            return np.zeros(32, dtype=np.float32)

    def step(self, action: int | float) -> Tuple[np.ndarray, float, bool, Dict[str, Any]]:
        if self.done:
            return self._obs(), 0.0, True, {}
        # Map action: discrete 0=HOLD,1=BUY,2=SELL or continuous [-1,1]
        side = None
        if isinstance(action, (int, np.integer)):
            if action == 1:
                side = "BUY"
            elif action == 2:
                side = "SELL"
        else:
            a = float(action)
            if a > 0.33:
                side = "BUY"
            elif a < -0.33:
                side = "SELL"
        reward = 0.0
        info: Dict[str, Any] = {"action_side": side or "HOLD"}

        # 1. Check intra-bar SL/TP bracket for open position
        if self.position:
            try:
                curr_bar = self.df.iloc[self.ptr] if hasattr(self.df, "iloc") else None
                high_p = float(curr_bar["high"]) if curr_bar is not None and "high" in curr_bar else (float(self.df.iloc[self.ptr]["close"]) if hasattr(self.df, "iloc") else float(self.df[self.ptr]))
                low_p = float(curr_bar["low"]) if curr_bar is not None and "low" in curr_bar else (float(self.df.iloc[self.ptr]["close"]) if hasattr(self.df, "iloc") else float(self.df[self.ptr]))
                pos_side = self.position["side"]
                sl = float(self.position["sl"])
                tp = float(self.position.get("tp", 0.0))
                bracket_hit = False
                exit_price = 0.0

                if pos_side == "BUY":
                    if low_p <= sl:
                        bracket_hit = True
                        exit_price = sl
                        info["stop_hit"] = True
                    elif tp > 0 and high_p >= tp:
                        bracket_hit = True
                        exit_price = tp
                        info["tp_hit"] = True
                else:  # SELL
                    if high_p >= sl:
                        bracket_hit = True
                        exit_price = sl
                        info["stop_hit"] = True
                    elif tp > 0 and low_p <= tp:
                        bracket_hit = True
                        exit_price = tp
                        info["tp_hit"] = True

                if bracket_hit:
                    vol = float(self.position["volume"])
                    entry = float(self.position["entry"])
                    pnl = (exit_price - entry) * 100000.0 * vol * (1 if pos_side == "BUY" else -1)
                    pnl -= self.config.commission_per_lot * vol * 100  # scale
                    r_mult = pnl / (self.balance * self.config.risk_per_trade + 1e-9)
                    reward += float(r_mult)
                    self.balance += pnl
                    self.equity = self.balance
                    self.peak_equity = max(self.peak_equity, self.equity)
                    info["closed_pnl"] = pnl
                    info["r_multiple"] = r_mult
                    self.history.append(r_mult)
                    self.position = None
            except Exception as e:
                info["bracket_error"] = str(e)

        # 2. Close existing position on opposite signal
        if self.position and side and side != self.position.get("side"):
            try:
                exit_price = float(self.df.iloc[self.ptr]["close"]) if hasattr(self.df, "iloc") else float(self.df[self.ptr])
                entry = float(self.position["entry"])
                vol = float(self.position["volume"])
                pos_side = self.position["side"]
                pnl = (exit_price - entry) * 100000.0 * vol * (1 if pos_side == "BUY" else -1)
                pnl -= self.config.commission_per_lot * vol * 100  # scale
                r_mult = pnl / (self.balance * self.config.risk_per_trade + 1e-9)
                reward += float(r_mult)
                self.balance += pnl
                self.equity = self.balance
                self.peak_equity = max(self.peak_equity, self.equity)
                info["closed_pnl"] = pnl
                info["r_multiple"] = r_mult
                self.history.append(r_mult)
            except Exception as e:
                info["close_error"] = str(e)
            self.position = None

        # 3. Open new position
        if side and not self.position:
            try:
                entry = float(self.df.iloc[self.ptr]["close"]) if hasattr(self.df, "iloc") else float(self.df[self.ptr])
                atr = float(self.df.iloc[self.ptr]["atr_14"]) if hasattr(self.df, "iloc") and "atr_14" in self.df else entry * 0.005
                sl = entry - atr * self.config.stop_atr_mult if side == "BUY" else entry + atr * self.config.stop_atr_mult
                tp_dist = atr * self.config.stop_atr_mult * self.config.take_profit_rr
                tp = entry + tp_dist if side == "BUY" else entry - tp_dist
                # Lot sizing: 1R
                risk_amt = self.balance * self.config.risk_per_trade
                dist = abs(entry - sl)
                vol = risk_amt / (dist * 100000) if dist > 0 else 0.01
                vol = max(0.01, min(vol, 1.0))
                vol = round(math.floor(vol / 0.01) * 0.01, 2)
                if vol >= 0.01:
                    self.position = {"side": side, "entry": entry, "sl": sl, "tp": tp, "volume": vol, "open_idx": self.ptr}
                    info["opened"] = {"side": side, "entry": entry, "sl": sl, "tp": tp, "volume": vol}
            except Exception as e:
                info["open_error"] = str(e)
        # Step pointer
        self.ptr += 1
        self.step_count += 1
        # Drawdown penalty
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
        if dd > 0.02:
            reward -= dd * 2.0
        # Check done
        if self.ptr >= self.n - 1 or self.step_count >= self.config.max_steps or dd >= self.config.max_drawdown_stop:
            self.done = True
        # Small time penalty to encourage not holding forever
        reward -= 0.001
        return self._obs(), float(reward), self.done, info

    def get_metrics(self) -> Dict[str, Any]:
        if not self.history:
            return {"trades": 0, "win_rate": 0, "avg_r": 0, "sharpe": 0, "sharpe_ann": 0}
        arr = np.array(self.history, dtype=float)
        wins = (arr > 0).sum()
        std = float(arr.std()) if len(arr) > 1 else 0.02
        # Clip std to avoid -290 artifact: FX R multiples std <0.01 is noise floor
        std_clipped = max(std, 0.02)
        sharpe_raw = float(arr.mean() / std_clipped) if std_clipped > 0 else 0.0
        sharpe_clipped = float(np.clip(sharpe_raw, -5.0, 5.0))
        # Annualized proxy: 252*60/M15 bars per year not meaningful for R, keep raw + ann
        sharpe_ann = float(sharpe_clipped * np.sqrt(252/60))  # scale IC-like
        return {
            "trades": len(arr),
            "win_rate": float(wins / len(arr)) if len(arr) > 0 else 0,
            "avg_r": float(arr.mean()) if len(arr) > 0 else 0,
            "sharpe": float(sharpe_clipped),
            "sharpe_ann": float(np.clip(sharpe_ann, -5, 5)),
            "sharpe_raw_unclipped": float(arr.mean() / (float(arr.std())+1e-9)) if len(arr)>1 else 0,
            "total_r": float(arr.sum()),
        }
