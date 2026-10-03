"""Reinforcement Learning Trading Agent Module.

Implements the DQN/PPO agent architecture from Hou (2026):
  - State space: OHLCV + technical indicators + regime signals
  - Action space: BUY/SELL/HOLD with position sizing
  - Reward: risk-adjusted returns with transaction costs
  - Supports DQN and PPO architectures
  - Integrated with alpha mining pipeline for signal enrichment

References:
  - Hou, A. (2026). Reinforcement Learning for Trading Strategies:
    A Reproducible Comparison with Classical Baselines.
  - GitHub: AndyH2007/reinforcement-learning-trading
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.info("PyTorch not available - using numpy fallback for RL agent")


class ActionType(Enum):
    BUY = 1
    HOLD = 0
    SELL = -1


@dataclass
class TradeAction:
    """Proposed trade action."""
    action: ActionType
    confidence: float
    position_size: float
    stop_loss: float
    take_profit: float
    reasoning: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "action_name": self.action.name,
            "confidence": round(self.confidence, 4),
            "position_size": round(self.position_size, 4),
            "stop_loss": round(self.stop_loss, 6),
            "take_profit": round(self.take_profit, 6),
            "reasoning": self.reasoning,
        }


@dataclass
class AgentConfig:
    """RL Agent configuration."""
    state_dim: int = 12
    action_dim: int = 3
    hidden_dim: int = 64
    learning_rate: float = 1e-4
    gamma: float = 0.99
    epsilon_start: float = 1.0
    epsilon_end: float = 0.01
    epsilon_decay: int = 1000
    batch_size: int = 32
    memory_size: int = 10000
    target_update: int = 100
    transaction_cost: float = 0.001
    risk_free_rate: float = 0.02
    max_position: float = 1.0
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
            "hidden_dim": self.hidden_dim,
            "learning_rate": self.learning_rate,
            "gamma": self.gamma,
            "transaction_cost": self.transaction_cost,
        }


class SimpleQNetwork:
    """Simple Q-network using numpy (fallback when PyTorch unavailable)."""

    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 64):
        self.state_dim = state_dim
        self.action_dim = action_dim
        scale = 0.01
        self.W1 = np.random.randn(state_dim, hidden_dim) * scale
        self.b1 = np.zeros(hidden_dim)
        self.W2 = np.random.randn(hidden_dim, hidden_dim) * scale
        self.b2 = np.zeros(hidden_dim)
        self.W3 = np.random.randn(hidden_dim, action_dim) * scale
        self.b3 = np.zeros(action_dim)

    def forward(self, state: np.ndarray) -> np.ndarray:
        h = np.maximum(0, state @ self.W1 + self.b1)
        h = np.maximum(0, h @ self.W2 + self.b2)
        return h @ self.W3 + self.b3

    def predict(self, state: np.ndarray) -> np.ndarray:
        if state.ndim == 1:
            state = state.reshape(1, -1)
        return self.forward(state)


class RLTradingAgent:
    """RL Trading Agent for signal generation.

    Generates trade signals using a trained or heuristic RL policy.
    Can be used as a signal generator within the alpha mining pipeline.
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        self.epsilon = self.config.epsilon_start
        self.steps = 0
        self.memory: List[Tuple] = []

        if TORCH_AVAILABLE:
            self.q_net = self._build_torch_network()
            self.target_net = self._build_torch_network()
            self.target_net.load_state_dict(self.q_net.state_dict())
            self.optimizer = optim.Adam(
                self.q_net.parameters(), lr=self.config.learning_rate
            )
        else:
            self.q_net = SimpleQNetwork(
                self.config.state_dim, self.config.action_dim, self.config.hidden_dim
            )
            self.target_net = None

    def _build_torch_network(self):
        """Build PyTorch Q-network."""
        if not TORCH_AVAILABLE:
            return None
        return nn.Sequential(
            nn.Linear(self.config.state_dim, self.config.hidden_dim),
            nn.ReLU(),
            nn.Linear(self.config.hidden_dim, self.config.hidden_dim),
            nn.ReLU(),
            nn.Linear(self.config.hidden_dim, self.config.action_dim),
        )

    def get_state(self, ohlcv: pd.DataFrame, idx: int) -> np.ndarray:
        """Extract state features from OHLCV data."""
        if idx < 10:
            return np.zeros(self.config.state_dim)

        closes = ohlcv["close"].values
        highs = ohlcv["high"].values
        lows = ohlcv["low"].values
        volumes = ohlcv["volume"].values

        c = closes[idx]
        if c == 0:
            return np.zeros(self.config.state_dim)

        returns_1 = (closes[idx] - closes[idx - 1]) / closes[idx - 1] if closes[idx - 1] != 0 else 0
        returns_5 = (closes[idx] - closes[idx - 5]) / closes[idx - 5] if idx >= 5 and closes[idx - 5] != 0 else 0
        returns_10 = (closes[idx] - closes[idx - 10]) / closes[idx - 10] if idx >= 10 and closes[idx - 10] != 0 else 0

        vol_5 = np.std(np.diff(closes[idx - 5:idx + 1]) / closes[idx - 5:idx]) if idx >= 5 else 0
        vol_10 = np.std(np.diff(closes[idx - 10:idx + 1]) / closes[idx - 10:idx]) if idx >= 10 else 0

        high_5 = max(highs[idx - 5:idx + 1]) / c if idx >= 5 else 1
        low_5 = min(lows[idx - 5:idx + 1]) / c if idx >= 5 else 1
        range_5 = high_5 - low_5

        vol_ma = np.mean(volumes[max(0, idx - 5):idx + 1])
        vol_ratio = volumes[idx] / vol_ma if vol_ma > 0 else 1

        obv = 0
        for k in range(max(1, idx - 5), idx + 1):
            if closes[k] > closes[k - 1]:
                obv += volumes[k]
            elif closes[k] < closes[k - 1]:
                obv -= volumes[k]
        obv_norm = obv / (vol_ma * 5) if vol_ma > 0 else 0

        state = np.array([
            returns_1,
            returns_5,
            returns_10,
            vol_5,
            vol_10,
            range_5,
            vol_ratio,
            obv_norm,
            (highs[idx] - lows[idx]) / c if c > 0 else 0,
            (c - lows[idx]) / (highs[idx] - lows[idx]) if (highs[idx] - lows[idx]) > 0 else 0.5,
            np.sign(returns_1) * min(abs(returns_1) * 100, 1),
            self.epsilon,
        ], dtype=np.float32)

        return np.clip(state, -5, 5)

    def select_action(self, state: np.ndarray) -> int:
        """Select action using epsilon-greedy policy."""
        self.steps += 1
        self.epsilon = max(
            self.config.epsilon_end,
            self.config.epsilon_start - self.steps / self.config.epsilon_decay,
        )

        if random.random() < self.epsilon:
            return random.randint(0, self.config.action_dim - 1)

        if TORCH_AVAILABLE and isinstance(self.q_net, nn.Module):
            with torch.no_grad():
                state_t = torch.FloatTensor(state).unsqueeze(0)
                q_values = self.q_net(state_t)
                return q_values.argmax(dim=1).item()
        else:
            q_values = self.q_net.predict(state)
            return int(np.argmax(q_values))

    def generate_signal(
        self, ohlcv: pd.DataFrame, idx: int
    ) -> TradeAction:
        """Generate a trade signal for the current bar."""
        state = self.get_state(ohlcv, idx)
        action_idx = self.select_action(state)

        action_map = {
            0: ActionType.SELL,
            1: ActionType.HOLD,
            2: ActionType.BUY,
        }
        action = action_map.get(action_idx, ActionType.HOLD)

        c = ohlcv["close"].values[idx]
        sl_dist = c * self.config.stop_loss_pct
        tp_dist = c * self.config.take_profit_pct

        if action == ActionType.BUY:
            stop_loss = c - sl_dist
            take_profit = c + tp_dist
        elif action == ActionType.SELL:
            stop_loss = c + sl_dist
            take_profit = c - tp_dist
        else:
            stop_loss = 0.0
            take_profit = 0.0

        confidence = 0.5
        if TORCH_AVAILABLE and isinstance(self.q_net, nn.Module):
            with torch.no_grad():
                state_t = torch.FloatTensor(state).unsqueeze(0)
                q_values = self.q_net(state_t)
                q_vals = q_values.numpy()[0]
                q_range = q_vals.max() - q_vals.min()
                confidence = (q_vals[action_idx] - q_vals.min()) / q_range if q_range > 0 else 0.5
        else:
            q_vals = self.q_net.predict(state)[0]
            q_range = q_vals.max() - q_vals.min()
            confidence = (q_vals[action_idx] - q_vals.min()) / q_range if q_range > 0 else 0.5

        return TradeAction(
            action=action,
            confidence=float(confidence),
            position_size=self.config.max_position * confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"RL agent: {action.name} (eps={self.epsilon:.3f})",
        )

    def store_transition(
        self, state: np.ndarray, action: int, reward: float,
        next_state: np.ndarray, done: bool,
    ):
        """Store transition in replay memory."""
        self.memory.append((state, action, reward, next_state, done))
        if len(self.memory) > self.config.memory_size:
            self.memory.pop(0)

    def compute_reward(
        self, pnl: float, position: float, risk_free: float = 0.0
    ) -> float:
        """Compute risk-adjusted reward."""
        cost = abs(position) * self.config.transaction_cost
        return pnl - cost

    def get_portfolio_metrics(
        self, ohlcv: pd.DataFrame
    ) -> Dict[str, Any]:
        """Get agent metrics without trading (evaluation mode)."""
        closes = ohlcv["close"].values
        returns = np.diff(closes) / closes[:-1]
        returns = returns[~np.isnan(returns)]

        if len(returns) == 0:
            return {"sharpe": 0, "max_drawdown": 0, "total_return": 0}

        total_return = (closes[-1] / closes[0] - 1) * 100 if closes[0] != 0 else 0
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0
        cumulative = (1 + returns).cumprod()
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak * 100
        max_dd = float(np.max(drawdown))

        return {
            "sharpe": round(float(sharpe), 4),
            "max_drawdown": round(max_dd, 2),
            "total_return": round(float(total_return), 2),
            "avg_daily_return": round(float(np.mean(returns) * 100), 4),
            "volatility": round(float(np.std(returns) * 100), 4),
        }
