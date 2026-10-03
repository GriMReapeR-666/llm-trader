#!/usr/bin/env python3
"""
RL Analytics Dashboard
Visualizes RL learning progress, discovered patterns, and performance metrics
"""

import json
import os
from typing import Dict, List, Any, Tuple
from datetime import datetime
import logging

# Optional imports for visualization
try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


class RLAnalytics:
    """Analytics and visualization for RL training progress"""
    
    def __init__(self, model_path: str = "rl_model.json"):
        self.model_path = model_path
        self.log = logging.getLogger("RLAnalytics")
        self.data = None
        self.load_model()
    
    def load_model(self):
        """Load RL model data"""
        if not os.path.exists(self.model_path):
            self.log.warning(f"Model file not found: {self.model_path}")
            return False
        
        try:
            with open(self.model_path, 'r') as f:
                self.data = json.load(f)
            self.log.info(f"Loaded RL model: {len(self.data.get('q_table', {}))} states")
            return True
        except Exception as e:
            self.log.error(f"Failed to load model: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        if not self.data:
            return {}
        
        stats = self.data.get('stats', {})
        q_table = self.data.get('q_table', {})
        training_data = self.data.get('training_data', [])
        episode_rewards = self.data.get('episode_rewards', [])
        
        # Calculate additional stats
        if q_table:
            q_values = []
            for state_actions in q_table.values():
                q_values.extend(state_actions.values())
            
            avg_q = sum(q_values) / len(q_values) if q_values else 0
            max_q = max(q_values) if q_values else 0
            min_q = min(q_values) if q_values else 0
        else:
            avg_q = max_q = min_q = 0
        
        return {
            **stats,
            'avg_q_value': avg_q,
            'max_q_value': max_q,
            'min_q_value': min_q,
            'total_training_samples': len(training_data),
            'total_episodes': len(episode_rewards)
        }
    
    def get_best_states(self, top_n: int = 10) -> List[Tuple[str, Dict[str, float], float]]:
        """Get states with highest Q-values"""
        if not self.data:
            return []
        
        q_table = self.data.get('q_table', {})
        state_values = []
        
        for state_key, q_values in q_table.items():
            max_q = max(q_values.values())
            best_action = max(q_values, key=q_values.get)
            state_values.append((state_key, q_values, max_q, best_action))
        
        # Sort by max Q-value
        state_values.sort(key=lambda x: x[2], reverse=True)
        
        return [(s, q, best_a) for s, q, _, best_a in state_values[:top_n]]
    
    def get_worst_states(self, top_n: int = 10) -> List[Tuple[str, Dict[str, float], float]]:
        """Get states with lowest Q-values"""
        if not self.data:
            return []
        
        q_table = self.data.get('q_table', {})
        state_values = []
        
        for state_key, q_values in q_table.items():
            min_q = min(q_values.values())
            worst_action = min(q_values, key=q_values.get)
            state_values.append((state_key, q_values, min_q, worst_action))
        
        # Sort by min Q-value
        state_values.sort(key=lambda x: x[2])
        
        return [(s, q, worst_a) for s, q, _, worst_a in state_values[:top_n]]
    
    def analyze_action_distribution(self) -> Dict[str, int]:
        """Analyze distribution of learned actions"""
        if not self.data:
            return {}
        
        q_table = self.data.get('q_table', {})
        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        
        for q_values in q_table.values():
            best_action = max(q_values, key=q_values.get)
            action_counts[best_action] += 1
        
        return action_counts
    
    def get_learning_curve(self) -> List[float]:
        """Get cumulative reward over time"""
        if not self.data:
            return []
        
        episode_rewards = self.data.get('episode_rewards', [])
        
        if not episode_rewards:
            return []
        
        # Calculate cumulative rewards
        cumulative = []
        total = 0
        for reward in episode_rewards:
            total += reward
            cumulative.append(total)
        
        return cumulative
    
    def print_summary(self):
        """Print comprehensive summary"""
        print("\n" + "="*60)
        print("RL ANALYTICS DASHBOARD")
        print("="*60)
        
        stats = self.get_stats()
        
        print("\n📊 TRAINING STATISTICS")
        print("-" * 60)
        print(f"Total Updates:        {stats.get('total_updates', 0)}")
        print(f"Q-Table Size:         {stats.get('q_table_size', 0)} states")
        print(f"Training Samples:     {stats.get('total_training_samples', 0)}")
        print(f"Episodes:             {stats.get('total_episodes', 0)}")
        print(f"Exploration Rate:     {stats.get('exploration_rate', 0):.2%}")
        
        print("\n💰 REWARD METRICS")
        print("-" * 60)
        print(f"Avg Reward (100):     {stats.get('avg_reward_100', 0):.3f}")
        print(f"Total Reward:         {stats.get('total_reward', 0):.3f}")
        print(f"Best Reward:          {stats.get('best_reward', 0):.3f}")
        print(f"Worst Reward:         {stats.get('worst_reward', 0):.3f}")
        
        print("\n🎯 Q-VALUE STATISTICS")
        print("-" * 60)
        print(f"Average Q-Value:      {stats.get('avg_q_value', 0):.3f}")
        print(f"Max Q-Value:          {stats.get('max_q_value', 0):.3f}")
        print(f"Min Q-Value:          {stats.get('min_q_value', 0):.3f}")
        
        print("\n🏆 TOP 5 PROFITABLE STATES")
        print("-" * 60)
        best_states = self.get_best_states(5)
        for i, (state_key, q_values, best_action) in enumerate(best_states, 1):
            max_q = max(q_values.values())
            print(f"{i}. {state_key[:50]}...")
            print(f"   Best Action: {best_action} | Q-Value: {max_q:.3f}")
        
        print("\n⚠️  TOP 5 UNPROFITABLE STATES")
        print("-" * 60)
        worst_states = self.get_worst_states(5)
        for i, (state_key, q_values, worst_action) in enumerate(worst_states, 1):
            min_q = min(q_values.values())
            print(f"{i}. {state_key[:50]}...")
            print(f"   Worst Action: {worst_action} | Q-Value: {min_q:.3f}")
        
        print("\n📈 ACTION DISTRIBUTION")
        print("-" * 60)
        action_dist = self.analyze_action_distribution()
        total = sum(action_dist.values())
        if total > 0:
            for action, count in action_dist.items():
                pct = (count / total) * 100
                print(f"{action:6s}: {count:4d} ({pct:5.1f}%)")
        
        print("\n" + "="*60)
    
    def plot_learning_curve(self, output_path: str = "rl_learning_curve.png"):
        """Plot cumulative reward over time"""
        if not MATPLOTLIB_AVAILABLE:
            self.log.warning("Matplotlib not available, skipping plot")
            return False
        
        cumulative_rewards = self.get_learning_curve()
        
        if not cumulative_rewards:
            self.log.warning("No reward data to plot")
            return False
        
        plt.figure(figsize=(12, 6))
        plt.plot(cumulative_rewards, linewidth=2)
        plt.title('RL Learning Curve - Cumulative Reward Over Time', fontsize=14, fontweight='bold')
        plt.xlabel('Episode', fontsize=12)
        plt.ylabel('Cumulative Reward', fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        self.log.info(f"Learning curve saved to {output_path}")
        return True
    
    def plot_q_value_distribution(self, output_path: str = "rl_q_distribution.png"):
        """Plot distribution of Q-values"""
        if not MATPLOTLIB_AVAILABLE:
            self.log.warning("Matplotlib not available, skipping plot")
            return False
        
        if not self.data:
            return False
        
        q_table = self.data.get('q_table', {})
        all_q_values = []
        
        for q_values in q_table.values():
            all_q_values.extend(q_values.values())
        
        if not all_q_values:
            self.log.warning("No Q-values to plot")
            return False
        
        plt.figure(figsize=(12, 6))
        plt.hist(all_q_values, bins=50, edgecolor='black', alpha=0.7)
        plt.title('Q-Value Distribution', fontsize=14, fontweight='bold')
        plt.xlabel('Q-Value', fontsize=12)
        plt.ylabel('Frequency', fontsize=12)
        plt.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        self.log.info(f"Q-value distribution saved to {output_path}")
        return True
    
    def export_to_csv(self, output_path: str = "rl_training_data.csv"):
        """Export training data to CSV"""
        if not PANDAS_AVAILABLE:
            self.log.warning("Pandas not available, skipping CSV export")
            return False
        
        if not self.data:
            return False
        
        training_data = self.data.get('training_data', [])
        
        if not training_data:
            self.log.warning("No training data to export")
            return False
        
        df = pd.DataFrame(training_data)
        df.to_csv(output_path, index=False)
        
        self.log.info(f"Training data exported to {output_path}")
        return True


def main():
    """Main function for standalone usage"""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    # Create analytics instance
    analytics = RLAnalytics("rl_model.json")
    
    if not analytics.data:
        print("❌ No RL model found. Train the model first by running demo trading.")
        return
    
    # Print summary
    analytics.print_summary()
    
    # Generate visualizations
    print("\n📊 Generating visualizations...")
    
    if analytics.plot_learning_curve():
        print("✅ Learning curve saved to rl_learning_curve.png")
    
    if analytics.plot_q_value_distribution():
        print("✅ Q-value distribution saved to rl_q_distribution.png")
    
    if analytics.export_to_csv():
        print("✅ Training data exported to rl_training_data.csv")
    
    print("\n✅ Analytics complete!")


if __name__ == "__main__":
    main()
