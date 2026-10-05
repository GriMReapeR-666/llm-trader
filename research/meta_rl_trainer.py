"""Offline Meta-RL starter (PEARL-inspired latent context / RL²-PPO).

Adapts position-management across regime × symbol × setup tasks using a
context encoder that aggregates in-task transitions, then conditions a PPO
policy on the latent (PEARL-style) — with a pure-numpy RL²-style fallback.

Research only: every public result hard-codes
``execution_authority=False`` and ``promotion_safe=False``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from research.kronos_trading_integrator import MANAGEMENT_ACTIONS
from research.ppo_trainer import (
    N_ACTIONS,
    OBS_DIM_KRONOS,
    _make_simulator,
    _softmax,
    compute_gae,
)

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    _HAS_TORCH = True
except ImportError:  # pragma: no cover
    torch = None  # type: ignore
    nn = None  # type: ignore
    F = None  # type: ignore
    _HAS_TORCH = False

log = logging.getLogger("MetaRLTrainer")

MODEL_KIND = "meta_rl_pearl_rl2_ppo_v1"
LATENT_DIM = 8

DEFAULT_TASKS: List[Dict[str, str]] = [
    {"symbol": "EURUSD", "regime": "volatile", "setup_id": "breakout_mgmt"},
    {"symbol": "EURUSD", "regime": "range", "setup_id": "mean_reversion_mgmt"},
    {"symbol": "GBPUSD", "regime": "bull", "setup_id": "trend_follow_mgmt"},
    {"symbol": "USDJPY", "regime": "bear", "setup_id": "breakdown_mgmt"},
]


def _one_hot(index: int, size: int = N_ACTIONS) -> np.ndarray:
    vec = np.zeros(size, dtype=float)
    if 0 <= index < size:
        vec[index] = 1.0
    return vec


def _pad_obs(vector: Sequence[float], obs_dim: int) -> np.ndarray:
    arr = np.asarray(vector, dtype=float).reshape(-1)
    if arr.shape[0] < obs_dim:
        return np.pad(arr, (0, obs_dim - arr.shape[0]))
    return arr[:obs_dim]


def task_key(task: Dict[str, Any]) -> str:
    return f"{task.get('symbol')}|{task.get('regime')}|{task.get('setup_id')}"


if _HAS_TORCH:

    class ContextEncoderNet(nn.Module):
        """Maps a transition to a latent increment (PEARL-inspired encoder)."""

        def __init__(self, transition_dim: int, latent_dim: int = LATENT_DIM, hidden: int = 64):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(transition_dim, hidden),
                nn.Tanh(),
                nn.Linear(hidden, latent_dim),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.net(x)

    class MetaActorCritic(nn.Module):
        """Policy/value conditioned on observation + task latent."""

        def __init__(self, obs_dim: int, latent_dim: int = LATENT_DIM, n_actions: int = N_ACTIONS, hidden: int = 64):
            super().__init__()
            self.backbone = nn.Sequential(
                nn.Linear(obs_dim + latent_dim, hidden),
                nn.Tanh(),
                nn.Linear(hidden, hidden),
                nn.Tanh(),
            )
            self.policy = nn.Linear(hidden, n_actions)
            self.value = nn.Linear(hidden, 1)

        def forward(self, obs: "torch.Tensor", z: "torch.Tensor") -> Tuple["torch.Tensor", "torch.Tensor"]:
            h = self.backbone(torch.cat([obs, z], dim=-1))
            return self.policy(h), self.value(h).squeeze(-1)


class NumpyContextEncoder:
    """Linear transition encoder + EMA posterior (torch-free PEARL-lite)."""

    def __init__(self, transition_dim: int, latent_dim: int = LATENT_DIM, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.W = rng.normal(0.0, 0.1, size=(transition_dim, latent_dim))
        self.b = np.zeros(latent_dim, dtype=float)
        self.latent_dim = latent_dim

    def encode(self, transition: np.ndarray) -> np.ndarray:
        x = transition.reshape(1, -1)
        return np.tanh(x @ self.W + self.b)[0]

    def state_dict(self) -> Dict[str, Any]:
        return {"W": self.W.tolist(), "b": self.b.tolist(), "latent_dim": self.latent_dim}

    def load_state_dict(self, data: Dict[str, Any]) -> None:
        self.W = np.asarray(data["W"], dtype=float)
        self.b = np.asarray(data["b"], dtype=float)
        self.latent_dim = int(data.get("latent_dim", LATENT_DIM))


class NumpyMetaActorCritic:
    """Linear softmax policy + value on concat(obs, latent)."""

    def __init__(self, obs_dim: int, latent_dim: int = LATENT_DIM, n_actions: int = N_ACTIONS, seed: int = 0):
        rng = np.random.default_rng(seed)
        scale = 0.1
        in_dim = obs_dim + latent_dim
        self.W_pi = rng.normal(0.0, scale, size=(in_dim, n_actions))
        self.b_pi = np.zeros(n_actions, dtype=float)
        self.W_v = rng.normal(0.0, scale, size=(in_dim,))
        self.b_v = 0.0

    def logits_value(self, obs: np.ndarray, z: np.ndarray) -> Tuple[np.ndarray, Any]:
        single = obs.ndim == 1
        o = obs.reshape(1, -1) if single else obs
        if z.ndim == 1:
            zz = np.repeat(z.reshape(1, -1), o.shape[0], axis=0)
        else:
            zz = z
        x = np.concatenate([o, zz], axis=-1)
        logits = x @ self.W_pi + self.b_pi
        values = x @ self.W_v + self.b_v
        if single:
            return logits[0], float(values[0])
        return logits, values

    def state_dict(self) -> Dict[str, Any]:
        return {
            "W_pi": self.W_pi.tolist(),
            "b_pi": self.b_pi.tolist(),
            "W_v": self.W_v.tolist(),
            "b_v": float(self.b_v),
        }

    def load_state_dict(self, data: Dict[str, Any]) -> None:
        self.W_pi = np.asarray(data["W_pi"], dtype=float)
        self.b_pi = np.asarray(data["b_pi"], dtype=float)
        self.W_v = np.asarray(data["W_v"], dtype=float)
        self.b_v = float(data["b_v"])


class MetaRLTrainer:
    """Offline PEARL-inspired / RL²-PPO trainer for management actions."""

    def __init__(
        self,
        obs_dim: int = OBS_DIM_KRONOS,
        *,
        latent_dim: int = LATENT_DIM,
        lr: float = 3e-3,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_eps: float = 0.2,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
        context_momentum: float = 0.85,
        seed: int = 0,
        use_torch: Optional[bool] = None,
    ):
        self.obs_dim = int(obs_dim)
        self.latent_dim = int(latent_dim)
        self.lr = float(lr)
        self.gamma = float(gamma)
        self.gae_lambda = float(gae_lambda)
        self.clip_eps = float(clip_eps)
        self.entropy_coef = float(entropy_coef)
        self.value_coef = float(value_coef)
        self.context_momentum = float(context_momentum)
        self.seed = int(seed)
        self.actions = list(MANAGEMENT_ACTIONS)
        self.transition_dim = self.obs_dim + N_ACTIONS + 1
        self.backend = "torch" if (use_torch if use_torch is not None else _HAS_TORCH) and _HAS_TORCH else "numpy"
        self.rng = np.random.default_rng(self.seed)
        self.context = np.zeros(self.latent_dim, dtype=float)
        self._context_count = 0

        if self.backend == "torch":
            torch.manual_seed(self.seed)
            self.encoder = ContextEncoderNet(self.transition_dim, self.latent_dim)
            self.model = MetaActorCritic(self.obs_dim, self.latent_dim, N_ACTIONS)
            params = list(self.encoder.parameters()) + list(self.model.parameters())
            self.optimizer = torch.optim.Adam(params, lr=self.lr)
            self.numpy_encoder: Optional[NumpyContextEncoder] = None
            self.numpy_model: Optional[NumpyMetaActorCritic] = None
        else:
            self.encoder = None
            self.model = None
            self.optimizer = None
            self.numpy_encoder = NumpyContextEncoder(self.transition_dim, self.latent_dim, seed=self.seed)
            self.numpy_model = NumpyMetaActorCritic(self.obs_dim, self.latent_dim, N_ACTIONS, seed=self.seed)

    def reset_context(self) -> None:
        self.context = np.zeros(self.latent_dim, dtype=float)
        self._context_count = 0

    def _encode_transition(self, obs: np.ndarray, action: int, reward: float) -> np.ndarray:
        transition = np.concatenate([obs, _one_hot(action), np.asarray([reward], dtype=float)])
        if self.backend == "torch":
            assert self.encoder is not None
            with torch.no_grad():
                z = self.encoder(torch.as_tensor(transition, dtype=torch.float32))
                return z.detach().cpu().numpy()
        assert self.numpy_encoder is not None
        return self.numpy_encoder.encode(transition)

    def update_context(self, obs_vec: Sequence[float], action: int, reward: float) -> np.ndarray:
        """EMA posterior update from one transition (PEARL-lite)."""
        obs = _pad_obs(obs_vec, self.obs_dim)
        encoded = self._encode_transition(obs, int(action), float(reward))
        m = self.context_momentum
        if self._context_count == 0:
            self.context = encoded
        else:
            self.context = m * self.context + (1.0 - m) * encoded
        self._context_count += 1
        return self.context.copy()

    def act(self, obs_vec: Sequence[float], *, z: Optional[np.ndarray] = None) -> Tuple[int, float, float]:
        obs = _pad_obs(obs_vec, self.obs_dim)
        latent = self.context if z is None else np.asarray(z, dtype=float).reshape(-1)
        if latent.shape[0] != self.latent_dim:
            latent = _pad_obs(latent, self.latent_dim)
        if self.backend == "torch":
            assert self.model is not None
            with torch.no_grad():
                o_t = torch.as_tensor(obs, dtype=torch.float32)
                z_t = torch.as_tensor(latent, dtype=torch.float32)
                logits, value = self.model(o_t, z_t)
                dist = torch.distributions.Categorical(logits=logits)
                action = int(dist.sample().item())
                log_prob = float(dist.log_prob(torch.tensor(action)).item())
                return action, log_prob, float(value.item())
        assert self.numpy_model is not None
        logits, value = self.numpy_model.logits_value(obs, latent)
        probs = _softmax(logits)
        action = int(self.rng.choice(N_ACTIONS, p=probs))
        log_prob = float(np.log(max(probs[action], 1e-12)))
        return action, log_prob, float(value)

    def _update_torch(
        self,
        obs: np.ndarray,
        latents: np.ndarray,
        actions: np.ndarray,
        old_log_probs: np.ndarray,
        advantages: np.ndarray,
        returns: np.ndarray,
        epochs: int,
        batch_size: int,
    ) -> Dict[str, float]:
        assert self.model is not None and self.optimizer is not None
        adv = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        dataset_size = len(obs)
        last_stats = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
        for _ in range(epochs):
            idx = self.rng.permutation(dataset_size)
            for start in range(0, dataset_size, batch_size):
                batch = idx[start : start + batch_size]
                o = torch.as_tensor(obs[batch], dtype=torch.float32)
                z = torch.as_tensor(latents[batch], dtype=torch.float32)
                a = torch.as_tensor(actions[batch], dtype=torch.int64)
                old_lp = torch.as_tensor(old_log_probs[batch], dtype=torch.float32)
                adv_b = torch.as_tensor(adv[batch], dtype=torch.float32)
                ret_b = torch.as_tensor(returns[batch], dtype=torch.float32)

                logits, values = self.model(o, z)
                dist = torch.distributions.Categorical(logits=logits)
                log_probs = dist.log_prob(a)
                ratio = torch.exp(log_probs - old_lp)
                surr1 = ratio * adv_b
                surr2 = torch.clamp(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * adv_b
                policy_loss = -torch.min(surr1, surr2).mean()
                value_loss = F.mse_loss(values, ret_b)
                entropy = dist.entropy().mean()
                loss = policy_loss + self.value_coef * value_loss - self.entropy_coef * entropy

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(
                    list(self.model.parameters()) + list(self.encoder.parameters()), 1.0
                )
                self.optimizer.step()
                last_stats = {
                    "policy_loss": float(policy_loss.item()),
                    "value_loss": float(value_loss.item()),
                    "entropy": float(entropy.item()),
                }
        return last_stats

    def _update_numpy(
        self,
        obs: np.ndarray,
        latents: np.ndarray,
        actions: np.ndarray,
        old_log_probs: np.ndarray,
        advantages: np.ndarray,
        returns: np.ndarray,
        epochs: int,
    ) -> Dict[str, float]:
        assert self.numpy_model is not None
        adv = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        last = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
        for _ in range(epochs):
            logits, values = self.numpy_model.logits_value(obs, latents)
            probs = _softmax(logits)
            row = np.arange(len(actions))
            new_log_probs = np.log(np.clip(probs[row, actions], 1e-12, None))
            ratio = np.exp(new_log_probs - old_log_probs)
            surr1 = ratio * adv
            surr2 = np.clip(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * adv
            policy_loss = -np.minimum(surr1, surr2).mean()
            value_loss = float(np.mean((values - returns) ** 2))
            entropy = float(-(probs * np.log(np.clip(probs, 1e-12, None))).sum(axis=1).mean())

            chosen = np.zeros_like(probs)
            chosen[row, actions] = 1.0
            use_unclipped = surr1 <= surr2
            coeff = np.where(
                use_unclipped,
                adv,
                adv * ((ratio >= 1.0 - self.clip_eps) & (ratio <= 1.0 + self.clip_eps)),
            )
            dlogits = (probs - chosen) * coeff.reshape(-1, 1)
            x = np.concatenate([obs, latents], axis=-1)
            self.numpy_model.W_pi -= self.lr * (x.T @ dlogits) / max(1, len(obs))
            self.numpy_model.b_pi -= self.lr * dlogits.mean(axis=0)
            derr = (values - returns) / max(1, len(obs))
            self.numpy_model.W_v -= self.lr * self.value_coef * (x.T @ derr)
            self.numpy_model.b_v -= self.lr * self.value_coef * float(derr.mean())
            last = {"policy_loss": float(policy_loss), "value_loss": value_loss, "entropy": entropy}
        return last

    def update(
        self,
        batch: Dict[str, np.ndarray],
        *,
        epochs: int = 4,
        batch_size: int = 64,
    ) -> Dict[str, float]:
        obs = np.asarray(batch["obs"], dtype=float)
        latents = np.asarray(batch["latents"], dtype=float)
        actions = np.asarray(batch["actions"], dtype=np.int64)
        old_log_probs = np.asarray(batch["log_probs"], dtype=float)
        advantages = np.asarray(batch["advantages"], dtype=float)
        returns = np.asarray(batch["returns"], dtype=float)
        if self.backend == "torch":
            return self._update_torch(
                obs, latents, actions, old_log_probs, advantages, returns, epochs, batch_size
            )
        return self._update_numpy(obs, latents, actions, old_log_probs, advantages, returns, epochs)

    def save_artifact(self, path: str, *, meta: Optional[Dict[str, Any]] = None) -> str:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "model_kind": MODEL_KIND,
            "backend": self.backend,
            "obs_dim": self.obs_dim,
            "latent_dim": self.latent_dim,
            "actions": self.actions,
            "seed": self.seed,
            "hyperparameters": {
                "lr": self.lr,
                "gamma": self.gamma,
                "gae_lambda": self.gae_lambda,
                "clip_eps": self.clip_eps,
                "entropy_coef": self.entropy_coef,
                "value_coef": self.value_coef,
                "context_momentum": self.context_momentum,
            },
            "execution_authority": False,
            "promotion_safe": False,
            "meta": meta or {},
        }
        if self.backend == "torch":
            assert self.model is not None and self.encoder is not None
            payload["state_dict"] = {
                "policy": {k: v.detach().cpu().tolist() for k, v in self.model.state_dict().items()},
                "encoder": {k: v.detach().cpu().tolist() for k, v in self.encoder.state_dict().items()},
            }
        else:
            assert self.numpy_model is not None and self.numpy_encoder is not None
            payload["state_dict"] = {
                "policy": self.numpy_model.state_dict(),
                "encoder": self.numpy_encoder.state_dict(),
            }
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return str(destination)


def _rollout_episode(
    trainer: MetaRLTrainer,
    sim,
    *,
    max_steps: int,
    direction: float,
    seed: int,
    update_context: bool,
    explore: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    obs = sim.open_management_episode(direction=direction, seed=seed)
    steps_data: List[Dict[str, Any]] = []
    ep_reward = 0.0
    steps = 0
    done = False
    info: Dict[str, Any] = {}
    z_snapshot = trainer.context.copy()

    while not done and steps < max_steps:
        vec = _pad_obs(sim.observation_vector(obs), trainer.obs_dim)
        if explore:
            action_i = int(trainer.rng.integers(0, N_ACTIONS))
            # Approximate log-prob under uniform explore for logging only.
            log_p = float(np.log(1.0 / N_ACTIONS))
            value = 0.0
        else:
            action_i, log_p, value = trainer.act(vec, z=z_snapshot)
        action_name = trainer.actions[action_i]
        next_obs, reward, done, info = sim.step(action_name)
        if update_context:
            trainer.update_context(vec, action_i, float(reward))
            z_snapshot = trainer.context.copy()
        steps_data.append(
            {
                "obs": vec,
                "latent": z_snapshot.copy(),
                "action": action_i,
                "log_prob": log_p,
                "reward": float(reward),
                "value": float(value),
                "done": bool(done),
            }
        )
        ep_reward += float(reward)
        obs = next_obs
        steps += 1
        if abs(float(obs.get("position") or 0.0)) < 1e-9:
            steps_data[-1]["done"] = True
            done = True

    if steps_data:
        steps_data[-1]["done"] = True  # Seal episode boundary for GAE

    summary = {
        "steps": steps,
        "reward": round(ep_reward, 4),
        "pnl": info.get("pnl") if isinstance(info, dict) else None,
        "direction": direction,
        "context_updates": trainer._context_count,
    }
    return steps_data, summary


def collect_meta_batch(
    trainer: MetaRLTrainer,
    tasks: Sequence[Dict[str, Any]],
    *,
    context_episodes: int,
    adapt_episodes: int,
    max_steps: int,
    seed: int,
    simulator: str = "kronos",
) -> Tuple[Dict[str, np.ndarray], List[Dict[str, Any]]]:
    """Per-task: explore for context, then PPO rollouts conditioned on latent."""
    obs_list: List[np.ndarray] = []
    latents: List[np.ndarray] = []
    actions: List[int] = []
    log_probs: List[float] = []
    rewards: List[float] = []
    values: List[float] = []
    dones: List[bool] = []
    task_summaries: List[Dict[str, Any]] = []

    for t_i, task in enumerate(tasks):
        trainer.reset_context()
        symbol = str(task["symbol"])
        regime = str(task["regime"])
        setup_id = str(task.get("setup_id", "unknown"))
        context_rewards: List[float] = []
        adapt_details: List[Dict[str, Any]] = []

        for c_i in range(max(1, int(context_episodes))):
            ep_seed = int(seed) + t_i * 1000 + c_i
            sim = _make_simulator(simulator, symbol=symbol, regime=regime, max_steps=max_steps, seed=ep_seed)
            direction = 1.0 if (c_i % 2 == 0) else -1.0
            _, summary = _rollout_episode(
                trainer,
                sim,
                max_steps=max_steps,
                direction=direction,
                seed=ep_seed,
                update_context=True,
                explore=True,
            )
            context_rewards.append(float(summary["reward"]))

        z_after_context = trainer.context.copy()

        for a_i in range(max(1, int(adapt_episodes))):
            ep_seed = int(seed) + t_i * 1000 + 100 + a_i
            sim = _make_simulator(simulator, symbol=symbol, regime=regime, max_steps=max_steps, seed=ep_seed)
            direction = 1.0 if (a_i % 2 == 0) else -1.0
            # Freeze latent during adapt episode (RL²/PEARL train batch conditioned on z).
            frozen_z = z_after_context.copy()
            trainer.context = frozen_z
            steps_data, summary = _rollout_episode(
                trainer,
                sim,
                max_steps=max_steps,
                direction=direction,
                seed=ep_seed,
                update_context=False,
                explore=False,
            )
            for step in steps_data:
                obs_list.append(step["obs"])
                latents.append(frozen_z)
                actions.append(step["action"])
                log_probs.append(step["log_prob"])
                rewards.append(step["reward"])
                values.append(step["value"])
                dones.append(step["done"])
            adapt_details.append(summary)

        task_summaries.append(
            {
                "task": task_key(task),
                "symbol": symbol,
                "regime": regime,
                "setup_id": setup_id,
                "context_mean_reward": float(np.mean(context_rewards)) if context_rewards else 0.0,
                "adapt_mean_reward": (
                    float(np.mean([d["reward"] for d in adapt_details])) if adapt_details else 0.0
                ),
                "adapt_episodes": adapt_details,
                "latent_norm": float(np.linalg.norm(z_after_context)),
                "execution_authority": False,
            }
        )

    advantages, returns = compute_gae(
        rewards, values, dones, gamma=trainer.gamma, lam=trainer.gae_lambda, last_value=0.0
    )
    batch = {
        "obs": np.asarray(obs_list, dtype=float),
        "latents": np.asarray(latents, dtype=float),
        "actions": np.asarray(actions, dtype=np.int64),
        "log_probs": np.asarray(log_probs, dtype=float),
        "rewards": np.asarray(rewards, dtype=float),
        "values": np.asarray(values, dtype=float),
        "dones": np.asarray(dones, dtype=bool),
        "advantages": advantages,
        "returns": returns,
    }
    return batch, task_summaries


def train_meta_rl_offline(
    *,
    tasks: Optional[Sequence[Dict[str, Any]]] = None,
    updates: int = 2,
    context_episodes: int = 1,
    adapt_episodes: int = 2,
    max_steps: int = 16,
    seed: int = 7,
    simulator: str = "kronos",
    artifact_path: str = "artifacts/meta_rl_position_mgmt.json",
    epochs: int = 2,
    batch_size: int = 32,
    use_torch: Optional[bool] = None,
) -> Dict[str, Any]:
    """Research-path entrypoint: offline Meta-RL on Kronos/MT5 simulators."""
    sim_key = str(simulator or "kronos").lower()
    if sim_key not in {"kronos", "mt5"}:
        raise ValueError("simulator must be 'kronos' or 'mt5'")

    task_list = [dict(t) for t in (tasks or DEFAULT_TASKS)]
    if not task_list:
        raise ValueError("at least one meta-RL task is required")

    probe_task = task_list[0]
    probe = _make_simulator(
        sim_key,
        symbol=str(probe_task["symbol"]),
        regime=str(probe_task["regime"]),
        max_steps=max_steps,
        seed=seed,
    )
    probe_obs = probe.open_management_episode(seed=seed)
    obs_dim = len(probe.observation_vector(probe_obs))

    trainer = MetaRLTrainer(obs_dim=obs_dim, seed=seed, use_torch=use_torch)
    update_metrics: List[Dict[str, Any]] = []
    all_task_details: List[Dict[str, Any]] = []

    config_fingerprint = {
        "updates": updates,
        "context_episodes": context_episodes,
        "adapt_episodes": adapt_episodes,
        "max_steps": max_steps,
        "seed": seed,
        "simulator": sim_key,
        "backend": trainer.backend,
        "model_kind": MODEL_KIND,
        "latent_dim": trainer.latent_dim,
        "actions": list(MANAGEMENT_ACTIONS),
        "tasks": [task_key(t) for t in task_list],
    }
    training_hash = hashlib.sha256(
        json.dumps(config_fingerprint, sort_keys=True).encode("utf-8")
    ).hexdigest()

    for update_i in range(max(1, int(updates))):
        batch, summaries = collect_meta_batch(
            trainer,
            task_list,
            context_episodes=context_episodes,
            adapt_episodes=adapt_episodes,
            max_steps=max_steps,
            seed=seed + update_i * 10_000,
            simulator=sim_key,
        )
        if len(batch["obs"]) == 0:
            continue
        stats = trainer.update(batch, epochs=epochs, batch_size=batch_size)
        update_metrics.append(
            {
                "update": update_i,
                "timesteps": int(len(batch["obs"])),
                "mean_reward": float(np.mean(batch["rewards"])) if len(batch["rewards"]) else 0.0,
                "mean_advantage": float(np.mean(batch["advantages"])) if len(batch["advantages"]) else 0.0,
                "n_tasks": len(summaries),
                **stats,
            }
        )
        all_task_details.extend(summaries)

    # Task-level session rollup for promotion evidence.
    rollup: Dict[str, Dict[str, Any]] = {}
    for row in all_task_details:
        key = row["task"]
        bucket = rollup.setdefault(
            key,
            {
                "task": key,
                "symbol": row["symbol"],
                "regime": row["regime"],
                "setup_id": row["setup_id"],
                "n": 0,
                "adapt_rewards": [],
                "context_rewards": [],
            },
        )
        bucket["n"] += 1
        bucket["adapt_rewards"].append(float(row["adapt_mean_reward"]))
        bucket["context_rewards"].append(float(row["context_mean_reward"]))
    task_rollup = []
    for bucket in rollup.values():
        task_rollup.append(
            {
                "task": bucket["task"],
                "symbol": bucket["symbol"],
                "regime": bucket["regime"],
                "setup_id": bucket["setup_id"],
                "passes": bucket["n"],
                "mean_adapt_reward": float(np.mean(bucket["adapt_rewards"])) if bucket["adapt_rewards"] else 0.0,
                "mean_context_reward": (
                    float(np.mean(bucket["context_rewards"])) if bucket["context_rewards"] else 0.0
                ),
                "execution_authority": False,
            }
        )

    # Empirical promotion candidacy: never auto-promotes, but flags for human gate
    _n_tasks = len(task_rollup)
    _n_positive = sum(1 for t in task_rollup if t["mean_adapt_reward"] > 0.5)
    _mean_reward = (
        float(np.mean([t["mean_adapt_reward"] for t in task_rollup])) if task_rollup else 0.0
    )
    _promotion_candidate = (
        _n_tasks >= 2
        and _n_positive >= _n_tasks * 0.5
        and _mean_reward > 0.3
    )

    report: Dict[str, Any] = {
        "status": "COMPLETED",
        "model_kind": MODEL_KIND,
        "training_data_hash": training_hash,
        "configuration": config_fingerprint,
        "updates": update_metrics,
        "task_details": all_task_details,
        "task_rollup": task_rollup,
        "mean_adapt_reward": (
            float(np.mean([t["adapt_mean_reward"] for t in all_task_details])) if all_task_details else 0.0
        ),
        "artifact_written": False,
        "promotion_safe": False,
        "promotion_candidate": _promotion_candidate,
        "promotion_criteria": {
            "min_tasks": 2,
            "min_positive_ratio": 0.5,
            "min_mean_adapt_reward": 0.3,
            "actual_tasks": _n_tasks,
            "actual_positive": _n_positive,
            "actual_mean_reward": round(_mean_reward, 4),
        },
        "execution_authority": False,
        "source": "MT5KronosRLSimulator" if sim_key == "mt5" else "KronosRLMarketSimulator",
        "limitation": (
            "Offline Meta-RL research artifact only; promotion_candidate=True flags this "
            "for human review but never grants automatic live execution."
        ),
    }

    if artifact_path:
        path = trainer.save_artifact(
            artifact_path,
            meta={
                "training_data_hash": training_hash,
                "report_status": report["status"],
                "task_rollup": task_rollup,
            },
        )
        report["artifact_written"] = True
        report["artifact_path"] = path
        # Content hash of the written weights for promotion evidence.
        report["artifact_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    log.info(
        "Meta-RL offline training complete: updates=%s tasks=%s backend=%s",
        len(update_metrics),
        len(task_list),
        trainer.backend,
    )

    if artifact_path:
        report_path = str(
            Path(artifact_path).with_name(Path(artifact_path).stem + "_report.json"))
        Path(report_path).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["report_path"] = report_path

    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline PEARL-inspired / RL²-PPO Meta-RL trainer (research only)"
    )
    parser.add_argument("--updates", type=int, default=2)
    parser.add_argument("--context-episodes", type=int, default=1)
    parser.add_argument("--adapt-episodes", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--simulator", choices=("kronos", "mt5"), default="kronos")
    parser.add_argument("--artifact-path", default="artifacts/meta_rl_position_mgmt.json")
    parser.add_argument("--numpy-only", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = train_meta_rl_offline(
        updates=args.updates,
        context_episodes=args.context_episodes,
        adapt_episodes=args.adapt_episodes,
        max_steps=args.max_steps,
        seed=args.seed,
        simulator=args.simulator,
        artifact_path=args.artifact_path,
        use_torch=False if args.numpy_only else None,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
