"""Shared research utilities for RL trainers (PPO, GRPO, Meta-RL).

Eliminates duplicated _softmax, compute_gae, and numpy policy helpers
across ppo_trainer.py, grpo_proposer.py, and meta_rl_trainer.py.
"""

from __future__ import annotations

from typing import Any, Dict, Sequence, Tuple

import numpy as np


def softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over last axis."""
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(np.clip(shifted, -30.0, 30.0))
    return exp / np.clip(exp.sum(axis=-1, keepdims=True), 1e-12, None)


def compute_gae(
    rewards: Sequence[float],
    values: Sequence[float],
    dones: Sequence[bool],
    *,
    gamma: float = 0.99,
    lam: float = 0.95,
    last_value: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Generalized Advantage Estimation; returns (advantages, returns)."""
    rewards_a = np.asarray(rewards, dtype=float)
    values_a = np.asarray(values, dtype=float)
    dones_a = np.asarray(dones, dtype=float)
    n = len(rewards_a)
    advantages = np.zeros(n, dtype=float)
    gae = 0.0
    next_value = float(last_value)
    for t in reversed(range(n)):
        mask = 1.0 - float(dones_a[t])
        delta = rewards_a[t] + gamma * next_value * mask - values_a[t]
        gae = delta + gamma * lam * mask * gae
        advantages[t] = gae
        next_value = values_a[t]
    returns = advantages + values_a
    return advantages, returns


def group_relative_advantages(
    rewards: Sequence[float],
    *,
    eps: float = 1e-8,
) -> np.ndarray:
    """GRPO advantage: standardize rewards within a group (no critic)."""
    arr = np.asarray(rewards, dtype=float)
    if arr.size == 0:
        return arr
    mean = float(arr.mean())
    std = float(arr.std())
    if std < eps:
        return arr - mean
    return (arr - mean) / (std + eps)


class NumpyLinearPolicy:
    """Pure-numpy linear softmax policy + value head (torch-free fallback).

    Shared by PPO and GRPO trainers.
    """

    def __init__(self, obs_dim: int, n_actions: int, seed: int = 0, with_value: bool = True):
        rng = np.random.default_rng(seed)
        scale = 0.1
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.W_pi = rng.normal(0.0, scale, size=(obs_dim, n_actions))
        self.b_pi = np.zeros(n_actions, dtype=float)
        self.with_value = with_value
        if with_value:
            self.W_v = rng.normal(0.0, scale, size=(obs_dim,))
            self.b_v = 0.0

    def logits(self, obs: np.ndarray) -> np.ndarray:
        single = obs.ndim == 1
        x = obs.reshape(1, -1) if single else obs
        out = x @ self.W_pi + self.b_pi
        return out[0] if single else out

    def logits_value(self, obs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        single = obs.ndim == 1
        x = obs.reshape(1, -1) if single else obs
        logits = x @ self.W_pi + self.b_pi
        if self.with_value:
            values = x @ self.W_v + self.b_v
            if single:
                return logits[0], float(values[0])
            return logits, values
        if single:
            return logits[0], 0.0
        return logits, np.zeros(len(obs))

    def probs(self, obs: np.ndarray) -> np.ndarray:
        return softmax(self.logits(obs))

    def sample(self, obs: np.ndarray, rng: np.random.Generator) -> Tuple[int, float, np.ndarray]:
        probs = self.probs(obs)
        action = int(rng.choice(self.n_actions, p=probs))
        log_prob = float(np.log(max(probs[action], 1e-12)))
        return action, log_prob, probs

    def state_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "W_pi": self.W_pi.tolist(),
            "b_pi": self.b_pi.tolist(),
            "obs_dim": self.obs_dim,
            "n_actions": self.n_actions,
        }
        if self.with_value:
            d["W_v"] = self.W_v.tolist()
            d["b_v"] = float(self.b_v)
        return d

    def load_state_dict(self, data: Dict[str, Any]) -> None:
        self.W_pi = np.asarray(data["W_pi"], dtype=float)
        self.b_pi = np.asarray(data["b_pi"], dtype=float)
        if self.with_value and "W_v" in data:
            self.W_v = np.asarray(data["W_v"], dtype=float)
            self.b_v = float(data.get("b_v", 0.0))


def clipped_ppo_gradient_step(
    policy: NumpyLinearPolicy,
    obs: np.ndarray,
    actions: np.ndarray,
    old_log_probs: np.ndarray,
    advantages: np.ndarray,
    lr: float,
    clip_eps: float,
    entropy_coef: float = 0.0,
    value_coef: float = 0.5,
    returns: np.ndarray | None = None,
    *,
    is_grpo: bool = False,
) -> Dict[str, float]:
    """Clipped PPO/GRPO gradient step on a numpy linear policy.

    Returns dict with policy_loss, value_loss (if with_value), entropy, n.
    """
    adv = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
    logits = policy.logits(obs)
    probs = softmax(logits)
    row = np.arange(len(actions))
    new_log_probs = np.log(np.clip(probs[row, actions], 1e-12, None))
    ratio = np.exp(new_log_probs - old_log_probs)
    surr1 = ratio * adv
    surr2 = np.clip(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * adv
    policy_loss = -np.minimum(surr1, surr2).mean()
    entropy = float(-(probs * np.log(np.clip(probs, 1e-12, None))).sum(axis=1).mean())

    # Gradient via softmax Jacobian
    chosen = np.zeros_like(probs)
    chosen[row, actions] = 1.0
    use_unclipped = surr1 <= surr2
    coeff = np.where(
        use_unclipped,
        adv,
        adv * ((ratio >= 1.0 - clip_eps) & (ratio <= 1.0 + clip_eps)),
    )
    dlogits = (probs - chosen) * coeff.reshape(-1, 1)

    if entropy_coef:
        dlogits = dlogits - entropy_coef * (np.log(np.clip(probs, 1e-12, None)) + 1.0) / max(1, len(obs))

    policy.W_pi -= lr * (obs.T @ dlogits) / max(1, len(obs))
    policy.b_pi -= lr * dlogits.mean(axis=0)

    result: Dict[str, float] = {"policy_loss": float(policy_loss), "entropy": entropy, "n": float(len(obs))}

    if policy.with_value and returns is not None:
        values = policy.logits_value(obs)[1]
        value_loss = float(np.mean((values - returns) ** 2))
        derr = (values - returns) / max(1, len(obs))
        policy.W_v -= lr * value_coef * (obs.T @ derr)
        policy.b_v -= lr * value_coef * float(derr.mean())
        result["value_loss"] = value_loss

    return result
