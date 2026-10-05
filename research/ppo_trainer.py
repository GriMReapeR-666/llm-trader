"""Offline PPO + GAE for position-management research (Phase 2 starter).

Trains only inside ``KronosRLMarketSimulator`` / ``MT5KronosRLSimulator``.
Never grants broker authority: ``execution_authority=False`` and
``promotion_safe=False`` are hard-coded on every public result.

Discrete management actions
---------------------------
HOLD | TIGHTEN_SL | TRAIL | PARTIAL_CLOSE | FULL_EXIT

These extend the simulator action space (BUY/SELL remain for the Q-learning
baseline in ``rl_trainer.py``). Episodes start with an already-open position
via ``open_management_episode`` so the policy learns manage-not-enter.
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

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    _HAS_TORCH = True
except ImportError:  # pragma: no cover - exercised when torch missing
    torch = None  # type: ignore
    nn = None  # type: ignore
    F = None  # type: ignore
    _HAS_TORCH = False

log = logging.getLogger("PPOTrainer")

MODEL_KIND = "ppo_gae_position_mgmt_v1"
OBS_DIM_KRONOS = 9
N_ACTIONS = len(MANAGEMENT_ACTIONS)


from research.utils import softmax as _softmax, compute_gae  # noqa: F401


if _HAS_TORCH:

    class ActorCritic(nn.Module):
        """Small MLP policy + value head for discrete management actions."""

        def __init__(self, obs_dim: int, n_actions: int = N_ACTIONS, hidden: int = 64):
            super().__init__()
            self.backbone = nn.Sequential(
                nn.Linear(obs_dim, hidden),
                nn.Tanh(),
                nn.Linear(hidden, hidden),
                nn.Tanh(),
            )
            self.policy = nn.Linear(hidden, n_actions)
            self.value = nn.Linear(hidden, 1)

        def forward(self, obs: "torch.Tensor") -> Tuple["torch.Tensor", "torch.Tensor"]:
            h = self.backbone(obs)
            return self.policy(h), self.value(h).squeeze(-1)


class NumpyActorCritic:
    """Pure-numpy linear softmax policy + value (torch-free fallback)."""

    def __init__(self, obs_dim: int, n_actions: int = N_ACTIONS, seed: int = 0):
        rng = np.random.default_rng(seed)
        scale = 0.1
        self.W_pi = rng.normal(0.0, scale, size=(obs_dim, n_actions))
        self.b_pi = np.zeros(n_actions, dtype=float)
        self.W_v = rng.normal(0.0, scale, size=(obs_dim,))
        self.b_v = 0.0

    def logits_value(self, obs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        single = obs.ndim == 1
        x = obs.reshape(1, -1) if single else obs
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


class PPOTrainer:
    """Offline PPO clipped objective with GAE for position management."""

    def __init__(
        self,
        obs_dim: int = OBS_DIM_KRONOS,
        *,
        lr: float = 3e-3,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_eps: float = 0.2,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
        seed: int = 0,
        use_torch: Optional[bool] = None,
    ):
        self.obs_dim = int(obs_dim)
        self.lr = float(lr)
        self.gamma = float(gamma)
        self.gae_lambda = float(gae_lambda)
        self.clip_eps = float(clip_eps)
        self.entropy_coef = float(entropy_coef)
        self.value_coef = float(value_coef)
        self.seed = int(seed)
        self.actions = list(MANAGEMENT_ACTIONS)
        self.backend = "torch" if (use_torch if use_torch is not None else _HAS_TORCH) and _HAS_TORCH else "numpy"
        self.rng = np.random.default_rng(self.seed)

        if self.backend == "numpy" and not _HAS_TORCH:
            log.warning(
                "PPO using NumpyActorCritic linear fallback — torch not installed. "
                "Correct PPO clipped logic but linear not MLP. Fix: pip install torch --index-url https://download.pytorch.org/whl/cpu — error auto-clears when torch present."
            )
        elif self.backend == "numpy":
            log.info("PPO forced to numpy backend (use_torch=False) — linear policy, not MLP")

        if self.backend == "torch":
            torch.manual_seed(self.seed)
            self.model = ActorCritic(self.obs_dim, N_ACTIONS)
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
            self.numpy_model: Optional[NumpyActorCritic] = None
        else:
            self.model = None
            self.optimizer = None
            self.numpy_model = NumpyActorCritic(self.obs_dim, N_ACTIONS, seed=self.seed)

    def _obs(self, vector: Sequence[float]) -> np.ndarray:
        arr = np.asarray(vector, dtype=float).reshape(-1)
        if arr.shape[0] != self.obs_dim:
            # Pad / truncate so MT5 (11-d) can share a Kronos-sized head when needed.
            if arr.shape[0] < self.obs_dim:
                arr = np.pad(arr, (0, self.obs_dim - arr.shape[0]))
            else:
                arr = arr[: self.obs_dim]
        return arr

    def act(self, obs_vec: Sequence[float]) -> Tuple[int, float, float]:
        """Sample action; returns (action_index, log_prob, value)."""
        x = self._obs(obs_vec)
        if self.backend == "torch":
            assert self.model is not None
            with torch.no_grad():
                logits, value = self.model(torch.as_tensor(x, dtype=torch.float32))
                dist = torch.distributions.Categorical(logits=logits)
                action = int(dist.sample().item())
                log_prob = float(dist.log_prob(torch.tensor(action)).item())
                return action, log_prob, float(value.item())
        assert self.numpy_model is not None
        logits, value = self.numpy_model.logits_value(x)
        probs = _softmax(logits)
        action = int(self.rng.choice(N_ACTIONS, p=probs))
        log_prob = float(np.log(max(probs[action], 1e-12)))
        return action, log_prob, float(value)

    def _update_torch(
        self,
        obs: np.ndarray,
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
                a = torch.as_tensor(actions[batch], dtype=torch.int64)
                old_lp = torch.as_tensor(old_log_probs[batch], dtype=torch.float32)
                adv_b = torch.as_tensor(adv[batch], dtype=torch.float32)
                ret_b = torch.as_tensor(returns[batch], dtype=torch.float32)

                logits, values = self.model(o)
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
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
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
        actions: np.ndarray,
        old_log_probs: np.ndarray,
        advantages: np.ndarray,
        returns: np.ndarray,
        epochs: int,
    ) -> Dict[str, float]:
        """Clipped PPO-style gradient step on the linear numpy policy."""
        assert self.numpy_model is not None
        adv = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        last = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
        for _ in range(epochs):
            logits, values = self.numpy_model.logits_value(obs)
            probs = _softmax(logits)
            row = np.arange(len(actions))
            new_log_probs = np.log(np.clip(probs[row, actions], 1e-12, None))
            ratio = np.exp(new_log_probs - old_log_probs)
            surr1 = ratio * adv
            surr2 = np.clip(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * adv
            policy_loss = -np.minimum(surr1, surr2).mean()
            value_loss = float(np.mean((values - returns) ** 2))
            entropy = float(-(probs * np.log(np.clip(probs, 1e-12, None))).sum(axis=1).mean())

            # Policy gradient via softmax Jacobian approximation on clipped objective.
            chosen = np.zeros_like(probs)
            chosen[row, actions] = 1.0
            # Prefer the unclipped branch when it is the min (same as PPO).
            use_unclipped = surr1 <= surr2
            coeff = np.where(use_unclipped, adv, adv * ((ratio >= 1.0 - self.clip_eps) & (ratio <= 1.0 + self.clip_eps)))
            dlogits = (probs - chosen) * coeff.reshape(-1, 1)
            self.numpy_model.W_pi -= self.lr * (obs.T @ dlogits) / max(1, len(obs))
            self.numpy_model.b_pi -= self.lr * dlogits.mean(axis=0)
            # Value MSE gradient
            derr = (values - returns) / max(1, len(obs))
            self.numpy_model.W_v -= self.lr * self.value_coef * (obs.T @ derr)
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
        actions = np.asarray(batch["actions"], dtype=np.int64)
        old_log_probs = np.asarray(batch["log_probs"], dtype=float)
        advantages = np.asarray(batch["advantages"], dtype=float)
        returns = np.asarray(batch["returns"], dtype=float)
        if self.backend == "torch":
            return self._update_torch(obs, actions, old_log_probs, advantages, returns, epochs, batch_size)
        return self._update_numpy(obs, actions, old_log_probs, advantages, returns, epochs)

    def save_artifact(self, path: str, *, meta: Optional[Dict[str, Any]] = None) -> str:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "model_kind": MODEL_KIND,
            "backend": self.backend,
            "obs_dim": self.obs_dim,
            "actions": self.actions,
            "seed": self.seed,
            "hyperparameters": {
                "lr": self.lr,
                "gamma": self.gamma,
                "gae_lambda": self.gae_lambda,
                "clip_eps": self.clip_eps,
                "entropy_coef": self.entropy_coef,
                "value_coef": self.value_coef,
            },
            "execution_authority": False,
            "promotion_safe": False,
            "meta": meta or {},
        }
        if self.backend == "torch":
            assert self.model is not None
            payload["state_dict"] = {k: v.detach().cpu().tolist() for k, v in self.model.state_dict().items()}
        else:
            assert self.numpy_model is not None
            payload["state_dict"] = self.numpy_model.state_dict()
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return str(destination)

    def load_artifact(self, path: str) -> Dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if self.backend == "torch" and payload.get("backend") == "torch":
            assert self.model is not None
            tensors = {k: torch.as_tensor(v) for k, v in payload["state_dict"].items()}
            self.model.load_state_dict(tensors)
        elif self.numpy_model is not None and payload.get("backend") == "numpy":
            self.numpy_model.load_state_dict(payload["state_dict"])
        return payload


def _make_simulator(
    backend: str,
    *,
    symbol: str,
    regime: str,
    max_steps: int,
    seed: int,
):
    if backend == "mt5":
        from research.kronos_mt5_adapter import MT5KronosRLSimulator

        return MT5KronosRLSimulator(
            symbol=symbol, regime=regime, max_steps=max_steps, seed=seed
        )
    from research.kronos_trading_integrator import KronosRLMarketSimulator

    return KronosRLMarketSimulator(
        symbol=symbol, regime=regime, max_steps=max_steps, force_heuristic=True, seed=seed
    )


def collect_rollouts(
    trainer: PPOTrainer,
    *,
    episodes: int,
    symbol: str,
    regime: str,
    max_steps: int,
    seed: int,
    simulator: str = "kronos",
) -> Tuple[Dict[str, np.ndarray], List[Dict[str, Any]]]:
    """Roll out management episodes; returns PPO batch + episode summaries."""
    obs_list: List[np.ndarray] = []
    actions: List[int] = []
    log_probs: List[float] = []
    rewards: List[float] = []
    values: List[float] = []
    dones: List[bool] = []
    summaries: List[Dict[str, Any]] = []

    for ep in range(max(1, int(episodes))):
        ep_seed = int(seed) + ep
        sim = _make_simulator(simulator, symbol=symbol, regime=regime, max_steps=max_steps, seed=ep_seed)
        direction = 1.0 if (ep % 2 == 0) else -1.0
        obs = sim.open_management_episode(direction=direction, seed=ep_seed)
        ep_reward = 0.0
        steps = 0
        done = False
        info: Dict[str, Any] = {}
        while not done and steps < max_steps:
            vec = trainer._obs(sim.observation_vector(obs))
            action_i, log_p, value = trainer.act(vec)
            action_name = trainer.actions[action_i]
            next_obs, reward, done, info = sim.step(action_name)
            obs_list.append(vec)
            actions.append(action_i)
            log_probs.append(log_p)
            rewards.append(float(reward))
            values.append(float(value))
            dones.append(bool(done))
            ep_reward += float(reward)
            obs = next_obs
            steps += 1
            if abs(float(obs.get("position") or 0.0)) < 1e-9:
                # Position closed (exit / stop); end management episode.
                dones[-1] = True
                done = True
        if dones:
            dones[-1] = True  # Ensure episode boundary is closed for GAE
        summaries.append(
            {
                "episode": ep,
                "seed": ep_seed,
                "steps": steps,
                "reward": round(ep_reward, 4),
                "pnl": info.get("pnl") if isinstance(info, dict) else None,
                "direction": direction,
            }
        )

    advantages, returns = compute_gae(
        rewards, values, dones, gamma=trainer.gamma, lam=trainer.gae_lambda, last_value=0.0
    )
    batch = {
        "obs": np.asarray(obs_list, dtype=float),
        "actions": np.asarray(actions, dtype=np.int64),
        "log_probs": np.asarray(log_probs, dtype=float),
        "rewards": np.asarray(rewards, dtype=float),
        "values": np.asarray(values, dtype=float),
        "dones": np.asarray(dones, dtype=bool),
        "advantages": advantages,
        "returns": returns,
    }
    return batch, summaries


def train_ppo_offline(
    *,
    episodes: int = 4,
    updates: int = 2,
    max_steps: int = 20,
    symbol: str = "EURUSD",
    regime: str = "volatile",
    seed: int = 7,
    simulator: str = "kronos",
    artifact_path: str = "artifacts/ppo_position_mgmt.json",
    epochs: int = 2,
    batch_size: int = 32,
    use_torch: Optional[bool] = None,
) -> Dict[str, Any]:
    """Research-path entrypoint: offline PPO on Kronos/MT5 simulators.

    Always returns ``execution_authority=False`` and ``promotion_safe=False``.
    """
    sim_key = str(simulator or "kronos").lower()
    if sim_key not in {"kronos", "mt5"}:
        raise ValueError("simulator must be 'kronos' or 'mt5'")

    # MT5 observation vector is longer; size the head from a probe reset.
    probe = _make_simulator(sim_key, symbol=symbol, regime=regime, max_steps=max_steps, seed=seed)
    probe_obs = probe.open_management_episode(seed=seed)
    obs_dim = len(probe.observation_vector(probe_obs))

    trainer = PPOTrainer(obs_dim=obs_dim, seed=seed, use_torch=use_torch)
    update_metrics: List[Dict[str, Any]] = []
    all_episode_details: List[Dict[str, Any]] = []
    config_fingerprint = {
        "episodes": episodes,
        "updates": updates,
        "max_steps": max_steps,
        "symbol": symbol,
        "regime": regime,
        "seed": seed,
        "simulator": sim_key,
        "backend": trainer.backend,
        "model_kind": MODEL_KIND,
        "actions": list(MANAGEMENT_ACTIONS),
    }
    training_hash = hashlib.sha256(
        json.dumps(config_fingerprint, sort_keys=True).encode("utf-8")
    ).hexdigest()

    for update_i in range(max(1, int(updates))):
        batch, summaries = collect_rollouts(
            trainer,
            episodes=episodes,
            symbol=symbol,
            regime=regime,
            max_steps=max_steps,
            seed=seed + update_i * max(1, int(episodes)),
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
                **stats,
            }
        )
        all_episode_details.extend(summaries)

    report: Dict[str, Any] = {
        "status": "COMPLETED",
        "model_kind": MODEL_KIND,
        "training_data_hash": training_hash,
        "configuration": config_fingerprint,
        "updates": update_metrics,
        "episodes_detail": all_episode_details,
        "mean_episode_reward": (
            float(np.mean([e["reward"] for e in all_episode_details])) if all_episode_details else 0.0
        ),
        "artifact_written": False,
        "promotion_safe": False,
        "execution_authority": False,
        "source": "MT5KronosRLSimulator" if sim_key == "mt5" else "KronosRLMarketSimulator",
        "limitation": (
            "Offline PPO research artifact only; never grants live execution or promotion."
        ),
    }
    try:
        from operations.reward_sustainability import cvar_mixture_objective
        ep_rewards = [float(e["reward"]) for e in all_episode_details]
        report["sustainability_objective"] = cvar_mixture_objective(
            ep_rewards, alpha=0.05, mean_weight=0.5
        )
    except Exception as exc:
        report["sustainability_objective"] = {
            "status": "SKIPPED",
            "reason": str(exc)[:200],
            "execution_authority": False,
        }

    if artifact_path:
        path = trainer.save_artifact(
            artifact_path,
            meta={"training_data_hash": training_hash, "report_status": report["status"]},
        )
        report["artifact_written"] = True
        report["artifact_path"] = path
        report["artifact_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    # Session-style rollup for promotion evidence (single-task PPO runs).
    report["session_rollup"] = {
        "symbol": symbol,
        "regime": regime,
        "simulator": sim_key,
        "episodes": len(all_episode_details),
        "mean_episode_reward": report["mean_episode_reward"],
        "execution_authority": False,
    }

    log.info(
        "PPO offline training complete: updates=%s episodes=%s backend=%s",
        len(update_metrics),
        len(all_episode_details),
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
    parser = argparse.ArgumentParser(description="Offline PPO+GAE position-management trainer (research only)")
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--updates", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--regime", default="volatile")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--simulator", choices=("kronos", "mt5"), default="kronos")
    parser.add_argument("--artifact-path", default="artifacts/ppo_position_mgmt.json")
    parser.add_argument("--numpy-only", action="store_true", help="Force numpy policy even if torch is installed")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = train_ppo_offline(
        episodes=args.episodes,
        updates=args.updates,
        max_steps=args.max_steps,
        symbol=args.symbol,
        regime=args.regime,
        seed=args.seed,
        simulator=args.simulator,
        artifact_path=args.artifact_path,
        use_torch=False if args.numpy_only else None,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
