"""Prioritized Experience Replay Buffer for RL Training

Implements a replay buffer with prioritized experience replay (PER) for
more sample-efficient learning. The buffer stores transitions and samples
them based on their TD-error priority.

Usage:
    buffer = ReplayBuffer(capacity=10000)
    buffer.push(state, action, reward, next_state, done)
    batch = buffer.sample(batch_size=32)
"""

from __future__ import annotations

import random
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class ReplayBuffer:
    """Fixed-capacity replay buffer with optional prioritized sampling."""

    def __init__(self, capacity: int = 10000, alpha: float = 0.6, beta: float = 0.4):
        """Initialize the replay buffer.

        Args:
            capacity: Maximum number of transitions to store
            alpha: Priority exponent (0 = uniform, 1 = full priority)
            beta: Importance sampling exponent (0 = no correction, 1 = full)
        """
        self.capacity = capacity
        self.alpha = alpha
        self.beta = beta
        self.buffer: deque = deque(maxlen=capacity)
        self.priorities: deque = deque(maxlen=capacity)
        self.position = 0
        self.size = 0
        self._lock = threading.Lock()

    def push(
        self,
        state: Any,
        action: Any,
        reward: float,
        next_state: Any,
        done: bool,
        priority: Optional[float] = None,
    ) -> None:
        """Add a transition to the buffer."""
        transition = {
            "state": state,
            "action": action,
            "reward": reward,
            "next_state": next_state,
            "done": done,
        }
        with self._lock:
            if self.size < self.capacity:
                self.buffer.append(transition)
                self.priorities.append(priority or 1.0)
                self.size += 1
            else:
                self.buffer[self.position] = transition
                self.priorities[self.position] = priority or 1.0
            self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int = 32) -> Dict[str, Any]:
        """Sample a batch of transitions with prioritized sampling.

        Returns a dict with keys: states, actions, rewards, next_states,
        dones, indices, weights (importance sampling weights).
        """
        with self._lock:
            if self.size == 0:
                return {
                    "states": [],
                    "actions": [],
                    "rewards": [],
                    "next_states": [],
                    "dones": [],
                    "indices": [],
                    "weights": [],
                }

            # Calculate sampling probabilities
            priorities = np.array(self.priorities[:self.size], dtype=np.float64)
            priorities = np.maximum(priorities, 1e-6)  # Avoid division by zero
            probs = priorities ** self.alpha
            probs /= probs.sum()

            # Sample indices
            indices = np.random.choice(self.size, size=min(batch_size, self.size),
                                       p=probs, replace=False)

            # Calculate importance sampling weights
            weights = (self.size * probs[indices]) ** (-self.beta)
            weights /= weights.max()  # Normalize

            # Extract transitions
            transitions = [self.buffer[i] for i in indices]
            states = [t["state"] for t in transitions]
            actions = [t["action"] for t in transitions]
            rewards = [t["reward"] for t in transitions]
            next_states = [t["next_state"] for t in transitions]
            dones = [t["done"] for t in transitions]

            return {
                "states": states,
                "actions": actions,
                "rewards": rewards,
                "next_states": next_states,
                "dones": dones,
                "indices": indices.tolist(),
                "weights": weights.tolist(),
            }

    def update_priorities(self, indices: List[int], priorities: List[float]) -> None:
        """Update priorities for sampled transitions (based on TD-error)."""
        with self._lock:
            for idx, priority in zip(indices, priorities):
                if 0 <= idx < self.size:
                    self.priorities[idx] = priority

    def __len__(self) -> int:
        return self.size

    def is_full(self) -> bool:
        return self.size >= self.capacity

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self.buffer.clear()
            self.priorities.clear()
            self.position = 0
            self.size = 0

    def get_stats(self) -> Dict[str, Any]:
        """Get buffer statistics."""
        with self._lock:
            if self.size == 0:
                return {"size": 0, "capacity": self.capacity, "avg_priority": 0}
            priorities = list(self.priorities[:self.size])
            return {
                "size": self.size,
                "capacity": self.capacity,
                "avg_priority": sum(priorities) / len(priorities),
                "max_priority": max(priorities),
                "min_priority": min(priorities),
            }


class SimpleReplayBuffer:
    """Simpler uniform replay buffer without prioritization."""

    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def push(
        self,
        state: Any,
        action: Any,
        reward: float,
        next_state: Any,
        done: bool,
    ) -> None:
        """Add a transition to the buffer."""
        transition = {
            "state": state,
            "action": action,
            "reward": reward,
            "next_state": next_state,
            "done": done,
        }
        with self._lock:
            self.buffer.append(transition)

    def sample(self, batch_size: int = 32) -> Dict[str, Any]:
        """Sample a batch of transitions uniformly."""
        with self._lock:
            if len(self.buffer) == 0:
                return {
                    "states": [],
                    "actions": [],
                    "rewards": [],
                    "next_states": [],
                    "dones": [],
                }

            batch = random.sample(self.buffer, min(batch_size, len(self.buffer)))
            return {
                "states": [t["state"] for t in batch],
                "actions": [t["action"] for t in batch],
                "rewards": [t["reward"] for t in batch],
                "next_states": [t["next_state"] for t in batch],
                "dones": [t["done"] for t in batch],
            }

    def __len__(self) -> int:
        return len(self.buffer)

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self.buffer.clear()
