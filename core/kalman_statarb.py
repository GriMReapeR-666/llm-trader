"""Module 05: Dynamic Kalman Filter Statistical Arbitrage Alpha Engine.

Implements online recursive state-space cointegration tracking:
    Hidden State Vector: theta_t = [alpha_t, beta_t]^T
    Observation Equation: y_t = alpha_t + beta_t * x_t + e_t
    Innovation Residual: e_t = y_t - (alpha_t + beta_t * x_t)
    Kalman Gain: K_t = P_{t|t-1} F_t^T / (F_t P_{t|t-1} F_t^T + V_t)
    State Update: theta_t = theta_{t|t-1} + K_t * e_t

With continuous Ornstein-Uhlenbeck mean-reversion modeling and analytical half-life:
    tau_1/2 = -ln(2) / theta
And 4-stage execution state machine:
    - Long Entry: Z < -2.0
    - Short Entry: Z > +2.0
    - Target Exit: |Z| <= 0.20
    - Invalidation Stop: |Z| >= 3.0
    - Temporal Decay Exit: holding_bars > 2 * tau_1/2
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np
import pandas as pd


@dataclass
class KalmanState:
    alpha: float
    beta: float
    innovation_error: float
    innovation_variance: float
    spread_z_score: float
    ou_half_life_bars: float
    state_machine_signal: str  # "BUY_SPREAD", "SELL_SPREAD", "EXIT_TARGET", "STOP_LOSS", "EXIT_TIME_STOP", "HOLD", "FLAT"


def estimate_spread_mean_reversion_half_life(
    spread_residuals: Sequence[float],
    min_lookback: int = 20,
) -> float:
    """Estimates Ornstein-Uhlenbeck (OU) mean-reversion half-life:
    Delta e_t = theta * (mu - e_{t-1}) Delta t + epsilon_t
    tau_1/2 = -ln(2) / theta
    """
    arr = np.asarray(spread_residuals, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < min_lookback:
        return 10.0  # Default fallback half-life

    y = arr[-min_lookback:]
    y_lag = y[:-1]
    dy = y[1:] - y_lag
    mean_lag = np.mean(y_lag)
    var_lag = np.sum((y_lag - mean_lag) ** 2)

    if var_lag == 0:
        return 60.0

    theta = np.sum((y_lag - mean_lag) * (dy - np.mean(dy))) / var_lag
    if theta >= 0.0:
        # Non-mean reverting or explosive series
        return 60.0

    half_life = -math.log(2.0) / theta
    return float(max(1.0, min(60.0, half_life)))


def evaluate_statarb_state_machine(
    z_score: float,
    current_position: int,
    holding_bars: int,
    half_life_bars: float,
    z_entry_threshold: float = 2.0,
    z_exit_threshold: float = 0.20,
    z_stop_threshold: float = 3.0,
) -> Tuple[str, int]:
    """Evaluates the 4-stage Z-score and Ornstein-Uhlenbeck state machine:
    Returns (signal_name, updated_position).
    """
    z = float(z_score)
    pos = int(current_position)
    bars = int(holding_bars)
    hl = float(half_life_bars)

    if pos == 0:
        if z <= -z_entry_threshold:
            return "BUY_SPREAD", 1
        elif z >= z_entry_threshold:
            return "SELL_SPREAD", -1
        else:
            return "FLAT", 0
    elif pos == 1:  # Long spread (Long Y, Short X) - entered at Z <= -2.0
        if z >= -z_exit_threshold:
            return "EXIT_TARGET", 0
        elif z <= -z_stop_threshold:
            return "STOP_LOSS", 0
        elif bars > 2 * hl:
            return "EXIT_TIME_STOP", 0
        else:
            return "HOLD", 1
    else:  # pos == -1, Short spread (Short Y, Long X) - entered at Z >= +2.0
        if z <= z_exit_threshold:
            return "EXIT_TARGET", 0
        elif z >= z_stop_threshold:
            return "STOP_LOSS", 0
        elif bars > 2 * hl:
            return "EXIT_TIME_STOP", 0
        else:
            return "HOLD", -1



class DynamicKalmanStatArbEngine:
    """Online 2-State Kalman Filter StatArb Alpha Engine."""

    def __init__(
        self,
        delta: float = 1e-4,          # State transition covariance discount (W)
        measurement_var: float = 1e-2, # Measurement noise variance (V)
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.20,
        z_stop_threshold: float = 3.0,
        max_half_life_bars: int = 50,
    ):
        self.delta = float(delta)
        self.measurement_var = float(measurement_var)
        self.z_entry_threshold = float(z_entry_threshold)
        self.z_exit_threshold = float(z_exit_threshold)
        self.z_stop_threshold = float(z_stop_threshold)
        self.max_half_life_bars = int(max_half_life_bars)

        # State vector: [alpha, beta]^T
        self.theta = np.zeros(2)
        # State covariance matrix R
        self.R = np.eye(2) * 1.0
        # Transition covariance matrix W
        self.W = np.eye(2) * self.delta

        self.spread_history: List[float] = []
        self.current_position: int = 0  # +1: Long spread (Buy Y, Sell X), -1: Short spread, 0: Flat
        self.holding_bars: int = 0

    def update_state_space(self, y: float, x: float) -> KalmanState:
        """Process a new pair observation (y_t, x_t) and return updated state and signals."""
        y_val = float(y)
        x_val = float(x)

        # 1. State Prediction
        theta_pred = self.theta
        R_pred = self.R + self.W

        # 2. Measurement Vector H = [1, x]
        H = np.array([1.0, x_val])

        # 3. Innovation Error and Variance
        y_pred = float(np.dot(H, theta_pred))
        e_t = y_val - y_pred
        Q_t = float(np.dot(H, np.dot(R_pred, H.T))) + self.measurement_var

        # 4. Kalman Gain K_t
        K_t = np.dot(R_pred, H.T) / max(1e-12, Q_t)

        # 5. State and Covariance Update
        self.theta = theta_pred + K_t * e_t
        self.R = np.dot(np.eye(2) - np.outer(K_t, H), R_pred)

        self.spread_history.append(e_t)
        if len(self.spread_history) > 300:
            self.spread_history = self.spread_history[-300:]

        # 6. Standardized Z-Score
        spread_std = math.sqrt(max(1e-8, Q_t))
        z_score = e_t / spread_std if spread_std > 0 else 0.0

        # 7. Ornstein-Uhlenbeck Half-Life Estimation
        half_life = estimate_spread_mean_reversion_half_life(self.spread_history)

        # 8. State Machine Evaluation
        signal, new_pos = evaluate_statarb_state_machine(
            z_score=z_score,
            current_position=self.current_position,
            holding_bars=self.holding_bars,
            half_life_bars=half_life,
            z_entry_threshold=self.z_entry_threshold,
            z_exit_threshold=self.z_exit_threshold,
            z_stop_threshold=self.z_stop_threshold,
        )

        if new_pos != 0 and new_pos == self.current_position:
            self.holding_bars += 1
        elif new_pos != self.current_position:
            self.holding_bars = 0
            self.current_position = new_pos

        return KalmanState(
            alpha=round(float(self.theta[0]), 4),
            beta=round(float(self.theta[1]), 4),
            innovation_error=round(float(e_t), 4),
            innovation_variance=round(float(Q_t), 4),
            spread_z_score=round(float(z_score), 3),
            ou_half_life_bars=round(float(half_life), 1),
            state_machine_signal=signal,
        )

    # Backwards-compatible alias
    update = update_state_space

    def execute_historical_backtest(
        self,
        df_pairs: pd.DataFrame,
        initial_capital: float = 100_000.0,
        risk_per_trade_pct: float = 0.01,
        transaction_cost_bps: float = 2.0,
    ) -> Dict[str, Any]:
        """Executes full historical event-driven pairs backtest with 1R risk sizing."""
        df = df_pairs.copy().dropna()
        if "y" not in df.columns or "x" not in df.columns:
            raise ValueError("DataFrame must contain 'y' and 'x' price columns")

        y_prices = df["y"].values
        x_prices = df["x"].values
        n = len(df)

        equity = float(initial_capital)
        equity_curve = [equity]
        trade_pnls = []
        r_multiples = []

        pos = 0
        entry_idx = 0
        entry_y = 0.0
        entry_x = 0.0
        entry_beta = 1.0
        units_y = 0.0
        units_x = 0.0
        holding_bars = 0

        for t in range(n):
            state = self.update_state_space(y_prices[t], x_prices[t])
            sig = state.state_machine_signal

            if pos == 0:
                if sig in ("BUY_SPREAD", "SELL_SPREAD"):
                    pos = 1 if sig == "BUY_SPREAD" else -1
                    entry_idx = t
                    entry_y = y_prices[t]
                    entry_x = x_prices[t]
                    entry_beta = state.beta
                    holding_bars = 0

                    # 1R risk sizing
                    dollar_risk = equity * risk_per_trade_pct
                    spread_vol = max(0.01 * entry_y, math.sqrt(max(1e-6, state.innovation_variance)))
                    units_y = dollar_risk / (spread_vol * self.z_stop_threshold) if spread_vol > 0 else 1.0
                    units_x = units_y * abs(entry_beta)
            else:
                holding_bars += 1
                if sig in ("EXIT_TARGET", "STOP_LOSS", "EXIT_TIME_STOP"):
                    # Calculate realized PnL
                    pnl_y = pos * (y_prices[t] - entry_y) * units_y
                    pnl_x = -pos * (x_prices[t] - entry_x) * units_x
                    friction = (units_y * y_prices[t] + units_x * x_prices[t]) * (transaction_cost_bps / 10000.0)
                    gross_pnl = pnl_y + pnl_x
                    net_pnl = gross_pnl - friction

                    dollar_risk = equity * risk_per_trade_pct
                    r_mult = net_pnl / dollar_risk if dollar_risk > 0 else 0.0

                    equity += net_pnl
                    trade_pnls.append(net_pnl)
                    r_multiples.append(r_mult)
                    pos = 0
                    holding_bars = 0

            equity_curve.append(equity)

        total_trades = len(r_multiples)
        wins = [r for r in r_multiples if r > 0]
        losses = [r for r in r_multiples if r < 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        avg_win_r = float(np.mean(wins)) if wins else 0.0
        avg_loss_r = abs(float(np.mean(losses))) if losses else 0.0
        payoff_ratio = (avg_win_r / avg_loss_r) if avg_loss_r > 0 else 0.0
        ev_r = (win_rate * avg_win_r) - ((1.0 - win_rate) * avg_loss_r)
        sqn = (math.sqrt(total_trades) * float(np.mean(r_multiples)) / float(np.std(r_multiples, ddof=1))) if total_trades > 1 and np.std(r_multiples, ddof=1) > 0 else 0.0

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
            "payoff_ratio": round(payoff_ratio, 4),
            "expected_value_r": round(ev_r, 4),
            "sqn": round(sqn, 2),
            "final_equity": round(equity, 2),
            "total_pnl": round(equity - initial_capital, 2),
        }


# Canonical alias
DynamicKalmanStatArb = DynamicKalmanStatArbEngine
