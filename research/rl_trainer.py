#!/usr/bin/env python3
"""
RL Trainer - Reinforcement Learning for Trading
Uses Q-learning to learn profitable setups from demo trading
"""

import os
import json
import random
import logging
import tempfile
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from collections import defaultdict


class RLTrainer:
    """
    Q-Learning based RL trainer for trading
    Learns from actual trading results to identify profitable patterns
    """
    
    def __init__(self, learning_rate: float = 0.1, discount_factor: float = 0.95, 
                 epsilon: float = 0.2, model_path: Optional[str] = None):
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.epsilon = epsilon  # Exploration rate
        
        # Anchor relative default model_path to project root if not in CWD
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if model_path is None:
            model_path = os.path.join(repo_root, "rl_model.json")
        elif not os.path.isabs(model_path):
            if not os.path.exists(model_path):
                candidate = os.path.join(repo_root, model_path)
                if os.path.exists(candidate) or not os.path.exists(model_path):
                    model_path = candidate
        self.model_path = model_path
        
        # Q-table: state_key -> {action: q_value}
        self.q_table = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0})
        
        # Training history
        self.training_data = []
        self.episode_rewards = []
        # Epsilon actually in force for each recorded episode. Recorded, not
        # reconstructed: epsilon decays (decay_epsilon), so back-filling the
        # current scalar across past episodes would invent a flat exploration
        # curve and hide the real decay.
        self.episode_epsilons: List[float] = []
        
        # Statistics
        self.total_updates = 0
        self.exploration_count = 0
        self.exploitation_count = 0
        
        self.log = logging.getLogger("RLTrainer")

        # EWC (Elastic Weight Consolidation) — catastrophic forgetting prevention
        self._ewc = None
        try:
            from core.ewc_forgetting_prevention import EWCForgettingPrevention
            self._ewc = EWCForgettingPrevention(state_file="ewc_state.json")
        except Exception as exc:
            self.log.debug("EWC initialization skipped: %s", exc)

        # Load existing model if available
        self.load_model()
    
    @property
    def rewards_history(self) -> List[float]:
        """Backward-compatibility alias for episode_rewards."""
        return self.episode_rewards

    @property
    def epsilon_history(self) -> List[float]:
        """Per-episode exploration rate, as recorded. Empty until trained.

        Historically this returned ``[self.epsilon]`` -- a single *current*
        scalar, not a trace. Any consumer zipping it against the reward series
        silently truncated the learning curve to one point, and padding it out
        would have fabricated a flat curve over a real decay. Unknown stays
        unknown; see ``_numeric_bin`` for the same rule applied to features.
        """
        return list(self.episode_epsilons)


    def _discretize_value(self, value: float, bins: List[float]) -> str:
        """Discretize continuous value into bins"""
        for i, threshold in enumerate(bins):
            if value < threshold:
                return f"bin_{i}"
        return f"bin_{len(bins)}"

    def _numeric_bin(self, value: Any, bins: List[float]) -> str:
        """Return an explicit unknown bin for missing/non-numeric features.

        Feature snapshots are allowed to be incomplete when a broker feed,
        calendar, or indicator calculation is unavailable.  Treating ``None``
        as zero would turn an unknown market state into a confident neutral
        state and make offline learning labels misleading.  The state key
        therefore preserves an ``unknown`` bucket instead.
        """
        if value is None or isinstance(value, bool):
            return "unknown"
        try:
            return self._discretize_value(float(value), bins)
        except (TypeError, ValueError):
            return "unknown"

    def _sign_bin(self, value: Any) -> str:
        """Encode a signed feature while preserving missing-data provenance."""
        if value is None or isinstance(value, bool):
            return "unknown"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "unknown"
        return "pos" if numeric > 0 else "neg"
    
    def snapshot_for_ewc(self, regime: str) -> None:
        """Snapshot Q-table for EWC forgetting prevention.

        Call this when a regime transition is detected (e.g., from
        meta_adaptive_engine) so that EWC can preserve important
        Q-values from the old regime.
        """
        if not self._ewc:
            return
        # Convert Q-table to serializable format
        q_dict = {k: dict(v) for k, v in self.q_table.items()}
        visit_counts = {}
        for state_key, q_values in self.q_table.items():
            visit_counts[state_key] = {
                action: max(1, int(abs(qv) * 10))
                for action, qv in q_values.items()
            }
        self._ewc.snapshot_regime(
            regime=regime,
            q_table=q_dict,
            visit_counts=visit_counts,
            total_updates=self.total_updates,
        )
        self.log.info(
            "EWC snapshot: regime=%s, states=%d",
            regime, len(self.q_table),
        )

    def get_ewc_status(self) -> Dict[str, Any]:
        """Get EWC forgetting prevention status."""
        if not self._ewc:
            return {"available": False}
        return {"available": True, **self._ewc.get_status()}

    def _build_state_key(self, state: Dict[str, Any]) -> str:
        """
        Convert state dict to hashable key by discretizing continuous values.

        Keys are aligned with the schema produced by
        ``intelligence.llm_orchestrator._build_rl_state``:
        rsi_14, macd_histogram, ma_alignment, atr_normalized,
        confluence_score, near_sr, news_sentiment, volatility_regime,
        trend_strength, htf_trend, session.
        """
        try:
            # Volatility: use atr_normalized (produced by _build_rl_state).
            # Offline Kronos episodes produce the same volatility measure under
            # ``volatility_range`` (real bar range of the simulated series), so
            # fall back to it instead of emitting an "unknown" bin that no live
            # state could ever match.
            atr_norm = state.get("atr_normalized")
            if atr_norm is None:
                atr_norm = state.get("volatility_range")
            vol_bin = self._numeric_bin(atr_norm, [0.003, 0.008]) if atr_norm is not None else "unknown"

            # Direction / MA alignment
            direction = state.get("direction", state.get("ma_alignment", 0))  # -1, 0, 1

            # RSI binned into oversold / neutral / overbought
            rsi = state.get("rsi_14")
            rsi_bin = self._numeric_bin(rsi, [30.0, 50.0, 70.0]) if rsi is not None else "unknown"

            # Confluence
            confluence_score = state.get("confluence_score")
            confluence_bin = self._numeric_bin(confluence_score, [3, 6])

            near_sr = "1" if state.get("near_sr", False) else "0"
            htf_trend = state.get("htf_trend", 0)  # -1, 0, 1

            # News sentiment
            news_sentiment = state.get("news_sentiment")
            news_bin = self._numeric_bin(news_sentiment, [-0.3, 0.3])

            # Market regime
            volatility = state.get("volatility_regime", "medium")
            trend_strength = state.get("trend_strength")
            trend_bin = self._numeric_bin(trend_strength, [0.3, 0.7])

            # Time features.  Only real session buckets are canonical; the
            # offline simulator's "sim" label (and any other placeholder) maps
            # to "unknown" so the backoff lookup below can bridge sessions
            # rather than inventing a fake session.
            session = str(state.get("session", "unknown") or "unknown").lower()
            if session not in {"asia", "london", "ny", "overlap"}:
                session = "unknown"

            # Build compact state key (includes rsi_bin, drops phantom bar_pos)
            state_key = (
                f"vol:{vol_bin}|rsi:{rsi_bin}|dir:{direction}"
                f"|conf:{confluence_bin}|sr:{near_sr}|htf:{htf_trend}"
                f"|news:{news_bin}|regime:{volatility}|trend:{trend_bin}"
                f"|sess:{session}"
            )

            return state_key
            
        except Exception as e:
            self.log.warning(f"Error building state key: {e}")
            return "default_state"
    
    def select_action(self, state: Dict[str, Any], explore: bool = True) -> Tuple[str, bool]:
        """
        Select action using epsilon-greedy policy
        
        Returns:
            (action, was_exploration): Action and whether it was exploration
        """
        state_key = self._build_state_key(state)
        
        # Epsilon-greedy exploration
        if explore and random.random() < self.epsilon:
            action = random.choice(["BUY", "SELL", "HOLD"])
            self.exploration_count += 1
            return action, True
        else:
            # Exploit: choose best action
            q_values = self.q_table[state_key]
            action = max(q_values, key=q_values.get)
            self.exploitation_count += 1
            return action, False

    def q_values_for_state(
        self, state: Dict[str, Any], allow_backoff: bool = True
    ) -> Tuple[Optional[Dict[str, float]], str]:
        """Return learned Q-values for ``state`` plus the key that matched.

        Offline Kronos episodes carry synthetic session labels; live sessions
        are real.  Rather than shrinking both policies to a common fake
        session, an unseen exact key backs off to the same state with the
        session dimension masked to ``unknown`` (the bucket offline training
        uses).  This is genuine generalisation over a nuisance dimension, not
        a reward invention: if no learned key matches, ``(None, key)`` is
        returned and callers must treat the policy as having no opinion.
        """
        key = self._build_state_key(state)
        try:
            exact = self.q_table.get(key)
        except Exception:
            exact = None
        if exact:
            return dict(exact), key
        if not allow_backoff:
            return None, key
        parts = key.split("|")
        masked = "|".join(
            "sess:unknown" if p.startswith("sess:") else p for p in parts
        )
        if masked == key:
            return None, key
        try:
            backed_off = self.q_table.get(masked)
        except Exception:
            backed_off = None
        if backed_off:
            return dict(backed_off), masked
        return None, key

    def policy_advantage(
        self, state: Dict[str, Any], action: str
    ) -> Optional[Dict[str, Any]]:
        """Q-advantage of ``action`` over the best alternative, if learned.

        Returns ``None`` when the state has no learned Q-values or the action
        is not tradable.  The caller decides how (and whether) to use the
        advantage; this method grants no authority.
        """
        action_u = str(action or "").upper()
        if action_u not in {"BUY", "SELL"}:
            return None
        q_values, key = self.q_values_for_state(state)
        if not q_values:
            return None
        try:
            chosen = float(q_values.get(action_u, 0.0))
            alternatives = [
                float(v) for a, v in q_values.items() if str(a).upper() != action_u
            ]
            if not alternatives:
                return None
            best_alternative = max(alternatives)
            spread = max(q_values.values()) - min(q_values.values())
        except (TypeError, ValueError):
            return None
        return {
            "action": action_u,
            "advantage": chosen - best_alternative,
            "q_values": {str(k).upper(): float(v) for k, v in q_values.items()},
            "state_key": key,
            "spread": float(spread),
            "updates": int(self.total_updates),
            "execution_authority": False,
        }
    
    def calculate_reward(self, trade_result: Dict[str, Any]) -> float:
        """Terminal reward from broker-reconciled close (cost-aware R_net).

        Process bonuses are capped so they cannot overwhelm a full stop-out.
        Unreconciled closes return 0.0 and must not update the Q-table upstream.
        """
        breakdown = self.calculate_reward_breakdown(trade_result)
        return float(breakdown.get("reward") or 0.0)

    def calculate_reward_breakdown(self, trade_result: Dict[str, Any]) -> Dict[str, Any]:
        """Full terminal reward breakdown for journaling / reward_events."""
        from operations.reward_ledger import compute_terminal_reward

        # Fail closed unless caller marked the close as broker-reconciled.
        payload = dict(trade_result or {})
        if "broker_reconciled" not in payload:
            payload["broker_reconciled"] = False
        # Map common aliases used by update_rl_from_trade.
        if payload.get("risk") is None and payload.get("risk_amount") is not None:
            payload["risk"] = payload.get("risk_amount")
        return compute_terminal_reward(payload)    
    def update(self, state: Dict[str, Any], action: str, reward: float, 
               next_state: Dict[str, Any], done: bool = False):
        """
        Update Q-table using Q-learning formula
        
        Q(s,a) = Q(s,a) + α * [r + γ * max(Q(s',a')) - Q(s,a)]
        """
        state_key = self._build_state_key(state)
        next_state_key = self._build_state_key(next_state)
        
        # Current Q-value
        current_q = self.q_table[state_key][action]
        
        # Max Q-value for next state
        if done:
            max_next_q = 0  # Terminal state
        else:
            max_next_q = max(self.q_table[next_state_key].values())
        
        # Q-learning update (with EWC if available)
        if self._ewc and self._ewc.snapshots:
            # EWC-modified: penalizes deviation from important past Q-values
            new_q = self._ewc.ewc_update(
                state_key=state_key,
                action=action,
                reward=reward,
                max_next_q=max_next_q,
                learning_rate=self.learning_rate,
                discount_factor=self.discount_factor,
                current_q=current_q,
            )
        else:
            new_q = current_q + self.learning_rate * (
                reward + self.discount_factor * max_next_q - current_q
            )

        self.q_table[state_key][action] = new_q
        
        # Track statistics
        self.total_updates += 1
        self.episode_rewards.append(reward)
        # The exploration rate that governed this update, captured before any
        # decay. Keeps epsilon_history index-aligned with episode_rewards.
        self.episode_epsilons.append(float(self.epsilon))
        
        # Store training sample
        self.training_data.append({
            "state_key": state_key,
            "action": action,
            "reward": reward,
            "q_value": new_q,
            "timestamp": datetime.now().isoformat()
        })
        
        self.log.debug(f"RL Update: {state_key[:50]}... | {action} | R={reward:.3f} | Q={new_q:.3f}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get training statistics"""
        recent_rewards = self.episode_rewards[-100:] if self.episode_rewards else [0]
        
        return {
            "total_updates": self.total_updates,
            "exploration_count": self.exploration_count,
            "exploitation_count": self.exploitation_count,
            "exploration_rate": self.exploration_count / max(self.total_updates, 1),
            "q_table_size": len(self.q_table),
            "avg_reward_100": sum(recent_rewards) / len(recent_rewards),
            "total_reward": sum(self.episode_rewards),
            "best_reward": max(self.episode_rewards) if self.episode_rewards else 0,
            "worst_reward": min(self.episode_rewards) if self.episode_rewards else 0
        }
    
    def get_best_states(self, top_n: int = 10) -> List[Tuple[str, Dict[str, float]]]:
        """Get states with highest Q-values"""
        state_values = []
        
        for state_key, q_values in self.q_table.items():
            max_q = max(q_values.values())
            state_values.append((state_key, q_values, max_q))
        
        # Sort by max Q-value
        state_values.sort(key=lambda x: x[2], reverse=True)
        
        return [(state, q_vals) for state, q_vals, _ in state_values[:top_n]]
    
    def save_model(self, path: str = None):
        """Save Q-table and training data"""
        if path is None:
            path = self.model_path
        
        try:
            # Convert defaultdict to regular dict for JSON serialization
            q_table_dict = {k: dict(v) for k, v in self.q_table.items()}
            
            data = {
                "q_table": q_table_dict,
                "training_data": self.training_data[-1000:],  # Last 1000 samples
                "episode_rewards": self.episode_rewards[-1000:],
                "episode_epsilons": self.episode_epsilons[-1000:],
                "stats": self.get_stats(),
                "hyperparameters": {
                    "learning_rate": self.learning_rate,
                    "discount_factor": self.discount_factor,
                    "epsilon": self.epsilon
                },
                "saved_at": datetime.now().isoformat()
            }
            
            # Write atomically. A plain open(path,'w') truncates in place, so a
            # crash or full disk mid-dump left a torn JSON file and the entire
            # learned Q-table was unrecoverable - the next boot started from
            # zero with no fallback. Temp file + os.replace() means the reader
            # (this process at __init__, or the next boot) only ever sees a
            # complete document.
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=directory, prefix=".rl_model-", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except Exception:
                # Never leave a stray temp file behind on failure.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            self.log.info(f"RL model saved to {path} ({len(self.q_table)} states)")

        except Exception as e:
            self.log.error(f"Failed to save RL model: {e}")
    
    def load_model(self, path: str = None):
        """Load existing Q-table"""
        if path is None:
            path = self.model_path
        
        if not os.path.exists(path):
            self.log.info("No existing RL model found. Starting fresh.")
            return
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            # Load Q-table
            q_table_dict = data.get("q_table", {})
            self.q_table = defaultdict(
                lambda: {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0},
                {k: v for k, v in q_table_dict.items()}
            )
            
            # Load training history
            self.training_data = data.get("training_data", [])
            self.episode_rewards = data.get("episode_rewards", [])
            # Older checkpoints predate the trace; load nothing rather than
            # back-filling, and re-align if a truncated write left the two
            # series different lengths so they stay index-comparable.
            self.episode_epsilons = list(data.get("episode_epsilons", []))
            if len(self.episode_epsilons) > len(self.episode_rewards):
                self.episode_epsilons = self.episode_epsilons[-len(self.episode_rewards):] if self.episode_rewards else []
            
            # Load stats
            stats = data.get("stats", {})
            self.total_updates = stats.get("total_updates", 0)
            self.exploration_count = stats.get("exploration_count", 0)
            self.exploitation_count = stats.get("exploitation_count", 0)

            # Restore the hyperparameters save_model wrote. Without this a
            # trained model silently resumed at the constructor defaults,
            # discarding the learned epsilon decay and contradicting the
            # recorded episode_epsilons trace.
            hyper = data.get("hyperparameters", {})
            for name in ("learning_rate", "discount_factor", "epsilon"):
                value = hyper.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    try:
                        setattr(self, name, float(value))
                    except (TypeError, ValueError, OverflowError):
                        pass
            
            self.log.info(f"RL model loaded from {path} ({len(self.q_table)} states, {self.total_updates} updates)")
            
        except Exception as e:
            self.log.error(f"Failed to load RL model: {e}")
    
    def decay_epsilon(self, min_epsilon: float = 0.05, decay_rate: float = 0.995):
        """Decay exploration rate over time"""
        self.epsilon = max(min_epsilon, self.epsilon * decay_rate)
        self.log.debug(f"Epsilon decayed to {self.epsilon:.4f}")

    def train_on_kronos_simulator(
        self,
        episodes: int = 5,
        *,
        symbol: str = "EURUSD",
        regime: str = "volatile",
        max_steps: int = 40,
        explore: bool = True,
        save: bool = False,
    ) -> Dict[str, Any]:
        """Offline Q-learning on KronosRLMarketSimulator synthetic paths.

        Research-only: never grants broker execution authority.  Safe to run
        without neural Kronos weights (simulator uses heuristic regimes).
        """
        from research.kronos_trading_integrator import KronosRLMarketSimulator

        sim = KronosRLMarketSimulator(
            symbol=symbol,
            regime=regime,
            max_steps=max_steps,
            force_heuristic=True,
        )
        episode_summaries: List[Dict[str, Any]] = []
        for episode in range(max(1, int(episodes))):
            obs = sim.reset()
            state = sim.observation_to_rl_state(obs)
            done = False
            steps = 0
            ep_reward = 0.0
            while not done and steps < max_steps + 5:
                action, _ = self.select_action(state, explore=explore)
                next_obs, reward, done, info = sim.step(action)
                next_state = sim.observation_to_rl_state(next_obs)
                self.update(state, action, float(reward), next_state, done=done)
                ep_reward += float(reward)
                state = next_state
                steps += 1
            episode_summaries.append(
                {
                    "episode": episode,
                    "steps": steps,
                    "reward": round(ep_reward, 4),
                    "pnl": info.get("pnl") if isinstance(info, dict) else None,
                }
            )
            self.decay_epsilon()

        if save:
            self.save_model()

        result = {
            "status": "COMPLETED",
            "episodes": len(episode_summaries),
            "regime": regime,
            "symbol": symbol,
            "max_steps": max_steps,
            "stats": self.get_stats(),
            "episodes_detail": episode_summaries,
            "execution_authority": False,
            "source": "KronosRLMarketSimulator",
        }
        self.log.info(
            "Kronos offline RL training complete: episodes=%s updates=%s",
            result["episodes"],
            result["stats"].get("total_updates"),
        )
        return result


if __name__ == "__main__":
    # Test the RL trainer
    logging.basicConfig(level=logging.INFO)
    
    trainer = RLTrainer()
    
    # Simulate some training
    for i in range(10):
        state = {
            "volatility_range": random.uniform(0.001, 0.02),
            "bar_position": random.uniform(0.0, 1.0),
            "body_ratio": random.uniform(0.0, 1.0),
            "direction": random.choice([-1, 0, 1]),
            "confluence_score": random.randint(0, 9),
            "near_sr": random.choice([True, False]),
            "htf_trend": random.choice([-1, 0, 1]),
            "news_sentiment": random.uniform(-1, 1),
            "volatility_regime": random.choice(["low", "medium", "high"]),
            "trend_strength": random.uniform(0, 1),
            "session": random.choice(["asian", "london", "ny"])
        }
        
        action, was_exploration = trainer.select_action(state)
        reward = random.uniform(-1, 1)
        
        next_state = state.copy()
        next_state["volatility_range"] = random.uniform(0.001, 0.02)
        next_state["bar_position"] = random.uniform(0.0, 1.0)
        next_state["direction"] = random.choice([-1, 0, 1])
        
        trainer.update(state, action, reward, next_state)
    
    # Print stats
    print("\nRL Trainer Stats:")
    print(json.dumps(trainer.get_stats(), indent=2))
    
    # Save model
    trainer.save_model("test_rl_model.json")
    print("\nModel saved successfully!")
