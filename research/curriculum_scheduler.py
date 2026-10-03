"""Curriculum Learning Scheduler for RL Training

Implements a curriculum that progresses from simple (trending) to complex
(volatile/ranging) market regimes. This helps the RL agent learn basic
patterns first before tackling more challenging market conditions.

Usage:
    scheduler = CurriculumScheduler(total_episodes=1000)
    regime = scheduler.get_regime(episode=100)
    difficulty = scheduler.get_difficulty(episode=100)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


# Market regime definitions with difficulty scores
REGIMES = {
    # Simple regimes (low difficulty)
    "strong_trending": {"difficulty": 0.1, "description": "Clear directional trend"},
    "weak_trending": {"difficulty": 0.2, "description": "Mild directional bias"},
    
    # Medium regimes
    "ranging": {"difficulty": 0.4, "description": "Sideways market with clear bounds"},
    "breakout": {"difficulty": 0.5, "description": "Range breakout scenario"},
    "pullback": {"difficulty": 0.6, "description": "Trend with deep pullbacks"},
    
    # Complex regimes (high difficulty)
    "volatile": {"difficulty": 0.7, "description": "High volatility, erratic moves"},
    "choppy": {"difficulty": 0.8, "description": "No clear direction, frequent reversals"},
    "news_driven": {"difficulty": 0.9, "description": "Event-driven spikes and gaps"},
    
    # Expert regimes
    "regime_change": {"difficulty": 1.0, "description": "Transition between regimes"},
}


class CurriculumScheduler:
    """Curriculum scheduler for progressive difficulty training."""

    def __init__(
        self,
        total_episodes: int = 1000,
        warmup_episodes: int = 100,
        difficulty_curve: str = "linear",
    ):
        """Initialize the curriculum scheduler.

        Args:
            total_episodes: Total number of training episodes
            warmup_episodes: Episodes spent on easy regimes only
            difficulty_curve: Shape of difficulty progression ("linear", "exponential", "step")
        """
        self.total_episodes = total_episodes
        self.warmup_episodes = warmup_episodes
        self.difficulty_curve = difficulty_curve
        
        # Group regimes by difficulty
        self.regimes_by_difficulty = sorted(
            REGIMES.items(),
            key=lambda x: x[1]["difficulty"]
        )
        
        # Precompute difficulty thresholds for each regime
        self.regime_thresholds = self._compute_thresholds()

    def _compute_thresholds(self) -> Dict[str, Tuple[float, float]]:
        """Compute difficulty thresholds for each regime."""
        thresholds = {}
        n_regimes = len(self.regimes_by_difficulty)
        
        for i, (name, info) in enumerate(self.regimes_by_difficulty):
            # Each regime covers a range of difficulties
            lower = i / n_regimes
            upper = (i + 1) / n_regimes
            thresholds[name] = (lower, upper)
        
        return thresholds

    def get_difficulty(self, episode: int) -> float:
        """Get the target difficulty for a given episode (0.0 to 1.0)."""
        if episode < self.warmup_episodes:
            # During warmup, stay at low difficulty
            return 0.1
        
        # Progress through remaining episodes
        progress = (episode - self.warmup_episodes) / max(1, self.total_episodes - self.warmup_episodes)
        progress = min(1.0, progress)
        
        if self.difficulty_curve == "linear":
            return 0.1 + 0.9 * progress
        elif self.difficulty_curve == "exponential":
            return 0.1 + 0.9 * (math.exp(3 * progress) - 1) / (math.exp(3) - 1)
        elif self.difficulty_curve == "step":
            # Step function: increase difficulty every 20% of training
            step = int(progress * 5) / 5
            return 0.1 + 0.9 * step
        else:
            return 0.1 + 0.9 * progress

    def get_regime(self, episode: int) -> str:
        """Get the recommended regime for a given episode."""
        difficulty = self.get_difficulty(episode)
        
        # Find the regime that best matches the target difficulty
        best_regime = self.regimes_by_difficulty[0][0]
        best_diff = float('inf')
        
        for name, info in self.regimes_by_difficulty:
            diff = abs(info["difficulty"] - difficulty)
            if diff < best_diff:
                best_diff = diff
                best_regime = name
        
        return best_regime

    def get_regimes_for_episode(self, episode: int, n_regimes: int = 3) -> List[str]:
        """Get multiple regimes for an episode (for exploration)."""
        difficulty = self.get_difficulty(episode)
        
        # Sort regimes by distance to target difficulty
        sorted_regimes = sorted(
            self.regimes_by_difficulty,
            key=lambda x: abs(x[1]["difficulty"] - difficulty)
        )
        
        # Return top n regimes (biased toward target difficulty)
        return [name for name, _ in sorted_regimes[:n_regimes]]

    def should_explore(self, episode: int, exploration_rate: float = 0.2) -> bool:
        """Determine if we should explore a harder regime this episode."""
        if episode < self.warmup_episodes:
            return False
        
        # Reduce exploration as training progresses
        progress = episode / self.total_episodes
        adjusted_rate = exploration_rate * (1 - progress)
        
        import random
        return random.random() < adjusted_rate

    def get_curriculum_info(self, episode: int) -> Dict[str, any]:
        """Get detailed curriculum information for logging/debugging."""
        difficulty = self.get_difficulty(episode)
        regime = self.get_regime(episode)
        regimes = self.get_regimes_for_episode(episode)
        
        return {
            "episode": episode,
            "total_episodes": self.total_episodes,
            "progress": episode / self.total_episodes,
            "target_difficulty": difficulty,
            "primary_regime": regime,
            "regime_description": REGIMES[regime]["description"],
            "available_regimes": regimes,
            "warmup_phase": episode < self.warmup_episodes,
            "difficulty_curve": self.difficulty_curve,
        }


class AdaptiveCurriculumScheduler(CurriculumScheduler):
    """Adaptive curriculum that adjusts based on agent performance."""

    def __init__(
        self,
        total_episodes: int = 1000,
        warmup_episodes: int = 100,
        performance_window: int = 50,
        improvement_threshold: float = 0.1,
    ):
        super().__init__(total_episodes, warmup_episodes)
        self.performance_window = performance_window
        self.improvement_threshold = improvement_threshold
        self.recent_rewards: List[float] = []
        self.current_difficulty = 0.1
        self.stall_count = 0

    def update_performance(self, reward: float) -> None:
        """Update performance tracking with new reward."""
        self.recent_rewards.append(reward)
        if len(self.recent_rewards) > self.performance_window:
            self.recent_rewards.pop(0)

    def get_difficulty(self, episode: int) -> float:
        """Adaptively adjust difficulty based on performance."""
        if episode < self.warmup_episodes:
            return 0.1
        
        # Check if we're improving
        if len(self.recent_rewards) >= self.performance_window:
            recent_avg = sum(self.recent_rewards[-self.performance_window // 2:]) / (self.performance_window // 2)
            older_avg = sum(self.recent_rewards[:self.performance_window // 2]) / (self.performance_window // 2)
            
            improvement = (recent_avg - older_avg) / max(abs(older_avg), 1e-6)
            
            if improvement > self.improvement_threshold:
                # Agent is improving, increase difficulty
                self.current_difficulty = min(1.0, self.current_difficulty + 0.05)
                self.stall_count = 0
            elif improvement < -self.improvement_threshold:
                # Agent is declining, decrease difficulty
                self.current_difficulty = max(0.1, self.current_difficulty - 0.1)
                self.stall_count = 0
            else:
                # No significant change, count stalls
                self.stall_count += 1
                if self.stall_count > 10:
                    # Stalled, try increasing difficulty
                    self.current_difficulty = min(1.0, self.current_difficulty + 0.02)
                    self.stall_count = 0
        
        return self.current_difficulty

    def get_regime(self, episode: int) -> str:
        """Get regime based on adaptive difficulty."""
        difficulty = self.get_difficulty(episode)
        
        best_regime = self.regimes_by_difficulty[0][0]
        best_diff = float('inf')
        
        for name, info in self.regimes_by_difficulty:
            diff = abs(info["difficulty"] - difficulty)
            if diff < best_diff:
                best_diff = diff
                best_regime = name
        
        return best_regime
