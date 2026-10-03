"""Module 02: Path-Dependent Triple-Barrier Target Labeling, Meta-Labeling & Inefficiency Signals.

Implements:
1. Triple-Barrier Path-Dependent Target Labeling (Author-C):
   - Upper Horizontal Barrier: +k * ATR Take-Profit Target
   - Lower Horizontal Barrier: -k * ATR Stop-Loss Invalidation
   - Vertical Barrier: Max holding period expiration (N bars)
2. Meta-Labeling Secondary Probability Classifier:
   - Predicts P(Hit Upper TP | Primary Model Signal) to optimize position sizing and filter low-conviction trades.
3. Free Quantitative Inefficiency Signals:
   - Order Flow Imbalance (OFI): OFI_t = Delta Q_bid,t - Delta Q_ask,t
   - CFTC Commitment of Traders (COT) Commercial Index (3-year rolling percentile)
   - SEC EDGAR Operating Accruals Anomaly: Accruals = (Net Income - Operating Cash Flow) / Total Assets
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


@dataclass
class BarrierEvent:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    touch_type: str  # "UPPER_TP", "LOWER_SL", "TIME_BARRIER"
    realized_r: float
    label: int  # 1 for profit touch (UPPER_TP), 0 for loss/time expiration


class TripleBarrierPathDependentLabeler:
    """Applies path-dependent triple-barrier labeling to identified trade events."""

    @staticmethod
    def apply(
        prices: Union[pd.Series, np.ndarray, List[float]],
        events: List[int],
        upper_r: float = 1.5,
        lower_r: float = 1.0,
        vertical_barrier_bars: int = 24,
        volatility: Optional[Union[pd.Series, np.ndarray, List[float]]] = None,
        sides: Optional[Sequence[Union[int, str]]] = None,
    ) -> List[BarrierEvent]:
        """Apply dynamic volatility triple-barrier labeling.

        Args:
            prices: 1D sequence of asset close prices.
            events: Bar indices where primary entry signals fired.
            upper_r: Profit barrier in units of volatility/R-multiple, measured in
                the direction of the trade.
            lower_r: Loss barrier in units of volatility/R-multiple, measured
                against the direction of the trade.
            vertical_barrier_bars: Max holding horizon bars before time expiration.
            volatility: Optional rolling ATR or sigma series for dynamic barriers.
            sides: Per-event trade direction, aligned with `events`: "long"/"buy"/+1
                or "short"/"sell"/-1. Defaults to all-long (backwards compatible).
        """
        p = np.asarray(prices, dtype=float)
        v = np.asarray(volatility, dtype=float) if volatility is not None else np.ones_like(p) * (0.01 * p)
        n = len(p)
        results: List[BarrierEvent] = []

        def _direction(raw) -> int:
            # Normalise one side entry to +1 (long) or -1 (short).
            if isinstance(raw, str):
                s = raw.strip().lower()
                if s in ("short", "sell", "s", "-1"):
                    return -1
                return 1
            try:
                return -1 if float(raw) < 0 else 1
            except (TypeError, ValueError):
                return 1

        for pos, idx in enumerate(events):
            if idx >= n - 1:
                continue
            entry_price = p[idx]
            sigma = v[idx] if idx < len(v) and v[idx] > 0 else entry_price * 0.01
            d = _direction(sides[pos]) if sides is not None and pos < len(sides) else 1

            # Profit/loss barriers are defined IN THE DIRECTION OF THE TRADE, so
            # touch_type keeps its economic meaning for shorts too: UPPER_TP is
            # always "the profit barrier was touched first".
            profit_barrier = entry_price + d * (upper_r * sigma)
            loss_barrier = entry_price - d * (lower_r * sigma)
            max_idx = min(idx + vertical_barrier_bars, n - 1)

            touch_type = "TIME_BARRIER"
            exit_idx = max_idx
            exit_price = p[max_idx]

            for cur_idx in range(idx + 1, max_idx + 1):
                cur_price = p[cur_idx]
                touched_profit = (cur_price >= profit_barrier) if d > 0 else (cur_price <= profit_barrier)
                touched_loss = (cur_price <= loss_barrier) if d > 0 else (cur_price >= loss_barrier)
                if touched_profit:
                    touch_type = "UPPER_TP"
                    exit_idx = cur_idx
                    exit_price = cur_price
                    break
                elif touched_loss:
                    touch_type = "LOWER_SL"
                    exit_idx = cur_idx
                    exit_price = cur_price
                    break

            r_multiple = d * (exit_price - entry_price) / sigma if sigma > 0 else 0.0
            label = 1 if touch_type == "UPPER_TP" else 0

            results.append(BarrierEvent(
                entry_index=idx,
                exit_index=exit_idx,
                entry_price=entry_price,
                exit_price=exit_price,
                touch_type=touch_type,
                realized_r=round(float(r_multiple), 3),
                label=label,
            ))

        return results


def apply_triple_barrier(
    prices: Union[pd.Series, np.ndarray, List[float]],
    events: List[int],
    upper_r: float = 1.5,
    lower_r: float = 1.0,
    vertical_barrier_bars: int = 24,
    volatility: Optional[Union[pd.Series, np.ndarray, List[float]]] = None,
    sides: Optional[Sequence[Union[int, str]]] = None,
) -> List[BarrierEvent]:
    """Functional facade for TripleBarrierPathDependentLabeler.apply."""
    return TripleBarrierPathDependentLabeler.apply(
        prices=prices,
        events=events,
        upper_r=upper_r,
        lower_r=lower_r,
        vertical_barrier_bars=vertical_barrier_bars,
        volatility=volatility,
        sides=sides,
    )


class MetaLabelingProbabilityClassifier:
    """Secondary meta-classifier predicting probability of trade success P(win=1 | setup)."""

    def __init__(self, confidence_threshold: float = 0.60, learning_rate: float = 0.05, max_iterations: int = 200):
        self.confidence_threshold = float(confidence_threshold)
        self.learning_rate = float(learning_rate)
        self.max_iterations = int(max_iterations)
        self.weights: Optional[np.ndarray] = None
        self.bias: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """Fit logistic meta-classifier via batch gradient descent."""
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        if len(X_arr) == 0 or len(y_arr) == 0:
            return
        n_samples, n_features = X_arr.shape
        self.weights = np.zeros(n_features)
        self.bias = 0.0

        for _ in range(self.max_iterations):
            z = np.dot(X_arr, self.weights) + self.bias
            preds = 1.0 / (1.0 + np.exp(-np.clip(z, -20.0, 20.0)))
            err = preds - y_arr
            dw = np.dot(X_arr.T, err) / n_samples
            db = np.mean(err)
            self.weights -= self.learning_rate * dw
            self.bias -= self.learning_rate * db

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predict continuous probability of positive execution outcome P(win=1)."""
        X_arr = np.asarray(X, dtype=float)
        if self.weights is None or len(self.weights) != X_arr.shape[1]:
            return np.ones(len(X_arr)) * 0.50
        z = np.dot(X_arr, self.weights) + self.bias
        return 1.0 / (1.0 + np.exp(-np.clip(z, -20.0, 20.0)))

    def filter_signals(self, X: np.ndarray) -> np.ndarray:
        """Binary filter returning True where meta-probability >= confidence threshold."""
        probas = self.predict_proba(X)
        return probas >= self.confidence_threshold


# Aliases
MetaLabeler = MetaLabelingProbabilityClassifier


# ---------------------------------------------------------------------------
# Free Data Inefficiency Signal Calculators
# ---------------------------------------------------------------------------

def calculate_order_flow_imbalance(
    delta_bid_qty: float,
    delta_ask_qty: float,
) -> float:
    """Calculates Order Flow Imbalance (OFI):
    OFI_t = Delta Q_bid,t - Delta Q_ask,t
    Positive OFI indicates upward price drift pressure; negative indicates downward drift.
    """
    db = float(delta_bid_qty)
    da = float(delta_ask_qty)
    if not math.isfinite(db) or not math.isfinite(da):
        return 0.0
    return float(db - da)


def calculate_cot_commercial_hedger_index(
    current_net_commercial: float,
    rolling_3y_min_net: float,
    rolling_3y_max_net: float,
) -> float:
    """Calculates CFTC Commitment of Traders (COT) Commercial Index (0 to 100 percentile):
    COT Index = (Current Net - Min Net_3Y) / (Max Net_3Y - Min Net_3Y) * 100
    Readings > 90% indicate extreme smart money commercial buying.
    Readings < 10% indicate extreme commercial hedging/selling.
    """
    cur = float(current_net_commercial)
    c_min = float(rolling_3y_min_net)
    c_max = float(rolling_3y_max_net)

    if not all(math.isfinite(x) for x in (cur, c_min, c_max)) or c_max <= c_min:
        return 50.0

    raw_idx = ((cur - c_min) / (c_max - c_min)) * 100.0
    return float(max(0.0, min(100.0, raw_idx)))


def calculate_operating_accruals_anomaly(
    net_income: float,
    operating_cash_flow: float,
    total_assets: float,
) -> float:
    """Calculates Sloan Operating Accruals Anomaly:
    Accruals = (Net Income - Operating Cash Flow) / Total Assets
    High positive accruals indicate low earnings quality (aggressive accounting).
    Negative accruals indicate high earnings quality (cash-generative business).
    """
    ni = float(net_income)
    cfo = float(operating_cash_flow)
    ta = float(total_assets)

    if not all(math.isfinite(x) for x in (ni, cfo, ta)) or ta <= 0.0:
        return 0.0

    return float((ni - cfo) / ta)
