"""Offline GRPO for MarketModel proposer trajectories (Phase E).

Trajectory spine (research only)::

    macro ctx → opportunity candidate → proposal → frozen RiskController gate → outcome

GRPO updates **only** the proposer policy (BUY/SELL/HOLD). The RiskController
gate is frozen: no parameters, no gradients, no ``order_authority`` mutation.
Every public result hard-codes ``execution_authority=False`` and
``promotion_safe=False``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from reward_ledger import compute_terminal_reward
from reward_sustainability import cvar_mixture_objective

log = logging.getLogger("GRPOProposer")

MODEL_KIND = "grpo_marketmodel_proposer_v1"
PROPOSER_ACTIONS: Tuple[str, ...] = ("BUY", "SELL", "HOLD")
N_ACTIONS = len(PROPOSER_ACTIONS)
CTX_DIM = 10  # macro + candidate + gate-visible features
EPSILON_SAFE = 0.05
PROCESS_HOLD = 0.0
R_CLIP = 5.0
TRAINABLE_AGENT = "MarketModel"
FROZEN_AGENT = "RiskController"


from research.utils import softmax as _softmax, group_relative_advantages  # noqa: F401


class FrozenRiskGate:
    """Deterministic RiskController stand-in — never trainable."""

    def __init__(self) -> None:
        self.trainable = False
        self.order_authority = True  # contract role only; this class never places orders
        self.execution_authority = False

    def evaluate(self, ctx: Dict[str, Any], action: str) -> Dict[str, Any]:
        action_u = str(action or "HOLD").upper()
        reasons: List[str] = []
        if ctx.get("blackout") and action_u in {"BUY", "SELL"}:
            reasons.append("event_blackout")
        try:
            confluence = float(ctx.get("confluence") or 0.0)
        except (TypeError, ValueError):
            confluence = 0.0
        if action_u in {"BUY", "SELL"} and confluence < float(ctx.get("min_confluence", 4.0)):
            reasons.append("confluence_below_floor")
        required = str(ctx.get("required_side") or "").upper()
        if required in {"BUY", "SELL"} and action_u in {"BUY", "SELL"} and action_u != required:
            reasons.append("side_mismatch_vs_candidate")
        if ctx.get("kill_switch") and action_u in {"BUY", "SELL"}:
            reasons.append("kill_switch")
        approved = action_u == "HOLD" or not reasons
        # HOLD always "passes" the gate as a no-order decision.
        if action_u == "HOLD":
            approved = True
            reasons = []
        return {
            "approved": approved,
            "action": action_u,
            "reasons": reasons,
            "agent": FROZEN_AGENT,
            "trainable": False,
            "execution_authority": False,
            "order_submitted": False,
        }


class NumpyProposerPolicy:
    """Linear softmax proposer policy (numpy). No value head — GRPO is critic-free."""

    def __init__(self, obs_dim: int = CTX_DIM, n_actions: int = N_ACTIONS, seed: int = 0):
        rng = np.random.default_rng(seed)
        scale = 0.1
        self.obs_dim = int(obs_dim)
        self.n_actions = int(n_actions)
        self.W = rng.normal(0.0, scale, size=(self.obs_dim, self.n_actions))
        self.b = np.zeros(self.n_actions, dtype=float)

    def logits(self, obs: np.ndarray) -> np.ndarray:
        single = obs.ndim == 1
        x = obs.reshape(1, -1) if single else obs
        out = x @ self.W + self.b
        return out[0] if single else out

    def probs(self, obs: np.ndarray) -> np.ndarray:
        return _softmax(self.logits(obs))

    def sample(self, obs: np.ndarray, rng: np.random.Generator) -> Tuple[int, float, np.ndarray]:
        probs = self.probs(obs)
        action = int(rng.choice(self.n_actions, p=probs))
        log_prob = float(np.log(max(probs[action], 1e-12)))
        return action, log_prob, probs

    def state_dict(self) -> Dict[str, Any]:
        return {"W": self.W.tolist(), "b": self.b.tolist(), "obs_dim": self.obs_dim, "n_actions": self.n_actions}

    def load_state_dict(self, data: Dict[str, Any]) -> None:
        self.W = np.asarray(data["W"], dtype=float)
        self.b = np.asarray(data["b"], dtype=float)


def context_vector(ctx: Dict[str, Any]) -> np.ndarray:
    """Encode macro/candidate/gate-visible fields into a fixed vector."""
    def f(key: str, default: float = 0.0) -> float:
        try:
            return float(ctx.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    side = str(ctx.get("candidate_side") or ctx.get("required_side") or "NONE").upper()
    side_enc = 1.0 if side == "BUY" else (-1.0 if side == "SELL" else 0.0)
    return np.asarray(
        [
            f("macro_surprise"),
            f("causal_strength"),
            f("candidate_score"),
            side_enc,
            f("confluence"),
            1.0 if ctx.get("blackout") else 0.0,
            1.0 if ctx.get("kill_switch") else 0.0,
            f("session_liquidity", 0.5),
            f("spread_r"),
            f("latent_edge_r"),  # research sim only; live must not feed look-ahead
        ],
        dtype=float,
    )


def synthesize_contexts(
    n: int,
    *,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """Offline synthetic trajectory contexts (macro → candidate → gate inputs)."""
    rng = np.random.default_rng(seed)
    out: List[Dict[str, Any]] = []
    for i in range(max(0, int(n))):
        edge_dir = int(rng.choice([-1, 1]))
        edge_r = float(rng.normal(0.4 * edge_dir, 0.8))
        blackout = bool(rng.random() < 0.12)
        kill = bool(rng.random() < 0.03)
        candidate_side = "BUY" if edge_dir > 0 else "SELL"
        # Occasionally noisy candidate side.
        if rng.random() < 0.2:
            candidate_side = "BUY" if candidate_side == "SELL" else "SELL"
        ctx = {
            "context_id": f"ctx-{seed}-{i}",
            "macro_surprise": float(rng.normal(edge_dir * 0.3, 0.5)),
            "causal_strength": float(np.clip(abs(edge_r) / 2.0 + rng.normal(0, 0.1), 0, 1)),
            "candidate_score": float(np.clip(0.5 + abs(edge_r) * 0.2 + rng.normal(0, 0.15), 0, 1)),
            "candidate_side": candidate_side,
            "required_side": candidate_side if rng.random() < 0.7 else "",
            "confluence": float(np.clip(rng.normal(5.5, 1.5), 0, 10)),
            "min_confluence": 4.0,
            "blackout": blackout,
            "kill_switch": kill,
            "session_liquidity": float(np.clip(rng.random(), 0.1, 1.0)),
            "spread_r": float(abs(rng.normal(0.05, 0.02))),
            "latent_edge_r": edge_r,
            "latent_edge_dir": edge_dir,
            "execution_authority": False,
        }
        out.append(ctx)
    return out


def score_trajectory(
    ctx: Dict[str, Any],
    action: str,
    gate_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Outcome-level reward for one proposer sample (RiskController frozen)."""
    action_u = str(action or "HOLD").upper()
    # HOLD during blackout is never penalized.
    if ctx.get("blackout") and action_u == "HOLD":
        return {
            "reward": PROCESS_HOLD,
            "kind": "blackout_hold",
            "broker_reconciled": False,
            "execution_authority": False,
        }
    if action_u == "HOLD":
        # Missed-edge penalty only when strong positive OOS edge and no blackout.
        edge = float(ctx.get("latent_edge_r") or 0.0)
        if abs(edge) >= 1.0 and not ctx.get("blackout"):
            return {
                "reward": -0.03,
                "kind": "missed_edge_offline",
                "execution_authority": False,
            }
        return {
            "reward": PROCESS_HOLD,
            "kind": "hold",
            "execution_authority": False,
        }

    if not gate_result.get("approved"):
        reasons = gate_result.get("reasons") or []
        # Correct reject of a dangerous proposal → small safety credit.
        latent_dir = int(ctx.get("latent_edge_dir") or 0)
        wrong_side = (action_u == "BUY" and latent_dir < 0) or (action_u == "SELL" and latent_dir > 0)
        if wrong_side or "event_blackout" in reasons or "kill_switch" in reasons:
            return {
                "reward": EPSILON_SAFE,
                "kind": "correct_reject",
                "reasons": reasons,
                "execution_authority": False,
            }
        return {
            "reward": -EPSILON_SAFE,
            "kind": "quality_reject",
            "reasons": reasons,
            "execution_authority": False,
        }

    # Approved fill → simulated cost-aware R (research path; marked reconciled for ledger math).
    edge = float(ctx.get("latent_edge_r") or 0.0)
    align = 1.0 if (
        (action_u == "BUY" and edge >= 0) or (action_u == "SELL" and edge < 0)
    ) else -1.0
    # Noise around latent edge; spread haircut.
    spread = float(ctx.get("spread_r") or 0.0)
    r_net = align * abs(edge) - spread
    trade = {
        "pnl": r_net * 10.0,
        "risk": 10.0,
        "r_multiple": r_net,
        "broker_reconciled": True,
        "confluence_score": ctx.get("confluence", 0),
    }
    breakdown = compute_terminal_reward(trade, r_clip=R_CLIP)
    return {
        "reward": float(breakdown.get("reward") or 0.0),
        "kind": "terminal_simulated",
        "r_net": breakdown.get("r_net"),
        "breakdown": breakdown,
        "broker_reconciled": True,
        "execution_authority": False,
    }


class GRPOProposerTrainer:
    """Group Relative Policy Optimization for MarketModel proposals only."""

    def __init__(
        self,
        *,
        obs_dim: int = CTX_DIM,
        lr: float = 3e-3,
        clip_eps: float = 0.2,
        entropy_coef: float = 0.02,
        seed: int = 0,
    ):
        self.obs_dim = int(obs_dim)
        self.lr = float(lr)
        self.clip_eps = float(clip_eps)
        self.entropy_coef = float(entropy_coef)
        self.rng = np.random.default_rng(seed)
        self.policy = NumpyProposerPolicy(obs_dim=self.obs_dim, seed=seed)
        self.gate = FrozenRiskGate()
        self.trainable_agent = TRAINABLE_AGENT
        self.frozen_agent = FROZEN_AGENT

    def act(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        obs = context_vector(ctx)
        action_i, log_prob, probs = self.policy.sample(obs, self.rng)
        action = PROPOSER_ACTIONS[action_i]
        gate = self.gate.evaluate(ctx, action)
        scored = score_trajectory(ctx, action, gate)
        return {
            "obs": obs,
            "action": action,
            "action_index": action_i,
            "log_prob": log_prob,
            "probs": probs,
            "gate": gate,
            "reward": float(scored["reward"]),
            "score": scored,
            "agent": TRAINABLE_AGENT,
            "execution_authority": False,
        }

    def collect_group(
        self,
        ctx: Dict[str, Any],
        *,
        group_size: int = 4,
    ) -> Dict[str, Any]:
        samples = [self.act(ctx) for _ in range(max(1, int(group_size)))]
        rewards = [float(s["reward"]) for s in samples]
        advantages = group_relative_advantages(rewards)
        for i, sample in enumerate(samples):
            sample["advantage"] = float(advantages[i])
        return {
            "context_id": ctx.get("context_id"),
            "samples": samples,
            "rewards": rewards,
            "advantages": advantages.tolist(),
            "mean_reward": float(np.mean(rewards)) if rewards else 0.0,
            "execution_authority": False,
            "risk_controller_frozen": True,
        }

    def update(self, groups: Sequence[Dict[str, Any]], *, epochs: int = 2) -> Dict[str, float]:
        """Clipped GRPO policy step on proposer weights only."""
        obs_list: List[np.ndarray] = []
        actions: List[int] = []
        old_log_probs: List[float] = []
        advantages: List[float] = []
        for group in groups:
            for sample in group.get("samples") or []:
                obs_list.append(np.asarray(sample["obs"], dtype=float))
                actions.append(int(sample["action_index"]))
                old_log_probs.append(float(sample["log_prob"]))
                advantages.append(float(sample["advantage"]))
        if not obs_list:
            return {"policy_loss": 0.0, "entropy": 0.0, "n": 0.0}

        obs = np.asarray(obs_list, dtype=float)
        act = np.asarray(actions, dtype=np.int64)
        old_lp = np.asarray(old_log_probs, dtype=float)
        adv = np.asarray(advantages, dtype=float)
        # Extra standardize across batch for stability.
        if adv.std() > 1e-8:
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        last = {"policy_loss": 0.0, "entropy": 0.0, "n": float(len(obs))}
        for _ in range(max(1, int(epochs))):
            logits = self.policy.logits(obs)
            probs = _softmax(logits)
            row = np.arange(len(act))
            new_lp = np.log(np.clip(probs[row, act], 1e-12, None))
            ratio = np.exp(new_lp - old_lp)
            surr1 = ratio * adv
            surr2 = np.clip(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * adv
            policy_loss = -np.minimum(surr1, surr2).mean()
            entropy = float(-(probs * np.log(np.clip(probs, 1e-12, None))).sum(axis=1).mean())

            chosen = np.zeros_like(probs)
            chosen[row, act] = 1.0
            use_unclipped = surr1 <= surr2
            coeff = np.where(
                use_unclipped,
                adv,
                adv * ((ratio >= 1.0 - self.clip_eps) & (ratio <= 1.0 + self.clip_eps)),
            )
            dlogits = (probs - chosen) * coeff.reshape(-1, 1)
            # Entropy bonus gradient (encourage exploration).
            dlogits = dlogits - self.entropy_coef * (np.log(np.clip(probs, 1e-12, None)) + 1.0) / max(1, len(obs))
            self.policy.W -= self.lr * (obs.T @ dlogits) / max(1, len(obs))
            self.policy.b -= self.lr * dlogits.mean(axis=0)
            last = {"policy_loss": float(policy_loss), "entropy": entropy, "n": float(len(obs))}
        return last

    def save_artifact(self, path: str, *, meta: Optional[Dict[str, Any]] = None) -> str:
        payload = {
            "model_kind": MODEL_KIND,
            "trainable_agent": TRAINABLE_AGENT,
            "frozen_agent": FROZEN_AGENT,
            "actions": list(PROPOSER_ACTIONS),
            "policy": self.policy.state_dict(),
            "execution_authority": False,
            "promotion_safe": False,
            "risk_controller_frozen": True,
            "meta": meta or {},
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


def train_grpo_offline(
    *,
    contexts: int = 8,
    group_size: int = 4,
    updates: int = 2,
    epochs: int = 2,
    seed: int = 7,
    artifact_path: str = "artifacts/grpo_proposer.json",
) -> Dict[str, Any]:
    """Research entrypoint: GRPO on proposer; RiskController frozen."""
    trainer = GRPOProposerTrainer(seed=seed)
    ctxs = synthesize_contexts(contexts, seed=seed)
    config = {
        "contexts": contexts,
        "group_size": group_size,
        "updates": updates,
        "epochs": epochs,
        "seed": seed,
        "model_kind": MODEL_KIND,
        "trainable_agent": TRAINABLE_AGENT,
        "frozen_agent": FROZEN_AGENT,
        "actions": list(PROPOSER_ACTIONS),
    }
    training_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()

    update_metrics: List[Dict[str, Any]] = []
    all_group_means: List[float] = []
    all_rewards: List[float] = []

    for update_i in range(max(1, int(updates))):
        # Resample contexts each update with offset seed for diversity.
        batch_ctxs = synthesize_contexts(contexts, seed=seed + update_i * 17)
        groups = [trainer.collect_group(ctx, group_size=group_size) for ctx in batch_ctxs]
        stats = trainer.update(groups, epochs=epochs)
        means = [float(g["mean_reward"]) for g in groups]
        rewards = [r for g in groups for r in g["rewards"]]
        all_group_means.extend(means)
        all_rewards.extend(rewards)
        update_metrics.append({
            "update": update_i,
            "groups": len(groups),
            "mean_group_reward": float(np.mean(means)) if means else 0.0,
            **stats,
            "risk_controller_frozen": True,
            "execution_authority": False,
        })

    report: Dict[str, Any] = {
        "status": "COMPLETED",
        "model_kind": MODEL_KIND,
        "training_data_hash": training_hash,
        "configuration": config,
        "updates": update_metrics,
        "mean_group_reward": float(np.mean(all_group_means)) if all_group_means else 0.0,
        "sample_contexts": len(ctxs),
        "trainable_agent": TRAINABLE_AGENT,
        "frozen_agent": FROZEN_AGENT,
        "risk_controller_frozen": True,
        "promotion_safe": False,
        "execution_authority": False,
        "limitation": (
            "Offline GRPO research artifact for MarketModel proposals only; "
            "RiskController remains frozen and never grants live order authority."
        ),
    }
    try:
        report["sustainability_objective"] = cvar_mixture_objective(
            all_rewards, alpha=0.05, mean_weight=0.5
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
    else:
        report["artifact_written"] = False

    log.info(
        "GRPO offline complete: updates=%s mean_group_reward=%.4f frozen=%s",
        len(update_metrics),
        report["mean_group_reward"],
        FROZEN_AGENT,
    )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Offline GRPO MarketModel proposer trainer (research only)")
    parser.add_argument("--contexts", type=int, default=8)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--updates", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--artifact-path", default="artifacts/grpo_proposer.json")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = train_grpo_offline(
        contexts=args.contexts,
        group_size=args.group_size,
        updates=args.updates,
        epochs=args.epochs,
        seed=args.seed,
        artifact_path=args.artifact_path,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
